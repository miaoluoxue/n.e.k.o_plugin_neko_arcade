"""Small TTL/LRU cache for per-user game state."""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Callable, Generic, Iterator, Optional, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class UserValueCache(Generic[K, V]):
    """Dictionary-like cache with expiry and a hard entry limit."""

    def __init__(
        self,
        *,
        max_entries: int = 512,
        ttl: float = 24 * 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_entries = max(1, int(max_entries))
        self.ttl = max(0.0, float(ttl))
        self._clock = clock
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def _live(self, key: K) -> Optional[V]:
        item = self._data.get(key)
        if item is None:
            return None
        stamp, value = item
        if self.ttl > 0 and self._clock() - stamp > self.ttl:
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    def __contains__(self, key: object) -> bool:
        return self._live(key) is not None  # type: ignore[arg-type]

    def __getitem__(self, key: K) -> V:
        value = self._live(key)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key: K, value: V) -> None:
        self._data[key] = (self._clock(), value)
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)

    def __iter__(self) -> Iterator[K]:
        for key in list(self._data):
            if self._live(key) is not None:
                yield key

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def get(self, key: K, default: Optional[V] = None) -> Optional[V]:
        value = self._live(key)
        return default if value is None else value

    def setdefault(self, key: K, default: V) -> V:
        value = self._live(key)
        if value is not None:
            return value
        self[key] = default
        return default

    def pop(self, key: K, default=None):
        item = self._data.pop(key, None)
        return default if item is None else item[1]

    def clear(self) -> None:
        self._data.clear()
