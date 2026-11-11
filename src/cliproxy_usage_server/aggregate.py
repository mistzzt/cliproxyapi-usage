"""Usage aggregation behind the ``/api/usage/*`` endpoints.

Every endpoint is built from one grouped query: rows per (15-minute UTC slice,
api_key, model, source). The query tiers each request with its model's
``CostPlan`` and sums the base-rate and above-rate tokens, so cells price
exactly as the sum of per-request costs. Totals, per-entity rows, trends and
mixes are sums of the same priced cells, so they always agree, and cost is
independent of the chosen bucket.

People are redacted API keys (raw keys that redact alike merge into one
person); credentials are redacted sources. Responses carry only raw token
components (the frontend derives "tokens"); the stored ``total_tokens`` column
is never read. Query helpers return the response DTOs from ``schemas.py``
directly.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from operator import sub
from typing import Literal
from zoneinfo import ZoneInfo

from cliproxy_usage_server.db import (
    SLICE_SECONDS,
    Bucket,
    SliceIndexer,
    bucket_starts,
    effective_bucket,
)
from cliproxy_usage_server.pricing import (
    Amounts,
    CostPlan,
    Costs,
    CostStatus,
    ModelPricing,
    PricingResolution,
    Rates,
    cost_plan,
    resolve,
    rollup_cost_status,
)
from cliproxy_usage_server.redact import model_key, redact_key, redact_source
from cliproxy_usage_server.schemas import (
    BreakdownResponse,
    BreakdownRow,
    CostByType,
    Dimension,
    Entity,
    LongContextPrice,
    MixEntry,
    ModelPrice,
    OptionsResponse,
    SeriesMetric,
    SummaryResponse,
    Totals,
    TrendMetric,
    UnitPrices,
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
    reasoning_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    cache_creation_1h_tokens: int
    ok_latency_sum: int
    long_requests: int
    last_used: datetime
    costs: Costs
    long_cost: float
    cache_savings: float
    status: PricingResolution
    plan: CostPlan | None

    @property
    def cost(self) -> float:
        return sum(self.costs)

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
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_creation_1h_tokens: int = 0
    ok_latency_sum: int = 0
    long_requests: int = 0
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: float = 0.0
    cost_cache_creation: float = 0.0
    long_cost: float = 0.0
    cache_savings: float = 0.0
    last_used: datetime | None = None
    statuses: set[PricingResolution] = field(default_factory=set)

    def add(self, c: _Cell) -> None:
        self.requests += c.requests
        self.failed += c.failed
        self.input_tokens += c.input_tokens
        self.output_tokens += c.output_tokens
        self.reasoning_tokens += c.reasoning_tokens
        self.cache_read_tokens += c.cache_read_tokens
        self.cache_creation_tokens += c.cache_creation_tokens
        self.cache_creation_1h_tokens += c.cache_creation_1h_tokens
        self.ok_latency_sum += c.ok_latency_sum
        self.long_requests += c.long_requests
        self.cost_input += c.costs.input
        self.cost_output += c.costs.output
        self.cost_cache_read += c.costs.cache_read
        self.cost_cache_creation += c.costs.cache_creation
        self.long_cost += c.long_cost
        self.cache_savings += c.cache_savings
        self.statuses.add(c.status)
        if self.last_used is None or c.last_used > self.last_used:
            self.last_used = c.last_used

    def cost_status(self, has_pricing: bool) -> CostStatus:
        if not has_pricing:
            return "missing"
        return rollup_cost_status(self.statuses)

    @property
    def cost(self) -> float:
        return (
            self.cost_input
            + self.cost_output
            + self.cost_cache_read
            + self.cost_cache_creation
        )

    def cost_value(self, has_pricing: bool) -> float | None:
        return None if self.cost_status(has_pricing) == "missing" else self.cost

    def totals(self, has_pricing: bool) -> Totals:
        priced = self.cost_status(has_pricing) != "missing"
        return Totals(
            requests=self.requests,
            failed=self.failed,
            input_tokens=self.input_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
            output_tokens=self.output_tokens,
            reasoning_tokens=self.reasoning_tokens,
            cost=self.cost if priced else None,
            cost_status=self.cost_status(has_pricing),
            cost_by_type=(
                CostByType(
                    input=self.cost_input,
                    output=self.cost_output,
                    cache_read=self.cost_cache_read,
                    cache_creation=self.cost_cache_creation,
                )
                if priced
                else None
            ),
            long_context_cost=self.long_cost if priced else None,
            cache_savings=self.cache_savings if priced else None,
            latency_ms=self.ok_latency_sum,
            long_context_requests=self.long_requests,
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
    keys = {
        m: model_key(m) for (m,) in conn.execute("SELECT DISTINCT model FROM requests")
    }
    resolved = {m: resolve(k, pricing) for m, k in keys.items()}
    plans: dict[str, CostPlan] = {
        m: cost_plan(entry) for m, (entry, _) in resolved.items() if entry is not None
    }
    # The NULL row keeps VALUES non-empty; unpriced models get no plan row.
    plan_rows = ", ".join(["(NULL, 0, 0)", *["(?, ?, ?)"] * len(plans)])
    plan_params = [v for m, p in plans.items() for v in (m, p.switch, p.threshold)]
    where, params = _where(window, filters)
    rows = conn.execute(
        f"""
        WITH plan(plan_model, switch, threshold) AS (VALUES {plan_rows}),
        r AS (
            SELECT
                requests.*,
                COALESCE(switch, 0) AS switch,
                COALESCE(threshold, 0) AS t,
                {_PRICED_COLUMNS}
            FROM requests LEFT JOIN plan ON plan_model = model
            {where}
        )
        SELECT
            CAST(strftime('%s', timestamp) AS INTEGER) / {SLICE_SECONDS} AS slice_no,
            api_key,
            model,
            source,
            COUNT(*) AS requests,
            SUM(failed) AS failed,
            SUM(input_tokens) AS input_tokens,
            SUM(reasoning_tokens) AS reasoning_tokens,
            SUM(CASE WHEN failed THEN 0 ELSE latency_ms END) AS ok_latency_sum,
            SUM(CASE WHEN NOT failed AND {_LONG_REQUEST} THEN 1 ELSE 0 END)
                AS long_requests,
            MAX(timestamp) AS last_used,
            {_PRICED_SUMS}
        FROM r
        GROUP BY slice_no, api_key, model, source
        """,
        [*plan_params, *params],
    ).fetchall()

    people: dict[str, str] = {}
    credentials: dict[str, str] = {}
    cells: list[_Cell] = []
    n = len(Amounts._fields)
    for (
        slice_no,
        api_key,
        model,
        source,
        requests,
        failed,
        input_tokens,
        reasoning_tokens,
        ok_latency_sum,
        long_requests,
        last_used,
        *priced,
    ) in rows:
        _, status = resolved[model]
        plan = plans.get(model)
        total = Amounts._make(priced[:n])
        base = Amounts._make(priced[n:])
        above = Amounts._make(map(sub, total, base, strict=True))
        if plan is None or not plan.has_long_tier:
            long_requests = 0
        if api_key not in people:
            people[api_key] = redact_key(api_key)
        if source not in credentials:
            credentials[source] = redact_source(source)
        cells.append(
            _Cell(
                slice_no=slice_no,
                person=people[api_key],
                model=keys[model],
                credential=credentials[source],
                requests=requests,
                failed=failed,
                input_tokens=input_tokens,
                output_tokens=total.output,
                reasoning_tokens=reasoning_tokens,
                cache_read_tokens=total.cache_read,
                cache_creation_tokens=total.cache_creation + total.cache_creation_1h,
                cache_creation_1h_tokens=total.cache_creation_1h,
                ok_latency_sum=ok_latency_sum,
                long_requests=long_requests,
                last_used=_parse_ts(last_used),
                costs=plan.costs(base, above) if plan is not None else _NO_COST,
                long_cost=plan.long_cost(above) if plan is not None else 0.0,
                cache_savings=(
                    plan.cache_savings(base.cache_read, above.cache_read)
                    if plan is not None
                    else 0.0
                ),
                status=status,
                plan=plan,
            )
        )
    return cells


_NO_COST = Costs(0.0, 0.0, 0.0, 0.0)


def _base_rate(column: str) -> str:
    """Per-request share of *column* billed at its CostPlan's base rate."""
    return (
        f"CASE WHEN switch THEN (CASE WHEN input_tokens > t THEN 0 ELSE {column} END) "
        f"ELSE MIN({column}, t) END"
    )


