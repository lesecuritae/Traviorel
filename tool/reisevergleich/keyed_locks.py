"""Per-key locks that are dropped when nobody holds or waits for them.

`dict.setdefault(key, Lock())` kept one lock per distinct key for the life of the process,
so every distinct cache miss (and every distinct unauthenticated search) grew memory
without bound. These registries count holders and waiters and remove a key's lock when the
count returns to zero.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator


class KeyedAsyncLocks:
    def __init__(self) -> None:
        self._entries: dict[str, list] = {}  # key -> [asyncio.Lock, users]

    def __len__(self) -> int:
        return len(self._entries)

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        # No await between lookup and increment: safe on a single event loop.
        entry = self._entries.get(key)
        if entry is None:
            entry = self._entries[key] = [asyncio.Lock(), 0]
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0 and self._entries.get(key) is entry:
                del self._entries[key]


class KeyedThreadLocks:
    def __init__(self) -> None:
        self._entries: dict[str, list] = {}  # key -> [threading.Lock, users]
        self._guard = threading.Lock()

    def __len__(self) -> int:
        with self._guard:
            return len(self._entries)

    @contextmanager
    def hold(self, key: str) -> Iterator[None]:
        with self._guard:
            entry = self._entries.get(key)
            if entry is None:
                entry = self._entries[key] = [threading.Lock(), 0]
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self._guard:
                entry[1] -= 1
                if entry[1] == 0 and self._entries.get(key) is entry:
                    del self._entries[key]
