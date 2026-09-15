"""帮助图渲染测试: 浏览器(HTML)渲染路径的构造逻辑 + 浏览器定位。

不依赖真实浏览器(PIL/Playwright 缺失也能跑): 只断言 HTML 构造与候选目录。
真实截图由 render_help_html 在运行环境里完成, 失败自动回退 PIL 版 render_help。
"""

from __future__ import annotations

import os
import sys

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


def test_system_browser_candidates_cover_platform_browsers() -> None:
    """内置 Chromium 全不可用时, 还要能退到系统已装 Chrome/Edge(与宿主回退一致)。"""
    cands = ImageRenderer()._system_browser_candidates()
    assert cands, "系统浏览器候选列表不能为空"
    joined = " ".join(cands).replace("\\", "/").lower()
    if sys.platform == "win32":
        assert "chrome.exe" in joined
        assert "msedge.exe" in joined
    elif sys.platform == "darwin":
        assert "google chrome.app" in joined
    else:
        assert "chromium" in joined


def test_find_chromium_only_returns_real_files() -> None:
    """定位到的浏览器必须真实存在——不能是被剥空的目录(0.9.0.2 自带包就是这个坑)。"""
    exe = ImageRenderer()._find_chromium()
    assert exe is None or os.path.isfile(exe), exe


def test_host_browser_is_preferred(monkeypatch) -> None:
    """出图优先走宿主内置: 宿主自己的查找函数给出的浏览器必须排在最前。"""
    import types

    fake_exe = os.path.abspath(__file__)          # 任意真实文件充当"浏览器"

    def fake_import(name: str, *a, **kw):
        if name == "brain.browser_use_adapter":
            return types.SimpleNamespace(_find_chrome_path=lambda: fake_exe)
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", fake_import)
    assert ImageRenderer._host_browser_hint() == fake_exe
    assert ImageRenderer._find_chromium() == fake_exe


def test_host_hint_ignores_missing_browser(monkeypatch) -> None:
    """宿主给的路径不存在时必须继续往下找, 不能返回坏路径。"""
    import types

    def fake_import(name: str, *a, **kw):
        if name == "brain.browser_use_adapter":
            return types.SimpleNamespace(_find_chrome_path=lambda: r"Z:\nope\chrome.exe")
        raise ImportError(name)

    monkeypatch.setattr("importlib.import_module", fake_import)
    monkeypatch.setattr(ImageRenderer, "_browser_roots", classmethod(lambda cls: []))
    monkeypatch.setattr(ImageRenderer, "_system_browser_candidates",
                        staticmethod(lambda: []))
    assert ImageRenderer._host_browser_hint() is None
    assert ImageRenderer._find_chromium() is None


def test_launch_chain_prefers_host_environment(monkeypatch) -> None:
    """渲染尽量走宿主: 首选"不指定 exe"(宿主 PLAYWRIGHT_BROWSERS_PATH), 显式路径兜底。"""
    chain = ImageRenderer._launch_kwargs_chain()
    assert chain[0] == {"headless": True}
    assert "executable_path" not in chain[0]
    monkeypatch.setattr(ImageRenderer, "_find_chromium",
                        classmethod(lambda cls: r"C:\host\chrome.exe"))
    chain2 = ImageRenderer._launch_kwargs_chain()
    assert chain2[0] == {"headless": True}
    assert chain2[1]["executable_path"] == r"C:\host\chrome.exe"


def test_launch_chain_without_any_browser(monkeypatch) -> None:
    """找不到任何浏览器时链上只有宿主环境一项(交给 Playwright 自己解析)。"""
    monkeypatch.setattr(ImageRenderer, "_find_chromium", classmethod(lambda cls: None))
    assert ImageRenderer._launch_kwargs_chain() == [{"headless": True}]
