"""Tests for cliproxy_usage_server.aggregate — SQL aggregate helpers."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cliproxy_usage_collect.parser import iter_records
from cliproxy_usage_collect.schemas import RequestRecord
from cliproxy_usage_server.aggregate import (
    query_api_stats,
    query_credential_stats,
    query_distinct_models,
    query_health,
    query_model_stats,
    query_timeseries,
    query_token_breakdown,
    query_totals,
)
from cliproxy_usage_server.db import open_ro

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fixture_records(usage_export_json: pathlib.Path) -> list[RequestRecord]:
    """Parse the fixture JSON into a list of RequestRecord."""
    export = json.loads(usage_export_json.read_text())
    return list(iter_records(export))


# Full range covering all fixture timestamps (UTC bounds wider than the data).
_START = datetime(2026, 4, 23, 0, 0, 0, tzinfo=UTC)
_END = datetime(2026, 4, 23, 3, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# query_totals
# ---------------------------------------------------------------------------


def test_totals_full_range(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    totals = query_totals(conn, _START, _END)
    conn.close()

    assert totals.requests == len(recs)
    assert totals.input_tokens == sum(r.input_tokens for r in recs)
    assert totals.output_tokens == sum(r.output_tokens for r in recs)
    assert totals.total_tokens == sum(r.total_tokens for r in recs)
    assert totals.cached_tokens == sum(r.cached_tokens for r in recs)
    assert totals.reasoning_tokens == sum(r.reasoning_tokens for r in recs)
    assert totals.failed == sum(1 for r in recs if r.failed)
    assert isinstance(totals.avg_latency_ms, float)
    assert isinstance(totals.duration_seconds, float)


def test_totals_empty_range(seeded_db_path: Path) -> None:
    # Range before all data → zeros
    start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2020, 1, 2, 0, 0, 0, tzinfo=UTC)
    conn = open_ro(seeded_db_path)
    totals = query_totals(conn, start, end)
    conn.close()

    assert totals.requests == 0
    assert totals.total_tokens == 0
    assert totals.avg_latency_ms == 0.0
    assert totals.duration_seconds == 0.0


# ---------------------------------------------------------------------------
# query_timeseries
# ---------------------------------------------------------------------------


def test_timeseries_hour_all_models(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    ts = query_timeseries(conn, _START, _END, "hour", "requests", None)
    conn.close()

    assert set(ts.series.keys()) == {"__all__"}
    assert len(ts.buckets) == len(ts.series["__all__"])
    # Sum of per-bucket counts must equal total request count
    assert sum(ts.series["__all__"]) == len(recs)


def test_timeseries_hour_all_models_via_sentinel(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    ts = query_timeseries(conn, _START, _END, "hour", "requests", ["all"])
    conn.close()

    assert set(ts.series.keys()) == {"__all__"}
    assert sum(ts.series["__all__"]) == len(recs)


def test_timeseries_hour_filtered_models(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    subset = ["gemini-2.5-flash", "gpt-5.4"]
    conn = open_ro(seeded_db_path)
    ts = query_timeseries(conn, _START, _END, "hour", "requests", subset)
    conn.close()

    assert set(ts.series.keys()) == set(subset)
    # Each model's sum equals its record count
    for model in subset:
        expected = sum(1 for r in recs if r.model == model)
        assert sum(ts.series[model]) == expected


def test_timeseries_dense_buckets(seeded_db_path: Path) -> None:
    # Use a 3-hour window; fixture data is all in the 01:xx UTC hour,
    # so hours 00:xx and 02:xx must be in buckets but have zero values.
    start = datetime(2026, 4, 23, 0, 0, 0, tzinfo=UTC)
    end = datetime(2026, 4, 23, 3, 0, 0, tzinfo=UTC)
    conn = open_ro(seeded_db_path)
    ts = query_timeseries(conn, start, end, "hour", "requests", None)
    conn.close()

    # Expecting 3 hour buckets: 00, 01, 02
    assert len(ts.buckets) == 3
    assert "2026-04-23T00:00:00Z" in ts.buckets
    assert "2026-04-23T01:00:00Z" in ts.buckets
    assert "2026-04-23T02:00:00Z" in ts.buckets
    idx_00 = ts.buckets.index("2026-04-23T00:00:00Z")
    idx_02 = ts.buckets.index("2026-04-23T02:00:00Z")
    assert ts.series["__all__"][idx_00] == 0.0
    assert ts.series["__all__"][idx_02] == 0.0
    # 01:xx bucket must be non-zero
    idx_01 = ts.buckets.index("2026-04-23T01:00:00Z")
    assert ts.series["__all__"][idx_01] > 0


def test_timeseries_model_not_in_window_zero_filled(
    seeded_db_path: Path,
) -> None:
    # Request a model with no data in a zero-data range → zero-filled list
    start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2020, 1, 1, 3, 0, 0, tzinfo=UTC)
    conn = open_ro(seeded_db_path)
    ts = query_timeseries(conn, start, end, "hour", "requests", ["gemini-2.5-flash"])
    conn.close()

    assert "gemini-2.5-flash" in ts.series
    assert all(v == 0.0 for v in ts.series["gemini-2.5-flash"])


# ---------------------------------------------------------------------------
# query_token_breakdown
# ---------------------------------------------------------------------------


def test_token_breakdown_sum_equals_totals(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    tb = query_token_breakdown(conn, _START, _END, "hour")
    conn.close()

    assert sum(tb.input) == sum(r.input_tokens for r in recs)
    assert sum(tb.output) == sum(r.output_tokens for r in recs)
    assert sum(tb.cached) == sum(r.cached_tokens for r in recs)
    assert sum(tb.reasoning) == sum(r.reasoning_tokens for r in recs)
    assert len(tb.buckets) == len(tb.input)
    assert len(tb.buckets) == len(tb.output)


# ---------------------------------------------------------------------------
# query_api_stats
# ---------------------------------------------------------------------------


def test_api_stats_per_api_key(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    stats = query_api_stats(conn, _START, _END)
    conn.close()

    distinct_keys = set(r.api_key for r in recs)
    assert len(stats) == len(distinct_keys)
    assert sum(s.requests for s in stats) == len(recs)
    # Verify keys present
    returned_keys = {s.api_key for s in stats}
    assert returned_keys == distinct_keys


# ---------------------------------------------------------------------------
# query_model_stats
# ---------------------------------------------------------------------------


def test_model_stats_per_model(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    stats = query_model_stats(conn, _START, _END)
    conn.close()

    distinct_models = set(r.model for r in recs)
    assert len(stats) == len(distinct_models)
    assert sum(s.requests for s in stats) == len(recs)
    returned_models = {s.model for s in stats}
    assert returned_models == distinct_models


# ---------------------------------------------------------------------------
# query_credential_stats
# ---------------------------------------------------------------------------


def test_credential_stats_groups(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    stats = query_credential_stats(conn, _START, _END)
    conn.close()

    distinct_creds = {r.source for r in recs}
    assert len(stats) == len(distinct_creds)
    returned_creds = {s.source for s in stats}
    assert returned_creds == distinct_creds


# ---------------------------------------------------------------------------
# query_health
# ---------------------------------------------------------------------------


def test_health_percentiles(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    import statistics

    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    health = query_health(conn, _START, _END)
    conn.close()

    assert health.total_requests == len(recs)
    assert health.failed == sum(1 for r in recs if r.failed)
    assert health.p50 <= health.p95 <= health.p99

    # Verify percentile values match statistics.quantiles
    latencies = sorted(r.latency_ms for r in recs)
    qs = statistics.quantiles(latencies, n=100, method="inclusive")
    assert health.p50 == pytest.approx(qs[49])
    assert health.p95 == pytest.approx(qs[94])
    assert health.p99 == pytest.approx(qs[98])


def test_health_single_row_all_percentiles_equal(seeded_db_path: Path) -> None:
    # Pick a narrow 1-second window around a known record to get exactly 1 row.
    # The fixture first record is '2026-04-22T20:16:08.582370572-05:00' = 01:16:08 UTC
    start = datetime(2026, 4, 23, 1, 16, 8, tzinfo=UTC)
    end = datetime(2026, 4, 23, 1, 16, 9, tzinfo=UTC)
    conn = open_ro(seeded_db_path)
    health = query_health(conn, start, end)
    conn.close()

    assert health.total_requests == 1
    assert health.p50 == health.p95 == health.p99


def test_health_empty_window(seeded_db_path: Path) -> None:
    start = datetime(2020, 1, 1, 0, 0, 0, tzinfo=UTC)
    end = datetime(2020, 1, 2, 0, 0, 0, tzinfo=UTC)
    conn = open_ro(seeded_db_path)
    health = query_health(conn, start, end)
    conn.close()

    assert health.total_requests == 0
    assert health.failed == 0
    assert health.p50 == 0.0
    assert health.p95 == 0.0
    assert health.p99 == 0.0


# ---------------------------------------------------------------------------
# query_distinct_models
# ---------------------------------------------------------------------------


def test_distinct_models_sorted(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    models = query_distinct_models(conn)
    conn.close()

    expected = sorted(set(r.model for r in recs))
    assert models == expected


# ---------------------------------------------------------------------------
# models= filter tests
# ---------------------------------------------------------------------------


def test_totals_models_filter(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    baseline = query_totals(conn, _START, _END)
    filtered = query_totals(conn, _START, _END, models=[first_model])
    conn.close()

    # Filtered total_tokens must be <= baseline
    assert filtered.total_tokens <= baseline.total_tokens
    # Filtered requests must equal the fixture count for that model
    expected_requests = sum(1 for r in recs if r.model == first_model)
    assert filtered.requests == expected_requests
    # If fixture has multiple models, filtered < baseline
    if len(distinct) > 1:
        assert filtered.requests < baseline.requests


def test_timeseries_models_filter_restricts_sql(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    """models= filter on query_timeseries must restrict SQL rows, not just Python."""
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    # all_mode baseline
    ts_all = query_timeseries(conn, _START, _END, "hour", "requests", None)
    # single-model filter
    ts_filtered = query_timeseries(
        conn, _START, _END, "hour", "requests", [first_model]
    )
    conn.close()

    expected_count = sum(1 for r in recs if r.model == first_model)
    assert sum(ts_filtered.series[first_model]) == expected_count
    # If multiple models, all-mode sum > single model sum
    if len(distinct) > 1:
        assert sum(ts_all.series["__all__"]) > expected_count


def test_token_breakdown_models_filter(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    baseline = query_token_breakdown(conn, _START, _END, "hour")
    filtered = query_token_breakdown(conn, _START, _END, "hour", models=[first_model])
    conn.close()

    expected_input = sum(r.input_tokens for r in recs if r.model == first_model)
    assert sum(filtered.input) == expected_input
    if len(distinct) > 1:
        assert sum(filtered.input) <= sum(baseline.input)


def test_api_stats_models_filter(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    baseline = query_api_stats(conn, _START, _END)
    filtered = query_api_stats(conn, _START, _END, models=[first_model])
    conn.close()

    expected_requests = sum(1 for r in recs if r.model == first_model)
    assert sum(s.requests for s in filtered) == expected_requests
    if len(distinct) > 1:
        assert sum(s.requests for s in filtered) < sum(s.requests for s in baseline)


def test_model_stats_models_filter(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    filtered = query_model_stats(conn, _START, _END, models=[first_model])
    conn.close()

    # Should contain exactly one row for first_model
    assert len(filtered) == 1
    assert filtered[0].model == first_model


def test_credential_stats_models_filter(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    baseline = query_credential_stats(conn, _START, _END)
    filtered = query_credential_stats(conn, _START, _END, models=[first_model])
    conn.close()

    expected_requests = sum(1 for r in recs if r.model == first_model)
    assert sum(s.requests for s in filtered) == expected_requests
    if len(distinct) > 1:
        assert sum(s.requests for s in filtered) < sum(s.requests for s in baseline)


def test_health_models_filter(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    distinct = query_distinct_models(conn)
    first_model = distinct[0]

    baseline = query_health(conn, _START, _END)
    filtered = query_health(conn, _START, _END, models=[first_model])
    conn.close()

    expected_total = sum(1 for r in recs if r.model == first_model)
    assert filtered.total_requests == expected_total
    if len(distinct) > 1:
        assert filtered.total_requests < baseline.total_requests


def test_models_filter_empty_list_noop(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    """models=[] is a no-op and returns same results as models=None."""
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    baseline = query_totals(conn, _START, _END)
    filtered_empty = query_totals(conn, _START, _END, models=[])
    conn.close()

    assert filtered_empty.requests == baseline.requests == len(recs)


# ---------------------------------------------------------------------------
# query_timeseries top_n
# ---------------------------------------------------------------------------


def test_timeseries_top_n_keys_and_all_series(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    """top_n=2 with models=None returns __all__ + the 2 highest-token models.

    The __all__ series must be the sum over ALL models (not just the top 2).
    """
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)

    # Compute expected top-2 by total_tokens from fixture data.
    from collections import defaultdict

    model_tokens: dict[str, int] = defaultdict(int)
    for r in recs:
        model_tokens[r.model] += r.total_tokens
    top2 = [m for m, _ in sorted(model_tokens.items(), key=lambda x: -x[1])[:2]]

    ts = query_timeseries(conn, _START, _END, "day", "tokens", models=None, top_n=2)
    conn.close()

    # Must include __all__ plus both top-2 model keys.
    assert "__all__" in ts.series
    for model in top2:
        assert model in ts.series, f"expected top-2 model {model!r} in series keys"

    # Total key count: 1 (__all__) + 2 top models
    assert len(ts.series) == 3

    # __all__[i] == sum of ALL models (not just top-2) for each bucket.
    # Compute per-bucket totals from raw records.
    all_tokens_per_bucket: dict[str, float] = defaultdict(float)
    for r in recs:
        ts_dt = datetime.fromisoformat(r.timestamp).astimezone(UTC)
        bkt = ts_dt.strftime("%Y-%m-%d")
        all_tokens_per_bucket[bkt] += r.total_tokens

    for i, lbl in enumerate(ts.buckets):
        expected_all = all_tokens_per_bucket.get(lbl, 0.0)
        assert ts.series["__all__"][i] == pytest.approx(expected_all), (
            f"bucket {lbl!r}: __all__ {ts.series['__all__'][i]}"
            f" != expected {expected_all}"
        )


def test_timeseries_top_n_none_legacy(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    """top_n=None (default) returns only __all__ — legacy behaviour unchanged."""
    conn = open_ro(seeded_db_path)
    ts_legacy = query_timeseries(conn, _START, _END, "day", "tokens", models=None)
    ts_explicit = query_timeseries(
        conn, _START, _END, "day", "tokens", models=None, top_n=None
    )
    conn.close()

    assert set(ts_legacy.series.keys()) == {"__all__"}
    assert set(ts_explicit.series.keys()) == {"__all__"}
    assert ts_legacy.series["__all__"] == ts_explicit.series["__all__"]


def test_timeseries_top_n_models_set_ignores_top_n(
    seeded_db_path: Path,
) -> None:
    """When models= is explicitly set, top_n is ignored."""
    conn = open_ro(seeded_db_path)
    subset = ["gemini-2.5-flash"]
    ts = query_timeseries(conn, _START, _END, "day", "tokens", models=subset, top_n=5)
    conn.close()

    # Only the explicitly requested model series — no __all__, no other models.
    assert set(ts.series.keys()) == set(subset)


def test_timeseries_top_n_zero_behaves_like_none(
    seeded_db_path: Path,
) -> None:
    """top_n <= 0 is treated as top_n=None (no top-N decomposition)."""
    conn = open_ro(seeded_db_path)
    ts_zero = query_timeseries(
        conn, _START, _END, "day", "tokens", models=None, top_n=0
    )
    ts_neg = query_timeseries(
        conn, _START, _END, "day", "tokens", models=None, top_n=-1
    )
    conn.close()

    assert set(ts_zero.series.keys()) == {"__all__"}
    assert set(ts_neg.series.keys()) == {"__all__"}


def test_timeseries_top_n_fewer_models_than_n(
    seeded_db_path: Path, usage_export_json: pathlib.Path
) -> None:
    """When top_n > number of distinct models, return all available models."""
    recs = _fixture_records(usage_export_json)
    conn = open_ro(seeded_db_path)
    all_models = set(r.model for r in recs)
    # Request more than exist
    ts = query_timeseries(conn, _START, _END, "day", "tokens", models=None, top_n=999)
    conn.close()

    # All models should appear (not padded to 999)
    assert set(ts.series.keys()) == {"__all__"} | all_models
