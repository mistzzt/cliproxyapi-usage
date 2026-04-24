"""Tests for TtlCache with in-flight deduplication."""

from __future__ import annotations

import asyncio

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
        result1 = await cache.get_or_fetch("k", fetch, ttl=60)
        result2 = await cache.get_or_fetch("k", fetch, ttl=60)

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
        result1 = await cache.get_or_fetch("k", fetch, ttl=60)
        assert result1 == "v1"
        assert call_count == 1

        # Second call: clock=200, which is > 60 → expired → re-fetch
        result2 = await cache.get_or_fetch("k", fetch, ttl=60)
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
        r1 = await cache.get_or_fetch("k1", fetch_k1, ttl=60)
        r2 = await cache.get_or_fetch("k2", fetch_k2, ttl=60)

        # Each key hit its own fetcher exactly once
        assert r1 == "v-k1"
        assert r2 == "v-k2"
        assert k1_calls == 1
        assert k2_calls == 1

        # Re-fetch same keys → still only 1 call each
        await cache.get_or_fetch("k1", fetch_k1, ttl=60)
        await cache.get_or_fetch("k2", fetch_k2, ttl=60)
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
            asyncio.create_task(cache.get_or_fetch("k", slow_fetch, ttl=60))
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
            await cache.get_or_fetch("k", flaky_fetch, ttl=60)
            raise AssertionError("Expected RuntimeError")
        except RuntimeError as exc:
            assert str(exc) == "boom"

        # Second call with a different fetcher that returns "ok"
        result = await cache.get_or_fetch("k", flaky_fetch, ttl=60)
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
            asyncio.create_task(cache.get_or_fetch("k", boom_fetch, ttl=60))
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

        r1 = await cache.get_or_fetch("k", fetch, ttl=60)
        assert r1 == "v1"
        assert call_count == 1

        # After invalidation, next call must re-fetch
        cache.invalidate("k")

        r2 = await cache.get_or_fetch("k", fetch, ttl=60)
        assert r2 == "v2"
        assert call_count == 2

        # No invalidation → cached
        r3 = await cache.get_or_fetch("k", fetch, ttl=60)
        assert r3 == "v2"
        assert call_count == 2

    asyncio.run(run())
