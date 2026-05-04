import json
import pathlib
import sqlite3
from pathlib import Path

import pytest

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.parser import iter_records

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def usage_export_json() -> pathlib.Path:
    """Path to the sample usage-export JSON fixture."""
    return (
        pathlib.Path(__file__).parent
        / "fixtures"
        / "usage-export-2026-04-23T05-01-45-283Z.json"
    )


@pytest.fixture
def seeded_db_path(
    tmp_path: pathlib.Path, usage_export_json: pathlib.Path
) -> pathlib.Path:
    """Temp SQLite DB pre-loaded with records from the JSON export fixture."""
    export = json.loads(usage_export_json.read_text())
    records = list(iter_records(export))
    db_path = tmp_path / "test_usage.db"
    conn: sqlite3.Connection = open_db(db_path)
    insert_records(conn, records)
    conn.close()
    return db_path


@pytest.fixture
def claude_api_call_fixture() -> dict:  # type: ignore[type-arg]
    """Raw /v0/management/api-call envelope for Claude's /oauth/usage."""
    return json.loads((_FIXTURES / "claude-api-call.json").read_text())


@pytest.fixture
def codex_api_call_fixture() -> dict:  # type: ignore[type-arg]
    """Raw /v0/management/api-call envelope for Codex's /wham/usage."""
    return json.loads((_FIXTURES / "codex-api-call.json").read_text())
