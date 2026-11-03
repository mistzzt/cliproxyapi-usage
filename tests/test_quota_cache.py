"""Tests for TtlCache with in-flight deduplication."""

from __future__ import annotations

import asyncio

from cliproxy_usage_server.quota.cache import TtlCache


def _ttl(_: object) -> float:
    return 60


# ---------------------------------------------------------------------------
# Test 1: miss calls fetcher, result cached on subsequent call
# ---------------------------------------------------------------------------


def test_miss_calls_fetcher_and_caches() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    call_count = 0

    async def run() -> None:
        nonlocal call_count

        async def fetch() -> str:
            nonlocal call_count
            call_count += 1
            return "v1"

        cache: TtlCache[str, str] = TtlCache()
        result1 = await cache.get_or_fetch("k", fetch, ttl=_ttl)
        result2 = await cache.get_or_fetch("k", fetch, ttl=_ttl)

        assert result1 == "v1"
        assert result2 == "v1"
        assert call_count == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 2: TTL expiry triggers re-fetch
# ---------------------------------------------------------------------------


def test_ttl_expiry_triggers_refetch() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    # Clock returns successive values: 0, 1, 200 …
    times = iter([0.0, 1.0, 200.0, 201.0])

    def fake_clock() -> float:
        return next(times)

    call_count = 0

    async def run() -> None:
        nonlocal call_count

        async def fetch() -> str:
            nonlocal call_count
            call_count += 1
            return f"v{call_count}"

        cache: TtlCache[str, str] = TtlCache(clock=fake_clock)

        # First call: clock=0, stale_at = 0+60 = 60
        result1 = await cache.get_or_fetch("k", fetch, ttl=_ttl)
        assert result1 == "v1"
        assert call_count == 1

        # Second call: clock=200, which is > 60 → expired → re-fetch
        result2 = await cache.get_or_fetch("k", fetch, ttl=_ttl)
        assert result2 == "v2"
        assert call_count == 2

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 3: different keys are independent
# ---------------------------------------------------------------------------


def test_different_keys_are_independent() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    k1_calls = 0
    k2_calls = 0

    async def run() -> None:
        nonlocal k1_calls, k2_calls

        async def fetch_k1() -> str:
            nonlocal k1_calls
            k1_calls += 1
            return "v-k1"

        async def fetch_k2() -> str:
            nonlocal k2_calls
            k2_calls += 1
            return "v-k2"

        cache: TtlCache[str, str] = TtlCache()
        r1 = await cache.get_or_fetch("k1", fetch_k1, ttl=_ttl)
        r2 = await cache.get_or_fetch("k2", fetch_k2, ttl=_ttl)

        # Each key hit its own fetcher exactly once
        assert r1 == "v-k1"
        assert r2 == "v-k2"
        assert k1_calls == 1
        assert k2_calls == 1

        # Re-fetch same keys → still only 1 call each
        await cache.get_or_fetch("k1", fetch_k1, ttl=_ttl)
        await cache.get_or_fetch("k2", fetch_k2, ttl=_ttl)
        assert k1_calls == 1
        assert k2_calls == 1

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 4: concurrent miss deduplicates fetcher calls
# ---------------------------------------------------------------------------


def test_concurrent_miss_dedups_fetcher_calls() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    async def run() -> None:
        call_count = 0

        async def slow_fetch() -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return "shared"

        cache: TtlCache[str, str] = TtlCache()

        tasks = [
            asyncio.create_task(cache.get_or_fetch("k", slow_fetch, ttl=_ttl))
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks)

        assert call_count == 1, f"Expected 1 call, got {call_count}"
        assert all(r == "shared" for r in results)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 5: fetcher exception is not cached; next call can succeed
# ---------------------------------------------------------------------------


def test_fetcher_exception_not_cached() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    attempt = 0

    async def run() -> None:
        nonlocal attempt

        async def flaky_fetch() -> str:
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise RuntimeError("boom")
            return "ok"

        cache: TtlCache[str, str] = TtlCache()

        try:
            await cache.get_or_fetch("k", flaky_fetch, ttl=_ttl)
            raise AssertionError("Expected RuntimeError")
        except RuntimeError as exc:
            assert str(exc) == "boom"

        # Second call with a different fetcher that returns "ok"
        result = await cache.get_or_fetch("k", flaky_fetch, ttl=_ttl)
        assert result == "ok"
        assert attempt == 2

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 5b: concurrent waiters all see the exception when the leader raises
# ---------------------------------------------------------------------------


def test_concurrent_waiters_all_see_exception() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    async def run() -> None:
        call_count = 0

        async def boom_fetch() -> str:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            raise RuntimeError("concurrent-boom")

        cache: TtlCache[str, str] = TtlCache()

        tasks = [
            asyncio.create_task(cache.get_or_fetch("k", boom_fetch, ttl=_ttl))
            for _ in range(5)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Fetcher was called exactly once (dedup worked)
        assert call_count == 1, f"Expected 1 call, got {call_count}"
        # All awaiters received an exception
        for r in results:
            assert isinstance(r, RuntimeError), f"Expected RuntimeError, got {r!r}"
            assert str(r) == "concurrent-boom"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 6: invalidate clears the entry, forcing re-fetch
# ---------------------------------------------------------------------------


def test_invalidate_clears_entry() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    call_count = 0

    async def run() -> None:
        nonlocal call_count

        async def fetch() -> str:
            nonlocal call_count
            call_count += 1
            return f"v{call_count}"

        cache: TtlCache[str, str] = TtlCache()

        r1 = await cache.get_or_fetch("k", fetch, ttl=_ttl)
        assert r1 == "v1"
        assert call_count == 1

        # After invalidation, next call must re-fetch
        cache.invalidate("k")

        r2 = await cache.get_or_fetch("k", fetch, ttl=_ttl)
        assert r2 == "v2"
        assert call_count == 2

        # No invalidation → cached
        r3 = await cache.get_or_fetch("k", fetch, ttl=_ttl)
        assert r3 == "v2"
        assert call_count == 2

    asyncio.run(run())


def test_invalidate_detaches_in_flight_fetch_and_blocks_stale_publish() -> None:
    from cliproxy_usage_server.quota.cache import TtlCache

    async def run() -> None:
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        async def fetch() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_started.set()
                await release_first.wait()
                return "stale"
            return "fresh"

        cache: TtlCache[str, str] = TtlCache()
        old_task = asyncio.create_task(cache.get_or_fetch("k", fetch, ttl=_ttl))
        await first_started.wait()
        cache.invalidate("k")
        assert await cache.get_or_fetch("k", fetch, ttl=_ttl) == "fresh"
        release_first.set()
        assert await old_task == "stale"
        assert await cache.get_or_fetch("k", fetch, ttl=_ttl) == "fresh"
        assert calls == 2

    asyncio.run(run())


def test_ttl_depends_on_the_fetched_value() -> None:
    times = iter([0.0, 1.0, 20.0, 21.0, 22.0])
    cache: TtlCache[str, int] = TtlCache(clock=lambda: next(times))
    values = iter([5, 100])

    async def fetch() -> int:
        return next(values)

    async def run() -> None:
        # 5 lives 5 s (stale_at 6), so the call at 20 refetches; 100 lives on.
        assert await cache.get_or_fetch("k", fetch, ttl=float) == 5
        assert await cache.get_or_fetch("k", fetch, ttl=float) == 100
        assert await cache.get_or_fetch("k", fetch, ttl=float) == 100

    asyncio.run(run())
