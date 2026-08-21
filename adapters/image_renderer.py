"""图片渲染：猫娘表情头像 + 结果卡片 + 帮助文档图（纯 PIL 绘制）。"""

from __future__ import annotations

import importlib.util
import io
import logging
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("neko_arcade.image")

RARITY_COLORS = {
    "common": "#8a7e72", "uncommon": "#4a9e5f", "rare": "#3a7bd5",
    "epic": "#9b59b6", "legendary": "#d4a84b", "？": "#e74c3c",
}

MOOD_COLORS = {
    "excitement": "#ffd9a8", "curiosity": "#ffe9c8", "proud": "#ffd9a8",
    "upset": "#dcd2e8", "sleepy": "#e8e4da", "calm": "#ffe9c8",
}

CARD_BG = (250, 247, 240)
CARD_BORDER = (214, 200, 182)
INK = (61, 51, 40)
INK2 = (138, 122, 102)

BRAND_FOOTER = "N.E.K.O 猫娘小游戏"
_CJK_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
]


def _load_cjk_font(size: int):
    """加载支持中文的字体（PIL 默认字体不含 CJK，会渲染成方框）。"""
    try:
        from PIL import ImageFont
        for path in _CJK_FONT_CANDIDATES:
            if os.path.exists(path):
                try:
                    return ImageFont.truetype(path, size)
                except Exception:
                    continue
        return ImageFont.load_default()
    except Exception:
        return None


