"""Read-only DB access and time-range helpers for the usage webapp server."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

# Span at or below which sparklines/auto-selection prefer hour buckets.
_HOUR_SPAN_MAX = timedelta(hours=48)

# Bucket-count guard: an "hour" window wider than this many hours is
# auto-coarsened to "day" buckets to avoid emitting thousands of labels /
# SQL groups / chart points. ~240 hours ≈ 10 days.
_HOUR_BUCKET_CAP_HOURS = 240


def open_ro(path: Path) -> sqlite3.Connection:
    """Open the SQLite DB at *path* in read-only mode.

    Raises ``FileNotFoundError`` if *path* does not exist.
    The URI ``?mode=ro`` prevents any writes on the returned connection.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))
    uri = f"file:{path}?mode=ro"
    # check_same_thread=False: FastAPI runs sync endpoints in a threadpool, and
    # dependency teardown (conn.close()) may happen on a different thread than
    # the one that created the connection. The URI enforces read-only, so there
    # is no concurrent-write hazard; connections are per-request, not shared.
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def bucket_for_span(
    start: datetime | None,
    end: datetime,
) -> Literal["hour", "day"]:
    """Pick a bucket granularity from the window span.

    - ``start is None`` (open-ended "all") → ``"day"`` (no span to measure).
    - span ``<= 48h`` → ``"hour"``.
    - otherwise → ``"day"``.
    """
    if start is None:
        return "day"
    return "hour" if (end - start) <= _HOUR_SPAN_MAX else "day"


def coarsen_bucket(
    start: datetime | None,
    end: datetime,
    bucket: Literal["hour", "day"],
) -> Literal["hour", "day"]:
    """Auto-coarsen an ``"hour"`` bucket to ``"day"`` for wide windows.

    Guards the unbounded dense-label generators against runaway bucket counts:

    - ``"day"`` buckets pass through unchanged (naturally bounded).
    - ``start is None`` with ``"hour"`` → ``"day"`` unconditionally (an
      open-ended all-time hourly series is the worst runaway).
    - an ``"hour"`` window wider than the cap (~10 days) → ``"day"``.
    """
    if bucket != "hour":
        return bucket
    if start is None:
        return "day"
    if (end - start) > timedelta(hours=_HOUR_BUCKET_CAP_HOURS):
        return "day"
    return "hour"


def tz_sql_modifier(tz_offset_minutes: int) -> str:
    """Build a SQLite ``strftime`` timezone modifier like ``'-08:00'``.

    ``-480`` → ``'-08:00'``, ``+330`` → ``'+05:30'``, ``0`` → ``'+00:00'``.
    Requires SQLite >= 3.42 at the call site for the modifier to apply.
    """
    sign = "-" if tz_offset_minutes < 0 else "+"
    total = abs(tz_offset_minutes)
    hh, mm = divmod(total, 60)
    return f"{sign}{hh:02d}:{mm:02d}"
