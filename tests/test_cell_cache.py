"""The cell cache returns exactly what the direct query does.

Each check runs an endpoint query with and without a ``CellCache`` and compares
the JSON (or CSV) bytes, so floats must match bit for bit.
"""

from __future__ import annotations

import contextlib
import gc
import os
import random
import sqlite3
import threading
import time
import tracemalloc
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
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
from cliproxy_usage_server.cell_cache import ENTRY_BYTES, CellCache
from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.db import SLICE_SECONDS, open_ro
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


def _write(db: Path, sql: str) -> None:
    with contextlib.closing(sqlite3.connect(db)) as w, w:
        w.execute(sql)


def _make_db(path: Path) -> Path:
    pinned = _records(4, 1, T0)[0].model_copy(
        update={"timestamp": "2026-10-31T04:15:00.200Z"}
    )
    _insert(path, [*_records(1, 3_000, T0), pinned])
    # Python parses this timestamp but SQLite cannot, so its row has no interval.
    _insert(path, [pinned.model_copy(update={"timestamp": "20261031T041500.2Z"})])
    return path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return _make_db(tmp_path / "usage.db")


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    with contextlib.closing(open_ro(db)) as c:
        yield c


def _wait_ready(cache: CellCache, builds: int) -> None:
    deadline = time.monotonic() + 10
    while not (cache.ready and cache.builds >= builds):
        assert time.monotonic() < deadline, "cache did not rebuild"
        time.sleep(0.01)


def _built(cache: CellCache) -> CellCache:
    cache.start()
    _wait_ready(cache, 1)
    return cache


@pytest.fixture
def cache(db: Path) -> CellCache:
    return _built(CellCache(db, verify_seconds=0))


def _at(h: int, m: int = 0, s: int = 0, us: int = 0, day: int = 1) -> datetime:
    return T0 + timedelta(days=day, hours=h, minutes=m, seconds=s, microseconds=us)


