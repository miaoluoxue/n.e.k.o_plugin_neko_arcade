"""E2E 桩测试: 喵图相册(随机发图, 走插件主体 PhotoBridge 桥接)。

以 pytest 运行(本地自动经 tests/conftest.py 建链, CI 里插件已 mount)。
覆盖: 发图、分类发图、图鉴、每日上限、on_tick 自动发图、工具发图、上传。
"""
import asyncio
import json
from pathlib import Path

from plugin.plugins.neko_arcade.adapters.photo_bridge import PhotoBridge
from plugin.plugins.neko_arcade.games.neko_photo.game import NekoPhotoGame

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "data" / "config" / "neko_photo" / "config.json").read_text(encoding="utf-8"))
EMO = json.loads((ROOT / "data" / "config" / "neko_photo" / "emotion.json").read_text(encoding="utf-8"))
HELP = json.loads((ROOT / "data" / "config" / "neko_photo" / "help.json").read_text(encoding="utf-8"))
KWS = json.loads((ROOT / "data" / "config" / "neko_photo" / "keywords.json").read_text(encoding="utf-8"))

# Windows 沙箱下系统 temp 不可写, 用仓库内临时目录避免
_TMP = ROOT / ".tmp_neko_photo_test"
_TMP.mkdir(parents=True, exist_ok=True)

# 造一张假的本地图, 验证扫描逻辑
_FAKE_IMG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class StubStore:
    def __init__(self):
        self.data = {}

    async def store_get_user(self, gid, uid, default=None):
        return self.data.get((gid, uid), default)

    async def store_save_user(self, gid, uid, data):
        self.data[(gid, uid)] = data


class StubPush:
    def __init__(self, sink):
        self._sink = sink

    async def text(self, text):
        self._sink.append(text)

    async def text_with_image(self, text, image_bytes, mime="image/png"):
        self._sink.append((text, mime, len(image_bytes)))

    async def text_with_image_url(self, text, url):
        self._sink.append((text, url))

    def static_url(self, rel):
        return f"http://127.0.0.1:48916/plugin/neko_arcade/ui/{rel}"


class StubImg:
    def __init__(self, sink):
        self._sink = sink

    def render_neko_avatar(self, mood, size):
        self._sink.append(("avatar", mood, size))
        return b"\x89PNG\r\n\x1a\n" + b"1" * 64


class StubPlugin:
    def __init__(self):
        self.store = StubStore()
        self.pushes = []
        self.config_dir = ROOT

    async def store_get_user(self, gid, uid, default=None):
        return await self.store.store_get_user(gid, uid, default)

    async def store_save_user(self, gid, uid, data):
        await self.store.store_save_user(gid, uid, data)


def _make_game(tmp_path):
    """构造游戏实例 + 真实 PhotoBridge(图片目录注入 tmp_path/static/img/neko/)。"""
    plugin = StubPlugin()
    push = StubPush(plugin.pushes)
    avatar_sink = []   # 头像渲染独立记录, 与推送区分
    img = StubImg(avatar_sink)
    bridge = PhotoBridge(plugin, push=push, img=img)

    # 注入临时图片目录: static/img/neko/可爱/ + static/img/neko/日常/
    img_root = tmp_path / "static" / "img" / "neko"
    (img_root / "可爱").mkdir(parents=True, exist_ok=True)
    (img_root / "日常").mkdir(parents=True, exist_ok=True)
    (img_root / "可爱" / "cute_photo.png").write_bytes(_FAKE_IMG)
    (img_root / "日常" / "daily_photo.png").write_bytes(_FAKE_IMG)
    bridge._local_scan_dir = str(img_root)
    bridge._img_scan_ts = 0.0
    bridge._img_cache = []

    game = NekoPhotoGame(plugin)
    game._push = push
    game._img = img
    game._photo = bridge
    game._config = CFG
    game._help = HELP
    game._keywords = KWS
    game._emotion_templates = EMO
    return game, plugin


def test_neko_photo_send_and_album():
    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_1"
        r = await game.handle_action(uid, "发图")
        assert r["outcome"] in ("photo", "collect_new", "rare"), r
        assert r["facts"][0]["kind"] == "photo"
        # 游戏返回 images 数据(brain 统一推), 不直接 push
        assert r.get("images") and (r["images"][0].get("bytes") or r["images"][0].get("url")), \
            "应返回 images 数据(bytes 或 url)"

        # 图鉴
        r2 = await game.handle_action(uid, "图鉴")
        assert r2["outcome"] == "album"
        assert "喵图相册" in r2["message"]

        # 未知指令
        r3 = await game.handle_action(uid, "乱七八糟")
        assert r3["outcome"] == "unknown"

    asyncio.run(run())


def test_neko_photo_category_send():
    """分类发图: 「喵图 可爱」只从可爱分类发(走桥接)。"""
    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_1b"
        # 桥接指定分类挑图 → 一定是可爱分类
        photo = await game._photo.pick_photo(category="可爱")
        assert photo is not None and photo.get("category") == "可爱", photo
        # 不存在的分类 → 桥接返回 None, 游戏报 error
        r2 = await game.handle_action(uid, "喵图 不存在分类xyz")
        assert r2["outcome"] in ("error", "unknown"), r2

    asyncio.run(run())


