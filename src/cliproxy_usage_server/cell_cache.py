"""Usage sums that do not depend on pricing, and the in-memory cache of all history.

Requests are summed per (15-minute UTC slice, api_key, model, source).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
import time
from array import array
from bisect import bisect_left, insort
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, cast

from cliproxy_usage_server.db import SLICE_SECONDS, open_ro
from cliproxy_usage_server.pricing import Amounts, CostPlan

_log = logging.getLogger(__name__)


class Sums(NamedTuple):
    """One group's sums, in ``scan``'s column order."""

    requests: int
    failed: int
    input_tokens: int
    reasoning_tokens: int
    ok_latency_sum: int
    # Totals of the five priced token types.
    uncached_input: int
    output: int
    cache_creation_5m: int
    cache_read: int
    cache_creation_1h: int
    # For each long-context rule ``cost_plan`` can return: each type's tokens at the
    # base rate, and the successful requests with tokens at the long rate.
    marginal_200k_input: int
    marginal_200k_output: int
    marginal_200k_cache_creation_5m: int
    marginal_200k_cache_read: int
    marginal_200k_cache_creation_1h: int
    marginal_200k_long: int
    whole_200k_input: int
    whole_200k_output: int
    whole_200k_cache_creation_5m: int
    whole_200k_cache_read: int
    whole_200k_cache_creation_1h: int
    whole_200k_long: int
    whole_272k_input: int
    whole_272k_output: int
    whole_272k_cache_creation_5m: int
    whole_272k_cache_read: int
    whole_272k_cache_creation_1h: int
    whole_272k_long: int

    def total(self) -> Amounts:
        return Amounts(
            input=self.uncached_input,
            output=self.output,
            cache_creation_5m=self.cache_creation_5m,
            cache_read=self.cache_read,
            cache_creation_1h=self.cache_creation_1h,
        )

    def base(self, plan: CostPlan) -> tuple[Amounts, int]:
        """Base-rate tokens and long requests under *plan*'s long-context rule."""
        match plan.switch, plan.threshold:
            case False, 200_000:
                return Amounts(
                    input=self.marginal_200k_input,
                    output=self.marginal_200k_output,
                    cache_creation_5m=self.marginal_200k_cache_creation_5m,
                    cache_read=self.marginal_200k_cache_read,
                    cache_creation_1h=self.marginal_200k_cache_creation_1h,
                ), self.marginal_200k_long
            case True, 200_000:
                return Amounts(
                    input=self.whole_200k_input,
                    output=self.whole_200k_output,
                    cache_creation_5m=self.whole_200k_cache_creation_5m,
                    cache_read=self.whole_200k_cache_read,
                    cache_creation_1h=self.whole_200k_cache_creation_1h,
                ), self.whole_200k_long
            case True, 272_000:
                return Amounts(
                    input=self.whole_272k_input,
                    output=self.whole_272k_output,
                    cache_creation_5m=self.whole_272k_cache_creation_5m,
                    cache_read=self.whole_272k_cache_read,
                    cache_creation_1h=self.whole_272k_cache_creation_1h,
                ), self.whole_272k_long
        raise ValueError(f"no sums for the long-context rule of {plan}")


COLUMNS = ("api_key", "model", "source")
type Key = tuple[str, str, str]  # values of COLUMNS
# slice, *Key, last_used, sums
type Row = tuple[int, str, str, str, str, Sums]


