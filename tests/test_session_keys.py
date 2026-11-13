"""会话键必须稳定包含渠道和用户，缺用户时回退单人模式。"""

from __future__ import annotations

from plugin.plugins.neko_arcade.core.session import (
    SINGLE_SESSION,
    SessionKey,
    coerce_session_key,
    resolve_session_key,
)


def test_same_user_on_different_channels_has_different_storage():
    qq = SessionKey("qq", "alice")
    web = SessionKey("web", "alice")
    assert qq != web
    assert qq.storage_id != web.storage_id
    assert SessionKey.from_storage_id(qq.storage_id) == qq
    assert SessionKey.from_storage_id(web.storage_id) == web


def test_missing_user_falls_back_to_single_mode():
    assert resolve_session_key({"channel": "qq"}) == SINGLE_SESSION
    assert resolve_session_key({}, host_context=object()) == SINGLE_SESSION


def test_context_prefers_explicit_user_and_channel():
    assert resolve_session_key({
        "channel": "discord",
        "user_id": "u-42",
        "source": "chat",
    }) == SessionKey("discord", "u-42")
    assert resolve_session_key({"source": "chat", "actor_id": "u-7"}) == SessionKey(
        "chat", "u-7"
    )


def test_legacy_string_ids_remain_readable():
    assert coerce_session_key("alice") == SessionKey("legacy", "alice")
    assert coerce_session_key("alice").storage_id == "alice"
    assert coerce_session_key("default") == SINGLE_SESSION
    assert coerce_session_key(("qq", "alice")).storage_id.startswith("v1:")
