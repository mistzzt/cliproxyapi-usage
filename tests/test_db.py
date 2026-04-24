"""Tests for the DB layer (open_db and insert_records)."""

from __future__ import annotations

from pathlib import Path

from cliproxy_usage.db import insert_records, open_db
from cliproxy_usage.schemas import RequestRecord

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_record(timestamp: str, failed: bool = False) -> RequestRecord:
    return RequestRecord(
        timestamp=timestamp,
        api_key="key-a",
        model="claude-3-5-sonnet",
        source="source-x",
        auth_index="0",
        latency_ms=100,
        input_tokens=10,
        output_tokens=20,
        reasoning_tokens=0,
        cached_tokens=5,
        total_tokens=30,
        failed=failed,
    )


# ---------------------------------------------------------------------------
# open_db tests
# ---------------------------------------------------------------------------


def test_open_db_creates_requests_table(tmp_path: Path) -> None:
    """open_db on a fresh path creates the requests table."""
    conn = open_db(tmp_path / "usage.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='requests'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "requests"
    conn.close()


def test_open_db_creates_four_indexes(tmp_path: Path) -> None:
    """open_db creates exactly the 4 named indexes (excluding SQLite internal ones)."""
    conn = open_db(tmp_path / "usage.db")
    rows = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='index' AND tbl_name='requests' "
        "AND name NOT LIKE 'sqlite_autoindex_%'"
    ).fetchall()
    index_names = {row[0] for row in rows}
    expected = {
        "idx_requests_source",
        "idx_requests_model",
        "idx_requests_api_key",
        "idx_requests_source_ts",
    }
    assert index_names == expected
    conn.close()


def test_open_db_is_idempotent(tmp_path: Path) -> None:
    """Calling open_db on an existing DB does not error and preserves rows."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ts-1", "key-a", "model-a", "src", "0", 1, 1, 1, 0, 0, 2, 0),
    )
    conn.commit()
    conn.close()

    conn2 = open_db(db_path)
    count = conn2.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 1
    conn2.close()


# ---------------------------------------------------------------------------
# insert_records tests
# ---------------------------------------------------------------------------


def test_insert_records_returns_count(tmp_path: Path) -> None:
    """insert_records returns N for N distinct records on the first call."""
    conn = open_db(tmp_path / "usage.db")
    records = [make_record(f"2024-01-01T00:00:0{i}") for i in range(3)]
    result = insert_records(conn, records)
    assert result == 3
    conn.close()


def test_insert_records_dedup_returns_zero(tmp_path: Path) -> None:
    """Inserting the same batch twice returns 0 on the second call."""
    conn = open_db(tmp_path / "usage.db")
    records = [make_record(f"2024-01-01T00:00:0{i}") for i in range(3)]
    insert_records(conn, records)
    result = insert_records(conn, records)
    assert result == 0
    conn.close()


def test_insert_records_partial_overlap(tmp_path: Path) -> None:
    """A partially-overlapping second batch inserts only the new rows."""
    conn = open_db(tmp_path / "usage.db")
    first_batch = [make_record(f"2024-01-01T00:00:0{i}") for i in range(3)]
    insert_records(conn, first_batch)

    # Overlap with ts 0,1,2 and add new ts 3,4
    second_batch = [make_record(f"2024-01-01T00:00:0{i}") for i in range(5)]
    result = insert_records(conn, second_batch)
    assert result == 2

    total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert total == 5
    conn.close()


def test_insert_records_failed_bool_round_trip(tmp_path: Path) -> None:
    """failed=True is stored as 1, failed=False as 0."""
    conn = open_db(tmp_path / "usage.db")
    insert_records(
        conn,
        [make_record("ts-true", failed=True), make_record("ts-false", failed=False)],
    )
    rows = conn.execute(
        "SELECT timestamp, failed FROM requests ORDER BY timestamp"
    ).fetchall()
    assert rows == [("ts-false", 0), ("ts-true", 1)]
    conn.close()


def test_insert_records_single_transaction(tmp_path: Path) -> None:
    """All rows in a batch are inserted in a single transaction (no per-row commit)."""
    conn = open_db(tmp_path / "usage.db")
    records = [make_record(f"2024-01-01T00:00:{i:02d}") for i in range(10)]
    before = conn.total_changes
    insert_records(conn, records)
    after = conn.total_changes
    assert after - before == 10
    conn.close()
