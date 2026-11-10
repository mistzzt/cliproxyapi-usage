"""Writes a synthetic usage.db and pricing.json for the browser tests (via `uv run`).

Timestamps are relative to now, so every range the tests pick has data."""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.schemas import RequestRecord

KEYS = ["sk-e2e-alpha", "sk-e2e-bravo", "sk-e2e-charlie"]
MODELS = ["claude-e2e", "gpt-e2e"]
SOURCES = ["one@example.test", "two@example.test"]
PRICING = {
    "claude-e2e": {"input_cost_per_token": 3e-6, "output_cost_per_token": 15e-6},
    "gpt-e2e": {
        "litellm_provider": "openai",
        "input_cost_per_token": 2e-6,
        "output_cost_per_token": 8e-6,
    },
}


def records(now: datetime) -> list[RequestRecord]:
    """One request every 3 hours for 120 days, cycling keys, models, and sources."""
    out = []
    for i in range(120 * 8):
        ts = now - timedelta(hours=3 * i, minutes=7)
        out.append(
            RequestRecord(
                timestamp=ts.isoformat().replace("+00:00", "Z"),
                api_key=KEYS[i % len(KEYS)],
                model=MODELS[i % len(MODELS)],
                source=SOURCES[i % len(SOURCES)],
                auth_index="0",
                latency_ms=200 + i % 50,
                input_tokens=1000 + 10 * (i % 30),
                output_tokens=200 + i % 40,
                reasoning_tokens=0,
                cache_read_tokens=100,
                cache_creation_tokens=50 if i % 2 == 0 else 0,
                total_tokens=1200 + 10 * (i % 30) + i % 40,
                failed=i % 17 == 0,
            )
        )
    return out


def main() -> None:
    out_dir = Path(sys.argv[1])
    conn = open_db(out_dir / "usage.db")
    insert_records(conn, records(datetime.now(UTC)))
    conn.close()
    (out_dir / "pricing.json").write_text(json.dumps(PRICING))


if __name__ == "__main__":
    main()
