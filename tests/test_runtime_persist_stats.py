"""token 统计落盘任务必须被持引用并在 shutdown 前收尾。"""

from __future__ import annotations

import asyncio

from plugin.plugins.neko_arcade.core.runtime import ArcadeRuntime


class _Store:
    def __init__(self):
        self.saved = []

    async def set(self, key, value):
        self.saved.append((key, value))
        return None


def test_persist_stats_keeps_task_reference_until_done():
    async def run():
        runtime = ArcadeRuntime.__new__(ArcadeRuntime)
        runtime._persist_tasks = set()
        store = _Store()
        runtime._persist_llm_stats(store, {"calls": 1})
        assert runtime._persist_tasks
        await asyncio.gather(*runtime._persist_tasks)
        await asyncio.sleep(0)
        assert runtime._persist_tasks == set()
        assert store.saved == [("game_user_data:llm_stats", {"calls": 1})]

    asyncio.run(run())


def test_shutdown_drains_pending_stats_tasks():
    async def run():
        runtime = ArcadeRuntime.__new__(ArcadeRuntime)
        runtime._persist_tasks = set()
        runtime.assets = None
        runtime._tick_task = None
        runtime.registry = type("R", (), {"games": []})()
        store = _Store()
        runtime._persist_llm_stats(store, {"calls": 2})
        await runtime.shutdown()
        assert runtime._persist_tasks == set()
        assert store.saved[0][0] == "game_user_data:llm_stats"

    asyncio.run(run())
