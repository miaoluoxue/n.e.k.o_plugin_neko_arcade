"""大脑调度：游戏结果→情感流→多渠道输出。"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from .companion import Companion
from .emotion import EmotionRenderer
from .help import HelpRenderer, normalize_help, resolve_topic, split_help_intent
from .memory import GameMemory
from .persona import Persona, apply_persona, load_host_persona
from .proactive import ProactiveEngine
from .registry import GameRegistry

log = logging.getLogger("neko_arcade.brain")

# ── 重复回调守门（线上"双重回复"的真正来源） ──────────────────────────────
# 宿主在插件推送后会为角色起一轮 run，那一轮可能带着**同一条旧输入**再次回调
# play_game。实测 2026-09-27 五子棋对局：用户「我下在J9吧」落子成功并推送状态卡，
# 8 秒后宿主的 run 又拿同一句调了一次 → 第二次落子被判非法("有子了喵") →
# 用户看到两份状态卡，LLM 也跟着说两遍。
# 规则：同一条输入在上一次**没有推进状态**(illegal/unknown/idle/hint/error)
# 之后 DUP_INPUT_WINDOW 秒内再次出现 → 判为宿主重复回调，不再执行、不再推送。
# 只对"没有推进"的结果生效：正常推进的输入（如连点「抛竿」）不受影响。
DUP_INPUT_WINDOW = 15.0
NO_PROGRESS_OUTCOMES = frozenset({"illegal", "unknown", "idle", "hint", "error"})


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
        self.companion = Companion(
            persona=self.persona,
            llm=llm_provider,
            config_manager=config_manager,
            cfg=cfg,
        )
        self.memory = GameMemory(plugin)
        self.proactive = ProactiveEngine()
        # 会话按 user_id 分槽: user_id → game_id。
        # 旧实现只有一个全局槽(_current_game/_current_user), 多人/多群同时玩会互相
        # 覆盖(还在玩 A 却被 B 的输入顶掉), 而且只存内存、重启即丢。
        self._sessions: Dict[str, str] = {}
        self._session_start_by_user: Dict[str, float] = {}
        self._current_user: Optional[str] = None   # 最近活跃用户(tick/状态锚/快照用)
        self._session_start = 0.0                  # 最近活跃用户的开始时间(兼容旧字段)
        self._sessions_dirty = False
        self._last_persist_failed = False
        # 每个游戏最近一次推送完整邀请的时间戳（秒），用于抑制重复刷屏
        self._last_invite: Dict[str, float] = {}
        # 重复回调守门: (game_id, 输入原文) → (时间戳, 上次 outcome)，见模块注释
        self._recent_inputs: Dict[Any, Any] = {}
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
        # 交互式换游戏：会话进行中用户点名别的游戏时不静默切走——先让 LLM
        # 用猫娘口吻确认，用户确认后再切。pending 由 handle_action 维护，
        # 超时自动作废（避免旧的确认一直挂在上下文里）。
        self._pending_switch: Dict[str, Dict[str, Any]] = {}
        self._switch_confirm_ttl = float(cfg.get("game_switch_confirm_ttl", 180.0))
        # 统一帮助渲染器(懒加载): 游戏只给 help.json 数据, 主题与排版由插件解决
        self._help_rend: Optional[HelpRenderer] = None
        self._persona_raw: Optional[Dict[str, Any]] = None

    def _maybe_refresh_persona(self) -> bool:
        """宿主随时可能换猫娘: 每次 tick 检查一次(带签名+TTL, 平时不开销)。

        一旦人设变了: 就地更新 persona + 清掉台词缓存(旧人格生成的台词不能再用)。
        """
        try:
            raw = load_host_persona(self.cfg)
        except Exception as exc:
            log.debug("读取宿主人设失败: %s", exc)
            return False
        if not raw:
            return False
        changed = apply_persona(self.persona, raw)
        if changed:
            self._persona_raw = dict(raw)
            comp = getattr(self, "companion", None)
            clear = getattr(comp, "clear_line_cache", None)
            if callable(clear):
                clear()                      # 清台词变体池 + "最近说过" 记录
            else:
                cache = getattr(comp, "_cache", None)
                if isinstance(cache, dict):
                    cache.clear()
            log.info("检测到猫娘人设变化, 已刷新: %s", raw.get("name"))
        return changed

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

    # ── 会话分槽(每个 user 各自一个当前游戏) ──────────────
    def _session_table(self) -> Dict[str, str]:
        table = getattr(self, "_sessions", None)
        if table is None:                       # 测试里有 __new__ 直接构造的实例
            table = {}
            self._sessions = table
        return table

    def session_game(self, user_id: Optional[str] = None) -> Optional[str]:
        """某用户当前在玩的游戏; 不给 user_id 就取最近活跃用户。"""
        uid = user_id or getattr(self, "_current_user", None) or "default"
        return self._session_table().get(uid)

    def set_session(self, user_id: str, game_id: Optional[str]) -> None:
        """写入/清除某用户的会话槽(只影响这个 user)。"""
        table = self._session_table()
        uid = user_id or "default"
        starts = getattr(self, "_session_start_by_user", None)
        if starts is None:
            starts = {}
            self._session_start_by_user = starts
        if game_id:
            table[uid] = str(game_id)
            starts[uid] = time.time()
            self._current_user = uid
            self._session_start = starts[uid]
        else:
            table.pop(uid, None)
            starts.pop(uid, None)
            if getattr(self, "_current_user", None) == uid:
                self._current_user = next(iter(table), None)
                self._session_start = starts.get(self._current_user, 0.0) if self._current_user else 0.0
        self._sessions_dirty = True

    def active_sessions(self) -> Dict[str, str]:
        """所有在玩中的会话(user_id → game_id), tick 用。"""
        return dict(self._session_table())

    @property
    def _current_game(self) -> Optional[str]:
        """兼容旧字段/旧调用 = 最近活跃用户的当前游戏。"""
        return self.session_game(None)

    @_current_game.setter
    def _current_game(self, value: Optional[str]) -> None:
        self.set_session(getattr(self, "_current_user", None) or "default", value)

    async def _persist_sessions(self) -> bool:
        """把「谁在玩什么」落盘, 重启后能接着玩。"""
        self._last_persist_failed = False
        try:
            await self.memory.save_sessions(self.active_sessions())
            self._sessions_dirty = False
            return True
        except Exception as exc:                # 存档失败不该影响对局
            self._last_persist_failed = True
            log.warning("会话落盘失败: %s", exc)
            return False

    async def restore_sessions(self) -> int:
        """启动时恢复上次的会话(各自游戏的数据由游戏自己的存档负责)。"""
        try:
            saved = await self.memory.load_sessions()
        except Exception as exc:
            log.warning("读取会话存档失败: %s", exc)
            return 0
        n = 0
        for uid, gid in (saved or {}).items():
            game = self.registry.get(gid)
            if game is None or not getattr(game, "enabled", True):
                continue
            self.set_session(str(uid), gid)
            try:
                starter = getattr(game, "on_start", None)
                if callable(starter):
                    await starter(str(uid))
            except Exception as exc:
                log.debug("恢复 %s 的 %s on_start 失败: %s", uid, gid, exc)
            n += 1
        if n:
            self._sessions_dirty = False
            log.info("恢复了 %d 个游戏会话: %s", n, dict(self.active_sessions()))
        return n

    @property
    def in_session(self) -> bool:
        return bool(self.active_sessions())

    @property
    def current_game(self) -> Optional[str]:
        return self.session_game(None)

    async def start_game(self, game_id: str, user_id: str = "default") -> Dict[str, Any]:
        """开始游戏会话，不直接推送文本，返回上下文让 LLM 生成开场白。"""
        game = self.registry.get(game_id)
        if not game:
            return {"message": f"没有找到游戏「{game_id}」喵"}
        if not game.enabled:
            return {"message": f"「{game.name}」已经停用了喵，去面板把它打开吧"}
        # 面板直接开新游戏：旧会话要完整收尾（含 on_stop），不能只清字段。
        await self._end_current_game(user_id)
        self._clear_pending_switch(user_id)
        self.set_session(user_id, game_id)
        try:
            await self.memory.register_play(game_id)
        except Exception:
            log.warning("记录开局统计失败，游戏继续: %s", game_id, exc_info=True)
        await self._persist_sessions()
        try:
            self.persona.on_event("invite")
        except Exception:
            log.exception("开始游戏时更新猫娘心情失败: %s", game_id)
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
        game_id = self.session_game(user_id)
        if not game_id:
            return {"message": "没有在玩喵"}
        game = self.registry.get(game_id)
        game_name = game.name if game else "游戏"
        await self._end_current_game(user_id)
        self._clear_pending_switch(user_id)
        return {"game": game_id, "game_name": game_name, "stopped": True,
                "mood": self.persona.mood.snapshot()}

    async def _end_session(self, user_id: str = "default") -> None:
        self.set_session(user_id, None)
        self._last_anchor_ts = 0.0

    async def _end_current_game(self, user_id: str = "default") -> Optional[str]:
        """结束该用户的会话（含旧游戏的 on_stop），返回被结束的 game_id。"""
        old_id = self.session_game(user_id)
        if not old_id:
            await self._end_session(user_id)
            return None
        old = self.registry.get(old_id)
        if old is not None and hasattr(old, "on_stop"):
            try:
                await old.on_stop(user_id)
            except Exception as exc:
                log.warning("游戏 %s on_stop 异常: %s", old_id, exc)
        await self._end_session(user_id)
        await self._persist_sessions()
        return old_id

    async def _ask_switch(self, game, cmd: str, user_id: str) -> Dict[str, Any]:
        """会话中用户点名别的游戏 → 返回「确认切换」语义，交给 LLM 生成对话。

        这里**不推送任何固定话术**：只给 LLM 一条 summary 指令，让它用猫娘
        自己的口吻问「要换吗」。用户确认后 LLM 再调 play_game 并填 switch_to，
        才真正切换（见 :meth:`_switch_confirmed`）。
        """
        old_id = self.session_game(user_id)
        old = self.registry.get(old_id) if old_id else None
        old_name = old.name if old else str(old_id or "")
        self._set_pending_switch(user_id, {
            "from": old_id, "to": game.id, "user": user_id,
            "cmd": cmd, "ts": time.time(),
        })
        # 让下一次 tick 立刻注入「切换待确认」状态锚（不等 20s 节流）。
        self._last_anchor_ts = 0.0
        summary = (
            f"[游戏切换确认] 主人现在还在玩「{old_name}」，刚提到想玩「{game.name}」。"
            "请用猫娘自己的口吻问他要不要换过去（撒娇/在意/吐槽都行，语气随你），"
            "不要直接开始新游戏、不要自己扮演切换、不要输出固定模板句。"
            f"如果他确认要换，再调用 play_game：input 传他的原话，"
            f"并填 switch_to=\"{game.name}\"；"
            "如果他说不换或要继续原来的游戏，就不要填 switch_to。"
        )
        return {
            "game": game.id, "game_name": game.name, "game_icon": game.icon,
            "outcome": "switch_confirm", "level": "routine", "facts": [],
            "game_result": "", "emotion_suggestion": "",
            "summary": summary, "mood": self.persona.mood.snapshot(),
            "pending_switch": {"from": old_id, "to": game.id},
            "game_commands": [],
        }

    async def _switch_confirmed(self, switch_to: str, cmd: str, user_id: str,
                                args: Dict[str, Any]) -> Dict[str, Any]:
        """用户已确认换游戏：完整收尾旧会话 → 开始新会话 → 执行新游戏。"""
        target = self.registry.get(switch_to) or self.registry.get_by_name(switch_to)
        if target is None:
            self._clear_pending_switch(user_id)
            return {"facts": [], "outcome": "unknown",
                    "message": f"没有找到游戏「{switch_to}」喵"}
        if not target.enabled:
            self._clear_pending_switch(user_id)
            return {"facts": [], "outcome": "unknown",
                    "message": f"「{target.name}」已经停用了喵，去面板把它打开吧"}

        pending = self.pending_switch_for(user_id) or {}
        pending_to = str(pending.get("to") or "")
        if pending_to not in (target.id, target.name):
            # 没有待确认的切换（LLM 抢跑/或用户又换了个目标）→ 仍然先问一次。
            return await self._ask_switch(target, cmd, user_id)
        target_cmd = str(pending.get("cmd") or "").strip()
        if not target_cmd:
            kws = target.get_keywords() or []
            target_cmd = kws[0] if kws else target.name

        from_id = self.session_game(user_id)
        from_name = ""
        if from_id:
            old = self.registry.get(from_id)
            from_name = old.name if old else from_id

        if from_id != target.id:
            await self._end_current_game(user_id)   # 旧游戏 on_stop + 清会话
            await self._start_session(target.id, user_id)  # 新游戏 on_start
        self._clear_pending_switch(user_id)

        inner_args = {k: v for k, v in args.items() if k != "switch_to"}
        result = await self.handle_action(target.id, target_cmd, inner_args, user_id)
        if isinstance(result, dict) and from_id and from_id != target.id:
            note = (
                f"[切换完成] 已从「{from_name}」切到「{target.name}」。"
                "请用猫娘自己的口吻确认一下切换完成（措辞随你），再接着新游戏的"
                "内容；不要输出固定模板句，也不要复述本条指令。"
            )
            result["summary"] = note + str(result.get("summary") or "")
            result["switched_from"] = from_id
        return result

    @property
    def last_game(self) -> Optional[str]:
        """最近玩过的游戏(会话结束后仍保留, 供确认词路由)。"""
        return getattr(self, "_last_game", None)

    def _recent_inputs_map(self) -> Dict[Any, Any]:
        """重复回调守门表（懒初始化，兼容测试用 __new__ 直接构造）。"""
        table = getattr(self, "_recent_inputs", None)
        if table is None:
            table = {}
            self._recent_inputs = table
        return table

    def _pending_switch_map(self) -> Dict[str, Dict[str, Any]]:
        """待确认切换按用户分槽，避免 A 的确认被 B 的输入清掉。"""
        table = getattr(self, "_pending_switch", None)
        if table is None:
            table = {}
            self._pending_switch = table
        elif not isinstance(table, dict):
            table = {}
            self._pending_switch = table
        return table

    def pending_switch_for(self, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        return self._pending_switch_map().get(user_id or "default")

    def _set_pending_switch(self, user_id: str, pending: Dict[str, Any]) -> None:
        self._pending_switch_map()[user_id or "default"] = pending

    def _clear_pending_switch(self, user_id: str) -> None:
        self._pending_switch_map().pop(user_id or "default", None)

    async def handle_action(self, game_id: str, cmd: str, args: Optional[Dict] = None,
                            user_id: str = "default") -> Dict[str, Any]:
        args = dict(args or {})
        switch_to = str(args.get("switch_to") or "").strip()

        # ① 用户已确认换游戏（LLM 填了 switch_to）→ 先完整切会话，再执行新游戏。
        if switch_to:
            return await self._switch_confirmed(switch_to, cmd, user_id, args)

        game = self.registry.get(game_id)
        if not game:
            return {"message": f"没有找到游戏「{game_id}」喵"}
        if not game.enabled:
            return {"message": f"「{game.name}」已经停用了喵，去面板把它打开吧"}

        # ② 会话进行中用户点名别的游戏：先看是不是「XX帮助」——帮助直接出图，
        #    不算换游戏；否则返回确认语义，由 LLM 用猫娘口吻问「要换吗」。
        cur_game = self.session_game(user_id)
        if cur_game and game_id != cur_game:
            help_topic = split_help_intent(cmd, strip_words=self._help_strip_words(game))
            if help_topic is not None and len((cmd or "").strip()) <= 14:
                return await self.show_help(game_id, topic=help_topic)
            return await self._ask_switch(game, cmd, user_id)

        # ③ 记下进来时有没有待确认的切换：等会儿按当前游戏认不认这条输入决定
        #    是「放弃切换」还是「把切换确认再交给 LLM」。
        pending_before = self.pending_switch_for(user_id) is not None

        if not cmd or not cmd.strip():
            return await self._invite_game(game, user_id)

        if not cur_game:
            await self._start_session(game_id, user_id)

        # 帮助意图统一由插件拦截(游戏无需实现): 「修仙帮助」「修仙帮助 战斗」都能定位
        help_topic = split_help_intent(cmd, strip_words=self._help_strip_words(game))
        if help_topic is not None and len((cmd or "").strip()) <= 14:
            return await self.show_help(game_id, topic=help_topic)

        # 交互语义(确认词/催促词)由 LLM 判断并决定调用什么, 主插件不硬编码词表。
        # 游戏自身可通过 outcome 语义返回待选择提示, LLM 收到后决定下一步输入。
        # ── 重复回调守门（见模块顶部注释）：同一条输入刚发生过且没推进状态 → 忽略 ──
        dup_table = self._recent_inputs_map()
        dup_key = (user_id or "default", game_id, cmd.strip())
        now_ts = time.time()
        recent = dup_table.get(dup_key)
        if recent is not None:
            last_ts, last_outcome = recent
            if (now_ts - last_ts) < DUP_INPUT_WINDOW \
                    and str(last_outcome) in NO_PROGRESS_OUTCOMES:
                log.info("忽略重复回调: game=%s input=%r 上次 outcome=%s",
                         game_id, dup_key[1], last_outcome)
                return {
                    "game": game_id,
                    "game_name": game.name,
                    "game_icon": game.icon,
                    "outcome": "duplicate",
                    "level": "routine",
                    "facts": [],
                    "game_result": "",
                    "emotion_suggestion": "",
                    "duplicate": True,
                    # 给宿主 LLM 的说明：这条是重复回调，别再复述一遍
                    "summary": f"（{game.name}：刚才这条输入没推进对局，是重复回调，"
                               f"已忽略，不要重复念一遍结果）",
                }

        try:
            result = await game.handle_action(user_id, cmd, args or {})
            if not isinstance(result, dict):
                raise TypeError("game.handle_action must return a dict")
        except Exception:
            log.exception("游戏动作执行失败: %s/%s", game_id, cmd)
            message = f"{game.name}执行时出了点问题喵，请稍后再试。"
            try:
                await self.push.text(message, ai_behavior="blind")
            except Exception:
                log.exception("游戏错误提示推送失败: %s", game_id)
            return {
                "game": game_id,
                "game_name": game.name,
                "game_icon": game.icon,
                "outcome": "error",
                "level": "routine",
                "facts": [],
                "game_result": message,
                "emotion_suggestion": message,
                "summary": message,
                "mood": self.persona.mood.snapshot(),
                "session_ended": False,
                "game_commands": [],
                "card_image": False,
            }
        outcome = result.get("outcome", "done")
        dup_table[dup_key] = (now_ts, str(outcome))
        if len(dup_table) > 64:
            for stale in [k for k, v in dup_table.items()
                          if now_ts - float(v[0]) >= DUP_INPUT_WINDOW]:
                dup_table.pop(stale, None)

        # 工具被调用 = LLM 还连着游戏, 刷新状态锚节流(脱节时才注入锚)。
        self._last_anchor_ts = time.time()

        # 记录最近玩过的游戏(供「继续」类输入路由回当前游戏)
        if outcome not in ("unknown", "idle", "error"):
            self._last_game = game_id

        if self.session_game(user_id) == game_id:
            pending_switch = self.pending_switch_for(user_id)
            if outcome == "unknown" and pending_before and pending_switch:
                # 有换游戏待确认，这条输入当前游戏又不认 → 不当成放弃切换，
                # 重新把「要不要换到 X」交给 LLM（不硬编码确认词）。
                pending_game = self.registry.get(
                    str(pending_switch.get("to") or ""))
                if pending_game is not None:
                    return await self._ask_switch(
                        pending_game,
                        str(pending_switch.get("cmd") or ""), user_id)
            self._clear_pending_switch(user_id)

        # 游戏不认识指令 → 短提示（不再甩一长串玩法说明，避免刷屏）
        if outcome == "unknown":
            return await self._unknown_hint(game)

        facts = result.get("facts", [])
        if not isinstance(facts, list):
            facts = []
        game_msg = str(result.get("message", "") or "")
        images = result.get("images") or []
        if not isinstance(images, list):
            images = []

        # 游戏状态已经推进后，后续每个步骤都必须独立兜底。
        # 台词/记忆/推送任一步失败，都不能让宿主看不到本局结果。
        level = "routine"
        try:
            level = game.classify_event(outcome, facts) or "routine"
        except Exception:
            log.exception("分类游戏结果失败: %s/%s", game_id, outcome)
        try:
            self.persona.on_event(level)
        except Exception:
            log.exception("更新猫娘心情失败: %s/%s", game_id, outcome)
        try:
            self.memory.record(game_id, outcome, facts, game_msg)
        except Exception:
            log.exception("记录短期游戏记忆失败: %s/%s", game_id, outcome)
        try:
            await game.on_milestone(outcome, facts, self.memory)
        except Exception:
            log.exception("处理游戏里程碑失败: %s/%s", game_id, outcome)
        try:
            self.proactive.on_result(outcome)
        except Exception:
            log.exception("更新主动陪玩状态失败: %s/%s", game_id, outcome)

        neko_text = ""
        try:
            neko_text = await self.companion.speak(
                game_id, result, user_text=cmd, situation=game_msg,
            )
        except Exception:
            log.exception("陪伴台词生成失败，回退游戏结果: %s/%s", game_id, outcome)
        if not neko_text:
            try:
                templates = game.get_emotion_templates()
                neko_text, _ = await self.emotion.render(
                    game.name, outcome, facts, self.persona.mood.style(), templates)
            except Exception:
                log.exception("情感文本兜底失败: %s/%s", game_id, outcome)
        try:
            neko_text = self.persona.polish(neko_text or "")
        except Exception:
            log.exception("台词人格润色失败: %s/%s", game_id, outcome)
        neko_text = neko_text or game_msg
        if self.tts and neko_text:
            try:
                self.tts.note_tts_line(neko_text)
            except Exception:
                log.exception("记录 TTS 台词失败: %s/%s", game_id, outcome)

        # 输出编排：图片/文本每一条单独推送，失败后至少再尝试纯文本。
        pushed_any = False
        for img in images[:3]:
            if not isinstance(img, dict):
                continue
            text = str(img.get("text") or "")
            caption = text
            if game_msg and text != game_msg:
                caption = f"{text}\n\n{game_msg}" if text else game_msg
            caption = caption or neko_text
            try:
                img_url = img.get("url")
                img_bytes = img.get("bytes")
                if img_url:
                    await self.push.text_with_image_url(caption, img_url)
                    pushed_any = True
                elif img_bytes:
                    await self.push.text_with_image(
                        caption, img_bytes, img.get("mime", "image/png"))
                    pushed_any = True
            except Exception:
                log.exception("游戏图片推送失败: %s/%s", game_id, outcome)
        fallback_text = game_msg or neko_text
        if not pushed_any and fallback_text:
            try:
                await self.push.text(fallback_text, ai_behavior="blind")
                pushed_any = True
            except Exception:
                log.exception("游戏文本推送失败: %s/%s", game_id, outcome)

        card_img = None
        try:
            if level == "highlight" and game.wants_card(outcome, facts):
                card_img = await self._render_card(game, outcome, facts)
                if card_img and not pushed_any:
                    await self.push.text_with_image(neko_text or game_msg, card_img)
                    pushed_any = True
        except Exception:
            log.exception("高光卡片生成/推送失败: %s/%s", game_id, outcome)

        session_ended = False
        end_set = tuple(getattr(game, "end_session_outcomes", ()) or ())
        if end_set and str(outcome) in end_set and self.session_game(user_id) == game_id:
            try:
                await self._end_current_game(user_id)
                session_ended = True
            except Exception:
                log.exception("结束游戏会话失败: %s/%s", game_id, user_id)

        cmds = []
        try:
            help_data = (
                await self.registry.get_help(game.id)
                if hasattr(self.registry, "get_help") else {}
            )
            cmds = (help_data.get("commands", []) or [])[:5]
        except Exception:
            log.exception("读取游戏指令失败: %s", game_id)

        summary = neko_text or game_msg or "操作已经完成喵"
        try:
            facts_hint = self._facts_to_hint(facts)
        except Exception:
            facts_hint = ""
        if facts_hint:
            summary = f"{summary}\n[结果要点] {facts_hint}"
        if session_ended:
            summary += (f"\n[本局已结束] 已经退出{game.name}游戏模式。"
                        f"想再来一局直接说「{game.name}」就行；现在可以正常聊天了。")
        if getattr(self, "_last_persist_failed", False):
            summary += "\n[存档提醒] 操作已经执行，但会话存档没存上，请提醒主人稍后重试。"
        try:
            mood = self.persona.mood.snapshot()
        except Exception:
            mood = {}
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
            "session_ended": session_ended,
            "game_commands": [c[0] for c in cmds if isinstance(c, (list, tuple)) and c[0]],
            "card_image": bool(card_img),
        }

    @staticmethod
    def _facts_to_hint(facts: List[Dict]) -> str:
        """从 facts 提取给 LLM 的解读素材(结构化要点, 与 game_msg 不同表述)。

        游戏把关键结果字段放进 facts(如塔罗的 card/position/meaning), 这里
        提取拼进 summary——宿主 LLM 拿到素材才能真实解读(如解析塔罗牌意),
        而不是只会说情感模板话。只取常见字段, 不枚举整条 fact。
        """
        parts = []
        for f in facts[:8]:
            if not isinstance(f, dict):
                continue
            kind = f.get("kind", "")
            card = f.get("card") or f.get("name") or f.get("item") or ""
            meaning = f.get("meaning") or ""
            position = f.get("position") or ""
            pos_name = f.get("position_name") or ""
            if card:
                bit = f"{card}"
                if position:
                    bit += f"({position})"
                if pos_name:
                    bit = f"{pos_name}: {bit}"
                if meaning:
                    bit += f" 含义:{meaning}"
                parts.append(bit)
            elif kind and f.get("value") is not None:
                parts.append(f"{kind}={f.get('value')}")
        return "；".join(parts) if parts else ""

    async def _start_session(self, game_id: str, user_id: str) -> None:
        """开始游戏会话，不推送文本（由 LLM 生成邀请/开场白）。"""
        game = self.registry.get(game_id)
        if not game:
            return
        self.set_session(user_id, game_id)
        try:
            await self.memory.register_play(game_id)
        except Exception:
            log.warning("记录开局统计失败，游戏继续: %s", game_id, exc_info=True)
        await self._persist_sessions()
        try:
            self.persona.on_event("invite")
        except Exception:
            log.exception("启动游戏会话时更新猫娘心情失败: %s", game_id)
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
        try:
            await self.push.text(hint, ai_behavior="blind")
        except Exception:
            log.warning("未知指令提示推送失败: %s", game.id, exc_info=True)
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
        invite_key = (user_id or "default", game.id)
        last = self._last_invite.get(invite_key, 0.0)
        if now - last < 90.0:
            short = f"还在{game.name}这里喵，发「钓鱼」就能开始～"
            try:
                await self.push.text(short, ai_behavior="blind")
            except Exception:
                log.warning("游戏邀请提示推送失败: %s", game.id, exc_info=True)
            return {"game": game.id, "game_name": game.name, "game_icon": game.icon,
                    "user_call": call, "invitation": True, "summary": short,
                    "mood": self.persona.mood.snapshot(),
                    "game_commands": [c[0] for c in commands[:8] if isinstance(c, (list, tuple)) and c[0]]}
        self._last_invite[invite_key] = now

        text = f"{call}要玩{game.name}吗？{game.icon}\n\n玩法说明：\n{docs}"
        # 邀请文本原样推到聊天框；AI 回应由宿主按 summary 触发（proactive 管线），
        # 这里用 blind 避免和任务结果的 AI 轮次重复。
        try:
            await self.push.text(text, ai_behavior="blind")
        except Exception:
            log.warning("游戏邀请推送失败: %s", game.id, exc_info=True)
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

    # ── 统一帮助系统 ──────────────────────────────────────
    def _help_renderer(self) -> HelpRenderer:
        """懒加载统一帮助渲染器(素材与缓存都取自插件自身路径)。"""
        if self._help_rend is None:
            code_dir = str(getattr(self.plugin, "plugin_dir", "") or "")
            cache_dir = ""
            cache_path = getattr(self.plugin, "cache_path", None)
            if callable(cache_path):
                try:
                    cache_dir = str(cache_path("help"))
                except Exception as exc:      # 老宿主没有 cache_path → 不缓存
                    log.debug("cache_path 不可用, 帮助图不缓存: %s", exc)
            self._help_rend = HelpRenderer(self.img, code_dir, cache_dir,
                                           config_manager=self.cfg_mgr)
        return self._help_rend

    @staticmethod
    def _help_strip_words(game: Any) -> List[str]:
        """帮助主题里要去掉的噪声词: 游戏名 + 该游戏触发关键词(长度 ≥2)。"""
        words = {str(getattr(game, "name", "") or ""), str(getattr(game, "id", "") or "")}
        try:
            words.update(str(w) for w in (game.get_keywords() or []))
        except Exception:
            pass
        return [w for w in words if len(w) >= 2]

    def _help_theme(self) -> str:
        """帮助图主题: 优先桥接(与游戏一致), 否则读主配置, 缺省官方亮色。"""
        bridge = getattr(self.registry, "_render", None)
        theme = ""
        if bridge is not None and hasattr(bridge, "theme"):
            try:
                theme = str(bridge.theme() or "")
            except Exception:
                theme = ""
        if theme in ("light", "dark"):
            return theme
        try:
            cfg = (self.cfg_mgr.load_main_config() or {}) if self.cfg_mgr else {}
        except Exception:
            cfg = {}
        section = cfg.get("help") if isinstance(cfg.get("help"), dict) else {}
        theme = str(section.get("theme") or cfg.get("help_theme") or "light").lower()
        return theme if theme in ("light", "dark") else "light"

    async def show_help(self, game_id: str, topic: str = "") -> Dict[str, Any]:
        """渲染并推送游戏帮助图(支持功能主题)。

        topic 为空 → 目录页(或老格式单页); topic 命中分组/指令 → 对应页;
        未命中 → 目录页 + 近似建议。任何失败都逐级降级: 统一渲染器 → 旧 HTML 渲染
        → PIL → 纯文本, 保证用户至少拿到可读的指令清单。
        """
        game = self.registry.get(game_id)
        if not game:
            msg = f"没有找到游戏「{game_id}」喵"
            return {"message": msg, "summary": msg}
        try:
            help_data = await self.registry.get_help(game_id)
            doc = normalize_help(help_data or {}, game_id, game.name)
            page = resolve_topic(doc, topic)

            # ① 统一渲染桥接(官方 UI Kit 视觉 + 主题 + 宿主浏览器) —— 与游戏共用同一套
            try:
                bridge = getattr(self.registry, "_render", None)
                if bridge is not None and getattr(bridge, "ready", False):
                    pages = await bridge.help(game_id, game_name=game.name,
                                              raw_help=help_data, topic=topic)
                else:
                    renderer = self._help_renderer()
                    pages = await renderer.render(doc, page, theme=self._help_theme())
            except Exception as exc:
                log.warning("统一帮助渲染失败: %s", exc)
                pages = []
            if pages:
                label = doc.title or game.name
                if page.kind == "group" and page.group is not None:
                    label = f"{label} · {page.group.name}"
                elif page.kind == "command" and page.command is not None:
                    label = f"{label} · {page.command.cmd}"
                # ☠ 指令分级: 一次只发**第一页**(目录 / 分组 / 单指令各一级)，
                #   绝不把多页一次刷给用户；被截断时在配文里给下钻方法。
                extra = ""
                if len(pages) > 1:
                    extra = (f"（还有 {len(pages) - 1} 页没展开："
                             f"发「{game.name}帮助 <功能名>」逐级查看）")
                text = ((doc.text or "") + extra).strip() or None
                await self.push.help_doc(f"{label} 帮助", pages[0], text)
                msg = (f"已发送 {label} 的帮助喵" if len(pages) == 1
                       else f"已发送 {label} 的帮助目录喵（共 {len(pages)} 页，按功能分级查看）")
                return {"message": msg, "summary": msg, "image": True}

            # ② 旧路径: 浏览器渲染扁平帮助 → PIL → 纯文本
            commands = [c.as_pair() for c in (doc.all_commands() or doc.flat)]
            text = doc.text
            if commands:
                try:
                    pages = None
                    html_render = getattr(self.img, "render_help_html", None)
                    if callable(html_render):
                        try:
                            pages = await html_render(game.name, commands, text)
                        except Exception as exc:
                            log.warning("帮助图浏览器渲染失败, 回退 PIL: %s", exc)
                    if not pages:
                        pages = await self.img.render_help(game.name, commands, text)
                    if pages:
                        # 指令分级: 旧通道也只发第一页, 其余靠分组下钻
                        extra = (f"（还有 {len(pages) - 1} 页没展开："
                                 f"发「{game.name}帮助 <功能名>」逐级查看）"
                                 if len(pages) > 1 else "")
                        await self.push.help_doc(f"{game.name} 帮助", pages[0],
                                                 ((text or "") + extra).strip() or None)
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
        self._maybe_refresh_persona()          # 猫娘可换: 每秒轻量检查一次
        self.persona.mood.decay_all()
        self.proactive.tick()
        # 换游戏确认会过期：用户半天不回就不再提，避免旧的确认一直挂在上下文里。
        now = time.time()
        for uid, pending in list(self._pending_switch_map().items()):
            if now - float(pending.get("ts") or 0) > self._switch_confirm_ttl:
                self._clear_pending_switch(uid)
        # 后台自动发图: 不依赖游戏会话, 聊天过程中猫娘也会随机发图。
        # neko_photo 开启 auto_send 时, 无论当前在不在玩它都定期触发。
        await self._tick_background_games()
        # 每个用户的会话各自跑每秒钩子(多人同时玩不同游戏互不影响)
        sessions = self.active_sessions()
        if sessions:
            for uid, gid in list(sessions.items()):
                game = self.registry.get(gid)
                if game is None:                 # 游戏被删/改名 → 清掉这个槽
                    self.set_session(uid, None)
                    continue
                if hasattr(game, "on_tick"):
                    try:
                        said = await game.on_tick(uid)
                        # 局中主动搭话: 游戏只返回文字, 推送统一由本体做
                        if isinstance(said, str) and said.strip():
                            await self.push.text(said)
                    except Exception as exc:
                        log.warning("游戏 %s on_tick 异常: %s", gid, exc)
            if self._sessions_dirty:
                await self._persist_sessions()
            # 状态锚：游戏进行中且 LLM 长时间没调工具(脱节) → 注入只含当前游戏的
            # 状态文本(read, 不触发 AI 发言、用户不可见)。借鉴 MC 插件 keep-going
            # nudge 的思路, 但只取当前游戏的指令, 绝不含全部游戏(防炸 LLM)。
            if time.time() - self._last_anchor_ts >= self._anchor_interval:
                await self._inject_game_anchor(self._current_user or "default")
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
        if self.active_sessions():
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

    async def _inject_game_anchor(self, user_id: str = "default") -> None:
        """向 LLM 上下文注入「当前游戏状态锚」: 只含当前游戏的少量指令。

        场景: LLM 忘了调 play_game(如 22:33 轮盘全程自己扮演)时, 注入一条
        read 文本让 LLM 知道游戏引擎还在等输入, 并给出当前游戏的可选指令。
        只取当前游戏 help 前 6 条指令名, 不拼接全部游戏的指令。
        """
        game_id = self.session_game(user_id)
        if not game_id:
            return
        game = self.registry.get(game_id)
        if game is None:
            return
        pending_switch = self.pending_switch_for(user_id)
        if pending_switch:
            target = self.registry.get(str(pending_switch.get("to") or ""))
            to_name = target.name if target else str(pending_switch.get("to") or "")
            anchor = (
                f"[游戏切换待确认] 主人提出想玩「{to_name}」，你刚问了他要不要换，"
                "正在等他回答。他确认后请调用 play_game 并填 "
                f"switch_to=\"{to_name}\"；他说不换就继续当前游戏，不要自己切换，"
                "也不要输出固定模板句。"
            )
            self._last_anchor_ts = time.time()
            try:
                await self.push.text(anchor, visibility=[], ai_behavior="read")
                log.info("已注入换游戏确认锚(%s)", to_name)
            except Exception as exc:
                log.warning("注入换游戏确认锚失败: %s", exc)
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
        sessions = self.active_sessions()
        focus = self.session_game(None)
        return {
            "persona": self.persona.snapshot(),
            "session": {"active": bool(sessions), "game": focus,
                        "elapsed": round(time.time() - self._session_start, 1) if self._session_start else 0,
                        "sessions": sessions},
            "pending_switch": dict(self._pending_switch_map()),
            "proactive": self.proactive.snapshot(),
            "memory": await self.memory.snapshot(),
        }
