"""Schema version of ``usage.db``, kept in SQLite's ``PRAGMA user_version``."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
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


def has_requests_table(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
    ).fetchone()
    return row is not None


def execute_statements(conn: sqlite3.Connection, script: str) -> None:
    """Run *script* inside the open transaction; ``executescript`` would commit it.

    Each statement must end at a line end.
    """
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""


def locked_step(
    conn: sqlite3.Connection,
    path: Path,
    when: Callable[[int, bool], bool],
    apply: Callable[[], None],
    to_version: int,
) -> None:
    """Under the write lock, run *apply* and set *to_version* if *when* still holds.

    *when* gets the re-read version and whether the ``requests`` table exists.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if when(supported_version(conn, path), has_requests_table(conn)):
            apply()
            conn.execute(f"PRAGMA user_version = {to_version}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
