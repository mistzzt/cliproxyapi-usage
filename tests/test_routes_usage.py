"""Tests for usage routes: overview, timeseries, token-breakdown, per-group stats."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime

import pytest
from conftest import usage_export_queue_elements
from fastapi.testclient import TestClient

from cliproxy_usage_collect.db import insert_records, open_db
from cliproxy_usage_collect.parser import iter_records
from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.main import create_app
from cliproxy_usage_server.pricing import (
    ModelPricing,
    TokenCounts,
    compute_cost,
    resolve,
)
from cliproxy_usage_server.redact import redact_key
from cliproxy_usage_server.schemas import (
    HealthResponse,
    ModelsResponse,
    TimeseriesResponse,
    TokenBreakdownResponse,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "usage-export-2026-04-23T05-01-45-283Z.json"
)


def _load_records() -> list:
    export = json.loads(_FIXTURE.read_text())
    return list(iter_records(usage_export_queue_elements(export)))


@pytest.fixture()
def seeded_db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Seeded SQLite DB populated from the fixture JSON."""
    records = _load_records()
    db_path = tmp_path / "usage.db"
    conn: sqlite3.Connection = open_db(db_path)
    insert_records(conn, records)
    conn.close()
    return db_path


@pytest.fixture()
def app_no_pricing(seeded_db_path: pathlib.Path):
    """App with empty pricing (no cost resolution)."""
    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    return create_app(cfg, pricing_provider=lambda: {})


@pytest.fixture()
def client_no_pricing(app_no_pricing):
    with TestClient(app_no_pricing) as c:
        yield c


def _make_stub_pricing(model: str, rate: float) -> dict[str, ModelPricing]:
    """Single-model pricing stub: rate is input_cost_per_token."""
    return {model: ModelPricing(input_cost_per_token=rate, output_cost_per_token=rate)}


# ---------------------------------------------------------------------------
# Test 2: overview totals match seed
# ---------------------------------------------------------------------------


def test_overview_totals_match_seed(client_no_pricing) -> None:
    """Totals in overview must equal the fixture-derived values."""
    records = _load_records()
    expected_requests = len(records)
    expected_tokens = sum(r.total_tokens for r in records)

    resp = client_no_pricing.get("/api/overview?range=all")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    totals = body["totals"]
    assert totals["requests"] == expected_requests
    assert totals["tokens"] == expected_tokens


# ---------------------------------------------------------------------------
# Test 3: overview sparkline length
# ---------------------------------------------------------------------------


def test_overview_sparkline_length(client_no_pricing) -> None:
    """range=24h → each sparkline has 24 or 25 entries (hour buckets).

    The aggregate helper floors start to the nearest hour, which can yield 24
    or 25 labels depending on the sub-hour offset of ``now``.  The spec says
    "24 buckets for hour-based ranges" as a target; we allow ±1 to account for
    the flooring behaviour without coupling the test to wall-clock alignment.
    """
    resp = client_no_pricing.get("/api/overview?range=24h")
    assert resp.status_code == 200, resp.text
    sparklines = resp.json()["sparklines"]
    for key in ("requests", "tokens", "rpm", "tpm", "cost"):
        series = sparklines[key]
        assert 24 <= len(series) <= 25, (
            f"{key!r}: expected 24-25 entries (hour-bucket floor), got {len(series)}"
        )


# ---------------------------------------------------------------------------
# Test 4: timeseries all-models shape
# ---------------------------------------------------------------------------


def test_timeseries_all_models_shape(client_no_pricing) -> None:
    """No models filter → single '__all__' series; buckets and series aligned."""
    resp = client_no_pricing.get("/api/timeseries?range=all&bucket=day&metric=requests")
    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())
    assert "__all__" in body.series
    assert len(body.buckets) == len(body.series["__all__"])


# ---------------------------------------------------------------------------
# Test 5: timeseries filtered models
# ---------------------------------------------------------------------------


def test_timeseries_filtered_models(client_no_pricing) -> None:
    """Explicit model filter → response keys equal exactly the requested models."""
    records = _load_records()
    distinct = sorted({r.model for r in records})
    # Take 2 known models
    m1, m2 = distinct[0], distinct[1]

    resp = client_no_pricing.get(
        f"/api/timeseries?range=all&bucket=day&metric=tokens&models={m1},{m2}",
    )
    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())
    assert set(body.series.keys()) == {m1, m2}
    # All series must align with buckets
    for key, vals in body.series.items():
        assert len(vals) == len(body.buckets), f"{key}: length mismatch"


