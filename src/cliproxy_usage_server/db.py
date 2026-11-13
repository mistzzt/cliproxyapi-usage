"""Read-only DB access and viewer-local time-bucket helpers for the usage server.

SQL aggregates into 15-minute UTC slices; Python assigns slices to buckets that
follow the viewer's IANA timezone (DST included). Day buckets run local
midnight to midnight and week buckets start Monday 00:00 local.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

Bucket = Literal["hour", "day", "week"]

# Slice width of the SQL grouping; fine enough for half- and quarter-hour zones.
SLICE_SECONDS = 900

# Explicit granularities coarsen to the next unit beyond this many buckets.
MAX_BUCKETS = 1000

_AUTO_HOUR_MAX = timedelta(days=2)
_AUTO_DAY_MAX = timedelta(days=120)
_COARSER: dict[Bucket, Bucket] = {"hour": "day", "day": "week"}


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


def auto_bucket(start: datetime, end: datetime) -> Bucket:
    """Pick hour up to 2 days, day up to 120 days, week beyond."""
    span = end - start
    if span <= _AUTO_HOUR_MAX:
        return "hour"
    if span <= _AUTO_DAY_MAX:
        return "day"
    return "week"


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=tz)


def floor_to_bucket(instant: datetime, bucket: Bucket, tz: ZoneInfo) -> datetime:
    """Return the start (aware, in *tz*) of the bucket containing *instant*."""
    local = instant.astimezone(tz)
    if bucket == "hour":
        return local.replace(minute=0, second=0, microsecond=0)
    day = local.date()
    if bucket == "week":
        day -= timedelta(days=day.weekday())
    return _local_midnight(day, tz)


def _next_bucket(cur: datetime, bucket: Bucket, tz: ZoneInfo) -> datetime:
    if bucket == "hour":
        # Step in absolute time so the repeated fall-back hour stays distinct.
        return (cur.astimezone(UTC) + timedelta(hours=1)).astimezone(tz)
    days = 7 if bucket == "week" else 1
    return _local_midnight(cur.date() + timedelta(days=days), tz)


def bucket_starts(
    start: datetime,
    end: datetime,
    bucket: Bucket,
    tz: ZoneInfo,
    limit: int | None = None,
) -> list[datetime]:
    """Dense bucket starts from the bucket containing *start* up to *end* (exclusive).

    Stops early after *limit* entries when given.
    """
    out: list[datetime] = []
    cur = floor_to_bucket(start, bucket, tz)
    while cur < end and (limit is None or len(out) < limit):
        out.append(cur)
        cur = _next_bucket(cur, bucket, tz)
    return out


def effective_bucket(
    start: datetime,
    end: datetime,
    requested: Bucket | None,
    tz: ZoneInfo,
) -> Bucket:
    """Resolve auto (``None``) or coarsen an explicit bucket past ``MAX_BUCKETS``."""
    bucket = auto_bucket(start, end) if requested is None else requested
    while bucket in _COARSER:
        if len(bucket_starts(start, end, bucket, tz, limit=MAX_BUCKETS + 1)) <= (
            MAX_BUCKETS
        ):
            break
        bucket = _COARSER[bucket]
    return bucket


class SliceIndexer:
    """Map 15-minute UTC slice numbers to positions in a dense bucket list."""

    def __init__(self, buckets: list[datetime]) -> None:
        self._edges = [int(b.timestamp()) for b in buckets]
        self._cache: dict[int, int | None] = {}

    def __call__(self, slice_no: int | None) -> int | None:
        """Return the bucket position for *slice_no*, or None if outside the axis."""
        if slice_no is None:
            return None
        if slice_no in self._cache:
            return self._cache[slice_no]
        pos = bisect_right(self._edges, slice_no * SLICE_SECONDS) - 1
        result = pos if pos >= 0 else None
        self._cache[slice_no] = result
        return result