WINDOWS = {
    "mid-interval edges": Window(
        _at(3, 7, 13, 250_000), _at(17, 52, 1, 999_000, day=2)
    ),
    "aligned": Window(_at(0), _at(12)),
    "inside one interval": Window(_at(5, 1), _at(5, 14)),
    "adjacent intervals": Window(_at(5, 1), _at(5, 29)),
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

type Query = Callable[[sqlite3.Connection, CellCache | None], BaseModel | str]


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


def _options(c: sqlite3.Connection, k: CellCache | None) -> BaseModel:
    return query_options(c, PRICING, {"sk-c...bob": "Bob"}, cache=k)


def _bytes(body: BaseModel | str) -> str:
    return body if isinstance(body, str) else body.model_dump_json()


def _same(conn: sqlite3.Connection, cache: CellCache, query: Query) -> None:
    """The cached read hits the cache and gives the direct query's bytes."""
    hits = cache.hits
    assert _bytes(query(conn, cache)) == _bytes(query(conn, None))
    assert cache.hits > hits


def _all_same(conn: sqlite3.Connection, cache: CellCache, window: Window) -> None:
    for name, query in _queries(window, Filters()).items():
        assert _bytes(query(conn, cache)) == _bytes(query(conn, None)), name
    _same(conn, cache, _options)


@pytest.mark.parametrize("window", WINDOWS.values(), ids=list(WINDOWS))
@pytest.mark.parametrize("filters", FILTERS.values(), ids=list(FILTERS))
def test_cached_queries_match_direct(
    conn: sqlite3.Connection, cache: CellCache, window: Window, filters: Filters
) -> None:
    for name, query in _queries(window, filters).items():
        assert _bytes(query(conn, cache)) == _bytes(query(conn, None)), name


def test_options_match_direct(conn: sqlite3.Connection, cache: CellCache) -> None:
    _same(conn, cache, _options)


def test_window_inside_one_interval_skips_the_cache(
    conn: sqlite3.Connection, cache: CellCache
) -> None:
    query = _queries(WINDOWS["inside one interval"], Filters())["breakdown model"]
    query(conn, cache)
    hits = cache.hits
    query_export(
        conn,
        WINDOWS["inside one interval"],
        "hour",
        TZ,
        Filters(),
        PRICING,
        {},
        cache=cache,
    )
    assert cache.hits == hits


def test_insert_between_requests(
    db: Path, conn: sqlite3.Connection, cache: CellCache
) -> None:
    window = WINDOWS["mid-interval edges"]
    query = _queries(window, Filters())["breakdown model"]
    _same(conn, cache, query)
    # New rows land in held intervals, edge intervals and new intervals.
    _insert(db, _records(2, 500, T0 + timedelta(days=1)))
    _same(conn, cache, query)
    _same(conn, cache, _options)
    assert sorted(cache.values(conn, "model") or []) == sorted(MODELS)
    assert cache.builds == 1


def _replace_file(db: Path) -> None:
    copy = db.with_name("copy.db")
    with contextlib.closing(sqlite3.connect(db)) as src:
        src.execute("VACUUM INTO ?", [str(copy)])
    _write(copy, "DELETE FROM requests WHERE rowid % 7 = 0")
    os.replace(copy, db)


def _reuse_last_rowid(db: Path) -> None:
    _write(db, "DELETE FROM requests WHERE rowid = (SELECT MAX(rowid) FROM requests)")
    _insert(db, _records(3, 1, T0))


REWRITES: dict[str, Callable[[Path], None]] = {
    "file replaced": _replace_file,
    "schema changed": lambda db: _write(db, "CREATE INDEX idx_x ON requests(failed)"),
    "max rowid lower": lambda db: _write(
        db, "DELETE FROM requests WHERE rowid > (SELECT MAX(rowid) - 3 FROM requests)"
    ),
    "last rowid reused": _reuse_last_rowid,
    "row count changed": lambda db: _write(
        db, "DELETE FROM requests WHERE rowid = 100"
    ),
}


@pytest.mark.parametrize("rewrite", REWRITES.values(), ids=list(REWRITES))
def test_rebuild_after_rewrite(
    db: Path, conn: sqlite3.Connection, rewrite: Callable[[Path], None]
) -> None:
    # Only the count check needs a due verification; the others must not rely on it.
    verify = 0 if rewrite is REWRITES["row count changed"] else 3600
    cache = _built(CellCache(db, verify_seconds=verify))
    query = _queries(WINDOWS["open start"], Filters())["breakdown person"]
    _same(conn, cache, query)
    rewrite(db)
    with contextlib.closing(open_ro(db)) as fresh:
        hits = cache.hits
        # The read that notices falls back to the direct query.
        assert _bytes(query(fresh, cache)) == _bytes(query(fresh, None))
        assert cache.hits == hits
        _wait_ready(cache, 2)
        _all_same(fresh, cache, WINDOWS["mid-interval edges"])
    assert cache.builds == 2


def test_requests_during_build_use_the_direct_query(
    db: Path, conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    load = CellCache._load  # pyright: ignore[reportPrivateUsage]

    def slow_load(self: CellCache) -> object:
        release.wait(10)
        return load(self)

    monkeypatch.setattr(CellCache, "_load", slow_load)
    cache = CellCache(db)
    cache.start()
    window = WINDOWS["mid-interval edges"]
    for name, query in _queries(window, Filters()).items():
        assert _bytes(query(conn, cache)) == _bytes(query(conn, None)), name
    assert not cache.ready
    assert cache.hits == 0
    release.set()
    _wait_ready(cache, 1)
    _all_same(conn, cache, window)


def _evicting_cache(db: Path, kept: int) -> CellCache:
    return _built(CellCache(db, verify_seconds=0, max_mb=kept * ENTRY_BYTES / 2**20))


def test_eviction_matches_direct(
    db: Path, conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    cache = _evicting_cache(db, 600)
    state = cache._state  # pyright: ignore[reportPrivateUsage]
    assert state is not None and state.floor is not None
    assert state.entries <= 600
    assert "USAGE_CELL_CACHE_MAX_MB" in caplog.text
    floor = datetime.fromtimestamp(state.floor * SLICE_SECONDS, UTC)
    near = timedelta(minutes=37, seconds=11)
    windows = {
        "before": Window(floor - 8 * near, floor - near),
        "across": Window(floor - near, floor + near),
        "after": Window(floor + near, floor + 8 * near),
        "open start": Window(None, floor + near),
    }
    for window in windows.values():
        for filters in FILTERS.values():
            for name, query in _queries(window, filters).items():
                assert _bytes(query(conn, cache)) == _bytes(query(conn, None)), name
    _same(conn, cache, _options)
    assert cache.values(conn, "model") is None


def test_rows_in_evicted_intervals_count_as_seen(db: Path) -> None:
    cache = _evicting_cache(db, 600)
    with contextlib.closing(open_ro(db)) as conn:
        _same(conn, cache, _options)
        # Rows before every held interval are read from SQL, not rebuilt.
        _insert(db, _records(5, 200, T0 - timedelta(days=2)))
        _all_same(conn, cache, Window(T0 - timedelta(days=3), _at(9, 1)))
    assert cache.builds == 1


def test_entry_bytes_cover_sums_and_priced_result(tmp_path: Path) -> None:
    keys = [f"sk-{i:02d}" + "x" * 45 for i in range(10)]
    models = [f"claude-sonnet-4-5-2025092{i}" for i in range(4)]
    sources = [f"someone.{i}@example-company.com" for i in range(4)]
    combos = [(k, m, s) for k in keys for m in models for s in sources]
    start = datetime(2026, 9, 1, tzinfo=UTC)
    template = _records(6, 1, T0)[0]
    # One entry per interval, so per-interval overhead counts in full.
    records = [
        template.model_copy(
            update={
                "timestamp": (start + timedelta(minutes=15 * n)).isoformat(),
                "api_key": key,
                "model": model,
                "source": source,
                "latency_ms": 1_000 + n,
                "input_tokens": 300_000 + n,
                "output_tokens": 5_000 + n,
                "reasoning_tokens": 1_000 + n,
                "cache_read_tokens": 100_000 + n,
                "cache_creation_tokens": 20_000 + n,
                "cache_creation_1h_tokens": 10_000 + n,
            }
        )
        for n, (key, model, source) in enumerate(combos * 10)
    ]
    db = tmp_path / "big.db"
    _insert(db, records)
    with contextlib.closing(open_ro(db)) as conn:
        cache = CellCache(db)
        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            _built(cache)
            query_options(conn, dict.fromkeys(models, CLAUDE), {}, cache=cache)
            gc.collect()
            used = tracemalloc.get_traced_memory()[0] - before
        finally:
            tracemalloc.stop()
    state = cache._state  # pyright: ignore[reportPrivateUsage]
    assert state is not None and state.entries == len(records)
    assert set(state.priced) == set(state.cells)
    print(f"{used / state.entries:.0f} bytes per entry")
    assert used / state.entries <= ENTRY_BYTES


def _client(db: Path, enabled: bool) -> TestClient:
    cfg = ServerConfig(db_path=db, cell_cache=enabled)
    return TestClient(create_app(cfg, pricing_provider=lambda: PRICING))


def test_server_builds_cache_and_matches_direct(db: Path) -> None:
    params = {
        "start": "2026-10-31T03:07:13.25Z",
        "end": "2026-11-01T17:52:01Z",
        "tz": "America/Chicago",
    }
    filters = [
        {},
        {"people": "sk-a...lice,sk-c...bob"},
        {"models": "gpt-l"},
        {"credentials": "al@x.io"},
    ]
    with _client(db, True) as cached, _client(db, False) as direct:
        cache = cached.app.state.cell_cache  # type: ignore[attr-defined]
        assert direct.app.state.cell_cache is None  # type: ignore[attr-defined]
        _wait_ready(cache, 1)
        for extra in filters:
            for path, dim in (
                ("summary", {}),
                ("breakdown", {"dim": "person"}),
                ("breakdown", {"dim": "model"}),
                ("breakdown", {"dim": "credential"}),
                ("export", {}),
            ):
                url, query = f"/api/usage/{path}", params | extra | dim
                assert cached.get(url, params=query).content == (
                    direct.get(url, params=query).content
                )
        url = "/api/usage/options"
        assert cached.get(url).content == direct.get(url).content
        assert cache.hits > 0


def test_off_switch(monkeypatch: pytest.MonkeyPatch, db: Path) -> None:
    monkeypatch.setenv("USAGE_CELL_CACHE", "0")
    monkeypatch.setenv("USAGE_DB_PATH", str(db))
    assert ServerConfig().cell_cache is False


def test_edges_work_without_the_timestamp_index(db: Path) -> None:
    _write(db, "DROP INDEX idx_requests_ts_utc")
    cache = _built(CellCache(db, verify_seconds=0))
    with contextlib.closing(open_ro(db)) as conn:
        for key in ("mid-interval edges", "inside one interval"):
            for filters in FILTERS.values():
                for name, query in _queries(WINDOWS[key], filters).items():
                    cached, direct = query(conn, cache), query(conn, None)
                    assert _bytes(cached) == _bytes(direct), (key, name)


def test_filtered_edges_use_the_timestamp_index(
    conn: sqlite3.Connection, cache: CellCache
) -> None:
    edges: list[str] = []
    conn.set_trace_callback(
        lambda sql: edges.append(sql) if "datetime(timestamp) >=" in sql else None
    )
    filters = Filters(api_keys=KEYS[:2], models=["gpt-l"], sources=SOURCES[:1])
    _queries(WINDOWS["mid-interval edges"], filters)["summary"](conn, cache)
    conn.set_trace_callback(None)
    assert edges
    for sql in edges:
        plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
        assert any("idx_requests_ts_utc" in row[-1] for row in plan), plan