# ---------------------------------------------------------------------------
# Test 6: timeseries cost metric uses pricing
# ---------------------------------------------------------------------------


def test_timeseries_cost_metric_uses_pricing(
    seeded_db_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """With a known per-model rate, cost values match compute_cost with real split."""
    records = _load_records()
    distinct = sorted({r.model for r in records})
    model = distinct[0]
    rate = 2e-6  # $ per token — applied equally to input and output

    pricing = _make_stub_pricing(model, rate)
    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing)

    with TestClient(app) as client:
        cost_resp = client.get(
            f"/api/timeseries?range=all&bucket=day&metric=cost&models={model}",
        )
        assert cost_resp.status_code == 200
        cost_vals = cost_resp.json()["series"][model]
        buckets = cost_resp.json()["buckets"]

    # Independently compute expected cost per bucket using the real three-way split.
    expected_costs = _compute_expected_bucket_costs(records, pricing, "day")
    assert len(cost_vals) == len(buckets)
    for lbl, actual in zip(buckets, cost_vals, strict=True):
        expected = expected_costs.get(lbl, 0.0)
        assert abs(actual - expected) < 1e-12, (
            f"bucket {lbl}: expected {expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Test 7: timeseries cost with no pricing → 0.0 values
# ---------------------------------------------------------------------------


def test_timeseries_cost_no_pricing_returns_nulls(client_no_pricing) -> None:
    """Empty pricing → cost series values are all 0.0 (not None)."""
    records = _load_records()
    distinct = sorted({r.model for r in records})
    model = distinct[0]

    resp = client_no_pricing.get(
        f"/api/timeseries?range=all&bucket=day&metric=cost&models={model}",
    )
    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())
    vals = body.series[model]
    assert all(v == 0.0 for v in vals), f"Expected all 0.0, got: {vals}"


# ---------------------------------------------------------------------------
# Test 7b: timeseries cost all-mode + top_n uses real per-bucket costs
# ---------------------------------------------------------------------------


def _load_pricing_fixture() -> dict[str, ModelPricing]:
    """Load the litellm pricing fixture used by the test suite."""
    fixture_path = (
        pathlib.Path(__file__).parent / "fixtures" / "litellm-pricing-fixture.json"
    )
    raw = json.loads(fixture_path.read_text())
    return {k: ModelPricing.model_validate(v) for k, v in raw.items()}


def _compute_expected_bucket_costs(
    records: list,
    pricing: dict[str, ModelPricing],
    bucket: str,
) -> dict[str, float]:
    """Independently compute expected per-bucket total cost from raw records.

    Returns {bucket_label: total_cost} where bucket_label matches the SQLite
    strftime format used by the aggregate queries.  Timestamps are normalised
    to UTC before bucketing to mirror the SQLite strftime behaviour.
    """
    # Bucket label format mirrors aggregate._bucket_fmt
    if bucket == "hour":

        def _label(ts: str) -> str:
            dt = datetime.fromisoformat(ts).astimezone(UTC)
            return dt.strftime("%Y-%m-%dT%H:00:00Z")
    else:

        def _label(ts: str) -> str:
            dt = datetime.fromisoformat(ts).astimezone(UTC)
            return dt.strftime("%Y-%m-%d")

    # Accumulate (bucket, model) → {input, output, cached}
    sums: dict[tuple[str, str], dict[str, int]] = defaultdict(
        lambda: {"input": 0, "output": 0, "cached": 0}
    )
    for r in records:
        lbl = _label(r.timestamp)
        key: tuple[str, str] = (lbl, r.model)
        sums[key]["input"] += r.input_tokens
        sums[key]["output"] += r.output_tokens
        sums[key]["cached"] += r.cached_tokens

    # Compute cost per (bucket, model), then sum per bucket
    bucket_costs: dict[str, float] = defaultdict(float)
    for (lbl, model), tok in sums.items():
        entry, _ = resolve(model, pricing)
        if entry is None:
            continue
        tc: TokenCounts = {
            "input_tokens": tok["input"],
            "output_tokens": tok["output"],
            "cache_read_input_tokens": tok["cached"],
        }
        bucket_costs[lbl] += compute_cost(tc, entry)

    return dict(bucket_costs)


