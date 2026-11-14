"""Pricing-independent cell sums and the in-memory cache that keeps them for history.

A cell groups requests by (15-minute UTC interval, api_key, model, source). Its sums
hold every ``PLAN_SHAPES`` base-rate share, so pricing picks each model's shape later.
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
from cliproxy_usage_server.pricing import PLAN_SHAPES, Amounts, PlanShape

_log = logging.getLogger(__name__)

# Per-request tokens of each priced type, by ``Amounts`` field.
_PRICED: dict[str, str] = {
    "input": "input_tokens - cache_read_tokens - cache_creation_tokens",
    "output": "output_tokens",
    "cache_creation": "cache_creation_tokens - cache_creation_1h_tokens",
    "cache_read": "cache_read_tokens",
    "cache_creation_1h": "cache_creation_1h_tokens",
}
_FIELDS = Amounts._fields

# Layout of a cell's sums: plain counts, then long requests per shape,
# then every type's total, then every type's base-rate share per shape.
REQUESTS, FAILED, INPUT_TOKENS, REASONING_TOKENS, OK_LATENCY_SUM = range(5)
LONG_AT = 5
TOTAL_AT = LONG_AT + len(PLAN_SHAPES)
BASE_AT = TOTAL_AT + len(_FIELDS)
TOTAL = slice(TOTAL_AT, BASE_AT)


def base_slice(shape: int) -> slice:
    """Position of shape *shape*'s base-rate ``Amounts`` in a cell's sums."""
    start = BASE_AT + shape * len(_FIELDS)
    return slice(start, start + len(_FIELDS))


# Estimated bytes of one entry: its sums plus one memoized priced result.
ENTRY_BYTES = 2048

COLUMNS = ("api_key", "model", "source")
type Key = tuple[str, str, str]  # values of COLUMNS
# interval (None when SQLite cannot parse the timestamp), *Key, last_used, sums
type Row = tuple[int | None, str, str, str, str, Sequence[int]]


def _base_share(column: str, shape: PlanShape) -> str:
    t = shape.threshold
    if shape.switch:
        return f"CASE WHEN input_tokens > {t} THEN 0 ELSE {column} END"
    return f"MIN({column}, {t})"


def _is_long(shape: PlanShape) -> str:
    """A request has tokens at the long rate under *shape*."""
    t = shape.threshold
    if shape.switch:
        return f"input_tokens > {t}"
    return f"MAX({', '.join(f'priced_{f}' for f in _FIELDS)}) > {t}"


_SUMS = ",\n".join(
    [
        "COUNT(*)",
        "SUM(failed)",
        "SUM(input_tokens)",
        "SUM(reasoning_tokens)",
        "SUM(CASE WHEN failed THEN 0 ELSE latency_ms END)",
        *(
            f"SUM(CASE WHEN NOT failed AND {_is_long(s)} THEN 1 ELSE 0 END)"
            for s in PLAN_SHAPES
        ),
        *(f"SUM(priced_{f})" for f in _FIELDS),
        *(
            f"SUM({_base_share(f'priced_{f}', s)})"
            for s in PLAN_SHAPES
            for f in _FIELDS
        ),
    ]
)
_PRICED_COLUMNS = ", ".join(f"{_PRICED[f]} AS priced_{f}" for f in _FIELDS)


def scan(
    conn: sqlite3.Connection,
    where: str,
    params: Sequence[object],
    *,
    by_time: bool = False,
) -> list[Row]:
    """Cell sums of the requests matching *where*, ordered by interval then key.
    *by_time* makes SQLite use the timestamp index whatever else *where* filters."""
    index = "INDEXED BY idx_requests_ts_utc" if by_time else ""
    rows = conn.execute(
        f"""
        SELECT
            CAST(strftime('%s', timestamp) AS INTEGER) / {SLICE_SECONDS} AS slice_no,
            api_key, model, source, MAX(timestamp),
            {_SUMS}
        FROM (
            SELECT timestamp, api_key, model, source, failed, latency_ms,
                input_tokens, reasoning_tokens, {_PRICED_COLUMNS}
            FROM requests {index} {where}
        )
        GROUP BY slice_no, api_key, model, source
        ORDER BY slice_no, api_key, model, source
        """,
        list(params),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4], r[5:]) for r in rows]


@dataclass(slots=True)
class _Entry:
    last_used: str
    sums: array[int]


class Hit[T](NamedTuple):
    """A cached read: priced cells of the intervals from *start* (None: all) on."""

    max_rowid: int
    start: int | None
    unslotted: list[tuple[Key, T]]
    cells: list[tuple[Key, T]]


type _Interned = tuple[dict[str, str], dict[str, str], dict[str, str]]


def _interned() -> _Interned:
    return {}, {}, {}


@dataclass(slots=True)
class _State:
    file_id: tuple[int, int]
    schema_version: int
    max_rowid: int
    anchor: str | None  # timestamp of the row at max_rowid
    verified_at: float
    seen: int = 0  # rows folded in, evicted ones included
    entries: int = 0
    floor: int | None = None  # intervals below it are evicted
    cells: dict[int | None, dict[Key, _Entry]] = field(default_factory=dict)
    order: list[int] = field(default_factory=list)  # sorted intervals, None excluded
    values: _Interned = field(default_factory=_interned)  # by COLUMNS
    priced: dict[int | None, list[tuple[Key, Any]]] = field(default_factory=dict)
    priced_by: object = None

    def fold(self, row: Row) -> None:
        slot, api_key, model, source, last_used, sums = row
        self.seen += sums[REQUESTS]
        if slot is not None and self.floor is not None and slot < self.floor:
            return
        keys, models, sources = self.values
        key = (
            keys.setdefault(api_key, api_key),
            models.setdefault(model, model),
            sources.setdefault(source, source),
        )
        slot_cells = self.cells.get(slot)
        if slot_cells is None:
            slot_cells = self.cells[slot] = {}
            if slot is not None:
                insort(self.order, slot)
        entry = slot_cells.get(key)
        if entry is None:
            slot_cells[key] = _Entry(last_used, array("q", sums))
            self.entries += 1
        else:
            for i, v in enumerate(sums):
                entry.sums[i] += v
            entry.last_used = max(entry.last_used, last_used)
        self.priced.pop(slot, None)

    def evict(self, max_entries: int) -> bool:
        """Drop the oldest intervals until the entries fit; True if any were dropped."""
        n = 0
        while self.entries > max_entries and n < len(self.order):
            self.entries -= len(self.cells.pop(self.order[n]))
            self.priced.pop(self.order[n], None)
            n += 1
        if n:
            self.floor = self.order[n - 1] + 1
            del self.order[:n]
        return n > 0


def _head(conn: sqlite3.Connection) -> tuple[int, int]:
    (schema_version,) = conn.execute("PRAGMA schema_version").fetchone()
    (max_rowid,) = conn.execute("SELECT MAX(rowid) FROM requests").fetchone()
    return schema_version, max_rowid or 0


def _anchor(conn: sqlite3.Connection, rowid: int) -> str | None:
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
    """Cell sums of one DB's whole history, kept current by folding in new rows."""

    def __init__(
        self, db_path: Path, *, verify_seconds: float = 60.0, max_mb: float = 512.0
    ) -> None:
        self._db_path = db_path
        self._verify_seconds = verify_seconds
        self._max_entries = int(max_mb * 2**20) // ENTRY_BYTES
        self._lock = threading.Lock()
        self._state: _State | None = None
        self._generation = 0
        self._building = False
        self._retry_at = 0.0
        self._evicted = False  # the first eviction was logged
        self.builds = 0  # completed builds, for tests and benchmarks
        self.hits = 0  # reads served from the cache

    @property
    def ready(self) -> bool:
        return self._state is not None

    def start(self) -> None:
        """Build in a background thread unless a build is running or ready."""
        with self._lock:
            if self._state is None:
                self._kick()

    def build(self) -> None:
        """Build synchronously and install the result."""
        with self._lock:
            generation = self._generation
        state = self._load()
        with self._lock:
            if generation == self._generation:
                self._install(state)

    def read[T](
        self,
        conn: sqlite3.Connection,
        lo: int | None,
        hi: int | None,
        *,
        unslotted: bool,
        price: Callable[[Row], T],
    ) -> Hit[T] | None:
        """Priced cells of the held intervals in ``[lo, hi)`` (None: unbounded).
        None until ready; *price* keys the memo of priced cells."""
        with self._lock:
            st = self._synced(conn)
            if st is None:
                return None
            if st.priced_by is not price:
                st.priced = {}
                st.priced_by = price
            start = lo
            if st.floor is not None and (lo is None or lo < st.floor):
                start = st.floor
            if start is not None and hi is not None:
                start = min(start, hi)
            i = 0 if start is None else bisect_left(st.order, start)
            j = len(st.order) if hi is None else bisect_left(st.order, hi)
            cells = [c for s in st.order[i:j] for c in self._priced(st, s, price)]
            loose = (
                self._priced(st, None, price) if unslotted and None in st.cells else []
            )
            self.hits += 1
            return Hit(st.max_rowid, start, loose, cells)

    def values(self, conn: sqlite3.Connection, column: str) -> list[str] | None:
        """Distinct raw values of *column*; None until ready or while any is evicted."""
        with self._lock:
            st = self._synced(conn)
            if st is None or st.floor is not None:
                return None
            return list(st.values[COLUMNS.index(column)])

    @staticmethod
    def _priced[T](
        st: _State, slot: int | None, price: Callable[[Row], T]
    ) -> list[tuple[Key, T]]:
        memo = st.priced.get(slot)
        if memo is None:
            memo = st.priced[slot] = [
                (key, price((slot, *key, e.last_used, e.sums)))
                for key, e in sorted(st.cells[slot].items())
            ]
        return cast(list[tuple[Key, T]], memo)

    def _file_id(self) -> tuple[int, int]:
        st = os.stat(self._db_path)
        return st.st_dev, st.st_ino

    def _synced(self, conn: sqlite3.Connection) -> _State | None:
        """Fold rows past the last seen rowid; drop the cache on a rewrite."""
        st = self._state
        if st is None:
            self._kick()
            return None
        now = time.monotonic()
        verify = now >= st.verified_at + self._verify_seconds
        try:
            intact = self._file_id() == st.file_id
        except OSError:
            intact = False
        if intact:
            with _snapshot(conn):
                intact = self._fold_new(conn, st, verify)
        if not intact:
            _log.info("usage DB rewritten; rebuilding the cell cache")
            self._state = None
            self._generation += 1
            self._kick()
            return None
        if verify:
            st.verified_at = now
        self._evict(st)
        return st

    @staticmethod
    def _fold_new(conn: sqlite3.Connection, st: _State, verify: bool) -> bool:
        schema_version, max_rowid = _head(conn)
        if schema_version != st.schema_version or max_rowid < st.max_rowid:
            return False
        if st.max_rowid and _anchor(conn, st.max_rowid) != st.anchor:
            return False
        if max_rowid > st.max_rowid:
            for row in scan(conn, "WHERE rowid > ?", [st.max_rowid]):
                st.fold(row)
            st.max_rowid = max_rowid
            st.anchor = _anchor(conn, max_rowid)
        if not verify:
            return True
        return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == st.seen

    def _load(self) -> _State:
        file_id = self._file_id()
        with contextlib.closing(open_ro(self._db_path)) as conn, _snapshot(conn):
            schema_version, max_rowid = _head(conn)
            st = _State(
                file_id=file_id,
                schema_version=schema_version,
                max_rowid=max_rowid,
                anchor=_anchor(conn, max_rowid),
                verified_at=time.monotonic(),
            )
            for row in scan(conn, "", []):
                st.fold(row)
        return st

    def _evict(self, st: _State) -> None:
        if st.evict(self._max_entries) and not self._evicted:
            self._evicted = True
            _log.warning(
                "cell cache is over USAGE_CELL_CACHE_MAX_MB; "
                "intervals before the oldest kept one are read from the DB"
            )

    def _install(self, st: _State) -> None:
        self._evict(st)
        self._state = st
        self.builds += 1

    def _kick(self) -> None:
        """Start a background build; the caller holds the lock."""
        if self._building or time.monotonic() < self._retry_at:
            return
        self._building = True
        threading.Thread(
            target=self._build_loop, name="usage-cell-cache", daemon=True
        ).start()

    def _build_loop(self) -> None:
        while True:
            with self._lock:
                generation = self._generation
            try:
                state = self._load()
            except Exception as exc:
                _log.warning("cell cache build failed: %s", exc)
                with self._lock:
                    self._building = False
                    self._retry_at = time.monotonic() + _RETRY_SECONDS
                return
            with self._lock:
                if generation == self._generation:
                    self._install(state)
                    self._building = False
                    return
