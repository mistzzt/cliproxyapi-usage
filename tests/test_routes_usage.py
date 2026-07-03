"""Tests for usage routes: overview, timeseries, token-breakdown, per-group stats."""

from __future__ import annotations

import json
import pathlib
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime, timedelta

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

# All fixture rows fall on 2026-04-23 (UTC). A 24h window covering that day
# keeps ``bucket=hour`` requests on hour granularity (under the coarsen cap)
# while still including every seeded record.
_FIXTURE_DAY_WINDOW = "start=2026-04-23T00:00:00Z&end=2026-04-24T00:00:00Z"


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


@pytest.fixture()
def app_with_full_pricing(seeded_db_path: pathlib.Path):
    """App with pricing for every model present in the seed."""
    records = _load_records()
    distinct_models = sorted({r.model for r in records})
    pricing = {
        m: ModelPricing(input_cost_per_token=1e-6, output_cost_per_token=1e-6)
        for m in distinct_models
    }
    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    return create_app(cfg, pricing_provider=lambda: pricing)


@pytest.fixture()
def client_with_full_pricing(app_with_full_pricing):
    with TestClient(app_with_full_pricing) as c:
        yield c


def _make_stub_pricing(model: str, rate: float) -> dict[str, ModelPricing]:
    """Single-model pricing stub: rate is input_cost_per_token."""
    return {model: ModelPricing(input_cost_per_token=rate, output_cost_per_token=rate)}


def _iso_z(dt: datetime) -> str:
    """Render a UTC datetime as a ``Z``-suffixed ISO instant (URL-safe)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _rolling_window(hours: int) -> str:
    """Build a ``start=..&end=..`` query fragment for a rolling window ending now.

    Uses tz-aware (UTC) ``Z``-suffixed instants — the endpoints reject naive
    datetimes, and ``Z`` avoids the ``+`` → space URL-decoding pitfall.
    """
    now = datetime.now(UTC)
    start = now - timedelta(hours=hours)
    return f"start={_iso_z(start)}&end={_iso_z(now)}"


# ---------------------------------------------------------------------------
# Test 2: overview totals match seed
# ---------------------------------------------------------------------------


def test_overview_totals_match_seed(client_no_pricing) -> None:
    """Totals in overview must equal the fixture-derived values."""
    records = _load_records()
    expected_requests = len(records)
    expected_tokens = sum(r.total_tokens for r in records)

    resp = client_no_pricing.get("/api/overview")
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
    resp = client_no_pricing.get(f"/api/overview?{_rolling_window(24)}")
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
    resp = client_no_pricing.get("/api/timeseries?bucket=day&metric=requests")
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
        f"/api/timeseries?bucket=day&metric=tokens&models={m1},{m2}",
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
            f"/api/timeseries?bucket=day&metric=cost&models={model}",
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
        f"/api/timeseries?bucket=day&metric=cost&models={model}",
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
            f"/api/timeseries?{_FIXTURE_DAY_WINDOW}&bucket=hour&metric=cost&top_n=8",
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
            f"/api/timeseries?{_FIXTURE_DAY_WINDOW}&bucket=hour&metric=cost",
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

    resp = client_no_pricing.get("/api/token-breakdown?bucket=day")
    assert resp.status_code == 200, resp.text
    body = TokenBreakdownResponse.model_validate(resp.json())
    assert sum(body.input) == expected_input
    assert sum(body.output) == expected_output
    assert sum(body.cached) == expected_cached
    assert sum(body.reasoning) == expected_reasoning


# ---------------------------------------------------------------------------
# Test 9: window validation → 422
# ---------------------------------------------------------------------------


def test_window_start_after_end_returns_422(client_no_pricing) -> None:
    """start > end is an ordering violation → 422."""
    now = datetime.now(UTC)
    start = _iso_z(now)
    end = _iso_z(now - timedelta(hours=1))
    resp = client_no_pricing.get(f"/api/overview?start={start}&end={end}")
    assert resp.status_code == 422


def test_window_naive_start_returns_422(client_no_pricing) -> None:
    """A naive (offset-less) start datetime is rejected rather than assumed UTC."""
    resp = client_no_pricing.get("/api/overview?start=2026-04-23T00:00:00")
    assert resp.status_code == 422


def test_window_naive_end_returns_422(client_no_pricing) -> None:
    """A naive (offset-less) end datetime is rejected."""
    resp = client_no_pricing.get("/api/overview?end=2026-04-23T00:00:00")
    assert resp.status_code == 422


def test_window_explicit_range_scopes_totals(client_no_pricing) -> None:
    """An explicit [start, end) window restricts totals to rows inside it.

    The fixture spans 2026-04-23; a window fully covering that day returns all
    records, while a disjoint later window returns none.
    """
    records = _load_records()
    covering = "start=2026-04-23T00:00:00Z&end=2026-04-24T00:00:00Z"
    resp = client_no_pricing.get(f"/api/overview?{covering}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["requests"] == len(records)

    disjoint = "start=2026-05-01T00:00:00Z&end=2026-05-02T00:00:00Z"
    resp2 = client_no_pricing.get(f"/api/overview?{disjoint}")
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["totals"]["requests"] == 0


def test_open_start_returns_all_time(client_no_pricing) -> None:
    """Omitting start (open start) returns all-time totals."""
    records = _load_records()
    resp = client_no_pricing.get("/api/overview")
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["requests"] == len(records)


# ---------------------------------------------------------------------------
# Test 10: api-stats rows and totals
# ---------------------------------------------------------------------------


def test_api_stats_rows_and_totals(client_no_pricing) -> None:
    """Row count equals distinct api_keys; sum of requests equals overall total."""
    records = _load_records()
    expected_total_requests = len(records)
    expected_row_count = len({r.api_key for r in records})

    resp = client_no_pricing.get("/api/api-stats")
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
        resp = client.get("/api/model-stats")
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
    resp = client_no_pricing.get("/api/model-stats")
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

    resp = client_no_pricing.get("/api/credential-stats")
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
    resp = client_no_pricing.get("/api/health")
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
        # A 7h window ending now cannot contain fixture data (from 2026-04-23).
        resp = client.get(f"/api/health?{_rolling_window(7)}")
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
    base = client_no_pricing.get("/api/overview")
    assert base.status_code == 200, base.text
    base_requests = base.json()["totals"]["requests"]

    # Filtered
    resp = client_no_pricing.get(f"/api/overview?models={model}")
    assert resp.status_code == 200, resp.text
    filtered_requests = resp.json()["totals"]["requests"]

    expected = len([r for r in records if r.model == model])
    assert filtered_requests == expected
    assert filtered_requests < base_requests


def test_token_breakdown_models_filter_reduces_totals(client_no_pricing) -> None:
    """models=<single> narrows token-breakdown sums to that model only."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/token-breakdown?bucket=day&models={model}")
    assert resp.status_code == 200, resp.text
    body = TokenBreakdownResponse.model_validate(resp.json())

    expected_input = sum(r.input_tokens for r in records if r.model == model)
    assert sum(body.input) == expected_input

    # Must be strictly less than unfiltered total (fixture has multiple models)
    full_resp = client_no_pricing.get("/api/token-breakdown?bucket=day")
    full_body = TokenBreakdownResponse.model_validate(full_resp.json())
    assert sum(body.input) < sum(full_body.input)


