"""Integration-style tests for cli.main() with mocked HTTP transport."""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from cliproxy_usage.cli import main


def _mock_transport(body: bytes, status: int = 200):
    def handler(request):
        return httpx.Response(status, content=body)

    return httpx.MockTransport(handler)


@pytest.fixture
def env_vars(monkeypatch, tmp_path):
    """Set default env vars pointing at a temp DB; returns db path."""
    db = tmp_path / "u.db"
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "test-key")
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://localhost:8317/v0/management")
    monkeypatch.setenv("USAGE_DB_PATH", str(db))
    return db


def test_happy_path(env_vars, usage_export_json, capsys):
    """Happy path: exit 0, 138 rows inserted, stderr mentions count."""
    body = usage_export_json.read_bytes()
    client = httpx.Client(transport=_mock_transport(body))
    try:
        result = main(http_client=client)
    finally:
        client.close()

    assert result == 0

    conn = sqlite3.connect(env_vars)
    (row_count,) = conn.execute("SELECT COUNT(*) FROM requests").fetchone()
    conn.close()
    assert row_count == 138

    captured = capsys.readouterr()
    assert "138" in captured.err


def test_happy_path_second_run_inserts_zero(env_vars, usage_export_json, capsys):
    """Second run with same data: exit 0, 0 new rows inserted."""
    body = usage_export_json.read_bytes()

    client1 = httpx.Client(transport=_mock_transport(body))
    try:
        assert main(http_client=client1) == 0
    finally:
        client1.close()

    client2 = httpx.Client(transport=_mock_transport(body))
    try:
        result = main(http_client=client2)
    finally:
        client2.close()

    assert result == 0

    captured = capsys.readouterr()
    assert "0 new records" in captured.err


def test_missing_management_key(monkeypatch, tmp_path, usage_export_json, capsys):
    """Missing CLIPROXY_MANAGEMENT_KEY -> exit 2."""
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.setenv("USAGE_DB_PATH", str(tmp_path / "u.db"))

    result = main()

    assert result == 2
    captured = capsys.readouterr()
    assert captured.err  # some message on stderr


def test_401_response(env_vars, capsys):
    """401 response -> exit 3."""
    client = httpx.Client(
        transport=_mock_transport(b'{"error": "unauthorized"}', status=401)
    )
    try:
        result = main(http_client=client)
    finally:
        client.close()

    assert result == 3
    captured = capsys.readouterr()
    assert captured.err


def test_500_response(env_vars, capsys):
    """500 response -> exit 1."""
    client = httpx.Client(
        transport=_mock_transport(b'{"error": "server error"}', status=500)
    )
    try:
        result = main(http_client=client)
    finally:
        client.close()

    assert result == 1
    captured = capsys.readouterr()
    assert captured.err


def test_malformed_json_schema_error(env_vars, capsys):
    """Malformed JSON missing required fields -> SchemaError -> exit 4."""
    # Valid JSON but missing required detail fields
    bad_body = json.dumps(
        {"usage": {"apis": {"k": {"models": {"m": {"details": [{}]}}}}}}
    ).encode()
    client = httpx.Client(transport=_mock_transport(bad_body))
    try:
        result = main(http_client=client)
    finally:
        client.close()

    assert result == 4
    captured = capsys.readouterr()
    assert captured.err