def test_timeseries_cost_allmode_top_n_real_cost(seeded_db_path: pathlib.Path) -> None:
    """top_n mode: __all__ must equal per-model bucket costs summed across all models.

    Asserts:
    - Response contains __all__ plus per-model keys (up to top_n).
    - __all__ values are non-zero for buckets that have priced activity.
    - __all__ matches independently-computed bucket costs from fixture records.
    """
    records = _load_records()
    pricing = _load_pricing_fixture()

    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing)

    with TestClient(app) as client:
        resp = client.get(
            "/api/timeseries?range=all&bucket=hour&metric=cost&top_n=8",
        )

    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())

    # Must have __all__ plus at least one per-model series
    assert "__all__" in body.series, "Missing __all__ series"
    assert len(body.series) > 1, "Expected __all__ + per-model series"

    # All series must align with buckets
    for key, vals in body.series.items():
        assert len(vals) == len(body.buckets), f"{key}: length mismatch"

    # __all__ must be non-zero in at least one bucket (fixture has priced models)
    all_vals = body.series["__all__"]
    assert any(v > 0.0 for v in all_vals), (
        "__all__ is all zeros — cost not being computed"
    )

    # Independently compute expected costs and compare
    expected_costs = _compute_expected_bucket_costs(records, pricing, "hour")

    for lbl, actual in zip(body.buckets, all_vals, strict=True):
        expected = expected_costs.get(lbl, 0.0)
        assert abs(actual - expected) < 1e-10, (
            f"bucket {lbl}: expected {expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Test 7c: timeseries cost all-mode, no top_n — defensive path returns real cost
# ---------------------------------------------------------------------------


def test_timeseries_cost_allmode_no_topn_returns_real_cost(
    seeded_db_path: pathlib.Path,
) -> None:
    """metric=cost + no models + no top_n → only __all__ key with real non-zero costs.

    This is the formerly-broken path (Task 7).  After Task 5's fix, the
    endpoint must return real costs rather than zeros.
    Asserts:
    - Response series has exactly one key: "__all__".
    - "__all__" values are non-zero for buckets with priced activity.
    - "__all__" values match independently-computed per-bucket cost sums.
    """
    records = _load_records()
    pricing = _load_pricing_fixture()

    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing)

    with TestClient(app) as client:
        resp = client.get(
            "/api/timeseries?range=all&bucket=hour&metric=cost",
        )

    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())

    # Must have only __all__ — no per-model keys
    assert set(body.series.keys()) == {"__all__"}, (
        f"Expected only __all__, got: {set(body.series.keys())}"
    )

    # All series must align with buckets
    assert len(body.series["__all__"]) == len(body.buckets)

    # __all__ must be non-zero in at least one bucket (fixture has priced models)
    all_vals = body.series["__all__"]
    assert any(v > 0.0 for v in all_vals), (
        "__all__ is all zeros — real cost not being computed"
    )

    # Independently compute expected per-bucket costs and compare
    expected_costs = _compute_expected_bucket_costs(records, pricing, "hour")
    for lbl, actual in zip(body.buckets, all_vals, strict=True):
        expected = expected_costs.get(lbl, 0.0)
        assert abs(actual - expected) < 1e-10, (
            f"bucket {lbl}: expected {expected}, got {actual}"
        )


# ---------------------------------------------------------------------------
# Test 8: token-breakdown sums match fixture totals
# ---------------------------------------------------------------------------


def test_token_breakdown_sums_match(client_no_pricing) -> None:
    """Sum of each breakdown series must equal fixture totals."""
    records = _load_records()
    expected_input = sum(r.input_tokens for r in records)
    expected_output = sum(r.output_tokens for r in records)
    expected_cached = sum(r.cached_tokens for r in records)
    expected_reasoning = sum(r.reasoning_tokens for r in records)

    resp = client_no_pricing.get("/api/token-breakdown?range=all&bucket=day")
    assert resp.status_code == 200, resp.text
    body = TokenBreakdownResponse.model_validate(resp.json())
    assert sum(body.input) == expected_input
    assert sum(body.output) == expected_output
    assert sum(body.cached) == expected_cached
    assert sum(body.reasoning) == expected_reasoning


# ---------------------------------------------------------------------------
# Test 9: invalid range → 422
# ---------------------------------------------------------------------------


def test_range_invalid_returns_422(client_no_pricing) -> None:
    """An unrecognised range value must return 422 Unprocessable Entity."""
    resp = client_no_pricing.get("/api/overview?range=foo")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 10: api-stats rows and totals
# ---------------------------------------------------------------------------


def test_api_stats_rows_and_totals(client_no_pricing) -> None:
    """Row count equals distinct api_keys; sum of requests equals overall total."""
    records = _load_records()
    expected_total_requests = len(records)
    expected_row_count = len({r.api_key for r in records})

    resp = client_no_pricing.get("/api/api-stats?range=all")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == expected_row_count
    assert sum(r["requests"] for r in rows) == expected_total_requests