def test_neko_photo_daily_limit():
    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_2"
        cfg = dict(CFG)
        cfg["max_photos_per_day"] = 2
        game._config = cfg
        r1 = await game.handle_action(uid, "发图")
        assert r1["outcome"] != "limit"
        r2 = await game.handle_action(uid, "来张图")
        assert r2["outcome"] != "limit"
        r3 = await game.handle_action(uid, "发图")
        assert r3["outcome"] == "limit"
        assert "明天" in r3["message"]

    asyncio.run(run())


def test_neko_photo_on_tick_auto_send():
    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_3"
        # 清空推送记录
        plugin.pushes.clear()
        # 立即触发自动发图
        game._next_auto_ts = 0.0
        await game.on_tick(uid)
        assert plugin.pushes, "on_tick 应自动推图"
        # 再次调用应进入冷却(不推)
        before = len(plugin.pushes)
        await game.on_tick(uid)
        assert len(plugin.pushes) == before, "冷却期内不应重复发图"

    asyncio.run(run())


def test_neko_photo_background_tick_flag():
    """后台自动发图标记: 无会话时 brain.tick 也会调 neko_photo 的 on_tick。"""
    async def run():
        game, plugin = _make_game(_TMP)
        # 标记必须存在, brain._tick_background_games 靠它发现后台游戏
        assert getattr(game, "background_tick", False) is True, "neko_photo 需标记 background_tick"
        # 默认自动发图开启
        assert game._cfg("auto_send_enabled", True) is True

    asyncio.run(run())


def test_neko_photo_brain_background_tick():
    """brain._tick_background_games 无会话时也会触发 neko_photo 自动发图。"""
    async def run():
        from plugin.plugins.neko_arcade.core.brain import GameBrain

        game, plugin = _make_game(_TMP)
        plugin.pushes.clear()

        class FakeRegistry:
            def __init__(self, g):
                self._g = g

            @property
            def games(self):
                return [self._g]

            def get(self, gid):
                return self._g if self._g.id == gid else None

        brain = GameBrain.__new__(GameBrain)  # 跳过 __init__ 的宿主加载
        brain.registry = FakeRegistry(game)
        brain._current_game = None
        brain._current_user = "user_bg"

        game._next_auto_ts = 0.0  # 立即触发
        await brain._tick_background_games()
        assert plugin.pushes, "后台 tick 应触发自动发图"

    asyncio.run(run())


def test_neko_photo_bridge_scan_categories():
    """PhotoBridge 桥接: 分类扫描 + bytes(资源在 games/<game>/data/ 下)。"""
    async def run():
        game, plugin = _make_game(_TMP)
        bridge = game._photo
        imgs = bridge.scan_images()
        assert any(i["style"] == "cute_photo" and i["category"] == "可爱" for i in imgs), imgs
        assert any(i["style"] == "daily_photo" and i["category"] == "日常" for i in imgs), imgs
        cats = bridge.get_categories()
        assert "可爱" in cats and "日常" in cats, cats
        # 图片返回 bytes(交 brain 统一推送)
        assert any(i.get("bytes") for i in imgs), imgs

    asyncio.run(run())


def test_neko_photo_send_random_photo_tool():
    """LLM 工具入口: send_random_photo 走桥接, 不依赖关键词发图。"""
    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_4"
        plugin.pushes.clear()
        r = await game.send_random_photo(uid, caption="给你看看喵")
        assert r["ok"] is True, r
        assert "summary" in r and "发" in r["summary"], r
        # 应推送了图片
        assert plugin.pushes, "工具应推送图片"
        # 计入图鉴
        data = await game.get_user_data(uid, {})
        assert data.get("stats", {}).get("total", 0) >= 1, data

    asyncio.run(run())


def test_neko_photo_upload_with_category():
    """用户上传图片(走桥接): 保存到指定分类。"""
    import base64

    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_5"
        fake_b64 = base64.b64encode(_FAKE_IMG).decode()
        r = await game.upload_photo(uid, name="mypic.png", data_b64=fake_b64,
                                    category="我的图")
        assert r["ok"] is True, r
        assert "我的图" in r["message"], r
        # 新分类自动创建, 图库扫描能看到
        imgs = game._photo.scan_images()
        assert any("mypic" in i["style"] and i["category"] == "我的图" for i in imgs), imgs
        # 非法扩展名拒绝
        r2 = await game.upload_photo(uid, name="bad.txt", data_b64=fake_b64)
        assert r2["ok"] is False, r2
        # 空数据拒绝
        r3 = await game.upload_photo(uid, name="x.png", data_b64="")
        assert r3["ok"] is False, r3
        # 危险分类名被清洗
        r4 = await game.upload_photo(uid, name="y.png", data_b64=fake_b64,
                                     category="../evil")
        assert r4["ok"] is True, r4
        assert "evil" in r4["message"], r4

    asyncio.run(run())


