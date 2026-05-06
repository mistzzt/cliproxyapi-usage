"""Usage endpoints: /api/overview, /api/timeseries, /api/token-breakdown,
/api/health, /api/models, /api/api-keys.

Sparkline bucketing decisions (documented here per spec):
  - 7h  → hour buckets → 7 entries
  - 24h → hour buckets → 24 entries
  - 7d  → day buckets  → 7 entries   (visually distinct from 24h view)
  - all → day buckets, capped at 30 (most recent 30 days of data)

``metric=cost`` for timeseries:
  Uses a grouped ``(bucket, model)`` query to obtain the input/output/cached
  token split per bucket per model, then applies ``compute_cost`` with the
  real three-way split.  ``__all__`` is the bucket-wise sum across **all**
  models (not restricted to top-N).  Models without pricing contribute 0.0
  to the sum.

``Totals.cost``:
  Computed by iterating ``query_model_stats``, resolving pricing per model,
  and summing ``compute_cost`` across all resolved models.  Returns ``None``
  when the pricing map is entirely empty.
"""

import sqlite3
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from cliproxy_usage_server.aggregate import (
    query_api_stats,
    query_credential_stats,
    query_distinct_api_keys,
    query_distinct_models,
    query_health,
    query_model_stats,
    query_timeseries,
    query_token_breakdown,
    query_totals,
    resolve_redacted_api_keys,
)
from cliproxy_usage_server.db import open_ro, range_window
from cliproxy_usage_server.pricing import (
    CostStatus,
    ModelPricing,
    PricingResolution,
    TokenCounts,
    compute_cost,
    resolve,
    rollup_cost_status,
    split_tokens_for_cost,
)
from cliproxy_usage_server.pricing_refresh import (
    PricingRefreshState,
    maybe_refresh_pricing,
)
from cliproxy_usage_server.schemas import (
    ApiKeysResponse,
    ApiStat,
    CredentialStat,
    HealthResponse,
    LatencyPercentiles,
    ModelsResponse,
    ModelStat,
    OverviewResponse,
    SparklinePoint,
    Sparklines,
    TimeseriesResponse,
    TokenBreakdownResponse,
    Totals,
)


def _parse_models(models: str | None) -> list[str] | None:
    """Parse a comma-separated models query param into a list or None.

    Returns:
        None  — when *models* is absent, empty, or the literal "all".
        list  — non-empty list of stripped model names otherwise.
    """
    if models is None or models.strip() == "" or models.strip() == "all":
        return None
    return [m.strip() for m in models.split(",") if m.strip()]


def _parse_api_keys(api_keys: str | None) -> list[str] | None:
    """Parse a comma-separated api_keys query param (redacted) into a list.

    Returns:
        None  — when *api_keys* is absent, empty, or the literal "all".
        list  — non-empty list of stripped redacted key strings otherwise.
    """
    if api_keys is None or api_keys.strip() == "" or api_keys.strip() == "all":
        return None
    return [k.strip() for k in api_keys.split(",") if k.strip()]


def _resolve_api_keys(
    conn: sqlite3.Connection,
    redacted: list[str] | None,
) -> list[str] | None:
    """Resolve a list of redacted api-keys to raw keys for SQL filtering.

    Returns None when no filter was requested. Returns a (possibly empty) list
    otherwise — an empty list means the user picked keys that don't match
    anything in the DB, and the aggregate layer forces an empty result.
    """
    if redacted is None:
        return None
    return resolve_redacted_api_keys(conn, redacted)


# Minutes per bucket type — used for rpm/tpm sparkline derivation.
_MINUTES: dict[str, int] = {
    "hour": 60,
    "day": 1440,
}

# Map from range string → sparkline bucket type.
_SPARKLINE_BUCKET: dict[str, Literal["hour", "day"]] = {
    "7h": "hour",
    "24h": "hour",
    "7d": "day",
    "all": "day",
}

# Type alias for the range query parameter.
_RangeParam = Annotated[
    Literal["7h", "24h", "7d", "all"],
    Query(alias="range"),
]
_BucketParam = Annotated[Literal["hour", "day"], Query()]
_MetricParam = Annotated[Literal["requests", "tokens", "cost"], Query()]


