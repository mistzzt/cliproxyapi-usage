"""The cell cache returns exactly what the direct query does.

Each check runs an endpoint query with and without a ``CellCache`` and compares
the JSON (or CSV) bytes, so floats must match bit for bit.
"""

from __future__ import annotations

import contextlib
import os
import random
import sqlite3
import threading
import time
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
from cliproxy_usage_server.cell_cache import CellCache, Sums, scan
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


def _write(db: Path, sql: str) -> None:
    with contextlib.closing(sqlite3.connect(db)) as w, w:
        w.execute(sql)


def _make_db(path: Path) -> Path:
    pinned = _records(4, 1, T0)[0].model_copy(
        update={"timestamp": "2026-10-31T04:15:00.200Z"}
    )
    _insert(path, [*_records(1, 3_000, T0), pinned])
    return path


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return _make_db(tmp_path / "usage.db")


@pytest.fixture
def conn(db: Path) -> Iterator[sqlite3.Connection]:
    with contextlib.closing(open_ro(db)) as c:
        yield c


def _wait_ready(cache: CellCache) -> None:
    deadline = time.monotonic() + 10
    while not cache.ready:
        assert time.monotonic() < deadline, "cache did not rebuild"
        time.sleep(0.01)


def _built(cache: CellCache) -> CellCache:
    cache.start()
    _wait_ready(cache)
    return cache


@pytest.fixture
def cache(db: Path) -> CellCache:
    return _built(CellCache(db, verify_seconds=0))


def _at(h: int, m: int = 0, s: int = 0, us: int = 0, day: int = 1) -> datetime:
    return T0 + timedelta(days=day, hours=h, minutes=m, seconds=s, microseconds=us)


WINDOWS = {
    "mid-slice edges": Window(_at(3, 7, 13, 250_000), _at(17, 52, 1, 999_000, day=2)),
    "aligned": Window(_at(0), _at(12)),
    # Its previous period lies inside the same slice too.
    "inside one slice": Window(_at(5, 8, 30), _at(5, 14, 50)),
    "adjacent slices": Window(_at(5, 1), _at(5, 29)),
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


def _same(
    conn: sqlite3.Connection,
    cache: CellCache,
    query: Query,
    *,
    hit: bool = True,
    label: str = "",
) -> None:
    """The cached read gives the direct query's bytes and counts a hit iff *hit*."""
    hits = cache.hits
    cached = _bytes(query(conn, cache))
    assert (cache.hits > hits) == hit, label
    assert cached == _bytes(query(conn, None)), label


def _all_same(
    conn: sqlite3.Connection,
    cache: CellCache,
    window: Window,
    filters: Filters = Filters(),  # noqa: B008
) -> None:
    """Every query of *window* matches; only one inside a slice skips the cache."""
    inside = window is WINDOWS["inside one slice"]
    for name, query in _queries(window, filters).items():
        _same(conn, cache, query, hit=not inside, label=name)


@pytest.mark.parametrize("window", WINDOWS.values(), ids=list(WINDOWS))
@pytest.mark.parametrize("filters", FILTERS.values(), ids=list(FILTERS))
def test_cached_queries_match_direct(
    conn: sqlite3.Connection, cache: CellCache, window: Window, filters: Filters
) -> None:
    _all_same(conn, cache, window, filters)


def test_options_match_direct(conn: sqlite3.Connection, cache: CellCache) -> None:
    _same(conn, cache, _options)


def test_insert_between_requests(
    db: Path, conn: sqlite3.Connection, cache: CellCache
) -> None:
    window = WINDOWS["mid-slice edges"]
    query = _queries(window, Filters())["breakdown model"]
    _same(conn, cache, query)
    # New rows land in held slices, edge slices and new slices.
    _insert(db, _records(2, 500, T0 + timedelta(days=1)))
    _same(conn, cache, query)
    _same(conn, cache, _options)
    assert sorted(cache.values(conn, "model") or []) == sorted(MODELS)


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
        # The read that notices falls back to the direct query.
        _same(fresh, cache, query, hit=False)
        _wait_ready(cache)
        _all_same(fresh, cache, WINDOWS["mid-slice edges"])
        _same(fresh, cache, _options)


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
    window = WINDOWS["mid-slice edges"]
    for name, query in _queries(window, Filters()).items():
        _same(conn, cache, query, hit=False, label=name)
    assert not cache.ready
    release.set()
    _wait_ready(cache)
    _all_same(conn, cache, window)
    _same(conn, cache, _options)


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
        _wait_ready(cache)
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
        for key in ("mid-slice edges", "inside one slice"):
            for filters in FILTERS.values():
                _all_same(conn, cache, WINDOWS[key], filters)


def test_filtered_edges_use_the_timestamp_index(
    conn: sqlite3.Connection, cache: CellCache
) -> None:
    edges: list[str] = []
    conn.set_trace_callback(
        lambda sql: edges.append(sql) if "datetime(timestamp) >=" in sql else None
    )
    filters = Filters(api_keys=KEYS[:2], models=["gpt-l"], sources=SOURCES[:1])
    _queries(WINDOWS["mid-slice edges"], filters)["summary"](conn, cache)
    conn.set_trace_callback(None)
    assert edges
    for sql in edges:
        plan = conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
        assert any("idx_requests_ts_utc" in row[-1] for row in plan), plan


def test_scan_sums_each_long_context_rule() -> None:
    # Input 250k: past 200k but not 272k; uncached input alone passes 200k.
    conn = open_db(Path(":memory:"))
    record = RequestRecord(
        timestamp="2026-10-01T00:00:00Z",
        api_key="k",
        model="m",
        source="s",
        auth_index="0",
        latency_ms=100,
        input_tokens=250_000,
        output_tokens=1_000,
        reasoning_tokens=7,
        cache_read_tokens=30_000,
        cache_creation_tokens=15_000,
        cache_creation_1h_tokens=5_000,
        total_tokens=0,
        service_tier=None,
        ttft_ms=None,
        failed=False,
    )
    insert_records(conn, [record])
    [(_, _, _, _, _, sums)] = scan(conn, "", [])
    conn.close()
    assert sums == Sums(
        requests=1,
        failed=0,
        input_tokens=250_000,
        reasoning_tokens=7,
        ok_latency_sum=100,
        uncached_input=205_000,
        output=1_000,
        cache_creation_5m=10_000,
        cache_read=30_000,
        cache_creation_1h=5_000,
        marginal_200k_input=200_000,
        marginal_200k_output=1_000,
        marginal_200k_cache_creation_5m=10_000,
        marginal_200k_cache_read=30_000,
        marginal_200k_cache_creation_1h=5_000,
        marginal_200k_long=1,
        whole_200k_input=0,
        whole_200k_output=0,
        whole_200k_cache_creation_5m=0,
        whole_200k_cache_read=0,
        whole_200k_cache_creation_1h=0,
        whole_200k_long=1,
        whole_272k_input=205_000,
        whole_272k_output=1_000,
        whole_272k_cache_creation_5m=10_000,
        whole_272k_cache_read=30_000,
        whole_272k_cache_creation_1h=5_000,
        whole_272k_long=0,
    )
