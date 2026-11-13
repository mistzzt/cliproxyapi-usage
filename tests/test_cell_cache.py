"""The slot cache returns exactly what the direct query does.

Each check runs an endpoint query with and without a built ``CellCache`` and
compares the responses with ``==``, so floats must match bit for bit.
"""

from __future__ import annotations

import contextlib
import random
import sqlite3
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from test_pricing_ccusage import CLAUDE, GPT_LONG

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.schemas import RequestRecord
from cliproxy_usage_server.aggregate import (
    Filters,
    Window,
    query_breakdown,
    query_export,
    query_options,
    query_summary,
)
from cliproxy_usage_server.cell_cache import CellCache
from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.db import open_ro
from cliproxy_usage_server.main import create_app
from cliproxy_usage_server.pricing import ModelPricing

# One model per plan shape, plus an unpriced one.
PRICING: dict[str, ModelPricing] = {
    "claude-m": CLAUDE,
    "gpt-l": GPT_LONG,
    "gpt-flat": ModelPricing(
        litellm_provider="openai",
        input_cost_per_token=1.1e-6,
        output_cost_per_token=4.4e-6,
    ),
    "grok-l": GPT_LONG.model_copy(update={"litellm_provider": "xai"}),
}
MODELS = [*PRICING, "mystery"]
KEYS = ["sk-a-alice", "sk-b-alice", "sk-c-bob", "sk-d-carol"]
SOURCES = ["al@x.io", "sk-src-one", "sk-src-two"]
T0 = datetime(2026, 10, 30, tzinfo=UTC)
TZ = ZoneInfo("America/Chicago")


def _stamp(rng: random.Random, at: datetime) -> str:
    ms = rng.randrange(1000)
    if rng.random() < 0.2:
        local = at.astimezone(ZoneInfo("Asia/Kolkata"))
        return local.isoformat(timespec="milliseconds")
    return at.strftime("%Y-%m-%dT%H:%M:%S") + f".{ms:03d}Z"


