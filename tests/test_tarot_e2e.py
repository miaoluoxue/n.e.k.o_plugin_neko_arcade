"""E2E 桩测试: 塔罗牌 — 真实加载 game.py + 桩存档 + 真实图片目录。

以 pytest 运行(本地自动经 tests/conftest.py 建链, CI 里插件已 mount)。
覆盖: 单张牌、指定牌、牌阵占卜、主题切换、未知指令。
"""
import asyncio
import json
from pathlib import Path

from plugin.plugins.neko_arcade.games.tarot.game import TarotGame

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "data" / "config" / "tarot" / "config.json").read_text(encoding="utf-8"))
EMO = json.loads((ROOT / "data" / "config" / "tarot" / "emotion.json").read_text(encoding="utf-8"))
HELP = json.loads((ROOT / "data" / "config" / "tarot" / "help.json").read_text(encoding="utf-8"))
KWS = json.loads((ROOT / "data" / "config" / "tarot" / "keywords.json").read_text(encoding="utf-8"))


class StubStore:
    def __init__(self):
        self.data = {}

    async def store_get_user(self, gid, uid, default=None):
        return self.data.get((gid, uid), default)

    async def store_save_user(self, gid, uid, data):
        self.data[(gid, uid)] = data


class StubPush:
    def __init__(self):
        self.pushes = []

    async def text(self, text):
        self.pushes.append(text)

    async def text_with_image(self, text, image_bytes, mime="image/png"):
        self.pushes.append((text, mime, len(image_bytes)))

    async def text_with_image_url(self, text, url):
        self.pushes.append((text, url))

    def static_url(self, rel):
        return f"http://127.0.0.1:48916/plugin/neko_arcade/ui/{rel}"


class StubPlugin:
    def __init__(self):
        self.store = StubStore()
        self.config_dir = ROOT

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)


def _make_game():
    plugin = StubPlugin()
    push = StubPush()
    game = TarotGame(plugin)
    game._push = push
    game._config = CFG
    game._help = HELP
    game._keywords = KWS
    game._emotion_templates = EMO
    return game, plugin


def test_tarot_single_card():
    async def run():
        game, plugin = _make_game()
        r = await game.handle_action("user_1", "塔罗牌")
        assert r["outcome"] in ("tarot", "divine", "help"), r
        assert r["facts"][0]["kind"] == "tarot"
        assert "正位" in r["message"] or "逆位" in r["message"], r
        # 应返回牌面图 images
        assert r.get("images") and r["images"][0].get("url"), r

    asyncio.run(run())


def test_tarot_specified_card():
    async def run():
        game, plugin = _make_game()
        # 指定"恋人"(The Lovers, 编号 6)
        r = await game.handle_action("user_1", "塔罗牌 恋人")
        assert r["outcome"] == "tarot", r
        assert "恋人" in r["message"], r
        # 英文名
        r2 = await game.handle_action("user_1", "塔罗牌 The Lovers")
        assert r2["outcome"] == "tarot", r2
        # 不存在的牌
        r3 = await game.handle_action("user_1", "塔罗牌 不存在的牌xyz")
        assert r3["outcome"] == "not_found", r3

    asyncio.run(run())


def test_tarot_divine():
    async def run():
        game, plugin = _make_game()
        r = await game.handle_action("user_1", "占卜")
        assert r["outcome"] == "divine", r
        assert "牌阵" in r["message"], r
        # 牌阵应抽多张 → 多张 facts + 多张 images
        assert len(r["facts"]) >= 3, r
        assert len(r["images"]) >= 3, r

    asyncio.run(run())


def test_tarot_theme_switch():
    async def run():
        game, plugin = _make_game()
        r = await game.handle_action("user_1", "塔罗主题 TouhouTarot")
        assert r["outcome"] == "theme", r
        assert game._theme == "TouhouTarot", game._theme
        # 切到 TouhouTarot 后抽牌 → 只可能出大阿卡纳
        r2 = await game.handle_action("user_1", "塔罗牌")
        assert r2["outcome"] == "tarot", r2
        # 切回
        await game.handle_action("user_1", "塔罗主题 BilibiliTarot")
        assert game._theme == "BilibiliTarot", game._theme

    asyncio.run(run())


def test_tarot_unknown():
    async def run():
        game, plugin = _make_game()
        r = await game.handle_action("user_1", "乱七八糟")
        assert r["outcome"] == "unknown", r

    asyncio.run(run())


def test_tarot_help():
    async def run():
        game, plugin = _make_game()
        r = await game.handle_action("user_1", "帮助")
        assert r["outcome"] == "help", r
        assert "塔罗" in r["message"], r

    asyncio.run(run())
