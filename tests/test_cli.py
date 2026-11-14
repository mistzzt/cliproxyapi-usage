"""Integration-style tests for cli.main() with injected queue data."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from functools import partial
from typing import Any

import httpx
import pytest
from conftest import db_at_version, forbidden_queue_client, read_version

from cliproxy_usage_collect.cli import main
from cliproxy_usage_collect.config import Config
from cliproxy_usage_collect.queue_client import (
    AuthError,
    TransientError,
    pop_usage_records,
)
from cliproxy_usage_collect.schema_version import SCHEMA_VERSION

QueueRecord = dict[str, Any]


def _queue_payload(
    timestamp: str, *, model: str = "claude-sonnet-4-5", unclassified: int = 0
) -> QueueRecord:
    return {
        "timestamp": timestamp,
        "api_key": "api-key-1",
        "model": model,
        "source": "codex",
        "auth_index": "0",
        "latency_ms": 1234,
        "failed": False,
        "token_breakdown": {
            "schema_version": 2,
            "quality": "unclassified" if unclassified else "complete",
            "total_tokens": 34 + unclassified,
            "input": {
                "total_tokens": 14,
                "uncached_tokens": 10,
                "cache_read_tokens": 4,
                "cache_write_tokens": 0,
            },
            "output": {
                "total_tokens": 20,
                "non_reasoning_tokens": 17,
                "reasoning_tokens": 3,
            },
            "unclassified_tokens": unclassified,
        },
    }


def _queue_client(records: list[QueueRecord]):
    def pop_usage_records(cfg: Config) -> list[QueueRecord]:
        assert cfg.queue_pop_count == 500
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
        ("2026-04-22T19:22:30.001Z", "claude-sonnet-4-5", 34),
        ("2026-04-22T19:22:31.001Z", "gpt-5", 34),
    ]

    captured = capsys.readouterr()
    assert captured.err == (
        "inserted 2 new records from 2 queued records, 0 without a token split\n"
    )


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
    assert captured.err.endswith(
        "inserted 0 new records from 2 queued records, 0 without a token split\n"
    )


def test_empty_queue_returns_success(env_vars, capsys):
    result = main(queue_client=_queue_client([]))

    assert result == 0
    captured = capsys.readouterr()
    assert captured.err == (
        "inserted 0 new records from 0 queued records, 0 without a token split\n"
    )


def test_missing_management_key(monkeypatch: pytest.MonkeyPatch, tmp_path, capsys):
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "u.db"))

    result = main(queue_client=_queue_client([]))

    assert result == 2
    captured = capsys.readouterr()
    assert captured.err


def test_queue_auth_error_returns_3(env_vars, capsys):
    def queue_client(cfg: Config) -> list[QueueRecord]:
        raise AuthError("nope")

    result = main(queue_client=queue_client)

    assert result == 3
    captured = capsys.readouterr()
    assert captured.err


def test_queue_transient_error_returns_1(env_vars, capsys):
    def queue_client(cfg: Config) -> list[QueueRecord]:
        raise TransientError("usage queue unavailable")

    result = main(queue_client=queue_client)

    assert result == 1
    captured = capsys.readouterr()
    assert captured.err


def test_malformed_queued_payload_returns_4(env_vars, capsys):
    result = main(queue_client=_queue_client([{"timestamp": "only"}]))

    assert result == 4
    captured = capsys.readouterr()
    assert captured.err


def test_queue_client_is_not_called_after_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "u.db"))

    assert main(queue_client=forbidden_queue_client) == 2


def test_db_from_a_newer_version_exits_2_before_popping_the_queue(env_vars, capsys):
    db_at_version(env_vars, SCHEMA_VERSION + 1)

    assert main(queue_client=forbidden_queue_client) == 2
    assert f"schema version {SCHEMA_VERSION + 1}" in capsys.readouterr().err
    assert read_version(env_vars) == SCHEMA_VERSION + 1


def test_unreadable_db_exits_1_before_popping_the_queue(env_vars, capsys):
    env_vars.write_text("not a database, but long enough to fail the header check")

    assert main(queue_client=forbidden_queue_client) == 1
    assert capsys.readouterr().err.startswith("Database error: ")


def test_summary_counts_records_without_a_token_split(env_vars, capsys):
    records = [
        _queue_payload("2026-04-22T19:22:30.001Z", unclassified=3),
        _queue_payload("2026-04-22T19:22:31.001Z"),
    ]

    assert main(queue_client=_queue_client(records)) == 0

    conn = sqlite3.connect(env_vars)
    rows = conn.execute(
        "SELECT input_tokens, output_tokens, total_tokens FROM requests "
        "ORDER BY timestamp"
    ).fetchall()
    conn.close()
    assert rows == [(0, 0, 37), (14, 20, 34)]
    captured = capsys.readouterr()
    assert captured.err == (
        "inserted 2 new records from 2 queued records, 1 without a token split\n"
    )


def _proxy(respond: Callable[[httpx.Request], httpx.Response]):
    """Real queue client against a fake proxy that must never pop the queue."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/usage-statistics-enabled")
        return respond(request)

    def factory(*, timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    return partial(pop_usage_records, http_client_factory=factory)


def test_old_proxy_exits_2_before_popping_the_queue(env_vars, capsys):
    client = _proxy(lambda r: httpx.Response(200, headers={"X-CPA-VERSION": "v7.2.96"}))

    assert main(queue_client=client) == 2
    err = capsys.readouterr().err
    assert "v7.2.96" in err and "7.2.97" in err and "upgrade CLIProxyAPI" in err


@pytest.mark.parametrize("status_code", [401, 403])
def test_probe_auth_failure_exits_3(env_vars, status_code: int):
    client = _proxy(
        lambda r: httpx.Response(status_code, headers={"X-CPA-VERSION": "v8.0.72"})
    )

    assert main(queue_client=client) == 3


def test_probe_connection_error_exits_1(env_vars):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert main(queue_client=_proxy(refuse)) == 1
