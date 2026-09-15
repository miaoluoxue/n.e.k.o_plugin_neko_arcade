"""帮助图渲染测试: 浏览器(HTML)渲染路径的构造逻辑 + 浏览器定位。

不依赖真实浏览器(PIL/Playwright 缺失也能跑): 只断言 HTML 构造与候选目录。
真实截图由 render_help_html 在运行环境里完成, 失败自动回退 PIL 版 render_help。
"""

from __future__ import annotations

from plugin.plugins.neko_arcade.adapters.image_renderer import ImageRenderer


def test_build_help_html_contains_rows_title_and_brand() -> None:
    html = ImageRenderer().build_help_html(
        "测试游戏", [("开始", "开始游戏"), ("停止", "结束游戏")], "这里是说明")
    assert html.startswith("<!doctype html>")
    assert '<div class="hd">测试游戏 · 玩法帮助</div>' in html
    assert '<span class="k">开始</span>' in html
    assert '<span class="v">开始游戏</span>' in html
    assert "这里是说明" in html
    assert "N.E.K.O 猫娘小游戏 × 测试游戏" in html


def test_page_tag_only_when_multiple_pages() -> None:
    ir = ImageRenderer()
    assert "(1/1)" not in ir.build_help_html("G", [("a", "b")], page=1, pages=1)
    assert "(2/3)" in ir.build_help_html("G", [("a", "b")], page=2, pages=3)


def test_footer_only_rendered_on_first_page() -> None:
    ir = ImageRenderer()
    assert "页脚说明" in ir.build_help_html("G", [("a", "b")], "页脚说明", page=1, pages=2)
    assert "页脚说明" not in ir.build_help_html("G", [("a", "b")], "页脚说明", page=2, pages=2)


def test_html_special_chars_are_escaped() -> None:
    """指令里出现 < > & 时不能破坏 HTML(否则帮助图整页错乱)。"""
    html = ImageRenderer().build_help_html("G", [("<script>x</script>", "a & b")])
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b" in html


def test_browser_roots_include_system_playwright_cache() -> None:
    """应用自带 Chromium 可能缺 exe(实测 0.9.0.2 如此), 必须也能找到系统缓存。"""
    roots = ImageRenderer()._browser_roots()
    assert any("ms-playwright" in r for r in roots), roots
    assert any("playwright_browsers" in r for r in roots), roots