def _records(seed: int, n: int, start: datetime) -> list[RequestRecord]:
    rng = random.Random(seed)
    out: list[RequestRecord] = []
    for _ in range(n):
        at = start + timedelta(seconds=rng.randrange(4 * 86400))
        big = rng.random() < 0.15
        inp = rng.randrange(150_000, 400_000) if big else rng.randrange(100, 20_000)
        cache_read = rng.randrange(inp // 2 + 1)
        cache_creation = rng.randrange((inp - cache_read) // 2 + 1)
        out_tokens = rng.randrange(1, 210_000 if big else 4_000)
        out.append(
            RequestRecord(
                timestamp=_stamp(rng, at),
                api_key=rng.choice(KEYS),
                model=rng.choice(MODELS),
                source=rng.choice(SOURCES),
                auth_index="0",
                latency_ms=rng.randrange(50, 5_000),
                input_tokens=inp,
                output_tokens=out_tokens,
                reasoning_tokens=rng.randrange(out_tokens + 1),
                cache_read_tokens=cache_read,
                cache_creation_tokens=cache_creation,
                cache_creation_1h_tokens=rng.randrange(cache_creation + 1),
                total_tokens=inp + out_tokens,
                service_tier=None,
                ttft_ms=None,
                failed=rng.random() < 0.1,
            )
        )
    return out


def _insert(db: Path, records: list[RequestRecord]) -> None:
    with contextlib.closing(open_db(db)) as conn:
        insert_records(conn, records)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "usage.db"
    pinned = _records(4, 1, T0)[0].model_copy(
        update={"timestamp": "2026-10-31T04:15:00.200Z"}
    )
    _insert(path, [*_records(1, 3_000, T0), pinned])
    return path


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    with contextlib.closing(open_ro(db)) as c:
        yield c


@pytest.fixture
def cache(db: Path) -> CellCache:
    c = CellCache(db, verify_seconds=0)
    c.build()
    return c


def _at(h: int, m: int = 0, s: int = 0, us: int = 0, day: int = 1) -> datetime:
    return T0 + timedelta(days=day, hours=h, minutes=m, seconds=s, microseconds=us)


WINDOWS = {
    "mid-slot edges": Window(_at(3, 7, 13, 250_000), _at(17, 52, 1, 999_000, day=2)),
    "aligned": Window(_at(0), _at(12)),
    "one slot": Window(_at(5, 1), _at(5, 14)),
    "adjacent slots": Window(_at(5, 1), _at(5, 29)),
    # SQLite compares whole seconds, so the 04:15:00.2 row is inside this start.
    "sub-second bounds": Window(_at(4, 15, 0, 999_600), _at(9, 29, 59, 999_700)),
    "open start": Window(None, _at(20, 3, day=2)),
    "beyond data": Window(_at(0, day=-5), _at(0, day=30)),
}
FILTERS = {
    "none": Filters(),
    "people": Filters(api_keys=["sk-a-alice", "sk-c-bob"]),
    "model": Filters(models=["gpt-l", "mystery"]),
    "source and model": Filters(models=["claude-m"], sources=["al@x.io"]),
    "empty": Filters(sources=[]),
}

type Query = Callable[[sqlite3.Connection, CellCache | None], object]


def _queries(window: Window, filters: Filters) -> dict[str, Query]:
    aliases = {"sk-c...bob": "Bob"}
    return {
        "summary": lambda c, k: query_summary(
            c, window, None, TZ, filters, PRICING, cache=k
        ),
        "hourly summary": lambda c, k: query_summary(
            c, window, "hour", TZ, filters, PRICING, cache=k
        ),
        **{
            f"breakdown {dim}": (
                lambda c, k, dim=dim: query_breakdown(
                    c, dim, window, None, TZ, filters, PRICING, aliases, cache=k
                )
            )
            for dim in ("person", "model", "credential")
        },
        "export": lambda c, k: query_export(
            c, window, "hour", TZ, filters, PRICING, aliases, cache=k
        ),
    }


def _same(conn: sqlite3.Connection, cache: CellCache, query: Query) -> None:
    hits = cache.hits
    assert query(conn, cache) == query(conn, None)
    assert cache.hits > hits


@pytest.mark.parametrize("window", WINDOWS.values(), ids=list(WINDOWS))
@pytest.mark.parametrize("filters", FILTERS.values(), ids=list(FILTERS))
def test_cached_queries_match_direct(
    conn: sqlite3.Connection, cache: CellCache, window: Window, filters: Filters
) -> None:
    for name, query in _queries(window, filters).items():
        hits = cache.hits
        cached, direct = query(conn, cache), query(conn, None)
        assert cached == direct, name
        if window.start is None or window.end - window.start > timedelta(hours=1):
            assert cache.hits > hits, name


def test_options_match_direct(conn: sqlite3.Connection, cache: CellCache) -> None:
    _same(conn, cache, lambda c, k: query_options(c, PRICING, {}, cache=k))


def test_window_inside_one_slot_falls_back(
    conn: sqlite3.Connection, cache: CellCache
) -> None:
    window = WINDOWS["one slot"]
    hits = cache.hits
    query_summary(conn, window, None, TZ, Filters(), PRICING, cache=cache)
    assert cache.hits == hits


def test_insert_between_requests(
    db: Path, conn: sqlite3.Connection, cache: CellCache
) -> None:
    window = WINDOWS["mid-slot edges"]
    query = _queries(window, Filters())["breakdown model"]
    _same(conn, cache, query)
    # New rows land in cached slots, edge slots and new slots.
    _insert(db, _records(2, 500, T0 + timedelta(days=1)))
    _same(conn, cache, query)
    _same(conn, cache, lambda c, k: query_options(c, PRICING, {}, cache=k))
    assert cache.builds == 1


def _wait_ready(cache: CellCache, builds: int) -> None:
    deadline = time.monotonic() + 10
    while not (cache.ready and cache.builds >= builds):
        assert time.monotonic() < deadline, "cache did not rebuild"
        time.sleep(0.01)


def _write(db: Path, sql: str) -> None:
    with contextlib.closing(sqlite3.connect(db)) as w, w:
        w.execute(sql)


@pytest.mark.parametrize(
    "rewrite",
    [
        "DELETE FROM requests WHERE rowid = 100",
        "DELETE FROM requests WHERE rowid = (SELECT MAX(rowid) FROM requests)",
    ],
    ids=["middle", "last"],
)
def test_rebuild_after_deletion(
    db: Path, conn: sqlite3.Connection, cache: CellCache, rewrite: str
) -> None:
    query = _queries(WINDOWS["mid-slot edges"], Filters())["summary"]
    _same(conn, cache, query)
    _write(db, rewrite)
    # The read that notices falls back to the direct query.
    assert query(conn, cache) == query(conn, None)
    _wait_ready(cache, 2)
    _same(conn, cache, query)


def test_rebuild_when_last_rowid_is_reused(db: Path, conn: sqlite3.Connection) -> None:
    # A long verify interval leaves only the anchor row to catch this rewrite.
    cache = CellCache(db, verify_seconds=3600)
    cache.build()
    query = _queries(WINDOWS["open start"], Filters())["breakdown person"]
    _same(conn, cache, query)
    _write(db, "DELETE FROM requests WHERE rowid = (SELECT MAX(rowid) FROM requests)")
    _insert(db, _records(3, 1, T0))
    assert query(conn, cache) == query(conn, None)
    _wait_ready(cache, 2)
    _same(conn, cache, query)


def test_server_builds_cache_and_matches_direct(db: Path) -> None:
    def client(enabled: bool) -> TestClient:
        cfg = ServerConfig(db_path=db, cell_cache=enabled)
        return TestClient(create_app(cfg, pricing_provider=lambda: PRICING))

    params = {
        "start": "2026-10-31T03:07:13.25Z",
        "end": "2026-11-01T17:52:01Z",
        "tz": "America/Chicago",
    }
    with client(True) as cached, client(False) as direct:
        cache = cached.app.state.cell_cache  # type: ignore[attr-defined]
        assert direct.app.state.cell_cache is None  # type: ignore[attr-defined]
        _wait_ready(cache, 1)
        for path, extra in (("summary", {}), ("breakdown", {"dim": "model"})):
            url, query = f"/api/usage/{path}", params | extra
            assert cached.get(url, params=query).json() == (
                direct.get(url, params=query).json()
            )
        url = "/api/usage/options"
        assert cached.get(url).json() == direct.get(url).json()
        assert cache.hits > 0
