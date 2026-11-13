"""会话身份：所有可变的玩家状态统一按 ``(channel, user_id)`` 分槽。"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple, Union

SINGLE_CHANNEL = "single"
DEFAULT_USER = "default"
LEGACY_CHANNEL = "legacy"
_STORAGE_PREFIX = "v1"


def _clean_part(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    text = "".join(ch for ch in text if ord(ch) >= 32 and ch not in "\r\n")
    text = re.sub(r"\s+", " ", text)[:200]
    return text or fallback


@dataclass(frozen=True)
class SessionKey:
    channel: str = SINGLE_CHANNEL
    user_id: str = DEFAULT_USER

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _clean_part(self.channel, SINGLE_CHANNEL))
        object.__setattr__(self, "user_id", _clean_part(self.user_id, DEFAULT_USER))

    @property
    def is_single(self) -> bool:
        return self.channel == SINGLE_CHANNEL and self.user_id == DEFAULT_USER

    @property
    def storage_id(self) -> str:
        """稳定、可逆的存档 ID；单人模式继续兼容旧的 ``default``。"""
        if self.is_single:
            return DEFAULT_USER
        if self.channel == LEGACY_CHANNEL:
            return self.user_id
        channel = urllib.parse.quote(self.channel, safe="")
        user = urllib.parse.quote(self.user_id, safe="")
        return f"{_STORAGE_PREFIX}:{channel}:{user}"

    @classmethod
    def from_storage_id(cls, value: Any) -> "SessionKey":
        text = str(value or "").strip()
        if not text or text == DEFAULT_USER:
            return SINGLE_SESSION
        if text.startswith(_STORAGE_PREFIX + ":"):
            parts = text.split(":", 2)
            if len(parts) == 3:
                return cls(
                    urllib.parse.unquote(parts[1]),
                    urllib.parse.unquote(parts[2]),
                )
        return cls(LEGACY_CHANNEL, text)


SINGLE_SESSION = SessionKey()
SessionLike = Union[SessionKey, Tuple[str, str], str, None]


def coerce_session_key(value: SessionLike) -> SessionKey:
    if isinstance(value, SessionKey):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        return SessionKey(value[0], value[1])
    if isinstance(value, str):
        return SessionKey.from_storage_id(value)
    return SINGLE_SESSION


def resolve_session_key(
    context: Optional[Mapping[str, Any]] = None,
    *,
    host_context: Any = None,
) -> SessionKey:
    """从宿主 ``_ctx`` 解析会话；拿不到用户 ID 时退化为单人模式。"""
    ctx = dict(context or {})
    nested = ctx.get("host_reply")
    if isinstance(nested, Mapping):
        merged = dict(nested)
        merged.update(ctx)
        ctx = merged

    user_id = next((
        str(ctx.get(key) or "").strip()
        for key in ("user_id", "actor_id", "author_id", "sender_id")
        if str(ctx.get(key) or "").strip()
    ), "")
    if not user_id and host_context is not None:
        for attr in ("user_id", "actor_id", "author_id", "sender_id"):
            value = str(getattr(host_context, attr, "") or "").strip()
            if value:
                user_id = value
                break
    if not user_id:
        return SINGLE_SESSION

    channel = next((
        str(ctx.get(key) or "").strip()
        for key in ("channel", "platform", "source")
        if str(ctx.get(key) or "").strip()
    ), "")
    if not channel and host_context is not None:
        for attr in ("channel", "platform"):
            value = str(getattr(host_context, attr, "") or "").strip()
            if value:
                channel = value
                break
    return SessionKey(channel or "local", user_id)
