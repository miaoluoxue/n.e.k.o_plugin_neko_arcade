"""游戏记忆读写必须具备原子性与失败回滚。"""

from __future__ import annotations

import asyncio
import copy

from plugin.plugins.neko_arcade.core.memory import GameMemory
from plugin.plugins.neko_arcade.core.session import SessionKey


class _Err:
    def is_err(self):
        return True


class _Store:
    def __init__(self, value=None, *, get_error=None, set_error=None):
        self.value = value
        self.get_error = get_error
        self.set_error = set_error
        self.set_calls = 0

    async def get(self, _key):
        if self.get_error:
            raise self.get_error
        return self.value

    async def set(self, _key, value):
        self.set_calls += 1
        if self.set_error:
            raise self.set_error
        self.value = copy.deepcopy(value)
        return None


def _memory(store: _Store) -> GameMemory:
    return GameMemory(type("P", (), {"store": store})())


def test_read_failure_blocks_later_overwrite_and_restores_memory():
    store = _Store(get_error=OSError("disk read failed"))
    memory = _memory(store)

    assert asyncio.run(
        memory.register_play("fishing", session_key=SessionKey("qq", "alice"))
    ) is False
    assert store.set_calls == 0, "读取失败后绝不能拿空内存覆盖旧档"
    assert memory._milestones == {}


def test_write_failure_rolls_back_in_memory_profiles():
    existing = {
        "profiles": {
            "default": {
                "stats": {"fishing": {"plays": 2}},
            },
        },
    }
    store = _Store(existing, set_error=OSError("disk full"))
    memory = _memory(store)
    before = copy.deepcopy(existing)

    assert asyncio.run(memory.register_play("fishing")) is False
    assert memory._milestones == before
    assert store.value == existing


def test_err_result_is_reported_and_rolled_back():
    store = _Store({"profiles": {}})

    async def set_err(_key, _value):
        store.set_calls += 1
        return _Err()

    store.set = set_err
    memory = _memory(store)
    assert asyncio.run(memory.register_play("fishing")) is False
    assert memory._milestones == {"profiles": {}}


def test_session_table_uses_storage_id_not_bare_username():
    store = _Store({"profiles": {}})
    memory = _memory(store)
    qq = SessionKey("qq", "alice")

    assert asyncio.run(memory.save_sessions({
        qq: "fishing",
        "bob": "xiuxian",
    })) is True
    assert store.value["sessions"] == {
        qq.storage_id: "fishing",
        "bob": "xiuxian",
    }
    assert asyncio.run(memory.load_sessions()) == store.value["sessions"]
