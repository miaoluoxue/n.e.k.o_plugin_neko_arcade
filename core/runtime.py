"""运行时：装配大脑、注入 LLM、配置管理、启动 tick。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from ..adapters import ImageRenderer, LLMProvider, PushSender, TTSClient
from .brain import GameBrain
from .config_manager import ConfigManager
from .registry import GameRegistry

log = logging.getLogger("neko_arcade.runtime")


def build_games_summary(registry: GameRegistry) -> str:
    """生成极短的游戏列表摘要，仅用于 LLM 工具 description（不列具体指令）。"""
    parts = []
    for game in registry.games:
        parts.append(f"{game.name}（{game.description}）")
    return "；".join(parts) if parts else "暂无游戏"


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
        self.brain = GameBrain(self.plugin, self.registry, self.cfg,
                               self.llm, self.push, self.img, self.cfg_mgr, self.tts)
        self._wire_llm()
        # 将插件服务注入注册表，游戏注册后可调用
        self.registry._push = self.push
        self.registry._img = self.img
        self.registry._tts = self.tts
        self.registry._llm = self.llm
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
            "用户想玩小游戏时调用。传入用户说的原话，插件自动判断玩什么游戏。"
            f"可用游戏：{summary}"
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

    def parse_input(self, input: str) -> tuple[Optional[str], str]:
        """解析用户输入，返回 (game_id, cmd)。遍历已注册游戏匹配关键词。"""
        input = input.strip()
        if not input:
            return None, ""
        for game in self.registry.games:
            for kw in game.get_keywords():
                if kw and kw in input:
                    return game.id, input
        if self.brain and self.brain.current_game:
            return self.brain.current_game, input
        return None, input

    def _wire_llm(self) -> None:
        """主插件 LLM 接口：配置了 llm_main_* → 所有游戏走新 LLM；没配 → 宿主。

        (用户要求: 配置优先于宿主, 而不是宿主优先。)
        """
        if not self.brain:
            return
        provider = self.cfg.get("llm_main_provider", "")
        model = self.cfg.get("llm_main_model", "")
        if provider and model:
            self.llm.set_client(provider, model,
                                self.cfg.get("llm_main_api_key", ""),
                                self.cfg.get("llm_main_base_url", ""))
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