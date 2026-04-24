"""Tests for cliproxy_usage_server.db — read-only opener and range helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cliproxy_usage_server.db import open_ro, range_window


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


def test_range_window_24h() -> None:
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start, end = range_window("24h", now)
    assert start == datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)
    assert end == now


def test_range_window_all() -> None:
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start, end = range_window("all", now)
    assert start is None
    assert end == now


def test_range_window_invalid() -> None:
    now = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        range_window("foo", now)
