"""Pricing model, resolver, cost computation, and disk-cached fetcher.

Ports ccusage's LiteLLM pricing and per-request cost calculation (Rust core
``cost.rs`` and the Codex adapter for OpenAI-accounting models) into Python.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "CostPlan",
    "CostStatus",
    "Costs",
    "ModelPricing",
    "PricingResolution",
    "Rates",
    "cost_plan",
    "fetch_pricing",
    "resolve",
    "rollup_cost_status",
]

PricingResolution = Literal["live", "missing"]
CostStatus = Literal["live", "partial_missing", "missing"]

_OPENAI_LITELLM_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "azure",
    }
)


def _uses_codex_billing(pricing: ModelPricing) -> bool:
    """Return True for models ccusage bills with its Codex (OpenAI) adapter."""
    provider = (pricing.litellm_provider or "").strip().lower()
    return provider in _OPENAI_LITELLM_PROVIDERS


_log = logging.getLogger(__name__)

_THRESHOLD_200K = 200_000
_THRESHOLD_272K = 272_000

PREFIX_CANDIDATES: tuple[str, ...] = (
    "anthropic/",
    "claude-3-5-",
    "claude-3-",
    "claude-",
    "openai/",
    "azure/",
    "openrouter/openai/",
)


class ModelPricing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    litellm_provider: str | None = None
    # Required, as in ccusage: entries without base input/output rates are dropped.
    input_cost_per_token: float
    output_cost_per_token: float
    cache_creation_input_token_cost: float | None = None
    cache_read_input_token_cost: float | None = None
    input_cost_per_token_above_200k_tokens: float | None = None
    output_cost_per_token_above_200k_tokens: float | None = None
    cache_creation_input_token_cost_above_200k_tokens: float | None = None
    cache_read_input_token_cost_above_200k_tokens: float | None = None
    input_cost_per_token_above_272k_tokens: float | None = None
    output_cost_per_token_above_272k_tokens: float | None = None
    cache_creation_input_token_cost_above_272k_tokens: float | None = None
    cache_read_input_token_cost_above_272k_tokens: float | None = None


def resolve(
    model_name: str, pricing: Mapping[str, ModelPricing]
) -> tuple[ModelPricing | None, PricingResolution]:
    """Return (ModelPricing, "live") for a hit, (None, "missing") for a miss.

    Match order:
    1. Exact key lookup.
    2. Each prefix in PREFIX_CANDIDATES prepended to model_name.
    3. First case-insensitive substring match (key contains name, or name
       contains key).
    """
    if model_name in pricing:
        return pricing[model_name], "live"

    for prefix in PREFIX_CANDIDATES:
        candidate = f"{prefix}{model_name}"
        if candidate in pricing:
            return pricing[candidate], "live"

    lower = model_name.lower()
    for key, value in pricing.items():
        key_lower = key.lower()
        if key_lower in lower or lower in key_lower:
            return value, "live"

    return None, "missing"


def rollup_cost_status(statuses: Collection[PricingResolution]) -> CostStatus:
    """Roll per-component statuses into one; no components means nothing is unpriced."""
    if "missing" not in statuses:
        return "live"
    return "partial_missing" if "live" in statuses else "missing"


# ccusage bills 1-hour cache writes at twice the input rate of their tier.
_CACHE_CREATION_1H_INPUT_MULTIPLIER = 2.0


class Rates(NamedTuple):
    """USD per token for each token type."""

    input: float
    output: float
    cache_creation: float
    cache_read: float
    cache_creation_1h: float


class Amounts(NamedTuple):
    """Token counts in ``Rates`` field order."""

    input: int  # uncached
    output: int
    cache_creation: int  # the 5-minute part
    cache_read: int
    cache_creation_1h: int


class Costs(NamedTuple):
    """USD spent on each token type."""

    input: float
    output: float
    cache_creation: float  # 5-minute and 1-hour writes
    cache_read: float


class CostPlan(NamedTuple):
    """How one model bills a request, following ccusage.

    ``aggregate._fetch_cells`` applies it per request in SQL; ``costs`` prices the sums.
    """

    # True: once input_tokens passes the threshold the whole request bills at `above`.
    # False: only each token type's excess over the threshold does.
    switch: bool
    threshold: int
    base: Rates
    above: Rates

    @property
    def has_long_tier(self) -> bool:
        return self.above != self.base

    def costs(self, base: Amounts, above: Amounts) -> Costs:
        """Per-type cost of token sums billed at the base and above rates."""
        inp, out, cache_creation, cache_read, cache_creation_1h = (
            b * rb + a * ra
            for b, a, rb, ra in zip(base, above, self.base, self.above, strict=True)
        )
        return Costs(inp, out, cache_creation + cache_creation_1h, cache_read)

    def long_cost(self, above: Amounts) -> float:
        """Cost of the tokens billed at long-context rates; 0 without a long tier."""
        if not self.has_long_tier:
            return 0.0
        return sum(n * r for n, r in zip(above, self.above, strict=True))

    def cache_savings(self, base_read: int, above_read: int) -> float:
        """Uncached input cost of cache reads, minus their cost, per tier."""
        return base_read * (self.base.input - self.base.cache_read) + above_read * (
            self.above.input - self.above.cache_read
        )


def cost_plan(pricing: ModelPricing) -> CostPlan:
    """Derive the billing plan for *pricing* as ccusage does for LiteLLM data.

    LiteLLM ``*_above_200k_tokens`` rates are marginal per token type. Rates only
    published above 272k (the models.dev tier ccusage uses) switch the whole
    request. OpenAI models always switch, as in ccusage's Codex adapter.
    """
    openai = _uses_codex_billing(pricing)
    inp = pricing.input_cost_per_token
    cache_read = pricing.cache_read_input_token_cost
    base = Rates(
        input=inp,
        output=pricing.output_cost_per_token,
        cache_creation=_or(pricing.cache_creation_input_token_cost, inp * 1.25),
        cache_read=_or(cache_read, inp if openai else inp * 0.1),
        cache_creation_1h=inp * _CACHE_CREATION_1H_INPUT_MULTIPLIER,
    )
    above_200k = (
        pricing.input_cost_per_token_above_200k_tokens,
        pricing.output_cost_per_token_above_200k_tokens,
        pricing.cache_creation_input_token_cost_above_200k_tokens,
        pricing.cache_read_input_token_cost_above_200k_tokens,
    )
    above_272k = (
        pricing.input_cost_per_token_above_272k_tokens,
        pricing.output_cost_per_token_above_272k_tokens,
        pricing.cache_creation_input_token_cost_above_272k_tokens,
        pricing.cache_read_input_token_cost_above_272k_tokens,
    )
    has_200k = any(r is not None for r in above_200k)
    has_272k = any(r is not None for r in above_272k)
    switch = openai or (has_272k and not has_200k)
    threshold, tier = (
        (_THRESHOLD_272K, above_272k)
        if switch and has_272k
        else (_THRESHOLD_200K, above_200k)
    )
    # No tier publishes a 1-hour rate; it is twice the tier's input rate.
    above = Rates(*(_or(a, b) for a, b in zip((*tier, None), base, strict=True)))
    above = above._replace(
        cache_creation_1h=above.input * _CACHE_CREATION_1H_INPUT_MULTIPLIER
    )
    if openai and cache_read is None:
        # Codex bills unpriced cache reads of long requests at the long input rate.
        above = above._replace(cache_read=above.input)
    return CostPlan(switch, threshold, base, above)


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


# ---------------------------------------------------------------------------
# Disk-cached pricing fetcher
# ---------------------------------------------------------------------------


def _parse_pricing_map(raw: object) -> dict[str, ModelPricing]:
    """Parse a raw JSON object into a dict of ModelPricing, dropping invalid entries."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ModelPricing] = {}
    for key, value in raw.items():
        try:
            result[key] = ModelPricing.model_validate(value)
        except ValidationError:
            continue
    return result