def scan(conn: sqlite3.Connection, where: str, params: Sequence[object]) -> list[Row]:
    """Sums of the requests matching *where*, ordered by slice then key."""
    rows = conn.execute(
        f"""
        SELECT
            CAST(strftime('%s', timestamp) AS INTEGER) / {SLICE_SECONDS} AS slice_no,
            api_key,
            model,
            source,
            MAX(timestamp) AS last_used,
            COUNT(*) AS requests,
            SUM(failed) AS failed,
            SUM(input_tokens) AS input_tokens,
            SUM(reasoning_tokens) AS reasoning_tokens,
            SUM(IIF(failed, 0, latency_ms)) AS ok_latency_sum,
            SUM(uncached_input) AS uncached_input,
            SUM(output_tokens) AS output,
            SUM(cache_creation_5m) AS cache_creation_5m,
            SUM(cache_read_tokens) AS cache_read,
            SUM(cache_creation_1h_tokens) AS cache_creation_1h,
            -- Marginal: each type's tokens past 200k bill at the long rate.
            SUM(MIN(uncached_input, 200000)) AS marginal_200k_input,
            SUM(MIN(output_tokens, 200000)) AS marginal_200k_output,
            SUM(MIN(cache_creation_5m, 200000)) AS marginal_200k_cache_creation_5m,
            SUM(MIN(cache_read_tokens, 200000)) AS marginal_200k_cache_read,
            SUM(MIN(cache_creation_1h_tokens, 200000))
                AS marginal_200k_cache_creation_1h,
            SUM(
                NOT failed AND MAX(uncached_input, output_tokens, cache_creation_5m,
                                   cache_read_tokens, cache_creation_1h_tokens) > 200000
            ) AS marginal_200k_long,
            -- Whole request: all of it bills at the long rate once input passes 200k.
            SUM(IIF(input_tokens > 200000, 0, uncached_input)) AS whole_200k_input,
            SUM(IIF(input_tokens > 200000, 0, output_tokens)) AS whole_200k_output,
            SUM(IIF(input_tokens > 200000, 0, cache_creation_5m))
                AS whole_200k_cache_creation_5m,
            SUM(IIF(input_tokens > 200000, 0, cache_read_tokens))
                AS whole_200k_cache_read,
            SUM(IIF(input_tokens > 200000, 0, cache_creation_1h_tokens))
                AS whole_200k_cache_creation_1h,
            SUM(NOT failed AND input_tokens > 200000) AS whole_200k_long,
            -- The same past 272k.
            SUM(IIF(input_tokens > 272000, 0, uncached_input)) AS whole_272k_input,
            SUM(IIF(input_tokens > 272000, 0, output_tokens)) AS whole_272k_output,
            SUM(IIF(input_tokens > 272000, 0, cache_creation_5m))
                AS whole_272k_cache_creation_5m,
            SUM(IIF(input_tokens > 272000, 0, cache_read_tokens))
                AS whole_272k_cache_read,
            SUM(IIF(input_tokens > 272000, 0, cache_creation_1h_tokens))
                AS whole_272k_cache_creation_1h,
            SUM(NOT failed AND input_tokens > 272000) AS whole_272k_long
        FROM (
            SELECT
                timestamp,
                api_key,
                model,
                source,
                failed,
                latency_ms,
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cache_read_tokens,
                cache_creation_1h_tokens,
                input_tokens - cache_read_tokens - cache_creation_tokens
                    AS uncached_input,
                cache_creation_tokens - cache_creation_1h_tokens AS cache_creation_5m
            FROM requests {where}
        )
        GROUP BY slice_no, api_key, model, source
        ORDER BY slice_no, api_key, model, source
        """,
        list(params),
    ).fetchall()
    return [
        (slice_no, api_key, model, source, last_used, Sums(*sums))
        for slice_no, api_key, model, source, last_used, *sums in rows
    ]


@dataclass(slots=True)
class _Entry:
    last_used: str
    sums: array[int]


class Hit[T](NamedTuple):
    """A cached read: priced cells, and the last rowid they include."""

    max_rowid: int
    cells: list[tuple[Key, T]]


@dataclass(slots=True)
class _State:
    file_id: tuple[int, int]
    schema_version: int
    max_rowid: int
    last_timestamp: str | None  # of the row at max_rowid
    verified_at: float
    row_count: int = 0
    entries: dict[int, dict[Key, _Entry]] = field(default_factory=dict)
    slices: list[int] = field(default_factory=list)  # sorted keys of entries
    # One shared copy of each distinct value, by column.
    values: dict[str, dict[str, str]] = field(
        default_factory=lambda: {c: {} for c in COLUMNS}
    )
    priced: dict[int, list[tuple[Key, Any]]] = field(default_factory=dict)
    priced_by: object = None  # the pricing map the memo was priced with

    def add(self, row: Row) -> None:
        slice_no, api_key, model, source, last_used, sums = row
        self.row_count += sums.requests
        values = self.values
        key = (
            values["api_key"].setdefault(api_key, api_key),
            values["model"].setdefault(model, model),
            values["source"].setdefault(source, source),
        )
        group = self.entries.get(slice_no)
        if group is None:
            group = self.entries[slice_no] = {}
            insort(self.slices, slice_no)
        entry = group.get(key)
        if entry is None:
            group[key] = _Entry(last_used, array("q", sums))
        else:
            for i, v in enumerate(sums):
                entry.sums[i] += v
            entry.last_used = max(entry.last_used, last_used)
        self.priced.pop(slice_no, None)


def _schema_and_max_rowid(conn: sqlite3.Connection) -> tuple[int, int]:
    (schema_version,) = conn.execute("PRAGMA schema_version").fetchone()
    (max_rowid,) = conn.execute("SELECT MAX(rowid) FROM requests").fetchone()
    return schema_version, max_rowid or 0


def _timestamp_at(conn: sqlite3.Connection, rowid: int) -> str | None:
    row = conn.execute(
        "SELECT timestamp FROM requests WHERE rowid = ?", [rowid]
    ).fetchone()
    return None if row is None else row[0]


@contextlib.contextmanager
def _snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run statements in one read transaction so they see the same rows."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.commit()


_RETRY_SECONDS = 30.0


