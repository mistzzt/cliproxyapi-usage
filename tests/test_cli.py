"""Integration-style tests for cli.main() with injected queue data."""

from __future__ import annotations

import json
import sqlite3

import pytest

from cliproxy_usage_collect.cli import main
from cliproxy_usage_collect.config import Config
from cliproxy_usage_collect.queue_client import AuthError, TransientError


def _queue_payload(timestamp: str, *, model: str = "claude-sonnet-4-5") -> str:
    return json.dumps(
        {
            "timestamp": timestamp,
            "api_key": "api-key-1",
            "model": model,
            "source": "codex",
            "auth_index": "0",
            "latency_ms": 1234,
            "failed": False,
            "tokens": {
                "input_tokens": 10,
                "output_tokens": 20,
                "reasoning_tokens": 3,
                "cached_tokens": 4,
                "total_tokens": 37,
            },
        }
    )


def _queue_client(records: list[str]):
    def pop_usage_records(cfg: Config) -> list[str]:
        assert cfg.queue_key == "queue"
        return records

    return pop_usage_records


@pytest.fixture
def env_vars(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Set default env vars pointing at a temp DB; returns db path."""
    db = tmp_path / "u.db"
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "test-key")
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://localhost:8317")
    monkeypatch.setenv("USAGE_DB_PATH", str(db))
    return db


def test_happy_path_inserts_all_valid_queued_records(env_vars, capsys):
    records = [
        _queue_payload("2026-04-22T19:22:30.001Z"),
        _queue_payload("2026-04-22T19:22:31.001Z", model="gpt-5"),
    ]

    result = main(queue_client=_queue_client(records))

    assert result == 0

    conn = sqlite3.connect(env_vars)
    rows = conn.execute(
        "SELECT timestamp, model, total_tokens FROM requests ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert rows == [
        ("2026-04-22T19:22:30.001Z", "claude-sonnet-4-5", 37),
        ("2026-04-22T19:22:31.001Z", "gpt-5", 37),
    ]

    captured = capsys.readouterr()
    assert captured.err == "inserted 2 new records from 2 queued records\n"


def test_second_run_with_duplicate_timestamps_inserts_zero(env_vars, capsys):
    records = [
        _queue_payload("2026-04-22T19:22:30.001Z"),
        _queue_payload("2026-04-22T19:22:31.001Z"),
    ]

    assert main(queue_client=_queue_client(records)) == 0
    result = main(queue_client=_queue_client(records))

    assert result == 0

    conn = sqlite3.connect(env_vars)
    (row_count,) = conn.execute("SELECT COUNT(*) FROM requests").fetchone()
    conn.close()
    assert row_count == 2

    captured = capsys.readouterr()
    assert captured.err.endswith("inserted 0 new records from 2 queued records\n")


def test_empty_queue_returns_success(env_vars, capsys):
    result = main(queue_client=_queue_client([]))

    assert result == 0
    captured = capsys.readouterr()
    assert captured.err == "inserted 0 new records from 0 queued records\n"


def test_missing_management_key(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys):
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "u.db"))

    result = main(queue_client=_queue_client([]))

    assert result == 2
    captured = capsys.readouterr()
    assert captured.err


def test_queue_auth_error_returns_3(env_vars, capsys):
    def queue_client(cfg: Config) -> list[str]:
        raise AuthError("nope")

    result = main(queue_client=queue_client)

    assert result == 3
    captured = capsys.readouterr()
    assert captured.err


def test_queue_transient_error_returns_1(env_vars, capsys):
    def queue_client(cfg: Config) -> list[str]:
        raise TransientError("redis unavailable")

    result = main(queue_client=queue_client)

    assert result == 1
    captured = capsys.readouterr()
    assert captured.err


def test_malformed_queued_payload_returns_4(env_vars, capsys):
    result = main(queue_client=_queue_client([json.dumps({"timestamp": "only"})]))

    assert result == 4
    captured = capsys.readouterr()
    assert captured.err


def test_queue_client_is_not_called_after_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "u.db"))

    def queue_client(cfg: Config) -> list[str]:
        raise AssertionError("queue client should not be called")

    assert main(queue_client=queue_client) == 2
