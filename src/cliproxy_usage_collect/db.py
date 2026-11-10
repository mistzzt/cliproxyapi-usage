"""SQLite persistence layer for cliproxy usage records."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from cliproxy_usage_collect.migration import migrate
from cliproxy_usage_collect.schemas import RequestRecord

_DDL = """
CREATE TABLE IF NOT EXISTS requests (
  timestamp             TEXT    PRIMARY KEY,
  api_key               TEXT    NOT NULL,
  model                 TEXT    NOT NULL,
  source                TEXT    NOT NULL,
  auth_index            TEXT    NOT NULL,
  latency_ms            INTEGER NOT NULL,
  input_tokens          INTEGER NOT NULL,  -- uncached + cache read + cache creation
  output_tokens         INTEGER NOT NULL,  -- all output, reasoning included
  reasoning_tokens      INTEGER NOT NULL,  -- part of output_tokens
  cache_read_tokens     INTEGER NOT NULL,  -- part of input_tokens
  cache_creation_tokens INTEGER NOT NULL DEFAULT 0,  -- part of input_tokens
  total_tokens          INTEGER NOT NULL,  -- upstream's reported total; never read
  failed                INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_source    ON requests(source);
CREATE INDEX IF NOT EXISTS idx_requests_model     ON requests(model);
CREATE INDEX IF NOT EXISTS idx_requests_api_key   ON requests(api_key);
CREATE INDEX IF NOT EXISTS idx_requests_source_ts ON requests(source, timestamp);
CREATE INDEX IF NOT EXISTS idx_requests_ts_utc    ON requests(datetime(timestamp));
"""

_COLUMNS = (
    "timestamp",
    "api_key",
    "model",
    "source",
    "auth_index",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "total_tokens",
    "failed",
)

_INSERT = (
    f"INSERT OR IGNORE INTO requests ({', '.join(_COLUMNS)}) "
    f"VALUES ({', '.join('?' * len(_COLUMNS))})"
)


def open_db(path: Path) -> sqlite3.Connection:
    """Open the DB at *path*, migrate or create its schema, return the connection.

    Raises ``MigrationError`` when an old-layout DB cannot be migrated.
    """
    conn = sqlite3.connect(path)
    try:
        migrate(conn, path)
    except BaseException:
        conn.close()
        raise
    conn.executescript(_DDL)
    return conn


def insert_records(conn: sqlite3.Connection, records: Iterable[RequestRecord]) -> int:
    """Insert *records* into the DB; skip duplicates (INSERT OR IGNORE on timestamp PK).

    Returns the number of genuinely new rows inserted.
    """
    rows = [
        (
            rec.timestamp,
            rec.api_key,
            rec.model,
            rec.source,
            rec.auth_index,
            rec.latency_ms,
            rec.input_tokens,
            rec.output_tokens,
            rec.reasoning_tokens,
            rec.cache_read_tokens,
            rec.cache_creation_tokens,
            rec.total_tokens,
            int(rec.failed),
        )
        for rec in records
    ]
    before = conn.total_changes
    with conn:
        conn.executemany(_INSERT, rows)
    return conn.total_changes - before
