"""Read-only DB access and time-range helpers for the usage webapp server."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

_RANGE_DELTAS: dict[str, timedelta | None] = {
    "7h": timedelta(hours=7),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "all": None,
}


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


def range_window(range_: str, now: datetime) -> tuple[datetime | None, datetime]:
    """Map a range string to a ``(start, end)`` pair relative to *now*.

    Supported values: ``"7h"``, ``"24h"``, ``"7d"``, ``"all"``.

    ``"all"`` returns ``(None, now)``.
    Any other value raises ``ValueError``.
    """
    if range_ not in _RANGE_DELTAS:
        valid = list(_RANGE_DELTAS)
        raise ValueError(f"invalid range {range_!r}; must be one of {valid}")
    delta = _RANGE_DELTAS[range_]
    start = None if delta is None else now - delta
    return start, now
