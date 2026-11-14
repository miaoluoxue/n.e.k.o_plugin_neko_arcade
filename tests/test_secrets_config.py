from __future__ import annotations

import json
from pathlib import Path

from plugin.plugins.neko_arcade.core.config_manager import ConfigManager
from plugin.plugins.neko_arcade.core.secrets import protect_secret, unprotect_secret


def test_llm_key_roundtrip_is_not_plaintext_on_disk(tmp_path: Path) -> None:
    cm = ConfigManager(str(tmp_path / "data"))
    secret = "sk-test-secret-value"
    cm.save_main_config({
        "llm": {
            "provider": "openai_compatible",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": secret,
        }
    })
    path = tmp_path / "data" / "main" / "config.json"
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    safe = cm.load_main_config()
    assert "api_key" not in safe["llm"]
    assert "api_key_enc" in safe["llm"]
    assert cm.load_llm_config()["api_key"] == secret
    public = cm.public_main_config()
    assert public["llm"]["api_key_set"] is True
    assert "api_key" not in public["llm"]
    assert "api_key_enc" not in public["llm"]


def test_legacy_plaintext_key_is_migrated_on_load(tmp_path: Path) -> None:
    data = tmp_path / "data"
    main = data / "main"
    main.mkdir(parents=True)
    (main / "config.json").write_text(json.dumps({
        "llm": {"provider": "openai", "model": "x", "api_key": "legacy-secret"}
    }), encoding="utf-8")

    cm = ConfigManager(str(data))
    safe = cm.load_main_config()
    disk = (main / "config.json").read_text(encoding="utf-8")
    assert "legacy-secret" not in disk
    assert "api_key" not in safe["llm"]
    assert cm.load_llm_config()["api_key"] == "legacy-secret"


def test_secret_fallback_is_reversible() -> None:
    protected = protect_secret("hello")
    assert protected
    assert "hello" not in protected
    assert unprotect_secret(protected) == "hello"