class CellCache:
    """Cell sums of one DB's whole history, kept current by adding new rows."""

    def __init__(self, db_path: Path, *, verify_seconds: float = 60.0) -> None:
        self._db_path = db_path
        self._verify_seconds = verify_seconds
        self._lock = threading.Lock()
        self._state: _State | None = None
        self._building = False
        self._retry_at = 0.0
        self.hits = 0  # reads served from the cache

    @property
    def ready(self) -> bool:
        return self._state is not None

    def start(self) -> None:
        """Build in a background thread unless a build is running or ready."""
        with self._lock:
            if self._state is None:
                self._start_build()

    def read[T](
        self,
        conn: sqlite3.Connection,
        lo: int | None,
        hi: int | None,
        pricing: object,
        price: Callable[[Row], T],
    ) -> Hit[T] | None:
        """Cells of the slices in ``[lo, hi)`` (None: unbounded) priced by *price*, or
        None until ready; memoized per slice while *pricing* is the same object."""
        with self._lock:
            st = self._synced(conn)
            if st is None:
                return None
            if st.priced_by is not pricing:
                st.priced = {}
                st.priced_by = pricing
            i = 0 if lo is None else bisect_left(st.slices, lo)
            j = len(st.slices) if hi is None else bisect_left(st.slices, hi)
            cells = [c for n in st.slices[i:j] for c in self._priced(st, n, price)]
            self.hits += 1
            return Hit(st.max_rowid, cells)

    def values(self, conn: sqlite3.Connection, column: str) -> list[str] | None:
        """Distinct raw values of *column*, or None until ready."""
        with self._lock:
            st = self._synced(conn)
            if st is None:
                return None
            return list(st.values[column])

    @staticmethod
    def _priced[T](
        st: _State, slice_no: int, price: Callable[[Row], T]
    ) -> list[tuple[Key, T]]:
        memo = st.priced.get(slice_no)
        if memo is None:
            memo = st.priced[slice_no] = [
                (key, price((slice_no, *key, e.last_used, Sums._make(e.sums))))
                for key, e in sorted(st.entries[slice_no].items())
            ]
        return cast(list[tuple[Key, T]], memo)

    def _file_id(self) -> tuple[int, int]:
        st = os.stat(self._db_path)
        return st.st_dev, st.st_ino

    def _synced(self, conn: sqlite3.Connection) -> _State | None:
        """Add rows past the last seen rowid; drop the cache on a rewrite."""
        st = self._state
        if st is None:
            self._start_build()
            return None
        now = time.monotonic()
        verify = now >= st.verified_at + self._verify_seconds
        try:
            intact = self._file_id() == st.file_id
        except OSError:
            intact = False
        if intact:
            with _snapshot(conn):
                intact = self._add_new(conn, st, verify)
        if not intact:
            _log.info("usage DB rewritten; rebuilding the cell cache")
            self._state = None
            self._start_build()
            return None
        if verify:
            st.verified_at = now
        return st

    @staticmethod
    def _add_new(conn: sqlite3.Connection, st: _State, verify: bool) -> bool:
        schema_version, max_rowid = _schema_and_max_rowid(conn)
        if schema_version != st.schema_version or max_rowid < st.max_rowid:
            return False
        if st.max_rowid and _timestamp_at(conn, st.max_rowid) != st.last_timestamp:
            return False
        if max_rowid > st.max_rowid:
            for row in scan(conn, "WHERE rowid > ?", [st.max_rowid]):
                st.add(row)
            st.max_rowid = max_rowid
            st.last_timestamp = _timestamp_at(conn, max_rowid)
        if not verify:
            return True
        (count,) = conn.execute("SELECT COUNT(*) FROM requests").fetchone()
        return count == st.row_count

    def _load(self) -> _State:
        file_id = self._file_id()
        with contextlib.closing(open_ro(self._db_path)) as conn, _snapshot(conn):
            schema_version, max_rowid = _schema_and_max_rowid(conn)
            st = _State(
                file_id=file_id,
                schema_version=schema_version,
                max_rowid=max_rowid,
                last_timestamp=_timestamp_at(conn, max_rowid),
                verified_at=time.monotonic(),
            )
            for row in scan(conn, "", []):
                st.add(row)
        return st

    def _start_build(self) -> None:
        """Start a background build; the caller holds the lock."""
        if self._building or time.monotonic() < self._retry_at:
            return
        self._building = True
        threading.Thread(
            target=self._build, name="usage-cell-cache", daemon=True
        ).start()

    def _build(self) -> None:
        try:
            state = self._load()
        except Exception as exc:
            _log.warning("cell cache build failed: %s", exc)
            state = None
        with self._lock:
            self._building = False
            if state is None:
                self._retry_at = time.monotonic() + _RETRY_SECONDS
            else:
                self._state = state
