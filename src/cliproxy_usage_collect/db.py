"""SQLite persistence layer for cliproxy usage records."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from cliproxy_usage_collect.schemas import RequestRecord

_DDL = """
CREATE TABLE IF NOT EXISTS requests (
  timestamp        TEXT    PRIMARY KEY,
  api_key          TEXT    NOT NULL,
  model            TEXT    NOT NULL,
  source           TEXT    NOT NULL,
  auth_index       TEXT    NOT NULL,
  latency_ms       INTEGER NOT NULL,
  input_tokens     INTEGER NOT NULL,
  output_tokens    INTEGER NOT NULL,
  reasoning_tokens INTEGER NOT NULL,
  cached_tokens    INTEGER NOT NULL,
  total_tokens     INTEGER NOT NULL,
  failed           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_source    ON requests(source);
CREATE INDEX IF NOT EXISTS idx_requests_model     ON requests(model);
CREATE INDEX IF NOT EXISTS idx_requests_api_key   ON requests(api_key);
CREATE INDEX IF NOT EXISTS idx_requests_source_ts ON requests(source, timestamp);
"""

_INSERT = """
INSERT OR IGNORE INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
"""


def open_db(path: Path) -> sqlite3.Connection:
    """Open the SQLite DB at *path*, create schema if missing, return the connection."""
    conn = sqlite3.connect(path)
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
            rec.cached_tokens,
            rec.total_tokens,
            int(rec.failed),
        )
        for rec in records
    ]
    before = conn.total_changes
    with conn:
        conn.executemany(_INSERT, rows)
    return conn.total_changes - before