def test_api_stats_models_filter_restricts_rows(client_no_pricing) -> None:
    """models=<single> returns only api_keys that have activity with that model."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/api-stats?models={model}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    # api_key values in the response are redacted — compare after redacting fixture keys
    expected_api_keys = {redact_key(r.api_key) for r in records if r.model == model}
    returned_api_keys = {row["api_key"] for row in rows}
    assert returned_api_keys == expected_api_keys

    # Filtered row count must be ≤ baseline
    base = client_no_pricing.get("/api/api-stats")
    assert len(rows) <= len(base.json())


def test_model_stats_models_filter_returns_exactly_one(client_no_pricing) -> None:
    """models=<single> returns exactly one row for that model."""
    model = _first_model()

    resp = client_no_pricing.get(f"/api/model-stats?models={model}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["model"] == model


def test_credential_stats_models_filter_restricts_rows(client_no_pricing) -> None:
    """models=<single> returns at most as many credential rows as unfiltered."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/credential-stats?models={model}")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    from cliproxy_usage_server.redact import redact_source

    expected_credentials = {
        redact_source(r.source) for r in records if r.model == model
    }
    returned_credentials = {row["source"] for row in rows}
    assert returned_credentials == expected_credentials

    base = client_no_pricing.get("/api/credential-stats")
    assert len(rows) <= len(base.json())


def test_health_models_filter_reduces_totals(client_no_pricing) -> None:
    """models=<single> reduces total_requests in health response."""
    records = _load_records()
    model = _first_model()

    resp = client_no_pricing.get(f"/api/health?models={model}")
    assert resp.status_code == 200, resp.text
    body = HealthResponse.model_validate(resp.json())

    expected_total = len([r for r in records if r.model == model])
    assert body.total_requests == expected_total

    base = client_no_pricing.get("/api/health")
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

    base = client_no_pricing.get("/api/overview")
    assert base.status_code == 200
    base_requests = base.json()["totals"]["requests"]

    resp = client_no_pricing.get(f"/api/overview?api_keys={redacted}")
    assert resp.status_code == 200, resp.text
    filtered_requests = resp.json()["totals"]["requests"]

    expected = sum(1 for r in records if redact_key(r.api_key) == redacted)
    assert filtered_requests == expected
    assert filtered_requests <= base_requests