class ImageRenderer:
    """用 PIL 绘制猫娘表情、结果卡片、帮助图。"""

    def __init__(self) -> None:
        self._pil_ok = self._check_pil()

    @staticmethod
    def _check_pil() -> bool:
        return importlib.util.find_spec("PIL") is not None

    # ══════════════════════════════════════════
    # 猫娘表情头像
    # ══════════════════════════════════════════

    def render_neko_avatar(self, mood: str = "calm", size: int = 128) -> Optional[bytes]:
        """绘制一只带表情的猫娘头像（PNG 字节）。"""
        if not self._pil_ok:
            return None
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            cx, cy = size / 2, size / 2
            r = size * 0.32

            # 发色
            hair = (255, 214, 150)
            # 猫耳（左）
            ear = [(cx - r * 1.1, cy - r * 0.9), (cx - r * 0.75, cy - r * 1.9), (cx - r * 0.15, cy - r * 1.0)]
            d.polygon(ear, fill=hair)
            # 猫耳（右）
            ear2 = [(cx + r * 1.1, cy - r * 0.9), (cx + r * 0.75, cy - r * 1.9), (cx + r * 0.15, cy - r * 1.0)]
            d.polygon(ear2, fill=hair)
            # 耳内粉
            inner = [(cx - r * 0.98, cy - r * 1.0), (cx - r * 0.72, cy - r * 1.65), (cx - r * 0.32, cy - r * 0.98)]
            d.polygon(inner, fill=(255, 200, 200))
            inner2 = [(cx + r * 0.98, cy - r * 1.0), (cx + r * 0.72, cy - r * 1.65), (cx + r * 0.32, cy - r * 0.98)]
            d.polygon(inner2, fill=(255, 200, 200))

            # 脸
            d.ellipse([cx - r, cy - r * 0.85, cx + r, cy + r * 1.15], fill=(255, 246, 235))
            # 腮红
            blush = (255, 200, 200)
            d.ellipse([cx - r * 0.9, cy + r * 0.35, cx - r * 0.45, cy + r * 0.62], fill=blush)
            d.ellipse([cx + r * 0.45, cy + r * 0.35, cx + r * 0.9, cy + r * 0.62], fill=blush)

            # 表情
            mood = (mood or "calm").lower()
            if mood in ("excitement", "proud", "curiosity"):
                # 兴奋/得意/好奇：大眼睛 + 星星
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.12, r * 0.16, "open")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.12, r * 0.16, "open")
                if mood == "excitement":
                    self._draw_sparkle(d, cx + r * 0.75, cy - r * 0.7, r * 0.1)
                    self._draw_sparkle(d, cx - r * 0.75, cy - r * 0.6, r * 0.07)
            elif mood == "upset":
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.1, r * 0.13, "droop")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.1, r * 0.13, "droop")
                # 泪珠
                d.ellipse([cx - r * 0.5, cy + r * 0.1, cx - r * 0.32, cy + r * 0.28], fill=(150, 200, 255))
            elif mood == "sleepy":
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.1, r * 0.13, "closed")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.1, r * 0.13, "closed")
                # zzz
                self._draw_zzz(d, cx + r * 0.7, cy - r * 0.9, r * 0.08)
            else:
                # calm 微笑弯眼
                self._draw_eye(d, cx - r * 0.42, cy - r * 0.08, r * 0.13, "smile")
                self._draw_eye(d, cx + r * 0.42, cy - r * 0.08, r * 0.13, "smile")

            # 嘴巴
            if mood in ("excitement", "proud"):
                d.arc([cx - r * 0.3, cy + r * 0.3, cx + r * 0.3, cy + r * 0.72], 20, 160, fill=INK, width=max(2, int(size * 0.02)))
            elif mood == "upset":
                d.arc([cx - r * 0.3, cy + r * 0.4, cx + r * 0.3, cy + r * 0.78], 200, 340, fill=INK, width=max(2, int(size * 0.02)))
            elif mood == "sleepy":
                d.ellipse([cx - r * 0.12, cy + r * 0.45, cx + r * 0.12, cy + r * 0.6], fill=INK)
            else:
                d.arc([cx - r * 0.28, cy + r * 0.28, cx + r * 0.28, cy + r * 0.7], 20, 160, fill=INK, width=max(2, int(size * 0.02)))

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("绘制猫娘头像失败: %s", exc)
            return None

    def _draw_eye(self, d, x: float, y: float, r: float, style: str) -> None:
        if style == "open":
            d.ellipse([x - r, y - r, x + r, y + r], outline=INK, width=max(1, int(r * 0.4)))
            d.ellipse([x - r * 0.35, y - r * 0.3, x + r * 0.35, y + r * 0.35], fill=INK)
            d.ellipse([x - r * 0.15, y - r * 0.5, x + r * 0.1, y - r * 0.1], fill=(255, 255, 255))
        elif style == "smile":
            d.arc([x - r, y - r, x + r, y + r * 0.6], 30, 150, fill=INK, width=max(1, int(r * 0.4)))
        elif style == "closed":
            d.line([x - r, y, x + r, y], fill=INK, width=max(1, int(r * 0.35)))
        elif style == "droop":
            d.line([x - r, y + r * 0.4, x + r, y - r * 0.2], fill=INK, width=max(1, int(r * 0.35)))

    def _draw_sparkle(self, d, x: float, y: float, r: float) -> None:
        d.polygon([(x, y - r * 2), (x + r * 0.4, y - r * 0.3), (x + r * 2, y),
                   (x + r * 0.4, y + r * 0.3), (x, y + r * 2),
                   (x - r * 0.4, y + r * 0.3), (x - r * 2, y),
                   (x - r * 0.4, y - r * 0.3)], fill=(255, 215, 130))

    def _draw_zzz(self, d, x: float, y: float, r: float) -> None:
        d.text((x, y), "z", fill=(150, 140, 160))
        d.text((x + r * 0.9, y - r * 0.7), "z", fill=(170, 160, 180))
        d.text((x + r * 1.7, y - r * 1.4), "z", fill=(190, 180, 200))

    # ══════════════════════════════════════════
    # 结果卡片（带猫娘表情）
    # ══════════════════════════════════════════

    async def render_card(self, game_name: str, title: str,
                          lines: List[Tuple[str, str]],
                          subtitle: str = "", mood: str = "calm") -> Optional[bytes]:
        """渲染一张带猫娘表情的结果卡片。"""
        if not self._pil_ok:
            return None
        try:
            from PIL import Image, ImageDraw
            avatar = self._get_avatar(mood, 96)
            font = _load_cjk_font(14)
            font_title = _load_cjk_font(18)
            w, h = 460, 74 + len(lines) * 34 + (44 if subtitle else 16)
            img = Image.new("RGB", (w, h), CARD_BG)
            draw = ImageDraw.Draw(img)
            draw.rectangle([2, 2, w - 3, h - 3], outline=CARD_BORDER, width=2)
            # 猫娘头像
            if avatar:
                img.paste(avatar, (14, 12), avatar)
            draw.text((118, 14), f"{game_name}", fill=INK2, font=font)
            draw.text((118, 36), title, fill=INK, font=font_title)
            y = 74
            for text, rarity in lines:
                draw.text((18, y), text, fill=RARITY_COLORS.get(rarity, INK), font=font)
                y += 34
            if subtitle:
                draw.text((18, y), subtitle, fill=INK2, font=font)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("渲染卡片失败: %s", exc)
            return None

    async def render_help(self, game_name: str, commands: List[Tuple[str, str]],
                          footer: str = "") -> Optional[bytes]:
        """渲染带猫娘头像的帮助文档图（底部带统一品牌落款）。"""
        if not self._pil_ok:
            return None
        try:
            from PIL import Image, ImageDraw
            avatar = self._get_avatar("curiosity", 88)
            font = _load_cjk_font(14)
            font_title = _load_cjk_font(18)
            font_footer = _load_cjk_font(12)
            brand = f"{BRAND_FOOTER} × {game_name}"
            line_h = 30
            extra = (26 if footer else 0) + 28  # 帮助文本行 + 品牌落款行
            w, h = 460, 62 + len(commands) * line_h + extra
            img = Image.new("RGB", (w, h), CARD_BG)
            draw = ImageDraw.Draw(img)
            draw.rectangle([2, 2, w - 3, h - 3], outline=CARD_BORDER, width=2)
            if avatar:
                img.paste(avatar, (14, 10), avatar)
            draw.text((110, 16), f"{game_name} · 玩法帮助", fill=INK, font=font_title)
            y = 52
            for cmd, desc in commands:
                draw.text((18, y), f"{cmd}", fill=(217, 168, 90), font=font)
                draw.text((128, y), desc, fill=INK, font=font)
                y += line_h
            if footer:
                draw.text((18, y), footer, fill=INK2, font=font)
                y += 26
            # 底部居中品牌落款
            try:
                bbox = draw.textbbox((0, 0), brand, font=font_footer)
                tw = bbox[2] - bbox[0]
            except Exception:
                tw = len(brand) * 12
            draw.text(((w - tw) // 2, h - 22), brand, fill=INK2, font=font_footer)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("渲染帮助失败: %s", exc)
            return None

    # ══════════════════════════════════════════
    # HTML → PNG（Playwright，复用 N.E.K.O 内置 Chromium）
    # ══════════════════════════════════════════

    async def render_html(self, html: str, css: str = "", width: int = 720,
                          height: int = 600, game_name: str = "",
                          selector: str = "body", brand_footer: bool = False) -> Optional[bytes]:
        """把 HTML 模板渲染成 PNG（插件端渲染方案，供游戏接入）。

        - 优先用 Playwright(Chromium)，复用 N.E.K.O 内置浏览器；
        - 渲染失败或环境无 Playwright 时返回 None，调用方自行降级；
        - brand_footer=True 时在底部叠加统一品牌落款。
        """
        png = await self._render_html_playwright(html, css, width, height, selector)
        if not png:
            return None
        if brand_footer:
            png = self._append_footer(png, game_name)
        return png

    async def _render_html_playwright(self, html: str, css: str, width: int,
                                      height: int, selector: str) -> Optional[bytes]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            log.info("Playwright 不可用，跳过 HTML 渲染")
            return None
        try:
            if css:
                html = f"<style>{css}</style>{html}"
            async with async_playwright() as p:
                launch_kwargs: Dict[str, Any] = {"headless": True}
                exe = self._find_chromium()
                if exe:
                    launch_kwargs["executable_path"] = exe
                browser = await p.chromium.launch(**launch_kwargs)
                try:
                    page = await browser.new_page(viewport={"width": width, "height": height})
                    await page.set_content(html, wait_until="networkidle")
                    el = await page.query_selector(selector)
                    if el:
                        return await el.screenshot()
                    return await page.screenshot(full_page=True)
                finally:
                    await browser.close()
        except Exception as exc:
            log.warning("Playwright HTML 渲染失败: %s", exc)
            return None

    @staticmethod
    def _find_chromium() -> Optional[str]:
        """定位 N.E.K.O 内置的 Playwright Chromium 可执行文件。"""
        import glob
        browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
        if not browsers_dir:
            for root in (os.path.dirname(os.path.abspath(sys.argv[0])),
                         os.path.dirname(os.path.abspath(__file__)),
                         os.getcwd()):
                c = os.path.join(root, "playwright_browsers")
                if os.path.isdir(c):
                    browsers_dir = c
                    break
        if not browsers_dir or not os.path.isdir(browsers_dir):
            return None
        for pat in (os.path.join(browsers_dir, "chromium-*", "chrome-win64", "chrome.exe"),
                    os.path.join(browsers_dir, "chromium-*", "chrome-win", "chrome.exe")):
            ms = glob.glob(pat)
            if ms:
                ms.sort()
                return ms[-1]
        return None

    def _append_footer(self, png_bytes: bytes, game_name: str) -> Optional[bytes]:
        """在 PNG 底部叠加统一品牌落款条。"""
        if not self._pil_ok:
            return png_bytes
        try:
            from PIL import Image, ImageDraw
            img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            w, h = img.size
            bar_h = 32
            canvas = Image.new("RGB", (w, h + bar_h), CARD_BG)
            canvas.paste(img, (0, 0))
            d = ImageDraw.Draw(canvas)
            font = _load_cjk_font(13)
            brand = f"{BRAND_FOOTER} × {game_name}" if game_name else BRAND_FOOTER
            try:
                bbox = d.textbbox((0, 0), brand, font=font)
                tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            except Exception:
                tw, th = len(brand) * 12, 16
            d.text(((w - tw) / 2, h + (bar_h - th) / 2), brand, fill=INK2, font=font)
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            buf.seek(0)
            return buf.read()
        except Exception as exc:
            log.warning("叠加品牌落款失败: %s", exc)
            return png_bytes

    def _get_avatar(self, mood: str, size: int) -> Optional[Any]:
        """获取猫娘表情头像（PIL Image）。"""
        if not self._pil_ok:
            return None
        from PIL import Image
        data = self.render_neko_avatar(mood, size)
        if not data:
            return None
        buf = io.BytesIO(data)
        buf.seek(0)
        return Image.open(buf).convert("RGBA")