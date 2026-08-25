"""大脑调度：游戏结果→情感流→多渠道输出。"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .emotion import EmotionRenderer
from .memory import GameMemory
from .persona import Persona
from .proactive import ProactiveEngine
from .registry import GameRegistry

log = logging.getLogger("neko_arcade.brain")


class GameBrain:
    """游戏大脑。"""

    def __init__(self, plugin: Any, registry: GameRegistry, cfg: Dict[str, Any],
                 llm_provider, push_sender, image_renderer, config_manager=None,
                 tts_client=None) -> None:
        self.plugin = plugin
        self.registry = registry
        self.cfg = cfg
        self.push = push_sender
        self.img = image_renderer
        self.cfg_mgr = config_manager
        self.tts = tts_client
        self.persona = Persona(self._load_host_persona())
        self.emotion = EmotionRenderer(llm_provider, cfg.get("llm_max_calls_per_minute", 15))
        self.memory = GameMemory(plugin)
        self.proactive = ProactiveEngine()
        self._current_game: Optional[str] = None
        self._current_user: Optional[str] = None
        self._session_start = 0.0
        # 每个游戏最近一次推送完整邀请的时间戳（秒），用于抑制重复刷屏
        self._last_invite: Dict[str, float] = {}
        # 游戏状态锚节流：游戏进行中且 LLM 长时间未调工具时，向 LLM 上下文
        # 注入「当前游戏 + 可用指令」（只含当前游戏，绝不含全部游戏，防炸上下文）。
        # 每次工具调用(handle_action)都会刷新，所以锚只在 LLM 脱节时出现。
        # 间隔默认 20s(曾被调到 60s 导致宿主插入互动消息后 LLM 长时间脱节,
        # 用户"继续"被当普通聊天打断)——缩短后 LLM 上下文里更频繁有游戏提醒。
        self._last_anchor_ts = 0.0
        self._anchor_interval = float(cfg.get("game_anchor_interval", 20.0))
        # 后台自动发图的活动窗口: 距主人最后一次聊天(通过 @message 刷新)超过
        # 该秒数就不再自动发图, 避免长时间没人说话时猫娘还在后台刷图。
        # 默认 15 分钟, 可用配置 background_active_window 调整。
        self._last_activity_ts = 0.0
        self._background_active_window = float(cfg.get("background_active_window", 900.0))

    def _load_host_persona(self) -> Optional[Dict[str, Any]]:
        try:
            fdir = os.path.dirname(os.path.abspath(__file__))
            host_root = fdir
            for _ in range(5):
                host_root = os.path.dirname(host_root)
            candidates = [
                os.path.join(host_root, "config", "characters.json"),
                os.path.join(host_root, "config", "characters", "zh-CN.json"),
                os.path.join(host_root, "config", "characters", "zh_CN.json"),
                os.path.join(host_root, "config", "characters", "en.json"),
            ]
            config_path = next((p for p in candidates if os.path.exists(p)), None)
            if not config_path:
                return None
            with open(config_path, encoding="utf-8") as f:
                characters = json.load(f)

            # 用户称呼（主人/昵称）
            master = characters.get("主人", {}) or {}
            user_call = (master.get("昵称") or "").strip() or "主人"

            # 当前猫娘名
            current_char = (characters.get("当前猫娘") or "").strip()

            # 猫娘人设
            char_name = self.cfg.get("character_name", "") or os.environ.get("NEKO_CHARACTER", "")
            persona = None
            if isinstance(characters, dict):
                cats = characters.get("猫娘")
                if isinstance(cats, dict):
                    persona = cats.get(char_name) or cats.get("default") or next(iter(cats.values()), None)
                else:
                    persona = characters.get(char_name) or characters.get("default") or next(iter(characters.values()), None)
            elif isinstance(characters, list) and characters:
                for c in characters:
                    if isinstance(c, dict) and c.get("name", "").lower() == char_name.lower():
                        persona = c
                        break
                if not persona:
                    persona = characters[0]
            if not persona:
                return None
            return {
                "traits": persona.get("核心特质", []) or persona.get("traits", []) or persona.get("core_traits", []),
                "description": persona.get("一句话台词", "") or persona.get("description", "") or "",
                "habits": persona.get("行为特点", {}) or persona.get("habits", {}) or {},
                "name": persona.get("name", "") or current_char or "喵喵",
                "user_call": user_call,
            }
        except Exception:
            return None

    @property
    def in_session(self) -> bool:
        return self._current_game is not None

    @property
    def current_game(self) -> Optional[str]:
        return self._current_game

    async def start_game(self, game_id: str, user_id: str = "default") -> Dict[str, Any]:
        """开始游戏会话，不直接推送文本，返回上下文让 LLM 生成开场白。"""
        game = self.registry.get(game_id)
        if not game:
            return {"message": f"没有找到游戏「{game_id}」喵"}
        if self._current_game:
            await self._end_session()
        self._current_game = game_id
        self._current_user = user_id
        self._session_start = time.time()
        await self.memory.register_play(game_id)
        self.persona.on_event("invite")
        if hasattr(game, "on_start"):
            try:
                await game.on_start(user_id)
            except Exception as exc:
                log.warning("on_start 异常: %s", exc)
        call = self.persona.user_call
        return {"game": game_id, "game_name": game.name, "game_icon": game.icon,
                "user_call": call, "started": True, "mood": self.persona.mood.snapshot()}

    async def stop_game(self, user_id: str = "default") -> Dict[str, Any]:
        """结束游戏会话，不直接推送文本，返回上下文让 LLM 生成告别。"""
        if not self._current_game:
            return {"message": "没有在玩喵"}
        game = self.registry.get(self._current_game)
        game_id = self._current_game
        game_name = game.name if game else "游戏"
        if game and hasattr(game, "on_stop"):
            try:
                await game.on_stop(user_id)
            except Exception as exc:
                log.warning("on_stop 异常: %s", exc)
        await self._end_session()
        return {"game": game_id, "game_name": game_name, "stopped": True,
                "mood": self.persona.mood.snapshot()}

    async def _end_session(self) -> None:
        self._current_game = None
        self._session_start = 0.0
        self._last_anchor_ts = 0.0

    @property
    def last_game(self) -> Optional[str]:
        """最近玩过的游戏(会话结束后仍保留, 供确认词路由)。"""
        return getattr(self, "_last_game", None)

    async def handle_action(self, game_id: str, cmd: str, args: Optional[Dict] = None,
                            user_id: str = "default") -> Dict[str, Any]:
        game = self.registry.get(game_id)
        if not game:
            return {"message": f"没有找到游戏「{game_id}」喵"}
        if not game.enabled:
            return {"message": f"「{game.name}」已经停用了喵，去面板把它打开吧"}

        if not cmd or not cmd.strip():
            return await self._invite_game(game, user_id)

        if not self._current_game:
            await self._start_session(game_id, user_id)
        if "帮助" in (cmd or "") or (cmd or "").strip() in ("?", "help"):
            return await self.show_help(game_id)

        # 交互语义(确认词/催促词)由 LLM 判断并决定调用什么, 主插件不硬编码词表。
        # 游戏自身可通过 outcome 语义返回待选择提示, LLM 收到后决定下一步输入。
        result = await game.handle_action(user_id, cmd, args or {})
        outcome = result.get("outcome", "done")

        # 工具被调用 = LLM 还连着游戏, 刷新状态锚节流(脱节时才注入锚)。
        self._last_anchor_ts = time.time()

        # 记录最近玩过的游戏(供「继续」类输入路由回当前游戏)
        if outcome not in ("unknown", "idle", "error"):
            self._last_game = game_id

        # 游戏不认识指令 → 短提示（不再甩一长串玩法说明，避免刷屏）
        if outcome == "unknown":
            return await self._unknown_hint(game)

        facts = result.get("facts", [])
        game_msg = result.get("message", "")
        images = result.get("images") or []   # 游戏返回的可选图片数据(主插件统一推)
        kind = game.classify_event(outcome, facts)
        self.persona.on_event(kind)
        self.memory.record(game_id, outcome, facts, game_msg)
        await game.on_milestone(outcome, facts, self.memory)
        self.proactive.on_result(outcome)

        # 生成情感文本供 LLM 参考（随 summary 一起由宿主喂给 LLM 自然回应）
        templates = game.get_emotion_templates()
        neko_text, level = await self.emotion.render(
            game.name, outcome, facts, self.persona.mood.style(), templates)
        neko_text = self.persona.polish(neko_text)
        if self.tts:
            self.tts.note_tts_line(neko_text)

        # ── 输出编排(主插件统一负责, 游戏不参与推送) ──
        # 1. 游戏返回了图片数据 → 推「情感文本/结算文本 + 图片」一次(盲显, 不触发 AI)
        # 2. 无图片但 message 非空 → 推 message 一次
        # 3. summary 一律用 neko_text(与用户已见内容不同), 避免宿主 LLM 复述造成双重回复
        pushed_any = False
        if images:
            for img in images[:3]:
                text = img.get("text") or neko_text or game_msg
                img_bytes = img.get("bytes")
                img_url = img.get("url")
                if img_url:
                    await self.push.text_with_image_url(text, img_url)
                    pushed_any = True
                elif img_bytes:
                    await self.push.text_with_image(
                        text, img_bytes, img.get("mime", "image/png"))
                    pushed_any = True
            if not pushed_any and game_msg:
                await self.push.text(game_msg, ai_behavior="blind")
        elif game_msg:
            await self.push.text(game_msg, ai_behavior="blind")

        # 高光事件推送图片卡片（LLM 无法生成图片）
        card_img = None
        if level == "highlight" and game.wants_card(outcome, facts):
            card_img = await self._render_card(game, outcome, facts)
            if card_img and not pushed_any:
                await self.push.text_with_image(neko_text, card_img)

        # 构建完整上下文返回给 LLM，让 LLM 自然生成猫娘情感陪伴回应
        help_data = await self.registry.get_help(game.id) if hasattr(self.registry, "get_help") else {}
        cmds = (help_data.get("commands", []) or [])[:5]
        mood = self.persona.mood.snapshot()
        # summary：宿主按入口的 llm_result_fields=["summary"] 提取，喂给对话 LLM
        # ⚠️ 双重回复守门(架构): 游戏不参与推送, 用户已见内容由 brain 统一输出;
        # summary 一律用 neko_text(情感模板, 与用户已见内容不同)——宿主 LLM 收到
        # summary 后自然演绎, 不会复述用户已见的原文。
        summary = neko_text or game_msg
        return {
            "game": game_id,
            "game_name": game.name,
            "game_icon": game.icon,
            "outcome": outcome,
            "level": level,
            "facts": facts,
            "game_result": game_msg,
            "emotion_suggestion": neko_text,
            "summary": summary,
            "mood": mood,
            "game_commands": [c[0] for c in cmds if isinstance(c, (list, tuple)) and c[0]],
            "card_image": bool(card_img),
        }

    async def _start_session(self, game_id: str, user_id: str) -> None:
        """开始游戏会话，不推送文本（由 LLM 生成邀请/开场白）。"""
        game = self.registry.get(game_id)
        if not game:
            return
        self._current_game = game_id
        self._current_user = user_id
        self._session_start = time.time()
        await self.memory.register_play(game_id)
        self.persona.on_event("invite")
        if hasattr(game, "on_start"):
            try:
                await game.on_start(user_id)
            except Exception as exc:
                log.warning("on_start 异常: %s", exc)

    async def _game_commands(self, game) -> List[str]:
        """取游戏的可玩指令名列表（用于邀请/提示）。"""
        help_data = await self.registry.get_help(game.id) if hasattr(self.registry, "get_help") else None
        if not help_data:
            help_data = getattr(game, "_help", {}) or {}
        commands = help_data.get("commands", []) or []
        return [c[0] for c in commands if isinstance(c, (list, tuple)) and c[0]]

    async def _unknown_hint(self, game) -> Dict[str, Any]:
        """游戏没听懂的指令：给一句短提示，不再甩一长串玩法说明。"""
        cmds = await self._game_commands(game)
        first = "、".join(f"「{c}」" for c in cmds[:3]) or "「钓鱼」"
        hint = f"这个指令喵没听懂呢……可以试试 {first} 喵"
        await self.push.text(hint, ai_behavior="blind")
        return {"game": game.id, "game_name": game.name, "game_icon": game.icon,
                "outcome": "unknown", "summary": hint, "invitation": False,
                "mood": self.persona.mood.snapshot(), "game_commands": cmds}

    async def _invite_game(self, game, user_id: str) -> Dict[str, Any]:
        """用户想玩但没给具体指令 → 推送邀请到聊天框，返回指令列表给 LLM。"""
        help_data = await self.registry.get_help(game.id) if hasattr(self.registry, "get_help") else None
        if not help_data:
            help_data = getattr(game, "_help", {}) or {}
        commands = help_data.get("commands", []) or []
        lines = [f"【{game.name}】{game.description}"]
        for cmd_entry in commands[:8]:
            if isinstance(cmd_entry, (list, tuple)) and len(cmd_entry) >= 2 and cmd_entry[0]:
                lines.append(f"  {cmd_entry[0]}：{cmd_entry[1]}")
        docs = "\n".join(lines)
        call = self.persona.user_call

        # 冷却：短时间内同一游戏不再重复刷一长串玩法说明，换成一句短提醒
        now = time.time()
        last = self._last_invite.get(game.id, 0.0)
        if now - last < 90.0:
            short = f"还在{game.name}这里喵，发「钓鱼」就能开始～"
            await self.push.text(short, ai_behavior="blind")
            return {"game": game.id, "game_name": game.name, "game_icon": game.icon,
                    "user_call": call, "invitation": True, "summary": short,
                    "mood": self.persona.mood.snapshot(),
                    "game_commands": [c[0] for c in commands[:8] if isinstance(c, (list, tuple)) and c[0]]}
        self._last_invite[game.id] = now

        text = f"{call}要玩{game.name}吗？{game.icon}\n\n玩法说明：\n{docs}"
        # 邀请文本原样推到聊天框；AI 回应由宿主按 summary 触发（proactive 管线），
        # 这里用 blind 避免和任务结果的 AI 轮次重复。
        await self.push.text(text, ai_behavior="blind")
        return {"game": game.id, "game_name": game.name, "game_icon": game.icon,
                "user_call": call, "game_docs": docs, "invitation": True,
                "summary": text, "mood": self.persona.mood.snapshot(),
                "game_commands": [c[0] for c in commands[:8] if isinstance(c, (list, tuple)) and c[0]]}

    async def _render_card(self, game, outcome: str, facts: List[Dict]) -> Optional[bytes]:
        """使用游戏提供的格式化方法渲染卡片。"""
        lines = []
        for f in facts[:5]:
            detail, rarity = game.format_fact_for_card(f)
            if detail:
                lines.append((detail, rarity))
        if not lines:
            return None
        mood = self.persona.mood.primary()
        return await self.img.render_card(game.name, "游戏结果", lines, outcome, mood)

    async def show_help(self, game_id: str) -> Dict[str, Any]:
        """渲染并推送游戏帮助文档图。任何失败都兜底推纯文本帮助。"""
        game = self.registry.get(game_id)
        if not game:
            msg = f"没有找到游戏「{game_id}」喵"
            return {"message": msg, "summary": msg}
        try:
            help_data = await self.registry.get_help(game_id)
            commands = (help_data or {}).get("commands", []) or []
            text = (help_data or {}).get("text", "") or ""
            if commands:
                try:
                    pages = await self.img.render_help(game.name, commands, text)
                    if pages:
                        # 多页帮助依次推送, 每页高度 ≤ ~600px 避免截断
                        for i, page_bytes in enumerate(pages):
                            title = f"{game.name} 帮助"
                            if len(pages) > 1:
                                title += f" ({i + 1}/{len(pages)})"
                            # 只有第一页带文字说明, 避免重复
                            await self.push.help_doc(title, page_bytes,
                                                     text if i == 0 else None)
                        msg = f"已发送 {game.name} 的帮助文档喵"
                        return {"message": msg, "summary": msg, "image": True}
                except Exception as exc:
                    log.warning("帮助图渲染/推送失败, 降级纯文本: %s", exc)
            # 降级：纯文本帮助（保证用户至少拿到可读的指令清单）
            lines = [text or f"{game.name} 玩法帮助："]
            lines += [f"{c[0]}：{c[1]}" for c in commands
                      if isinstance(c, (list, tuple)) and len(c) >= 2 and c[0]]
            msg = "\n".join(lines)
            await self.push.text(msg)
            return {"message": msg, "summary": msg}
        except Exception as exc:
            log.warning("show_help 异常: %s", exc)
            msg = f"{game.name} 的帮助暂时打不开喵(稍后再试)"
            return {"message": msg, "summary": msg}

    async def on_owner_speak(self) -> None:
        self.proactive.on_owner_speak()
        # 主人说话 = 可能给游戏下一步指令: 立即刷新锚节流, 让下一次 tick
        # 尽快注入状态锚(提醒 LLM 还在游戏中, 把用户的话传给 play_game)。
        # 防止宿主插入互动消息后 LLM 脱节, 用户"继续"被当普通聊天打断。
        self._last_anchor_ts = 0.0
        # 记录最近活跃时间: 后台自动发图只在此窗口内触发(避免无人也刷图)
        self._last_activity_ts = time.time()

    async def tick(self) -> Optional[str]:
        self.persona.mood.decay_all()
        self.proactive.tick()
        # 后台自动发图: 不依赖游戏会话, 聊天过程中猫娘也会随机发图。
        # neko_photo 开启 auto_send 时, 无论当前在不在玩它都定期触发。
        await self._tick_background_games()
        # 当前游戏的每秒钩子（海龟汤用于不活跃提醒 / 超时揭晓）
        if self._current_game:
            game = self.registry.get(self._current_game)
            if game is not None and hasattr(game, "on_tick"):
                try:
                    await game.on_tick(self._current_user or "default")
                except Exception as exc:
                    log.warning("游戏 %s on_tick 异常: %s", self._current_game, exc)
            # 状态锚：游戏进行中且 LLM 长时间没调工具(脱节) → 注入只含当前游戏的
            # 状态文本(read, 不触发 AI 发言、用户不可见)。借鉴 MC 插件 keep-going
            # nudge 的思路, 但只取当前游戏的指令, 绝不含全部游戏(防炸 LLM)。
            if time.time() - self._last_anchor_ts >= self._anchor_interval:
                await self._inject_game_anchor()
            # 游戏进行中禁止 proactive 抢话: 猫娘插话会把用户注意力从游戏拉走,
            # 也让 LLM 以为游戏被打断。游戏中的"等待感"由状态锚(read 注入)负责。
            return None
        if self.proactive.ready_to_speak:
            text = await self._proactive_text()
            if text:
                text = self.persona.polish(text)
                self.proactive.mark_spoke()
                await self.push.text(text)
                return text
        return None

    async def _tick_background_games(self) -> None:
        """后台游戏每秒钩子: 无会话时也运行, 用于「猫娘聊天中自动发图」。

        只对标记了 background_tick=True 且未在会话中的游戏调用 on_tick。
        调用间隔由游戏自身的冷却逻辑(如 neko_photo._next_auto_ts)控制,
        默认 60~180 秒一张, 低打扰(blind 推送, 不触发 AI 轮次)。

        活动窗口门控: 只有最近(默认 15 分钟内)主人说过话才允许自动发图,
        每次主人说话(@message 监听 → on_owner_speak)都会刷新 _last_activity_ts,
        超过窗口后后台发图自动停止, 避免无人聊天时猫娘持续刷图。
        """
        if self._current_game:
            return  # 有会话时, 当前游戏的 on_tick 已由主 tick 调用
        if not self._last_activity_ts:
            return  # 从未聊过天, 不自动发图
        if time.time() - self._last_activity_ts > self._background_active_window:
            return  # 超过活动窗口(默认15分钟无人说话), 停止自动发图
        for game in self.registry.games:
            if not getattr(game, "background_tick", False):
                continue
            if not game.enabled:
                continue
            try:
                await game.on_tick(self._current_user or "default")
            except Exception as exc:
                log.warning("后台游戏 %s on_tick 异常: %s", game.id, exc)

    async def _inject_game_anchor(self) -> None:
        """向 LLM 上下文注入「当前游戏状态锚」: 只含当前游戏的少量指令。

        场景: LLM 忘了调 play_game(如 22:33 轮盘全程自己扮演)时, 注入一条
        read 文本让 LLM 知道游戏引擎还在等输入, 并给出当前游戏的可选指令。
        只取当前游戏 help 前 6 条指令名, 不拼接全部游戏的指令。
        """
        game_id = self._current_game
        if not game_id:
            return
        game = self.registry.get(game_id)
        if game is None:
            return
        try:
            help_data = await self.registry.get_help(game_id)
            commands = (help_data or {}).get("commands", []) or []
        except Exception as exc:
            log.warning("取 %s 帮助失败, 状态锚用关键词兜底: %s", game_id, exc)
            commands = []
        names = []
        for entry in commands:
            if isinstance(entry, (list, tuple)) and entry and entry[0]:
                names.append(str(entry[0]))
            if len(names) >= 6:
                break
        if not names:
            # help 缺失时兜底用当前游戏关键词(同样截断)
            for kw in game.get_keywords():
                if kw and kw not in names:
                    names.append(kw)
                if len(names) >= 6:
                    break
        cmd_text = "、".join(f"「{n}」" for n in names) if names else "任意指令"
        anchor = (
            f"[游戏状态] 正在玩{game.name}, 游戏还没结束, 引擎在等主人的输入喵。"
            f"可用指令: {cmd_text}。"
            "即使刚才插入过其他消息或闲聊, 游戏会话仍在进行——主人接下来说的"
            "原话请通过 play_game 工具传进来, 不要自己扮演游戏流程, 也不要因为"
            "中间有别的对话就以为游戏结束了。"
        )
        self._last_anchor_ts = time.time()
        try:
            await self.push.text(anchor, visibility=[], ai_behavior="read")
            log.info("已注入游戏状态锚(%s): %s", game_id, anchor)
        except Exception as exc:
            log.warning("注入游戏状态锚失败: %s", exc)

    async def _proactive_text(self) -> Optional[str]:
        # 注意: 游戏进行中(tick 已短路 return None), 不会到这里。
        # 非游戏状态才可能主动说话(久不玩邀请等)。
        if await self.proactive.should_invite():
            self.proactive.mark_invited()
            return "好久没一起玩游戏了喵，要不要来一局？"
        return None

    async def snapshot(self) -> Dict[str, Any]:
        return {
            "persona": self.persona.snapshot(),
            "session": {"active": bool(self._current_game), "game": self._current_game,
                        "elapsed": round(time.time() - self._session_start, 1) if self._session_start else 0},
            "proactive": self.proactive.snapshot(),
            "memory": await self.memory.snapshot(),
        }