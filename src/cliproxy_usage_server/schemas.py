"""Response schemas (DTOs) for the FastAPI server.

Immutable pydantic models for request/response validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from cliproxy_usage_server.pricing import CostStatus
from cliproxy_usage_server.redact import redact_key

RedactedApiKey = Annotated[str, BeforeValidator(redact_key)]


class Totals(BaseModel):
    """Summary totals."""

    model_config = ConfigDict(frozen=True)

    requests: int
    tokens: int
    cost: float | None
    cost_status: CostStatus
    rpm: float
    tpm: float


class SparklinePoint(BaseModel):
    """Single point in a sparkline."""

    model_config = ConfigDict(frozen=True)

    ts: str  # ISO-8601
    value: float


class Sparklines(BaseModel):
    """Collection of sparkline series."""

    model_config = ConfigDict(frozen=True)

    requests: list[SparklinePoint]
    tokens: list[SparklinePoint]
    rpm: list[SparklinePoint]
    tpm: list[SparklinePoint]
    cost: list[SparklinePoint]


class LatencyPercentiles(BaseModel):
    """Latency percentile values."""

    model_config = ConfigDict(frozen=True)

    p50: float
    p95: float
    p99: float


class PricingEntry(BaseModel):
    """Pricing for a single model."""

    model_config = ConfigDict(frozen=True)

    input: float | None
    output: float | None
    cache_read: float | None
    cache_creation: float | None
    tiered: bool


class OverviewResponse(BaseModel):
    """Overview endpoint response."""

    model_config = ConfigDict(frozen=True)

    totals: Totals
    sparklines: Sparklines


class TimeseriesResponse(BaseModel):
    """Timeseries endpoint response."""

    model_config = ConfigDict(frozen=True)

    buckets: list[str]  # ISO-8601 strings
    series: dict[str, list[float]]  # key: model name or "__all__"
    series_status: dict[str, CostStatus] = Field(default_factory=dict)


class TokenBreakdownResponse(BaseModel):
    """Token breakdown endpoint response."""

    model_config = ConfigDict(frozen=True)

    buckets: list[str]
    input: list[int]
    output: list[int]
    cached: list[int]
    reasoning: list[int]


class ApiStat(BaseModel):
    """Statistics for a single API key."""

    model_config = ConfigDict(frozen=True)

    api_key: RedactedApiKey
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float | None
    cost_status: CostStatus
    failed: int
    avg_latency_ms: float


class ModelStat(BaseModel):
    """Statistics for a single model."""

    model_config = ConfigDict(frozen=True, protected_namespaces=())

    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost: float | None
    cost_status: CostStatus
    avg_latency_ms: float
    failed: int


class CredentialStat(BaseModel):
    """Statistics for a single credential."""

    model_config = ConfigDict(frozen=True)

    source: str
    requests: int
    total_tokens: int
    failed: int
    cost: float | None
    cost_status: CostStatus


class HealthResponse(BaseModel):
    """Health endpoint response."""

    model_config = ConfigDict(frozen=True)

    total_requests: int
    failed: int
    failed_rate: float  # 0.0-1.0
    latency: LatencyPercentiles


class ModelsResponse(BaseModel):
    """Models endpoint response."""

    model_config = ConfigDict(frozen=True)

    models: list[str]


class ApiKeysResponse(BaseModel):
    """Api-keys endpoint response (redacted)."""

    model_config = ConfigDict(frozen=True)

    api_keys: list[RedactedApiKey]


class PricingResponse(BaseModel):
    """Pricing endpoint response."""

    model_config = ConfigDict(frozen=True)

    pricing: dict[str, PricingEntry]


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


class ProviderQuota(BaseModel):
    """Quota information for a single provider."""

    model_config = ConfigDict(frozen=True)

    provider: Literal["claude", "codex"]
    auth_name: str
    plan_type: str | None
    windows: list[QuotaWindow]
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
