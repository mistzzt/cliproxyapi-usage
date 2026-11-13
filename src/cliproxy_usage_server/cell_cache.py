"""Plan-independent cell sums and the process-wide slot cache that holds them.

A cell groups requests by (15-minute UTC slot, api_key, model, source). Its sums
carry the base-rate share of every ``PLAN_SHAPES`` entry, so pricing picks each
model's shape afterwards and the sums never depend on the pricing map.

``CellCache`` keeps every cell of the DB in memory. One scan builds it in a
background thread; each read first folds in rows whose ``rowid`` passed the last
seen maximum. The collector only appends (``INSERT OR IGNORE``), so that is exact;
a replaced file, schema change, lower ``MAX(rowid)``, changed anchor row or (every
``verify_seconds``) a changed ``COUNT(*)`` drops the cache and rebuilds it.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
import threading
import time
from bisect import bisect_left, insort
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

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

# Layout of a cell's integer sums: plain counts, then long requests per shape,
# then every type's total, then every type's base-rate share per shape.
REQUESTS, FAILED, INPUT_TOKENS, REASONING_TOKENS, OK_LATENCY_SUM = range(5)
LONG_AT = 5
TOTAL_AT = LONG_AT + len(PLAN_SHAPES)
BASE_AT = TOTAL_AT + len(_FIELDS)


def base_slice(shape: int) -> slice:
    """Position of shape *shape*'s base-rate ``Amounts`` in a cell's sums."""
    start = BASE_AT + shape * len(_FIELDS)
    return slice(start, start + len(_FIELDS))


TOTAL = slice(TOTAL_AT, BASE_AT)

type Key = tuple[str, str, str]  # api_key, model, source
# slot (None for unparseable timestamps), api_key, model, source, last_used, sums
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


def scan(conn: sqlite3.Connection, where: str, params: Sequence[object]) -> list[Row]:
    """Cell sums of the requests matching *where*, ordered by slot then key."""
    rows = conn.execute(
        f"""
        SELECT
            CAST(strftime('%s', timestamp) AS INTEGER) / {SLICE_SECONDS} AS slice_no,
            api_key, model, source, MAX(timestamp),
            {_SUMS}
        FROM (
            SELECT timestamp, api_key, model, source, failed, latency_ms,
                input_tokens, reasoning_tokens, {_PRICED_COLUMNS}
            FROM requests {where}
        )
        GROUP BY slice_no, api_key, model, source
        ORDER BY slice_no, api_key, model, source
        """,
        list(params),
    ).fetchall()
    return [(r[0], r[1], r[2], r[3], r[4], r[5:]) for r in rows]


@dataclass(slots=True)
class _Sums:
    last_used: str
    ints: list[int]


@dataclass(slots=True)
class _State:
    file_id: tuple[int, int]
    schema_version: int
    max_rowid: int
    anchor: str | None  # timestamp of the row at max_rowid
    count: int
    verified_at: float
    cells: dict[int | None, dict[Key, _Sums]] = field(default_factory=dict)
    order: list[int] = field(default_factory=list)  # sorted non-None slots
    priced: dict[int | None, list[tuple[Key, Any]]] = field(default_factory=dict)
    priced_by: object = None

    def fold(self, row: Row) -> None:
        slot, api_key, model, source, last_used, ints = row
        slot_cells = self.cells.get(slot)
        if slot_cells is None:
            slot_cells = self.cells[slot] = {}
            if slot is not None:
                insort(self.order, slot)
        key = (api_key, model, source)
        sums = slot_cells.get(key)
        if sums is None:
            slot_cells[key] = _Sums(last_used, list(ints))
        else:
            sums.ints = [a + b for a, b in zip(sums.ints, ints, strict=True)]
            sums.last_used = max(sums.last_used, last_used)
        self.count += ints[REQUESTS]
        self.priced.pop(slot, None)


def _head(conn: sqlite3.Connection) -> tuple[int, int]:
    (schema_version,) = conn.execute("PRAGMA schema_version").fetchone()
    (max_rowid,) = conn.execute("SELECT MAX(rowid) FROM requests").fetchone()
    return schema_version, max_rowid or 0


def _anchor(conn: sqlite3.Connection, rowid: int) -> str | None:
    row = conn.execute(
        "SELECT timestamp FROM requests WHERE rowid = ?", [rowid]
    ).fetchone()
    return None if row is None else row[0]


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]


class _Read:
    """Run statements in one read transaction so they see the same rows."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._began = False

    def __enter__(self) -> sqlite3.Connection:
        if not self._conn.in_transaction:
            self._conn.execute("BEGIN")
            self._began = True
        return self._conn

    def __exit__(self, *_: object) -> None:
        if self._began:
            self._conn.commit()


