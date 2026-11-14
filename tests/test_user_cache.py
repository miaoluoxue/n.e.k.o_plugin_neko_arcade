from __future__ import annotations

from plugin.plugins.neko_arcade.core.user_cache import UserValueCache


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_user_cache_evicts_expired_entries():
    clock = _Clock()
    cache = UserValueCache(max_entries=10, ttl=5, clock=clock)
    cache["alice"] = {"plays": 1}
    clock.now = 6
    assert "alice" not in cache
    assert cache.get("alice") is None


def test_user_cache_evicts_least_recently_used():
    clock = _Clock()
    cache = UserValueCache(max_entries=2, ttl=100, clock=clock)
    cache["a"] = 1
    clock.now = 1
    cache["b"] = 2
    assert cache["a"] == 1
    clock.now = 2
    cache["c"] = 3
    assert "b" not in cache
    assert cache["a"] == 1 and cache["c"] == 3


def test_user_cache_preserves_mapping_compatibility():
    cache = UserValueCache(max_entries=3, ttl=100)
    assert cache.setdefault("u", {"food": 1}) == {"food": 1}
    assert cache.setdefault("u", {"food": 9})["food"] == 1
    assert cache.pop("u")["food"] == 1
    assert cache.pop("u", None) is None
    cache["x"] = 1
    cache.clear()
    assert len(cache) == 0
