"""猫娘小游戏：游戏大脑总管家。"""

from __future__ import annotations

from typing import Any

from plugin.sdk.plugin import (
    Err,
    NekoPluginBase,
    Ok,
    SdkError,
    lifecycle,
    llm_tool,
    neko_plugin,
    plugin_entry,
)

PUSH_SOURCE = "neko_arcade"
STORE_PREFIX = "game_user_data"


@neko_plugin
class NekoArcadePlugin(NekoPluginBase):
    """猫娘小游戏 —— 大脑总管家。"""

    def __init__(self, ctx: Any) -> None:
        super().__init__(ctx)
        self.logger = self.enable_file_logging(log_level="INFO")
        self.rt: Any = None

    # ui.context 由 plugin.toml 静态配置，装饰器在运行时动态注册
    async def ctx_dashboard(self) -> dict:
        """Hosted UI / static 面板共用的状态入口。"""
        if not self.rt:
            return {"games": [], "statuses": {}, "brain": {}, "started": False}
        return await self.entry_get_arcade_state()

    @lifecycle(id="startup")
    async def startup(self, **_) -> Any:
        # 延迟导入，避免模块加载时级联导入导致部分加载
        from .core import ArcadeRuntime

        # 静态 UI 必须在同步段注册，路由才能找到 /plugin/neko_arcade/ui/；
        # plugin.toml [[plugin.ui.panel]] entry 指向 static/index.html。
        if (self.config_dir / "static").exists():
            ok = self.register_static_ui(
                "static",
                index_file="index.html",
                cache_control="no-cache, no-store, must-revalidate",
            )
            if ok:
                self.logger.info("已注册猫娘小游戏面板: /plugin/neko_arcade/ui/")
            else:
                self.logger.warning("注册静态 UI 失败，请检查 static/index.html 是否存在")
        self.rt = ArcadeRuntime(self)
        count = await self.rt.start()
        return Ok({"status": "ready", "games_loaded": count, "brain": True})

    @lifecycle(id="shutdown")
    async def shutdown(self, **_) -> Any:
        if self.rt:
            await self.rt.shutdown()
        return Ok({"status": "shutdown"})

    async def store_get_user(self, game_id: str, user_id: str, default: Any = None) -> Any:
        key = f"{STORE_PREFIX}:{game_id}:{user_id}"
        val = await self.store.get(key)
        # SDK store.get 返回 Ok/Err 包装（数据在 Ok.value），统一解开再返回
        return self.unwrap_result(val, default=default)

    @staticmethod
    def unwrap_result(val: Any, default: Any = None) -> Any:
        """解开 SDK 的 Ok/Err 包装（兼容 value / data 两种字段名）。"""
        if val is None:
            return default
        # Err 包装 → 视为无数据，用默认值
        is_err = getattr(val, "is_err", None)
        if callable(is_err) and is_err():
            return default
        # Ok(...).value（SDK v2） / Ok(...).data（旧版兼容）
        for field in ("value", "data"):
            if hasattr(val, field):
                inner = getattr(val, field)
                return inner if inner is not None else default
        return val

    async def store_save_user(self, game_id: str, user_id: str, data: Any) -> None:
        key = f"{STORE_PREFIX}:{game_id}:{user_id}"
        await self.store.set(key, data)

    @plugin_entry(id="list_games", name="游戏列表", description="列出所有可玩的小游戏。",
                  input_schema={"type": "object", "properties": {}},
                  llm_result_fields=["message"])
    async def entry_list_games(self, **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        games = self.rt.registry.get_meta_list()
        if not games:
            return Ok({"games": [], "message": "暂时没有小游戏喵"})
        lines = "\n".join(f"  {g['icon']} {g['name']}：{g['description']}" for g in games)
        return Ok({"games": games, "message": f"猫娘小游戏里有这些游戏喵：\n{lines}"})

    @plugin_entry(id="play_game", name="玩游戏",
                  description="执行游戏指令。game填游戏名，cmd填用户原话，插件自动匹配具体指令。可用游戏通过list_games查看。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏名"},
                      "cmd": {"type": "string", "description": "用户原话，插件自动匹配指令"},
                      "args": {"type": "object", "description": "可选参数"},
                  }, "required": ["game", "cmd"]},
                  # 宿主用 summary 字段拼装任务结果喂给对话 LLM，猫娘才能对游戏结果有反馈
                  llm_result_fields=["summary"])
    async def entry_play_game(self, game: str = "", cmd: str = "", args: dict = None, **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        game_obj = self.rt.registry.get(game) or self.rt.registry.get_by_name(game)
        if not game_obj:
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        result = await self.rt.brain.handle_action(game_obj.id, cmd, args or {}, user_id)
        # 兜底：确保返回给宿主的 dict 一定带可读 summary（宿主按 llm_result_fields 提取）
        if isinstance(result, dict):
            result.setdefault("summary",
                              result.get("game_result") or result.get("message")
                              or result.get("game_docs") or "")
        return Ok(result)

    @plugin_entry(id="start_game", name="启动游戏", description="仅用于面板手动启动游戏会话。",
                  input_schema={"type": "object", "properties": {"game": {"type": "string"}}, "required": ["game"]},
                  metadata={"agent_hidden": True})
    async def entry_start_game(self, game: str = "", **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        result = await self.rt.brain.start_game(game)
        return Ok(result)

    @plugin_entry(id="stop_game", name="停止游戏", description="停止当前游戏会话。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_stop_game(self, **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        result = await self.rt.brain.stop_game()
        return Ok(result)

    @plugin_entry(id="game_status", name="游戏状态", description="查看小游戏当前状态。",
                  input_schema={"type": "object", "properties": {"game": {"type": "string", "description": "游戏 id 或名称"}}, "required": ["game"]})
    async def entry_game_status(self, game: str = "", **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        game_obj = self.rt.registry.get(game) or self.rt.registry.get_by_name(game)
        if not game_obj:
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        status = await game_obj.get_status(user_id)
        return Ok({"game": game_obj.id, "status": status})

    @plugin_entry(id="set_game_enabled", name="开关游戏", description="启用或停用某个小游戏。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                      "enabled": {"type": "boolean", "description": "true 启用 / false 停用"},
                  }, "required": ["game", "enabled"]},
                  metadata={"agent_hidden": True})
    async def entry_set_game_enabled(self, game: str = "", enabled: bool = True, **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        ok = await self.rt.registry.set_enabled(game, enabled)
        if not ok:
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        return Ok({"game": game, "enabled": enabled})

    @plugin_entry(id="game_help", name="游戏帮助",
                  description="查看并推送小游戏的帮助文档图。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                  }, "required": ["game"]},
                  llm_result_fields=["message"])
    async def entry_game_help(self, game: str = "", **_) -> Any:
        if not self.rt or not self.rt.brain:
            return Err(SdkError("猫娘小游戏还没准备好"))
        result = await self.rt.brain.show_help(game)
        return Ok(result)

    @plugin_entry(id="get_game_config", name="获取游戏配置",
                  description="获取某个小游戏的配置和帮助数据。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                  }, "required": ["game"]},
                  metadata={"agent_hidden": True})
    async def entry_get_game_config(self, game: str = "", **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        if not self.rt.registry.get(game):
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        gc = self.rt.cfg_mgr.load(game)
        panel = self.rt.registry.get_panel(game)
        return Ok({"game": game, "config": gc.config, "help": gc.help, "panel": panel})

    @staticmethod
    def _deep_merge(base: dict, update: dict) -> dict:
        """深度合并配置：update 覆盖 base，base 里未提到的键保留。"""
        out = dict(base or {})
        for k, v in (update or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = NekoArcadePlugin._deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    @plugin_entry(id="save_game_config", name="保存游戏配置",
                  description="保存小游戏的配置参数。",
                  input_schema={"type": "object", "properties": {
                      "game": {"type": "string", "description": "游戏 id"},
                      "config": {"type": "object", "description": "配置键值"},
                  }, "required": ["game", "config"]},
                  metadata={"agent_hidden": True})
    async def entry_save_game_config(self, game: str = "", config: dict = None, **_) -> Any:
        if not self.rt:
            return Err(SdkError("猫娘小游戏还没准备好"))
        if not self.rt.registry.get(game):
            return Err(SdkError(f"没有找到游戏「{game}」喵"))
        gc = self.rt.cfg_mgr.load(game)
        merged = self._deep_merge(gc.config, config or {})
        self.rt.cfg_mgr.save(game, merged)
        return Ok({"game": game, "saved": True})

    @plugin_entry(id="get_arcade_state", name="获取街机状态", description="供面板轮询。",
                  input_schema={"type": "object", "properties": {}},
                  metadata={"agent_hidden": True})
    async def entry_get_arcade_state(self, **_) -> Any:
        if not self.rt:
            return Ok({"games": [], "statuses": {}, "brain": {}, "started": False})
        user_id = getattr(self.ctx, "user_id", "default") or "default"
        games = self.rt.registry.get_meta_list(with_help=True)
        statuses = await self.rt.registry.get_statuses(user_id)
        brain_snap = await self.rt.brain.snapshot() if self.rt.brain else {}
        enabled = self.rt.registry.enabled_ids()
        return Ok({"games": games, "statuses": statuses, "brain": brain_snap,
                   "started": True, "enabled_count": len(enabled)})

    @llm_tool(name="play_game", description="用户想玩小游戏时调用。传入用户说的原话，插件自动判断玩什么游戏。",
              parameters={"type": "object", "properties": {
                  "input": {"type": "string", "description": "用户说的原话，插件自动匹配游戏和指令"},
              }, "required": ["input"]})
    async def tool_play_game(self, input: str) -> Any:
        if not self.rt:
            return {"error": "猫娘小游戏还没准备好"}
        game_id, cmd = self.rt.parse_input(input)
        if not game_id:
            return {"error": f"没有找到匹配的游戏喵，试试：{self.rt.registry.game_names()}"}
        result = await self.entry_play_game(game=game_id, cmd=cmd)
        # 从 Ok/Err 包装中解出数据（SDK v2: Ok.value；旧版: Ok.data）
        return self.unwrap_result(result, default={"error": "游戏执行失败喵"})