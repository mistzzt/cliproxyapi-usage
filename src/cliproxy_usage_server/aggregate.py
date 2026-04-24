"""SQL aggregate query helpers for the usage webapp server.

All functions take a ``sqlite3.Connection`` and a ``(start, end)`` window of
``datetime`` objects (UTC-aware or None for *start* when the full table is
desired).

Return types are frozen, slotted dataclasses defined at the top of this module
(see ``TotalsRow``, ``TimeseriesResult``, …). These are **internal** record
types — separate from the public-facing DTOs in ``schemas.py``, which the
router converts to before returning them over the wire.

Note on ``query_timeseries`` metric semantics:
  - ``metric="requests"`` → COUNT(*) per bucket.
  - ``metric="tokens"``   → SUM(total_tokens) per bucket.
  - ``metric="cost"``     → SUM(total_tokens) per bucket (same as "tokens").
    Cost computation is deferred to the caller, which applies per-model
    pricing.  This function just emits the token sums so the caller has the
    raw material.
"""

from __future__ import annotations

import sqlite3
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from cliproxy_usage_server.redact import redact_key as _redact_key

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TotalsRow:
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    failed: int
    avg_latency_ms: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class TimeseriesResult:
    buckets: list[str]
    series: dict[str, list[float]]


@dataclass(frozen=True, slots=True)
class TokenBreakdownResult:
    buckets: list[str]
    input: list[int]
    output: list[int]
    cached: list[int]
    reasoning: list[int]


@dataclass(frozen=True, slots=True)
class ApiStatRow:
    api_key: str
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    failed: int
    avg_latency_ms: float


@dataclass(frozen=True, slots=True)
class ModelStatRow:
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    total_tokens: int
    failed: int
    avg_latency_ms: float


@dataclass(frozen=True, slots=True)
class CredentialStatRow:
    source: str
    requests: int
    total_tokens: int
    failed: int


@dataclass(frozen=True, slots=True)
class HealthRow:
    total_requests: int
    failed: int
    p50: float
    p95: float
    p99: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ts_param(dt: datetime) -> str:
    """Convert a UTC datetime to an ISO-8601 string for SQLite comparisons."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _start_param(start: datetime | None, conn: sqlite3.Connection) -> str:
    """Return a start bound, falling back to MIN(timestamp) when *start* is None."""
    if start is not None:
        return _ts_param(start)
    row = conn.execute("SELECT MIN(timestamp) FROM requests").fetchone()
    if row is None or row[0] is None:
        # Empty table — use the epoch; the window will be empty anyway.
        return "1970-01-01T00:00:00.000000Z"
    # Normalise to UTC via SQLite itself.
    norm = conn.execute(
        "SELECT strftime('%Y-%m-%dT%H:%M:%S.000000Z', MIN(timestamp)) FROM requests"
    ).fetchone()
    return norm[0]  # type: ignore[return-value]


def _range_where(
    start: datetime | None,
    end: datetime,
    conn: sqlite3.Connection,
) -> tuple[str, list[str]]:
    """Return (WHERE clause, [params]) for a half-open [start, end) range.

    Uses ``datetime(ts)`` on both sides so SQLite converts timezone-offset
    timestamps to UTC before comparing.
    """
    s = _start_param(start, conn)
    e = _ts_param(end)
    clause = (
        "WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)"
    )
    return clause, [s, e]


def _models_where(models: list[str] | None) -> tuple[str, list[str]]:
    """Return (' AND model IN (?, ...)', list_of_models) or ('', []) for no-op.

    An empty list is treated as a no-op (same as None).
    """
    if not models:
        return "", []
    placeholders = ", ".join("?" * len(models))
    return f" AND model IN ({placeholders})", list(models)


def _api_keys_where(api_keys: list[str] | None) -> tuple[str, list[str]]:
    """Return (' AND api_key IN (?, ...)', list) for raw api-key filter.

    ``None``  — no filter.
    ``[]``    — request was made but resolved to zero raw keys; force empty
                result via ``AND 0=1``.
    """
    if api_keys is None:
        return "", []
    if len(api_keys) == 0:
        return " AND 0=1", []
    placeholders = ", ".join("?" * len(api_keys))
    return f" AND api_key IN ({placeholders})", list(api_keys)


def resolve_redacted_api_keys(
    conn: sqlite3.Connection,
    redacted: list[str],
) -> list[str]:
    """Given a list of redacted api-key strings, return the matching raw keys.

    Scans the full ``requests.api_key`` distinct set; uses ``redact_key`` to
    match. Returns an empty list if no raw keys correspond — callers should
    pass this through ``_api_keys_where`` which treats ``[]`` as force-empty.
    """
    if not redacted:
        return []
    target = set(redacted)
    rows = conn.execute("SELECT DISTINCT api_key FROM requests").fetchall()
    return [r[0] for r in rows if _redact_key(r[0]) in target]


def query_distinct_api_keys(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct redacted api_key values sorted lexicographically."""
    rows = conn.execute("SELECT DISTINCT api_key FROM requests").fetchall()
    return sorted({_redact_key(r[0]) for r in rows})


