"""Tests for cliproxy_usage_server.db: read-only opener and bucket coarsening."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cliproxy_usage_server.db import effective_bucket, open_ro


def test_open_ro_read_only_rejects_write(seeded_db_path: Path) -> None:
    conn = open_ro(seeded_db_path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute(
            "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ts", "key", "model", "src", "auth", 0, 0, 0, 0, 0, 0, 0),
        )
    conn.close()


def test_open_ro_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "nonexistent.db"
    with pytest.raises(FileNotFoundError):
        open_ro(missing)


def test_explicit_hour_coarsens_past_1000_buckets() -> None:
    start = datetime(2026, 1, 1, 6, tzinfo=UTC)
    chicago = ZoneInfo("America/Chicago")
    assert (
        effective_bucket(start, start + timedelta(hours=1000), "hour", chicago)
        == "hour"
    )
    assert (
        effective_bucket(start, start + timedelta(hours=1001), "hour", chicago) == "day"
    )