def test_overview_api_keys_filter_unknown_yields_zero(client_no_pricing) -> None:
    """An unknown redacted form yields zero rows (not a silent passthrough)."""
    resp = client_no_pricing.get("/api/overview?api_keys=does-not-exist")
    assert resp.status_code == 200
    assert resp.json()["totals"]["requests"] == 0


def test_codex_cost_split_matches_ccusage_formula(tmp_path: pathlib.Path) -> None:
    """OpenAI-convention rows use pricing metadata, not source prefixes."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-05-01T00:00:00.000000Z",
            "sk-test",
            "gpt-5",
            "openai-account@example.test",
            "0",
            100,
            1000,
            500,
            0,
            200,
            1500,
            0,
        ),
    )
    conn.commit()
    conn.close()

    pricing_map = {
        "gpt-5": ModelPricing(
            litellm_provider="openai",
            input_cost_per_token=1.25e-6,
            output_cost_per_token=1e-5,
            cache_read_input_token_cost=1.25e-7,
        )
    }
    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing_map)

    expected_input = 1000 - 200
    expected = expected_input * 1.25e-6 + 500 * 1e-5 + 200 * 1.25e-7

    with TestClient(app) as client:
        resp = client.get("/api/api-stats")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["cost"] == pytest.approx(expected)


def test_anthropic_cost_unaffected_by_split(tmp_path: pathlib.Path) -> None:
    """Non-OpenAI: cached billed at cache-read rate; input unchanged."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-05-01T00:00:00.000000Z",
            "sk-test",
            "claude-sonnet-4-5",
            "claude-account@example.test",
            "0",
            100,
            1000,
            500,
            0,
            200,
            1500,
            0,
        ),
    )
    conn.commit()
    conn.close()

    pricing_map = {
        "claude-sonnet-4-5": ModelPricing(
            input_cost_per_token=3e-6,
            output_cost_per_token=1.5e-5,
            cache_read_input_token_cost=3e-7,
        )
    }
    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing_map)

    expected = 1000 * 3e-6 + 500 * 1.5e-5 + 200 * 3e-7

    with TestClient(app) as client:
        resp = client.get("/api/api-stats")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert rows[0]["cost"] == pytest.approx(expected)


def test_timeseries_cost_emits_series_status_live(client_with_full_pricing) -> None:
    """When every model in the window has live pricing, series_status is all 'live'."""
    resp = client_with_full_pricing.get("/api/timeseries?bucket=day&metric=cost")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "series_status" in body
    assert body["series_status"]
    assert all(v == "live" for v in body["series_status"].values())


def test_timeseries_cost_emits_series_status_missing(client_no_pricing) -> None:
    """With empty pricing every series is 'missing'."""
    resp = client_no_pricing.get("/api/timeseries?bucket=day&metric=cost")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["series_status"]["__all__"] == "missing"


def test_timeseries_non_cost_metric_has_empty_series_status(
    client_with_full_pricing,
) -> None:
    resp = client_with_full_pricing.get("/api/timeseries?bucket=day&metric=tokens")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["series_status"] == {}


