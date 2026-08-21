"""Repository smoke tests for the standalone plugin package."""

from __future__ import annotations

import pathlib
import tomllib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return tomllib.loads((_ROOT / "plugin.toml").read_text(encoding="utf-8"))


def test_plugin_manifest_declares_expected_entrypoint_and_runtime():
    manifest = _manifest()

    assert manifest["plugin"]["id"] == "neko_arcade"
    assert manifest["plugin"]["entry"] == "plugins.neko_arcade:NekoArcadePlugin"
    assert manifest["plugin_runtime"]["enabled"] is True


def test_plugin_manifest_declares_hosted_ui_surface_and_files_exist():
    manifest = _manifest()

    assert manifest["plugin"]["ui"]["enabled"] is True
    for panel in manifest["plugin"]["ui"]["panel"]:
        entry = _ROOT / panel["entry"]
        assert entry.exists(), f"UI entry missing: {panel['entry']}"
    for guide in manifest["plugin"]["ui"]["guide"]:
        entry = _ROOT / guide["entry"]
        assert entry.exists(), f"Guide entry missing: {guide['entry']}"


def test_plugin_source_modules_compile():
    import ast

    modules = (
        sorted((_ROOT / "core").glob("*.py"))
        + sorted((_ROOT / "adapters").glob("*.py"))
        + sorted((_ROOT / "games").rglob("*.py"))
        + [_ROOT / "__init__.py"]
    )
    for py in modules:
        ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