# ---------------------------------------------------------------------------
# Dense-bucket label generation
# ---------------------------------------------------------------------------


def _hour_labels(start: datetime, end: datetime) -> list[str]:
    """Generate hourly bucket labels from *start* (floored) to *end* (exclusive)."""
    floored = start.replace(minute=0, second=0, microsecond=0)
    labels: list[str] = []
    cur = floored
    while cur < end:
        labels.append(cur.strftime("%Y-%m-%dT%H:00:00Z"))
        cur += timedelta(hours=1)
    return labels


def _day_labels(start: datetime, end: datetime) -> list[str]:
    """Generate daily bucket labels from *start* (floored to day) to *end* exclusive."""
    floored = start.replace(hour=0, minute=0, second=0, microsecond=0)
    labels: list[str] = []
    cur = floored
    while cur < end:
        labels.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return labels


def _bucket_labels(
    start: datetime | None,
    end: datetime,
    bucket: Literal["hour", "day"],
    conn: sqlite3.Connection,
) -> list[str]:
    """Return the full dense list of bucket label strings for the window."""
    if start is None:
        # Use MIN(timestamp) from DB as floor.
        row = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%SZ', MIN(timestamp)) FROM requests"
        ).fetchone()
        if row is None or row[0] is None:
            return []
        start = datetime.fromisoformat(row[0].replace("Z", "+00:00"))

    if bucket == "hour":
        return _hour_labels(start, end)
    return _day_labels(start, end)


def _bucket_fmt(bucket: Literal["hour", "day"]) -> str:
    if bucket == "hour":
        return "%Y-%m-%dT%H:00:00Z"
    return "%Y-%m-%d"


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------