def _load_cache(cache_path: Path) -> dict[str, ModelPricing]:
    """Load and parse the cache file; return empty dict on any error."""
    try:
        raw = json.loads(cache_path.read_text())
        return _parse_pricing_map(raw)
    except Exception:
        return {}


def _write_cache_atomic(cache_path: Path, data: dict) -> None:
    """Atomically write *data* as JSON to *cache_path* via a temp file + rename."""
    fd, tmp_name = tempfile.mkstemp(dir=cache_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp_name, cache_path)
    except Exception:
        # Best-effort cleanup of temp file on failure.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def fetch_pricing(
    *,
    url: str,
    cache_path: Path,
    ttl_seconds: int,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> dict[str, ModelPricing]:
    """Fetch liteLLM pricing JSON with a disk cache and TTL.

    Parameters
    ----------
    url:
        URL of the upstream pricing JSON
        (e.g. liteLLM's model_prices_and_context_window.json).
    cache_path:
        Path to the local cache file.
    ttl_seconds:
        Age (in seconds) after which the cache is considered stale.
    client:
        Optional pre-built ``httpx.Client``.  Pass one with a
        ``MockTransport`` in tests; leave *None* in production.
    now:
        Current time for TTL calculation.  Defaults to ``datetime.now(UTC)``.

    Returns
    -------
    dict[str, ModelPricing]
        Parsed pricing map keyed by model name.  Returns ``{}`` on error
        when no cache is available.
    """
    if now is None:
        now = datetime.now(UTC)

    # Check if the cache is fresh enough to use without hitting the network.
    if cache_path.exists():
        age = now.timestamp() - cache_path.stat().st_mtime
        if age < ttl_seconds:
            return _load_cache(cache_path)

    # Need to fetch from network.
    _own_client = client is None
    if _own_client:
        client = httpx.Client(timeout=10.0)

    try:
        response = client.get(url)
        response.raise_for_status()
        raw = response.json()
        pricing_map = _parse_pricing_map(raw)
        if not pricing_map:
            raise ValueError("response holds no usable pricing entries")
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        # A failed download never replaces the cache with an error body.
        _log.warning("Failed to fetch pricing from %s: %s", url, exc)
        return _load_cache(cache_path) if cache_path.exists() else {}
    finally:
        if _own_client:
            client.close()  # type: ignore[union-attr]

    # Atomic write: persist the raw JSON (not the parsed map) so all original
    # fields are preserved for future reads.
    try:
        _write_cache_atomic(cache_path, raw)
    except Exception as exc:
        _log.warning("Failed to write pricing cache to %s: %s", cache_path, exc)

    return pricing_map
