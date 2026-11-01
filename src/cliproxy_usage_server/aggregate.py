"""Usage aggregation behind the ``/api/usage/*`` endpoints.

Every endpoint is built from one grouped query: rows per (15-minute UTC slice,
api_key, model, source), each priced with ``split_tokens_for_cost`` and
``compute_cost``. Totals, per-entity rows, trends and the matrix are sums of
the same priced cells, so they always agree, and pricing per slice keeps cost
independent of the chosen bucket.

People are redacted API keys (raw keys that redact alike merge into one
person); credentials are redacted sources. Responses carry only raw token
components (the frontend derives "tokens"); the stored ``total_tokens`` column
is never summed. Query helpers return the response DTOs from ``schemas.py``
directly.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from cliproxy_usage_server.db import (
    SLICE_SECONDS,
    Bucket,
    SliceIndexer,
    bucket_starts,
    effective_bucket,
)
from cliproxy_usage_server.pricing import (
    CostStatus,
    ModelPricing,
    PricingResolution,
    compute_cost,
    resolve,
    rollup_cost_status,
    split_tokens_for_cost,
)
from cliproxy_usage_server.redact import redact_key, redact_source
from cliproxy_usage_server.schemas import (
    BreakdownResponse,
    BreakdownRow,
    Dimension,
    Entity,
    MatrixCell,
    MatrixResponse,
    MixEntry,
    ModelPrice,
    OptionsResponse,
    SeriesMetric,
    SummaryResponse,
    Totals,
    TrendMetric,
)

# Number of entities per dimension that get a fixed color slot.
SLOT_COUNT = 7

# Paired dimension shown in each breakdown row's mix bar.
MIX_DIM: dict[Dimension, Dimension] = {
    "person": "model",
    "model": "person",
    "credential": "person",
}


@dataclass(frozen=True, slots=True)
class Window:
    """Half-open ``[start, end)`` UTC window; ``start=None`` means all time."""

    start: datetime | None
    end: datetime

    def previous(self) -> Window | None:
        """The equal-length window ending at ``start``; None for all time."""
        if self.start is None:
            return None
        return Window(self.start - (self.end - self.start), self.start)


@dataclass(frozen=True, slots=True)
class Filters:
    """Raw-value SQL filters; ``None`` is no filter, ``[]`` matches nothing."""

    api_keys: list[str] | None = None
    models: list[str] | None = None
    sources: list[str] | None = None


@dataclass(frozen=True, slots=True)
class _Cell:
    slice_no: int
    person: str
    model: str
    credential: str
    requests: int
    failed: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    latency_sum: int
    last_used: datetime
    cost: float
    status: PricingResolution

    def key(self, dim: Dimension) -> str:
        if dim == "person":
            return self.person
        if dim == "model":
            return self.model
        return self.credential


@dataclass(slots=True)
class _Acc:
    """Running sums over priced cells."""

    requests: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_sum: int = 0
    cost: float = 0.0
    last_used: datetime | None = None
    statuses: set[PricingResolution] = field(default_factory=set)

    def add(self, c: _Cell) -> None:
        self.requests += c.requests
        self.failed += c.failed
        self.input_tokens += c.input_tokens
        self.output_tokens += c.output_tokens
        self.cached_tokens += c.cached_tokens
        self.reasoning_tokens += c.reasoning_tokens
        self.latency_sum += c.latency_sum
        self.cost += c.cost
        self.statuses.add(c.status)
        if self.last_used is None or c.last_used > self.last_used:
            self.last_used = c.last_used

    def cost_status(self, has_pricing: bool) -> CostStatus:
        # No cells means nothing is unpriced; only an absent pricing map is "missing".
        if not has_pricing:
            return "missing"
        return rollup_cost_status(sorted(self.statuses)) if self.statuses else "live"

    def cost_value(self, has_pricing: bool) -> float | None:
        return None if self.cost_status(has_pricing) == "missing" else self.cost

    def totals(self, has_pricing: bool) -> Totals:
        return Totals(
            requests=self.requests,
            failed=self.failed,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost=self.cost_value(has_pricing),
            cost_status=self.cost_status(has_pricing),
            avg_latency_ms=self.latency_sum / self.requests if self.requests else 0.0,
            last_used=self.last_used,
        )

    def rank(self, key: str) -> tuple[float, int, str]:
        """Sort key: cost desc, then input + output tokens desc, then key."""
        return (-self.cost, -(self.input_tokens + self.output_tokens), key)


@dataclass(frozen=True, slots=True)
class _Axis:
    bucket: Bucket
    starts: list[datetime]
    position: SliceIndexer


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------


def _ts_param(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _where(window: Window | None, filters: Filters) -> tuple[str, list[str]]:
    """Build the WHERE clause; ``datetime(timestamp)`` matches idx_requests_ts_utc."""
    clauses: list[str] = []
    params: list[str] = []
    if window is not None:
        if window.start is not None:
            clauses.append("datetime(timestamp) >= datetime(?)")
            params.append(_ts_param(window.start))
        clauses.append("datetime(timestamp) < datetime(?)")
        params.append(_ts_param(window.end))
    for column, values in (
        ("api_key", filters.api_keys),
        ("model", filters.models),
        ("source", filters.sources),
    ):
        if values is None:
            continue
        if not values:
            clauses.append("0 = 1")
            continue
        clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
        params.extend(values)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _fetch_cells(
    conn: sqlite3.Connection,
    window: Window | None,
    filters: Filters,
    pricing: Mapping[str, ModelPricing],
) -> list[_Cell]:
    """Group the window into priced (slice, api_key, model, source) cells."""
    where, params = _where(window, filters)
    rows = conn.execute(
        f"""
        SELECT
            CAST(strftime('%s', timestamp) AS INTEGER) / {SLICE_SECONDS} AS slice_no,
            api_key,
            model,
            source,
            COUNT(*),
            SUM(failed),
            SUM(input_tokens),
            SUM(output_tokens),
            SUM(cached_tokens),
            SUM(reasoning_tokens),
            SUM(latency_ms),
            MAX(timestamp)
        FROM requests
        {where}
        GROUP BY slice_no, api_key, model, source
        """,
        params,
    ).fetchall()

    resolved: dict[str, tuple[ModelPricing | None, PricingResolution]] = {}
    people: dict[str, str] = {}
    credentials: dict[str, str] = {}
    cells: list[_Cell] = []
    for (
        slice_no,
        api_key,
        model,
        source,
        req,
        failed,
        inp,
        out,
        cac,
        rea,
        lat,
        last,
    ) in rows:
        if model not in resolved:
            resolved[model] = resolve(model, pricing)
        entry, status = resolved[model]
        cost = (
            compute_cost(split_tokens_for_cost(entry, inp, out, cac), entry)
            if entry is not None
            else 0.0
        )
        if api_key not in people:
            people[api_key] = redact_key(api_key)
        if source not in credentials:
            credentials[source] = redact_source(source)
        cells.append(
            _Cell(
                slice_no=slice_no,
                person=people[api_key],
                model=model,
                credential=credentials[source],
                requests=req,
                failed=failed,
                input_tokens=inp,
                output_tokens=out,
                cached_tokens=cac,
                reasoning_tokens=rea,
                latency_sum=lat,
                last_used=_parse_ts(last),
                cost=cost,
                status=status,
            )
        )
    return cells


def _data_start(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute("SELECT MIN(timestamp) FROM requests").fetchone()
    return None if row is None or row[0] is None else _parse_ts(row[0])


def _axis(
    conn: sqlite3.Connection,
    window: Window,
    requested: Bucket | None,
    tz: ZoneInfo,
) -> _Axis:
    """Dense bucket axis; all-time windows start at the first recorded request."""
    start = window.start or _data_start(conn) or window.end
    bucket = effective_bucket(start, window.end, requested, tz)
    starts = bucket_starts(start, window.end, bucket, tz)
    return _Axis(bucket=bucket, starts=starts, position=SliceIndexer(starts))


def _group(cells: Iterable[_Cell], key: Callable[[_Cell], str]) -> dict[str, _Acc]:
    out: dict[str, _Acc] = defaultdict(_Acc)
    for c in cells:
        out[key(c)].add(c)
    return out


def _label(dim: Dimension, key: str, aliases: Mapping[str, str]) -> str:
    return aliases.get(key, key) if dim == "person" else key


# ---------------------------------------------------------------------------
# Filter resolution
# ---------------------------------------------------------------------------


def resolve_redacted_api_keys(
    conn: sqlite3.Connection,
    redacted: list[str],
) -> list[str]:
    """Return the raw api keys whose ``redact_key`` value is in *redacted*."""
    target = set(redacted)
    rows = conn.execute("SELECT DISTINCT api_key FROM requests").fetchall()
    return [r[0] for r in rows if redact_key(r[0]) in target]


def resolve_redacted_sources(
    conn: sqlite3.Connection,
    redacted: list[str],
) -> list[str]:
    """Return the raw sources whose ``redact_source`` value is in *redacted*."""
    target = set(redacted)
    rows = conn.execute("SELECT DISTINCT source FROM requests").fetchall()
    return [r[0] for r in rows if redact_source(r[0]) in target]


def query_people(conn: sqlite3.Connection) -> set[str]:
    """Return every distinct redacted api key."""
    rows = conn.execute("SELECT DISTINCT api_key FROM requests").fetchall()
    return {redact_key(r[0]) for r in rows}


# ---------------------------------------------------------------------------
# Endpoint queries
# ---------------------------------------------------------------------------


def query_summary(
    conn: sqlite3.Connection,
    window: Window,
    requested_bucket: Bucket | None,
    tz: ZoneInfo,
    filters: Filters,
    pricing: Mapping[str, ModelPricing],
) -> SummaryResponse:
    """Totals for the window and its previous period, plus per-bucket series."""
    has_pricing = bool(pricing)
    axis = _axis(conn, window, requested_bucket, tz)
    cells = _fetch_cells(conn, window, filters, pricing)

    current = _Acc()
    n = len(axis.starts)
    series: dict[SeriesMetric, list[float]] = {
        "cost": [0.0] * n,
        "input_tokens": [0.0] * n,
        "output_tokens": [0.0] * n,
        "cached_tokens": [0.0] * n,
        "requests": [0.0] * n,
    }
    for c in cells:
        current.add(c)
        pos = axis.position(c.slice_no)
        if pos is None:
            continue
        series["cost"][pos] += c.cost
        series["input_tokens"][pos] += c.input_tokens
        series["output_tokens"][pos] += c.output_tokens
        series["cached_tokens"][pos] += c.cached_tokens
        series["requests"][pos] += c.requests

    prev_window = window.previous()
    previous: Totals | None = None
    if prev_window is not None:
        prev = _Acc()
        for c in _fetch_cells(conn, prev_window, filters, pricing):
            prev.add(c)
        previous = prev.totals(has_pricing)

    return SummaryResponse(
        bucket=axis.bucket,
        buckets=axis.starts,
        current=current.totals(has_pricing),
        previous=previous,
        series=series,
    )


def _per_million(per_token: float | None) -> float | None:
    # Rounding drops float noise such as 3e-06 * 1e6 == 3.0000000000000004.
    return None if per_token is None else round(per_token * 1_000_000, 6)


def _model_price(model: str, pricing: Mapping[str, ModelPricing]) -> ModelPrice | None:
    """List price per 1M tokens from the same resolution used for cost."""
    entry, _ = resolve(model, pricing)
    if entry is None:
        return None
    return ModelPrice(
        input=_per_million(entry.input_cost_per_token),
        output=_per_million(entry.output_cost_per_token),
        cache_read=_per_million(entry.cache_read_input_token_cost),
        cache_write=_per_million(entry.cache_creation_input_token_cost),
        input_above_200k=_per_million(entry.input_cost_per_token_above_200k_tokens),
        output_above_200k=_per_million(entry.output_cost_per_token_above_200k_tokens),
    )


def query_breakdown(
    conn: sqlite3.Connection,
    dim: Dimension,
    window: Window,
    requested_bucket: Bucket | None,
    tz: ZoneInfo,
    filters: Filters,
    pricing: Mapping[str, ModelPricing],
    aliases: Mapping[str, str],
) -> BreakdownResponse:
    """One row per entity of *dim* with totals, previous totals, trend and mix."""
    has_pricing = bool(pricing)
    mix_dim = MIX_DIM[dim]
    axis = _axis(conn, window, requested_bucket, tz)
    n = len(axis.starts)

    per: dict[str, _Acc] = defaultdict(_Acc)
    mix: dict[str, dict[str, _Acc]] = defaultdict(lambda: defaultdict(_Acc))
    trend: dict[str, dict[TrendMetric, list[float]]] = defaultdict(
        lambda: {
            "cost": [0.0] * n,
            "input_tokens": [0.0] * n,
            "output_tokens": [0.0] * n,
            "cached_tokens": [0.0] * n,
        }
    )
    for c in _fetch_cells(conn, window, filters, pricing):
        key = c.key(dim)
        per[key].add(c)
        mix[key][c.key(mix_dim)].add(c)
        pos = axis.position(c.slice_no)
        if pos is not None:
            t = trend[key]
            t["cost"][pos] += c.cost
            t["input_tokens"][pos] += c.input_tokens
            t["output_tokens"][pos] += c.output_tokens
            t["cached_tokens"][pos] += c.cached_tokens

    prev_window = window.previous()
    prev = (
        _group(_fetch_cells(conn, prev_window, filters, pricing), lambda c: c.key(dim))
        if prev_window is not None
        else None
    )

    rows: list[BreakdownRow] = []
    for key in sorted(per, key=lambda k: per[k].rank(k)):
        parts = mix[key]
        rows.append(
            BreakdownRow(
                key=key,
                label=_label(dim, key, aliases),
                current=per[key].totals(has_pricing),
                previous=(
                    prev.get(key, _Acc()).totals(has_pricing)
                    if prev is not None
                    else None
                ),
                trend=trend[key],
                mix=[
                    MixEntry(
                        key=k,
                        label=_label(mix_dim, k, aliases),
                        cost=parts[k].cost_value(has_pricing),
                        input_tokens=parts[k].input_tokens,
                        output_tokens=parts[k].output_tokens,
                        cached_tokens=parts[k].cached_tokens,
                    )
                    for k in sorted(parts, key=lambda k: parts[k].rank(k))
                ],
                price=_model_price(key, pricing) if dim == "model" else None,
            )
        )
    return BreakdownResponse(
        dim=dim, bucket=axis.bucket, buckets=axis.starts, rows=rows
    )


def query_matrix(
    conn: sqlite3.Connection,
    row_dim: Dimension,
    col_dim: Dimension,
    window: Window,
    filters: Filters,
    pricing: Mapping[str, ModelPricing],
) -> MatrixResponse:
    """Sparse cross tabulation; rows and columns ordered like ``_Acc.rank``."""
    has_pricing = bool(pricing)
    cells = _fetch_cells(conn, window, filters, pricing)
    row_tot = _group(cells, lambda c: c.key(row_dim))
    col_tot = _group(cells, lambda c: c.key(col_dim))
    pairs = _group(cells, lambda c: f"{c.key(row_dim)}\0{c.key(col_dim)}")

    out: list[MatrixCell] = []
    for pair in sorted(pairs, key=lambda p: pairs[p].rank(p)):
        row, col = pair.split("\0", 1)
        acc = pairs[pair]
        out.append(
            MatrixCell(
                row=row,
                col=col,
                cost=acc.cost_value(has_pricing),
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                cached_tokens=acc.cached_tokens,
                requests=acc.requests,
            )
        )
    return MatrixResponse(
        rows=sorted(row_tot, key=lambda k: row_tot[k].rank(k)),
        cols=sorted(col_tot, key=lambda k: col_tot[k].rank(k)),
        cells=out,
    )


def query_options(
    conn: sqlite3.Connection,
    pricing: Mapping[str, ModelPricing],
    aliases: Mapping[str, str],
) -> OptionsResponse:
    """Every entity per dimension ordered by all-time spend; the top 7 get slots."""
    cells = _fetch_cells(conn, None, Filters(), pricing)

    def entities(dim: Dimension) -> list[Entity]:
        groups = _group(cells, lambda c: c.key(dim))
        ranked = sorted(groups, key=lambda k: groups[k].rank(k))
        return [
            Entity(
                key=k,
                label=_label(dim, k, aliases),
                slot=i if i < SLOT_COUNT else None,
            )
            for i, k in enumerate(ranked)
        ]

    return OptionsResponse(
        people=entities("person"),
        models=entities("model"),
        credentials=entities("credential"),
    )
