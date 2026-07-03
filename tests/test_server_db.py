"""Tests for cliproxy_usage_server.db — read-only opener and range helpers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cliproxy_usage_server.db import (
    bucket_for_span,
    coarsen_bucket,
    open_ro,
    tz_sql_modifier,
)


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


# ---------------------------------------------------------------------------
# bucket_for_span
# ---------------------------------------------------------------------------


def test_bucket_for_span_open_start_is_day() -> None:
    """Open-ended start (all-time) has no measurable span → day buckets."""
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    assert bucket_for_span(None, end) == "day"


def test_bucket_for_span_short_window_is_hour() -> None:
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start = end - timedelta(hours=24)
    assert bucket_for_span(start, end) == "hour"


def test_bucket_for_span_48h_boundary_inclusive_is_hour() -> None:
    """A span of exactly 48h stays on hour buckets."""
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start = end - timedelta(hours=48)
    assert bucket_for_span(start, end) == "hour"


def test_bucket_for_span_just_past_48h_is_day() -> None:
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start = end - timedelta(hours=48, minutes=1)
    assert bucket_for_span(start, end) == "day"


# ---------------------------------------------------------------------------
# coarsen_bucket
# ---------------------------------------------------------------------------


def test_coarsen_bucket_day_passthrough() -> None:
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start = end - timedelta(days=365)
    assert coarsen_bucket(start, end, "day") == "day"


def test_coarsen_bucket_open_start_hour_becomes_day() -> None:
    """All-time hourly is the worst runaway → always coarsened to day."""
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    assert coarsen_bucket(None, end, "hour") == "day"


def test_coarsen_bucket_hour_within_cap_stays_hour() -> None:
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start = end - timedelta(days=10)  # 240h, at the cap
    assert coarsen_bucket(start, end, "hour") == "hour"


def test_coarsen_bucket_hour_past_cap_becomes_day() -> None:
    end = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
    start = end - timedelta(days=11)  # 264h, past the ~240h cap
    assert coarsen_bucket(start, end, "hour") == "day"


# ---------------------------------------------------------------------------
# tz_sql_modifier
# ---------------------------------------------------------------------------


def test_tz_sql_modifier_zero() -> None:
    assert tz_sql_modifier(0) == "+00:00"


def test_tz_sql_modifier_negative_whole_hours() -> None:
    assert tz_sql_modifier(-480) == "-08:00"


def test_tz_sql_modifier_positive_half_hour() -> None:
    assert tz_sql_modifier(330) == "+05:30"
