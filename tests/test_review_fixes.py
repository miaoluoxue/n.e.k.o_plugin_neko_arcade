"""回归测试：外部深评指出的四处问题（2026-10 修复）。

1. 台词复读 —— 同一事件+心情反复拿到一模一样的一句
   · 旧: 缓存 key → 单个字符串, 命中就原样返回; 模板兜底纯随机、无"刚说过"记忆
   · 新: 每个 key 存多条变体轮换 + 全层"最近说过"去重 + 提示词带上最近几句
2. 会话单槽 —— 多人同时玩互相串台, 且只存内存、重启丢
   · 新: user_id → game_id 分槽, 落盘(store)并在启动时恢复
3. 猜硬币 —— 任意输入都被当成「反」, 还照样扣掉一次机会
   · 新: 只认明确的正/反, 认不出就再问一次(不消耗次数)
4. runtime 绝对导入 —— 宿主 sys.path 不含插件目录时启动 ImportError
   · 新: 相对导入 + try/except 降级
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import pytest
from plugin.plugins.neko_arcade.core.brain import GameBrain
from plugin.plugins.neko_arcade.core.companion import Companion
from plugin.plugins.neko_arcade.core.memory import GameMemory
from plugin.plugins.neko_arcade.core.session import SessionKey
from plugin.plugins.neko_arcade.games.coinflip.game import CoinFlipGame, parse_pick

ROOT = Path(__file__).resolve().parent.parent


# ── 1. 台词不复读 ─────────────────────────────
class _CfgStub:
    """cfg_mgr.load(game_id) → 带 emotion_templates 的对象。"""

    def __init__(self, templates):
        self._t = templates

    def load(self, _game_id):
        class _G:
            emotion_templates = self._t
        return _G()


class _SameLLM:
    """永远返回同一句话的假 LLM：最能暴露"复读"。"""

    def __init__(self, text="喵又赢了。"):
        self.text = text
        self.calls = 0
        self._client = object()          # 让 Companion._llm_ready() 为真

    async def call(self, _prompt):
        self.calls += 1
        return self.text


def _companion(templates, llm=None):
    cfg = {"enable_llm_lines": True}
    return Companion(persona=None, llm=llm, config_manager=_CfgStub(templates),
                     cfg=cfg, rng=random.Random(7))


def test_templates_do_not_repeat_back_to_back():
    templates = {"win": ["赢啦喵！", "又赢了，主人真强~", "嘿嘿，这局我赢了"]}
    comp = _companion(templates)                     # 无 LLM → 全走模板
    got = [asyncio.run(comp.line("fishing", "win")) for _ in range(3)]
    assert len(set(got)) == 3, f"三条模板应该各不相同, 实际: {got}"
    assert comp._recent, "应该记录最近说过的台词"


def test_llm_same_line_is_not_repeated():
    """LLM 每次都返回同一句话 → 第二次必须换一句（不能原样复读）。"""
    templates = {"win": ["赢啦喵！", "又赢了主人~"]}
    llm = _SameLLM("喵又赢了。")
    comp = _companion(templates, llm=llm)
    first = asyncio.run(comp.line("fishing", "win"))
    second = asyncio.run(comp.line("fishing", "win"))
    assert first == "喵又赢了。"
    assert second != first, "同一句台词不能连着复读"


def test_no_duplicate_when_no_alternative_exists():
    """没有模板也没有别的变体时宁可返回空串，也不能把上一句原样再播一次。"""
    llm = _SameLLM("唯一一句")
    comp = _companion({}, llm=llm)
    first = asyncio.run(comp.line("fishing", "done"))
    second = asyncio.run(comp.line("fishing", "done"))
    assert first == "唯一一句"
    assert second == "", "无替代句时必须交给调用方使用游戏原始结果"


def test_prompt_lists_recent_lines():
    """提示词里要带上"刚才说过这些", 让模型主动避开。"""
    comp = _companion({"win": ["赢啦喵！"]}, llm=_SameLLM())
    asyncio.run(comp.line("fishing", "win"))
    prompt = comp._prompt("win", {}, "", "", "")
    assert "刚才已经说过" in prompt and "喵又赢了" in prompt


# ── 2. 会话按用户分槽 + 持久化 ──────────────────
class _FakeMood:
    def snapshot(self):
        return {"mood": "calm"}

    def style(self):
        return "calm"

    def decay_all(self):
        pass


class _FakePersona:
    def __init__(self):
        self.mood = _FakeMood()
        self.user_call = "主人"

    def on_event(self, _kind):
        pass

    def polish(self, text):
        return text

    def snapshot(self):
        return {}


class _FakeStore:
    """内存版 SDK store。"""

    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value


class _FakePlugin:
    def __init__(self):
        self.store = _FakeStore()


class _FakeGame:
    def __init__(self, gid, name):
        self.id = gid
        self.name = name
        self.icon = "🎮"
        self.enabled = True
        self.started = []
        self.stopped = []
        self.last_cmd = None
        self.seen_users = []
        self.outcome = "done"

    def get_keywords(self):
        return [self.name]

    def get_emotion_templates(self):
        return {}

    def classify_event(self, _outcome, _facts):
        return "routine"

    def wants_card(self, *_a):
        return False

    async def on_start(self, uid):
        self.started.append(uid)

    async def on_stop(self, uid):
        self.stopped.append(uid)

    async def on_milestone(self, *_a):
        pass

    async def handle_action(self, uid, cmd, args=None):
        self.last_cmd = cmd
        self.seen_users.append(uid)
        return {"facts": [], "outcome": self.outcome, "message": f"{self.name}收到"}


class _FakeRegistry:
    def __init__(self, games):
        self.games = list(games)
        self._by_id = {g.id: g for g in games}

    def get(self, gid):
        return self._by_id.get(gid)

    def get_by_name(self, name):
        for g in self.games:
            if name and (name in g.name or name in g.id):
                return g
        return None

    async def get_help(self, _gid):
        return {"commands": []}


class _FakePush:
    def __init__(self):
        self.texts = []

    async def text(self, text, **kw):
        self.texts.append(text)


def _make_brain(games, plugin=None):
    brain = GameBrain.__new__(GameBrain)
    brain.registry = _FakeRegistry(games)
    brain.persona = _FakePersona()
    brain.memory = GameMemory(plugin or _FakePlugin())
    brain.proactive = type("P", (), {"tick": lambda s: None, "on_result": lambda s, o: None,
                                     "on_owner_speak": lambda s: None,
                                     "snapshot": lambda s: {}})()
    brain.emotion = type("E", (), {"render": lambda s, *a, **k: _coro(("", "routine"))})()
    brain.push = _FakePush()
    brain.tts = None
    brain.img = None
    brain.cfg = {}
    brain.plugin = None
    brain.cfg_mgr = None
    brain._sessions = {}
    brain._session_start_by_key = {}
    brain._current_session = None
    brain._session_start = 0.0
    brain._sessions_dirty = False
    brain._last_anchor_ts = 0.0
    brain._last_anchor_ts_by_session = {}
    brain._anchor_interval = 20.0
    brain._last_invite = {}
    brain._pending_switch = {}
    brain._switch_confirm_ttl = 180.0
    brain._last_activity_ts = 0.0
    brain._last_activity_ts_by_session = {}
    brain._background_active_window = 900.0
    brain._recent_inputs = {}
    brain._last_game_by_session = {}
    brain._companions = {}
    brain._llm_provider = None
    brain._proactive_by_session = {}
    brain.companion = Companion(persona=brain.persona, llm=None)
    return brain


async def _coro(value):
    return value


def test_sessions_are_isolated_per_user():
    """A 玩钓鱼、B 玩修仙: 两个会话并存, 输入各走各的, 停一个不影响另一个。"""
    fishing = _FakeGame("fishing", "钓鱼")
    xiuxian = _FakeGame("xiuxian", "修仙")
    brain = _make_brain([fishing, xiuxian])

    asyncio.run(brain.handle_action("fishing", "钓鱼", {}, user_id="alice"))
    asyncio.run(brain.handle_action("xiuxian", "修仙", {}, user_id="bob"))

    assert brain.session_game("alice") == "fishing"
    assert brain.session_game("bob") == "xiuxian", "B 的游戏不能顶掉 A 的"
    assert {key.storage_id for key in brain.active_sessions()} == {"alice", "bob"}

    # A 继续下棋指令 → 只走钓鱼; B 不受影响
    asyncio.run(brain.handle_action("fishing", "抛竿", {}, user_id="alice"))
    assert fishing.last_cmd == "抛竿"
    assert brain.session_game("bob") == "xiuxian"

    # 停 A → B 还在玩
    asyncio.run(brain.stop_game(user_id="alice"))
    assert brain.session_game("alice") is None
    assert brain.session_game("bob") == "xiuxian"
    assert fishing.stopped == ["alice"]


def test_same_user_on_different_channels_uses_different_game_state():
    fishing = _FakeGame("fishing", "钓鱼")
    brain = _make_brain([fishing])
    qq = SessionKey("qq", "alice")
    web = SessionKey("web", "alice")

    asyncio.run(brain.handle_action("fishing", "钓鱼", {}, session=qq))
    asyncio.run(brain.handle_action("fishing", "钓鱼", {}, session=web))

    assert brain.session_game(qq) == "fishing"
    assert brain.session_game(web) == "fishing"
    assert fishing.seen_users[-2:] == [qq.storage_id, web.storage_id]
    assert len(set(fishing.seen_users[-2:])) == 2


def test_real_game_save_is_separate_for_same_user_on_two_channels():
    class _StorePlugin:
        def __init__(self):
            self.values = {}

        async def store_get_user(self, game_id, user_id, default=None):
            return self.values.get((game_id, user_id), default)

        async def store_save_user(self, game_id, user_id, data):
            self.values[(game_id, user_id)] = data

    plugin = _StorePlugin()
    game = CoinFlipGame(plugin)
    game._config = {"max_plays_per_day": 20}
    qq = SessionKey("qq", "alice").storage_id
    web = SessionKey("web", "alice").storage_id

    asyncio.run(game.handle_action(qq, "猜硬币 正"))
    asyncio.run(game.handle_action(web, "猜硬币 反"))
    assert (game.id, qq) in plugin.values
    assert (game.id, web) in plugin.values
    assert qq != web


def test_memory_and_stats_are_scoped_per_session():
    plugin = _FakePlugin()
    memory = GameMemory(plugin)
    qq = SessionKey("qq", "alice")
    web = SessionKey("web", "alice")

    memory.record("fishing", "win", [{"kind": "fish", "name": "鲤鱼"}],
                  "钓到鲤鱼", session_key=qq)
    memory.record("xiuxian", "breakthrough", [], "突破成功", session_key=web)
    asyncio.run(memory.register_play("fishing", session_key=qq))
    asyncio.run(memory.register_play("xiuxian", session_key=web))

    assert "fishing" in memory.recent_session(session_key=qq)
    assert "xiuxian" not in memory.recent_session(session_key=qq)
    assert "xiuxian" in memory.recent_session(session_key=web)
    qq_stats = asyncio.run(memory.get_stats("fishing", session_key=qq))
    web_stats = asyncio.run(memory.get_stats("fishing", session_key=web))
    assert qq_stats["plays"] == 1
    assert web_stats == {}


def test_sessions_persist_and_restore():
    fishing = _FakeGame("fishing", "钓鱼")
    plugin = _FakePlugin()                      # 同一个 store 模拟"重启后还在"
    brain = _make_brain([fishing], plugin=plugin)
    asyncio.run(brain.handle_action("fishing", "钓鱼", {}, user_id="alice"))
    saved = asyncio.run(brain.memory.load_sessions())
    assert saved == {"alice": "fishing"}, "会话要落盘"

    # 新实例(同一份存档) → 恢复同一个会话
    fresh = _make_brain([_FakeGame("fishing", "钓鱼")], plugin=plugin)
    n = asyncio.run(fresh.restore_sessions())
    assert n == 1
    assert fresh.session_game("alice") == "fishing"
    assert fresh.registry.get("fishing").started == ["alice"], "恢复时要跑 on_start"


def test_brain_initializes_companion_with_same_persona():
    plugin = _FakePlugin()
    registry = _FakeRegistry([_FakeGame("fishing", "钓鱼")])
    brain = GameBrain(plugin, registry, {}, None, _FakePush(), None)
    assert brain.companion.persona is brain.persona
    registry._companion = brain.companion
    assert registry._companion is not None


def test_post_action_failures_do_not_discard_game_result():
    class BrokenPersona(_FakePersona):
        def on_event(self, _kind):
            raise RuntimeError("mood failed")

    class BrokenEmotion:
        async def render(self, *_a, **_k):
            raise RuntimeError("lines failed")

    class BrokenPush(_FakePush):
        async def text(self, _text, **_kw):
            raise RuntimeError("push failed")

    game = _FakeGame("fishing", "钓鱼")
    game.outcome = "done"

    async def broken_milestone(*_a):
        raise RuntimeError("milestone failed")

    game.on_milestone = broken_milestone
    brain = _make_brain([game])
    brain.persona = BrokenPersona()
    brain.emotion = BrokenEmotion()
    brain.push = BrokenPush()
    res = asyncio.run(brain.handle_action("fishing", "抛竿", {}, user_id="alice"))
    assert res["game_result"] == "钓鱼收到"
    assert res["outcome"] == "done"
    assert "钓鱼收到" in res["summary"]


def test_memory_save_error_is_not_silently_accepted():
    class _Err:
        def is_err(self):
            return True

    class _Store:
        async def set(self, *_a, **_k):
            return _Err()

    memory = GameMemory(type("P", (), {"store": _Store()})())
    with pytest.raises(RuntimeError, match="游戏记忆写入失败"):
        asyncio.run(memory._save())


def test_session_save_failure_logs_warning():
    class _BrokenMemory:
        async def save_sessions(self, _sessions):
            raise OSError("disk full")

    brain = _make_brain([_FakeGame("fishing", "钓鱼")])
    brain.memory = _BrokenMemory()
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("neko_arcade.brain")
    old_level = logger.level
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        assert asyncio.run(brain._persist_sessions()) is False
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    assert any("会话落盘失败" in record.getMessage() for record in records)


def test_stale_session_for_removed_game_is_dropped():
    """存档里指向已删除游戏的会话, 恢复时跳过, 不写进会话表。"""
    brain = _make_brain([_FakeGame("fishing", "钓鱼")])
    asyncio.run(brain.memory.save_sessions({"alice": "ghost_game"}))
    fresh = _make_brain([_FakeGame("fishing", "钓鱼")])
    assert asyncio.run(fresh.restore_sessions()) == 0
    assert fresh.session_game("alice") is None


# ── 3. 猜硬币不再误判 ─────────────────────────
def test_coinflip_parses_only_explicit_side():
    assert parse_pick("正") == "正"
    assert parse_pick("猜硬币 正面") == "正"
    assert parse_pick("heads") == "正"
    assert parse_pick("反") == "反"
    assert parse_pick("猜硬币 反面") == "反"
    assert parse_pick("tails") == "反"
    for garbage in ("随便说点什么", "你好", "哈哈哈", "", "？"):
        assert parse_pick(garbage) is None, f"{garbage!r} 不该被当成正/反"


def test_coinflip_garbage_input_does_not_consume_a_play():
    game = CoinFlipGame(plugin=None)
    played = {}

    async def fake_get(uid, default):
        return played.setdefault(uid, dict(default or {}))

    async def fake_save(uid, data):
        played[uid] = data

    game.get_user_data = fake_get
    game.save_user_data = fake_save
    game._config = {}

    res = asyncio.run(game.handle_action("alice", "随便说点什么"))
    assert res["outcome"] == "need_pick", res
    # 认不出正反 → 根本没读/写存档, 自然也没扣次数
    assert played.get("alice", {}).get("plays_today", 0) == 0, "认不出正反时不该扣次数"
    assert res["facts"] == []

    res2 = asyncio.run(game.handle_action("alice", "猜硬币 正"))
    assert res2["outcome"] in ("win", "lose")
    assert played["alice"]["plays_today"] == 1


# ── 3b. 一局结束就退出游戏模式 ─────────────────
def test_round_end_finishes_session():
    """认输/分胜负 = 整局结束 → 自动收尾会话（旧版认输后还赖在游戏里）。"""
    bg = _FakeGame("boardgame", "棋类对弈")
    bg.end_session_outcomes = ("win", "lose", "draw", "finished", "resign")
    bg.outcome = "lose"                       # 认输
    brain = _make_brain([bg])
    res = asyncio.run(brain.handle_action("boardgame", "认输", {}, user_id="alice"))
    assert res["session_ended"] is True
    assert brain.session_game("alice") is None, "整局结束后要退出游戏模式"
    assert bg.stopped == ["alice"], "结束会话要跑 on_stop"
    assert "本局已结束" in res["summary"]


def test_mid_game_outcome_keeps_session():
    """中盘事件(普通落子/将军/悔棋)不能把会话结束掉。"""
    bg = _FakeGame("boardgame", "棋类对弈")
    bg.end_session_outcomes = ("lose",)
    bg.outcome = "board"
    brain = _make_brain([bg])
    res = asyncio.run(brain.handle_action("boardgame", "H8", {}, user_id="alice"))
    assert res["session_ended"] is False
    assert brain.session_game("alice") == "boardgame"


def test_endless_game_never_auto_ends():
    """钓鱼/修仙这类长期玩法(没声明 end_session_outcomes)永远不会被自动结束。"""
    fish = _FakeGame("fishing", "钓鱼")
    fish.outcome = "win"
    brain = _make_brain([fish])
    res = asyncio.run(brain.handle_action("fishing", "抛竿", {}, user_id="alice"))
    assert res["session_ended"] is False
    assert brain.session_game("alice") == "fishing"


# ── 4. runtime 只用相对导入 ───────────────────def test_runtime_has_no_absolute_package_imports():
    """宿主 sys.path 不含插件目录时, 绝对导入会让插件启动即 ImportError。"""
    src = (ROOT / "core" / "runtime.py").read_text(encoding="utf-8")
    offenders = [ln.strip() for ln in src.splitlines()
                 if ln.strip().startswith(("from adapters", "import adapters",
                                           "from core.", "import core.", "from games."))]
    assert offenders == [], f"runtime.py 里不该有绝对导入: {offenders}"