# ---------------------------------------------------------------------------
# Test 11: model-stats cost with pricing
# ---------------------------------------------------------------------------


def test_model_stats_cost_with_pricing(
    seeded_db_path: pathlib.Path,
) -> None:
    """Given stub pricing for one model, cost equals hand-computed value."""
    records = _load_records()
    # Pick gemini-2.5-flash — largest model in fixture
    model = "gemini-2.5-flash"
    rate_in = 1e-6
    rate_out = 3e-6
    rate_cache_read = 0.5e-6

    pricing = {
        model: ModelPricing(
            input_cost_per_token=rate_in,
            output_cost_per_token=rate_out,
            cache_read_input_token_cost=rate_cache_read,
        )
    }
    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing)

    with TestClient(app) as client:
        resp = client.get("/api/model-stats?range=all")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    # Find the row for our model
    row = next((r for r in rows if r["model"] == model), None)
    assert row is not None, f"No row for {model!r}"

    # Compute expected cost from fixture-derived sums
    model_records = [r for r in records if r.model == model]
    inp = sum(r.input_tokens for r in model_records)
    out = sum(r.output_tokens for r in model_records)
    cached = sum(r.cached_tokens for r in model_records)
    tc: TokenCounts = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": cached,
    }
    expected_cost = compute_cost(tc, pricing[model])

    assert row["cost"] is not None
    assert abs(row["cost"] - expected_cost) < 1e-12, (
        f"cost mismatch: expected {expected_cost}, got {row['cost']}"
    )


# ---------------------------------------------------------------------------
# Test 12: model-stats cost null without pricing
# ---------------------------------------------------------------------------


def test_model_stats_cost_null_without_pricing(client_no_pricing) -> None:
    """Empty pricing → every model row has cost=None."""
    resp = client_no_pricing.get("/api/model-stats?range=all")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) > 0
    for row in rows:
        assert row["cost"] is None, (
            f"Expected None for {row['model']}, got {row['cost']}"
        )


# ---------------------------------------------------------------------------
# Test 13: credential-stats groups by source
# ---------------------------------------------------------------------------


def test_credential_stats_groups_by_source(
    client_no_pricing,
) -> None:
    """Distinct source count matches fixture; auth_index not exposed."""
    records = _load_records()
    expected_distinct = len({r.source for r in records})

    resp = client_no_pricing.get("/api/credential-stats?range=all")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == expected_distinct
    # Verify each row has the expected keys and auth_index is absent.
    for row in rows:
        assert "source" in row
        assert "auth_index" not in row
        assert "requests" in row
        assert "total_tokens" in row
        assert "failed" in row
        assert "cost" in row


# ---------------------------------------------------------------------------
# Test 15: health percentile ordering
# ---------------------------------------------------------------------------


def test_health_percentile_order(client_no_pricing) -> None:
    """p50 <= p95 <= p99 must hold for any non-empty range."""
    resp = client_no_pricing.get("/api/health?range=all")
    assert resp.status_code == 200, resp.text
    body = HealthResponse.model_validate(resp.json())
    assert body.latency.p50 <= body.latency.p95 <= body.latency.p99


# ---------------------------------------------------------------------------
# Test 16: health empty range
# ---------------------------------------------------------------------------


def test_health_empty_range(seeded_db_path: pathlib.Path) -> None:
    """A range with no rows returns zeros for all fields."""
    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: {})
    with TestClient(app) as client:
        # 7h range is very unlikely to contain fixture data (from 2026-04-23)
        resp = client.get("/api/health?range=7h")
    assert resp.status_code == 200, resp.text
    body = HealthResponse.model_validate(resp.json())
    assert body.total_requests == 0
    assert body.failed == 0
    assert body.failed_rate == 0.0
    assert body.latency.p50 == 0.0
    assert body.latency.p95 == 0.0
    assert body.latency.p99 == 0.0


# ---------------------------------------------------------------------------
# Test 21: models sorted
# ---------------------------------------------------------------------------


def test_models_sorted(client_no_pricing) -> None:
    """Returned model list is lexicographically sorted and matches fixture models."""
    records = _load_records()
    expected = sorted({r.model for r in records})

    resp = client_no_pricing.get("/api/models")
    assert resp.status_code == 200, resp.text
    body = ModelsResponse.model_validate(resp.json())
    assert body.models == expected


# ---------------------------------------------------------------------------
# Task 6: models= filter on all /api endpoints
# ---------------------------------------------------------------------------


def _first_model() -> str:
    """Return the lexicographically first model in the fixture."""
    records = _load_records()
    return sorted({r.model for r in records})[0]