def test_credential_stats_redacts_key_sources(tmp_path: pathlib.Path) -> None:
    """API-key sources get redacted; email sources pass through."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    rows = [
        ("openai:sk-proj-secret-abc12345", "gpt-5"),
        ("codex-reviewer@example.test", "gpt-5"),
        ("anthropic:sk-ant-01-tail9999", "claude-sonnet-4-5"),
    ]
    for i, (source, model) in enumerate(rows):
        conn.execute(
            "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
            "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
            "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"2026-05-01T00:00:0{i}.000000Z",
                "sk-test",
                model,
                source,
                "0",
                100,
                100,
                50,
                0,
                0,
                150,
                0,
            ),
        )
    conn.commit()
    conn.close()

    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: {})

    with TestClient(app) as client:
        resp = client.get("/api/credential-stats")
        assert resp.status_code == 200, resp.text
        sources = {row["source"] for row in resp.json()}
        assert "openai:sk-*******-abc12345" in sources
        assert "codex-reviewer@example.test" in sources
        assert "anthropic:sk-*******-tail9999" in sources
        assert "openai:sk-proj-secret-abc12345" not in sources


# ---------------------------------------------------------------------------
# Bucket-count guard: hour auto-coarsens to day for wide / open windows
# ---------------------------------------------------------------------------


def test_timeseries_hour_autocoarsens_wide_window(client_no_pricing) -> None:
    """bucket=hour over a >10-day window is coarsened to day (effective bucket)."""
    window = "start=2026-04-01T00:00:00Z&end=2026-04-23T00:00:00Z"  # ~22 days
    resp = client_no_pricing.get(
        f"/api/timeseries?{window}&bucket=hour&metric=requests"
    )
    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())
    assert body.bucket == "day"
    # Day granularity keeps the label count small (not 500+ hour points).
    assert len(body.buckets) <= 32


def test_timeseries_hour_open_start_autocoarsens(client_no_pricing) -> None:
    """bucket=hour with an open start (all-time) is always coarsened to day."""
    resp = client_no_pricing.get("/api/timeseries?bucket=hour&metric=requests")
    assert resp.status_code == 200, resp.text
    body = TimeseriesResponse.model_validate(resp.json())
    assert body.bucket == "day"


def test_token_breakdown_hour_autocoarsens_wide_window(client_no_pricing) -> None:
    """token-breakdown mirrors the same hour→day guard."""
    window = "start=2026-04-01T00:00:00Z&end=2026-04-23T00:00:00Z"
    resp = client_no_pricing.get(f"/api/token-breakdown?{window}&bucket=hour")
    assert resp.status_code == 200, resp.text
    body = TokenBreakdownResponse.model_validate(resp.json())
    assert body.bucket == "day"


# ---------------------------------------------------------------------------
# tz_offset_minutes shifts day-bucket boundaries (and cost keys line up)
# ---------------------------------------------------------------------------


def _single_row_db(tmp_path: pathlib.Path, timestamp: str) -> pathlib.Path:
    """Seed a DB with one gpt-5 row at *timestamp* (1000 input / 500 output)."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            timestamp,
            "sk-test",
            "gpt-5",
            "openai-account@example.test",
            "0",
            100,
            1000,
            500,
            0,
            0,
            1500,
            0,
        ),
    )
    conn.commit()
    conn.close()
    return db_path


# Window bracketing 2026-04-30 / 2026-05-01 for the boundary row below.
_BOUNDARY_WINDOW = "start=2026-04-29T00:00:00Z&end=2026-05-02T00:00:00Z"


def test_tz_offset_shifts_token_breakdown_day_bucket(tmp_path: pathlib.Path) -> None:
    """A row at 02:00 UTC lands on the prior local day under a UTC-8 offset.

    The dense day labels and the grouped data must shift together, so the
    input-token total stays attached to the correct (shifted) bucket.
    """
    db_path = _single_row_db(tmp_path, "2026-05-01T02:00:00.000000Z")
    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: {})

    with TestClient(app) as client:
        # UTC: bucket stays on 2026-05-01.
        utc = client.get(f"/api/token-breakdown?{_BOUNDARY_WINDOW}&bucket=day")
        assert utc.status_code == 200, utc.text
        utc_body = TokenBreakdownResponse.model_validate(utc.json())
        utc_nonzero = {
            lbl: v for lbl, v in zip(utc_body.buckets, utc_body.input, strict=True) if v
        }
        assert utc_nonzero == {"2026-05-01": 1000}

        # UTC-8 (-480): 02:00Z → 18:00 previous local day, bucket 2026-04-30.
        shifted = client.get(
            f"/api/token-breakdown?{_BOUNDARY_WINDOW}&bucket=day&tz_offset_minutes=-480"
        )
        assert shifted.status_code == 200, shifted.text
        shifted_body = TokenBreakdownResponse.model_validate(shifted.json())
        shifted_nonzero = {
            lbl: v
            for lbl, v in zip(shifted_body.buckets, shifted_body.input, strict=True)
            if v
        }
        assert shifted_nonzero == {"2026-04-30": 1000}


def test_tz_offset_shifts_cost_day_bucket(tmp_path: pathlib.Path) -> None:
    """The cost path applies the same offset/bucket as the labels.

    Guards the highest-risk bug: if the cost bucketing did not honor the tz
    offset, its keys would miss the shifted labels and the series would zero.
    """
    db_path = _single_row_db(tmp_path, "2026-05-01T02:00:00.000000Z")
    pricing = {"gpt-5": ModelPricing(input_cost_per_token=1e-6)}
    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing)

    with TestClient(app) as client:
        resp = client.get(
            f"/api/timeseries?{_BOUNDARY_WINDOW}&bucket=day&metric=cost"
            "&tz_offset_minutes=-480"
        )
        assert resp.status_code == 200, resp.text
        body = TimeseriesResponse.model_validate(resp.json())
        assert body.bucket == "day"
        nonzero = {
            lbl: v
            for lbl, v in zip(body.buckets, body.series["__all__"], strict=True)
            if v
        }
        # Cost must be non-zero and attached to the shifted local day.
        assert set(nonzero) == {"2026-04-30"}
        assert nonzero["2026-04-30"] == pytest.approx(1000 * 1e-6)
