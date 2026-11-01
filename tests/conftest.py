import json
import pathlib
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.parser import iter_records
from cliproxy_usage_collect.schemas import RequestRecord
from cliproxy_usage_server.aggregate import Filters, Window, query_summary
from cliproxy_usage_server.pricing import ModelPricing

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def usage_export_json() -> pathlib.Path:
    """Path to the sample usage-export JSON fixture."""
    return (
        pathlib.Path(__file__).parent
        / "fixtures"
        / "usage-export-2026-04-23T05-01-45-283Z.json"
    )


def usage_export_queue_elements(export: dict[str, Any]) -> Iterable[str]:
    """Flatten an old usage export fixture into queue JSON payloads."""
    for api_key, api_stats in export["usage"]["apis"].items():
        for model, model_stats in api_stats["models"].items():
            for detail in model_stats["details"]:
                yield json.dumps(
                    {
                        **detail,
                        "api_key": api_key,
                        "model": model,
                    }
                )


@pytest.fixture
def seeded_db_path(
    tmp_path: pathlib.Path, usage_export_json: pathlib.Path
) -> pathlib.Path:
    """Temp SQLite DB pre-loaded with records from the JSON export fixture."""
    export = json.loads(usage_export_json.read_text())
    records = list(iter_records(usage_export_queue_elements(export)))
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


@pytest.fixture
def codex_weekly_only_api_call_fixture() -> dict:  # type: ignore[type-arg]
    """Current Codex payload with weekly windows in primary positions."""
    return json.loads((_FIXTURES / "codex-weekly-only-api-call.json").read_text())


# (timestamp, api_key, model, source, input, output, cached). The last five
# straddle the 2026-11-01 fall-back in America/Chicago; sk-a/b-alice redact alike.
UsageRow = tuple[str, str, str, str, int, int, int]
SYNTHETIC_ROWS: list[UsageRow] = [
    ("2026-10-29T15:00:00Z", "sk-a-bob", "gpt-t", "sk-c-secret", 200, 100, 50),
    ("2026-10-31T05:30:00Z", "sk-a-alice", "claude-t", "al@x.io", 1000, 200, 100),
    ("2026-11-01T04:30:00Z", "sk-b-alice", "claude-t", "al@x.io", 2000, 400, 500),
    ("2026-11-01T06:30:00Z", "sk-a-bob", "gpt-t", "sk-c-secret", 4000, 1000, 1000),
    ("2026-11-02T05:30:00Z", "sk-a-alice", "gpt-t", "sk-c-secret", 600, 60, 100),
    ("2026-11-02T06:30:00Z", "sk-a-bob", "claude-t", "al@x.io", 700, 70, 0),
]

# gpt-t uses OpenAI cached-in-input accounting.
SYNTHETIC_PRICING: dict[str, ModelPricing] = {
    "claude-t": ModelPricing(
        input_cost_per_token=1e-6,
        output_cost_per_token=5e-6,
        cache_read_input_token_cost=1e-7,
    ),
    "gpt-t": ModelPricing(
        litellm_provider="openai",
        input_cost_per_token=2e-6,
        output_cost_per_token=8e-6,
        cache_read_input_token_cost=2e-7,
    ),
}


def _records(rows: Iterable[UsageRow]) -> list[RequestRecord]:
    return [
        RequestRecord(
            timestamp=ts,
            api_key=key,
            model=model,
            source=source,
            auth_index="0",
            latency_ms=0,
            input_tokens=inp,
            output_tokens=out,
            reasoning_tokens=0,
            cached_tokens=cac,
            total_tokens=inp + out + cac,
            failed=False,
        )
        for ts, key, model, source, inp, out, cac in rows
    ]


def write_usage_db(db_path: Path, rows: Iterable[UsageRow]) -> Path:
    conn = open_db(db_path)
    insert_records(conn, _records(rows))
    conn.close()
    return db_path


def request_cost(pricing: ModelPricing, inp: int, out: int, cached: int) -> float:
    """Cost of one request with these raw DB counts, priced by the production query."""
    conn = open_db(Path(":memory:"))
    insert_records(
        conn, _records([("2026-10-01T00:00:00Z", "k", "m", "s", inp, out, cached)])
    )
    window = Window(None, datetime(2100, 1, 1, tzinfo=UTC))
    summary = query_summary(
        conn, window, None, ZoneInfo("UTC"), Filters(), {"m": pricing}
    )
    conn.close()
    assert summary.current.cost is not None
    return summary.current.cost


@pytest.fixture
def synthetic_db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return write_usage_db(tmp_path / "synthetic.db", SYNTHETIC_ROWS)