# Per-request tokens of each priced type, by ``Amounts`` field.
_PRICED: dict[str, str] = {
    "input": "input_tokens - cache_read_tokens - cache_creation_tokens",
    "output": "output_tokens",
    "cache_creation": "cache_creation_tokens - cache_creation_1h_tokens",
    "cache_read": "cache_read_tokens",
    "cache_creation_1h": "cache_creation_1h_tokens",
}
_PRICED_COLUMNS = ", ".join(f"{_PRICED[f]} AS priced_{f}" for f in Amounts._fields)
# Every type's total, then every type's base-rate share, in ``Amounts`` order.
_PRICED_SUMS = ", ".join(
    [f"SUM(priced_{f}) AS total_{f}" for f in Amounts._fields]
    + [f"SUM({_base_rate(f'priced_{f}')}) AS base_{f}" for f in Amounts._fields]
)
# A request has tokens at the long rate: whole-request plans when its input passes
# the threshold, marginal plans when any one token type exceeds it.
_LONG_REQUEST = (
    "(CASE WHEN switch THEN input_tokens > t "
    f"ELSE MAX({', '.join(f'priced_{f}' for f in Amounts._fields)}) > t END)"
)


def _data_start(conn: sqlite3.Connection) -> datetime | None:
    # Text MIN is not chronological across UTC offsets; order by the indexed instant.
    row = conn.execute(
        "SELECT timestamp FROM requests ORDER BY datetime(timestamp) LIMIT 1"
    ).fetchone()
    return None if row is None else _parse_ts(row[0])


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