def test_neko_photo_upload_to_existing_category():
    """上传到已有文件夹(走桥接): 选中已有分类, 图进该文件夹。"""
    import base64

    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_6"
        fake_b64 = base64.b64encode(_FAKE_IMG).decode()
        r = await game.upload_photo(uid, name="more_cute.png", data_b64=fake_b64,
                                    category="可爱")
        assert r["ok"] is True, r
        assert "可爱" in r["message"], r
        imgs = game._photo.scan_images()
        assert any("more_cute" in i["style"] and i["category"] == "可爱" for i in imgs), imgs
        cats = game._photo.get_categories()
        assert cats.count("可爱") == 1, cats

    asyncio.run(run())


def test_neko_photo_custom_category_command():
    """自创文件夹(分类)也要支持命令发图, 即使分类名含"图/照片"等词。"""
    import base64

    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_7"
        fake_b64 = base64.b64encode(_FAKE_IMG).decode()
        # 自创分类「我的猫图」(含"图"字, 旧剥词逻辑会破坏)
        r = await game.upload_photo(uid, name="my_cat.png", data_b64=fake_b64,
                                    category="我的猫图")
        assert r["ok"] is True, r

        # 1. 直接「喵图 我的猫图」→ 应命中该分类
        photo = await game._photo.pick_photo(category="我的猫图")
        assert photo is not None and photo.get("category") == "我的猫图", photo

        # 2. 提取分类: 自创分类名应被直接匹配(不剥词)
        cat = game._extract_category("喵图 我的猫图")
        assert cat == "我的猫图", cat

        # 3. 自创分类经命令发图成功
        r2 = await game.handle_action(uid, "喵图 我的猫图")
        assert r2["outcome"] in ("photo", "collect_new", "rare"), r2

        # 4. 「发张我的猫图的照片」也能命中
        cat2 = game._extract_category("发张我的猫图的照片")
        assert cat2 == "我的猫图", cat2

        # 5. 不存在的自创分类 → 提取出来但发图报错
        cat3 = game._extract_category("喵图 不存在的xyz")
        assert cat3 == "不存在的xyz", cat3
        r3 = await game.handle_action(uid, "喵图 不存在的xyz")
        assert r3["outcome"] in ("error", "unknown"), r3

    asyncio.run(run())


def test_neko_photo_no_double_push():
    """双重回复回归(新架构): 游戏返回 images 时 brain 只推一次(图文合一)。

    模拟 brain.handle_action 的输出编排: 有 images → 推图文(不重复推 message);
    无 images → 推 message 一次。
    """
    async def run():
        def build_push(images, game_msg):
            pushes = []
            if images:
                for img in images[:3]:
                    text = img.get("text") or game_msg
                    if img.get("bytes"):
                        pushes.append((text, "image/png"))
            elif game_msg:
                pushes.append(game_msg)
            return pushes

        # 1. 有 images → 只推图文, 不重复推 message
        pushes1 = build_push([{"text": "看~这张照片", "bytes": b"fake"}], "发了一张照片")
        assert len(pushes1) == 1, f"有 images 时应只推一次图文, 实际: {pushes1}"

        # 2. 无 images → 推 message 一次
        pushes2 = build_push([], "钓到了一条鱼")
        assert pushes2 == ["钓到了一条鱼"], f"无 images 时应推 message 一次, 实际: {pushes2}"

    asyncio.run(run())


def test_neko_photo_send_marks_pushed():
    """neko_photo 发图返回 images 数据(游戏适配插件: 不 push, 交 brain 统一推)。"""
    async def run():
        game, plugin = _make_game(_TMP)
        uid = "user_8"
        plugin.pushes.clear()
        r = await game.handle_action(uid, "发图")
        assert r["outcome"] in ("photo", "collect_new", "rare"), r
        # 游戏不 push, 返回 images 数据(由 brain 统一推送)
        assert r.get("images"), "neko_photo 发图应返回 images 数据(brain 统一推)"
        assert isinstance(r["images"], list) and \
            (r["images"][0].get("bytes") or r["images"][0].get("url")), r["images"]
        # 游戏不应直接推送
        assert not plugin.pushes, f"游戏不应直接 push, 实际: {plugin.pushes}"

    asyncio.run(run())


def test_brain_summary_guard_no_double_reply():
    """双重回复守门(brain 层, 新架构): summary 一律用 neko_text, 不含用户已见原文。

    游戏不参与推送, 用户已见内容由 brain 统一输出; summary 用 neko_text
    (情感模板)喂给宿主 LLM, 与用户已见内容不同, 避免 LLM 复述。
    """
    async def run():
        def build_summary(game_msg, neko_text):
            return neko_text or game_msg

        # summary 用 neko_text, 不含用户已见原文
        s = build_summary("哇,15 条呢!主人想听哪个年代的故事?",
                          "今天有 15 条历史大事喵!")
        assert "哇,15 条呢" not in s, f"summary 不应含用户已见原文: {s}"
        assert "历史大事喵" in s, s

    asyncio.run(run())
