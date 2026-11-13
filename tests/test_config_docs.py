"""主插件配置文档必须与当前代码消费的键和默认方向一致。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_documented_main_config_keys_exist_in_code():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    sources = {
        "brain": (ROOT / "core" / "brain.py").read_text(encoding="utf-8"),
        "runtime": (ROOT / "core" / "runtime.py").read_text(encoding="utf-8"),
        "assets": (ROOT / "adapters" / "asset_store.py").read_text(encoding="utf-8"),
    }
    keys = {
        "llm_max_calls_per_minute": "runtime",
        "llm_timeout": "runtime",
        "asset_prefetch": "runtime",
        "asset_cache_mb": "assets",
        "game_anchor_interval": "brain",
        "background_active_window": "brain",
        "game_switch_confirm_ttl": "brain",
        "session_cache_ttl": "brain",
        "session_cache_max": "brain",
        "session_prune_interval": "brain",
        "proactive_daily_global_limit": "brain",
    }
    for key, source in keys.items():
        assert f"`{key}`" in readme, f"README 缺少主配置键 {key}"
        assert key in sources[source], f"{key} 已写入文档但 {source} 未消费"


def test_asset_prefetch_default_stays_opt_in():
    runtime = (ROOT / "core" / "runtime.py").read_text(encoding="utf-8")
    assert 'cfg.get("asset_prefetch", False)' in runtime