type FilterColumn = Literal["api_key", "model", "source"]

# Display key of each filterable column's raw values.
_FILTER_KEY: dict[FilterColumn, Callable[[str], str]] = {
    "api_key": redact_key,
    "model": model_key,
    "source": redact_source,
}


def resolve_filter(
    conn: sqlite3.Connection,
    column: FilterColumn,
    keys: list[str] | None,
) -> list[str] | None:
    """Raw *column* values whose display key is in *keys*; None means no filter."""
    if keys is None:
        return None
    key_of = _FILTER_KEY[column]
    target = set(keys)
    rows = conn.execute(f"SELECT DISTINCT {column} FROM requests").fetchall()
    return [r[0] for r in rows if key_of(r[0]) in target]


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
        "cache_read_tokens": [0.0] * n,
        "requests": [0.0] * n,
        "failed": [0.0] * n,
        "latency_ms": [0.0] * n,
    }
    for c in cells:
        current.add(c)
        pos = axis.position(c.slice_no)
        if pos is None:
            continue
        series["cost"][pos] += c.cost
        series["input_tokens"][pos] += c.input_tokens
        series["output_tokens"][pos] += c.output_tokens
        series["cache_read_tokens"][pos] += c.cache_read_tokens
        series["requests"][pos] += c.requests
        series["failed"][pos] += c.failed
        series["latency_ms"][pos] += c.ok_latency_sum

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


def _unit_prices(rates: Rates) -> UnitPrices:
    # Rounding drops float noise such as 3e-06 * 1e6 == 3.0000000000000004.
    def m(per_token: float) -> float:
        return round(per_token * 1_000_000, 6)

    return UnitPrices(
        input=m(rates.input),
        output=m(rates.output),
        cache_read=m(rates.cache_read),
        cache_write=m(rates.cache_creation),
    )


def _model_price(plan: CostPlan | None) -> ModelPrice | None:
    """Per-1M rates of the plan that prices the model's cost."""
    if plan is None:
        return None
    long_context = (
        LongContextPrice(
            **_unit_prices(plan.above).model_dump(),
            threshold=plan.threshold,
            whole_request=plan.switch,
        )
        if plan.has_long_tier
        else None
    )
    return ModelPrice(
        **_unit_prices(plan.base).model_dump(),
        long_context=long_context,
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
            "cache_read_tokens": [0.0] * n,
        }
    )
    plans: dict[str, CostPlan | None] = {}
    for c in _fetch_cells(conn, window, filters, pricing):
        key = c.key(dim)
        plans[c.model] = c.plan
        per[key].add(c)
        mix[key][c.key(mix_dim)].add(c)
        pos = axis.position(c.slice_no)
        if pos is not None:
            t = trend[key]
            t["cost"][pos] += c.cost
            t["input_tokens"][pos] += c.input_tokens
            t["output_tokens"][pos] += c.output_tokens
            t["cache_read_tokens"][pos] += c.cache_read_tokens

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
                        cache_read_tokens=parts[k].cache_read_tokens,
                    )
                    for k in sorted(parts, key=lambda k: parts[k].rank(k))
                ],
                price=_model_price(plans[key]) if dim == "model" else None,
            )
        )
    return BreakdownResponse(
        dim=dim, bucket=axis.bucket, buckets=axis.starts, rows=rows
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


EXPORT_COLUMNS = (
    "bucket_start",
    "person",
    "person_label",
    "model",
    "credential",
    "requests",
    "failed",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cache_creation_1h_tokens",
    "reasoning_tokens",
    "cost",
)


def query_export(
    conn: sqlite3.Connection,
    window: Window,
    requested_bucket: Bucket | None,
    tz: ZoneInfo,
    filters: Filters,
    pricing: Mapping[str, ModelPricing],
    aliases: Mapping[str, str],
) -> str:
    """CSV with one row per (bucket, person, model, credential) that has requests."""
    axis = _axis(conn, window, requested_bucket, tz)
    groups: dict[tuple[int, str, str, str], _Acc] = defaultdict(_Acc)
    priced: dict[str, bool] = {}
    for c in _fetch_cells(conn, window, filters, pricing):
        pos = axis.position(c.slice_no)
        if pos is None:
            continue
        groups[(pos, c.person, c.model, c.credential)].add(c)
        priced[c.model] = c.plan is not None

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    for (pos, person, model, credential), acc in sorted(groups.items()):
        writer.writerow(
            (
                axis.starts[pos].isoformat(),
                person,
                _label("person", person, aliases),
                model,
                credential,
                acc.requests,
                acc.failed,
                acc.input_tokens,
                acc.output_tokens,
                acc.cache_read_tokens,
                acc.cache_creation_tokens,
                acc.cache_creation_1h_tokens,
                acc.reasoning_tokens,
                repr(acc.cost) if priced[model] else "",
            )
        )
    return out.getvalue()