def test_overview_models_filter_reduces_totals(client_no_pricing) -> None:
    """models=<single> narrows overview totals to that model only."""
    records = _load_records()
    model = _first_model()

    # Baseline (all models)
    base = client_no_pricing.get("/api/overview?range=all")
    assert base.status_code == 200, base.text
    base_requests = base.json()["totals"]["requests"]

    # Filtered
    resp = client_no_pricing.get(f"/api/overview?range=all&models={model}")
    assert resp.status_code == 200, resp.text
    filtered_requests = resp.json()["totals"]["requests"]

    expected = len([r for r in records if r.model == model])
    assert filtered_requests == expected
    assert filtered_requests < base_requests


def test_token_breakdown_models_filter_reduces_totals(client_no_pricing) -> None:
    """models=<single> narrows token-breakdown sums to that model only."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(
        f"/api/token-breakdown?range=all&bucket=day&models={model}"
    )
    assert resp.status_code == 200, resp.text
    body = TokenBreakdownResponse.model_validate(resp.json())

    expected_input = sum(r.input_tokens for r in records if r.model == model)
    assert sum(body.input) == expected_input

    # Must be strictly less than unfiltered total (fixture has multiple models)
    full_resp = client_no_pricing.get("/api/token-breakdown?range=all&bucket=day")
    full_body = TokenBreakdownResponse.model_validate(full_resp.json())
    assert sum(body.input) < sum(full_body.input)


def test_api_stats_models_filter_restricts_rows(client_no_pricing) -> None:
    """models=<single> returns only api_keys that have activity with that model."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/api-stats?range=all&models={model}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    # api_key values in the response are redacted — compare after redacting fixture keys
    expected_api_keys = {redact_key(r.api_key) for r in records if r.model == model}
    returned_api_keys = {row["api_key"] for row in rows}
    assert returned_api_keys == expected_api_keys

    # Filtered row count must be ≤ baseline
    base = client_no_pricing.get("/api/api-stats?range=all")
    assert len(rows) <= len(base.json())


def test_model_stats_models_filter_returns_exactly_one(client_no_pricing) -> None:
    """models=<single> returns exactly one row for that model."""
    model = _first_model()

    resp = client_no_pricing.get(f"/api/model-stats?range=all&models={model}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["model"] == model


def test_credential_stats_models_filter_restricts_rows(client_no_pricing) -> None:
    """models=<single> returns at most as many credential rows as unfiltered."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/credential-stats?range=all&models={model}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    expected_credentials = {r.source for r in records if r.model == model}
    returned_credentials = {row["source"] for row in rows}
    assert returned_credentials == expected_credentials

    base = client_no_pricing.get("/api/credential-stats?range=all")
    assert len(rows) <= len(base.json())


def test_health_models_filter_reduces_totals(client_no_pricing) -> None:
    """models=<single> reduces total_requests in health response."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/health?range=all&models={model}")
    assert resp.status_code == 200, resp.text
    body = HealthResponse.model_validate(resp.json())

    expected_total = len([r for r in records if r.model == model])
    assert body.total_requests == expected_total

    base = client_no_pricing.get("/api/health?range=all")
    base_body = HealthResponse.model_validate(base.json())
    assert body.total_requests < base_body.total_requests


def test_api_keys_endpoint_sorted(client_no_pricing) -> None:
    """/api/api-keys returns distinct redacted keys, sorted."""
    records = _load_records()
    expected = sorted({redact_key(r.api_key) for r in records})

    resp = client_no_pricing.get("/api/api-keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["api_keys"] == expected


def test_overview_api_keys_filter_reduces_totals(client_no_pricing) -> None:
    """api_keys=<redacted> narrows overview totals to that key only."""
    records = _load_records()
    key = records[0].api_key
    redacted = redact_key(key)

    base = client_no_pricing.get("/api/overview?range=all")
    assert base.status_code == 200
    base_requests = base.json()["totals"]["requests"]

    resp = client_no_pricing.get(f"/api/overview?range=all&api_keys={redacted}")
    assert resp.status_code == 200, resp.text
    filtered_requests = resp.json()["totals"]["requests"]

    expected = sum(1 for r in records if redact_key(r.api_key) == redacted)
    assert filtered_requests == expected
    assert filtered_requests <= base_requests


def test_overview_api_keys_filter_unknown_yields_zero(client_no_pricing) -> None:
    """An unknown redacted form yields zero rows (not a silent passthrough)."""
    resp = client_no_pricing.get("/api/overview?range=all&api_keys=does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["totals"]["requests"] == 0
