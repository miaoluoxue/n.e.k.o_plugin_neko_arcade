"""后台 tick 必须隔离单次异常，不能因一次错误永久停摆。"""

from __future__ import annotations

import asyncio

import pytest
from plugin.plugins.neko_arcade.core import runtime as runtime_module
from plugin.plugins.neko_arcade.core.runtime import ArcadeRuntime


class _FlakyBrain:
    def __init__(self):
        self.calls = 0

    async def tick(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("first failure")
        if self.calls == 2:
            raise ValueError("second failure")
        raise asyncio.CancelledError


def test_tick_loop_logs_and_keeps_running(monkeypatch):
    runtime = ArcadeRuntime.__new__(ArcadeRuntime)
    runtime.brain = _FlakyBrain()

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(runtime_module.asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._tick_loop())

    assert runtime.brain.calls == 3, "两次异常后仍应继续执行下一次 tick"
