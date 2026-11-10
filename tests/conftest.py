import json
import pathlib
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.schemas import RequestRecord
from cliproxy_usage_server.aggregate import Filters, Window, query_summary
from cliproxy_usage_server.pricing import ModelPricing
from cliproxy_usage_server.schemas import Totals

_FIXTURES = Path(__file__).parent / "fixtures"


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


# (timestamp, api_key, model, source, input, output, cache read); input includes
# the cache reads. The last five straddle the 2026-11-01 fall-back in
# America/Chicago; sk-a/b-alice redact alike.
UsageRow = tuple[str, str, str, str, int, int, int]
SYNTHETIC_ROWS: list[UsageRow] = [
    ("2026-10-29T15:00:00Z", "sk-a-bob", "gpt-t", "sk-c-secret", 200, 100, 50),
    ("2026-10-31T05:30:00Z", "sk-a-alice", "claude-t", "al@x.io", 1100, 200, 100),
    ("2026-11-01T04:30:00Z", "sk-b-alice", "claude-t", "al@x.io", 2500, 400, 500),
    ("2026-11-01T06:30:00Z", "sk-a-bob", "gpt-t", "sk-c-secret", 4000, 1000, 1000),
    ("2026-11-02T05:30:00Z", "sk-a-alice", "gpt-t", "sk-c-secret", 600, 60, 100),
    ("2026-11-02T06:30:00Z", "sk-a-bob", "claude-t", "al@x.io", 700, 70, 0),
]

# gpt-t bills like OpenAI models (whole-request long context).
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


def _record(row: UsageRow, cache_creation: int = 0) -> RequestRecord:
    ts, key, model, source, inp, out, cache_read = row
    return RequestRecord(
        timestamp=ts,
        api_key=key,
        model=model,
        source=source,
        auth_index="0",
        latency_ms=0,
        input_tokens=inp,
        output_tokens=out,
        reasoning_tokens=0,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        total_tokens=inp + out,
        failed=False,
    )


def write_usage_db(db_path: Path, rows: Iterable[UsageRow]) -> Path:
    conn = open_db(db_path)
    insert_records(conn, [_record(row) for row in rows])
    conn.close()
    return db_path


def request_totals(
    pricing: ModelPricing, inp: int, out: int, cache_read: int, cache_creation: int
) -> Totals:
    """Totals of one request with these DB columns, from the production query."""
    conn = open_db(Path(":memory:"))
    row = ("2026-10-01T00:00:00Z", "k", "m", "s", inp, out, cache_read)
    insert_records(conn, [_record(row, cache_creation)])
    window = Window(None, datetime(2100, 1, 1, tzinfo=UTC))
    summary = query_summary(
        conn, window, None, ZoneInfo("UTC"), Filters(), {"m": pricing}
    )
    conn.close()
    return summary.current


def request_cost(
    pricing: ModelPricing, inp: int, out: int, cache_read: int, cache_creation: int
) -> float:
    """Cost of one request with these DB columns, priced by the production query."""
    cost = request_totals(pricing, inp, out, cache_read, cache_creation).cost
    assert cost is not None
    return cost


@pytest.fixture
def synthetic_db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return write_usage_db(tmp_path / "synthetic.db", SYNTHETIC_ROWS)
