"""游戏经历记忆：短期会话 + 长期里程碑（store 持久化）。"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from typing import Any, Dict, List

from .session import SINGLE_SESSION, SessionKey, SessionLike, coerce_session_key

STORE_KEY = "game_memories"
log = logging.getLogger("neko_arcade.memory")
MAX_RECENT_SESSIONS = 1000
RECENT_SESSION_TTL = 7 * 24 * 3600


class GameMemory:
    """记忆库。"""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._session_by_key: Dict[SessionKey, List[Dict[str, Any]]] = {}
        self._session = self._session_by_key.setdefault(SINGLE_SESSION, [])
        self._session_touched: Dict[SessionKey, float] = {SINGLE_SESSION: time.time()}
        self._milestones: Dict[str, Any] = {}
        self._loaded = False
        self._load_failed = False
        # store 是所有用户共享的单对象；load/mutate/save 必须串行，失败回滚
        # 只能撤销本次修改，不能覆盖并发用户已完成的内存状态。
        self._write_lock = asyncio.Lock()

    async def _load(self) -> Dict[str, Any]:
        if self._loaded:
            return self._milestones
        try:
            data = await self.plugin.store.get(STORE_KEY)
            # SDK store.get 返回 Ok/Err 包装（数据在 Ok.value），统一解开
            if hasattr(data, "is_err") and callable(data.is_err) and data.is_err():
                raise RuntimeError("store returned Err")
            elif hasattr(data, "value"):
                data = data.value
            elif hasattr(data, "data"):
                data = data.data
            if data is not None and not isinstance(data, dict):
                raise ValueError(f"unexpected store payload: {type(data).__name__}")
        except Exception as exc:
            self._load_failed = True
            log.warning("读取游戏记忆失败，禁止后续覆盖写入: %s", exc)
            return {}
        # 与 store 返回对象彻底脱钩：失败写入时 store 中未提交的引用不能被改脏。
        self._milestones = copy.deepcopy(data) if isinstance(data, dict) else {}
        self._loaded = True
        self._load_failed = False
        return self._milestones

    async def _save(self, before: Dict[str, Any]) -> bool:
        if self._load_failed:
            self._milestones = copy.deepcopy(before)
            log.warning("游戏记忆读取曾失败，拒绝用当前内存覆盖旧存档")
            return False
        try:
            result = await self.plugin.store.set(
                STORE_KEY, copy.deepcopy(self._milestones))
            is_err = getattr(result, "is_err", None)
            if callable(is_err) and is_err():
                raise RuntimeError("store returned Err")
            return True
        except Exception as exc:
            self._milestones = copy.deepcopy(before)
            log.warning("游戏记忆写入失败，已回滚内存: %s", exc)
            return False

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
        now = time.time()
        expired = [
            item for item, stamp in self._session_touched.items()
            if item != SINGLE_SESSION and now - stamp > RECENT_SESSION_TTL
        ]
        for item in expired:
            self._session_by_key.pop(item, None)
            self._session_touched.pop(item, None)
        self._session_touched[key] = now
        result = self._session_by_key.setdefault(key, [])
        if len(self._session_by_key) > MAX_RECENT_SESSIONS:
            removable = sorted(
                (
                    (stamp, item)
                    for item, stamp in self._session_touched.items()
                    if item != SINGLE_SESSION and item != key
                ),
                key=lambda pair: pair[0],
            )
            for _, item in removable[:len(self._session_by_key) - MAX_RECENT_SESSIONS]:
                self._session_by_key.pop(item, None)
                self._session_touched.pop(item, None)
        return result

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
                        *, session_key: SessionLike = SINGLE_SESSION) -> bool:
        async with self._write_lock:
            await self._load()
            before = copy.deepcopy(self._milestones)
            profile = self._profile(coerce_session_key(session_key))
            stats = profile.setdefault("stats", {}).setdefault(game_id, {})
            stats[key] = stats.get(key, 0) + amount
            return await self._save(before)

    async def set_record(self, game_id: str, key: str, value: Any, label: str = "",
                         *, session_key: SessionLike = SINGLE_SESSION) -> bool:
        async with self._write_lock:
            await self._load()
            before = copy.deepcopy(self._milestones)
            profile = self._profile(coerce_session_key(session_key))
            records = profile.setdefault("records", {}).setdefault(game_id, {})
            old = records.get(key)
            if old is None or (isinstance(value, (int, float)) and value > old):
                records[key] = {"value": value, "label": label, "ts": time.time()}
                return await self._save(before)
            return False

    async def record_species(self, game_id: str, name: str,
                             *, session_key: SessionLike = SINGLE_SESSION) -> bool:
        async with self._write_lock:
            await self._load()
            before = copy.deepcopy(self._milestones)
            profile = self._profile(coerce_session_key(session_key))
            seen = profile.setdefault("species", {}).setdefault(game_id, [])
            if name in seen:
                return False
            seen.append(name)
            return await self._save(before)

    async def register_play(self, game_id: str,
                            *, session_key: SessionLike = SINGLE_SESSION) -> bool:
        async with self._write_lock:
            await self._load()
            before = copy.deepcopy(self._milestones)
            profile = self._profile(coerce_session_key(session_key))
            stats = profile.setdefault("stats", {}).setdefault(game_id, {})
            stats["plays"] = stats.get("plays", 0) + 1
            stats["last_play_ts"] = time.time()
            return await self._save(before)

    async def get_stats(self, game_id: str,
                        *, session_key: SessionLike = SINGLE_SESSION) -> Dict[str, Any]:
        async with self._write_lock:
            await self._load()
            return copy.deepcopy(
                self._profile(coerce_session_key(session_key)).get(
                    "stats", {}
                ).get(game_id, {})
            )

    async def snapshot(
        self, *, session_key: SessionLike = SINGLE_SESSION
    ) -> Dict[str, Any]:
        async with self._write_lock:
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
    async def save_sessions(self, sessions: Dict[SessionLike, str]) -> bool:
        """把 ``(channel,user_id)`` 会话表按稳定存储 ID 落盘。"""
        async with self._write_lock:
            await self._load()
            before = copy.deepcopy(self._milestones)
            self._milestones["sessions"] = {
                coerce_session_key(key).storage_id: str(value)
                for key, value in (sessions or {}).items()
            }
            return await self._save(before)

    async def load_sessions(self) -> Dict[str, str]:
        """读回会话表；没有就返回空表。"""
        async with self._write_lock:
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

    async def bump_stat(self, game_id: str, name: str, amount: int = 1) -> bool:
        return await self._memory.bump_stat(
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

    async def register_play(self, game_id: str) -> bool:
        return await self._memory.register_play(
            game_id, session_key=self._key
        )

    async def get_stats(self, game_id: str) -> Dict[str, Any]:
        return await self._memory.get_stats(game_id, session_key=self._key)
