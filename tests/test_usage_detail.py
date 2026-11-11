"""Drawer detail figures (cost by type, long context, cache savings) and CSV export.

Single requests go through the production query; amounts are hand-checked.
"""

from __future__ import annotations

import csv
import io
import pathlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from conftest import SYNTHETIC_PRICING, request_totals
from fastapi.testclient import TestClient
from test_pricing_ccusage import CLAUDE, GPT_LONG

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.schemas import RequestRecord
from cliproxy_usage_server.aggregate import Filters, Window, query_summary
from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.main import create_app
from cliproxy_usage_server.pricing import ModelPricing
from cliproxy_usage_server.schemas import Totals

FLAT = ModelPricing(
    input_cost_per_token=3e-6,
    output_cost_per_token=15e-6,
    cache_read_input_token_cost=3e-7,
)


def _detail(t: Totals) -> tuple[object, ...]:
    assert t.cost_by_type is not None
    by_type = t.cost_by_type
    return (
        pytest.approx((by_type.input, by_type.output, by_type.cache_read)),
        pytest.approx(t.long_context_cost),
        t.long_context_requests,
        pytest.approx(t.cache_savings),
        t.uncached_input_tokens,
        t.cache_read_tokens,
    )


@pytest.mark.parametrize(
    ("pricing", "inp", "out", "cached", "expected"),
    [
        # Marginal 200k: only the 50k input excess bills at 6e-6.
        (CLAUDE, 250_000, 1_000, 50_000, ((0.9, 0.015, 0.015), 0.3, 1, 0.135)),
        # Marginal 200k: 300k combined context, but no single type crosses 200k.
        (CLAUDE, 150_000, 1_000, 150_000, ((0.45, 0.015, 0.045), 0.0, 0, 0.405)),
        # OpenAI 272k: raw input includes the 100k cached; whole request at long rates.
        (GPT_LONG, 272_001, 2_000, 100_000, ((1.72001, 0.09, 0.1), 1.91001, 1, 0.9)),
        # 272k-only rates, non-OpenAI accounting: input + cached passes 272k.
        (
            GPT_LONG.model_copy(update={"litellm_provider": "xai"}),
            200_000,
            0,
            100_000,
            ((2.0, 0.0, 0.1), 2.1, 1, 0.9),
        ),
        # No long tier: tokens past 200k are not long-context spend.
        (FLAT, 300_000, 0, 0, ((0.9, 0.0, 0.0), 0.0, 0, 0.0)),
    ],
)
def test_single_request_detail(
    pricing: ModelPricing,
    inp: int,
    out: int,
    cached: int,
    expected: tuple[tuple[float, float, float], float, int, float],
) -> None:
    t = request_totals(pricing, inp, out, cached)
    by_type, long_cost, long_requests, savings = expected
    openai = pricing.litellm_provider == "openai"
    cache_read = min(cached, inp) if openai else cached
    assert _detail(t) == (
        by_type,
        long_cost,
        long_requests,
        savings,
        inp - cache_read if openai else inp,
        cache_read,
    )
    assert t.cost == pytest.approx(sum(by_type))


def _record(ts: str, *, inp: int, latency: int, failed: bool) -> RequestRecord:
    return RequestRecord(
        timestamp=ts,
        api_key="k",
        model="m",
        source="s",
        auth_index="0",
        latency_ms=latency,
        input_tokens=inp,
        output_tokens=0,
        reasoning_tokens=0,
        cached_tokens=0,
        total_tokens=inp,
        failed=failed,
    )


def test_failed_requests_excluded_from_latency_and_long_context() -> None:
    conn = open_db(pathlib.Path(":memory:"))
    insert_records(
        conn,
        [
            _record("2026-10-01T00:00:00Z", inp=250_000, latency=3_000, failed=False),
            _record("2026-10-01T01:00:00Z", inp=250_000, latency=50, failed=True),
            _record("2026-10-02T00:00:00Z", inp=10, latency=1_000, failed=False),
        ],
    )
    window = Window(
        datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 3, tzinfo=UTC)
    )
    s = query_summary(conn, window, "day", ZoneInfo("UTC"), Filters(), {"m": CLAUDE})
    conn.close()
    assert (s.current.requests, s.current.failed) == (3, 1)
    assert s.current.latency_ms == 4_000
    assert s.current.long_context_requests == 1
    assert s.series["failed"] == [1, 0]
    assert s.series["latency_ms"] == [3_000, 1_000]


def test_unpriced_detail_is_none() -> None:
    conn = open_db(pathlib.Path(":memory:"))
    insert_records(
        conn, [_record("2026-10-01T00:00:00Z", inp=10, latency=0, failed=False)]
    )
    window = Window(None, datetime(2100, 1, 1, tzinfo=UTC))
    s = query_summary(conn, window, None, ZoneInfo("UTC"), Filters(), {})
    conn.close()
    cur = s.current
    assert (cur.cost, cur.cost_by_type, cur.long_context_cost, cur.cache_savings) == (
        None,
        None,
        None,
        None,
    )


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

Q = (
    "start=2026-10-31T00:00:00-05:00&end=2026-11-03T00:00:00-06:00"
    "&tz=America/Chicago&bucket=day"
)


def _client(db: pathlib.Path, pricing: dict[str, ModelPricing]) -> TestClient:
    cfg = ServerConfig(db_path=db)  # pyright: ignore[reportCallIssue]
    return TestClient(create_app(cfg, pricing_provider=lambda: pricing))


def _export(db: pathlib.Path, pricing: dict[str, ModelPricing]) -> list[dict[str, str]]:
    with _client(db, pricing) as c:
        resp = c.get(f"/api/usage/export?{Q}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    return list(csv.DictReader(io.StringIO(resp.text)))


def test_export_rows_match_breakdown(synthetic_db_path: pathlib.Path) -> None:
    rows = _export(synthetic_db_path, SYNTHETIC_PRICING)
    with _client(synthetic_db_path, SYNTHETIC_PRICING) as c:
        body = c.get(f"/api/usage/breakdown?dim=person&{Q}").json()
    assert list(rows[0]) == [
        "bucket_start",
        "person",
        "person_label",
        "model",
        "credential",
        "requests",
        "failed",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "cost",
    ]
    # Five requests; alice's two on Oct 31 share a row (her keys redact alike).
    assert len(rows) == 4
    assert rows[0]["bucket_start"] == "2026-10-31T00:00:00-05:00"
    for row in body["rows"]:
        mine = [r for r in rows if r["person"] == row["key"]]
        assert sum(int(r["requests"]) for r in mine) == row["current"]["requests"]
        assert sum(float(r["cost"]) for r in mine) == pytest.approx(
            row["current"]["cost"]
        )


def test_export_redacts_and_blanks_unpriced(synthetic_db_path: pathlib.Path) -> None:
    rows = _export(synthetic_db_path, {"claude-t": SYNTHETIC_PRICING["claude-t"]})
    text = repr(rows)
    for raw in ("sk-a-bob", "sk-a-alice", "sk-b-alice", "sk-c-secret"):
        assert raw not in text
    assert {r["model"]: r["cost"] == "" for r in rows} == {
        "claude-t": False,
        "gpt-t": True,
    }