def _maybe_refresh_pricing(
    request: Request,
    background_tasks: BackgroundTasks,
    statuses: list[PricingResolution],
) -> None:
    """Schedule a non-blocking pricing refresh if any status is 'missing'."""
    if not any(s == "missing" for s in statuses):
        return
    state: PricingRefreshState | None = getattr(
        request.app.state, "pricing_refresh", None
    )
    if state is None:
        return
    target = request.app.state.pricing
    config = getattr(request.app.state, "pricing_config", None)
    if config is None:
        return
    background_tasks.add_task(
        maybe_refresh_pricing,
        state=state,
        fetcher=config.fetcher,
        target=target,
    )


def _compute_totals_cost(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> tuple[float | None, CostStatus, list[PricingResolution]]:
    """Sum costs across all (model, source) cells in the window.

    Returns (cost, cost_status, statuses). cost is None only when status == "missing".
    Empty pricing map -> (None, "missing", []).
    """
    if not pricing:
        return None, "missing", ["missing"]

    rows = _grouped_cost_rows(
        conn,
        start,
        end,
        "1",
        "1",
        models=models,
        api_keys=api_keys,
    )
    statuses: list[PricingResolution] = []
    total = 0.0
    for _const, model, source, inp, out, cached in rows:
        entry, status = resolve(model, pricing)
        statuses.append(status)
        if entry is not None:
            tc = split_tokens_for_cost(source, inp, out, cached)
            total += compute_cost(tc, entry)

    roll = rollup_cost_status(statuses)
    cost: float | None = None if roll == "missing" else total
    return cost, roll, statuses


def _query_bucket_model_costs(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    bucket_fmt: str,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> tuple[dict[tuple[str, str], float], dict[str, list[PricingResolution]]]:
    """Compute per-(bucket, model) cost and per-model status list.

    Groups SQL by (bucket, model, source) and applies split_tokens_for_cost
    per cell, then rolls source up so the caller sees (bucket, model) -> cost.
    The second return value maps model -> list of per-cell statuses.
    """
    # Replicate the aggregate._range_where logic inline.
    if start is not None:
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        norm = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%S.000000Z', MIN(timestamp)) FROM requests"
        ).fetchone()
        s = norm[0] if (norm and norm[0]) else "1970-01-01T00:00:00.000000Z"

    e = end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    where = (
        "WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)"
    )
    params: list[str] = [s, e]

    mfrag = ""
    if models:
        placeholders = ", ".join("?" * len(models))
        mfrag = f" AND model IN ({placeholders})"
        params.extend(models)

    kfrag = ""
    if api_keys is not None:
        if not api_keys:
            kfrag = " AND 0=1"
        else:
            placeholders_k = ", ".join("?" * len(api_keys))
            kfrag = f" AND api_key IN ({placeholders_k})"
            params.extend(api_keys)

    rows = conn.execute(
        f"""
        SELECT
            strftime('{bucket_fmt}', timestamp) AS bkt,
            model,
            source,
            COALESCE(SUM(input_tokens), 0)  AS inp,
            COALESCE(SUM(output_tokens), 0) AS out,
            COALESCE(SUM(cached_tokens), 0) AS cac
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY bkt, model, source
        ORDER BY bkt, model, source
        """,
        params,
    ).fetchall()

    cell_cost: dict[tuple[str, str], float] = {}
    model_statuses: dict[str, list[PricingResolution]] = {}
    for bkt, model, source, inp, out, cac in rows:
        entry, status = resolve(model, pricing)
        model_statuses.setdefault(model, []).append(status)
        if entry is None:
            cell_cost[(bkt, model)] = cell_cost.get((bkt, model), 0.0)
            continue
        tc = split_tokens_for_cost(source, inp, out, cac)
        cell_cost[(bkt, model)] = cell_cost.get((bkt, model), 0.0) + compute_cost(tc, entry)
    return cell_cost, model_statuses


def _grouped_cost_rows(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    group_cols: str,
    order_cols: str,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> list[tuple]:
    """Run a grouped (group_cols, model) token-sum query for cost helpers.

    Returns rows of (group_col_values..., model, input_tokens, output_tokens,
    cached_tokens).  The WHERE clause mirrors aggregate._range_where.
    When *models* is set, restricts to those models only.
    """
    # Replicate the aggregate._range_where logic inline to avoid importing a
    # private symbol (which confuses ruff's import sorter).
    if start is not None:
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        norm = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%S.000000Z', MIN(timestamp)) FROM requests"
        ).fetchone()
        s = norm[0] if (norm and norm[0]) else "1970-01-01T00:00:00.000000Z"

    e = end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    where = (
        "WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)"
    )
    params: list[str] = [s, e]

    mfrag = ""
    if models:
        placeholders = ", ".join("?" * len(models))
        mfrag = f" AND model IN ({placeholders})"
        params.extend(models)

    kfrag = ""
    if api_keys is not None:
        if not api_keys:
            kfrag = " AND 0=1"
        else:
            placeholders_k = ", ".join("?" * len(api_keys))
            kfrag = f" AND api_key IN ({placeholders_k})"
            params.extend(api_keys)

    return conn.execute(
        f"""
        SELECT
            {group_cols},
            model,
            source,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY {group_cols}, model, source
        ORDER BY {order_cols}, model ASC, source ASC
        """,
        params,
    ).fetchall()


def _cost_by_api_key(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> tuple[dict[str, tuple[float | None, CostStatus]], list[PricingResolution]]:
    """Compute (cost, cost_status) per api_key plus a flat status list."""
    if not pricing:
        return {}, ["missing"]

    rows = _grouped_cost_rows(
        conn, start, end, "api_key", "api_key ASC", models=models, api_keys=api_keys
    )
    per_key_cost: dict[str, float] = {}
    per_key_statuses: dict[str, list[PricingResolution]] = {}
    for api_key, model, source, inp, out, cached in rows:
        entry, status = resolve(model, pricing)
        per_key_statuses.setdefault(api_key, []).append(status)
        if entry is not None:
            tc = split_tokens_for_cost(source, inp, out, cached)
            per_key_cost[api_key] = per_key_cost.get(api_key, 0.0) + compute_cost(tc, entry)
        else:
            per_key_cost.setdefault(api_key, 0.0)

    result: dict[str, tuple[float | None, CostStatus]] = {}
    flat: list[PricingResolution] = []
    for api_key, statuses in per_key_statuses.items():
        roll = rollup_cost_status(statuses)
        cost: float | None = None if roll == "missing" else per_key_cost.get(api_key, 0.0)
        result[api_key] = (cost, roll)
        flat.extend(statuses)
    return result, flat


def _cost_by_credential(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> tuple[dict[str, tuple[float | None, CostStatus]], list[PricingResolution]]:
    """Compute (cost, cost_status) per ``source`` plus a flat status list."""
    if not pricing:
        return {}, ["missing"]

    rows = _grouped_cost_rows(
        conn, start, end, "source", "source ASC", models=models, api_keys=api_keys
    )
    per_src_cost: dict[str, float] = {}
    per_src_statuses: dict[str, list[PricingResolution]] = {}
    for grouping_source, model, row_source, inp, out, cached in rows:
        entry, status = resolve(model, pricing)
        per_src_statuses.setdefault(grouping_source, []).append(status)
        if entry is not None:
            tc = split_tokens_for_cost(row_source, inp, out, cached)
            per_src_cost[grouping_source] = per_src_cost.get(grouping_source, 0.0) + compute_cost(tc, entry)
        else:
            per_src_cost.setdefault(grouping_source, 0.0)

    result: dict[str, tuple[float | None, CostStatus]] = {}
    flat: list[PricingResolution] = []
    for src, statuses in per_src_statuses.items():
        roll = rollup_cost_status(statuses)
        cost: float | None = None if roll == "missing" else per_src_cost.get(src, 0.0)
        result[src] = (cost, roll)
        flat.extend(statuses)
    return result, flat


def build_router(db_path: Path) -> APIRouter:
    """Build the usage API router.

    DB connections are opened read-only from *db_path* per-request.
    Access control is delegated to the upstream reverse proxy.
    """
    r = APIRouter(prefix="", tags=["usage"])

    def get_pricing(request: Request) -> Mapping[str, ModelPricing]:
        return request.app.state.pricing  # type: ignore[no-any-return]

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = open_ro(db_path)
        try:
            yield conn
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # GET /api/overview
    # -----------------------------------------------------------------------

    @r.get("/overview", response_model=OverviewResponse)
    def overview(
        request: Request,
        background_tasks: BackgroundTasks,
        range_: _RangeParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: Annotated[Mapping[str, ModelPricing], Depends(get_pricing)],
        models: Annotated[str | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> OverviewResponse:
        """Return summary totals and sparklines for the requested range.

        Sparkline bucketing:
          7h  → hour (7 pts), 24h → hour (24 pts),
          7d  → day (7 pts),  all → day (≤30 pts, capped to most recent 30 days).

        ``models`` / ``api_keys`` are optional comma-separated lists.
        Absent or 'all' → aggregate mode.
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)
        bucket = _SPARKLINE_BUCKET[range_]
        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))

        # --- totals ---
        raw = query_totals(conn, start, end, models=models_list, api_keys=raw_keys)
        duration_s = raw.duration_seconds
        duration_m = duration_s / 60.0 if duration_s > 0 else 0.0
        rpm = raw.requests / duration_m if duration_m > 0 else 0.0
        tpm = raw.total_tokens / duration_m if duration_m > 0 else 0.0
        cost, cost_status, totals_statuses = _compute_totals_cost(
            conn, start, end, pricing, models=models_list, api_keys=raw_keys
        )
        _maybe_refresh_pricing(request, background_tasks, totals_statuses)
        totals = Totals(
            requests=raw.requests,
            tokens=raw.total_tokens,
            cost=cost,
            cost_status=cost_status,
            rpm=rpm,
            tpm=tpm,
        )

        # --- sparklines ---
        req_ts = query_timeseries(
            conn, start, end, bucket, "requests", models_list, api_keys=raw_keys
        )
        tok_ts = query_timeseries(
            conn, start, end, bucket, "tokens", models_list, api_keys=raw_keys
        )
        req_labels, req_series = req_ts.buckets, req_ts.series
        tok_labels, tok_series = tok_ts.buckets, tok_ts.series

        minutes_per_bucket = _MINUTES[bucket]

        # When models_list is None, query_timeseries returns '__all__'.
        # When models_list is set, series has one key per model — sum across them.
        def _sum_series(
            series: dict[str, list[float]], labels: list[str]
        ) -> list[float]:
            if "__all__" in series:
                return series["__all__"]
            n = len(labels)
            result = [0.0] * n
            for vals in series.values():
                for i, v in enumerate(vals):
                    result[i] += v
            return result

        req_vals = _sum_series(req_series, req_labels)
        tok_vals = _sum_series(tok_series, tok_labels)

        req_points = [
            SparklinePoint(ts=lbl, value=v)
            for lbl, v in zip(req_labels, req_vals, strict=True)
        ]
        tok_points = [
            SparklinePoint(ts=lbl, value=v)
            for lbl, v in zip(tok_labels, tok_vals, strict=True)
        ]
        rpm_points = [
            SparklinePoint(ts=lbl, value=v / minutes_per_bucket)
            for lbl, v in zip(req_labels, req_vals, strict=True)
        ]
        tpm_points = [
            SparklinePoint(ts=lbl, value=v / minutes_per_bucket)
            for lbl, v in zip(tok_labels, tok_vals, strict=True)
        ]
        # cost sparkline: overview returns 0.0 per bucket;
        # use /api/timeseries?metric=cost for real per-bucket cost data.
        cost_points = [SparklinePoint(ts=lbl, value=0.0) for lbl in req_labels]

        sparklines = Sparklines(
            requests=req_points,
            tokens=tok_points,
            rpm=rpm_points,
            tpm=tpm_points,
            cost=cost_points,
        )

        return OverviewResponse(totals=totals, sparklines=sparklines)

    # -----------------------------------------------------------------------
    # GET /api/timeseries
    # -----------------------------------------------------------------------

    @r.get("/timeseries", response_model=TimeseriesResponse)
    def timeseries(
        request: Request,
        background_tasks: BackgroundTasks,
        range_: _RangeParam,
        bucket: _BucketParam,
        metric: _MetricParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: Annotated[Mapping[str, ModelPricing], Depends(get_pricing)],
        models: Annotated[str | None, Query()] = None,
        top_n: Annotated[int | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> TimeseriesResponse:
        """Return bucketed timeseries data.

        ``models`` / ``api_keys`` are optional comma-separated lists.
        Absent or 'all' → aggregate mode.  With ``top_n`` set (and no
        ``models``), returns ``{"__all__": [...], model1: [...], ...}`` for
        the top-N models.  Unknown model names yield zero-filled series.

        ``metric=cost``: uses the real three-way input/output/cached token
        split per (bucket, model) to apply ``compute_cost``.  ``__all__``
        sums across all models (not restricted to top-N).
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)

        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
        is_all_mode = models_list is None

        if metric == "cost":
            # Use the bucket_fmt that mirrors aggregate._bucket_fmt.
            bfmt = "%Y-%m-%dT%H:00:00Z" if bucket == "hour" else "%Y-%m-%d"

            if is_all_mode:
                # Need dense bucket labels — fetch them via a tokens query.
                ts = query_timeseries(
                    conn,
                    start,
                    end,
                    bucket,
                    "tokens",
                    models_list,
                    top_n,
                    api_keys=raw_keys,
                )
                labels = ts.buckets
                # Determine which per-model series to include in the output.
                # For top_n mode, ts.series has __all__ + top-N model keys.
                # For plain all-mode (top_n=None), ts.series has only __all__.
                output_model_keys = [k for k in ts.series if k != "__all__"]

                # Run the cost breakdown over ALL models (no model restriction)
                # so __all__ sums across every model.
                cell_costs, model_statuses = _query_bucket_model_costs(
                    conn, start, end, bfmt, pricing, models=None, api_keys=raw_keys
                )

                # Build __all__ series: sum all models per bucket.
                all_cost: dict[str, float] = {}
                for (bkt, _model), cost_val in cell_costs.items():
                    all_cost[bkt] = all_cost.get(bkt, 0.0) + cost_val

                # Build per-model cost series restricted to output_model_keys.
                model_costs: dict[str, dict[str, float]] = {
                    m: {} for m in output_model_keys
                }
                for (bkt, mdl), cost_val in cell_costs.items():
                    if mdl in model_costs:
                        prev = model_costs[mdl].get(bkt, 0.0)
                        model_costs[mdl][bkt] = prev + cost_val

                cost_series: dict[str, list[float]] = {
                    "__all__": [all_cost.get(lbl, 0.0) for lbl in labels]
                }
                for mdl in output_model_keys:
                    cost_series[mdl] = [
                        model_costs[mdl].get(lbl, 0.0) for lbl in labels
                    ]

                all_statuses: list[PricingResolution] = []
                for sts in model_statuses.values():
                    all_statuses.extend(sts)
                series_status: dict[str, CostStatus] = {
                    "__all__": rollup_cost_status(all_statuses),
                }
                for mdl in output_model_keys:
                    series_status[mdl] = rollup_cost_status(model_statuses.get(mdl, []))

                _maybe_refresh_pricing(request, background_tasks, all_statuses)

                return TimeseriesResponse(
                    buckets=labels, series=cost_series, series_status=series_status
                )

            else:
                # Explicit models= filter: get dense labels via tokens query,
                # then compute cost with real three-way split for each model.
                ts = query_timeseries(
                    conn,
                    start,
                    end,
                    bucket,
                    "tokens",
                    models_list,
                    api_keys=raw_keys,
                )
                labels = ts.buckets

                cell_costs, model_statuses = _query_bucket_model_costs(
                    conn,
                    start,
                    end,
                    bfmt,
                    pricing,
                    models=models_list,
                    api_keys=raw_keys,
                )

                assert models_list is not None
                cost_series_explicit: dict[str, list[float]] = {}
                for mdl in models_list:
                    mdl_bkt: dict[str, float] = {}
                    for (bkt, m), cost_val in cell_costs.items():
                        if m == mdl:
                            mdl_bkt[bkt] = mdl_bkt.get(bkt, 0.0) + cost_val
                    cost_series_explicit[mdl] = [
                        mdl_bkt.get(lbl, 0.0) for lbl in labels
                    ]

                series_status_explicit: dict[str, CostStatus] = {
                    mdl: rollup_cost_status(model_statuses.get(mdl, []))
                    for mdl in models_list
                }
                all_explicit: list[PricingResolution] = []
                for mdl in models_list:
                    all_explicit.extend(model_statuses.get(mdl, []))
                _maybe_refresh_pricing(request, background_tasks, all_explicit)

                return TimeseriesResponse(
                    buckets=labels,
                    series=cost_series_explicit,
                    series_status=series_status_explicit,
                )

        # Non-cost metrics: delegate entirely to query_timeseries.
        fetch_metric: Literal["requests", "tokens", "cost"] = metric
        ts = query_timeseries(
            conn,
            start,
            end,
            bucket,
            fetch_metric,
            models_list,
            top_n,
            api_keys=raw_keys,
        )
        return TimeseriesResponse(buckets=ts.buckets, series=ts.series)

    # -----------------------------------------------------------------------
    # GET /api/token-breakdown
    # -----------------------------------------------------------------------

    @r.get("/token-breakdown", response_model=TokenBreakdownResponse)
    def token_breakdown(
        range_: _RangeParam,
        bucket: _BucketParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        models: Annotated[str | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> TokenBreakdownResponse:
        """Return per-bucket token type breakdown (input/output/cached/reasoning).

        ``models`` / ``api_keys`` are optional comma-separated lists.
        Absent or 'all' → aggregate mode.
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)
        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
        tb = query_token_breakdown(
            conn, start, end, bucket, models=models_list, api_keys=raw_keys
        )
        return TokenBreakdownResponse(
            buckets=tb.buckets,
            input=tb.input,
            output=tb.output,
            cached=tb.cached,
            reasoning=tb.reasoning,
        )

    # -----------------------------------------------------------------------
    # GET /api/api-stats
    # -----------------------------------------------------------------------

    @r.get("/api-stats", response_model=list[ApiStat])
    def api_stats(
        request: Request,
        background_tasks: BackgroundTasks,
        range_: _RangeParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: Annotated[Mapping[str, ModelPricing], Depends(get_pricing)],
        models: Annotated[str | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> list[ApiStat]:
        """Return per-API-key aggregate stats.

        ``cost`` is computed by summing per-model costs for that api_key.
        If any model for an api_key lacks pricing, that row's cost is None.
        If pricing is entirely empty, all costs are None.
        ``models`` / ``api_keys`` are optional comma-separated lists.
        Absent or 'all' → aggregate mode.
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)
        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
        rows = query_api_stats(conn, start, end, models=models_list, api_keys=raw_keys)
        cost_map, api_statuses = _cost_by_api_key(
            conn, start, end, pricing, models=models_list, api_keys=raw_keys
        )
        _maybe_refresh_pricing(request, background_tasks, api_statuses)
        return [
            ApiStat(
                api_key=row.api_key,
                requests=row.requests,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                total_tokens=row.total_tokens,
                failed=row.failed,
                avg_latency_ms=row.avg_latency_ms,
                cost=cost_map.get(row.api_key, (None, "missing"))[0],
                cost_status=cost_map.get(row.api_key, (None, "missing"))[1],
            )
            for row in rows
        ]

    # -----------------------------------------------------------------------
    # GET /api/model-stats
    # -----------------------------------------------------------------------

    @r.get("/model-stats", response_model=list[ModelStat])
    def model_stats(
        request: Request,
        background_tasks: BackgroundTasks,
        range_: _RangeParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: Annotated[Mapping[str, ModelPricing], Depends(get_pricing)],
        models: Annotated[str | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> list[ModelStat]:
        """Return per-model aggregate stats.

        ``cost`` is computed via resolve + compute_cost for each model row.
        Token mapping: input_tokens, output_tokens,
        cache_read_input_tokens=cached_tokens.  No cache_creation_input_tokens;
        no reasoning_tokens (not charged in liteLLM schema).
        Returns None if pricing is absent for that model or pricing map is empty.
        ``models`` is an optional comma-separated list.  Absent or 'all' →
        aggregate mode (all models).
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)
        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
        rows = query_model_stats(
            conn, start, end, models=models_list, api_keys=raw_keys
        )

        grouped = _grouped_cost_rows(
            conn, start, end, "1", "1", models=models_list, api_keys=raw_keys
        )
        per_model_cost: dict[str, float] = {}
        per_model_status: dict[str, list[PricingResolution]] = {}
        if pricing:
            for _const, model, source, inp, out, cached in grouped:
                entry, status = resolve(model, pricing)
                per_model_status.setdefault(model, []).append(status)
                if entry is not None:
                    tc = split_tokens_for_cost(source, inp, out, cached)
                    per_model_cost[model] = per_model_cost.get(model, 0.0) + compute_cost(tc, entry)
        flat_model_statuses: list[PricingResolution] = [
            s for sts in per_model_status.values() for s in sts
        ]
        _maybe_refresh_pricing(request, background_tasks, flat_model_statuses)

        result: list[ModelStat] = []
        for row in rows:
            if not pricing:
                cost: float | None = None
                cost_status: CostStatus = "missing"
            else:
                statuses = per_model_status.get(row.model, [])
                roll = rollup_cost_status(statuses)
                cost_status = roll
                cost = None if roll == "missing" else per_model_cost.get(row.model, 0.0)
            result.append(
                ModelStat(
                    model=row.model,
                    requests=row.requests,
                    input_tokens=row.input_tokens,
                    output_tokens=row.output_tokens,
                    cached_tokens=row.cached_tokens,
                    reasoning_tokens=row.reasoning_tokens,
                    total_tokens=row.total_tokens,
                    failed=row.failed,
                    avg_latency_ms=row.avg_latency_ms,
                    cost=cost,
                    cost_status=cost_status,
                )
            )
        return result

    # -----------------------------------------------------------------------
    # GET /api/credential-stats
    # -----------------------------------------------------------------------

    @r.get("/credential-stats", response_model=list[CredentialStat])
    def credential_stats(
        request: Request,
        background_tasks: BackgroundTasks,
        range_: _RangeParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: Annotated[Mapping[str, ModelPricing], Depends(get_pricing)],
        models: Annotated[str | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> list[CredentialStat]:
        """Return per-credential (source) aggregate stats.

        ``cost`` is computed by summing per-model costs for that source.
        If any model for a source lacks pricing, that row's cost is None.
        If pricing is entirely empty, all costs are None.
        ``models`` / ``api_keys`` are optional comma-separated lists.
        Absent or 'all' → aggregate mode.
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)
        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
        rows = query_credential_stats(
            conn, start, end, models=models_list, api_keys=raw_keys
        )
        cost_map, cred_statuses = _cost_by_credential(
            conn, start, end, pricing, models=models_list, api_keys=raw_keys
        )
        _maybe_refresh_pricing(request, background_tasks, cred_statuses)
        return [
            CredentialStat(
                source=row.source,
                requests=row.requests,
                total_tokens=row.total_tokens,
                failed=row.failed,
                cost=cost_map.get(row.source, (None, "missing"))[0],
                cost_status=cost_map.get(row.source, (None, "missing"))[1],
            )
            for row in rows
        ]

    # -----------------------------------------------------------------------
    # GET /api/health
    # -----------------------------------------------------------------------

    @r.get("/health", response_model=HealthResponse)
    def health(
        range_: _RangeParam,
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        models: Annotated[str | None, Query()] = None,
        api_keys: Annotated[str | None, Query()] = None,
    ) -> HealthResponse:
        """Return health stats including latency percentiles for the requested range.

        ``models`` / ``api_keys`` are optional comma-separated lists.
        Absent or 'all' → aggregate mode.
        """
        now = datetime.now(UTC)
        start, end = range_window(range_, now)
        models_list = _parse_models(models)
        raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
        raw = query_health(conn, start, end, models=models_list, api_keys=raw_keys)
        failed_rate = raw.failed / raw.total_requests if raw.total_requests > 0 else 0.0
        return HealthResponse(
            total_requests=raw.total_requests,
            failed=raw.failed,
            failed_rate=failed_rate,
            latency=LatencyPercentiles(
                p50=raw.p50,
                p95=raw.p95,
                p99=raw.p99,
            ),
        )

    # -----------------------------------------------------------------------
    # GET /api/models
    # -----------------------------------------------------------------------

    @r.get("/models", response_model=ModelsResponse)
    def models(
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> ModelsResponse:
        """Return all distinct model names sorted lexicographically."""
        return ModelsResponse(models=query_distinct_models(conn))

    # -----------------------------------------------------------------------
    # GET /api/api-keys
    # -----------------------------------------------------------------------

    @r.get("/api-keys", response_model=ApiKeysResponse)
    def api_keys_list(
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    ) -> ApiKeysResponse:
        """Return all distinct api_key values (redacted) sorted lexicographically."""
        return ApiKeysResponse(api_keys=query_distinct_api_keys(conn))

    return r