def query_totals(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> TotalsRow:
    """Return aggregate totals for the window."""
    where, params = _range_where(start, end, conn)
    mfrag, mparams = _models_where(models)
    kfrag, kparams = _api_keys_where(api_keys)
    row = conn.execute(
        f"""
        SELECT
            COUNT(*)                              AS requests,
            COALESCE(SUM(input_tokens), 0)        AS input_tokens,
            COALESCE(SUM(output_tokens), 0)       AS output_tokens,
            COALESCE(SUM(total_tokens), 0)        AS total_tokens,
            COALESCE(SUM(cached_tokens), 0)       AS cached_tokens,
            COALESCE(SUM(reasoning_tokens), 0)    AS reasoning_tokens,
            COALESCE(SUM(failed), 0)              AS failed,
            COALESCE(AVG(latency_ms), 0.0)        AS avg_latency_ms,
            COALESCE(
                (julianday(MAX(timestamp)) - julianday(MIN(timestamp))) * 86400.0,
                0.0
            )                                     AS duration_seconds
        FROM requests
        {where}{mfrag}{kfrag}
        """,
        [*params, *mparams, *kparams],
    ).fetchone()

    return TotalsRow(
        requests=row[0],
        input_tokens=row[1],
        output_tokens=row[2],
        total_tokens=row[3],
        cached_tokens=row[4],
        reasoning_tokens=row[5],
        failed=row[6],
        avg_latency_ms=float(row[7]),
        duration_seconds=float(row[8]),
    )


def query_timeseries(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    bucket: Literal["hour", "day"],
    metric: Literal["requests", "tokens", "cost"],
    models: list[str] | None,
    top_n: int | None = None,
    api_keys: list[str] | None = None,
) -> TimeseriesResult:
    """Return bucket labels and per-model (or ``"__all__"``) series.

    When *models* is ``None`` or ``["all"]``, the behaviour depends on *top_n*:

    - ``top_n=None`` (default): returns a single ``"__all__"`` series (legacy).
    - ``top_n > 0``: returns ``{"__all__": [...], model1: [...], ...}`` where
      the additional per-model series are for the *top_n* models ranked by
      ``SUM(total_tokens)`` within the window.  ``__all__`` is always computed
      over the **full** unfiltered population, not restricted to top-N models.
      If fewer than *top_n* distinct models exist in the window, only the
      available ones are included (no padding).
    - ``top_n <= 0``: treated as ``top_n=None`` (no decomposition).

    When *models* is set explicitly, *top_n* is ignored and one series per
    requested model is returned (zero-filled when missing).

    Buckets are dense — intervals with no data are filled with ``0.0``.
    """
    fmt = _bucket_fmt(bucket)
    labels = _bucket_labels(start, end, bucket, conn)

    # "tokens" and "cost" both emit total_tokens; cost computation is
    # deferred to the caller which applies per-model pricing.
    agg_expr = "COUNT(*)" if metric == "requests" else "COALESCE(SUM(total_tokens), 0)"

    where, params = _range_where(start, end, conn)
    kfrag, kparams = _api_keys_where(api_keys)

    all_mode = models is None or models == ["all"]

    # Normalise top_n: treat <= 0 as None (no-op).
    effective_top_n = top_n if (top_n is not None and top_n > 0) else None

    if all_mode:
        # Always compute the __all__ series over the full unfiltered population.
        rows = conn.execute(
            f"""
            SELECT strftime('{fmt}', timestamp) AS bkt, {agg_expr} AS val
            FROM requests
            {where}{kfrag}
            GROUP BY bkt
            ORDER BY bkt
            """,
            [*params, *kparams],
        ).fetchall()
        bucket_map: dict[str, float] = {r[0]: float(r[1]) for r in rows}
        all_values = [bucket_map.get(lbl, 0.0) for lbl in labels]

        if effective_top_n is None:
            # Legacy: single __all__ series only.
            return TimeseriesResult(buckets=labels, series={"__all__": all_values})

        # top_n > 0: additionally compute per-model series for the top-N models
        # ranked by SUM(total_tokens) within the window (always by tokens,
        # regardless of metric).
        top_rows = conn.execute(
            f"""
            SELECT model, COALESCE(SUM(total_tokens), 0) AS toks
            FROM requests
            {where}{kfrag}
            GROUP BY model
            ORDER BY toks DESC
            LIMIT ?
            """,
            [*params, *kparams, effective_top_n],
        ).fetchall()
        top_models = [r[0] for r in top_rows]

        if not top_models:
            # Empty window — nothing to decompose.
            return TimeseriesResult(buckets=labels, series={"__all__": all_values})

        # Fetch per-bucket, per-model data for the top models in one query.
        mfrag_top, mparams_top = _models_where(top_models)
        model_rows = conn.execute(
            f"""
            SELECT strftime('{fmt}', timestamp) AS bkt, model, {agg_expr} AS val
            FROM requests
            {where}{mfrag_top}{kfrag}
            GROUP BY bkt, model
            ORDER BY bkt
            """,
            [*params, *mparams_top, *kparams],
        ).fetchall()

        model_bucket_map: dict[str, dict[str, float]] = {m: {} for m in top_models}
        for bkt, mdl, val in model_rows:
            if mdl in model_bucket_map:
                model_bucket_map[mdl][bkt] = float(val)

        series: dict[str, list[float]] = {"__all__": all_values}
        for mdl in top_models:
            series[mdl] = [model_bucket_map[mdl].get(lbl, 0.0) for lbl in labels]

        return TimeseriesResult(buckets=labels, series=series)

    # Per-model series.  ``models`` is non-None here (all_mode was False).
    # top_n is ignored when models is explicitly set.
    assert models is not None
    mfrag, mparams = _models_where(models)
    per_model_rows = conn.execute(
        f"""
        SELECT strftime('{fmt}', timestamp) AS bkt, model, {agg_expr} AS val
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY bkt, model
        ORDER BY bkt
        """,
        [*params, *mparams, *kparams],
    ).fetchall()

    # Build nested map: model → bucket → value
    model_bucket_explicit: dict[str, dict[str, float]] = {m: {} for m in models}
    for bkt, mdl, val in per_model_rows:
        if mdl in model_bucket_explicit:
            model_bucket_explicit[mdl][bkt] = float(val)

    explicit_series: dict[str, list[float]] = {
        mdl: [model_bucket_explicit[mdl].get(lbl, 0.0) for lbl in labels]
        for mdl in models
    }
    return TimeseriesResult(buckets=labels, series=explicit_series)


def query_token_breakdown(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    bucket: Literal["hour", "day"],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> TokenBreakdownResult:
    """Return per-bucket token breakdown (input/output/cached/reasoning).

    All value lists are ints; missing buckets are filled with ``0``.
    """
    fmt = _bucket_fmt(bucket)
    labels = _bucket_labels(start, end, bucket, conn)
    where, params = _range_where(start, end, conn)
    mfrag, mparams = _models_where(models)
    kfrag, kparams = _api_keys_where(api_keys)

    rows = conn.execute(
        f"""
        SELECT
            strftime('{fmt}', timestamp)       AS bkt,
            COALESCE(SUM(input_tokens), 0)     AS inp,
            COALESCE(SUM(output_tokens), 0)    AS out,
            COALESCE(SUM(cached_tokens), 0)    AS cac,
            COALESCE(SUM(reasoning_tokens), 0) AS rea
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY bkt
        ORDER BY bkt
        """,
        [*params, *mparams, *kparams],
    ).fetchall()

    bkt_inp: dict[str, int] = {}
    bkt_out: dict[str, int] = {}
    bkt_cac: dict[str, int] = {}
    bkt_rea: dict[str, int] = {}
    for bkt, inp, out, cac, rea in rows:
        bkt_inp[bkt] = int(inp)
        bkt_out[bkt] = int(out)
        bkt_cac[bkt] = int(cac)
        bkt_rea[bkt] = int(rea)

    return TokenBreakdownResult(
        buckets=labels,
        input=[bkt_inp.get(lbl, 0) for lbl in labels],
        output=[bkt_out.get(lbl, 0) for lbl in labels],
        cached=[bkt_cac.get(lbl, 0) for lbl in labels],
        reasoning=[bkt_rea.get(lbl, 0) for lbl in labels],
    )


def query_api_stats(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> list[ApiStatRow]:
    """Return per-API-key aggregate stats, ordered by ``api_key ASC``."""
    where, params = _range_where(start, end, conn)
    mfrag, mparams = _models_where(models)
    kfrag, kparams = _api_keys_where(api_keys)
    rows = conn.execute(
        f"""
        SELECT
            api_key,
            COUNT(*)                           AS requests,
            COALESCE(SUM(input_tokens), 0)     AS input_tokens,
            COALESCE(SUM(output_tokens), 0)    AS output_tokens,
            COALESCE(SUM(total_tokens), 0)     AS total_tokens,
            COALESCE(SUM(failed), 0)           AS failed,
            COALESCE(AVG(latency_ms), 0.0)     AS avg_latency_ms
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY api_key
        ORDER BY api_key ASC
        """,
        [*params, *mparams, *kparams],
    ).fetchall()

    return [
        ApiStatRow(
            api_key=r[0],
            requests=r[1],
            input_tokens=r[2],
            output_tokens=r[3],
            total_tokens=r[4],
            failed=r[5],
            avg_latency_ms=float(r[6]),
        )
        for r in rows
    ]


def query_model_stats(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> list[ModelStatRow]:
    """Return per-model aggregate stats, ordered by ``model ASC``."""
    where, params = _range_where(start, end, conn)
    mfrag, mparams = _models_where(models)
    kfrag, kparams = _api_keys_where(api_keys)
    rows = conn.execute(
        f"""
        SELECT
            model,
            COUNT(*)                                AS requests,
            COALESCE(SUM(input_tokens), 0)          AS input_tokens,
            COALESCE(SUM(output_tokens), 0)         AS output_tokens,
            COALESCE(SUM(cached_tokens), 0)         AS cached_tokens,
            COALESCE(SUM(reasoning_tokens), 0)      AS reasoning_tokens,
            COALESCE(SUM(total_tokens), 0)          AS total_tokens,
            COALESCE(SUM(failed), 0)                AS failed,
            COALESCE(AVG(latency_ms), 0.0)          AS avg_latency_ms
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY model
        ORDER BY model ASC
        """,
        [*params, *mparams, *kparams],
    ).fetchall()

    return [
        ModelStatRow(
            model=r[0],
            requests=r[1],
            input_tokens=r[2],
            output_tokens=r[3],
            cached_tokens=r[4],
            reasoning_tokens=r[5],
            total_tokens=r[6],
            failed=r[7],
            avg_latency_ms=float(r[8]),
        )
        for r in rows
    ]


def query_credential_stats(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> list[CredentialStatRow]:
    """Return per-credential ``source`` aggregate stats, ordered by ``source ASC``."""
    where, params = _range_where(start, end, conn)
    mfrag, mparams = _models_where(models)
    kfrag, kparams = _api_keys_where(api_keys)
    rows = conn.execute(
        f"""
        SELECT
            source,
            COUNT(*)                           AS requests,
            COALESCE(SUM(total_tokens), 0)     AS total_tokens,
            COALESCE(SUM(failed), 0)           AS failed
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY source
        ORDER BY source ASC
        """,
        [*params, *mparams, *kparams],
    ).fetchall()

    return [
        CredentialStatRow(
            source=r[0],
            requests=r[1],
            total_tokens=r[2],
            failed=r[3],
        )
        for r in rows
    ]


def query_health(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> HealthRow:
    """Return health stats including latency percentiles.

    Empty window → zeros for all values.
    Percentiles computed in Python via ``statistics.quantiles``.
    """
    where, params = _range_where(start, end, conn)
    mfrag, mparams = _models_where(models)
    kfrag, kparams = _api_keys_where(api_keys)
    all_params: list[str] = [*params, *mparams, *kparams]

    count_row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(failed), 0) FROM requests "
        f"{where}{mfrag}{kfrag}",
        all_params,
    ).fetchone()
    total = count_row[0]
    failed = count_row[1]

    if total == 0:
        return HealthRow(total_requests=0, failed=0, p50=0.0, p95=0.0, p99=0.0)

    latencies = [
        r[0]
        for r in conn.execute(
            f"SELECT latency_ms FROM requests {where}{mfrag}{kfrag}"
            " ORDER BY latency_ms",
            all_params,
        ).fetchall()
    ]

    if len(latencies) == 1:
        p50 = p95 = p99 = float(latencies[0])
    else:
        qs = statistics.quantiles(latencies, n=100, method="inclusive")
        p50 = float(qs[49])
        p95 = float(qs[94])
        p99 = float(qs[98])

    return HealthRow(
        total_requests=total,
        failed=failed,
        p50=p50,
        p95=p95,
        p99=p99,
    )


def query_distinct_models(conn: sqlite3.Connection) -> list[str]:
    """Return all distinct model names sorted lexicographically."""
    rows = conn.execute("SELECT DISTINCT model FROM requests ORDER BY model").fetchall()
    return [r[0] for r in rows]
