"""One-time migration from the ``cached_tokens`` layout to fixed-meaning token columns.

Ships in 0.3.x only and is removed in 0.4.0; the only code that knows the old layout.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

BACKUP_SUFFIX = ".pre-cache-columns"

# Claude rows stored uncached input; cache creation is the rest of their total.
_MIGRATE = """
ALTER TABLE requests RENAME COLUMN cached_tokens TO cache_read_tokens;
ALTER TABLE requests ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0;
UPDATE requests SET
  cache_creation_tokens =
    MAX(0, total_tokens - input_tokens - output_tokens - cache_read_tokens),
  input_tokens = input_tokens + cache_read_tokens
    + MAX(0, total_tokens - input_tokens - output_tokens - cache_read_tokens)
WHERE model GLOB 'claude*';
"""


class MigrationError(Exception):
    """Raised when an old-layout DB could not be migrated; the DB is unchanged."""


def _is_old_layout(conn: sqlite3.Connection) -> bool:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(requests)")}
    return "cached_tokens" in columns


def migrate(conn: sqlite3.Connection, path: Path) -> None:
    """Migrate the ``requests`` table if it has the old layout, backing it up first.

    The backup is a consistent copy at ``<path>.pre-cache-columns``.
    """
    if not _is_old_layout(conn):
        return
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        backup.unlink(missing_ok=True)
        conn.execute("VACUUM INTO ?", (str(backup),))
        conn.execute("BEGIN IMMEDIATE")
        try:
            if _is_old_layout(conn):
                for statement in _MIGRATE.split(";"):
                    if statement.strip():
                        conn.execute(statement)
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    except (sqlite3.Error, OSError) as exc:
        msg = f"could not migrate {path}: {exc}"
        raise MigrationError(msg) from exc
