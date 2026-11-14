"""One-time migration from the ``cached_tokens`` layout to fixed-meaning token columns.

Ships in 0.3.x only and is removed in 0.4.0; the only code that knows the old layout.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from cliproxy_usage_collect.schema_version import write_transaction

BACKUP_SUFFIX = ".pre-cache-columns"

# Claude rows stored uncached input; cache creation is the rest of their total.
# Other rows already stored all input; capping cache reads keeps uncached input >= 0.
_MIGRATE = (
    "ALTER TABLE requests RENAME COLUMN cached_tokens TO cache_read_tokens",
    "ALTER TABLE requests ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE requests ADD COLUMN cache_creation_1h_tokens "
    "INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE requests ADD COLUMN service_tier TEXT",
    "ALTER TABLE requests ADD COLUMN ttft_ms INTEGER",
    """
UPDATE requests SET
  cache_creation_tokens =
    MAX(0, total_tokens - input_tokens - output_tokens - cache_read_tokens),
  input_tokens = input_tokens + cache_read_tokens
    + MAX(0, total_tokens - input_tokens - output_tokens - cache_read_tokens)
WHERE model GLOB 'claude*'
""",
    """
UPDATE requests SET cache_read_tokens = MIN(cache_read_tokens, input_tokens)
WHERE NOT model GLOB 'claude*'
""",
)


class MigrationError(Exception):
    """Raised when a version-0 DB could not be migrated; the DB is unchanged."""


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def migrate(conn: sqlite3.Connection, path: Path) -> None:
    """Migrate a version-0 ``requests`` table, backing it up first.

    The backup is ``<path>.pre-cache-columns``; one from an earlier failed run is kept.
    """
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    try:
        if not backup.exists():
            conn.execute("VACUUM INTO ?", (str(backup),))
            # VACUUM INTO does not sync its output; make the backup durable first.
            _fsync(backup)
            _fsync(path.parent)
        with write_transaction(conn):
            for statement in _MIGRATE:
                conn.execute(statement)
            conn.execute("PRAGMA user_version = 1")
    except (sqlite3.Error, OSError) as exc:
        msg = f"could not migrate {path}: {exc}"
        raise MigrationError(msg) from exc
