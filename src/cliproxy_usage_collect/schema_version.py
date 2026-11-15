"""Schema version of ``usage.db``, kept in SQLite's ``PRAGMA user_version``."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# Version of the layout in db.py's DDL; 0 is the pre-0.3.0 ``cached_tokens`` layout.
SCHEMA_VERSION = 1


class SchemaVersionError(Exception):
    """Raised when the DB has a newer schema version than this code supports."""


def user_version(conn: sqlite3.Connection) -> int:
    """Return the DB's schema version (``PRAGMA user_version``)."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    return version


def mismatch_message(path: Path, version: int, action: str) -> str:
    """Describe a DB at *version* instead of ``SCHEMA_VERSION`` and what to do."""
    return (
        f"{path} has schema version {version}, but this version of cliproxy-usage "
        f"needs {SCHEMA_VERSION}; {action}"
    )


@contextmanager
def write_transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Run the block in one write transaction; commit on success, else roll back."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
