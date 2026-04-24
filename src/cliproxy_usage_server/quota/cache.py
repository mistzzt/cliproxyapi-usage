"""Async TTL cache with in-flight deduplication.

A single ``asyncio.Lock`` guards all mutations of the two internal dicts.
When multiple coroutines race for the same missing key, only the first
(the *leader*) calls the fetcher; the rest await the same
``asyncio.Future``.  If the fetcher raises, the exception is forwarded to
all waiters and the entry is **not** cached so subsequent calls can retry.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

__all__ = ["TtlCache"]


@dataclass
class _Entry[V]:
    value: V
    stale_at: float


class TtlCache[K, V]:
    """Async TTL cache with in-flight deduplication."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock: asyncio.Lock = asyncio.Lock()
        self._store: dict[K, _Entry[V]] = {}
        self._in_flight: dict[K, asyncio.Future[V]] = {}

    async def get_or_fetch(
        self,
        key: K,
        fetch: Callable[[], Awaitable[V]],
        *,
        ttl: float,
    ) -> V:
        """Return the cached value for *key*, fetching it if missing or stale.

        Concurrent callers for the same key share a single in-flight fetch;
        only one coroutine runs *fetch* at a time per key.
        """
        is_leader = False
        fut: asyncio.Future[V]

        async with self._lock:
            entry = self._store.get(key)
            now = self._clock()
            if entry is not None and entry.stale_at > now:
                return entry.value

            in_flight = self._in_flight.get(key)
            if in_flight is not None:
                fut = in_flight
                is_leader = False
            else:
                loop = asyncio.get_running_loop()
                fut = loop.create_future()
                self._in_flight[key] = fut
                is_leader = True

        if not is_leader:
            return await fut

        # We are the leader: call the fetcher outside the lock.
        try:
            value = await fetch()
        except BaseException as exc:
            async with self._lock:
                self._in_flight.pop(key, None)
            if not fut.done():
                fut.set_exception(exc)
                # Suppress "Future exception was never retrieved" when there
                # are no waiters (serial error case).
                fut.add_done_callback(
                    lambda f: f.exception() if not f.cancelled() else None
                )
            raise

        async with self._lock:
            self._store[key] = _Entry(value=value, stale_at=self._clock() + ttl)
            self._in_flight.pop(key, None)

        fut.set_result(value)
        return value

    def invalidate(self, key: K) -> None:
        """Remove *key* from the cache so the next call re-fetches."""
        self._store.pop(key, None)
