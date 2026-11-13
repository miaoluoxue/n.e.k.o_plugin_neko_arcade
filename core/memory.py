"""游戏经历记忆：短期会话 + 长期里程碑（store 持久化）。"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from .session import SINGLE_SESSION, SessionKey, SessionLike, coerce_session_key

STORE_KEY = "game_memories"


class GameMemory:
    """记忆库。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._session_by_key: Dict[SessionKey, List[Dict[str, Any]]] = {}
        self._session = self._session_by_key.setdefault(SINGLE_SESSION, [])
        self._milestones: Dict[str, Any] = {}

    async def _load(self) -> Dict[str, Any]:
        if not self._milestones:
            data = await self.plugin.store.get(STORE_KEY)
            # SDK store.get 返回 Ok/Err 包装（数据在 Ok.value），统一解开
            if hasattr(data, "is_err") and callable(data.is_err) and data.is_err():
                data = None
            elif hasattr(data, "value"):
                data = data.value
            elif hasattr(data, "data"):
                data = data.data
            self._milestones = data if isinstance(data, dict) else {}
        return self._milestones

    async def _save(self) -> None:
        result = await self.plugin.store.set(STORE_KEY, self._milestones)
        is_err = getattr(result, "is_err", None)
        if callable(is_err) and is_err():
            raise RuntimeError("游戏记忆写入失败")

    def _profile(self, key: SessionKey) -> Dict[str, Any]:
        profiles = self._milestones.setdefault("profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self._milestones["profiles"] = profiles
        if not profiles:
            legacy_keys = {"profiles", "sessions"}
            legacy = {
                name: value
                for name, value in self._milestones.items()
                if name not in legacy_keys
            }
            if legacy:
                profiles[SINGLE_SESSION.storage_id] = legacy
        profile = profiles.get(key.storage_id)
        if not isinstance(profile, dict):
            profile = {}
            profiles[key.storage_id] = profile
        return profile

    def _recent_for(self, key: SessionKey) -> List[Dict[str, Any]]:
        return self._session_by_key.setdefault(key, [])

    def record(self, game_id: str, outcome: str, facts: List[Dict], text: str,
               *, session_key: SessionLike = SINGLE_SESSION) -> None:
        key = coerce_session_key(session_key)
        session = self._recent_for(key)
        session.append({"ts": time.time(), "game": game_id, "outcome": outcome,
                        "facts": facts[:5], "neko_said": text[:80]})
        if len(session) > 20:
            del session[:-20]

    def recent_session(
        self, n: int = 5, *, session_key: SessionLike = SINGLE_SESSION
    ) -> str:
        lines = []
        key = coerce_session_key(session_key)
        for e in self._recent_for(key)[-n:]:
            names = [str(f.get("name", f.get("item", ""))) for f in (e.get("facts") or []) if f.get("name") or f.get("item")]
            lines.append(f"[{e['game']}] {e['outcome']}" + (f"（{', '.join(names)}）" if names else ""))
        return "；".join(lines)

    async def bump_stat(self, game_id: str, key: str, amount: int = 1,
                        *, session_key: SessionLike = SINGLE_SESSION) -> None:
        await self._load()
        profile = self._profile(coerce_session_key(session_key))
        stats = profile.setdefault("stats", {}).setdefault(game_id, {})
        stats[key] = stats.get(key, 0) + amount
        await self._save()

    async def set_record(self, game_id: str, key: str, value: Any, label: str = "",
                         *, session_key: SessionLike = SINGLE_SESSION) -> bool:
        await self._load()
        profile = self._profile(coerce_session_key(session_key))
        records = profile.setdefault("records", {}).setdefault(game_id, {})
        old = records.get(key)
        if old is None or (isinstance(value, (int, float)) and value > old):
            records[key] = {"value": value, "label": label, "ts": time.time()}
            await self._save()
            return True
        return False

    async def record_species(self, game_id: str, name: str,
                             *, session_key: SessionLike = SINGLE_SESSION) -> bool:
        await self._load()
        profile = self._profile(coerce_session_key(session_key))
        seen = profile.setdefault("species", {}).setdefault(game_id, [])
        if name in seen:
            return False
        seen.append(name)
        await self._save()
        return True

    async def register_play(self, game_id: str,
                            *, session_key: SessionLike = SINGLE_SESSION) -> None:
        await self._load()
        profile = self._profile(coerce_session_key(session_key))
        stats = profile.setdefault("stats", {}).setdefault(game_id, {})
        stats["plays"] = stats.get("plays", 0) + 1
        stats["last_play_ts"] = time.time()
        await self._save()

    async def get_stats(self, game_id: str,
                        *, session_key: SessionLike = SINGLE_SESSION) -> Dict[str, Any]:
        await self._load()
        return self._profile(coerce_session_key(session_key)).get(
            "stats", {}
        ).get(game_id, {})

    async def snapshot(
        self, *, session_key: SessionLike = SINGLE_SESSION
    ) -> Dict[str, Any]:
        await self._load()
        key = coerce_session_key(session_key)
        profile = self._profile(key)
        summary = {}
        for gid, st in (profile.get("stats", {}) or {}).items():
            summary[gid] = {"plays": st.get("plays", 0), "last": st.get("last_play_ts")}
        return {"games": summary, "session": self.recent_session(session_key=key)}

    def scoped(self, session_key: SessionLike) -> "_ScopedGameMemory":
        return _ScopedGameMemory(self, coerce_session_key(session_key))

    # ── 会话指针持久化（谁在玩什么，重启后能接着玩）────────────
    async def save_sessions(self, sessions: Dict[str, str]) -> None:
        """把 user_id → game_id 的会话表落盘。"""
        m = await self._load()
        m["sessions"] = {str(k): str(v) for k, v in (sessions or {}).items()}
        await self._save()

    async def load_sessions(self) -> Dict[str, str]:
        """读回会话表；没有就返回空表。"""
        m = await self._load()
        got = m.get("sessions")
        if not isinstance(got, dict):
            return {}
        return {str(k): str(v) for k, v in got.items() if v}


class _ScopedGameMemory:
    """交给游戏的记忆代理，自动固定到当前 ``(channel,user_id)``。"""

    def __init__(self, memory: GameMemory, key: SessionKey) -> None:
        self._memory = memory
        self._key = key

    async def bump_stat(self, game_id: str, name: str, amount: int = 1) -> None:
        await self._memory.bump_stat(
            game_id, name, amount, session_key=self._key
        )

    async def set_record(self, game_id: str, name: str, value: Any,
                         label: str = "") -> bool:
        return await self._memory.set_record(
            game_id, name, value, label, session_key=self._key
        )

    async def record_species(self, game_id: str, name: str) -> bool:
        return await self._memory.record_species(
            game_id, name, session_key=self._key
        )

    async def register_play(self, game_id: str) -> None:
        await self._memory.register_play(game_id, session_key=self._key)

    async def get_stats(self, game_id: str) -> Dict[str, Any]:
        return await self._memory.get_stats(game_id, session_key=self._key)
