"""Tests for the one-time ``cached_tokens`` migration; deleted with it in 0.4.0."""

from __future__ import annotations

import sqlite3
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest
from conftest import forbidden_queue_client, read_version

from cliproxy_usage_collect import db as db_module
from cliproxy_usage_collect.cli import main
from cliproxy_usage_collect.db import open_db
from cliproxy_usage_collect.migration import BACKUP_SUFFIX, MigrationError
from cliproxy_usage_collect.schema_version import supported_version, user_version

# The 0.2.0 layout, with token columns in its order and meanings.
OLD_DDL = """
CREATE TABLE requests (
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
CREATE INDEX idx_requests_model  ON requests(model);
CREATE INDEX idx_requests_ts_utc ON requests(datetime(timestamp));
"""

# (timestamp, model, input, output, cached, total) in the old meanings.
OLD_ROWS = [
    # Claude after v7.1.26: the total includes 1000 cache reads and 300 creation.
    ("2026-10-01T00:00:00Z", "claude-sonnet-4-5", 100, 50, 1000, 1450),
    # Older Claude row: total_tokens = input + output.
    ("2026-05-01T00:00:00Z", "claude-opus-4-1", 100, 50, 1000, 150),
    # GPT already stores all input, cache reads included.
    ("2026-10-02T00:00:00Z", "gpt-5", 2000, 100, 500, 2100),
    # A GPT row reporting more cache reads than input gets them capped at input.
    ("2026-10-03T00:00:00Z", "gpt-5", 300, 10, 900, 310),
]

COLUMNS = (
    "timestamp, model, input_tokens, output_tokens, reasoning_tokens, "
    "cache_read_tokens, cache_creation_tokens, total_tokens"
)


def _old_db(path: Path) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(OLD_DDL)
    conn.executemany(
        "INSERT INTO requests VALUES (?, 'k', ?, 's', '0', 1, ?, ?, 2, ?, ?, 0)",
        OLD_ROWS,
    )
    conn.commit()
    conn.close()
    return path


def _rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return conn.execute(f"SELECT {COLUMNS} FROM requests ORDER BY timestamp").fetchall()


def _layout(conn: sqlite3.Connection) -> set[tuple[Any, ...]]:
    # (name, type, notnull, default, pk), without the column position.
    return {row[1:] for row in conn.execute("PRAGMA table_info(requests)")}


def test_module_is_removed_in_0_4_0() -> None:
    major, minor = (int(part) for part in version("cliproxy-usage").split(".")[:2])
    assert (major, minor) < (0, 4), "delete migration.py and this test file"


def test_migration_rewrites_claude_rows_only(tmp_path: Path) -> None:
    conn = open_db(_old_db(tmp_path / "usage.db"))

    assert _rows(conn) == [
        ("2026-05-01T00:00:00Z", "claude-opus-4-1", 1100, 50, 2, 1000, 0, 150),
        ("2026-10-01T00:00:00Z", "claude-sonnet-4-5", 1400, 50, 2, 1000, 300, 1450),
        ("2026-10-02T00:00:00Z", "gpt-5", 2000, 100, 2, 500, 0, 2100),
        ("2026-10-03T00:00:00Z", "gpt-5", 300, 10, 2, 300, 0, 310),
    ]
    new_columns = "cache_creation_1h_tokens, service_tier, ttft_ms"
    assert conn.execute(f"SELECT DISTINCT {new_columns} FROM requests").fetchall() == [
        (0, None, None)
    ]
    conn.close()


def test_migration_writes_a_backup_of_the_old_db(tmp_path: Path) -> None:
    db = _old_db(tmp_path / "usage.db")
    backup = tmp_path / f"usage.db{BACKUP_SUFFIX}"
    backup.write_text("an earlier backup")

    open_db(db).close()

    old = sqlite3.connect(backup)
    rows = old.execute(
        "SELECT timestamp, model, input_tokens, output_tokens, cached_tokens, "
        "total_tokens FROM requests ORDER BY timestamp"
    ).fetchall()
    old.close()
    assert rows == sorted(OLD_ROWS)


def _backup_columns(backup: Path) -> set[str]:
    conn = sqlite3.connect(backup)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(requests)")}
    conn.close()
    return columns


def test_run_that_loses_the_race_keeps_the_first_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _old_db(tmp_path / "usage.db")
    raced = False

    def read_then_race(conn: sqlite3.Connection, path: Path) -> int:
        # After this run's first read, another run migrates before it takes the lock.
        nonlocal raced
        version = supported_version(conn, path)
        if not raced:
            raced = True
            open_db(db).close()
        return version

    monkeypatch.setattr(db_module, "supported_version", read_then_race)
    conn = open_db(db)
    rows = _rows(conn)
    conn.close()

    assert raced
    assert "cached_tokens" in _backup_columns(tmp_path / f"usage.db{BACKUP_SUFFIX}")
    assert rows[0] == (
        "2026-05-01T00:00:00Z",
        "claude-opus-4-1",
        1100,
        50,
        2,
        1000,
        0,
        150,
    )
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "usage.db",
        f"usage.db{BACKUP_SUFFIX}",
    ]


def test_second_open_changes_nothing(tmp_path: Path) -> None:
    db = _old_db(tmp_path / "usage.db")
    backup = tmp_path / f"usage.db{BACKUP_SUFFIX}"
    conn = open_db(db)
    migrated = _rows(conn)
    conn.close()
    backup_mtime = backup.stat().st_mtime_ns

    conn = open_db(db)
    assert _rows(conn) == migrated
    conn.close()
    assert backup.stat().st_mtime_ns == backup_mtime


def test_migrated_layout_matches_a_fresh_db(tmp_path: Path) -> None:
    migrated = open_db(_old_db(tmp_path / "old.db"))
    fresh = open_db(tmp_path / "fresh.db")

    assert _layout(migrated) == _layout(fresh)
    assert user_version(migrated) == user_version(fresh) == 1
    migrated.close()
    fresh.close()


def test_locked_db_is_left_unchanged(tmp_path: Path) -> None:
    db = _old_db(tmp_path / "usage.db")
    holder = sqlite3.connect(db)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(MigrationError):
            open_db(db)
    finally:
        holder.rollback()
        holder.close()

    conn = sqlite3.connect(db)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(requests)")}
    conn.close()
    assert "cached_tokens" in columns
    assert read_version(db) == 0


def test_migration_failure_exits_1_before_popping_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = _old_db(tmp_path / "usage.db")
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "test-key")
    monkeypatch.setenv("USAGE_DB_PATH", str(db))
    # A directory where the backup goes makes the migration fail.
    (tmp_path / f"usage.db{BACKUP_SUFFIX}").mkdir()

    assert main(queue_client=forbidden_queue_client) == 1
    assert "Migration error" in capsys.readouterr().err
    assert read_version(db) == 0
