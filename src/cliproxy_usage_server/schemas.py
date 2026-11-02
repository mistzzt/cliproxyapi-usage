"""Response schemas (DTOs) for the FastAPI server.

Immutable pydantic models for request/response validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cliproxy_usage_server.db import Bucket
from cliproxy_usage_server.pricing import CostStatus

Dimension = Literal["person", "model", "credential"]
TrendMetric = Literal["cost", "input_tokens", "output_tokens", "cached_tokens"]
SeriesMetric = Literal[
    "cost", "input_tokens", "output_tokens", "cached_tokens", "requests"
]


class Totals(BaseModel):
    """Aggregate usage over a window; derived "tokens" is a frontend concern."""

    model_config = ConfigDict(frozen=True)

    requests: int
    failed: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    cost: float | None
    cost_status: CostStatus
    avg_latency_ms: float
    last_used: datetime | None


class SummaryResponse(BaseModel):
    """``GET /api/usage/summary``: KPI totals plus dense per-bucket series."""

    model_config = ConfigDict(frozen=True)

    bucket: Bucket  # effective bucket after auto selection / coarsening
    buckets: list[datetime]  # bucket starts in the viewer's timezone, dense
    current: Totals
    previous: Totals | None  # None when the window has no start (all time)
    series: dict[SeriesMetric, list[float]]


class MixEntry(BaseModel):
    """One slice of a row's paired-dimension mix."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    cost: float | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class UnitPrices(BaseModel):
    """USD per 1M tokens of each type."""

    model_config = ConfigDict(frozen=True)

    input: float
    output: float
    cache_read: float
    cache_write: float


class LongContextPrice(UnitPrices):
    """Rates past *threshold* tokens, for the whole request or each type's excess."""

    threshold: int
    whole_request: bool


class ModelPrice(UnitPrices):
    """The rates cost is computed with (base tier, no speed multiplier)."""

    long_context: LongContextPrice | None  # None when the model has no long tier
    cached_in_input: bool  # OpenAI accounting: cached tokens are a subset of input


class BreakdownRow(BaseModel):
    """One entity (person, model, or credential) in a breakdown."""

    model_config = ConfigDict(frozen=True)

    key: str  # redacted key / model id / redacted source
    label: str
    current: Totals
    previous: Totals | None
    trend: dict[TrendMetric, list[float]]  # aligned with the response buckets
    mix: list[MixEntry]  # paired dimension: person -> model, others -> person
    price: ModelPrice | None  # model rows only; None when unpriced or not a model


class BreakdownResponse(BaseModel):
    """``GET /api/usage/breakdown``: per-entity rows for one dimension."""

    model_config = ConfigDict(frozen=True)

    dim: Dimension
    bucket: Bucket
    buckets: list[datetime]
    rows: list[BreakdownRow]


class MatrixCell(BaseModel):
    """One (row entity, column entity) cell of the usage matrix."""

    model_config = ConfigDict(frozen=True)

    row: str
    col: str
    cost: float | None
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    requests: int


class MatrixResponse(BaseModel):
    """``GET /api/usage/matrix``: sparse cross tabulation of two dimensions."""

    model_config = ConfigDict(frozen=True)

    rows: list[str]
    cols: list[str]
    cells: list[MatrixCell]  # sparse; absent means zero


class Entity(BaseModel):
    """A filterable entity with its stable color slot."""

    model_config = ConfigDict(frozen=True)

    key: str
    label: str
    slot: int | None  # 0..6 by all-time spend rank; None folds to "Other"


class OptionsResponse(BaseModel):
    """``GET /api/usage/options``: every entity per dimension, by all-time spend."""

    model_config = ConfigDict(frozen=True)

    people: list[Entity]
    models: list[Entity]
    credentials: list[Entity]


class QuotaAccount(BaseModel):
    """Account information for quota display."""

    model_config = ConfigDict(frozen=True)

    provider: Literal["claude", "codex"]
    auth_name: str
    display_name: str | None


class QuotaAccountsResponse(BaseModel):
    """Response containing list of quota accounts."""

    model_config = ConfigDict(frozen=True)

    accounts: list[QuotaAccount]


class QuotaWindow(BaseModel):
    """A quota window (e.g., monthly, daily) with usage and reset info."""

    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    used_percent: float | None
    resets_at: datetime | None


class ManualResetCredit(BaseModel):
    """One manual reset credit and when it stops being usable."""

    model_config = ConfigDict(frozen=True)

    id: str
    granted_at: datetime | None
    expires_at: datetime


class ManualResetSummary(BaseModel):
    """Manual quota resets available for an account."""

    model_config = ConfigDict(frozen=True)

    available_count: int = Field(ge=0)
    # Sorted by expires_at ascending; empty when per-credit detail is unavailable.
    credits: list[ManualResetCredit] = Field(default_factory=list)
    credits_error: str | None = None


class ProviderQuota(BaseModel):
    """Quota information for a single provider."""

    model_config = ConfigDict(frozen=True)

    provider: Literal["claude", "codex"]
    auth_name: str
    plan_type: str | None
    windows: list[QuotaWindow]
    manual_resets: ManualResetSummary | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class QuotaError(BaseModel):
    """Error information for quota fetch failures."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["auth", "rate_limited", "upstream", "schema", "transient"]
    message: str
    upstream_status: int | None


class QuotaResponse(BaseModel):
    """Response containing quota information or error."""

    model_config = ConfigDict(frozen=True)

    quota: ProviderQuota | None
    error: QuotaError | None
    fetched_at: datetime
    stale_at: datetime

    @model_validator(mode="after")
    def exactly_one_of_quota_error(self) -> QuotaResponse:
        """Enforce that exactly one of quota or error is populated."""
        has_quota = self.quota is not None
        has_error = self.error is not None

        if not (has_quota ^ has_error):  # XOR: true if exactly one is true
            raise ValueError(
                "QuotaResponse must have exactly one of 'quota' or 'error' populated"
            )

        return self