_RETRY_SECONDS = 30.0


class CellCache:
    """Process-wide cell sums of one DB, kept current by folding in new rows."""

    def __init__(self, db_path: Path, *, verify_seconds: float = 60.0) -> None:
        self._db_path = db_path
        self._verify_seconds = verify_seconds
        self._lock = threading.Lock()
        self._state: _State | None = None
        self._generation = 0
        self._building = False
        self._retry_at = 0.0
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
    ) -> tuple[int, list[tuple[Key, T]]] | None:
        """Priced cells of slots in ``[lo, hi)`` (None: unbounded) and the rowid
        they cover; None while the cache is not ready. *price* keys the memo."""
        with self._lock:
            st = self._synced(conn)
            if st is None:
                return None
            if st.priced_by is not price:
                st.priced = {}
                st.priced_by = price
            out: list[tuple[Key, T]] = []
            if unslotted and None in st.cells:
                out.extend(self._priced(st, None, price))
            i = 0 if lo is None else bisect_left(st.order, lo)
            j = len(st.order) if hi is None else bisect_left(st.order, hi)
            for slot in st.order[i:j]:
                out.extend(self._priced(st, slot, price))
            self.hits += 1
            return st.max_rowid, out

    @staticmethod
    def _priced[T](
        st: _State, slot: int | None, price: Callable[[Row], T]
    ) -> list[tuple[Key, T]]:
        memo = st.priced.get(slot)
        if memo is None:
            memo = st.priced[slot] = [
                (key, price((slot, *key, s.last_used, s.ints)))
                for key, s in sorted(st.cells[slot].items())
            ]
        return cast(list[tuple[Key, T]], memo)

    def _file_id(self) -> tuple[int, int]:
        st = os.stat(self._db_path)
        return st.st_dev, st.st_ino

    def _synced(self, conn: sqlite3.Connection) -> _State | None:
        """Fold rows past the cached max rowid; drop the cache on a rewrite."""
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
            with _Read(conn):
                intact = self._fold_new(conn, st, verify)
        if not intact:
            _log.info("usage DB rewritten; rebuilding the cell cache")
            self._state = None
            self._generation += 1
            self._kick()
            return None
        if verify:
            st.verified_at = now
        return st

    @staticmethod
    def _fold_new(conn: sqlite3.Connection, st: _State, verify: bool) -> bool:
        schema_version, max_rowid = _head(conn)
        if schema_version != st.schema_version or max_rowid < st.max_rowid:
            return False
        if st.max_rowid and _anchor(conn, st.max_rowid) != st.anchor:
            return False
        if max_rowid > st.max_rowid:
            for row in scan(
                conn, "WHERE rowid > ? AND rowid <= ?", [st.max_rowid, max_rowid]
            ):
                st.fold(row)
            st.max_rowid = max_rowid
            st.anchor = _anchor(conn, max_rowid)
        return not verify or _count(conn) == st.count

    def _load(self) -> _State:
        file_id = self._file_id()
        with contextlib.closing(open_ro(self._db_path)) as conn, _Read(conn):
            schema_version, max_rowid = _head(conn)
            st = _State(
                file_id=file_id,
                schema_version=schema_version,
                max_rowid=max_rowid,
                anchor=_anchor(conn, max_rowid),
                count=0,
                verified_at=time.monotonic(),
            )
            for row in scan(conn, "WHERE rowid <= ?", [max_rowid]):
                st.fold(row)
        return st

    def _install(self, st: _State) -> None:
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
