"""运行时：装配大脑、注入 LLM、配置管理、启动 tick。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from ..adapters import ImageRenderer, LLMProvider, PhotoBridge, PushSender, TTSClient
from .brain import GameBrain
from .config_manager import ConfigManager
from .registry import GameRegistry

log = logging.getLogger("neko_arcade.runtime")


def build_games_summary(registry: GameRegistry, with_desc: bool = True) -> str:
    """生成游戏列表摘要，用于 LLM 工具 description。

    默认带一句话简介(帮助模型识别游戏意图, 历史验证此格式 LLM 会正常调工具);
    不列具体指令, 避免撑爆上下文。
    """
    parts = []
    for game in registry.games:
        if with_desc:
            parts.append(f"{game.name}（{game.description}）")
        else:
            parts.append(game.name)
    return "、".join(parts) if parts else "暂无游戏"


class ArcadeRuntime:
    """插件运行时。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self.cfg: Dict[str, Any] = {}
        self.cfg_mgr = ConfigManager()
        self.registry = GameRegistry(plugin, self.cfg_mgr)
        self.llm = LLMProvider(15)
        self.push = PushSender(plugin)
        self.img = ImageRenderer()
        self.tts = TTSClient(plugin)
        # 发图桥接: 插件主体通用发图能力, 注入所有游戏(游戏 self.send_photo 走桥接)
        self.photo = PhotoBridge(plugin, push=self.push, img=self.img)
        self.brain: Optional[GameBrain] = None
        self._tick_task: Optional[asyncio.Task] = None

    async def start(self) -> int:
        cfg = await self.plugin.config.dump()
        # 兼容两种形状: {"neko_arcade": {...}} 段 或 扁平配置
        if isinstance(cfg, dict):
            self.cfg = cfg.get("neko_arcade", cfg)
        else:
            self.cfg = {}
        self.llm = LLMProvider(self.cfg.get("llm_max_calls_per_minute", 15))
        # token 统计落盘: 写插件自身 store(键 game_user_data:llm_stats), 供 UI 查询
        try:
            store = self.plugin.store

            def _persist_stats(s: Dict[str, Any]) -> None:
                try:
                    asyncio.get_event_loop().create_task(
                        store.set("game_user_data:llm_stats", s)
                    )
                except Exception:
                    pass
            self.llm.set_persist(_persist_stats)
        except Exception as exc:
            log.warning("设置 token 统计落盘失败: %s", exc)
        self.brain = GameBrain(self.plugin, self.registry, self.cfg,
                               self.llm, self.push, self.img, self.cfg_mgr, self.tts)
        self._wire_llm()
        # 将插件服务注入注册表，游戏注册后可调用
        self.registry._push = self.push
        self.registry._img = self.img
        self.registry._tts = self.tts
        self.registry._llm = self.llm
        self.registry._photo = self.photo
        count = await self.registry.discover()
        self._register_dynamic_llm_tool()
        self._tick_task = asyncio.create_task(self._tick_loop())
        log.info("猫娘小游戏就绪，加载 %d 个小游戏", count)
        return count

    def _register_dynamic_llm_tool(self) -> None:
        """注册 play_game 工具：只接收用户原话，插件自动匹配游戏。"""
        plugin = self.plugin
        register = getattr(plugin, "register_llm_tool", None)
        if not register:
            log.info("宿主不支持 register_llm_tool，保留静态工具描述")
            return
        summary = build_games_summary(self.registry)
        if not summary:
            return
        description = (
            "用户想玩小游戏时调用。传入用户说的原话，插件自动判断玩什么游戏并执行。"
            f"可用游戏：{summary}。\n"
            "多轮游戏(如人生重开需选天赋/分属性)返回选择提示后, 用户说「可以/好的/帮我/"
            "随机/继续」等表示继续或让 AI 决定时, 应调用本工具并传「随机」让游戏自动选择,"
            "或传用户明确给出的编号/数值。\n"
            "用户没提到任何游戏玩法时不要调用。调用后直接基于工具结果回应, 不要重复调用。\n"
            "若当前有进行中的游戏(上下文里可能出现 [游戏状态] 提示), 用户说的原话都应传"
            "给本工具, 不要自己扮演游戏流程。"
        )
        unregister = getattr(plugin, "unregister_llm_tool", None)
        if unregister:
            try:
                unregister("play_game")
            except Exception as exc:
                log.warning("注销静态 play_game 工具失败: %s", exc)
        try:
            register(
                name="play_game",
                description=description,
                parameters={
                    "type": "object",
                    "properties": {
                        "input": {"type": "string",
                                  "description": "用户说的原话，如「钓鱼」「钓鱼3次」「鱼缸」「猜硬币」「猜硬币正」"},
                    },
                    "required": ["input"],
                },
                handler=plugin.tool_play_game,
            )
            log.info("已动态注册 play_game 工具（%d 个游戏，自动匹配）",
                     len(self.registry.game_ids))
        except Exception as exc:
            log.error("动态注册 play_game 工具失败: %s", exc)
        self._register_send_photo_tool(plugin, register, unregister)

    def _register_send_photo_tool(self, plugin, register, unregister) -> None:
        """注册 send_photo 工具: 猫娘聊天中自主随机发图。

        不依赖用户关键词 —— 猫娘想分享心情/活跃气氛/给主人惊喜时自行调用。
        工具描述刻意写得不强制: 让 LLM 自然决定频率。
        """
        photo = self.registry.get("neko_photo")
        if not photo:
            return
        if unregister:
            try:
                unregister("send_photo")
            except Exception as exc:
                log.warning("注销静态 send_photo 工具失败: %s", exc)
        description = (
            "聊天过程中想给主人发一张照片时调用。适合这些时刻: 聊到开心想分享心情、"
            "气氛有点安静想活跃一下、想给主人一个惊喜、或者主人提到想看你的照片/图片时。"
            "调用后你会随机发一张照片给主人(可能是你的表情自拍, 也可能是主人上传的图库照片), "
            "不要连续多次调用, 一次聊天里发一两张就够了。"
        )
        try:
            register(
                name="send_photo",
                description=description,
                parameters={
                    "type": "object",
                    "properties": {
                        "input": {"type": "string",
                                  "description": "可选的配文/发图理由, 不填则猫娘自动配文"},
                    },
                    "required": [],
                },
                handler=plugin.tool_send_photo,
            )
            log.info("已动态注册 send_photo 工具(猫娘聊天中自主发图)")
        except Exception as exc:
            log.error("动态注册 send_photo 工具失败: %s", exc)

    def parse_input(self, input: str) -> tuple[Optional[str], str]:
        """解析用户输入，返回 (game_id, cmd)。

        规则(按优先级)：
        1. 当前会话游戏优先：若正在玩某游戏，且输入命中该游戏关键词 → 路由回去。
           (修复「商店」等词被钓鱼截胡——用户玩修仙时发「商店」应进修仙市场)
        2. 显式切游戏：输入命中**其他**游戏关键词 → 切过去(如玩修仙时发「钓鱼」应切钓鱼)。
        3. current_game 兜底：输入不含任何游戏关键词时回当前游戏(多轮续接, 如
           「随机」「0 1 2」等由游戏自身语义判断)。

        确认/催促词(可以/帮我)不在此硬编码——由 LLM 判断并决定调用 play_game 传什么
        (工具描述已指导: 多轮选择时用户说「可以/随机」→ 传「随机」)。
        """
        input = input.strip()
        if not input:
            return None, ""
        cur = self.brain.current_game if (self.brain and self.brain.current_game) else None

        # 1. 当前游戏优先
        if cur:
            cur_game = self.registry.get(cur)
            if cur_game:
                for kw in cur_game.get_keywords():
                    if kw and kw in input:
                        return cur, input

        # 2. 全局关键词(先到先得, 允许切游戏)
        for game in self.registry.games:
            if cur and game.id == cur:
                continue  # 当前游戏已在上面查过
            for kw in game.get_keywords():
                if kw and kw in input:
                    return game.id, input

        # 3. current_game 兜底(多轮续接: 数字/随机等无关键词输入)
        if cur:
            return cur, input
        return None, input

    def _wire_llm(self) -> None:
        """主插件 LLM 接口：配置了 → 所有游戏走新 LLM；没配 → 宿主。

        (用户要求: 配置优先于宿主, 而不是宿主优先。)
        配置来源: data/config/main/config.json 的 llm 段
        (兼容旧键: neko_arcade.llm_main_* 插件配置)。
        """
        if not self.brain:
            return
        provider = model = api_key = base_url = ""
        try:
            main_cfg = self.cfg_mgr.load_main_config()
            if isinstance(main_cfg, dict):
                llm = main_cfg.get("llm") or {}
                if isinstance(llm, dict):
                    provider = str(llm.get("provider", "") or "")
                    model = str(llm.get("model", "") or "")
                    api_key = str(llm.get("api_key", "") or "")
                    base_url = str(llm.get("base_url", "") or "")
        except Exception:
            pass
        if not provider or not model:
            # 兼容旧配置(plugin 配置 neko_arcade.llm_main_*)
            provider = self.cfg.get("llm_main_provider", "")
            model = self.cfg.get("llm_main_model", "")
            api_key = self.cfg.get("llm_main_api_key", "")
            base_url = self.cfg.get("llm_main_base_url", "")
        if provider and model:
            self.llm.set_client(provider, model, api_key, base_url)
            log.info("已配置 LLM: %s/%s (所有游戏走此接口)", provider, model)
            return
        host_call = getattr(self.plugin, "__call_llm", None)
        if host_call:
            self.llm.set_host_call(host_call)
            log.info("未配置 LLM, 回退宿主 __call_llm")

    async def shutdown(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()
            self._tick_task = None
        for game in self.registry.games:
            try:
                await game.on_unload()
            except Exception as exc:
                log.warning("卸载 %s 异常: %s", game.id, exc)

    async def _tick_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1)
                if self.brain:
                    await self.brain.tick()
        except asyncio.CancelledError:
            pass

    async def dispatch(self, game_id: str, cmd: str, args: Optional[Dict],
                       user_id: str) -> Dict[str, Any]:
        if not self.brain:
            return {"message": "大脑还没准备好喵"}
        return await self.brain.handle_action(game_id, cmd, args, user_id)