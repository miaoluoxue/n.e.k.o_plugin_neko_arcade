"""PushSender 单元测试: 原生图片通道优先 + 旧宿主 markdown 回退。

覆盖 text_with_image / text_with_image_url / help_doc 三条推送路径:
- 新宿主(#2835): ctx.images.upload 存在 → 原生图片 part + visibility=["chat"]
- 旧宿主: 无 ctx.images → 回退 static/cards markdown 图片语法 ![alt](url)
  (旧宿主前端不渲染 <img> HTML, 必须用标准 markdown 图片语法)
- 落盘前缩放: 大图最长边 ≤720px 转 JPEG, 防窄窗溢出(markdown 无 CSS 限制)

注意: Windows 沙箱下系统 temp 不可写(pitfalls §9), 临时目录放仓库内
.tmp_push_sender_test, 测试自行创建/清理, 不用 pytest 的 tmp_path。
"""
import asyncio
import io
import shutil
from pathlib import Path

from plugin.plugins.neko_arcade.adapters.push_sender import PushSender

_TMP = Path(__file__).resolve().parent.parent / ".tmp_push_sender_test"


def _tmp_dir() -> Path:
    if _TMP.exists():
        shutil.rmtree(_TMP, ignore_errors=True)
    _TMP.mkdir(parents=True, exist_ok=True)
    return _TMP


class StubImages:
    """模拟新宿主 ctx.images(upload 可用)。"""

    def __init__(self):
        self.uploads = []

    async def upload(self, data, timeout=8.0):
        self.uploads.append((data, timeout))
        return {"type": "image", "url": "http://up/img.jpeg", "mime": "image/jpeg"}


class StubPlugin:
    def __init__(self, config_dir=None, has_images=True):
        self.config_dir = str(config_dir) if config_dir else None
        self.ctx = type("Ctx", (), {"images": StubImages()})() if has_images \
            else type("Ctx", (), {})()
        self.pushed = []

    def push_message(self, **kw):
        self.pushed.append(kw)


def _sender(config_dir=None, has_images=True):
    return PushSender(StubPlugin(config_dir, has_images))


def test_text_with_image_prefers_native_channel():
    async def run():
        sender = _sender(has_images=True)
        await sender.text_with_image("配文", b"PNGDATA", "image/png")
        assert len(sender.plugin.pushed) == 1
        msg = sender.plugin.pushed[0]
        assert msg["visibility"] == ["chat"]
        assert msg["ai_behavior"] == "read"
        parts = msg["parts"]
        assert parts[0] == {"type": "text", "text": "配文"}
        assert parts[1]["type"] == "image" and parts[1]["url"] == "http://up/img.jpeg"
        # 原生通道成功 → 不应落盘 static
        assert sender.plugin.ctx.images.uploads, "应调用 ctx.images.upload"

    asyncio.run(run())


def test_text_with_image_falls_back_to_markdown_image_syntax():
    async def run():
        tmp = _tmp_dir()
        sender = _sender(config_dir=tmp, has_images=False)
        await sender.text_with_image("配文", b"PNGDATA", "image/png")
        assert len(sender.plugin.pushed) == 1
        msg = sender.plugin.pushed[0]
        # 旧宿主 _push 默认 visibility=["chat","hud"]
        assert msg["visibility"] == ["chat", "hud"]
        text = msg["parts"][0]["text"]
        assert "配文" in text
        # 旧宿主前端不渲染 <img> HTML, 必须用标准 markdown 图片语法
        assert "![游戏图片](http://127.0.0.1:48916/plugin/neko_arcade/ui/cards/" in text, text
        assert "<img" not in text, "旧宿主不渲染 <img> HTML, 会显示成代码"
        # 图片确实落盘
        cards = Path(tmp) / "static" / "cards"
        assert any(cards.glob("*.png")), list(cards.glob("*"))

    asyncio.run(run())


def test_text_with_image_no_static_dir_pushes_text_only():
    async def run():
        # config_dir 为 None → save_image 返回 None → 只剩文本
        sender = _sender(config_dir=None, has_images=False)
        await sender.text_with_image("只有文字", b"PNGDATA", "image/png")
        assert len(sender.plugin.pushed) == 1
        text = sender.plugin.pushed[0]["parts"][0]["text"]
        assert text == "只有文字", text

    asyncio.run(run())


def test_help_doc_prefers_native_channel_with_blind():
    async def run():
        sender = _sender(has_images=True)
        await sender.help_doc("修仙 帮助", b"PNGDATA", "玩法说明")
        assert len(sender.plugin.pushed) == 1
        msg = sender.plugin.pushed[0]
        # 帮助文档图: 用户可见即可, 不喂 LLM → ai_behavior="blind"
        assert msg["ai_behavior"] == "blind"
        assert msg["visibility"] == ["chat"]
        parts = msg["parts"]
        assert parts[0] == {"type": "text", "text": "玩法说明"}
        assert parts[1]["type"] == "image"

    asyncio.run(run())


def test_help_doc_fallback_uses_title_and_markdown_image():
    async def run():
        tmp = _tmp_dir()
        sender = _sender(config_dir=tmp, has_images=False)
        # text=None → 回退时 caption 用 title
        await sender.help_doc("修仙 帮助", b"PNGDATA", None)
        assert len(sender.plugin.pushed) == 1
        text = sender.plugin.pushed[0]["parts"][0]["text"]
        # 标准 markdown 图片语法(旧宿主前端不渲染 <img> HTML)
        assert "![修仙 帮助](http://127.0.0.1:48916/plugin/neko_arcade/ui/cards/" in text, text
        assert "<img" not in text, "旧宿主不渲染 <img> HTML, 会显示成代码"

    asyncio.run(run())


def test_text_with_image_url_prefers_native_channel():
    async def run():
        sender = _sender(has_images=True)
        # url 已由 upload 产生 → 原生通道直接构造 part
        await sender.text_with_image_url("看图", "http://up/img.jpeg")
        assert len(sender.plugin.pushed) == 1
        msg = sender.plugin.pushed[0]
        assert msg["ai_behavior"] == "read"
        assert msg["parts"][1] == {"type": "image", "url": "http://up/img.jpeg",
                                   "mime": "image/jpeg"}

    asyncio.run(run())


def test_save_image_resizes_large_png_to_jpeg():
    """大图落盘前缩放(≤720px)并转 JPEG, 防 markdown 窄窗溢出。"""
    from PIL import Image

    # 构造 1440x2160 真实 PNG
    img = Image.new("RGB", (1440, 2160), (200, 120, 80))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    async def run():
        tmp = _tmp_dir()
        sender = _sender(config_dir=tmp, has_images=False)
        url = await sender.save_image(png_bytes, "image/png")
        assert url and url.endswith(".jpg"), f"缩放输出应为 jpg: {url}"
        # 落盘文件是 JPEG, 最长边 ≤720
        fname = Path(url).name
        saved = Path(tmp) / "static" / "cards" / fname
        assert saved.exists()
        im = Image.open(saved)
        assert max(im.size) <= 720, f"应缩放到 ≤720: {im.size}"
        assert im.format == "JPEG"

    asyncio.run(run())


def test_save_image_small_png_kept_as_is():
    """小图不缩放, 保持原样落盘。"""
    from PIL import Image

    img = Image.new("RGB", (300, 200), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    async def run():
        tmp = _tmp_dir()
        sender = _sender(config_dir=tmp, has_images=False)
        url = await sender.save_image(buf.getvalue(), "image/png")
        assert url and url.endswith(".png"), f"小图应保持 png: {url}"

    asyncio.run(run())
