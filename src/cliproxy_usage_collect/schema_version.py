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


def supported_version(conn: sqlite3.Connection, path: Path) -> int:
    """Return the DB's version; raise ``SchemaVersionError`` if it is too new."""
    version = user_version(conn)
    if version > SCHEMA_VERSION:
        raise SchemaVersionError(
            mismatch_message(path, version, "upgrade the collector")
        )
    return version


@contextmanager
def write_lock(conn: sqlite3.Connection, path: Path) -> Iterator[int]:
    """Hold the write lock for the block and yield the re-read version.

    Raises ``SchemaVersionError`` for a newer DB; commits on success, else rolls back.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield supported_version(conn, path)
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
