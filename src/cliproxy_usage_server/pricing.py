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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "PREFIX_CANDIDATES",
    "CostPlan",
    "CostStatus",
    "ModelPricing",
    "PricingResolution",
    "ProviderEntry",
    "Rates",
    "TokenCounts",
    "compute_cost",
    "cost_plan",
    "fetch_pricing",
    "resolve",
    "rollup_cost_status",
    "split_tokens_for_cost",
]

PricingResolution = Literal["live", "missing"]
CostStatus = Literal["live", "partial_missing", "missing"]

_OPENAI_LITELLM_PROVIDERS: frozenset[str] = frozenset(
    {
        "openai",
        "azure",
    }
)


def _uses_openai_token_accounting(pricing: ModelPricing) -> bool:
    """Return True when cached input is reported as a subset of input tokens."""
    provider = (pricing.litellm_provider or "").strip().lower()
    return provider in _OPENAI_LITELLM_PROVIDERS


def split_tokens_for_cost(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
) -> TokenCounts:
    """Return TokenCounts ready for compute_cost.

    For OpenAI-convention providers cached_tokens is treated as a subset of
    input_tokens. This mirrors ccusage's Codex cost split and OpenAI Responses
    token accounting.

    For all other sources the values pass through: cached_tokens flow into
    cache_read_input_tokens and input_tokens stays as the already-uncached
    count (Anthropic / Gemini convention).
    """
    if _uses_openai_token_accounting(pricing):
        cache_read = min(cached_tokens, input_tokens)
        return {
            "input_tokens": max(input_tokens - cache_read, 0),
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
        }
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cached_tokens,
    }


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


class ProviderEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    fast: float | None = None


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
    provider_specific_entry: ProviderEntry | None = None


class TokenCounts(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


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


def rollup_cost_status(statuses: list[PricingResolution]) -> CostStatus:
    """Roll up a list of per-component statuses into one row-level CostStatus.

    - empty list           -> "missing" (no components contribute)
    - all "live"           -> "live"
    - all "missing"        -> "missing"
    - mix of live + missing -> "partial_missing"
    """
    if not statuses:
        return "missing"
    has_live = any(s == "live" for s in statuses)
    has_miss = any(s == "missing" for s in statuses)
    if has_live and has_miss:
        return "partial_missing"
    return "live" if has_live else "missing"


class Rates(NamedTuple):
    """USD per token for each token type."""

    input: float
    output: float
    cache_creation: float
    cache_read: float


Amounts = tuple[int, int, int, int]  # token counts in ``Rates`` field order


class CostPlan(NamedTuple):
    """How one model bills a request, following ccusage."""

    openai: bool  # cached tokens are reported inside input_tokens
    # True: past the threshold the whole request bills at `above`.
    # False: only each token type's excess over the threshold does.
    switch: bool
    threshold: int
    base: Rates
    above: Rates

    def tier(self, amounts: Amounts) -> tuple[Amounts, Amounts]:
        """Split one request's split-form token counts into (base-rate, above-rate)."""
        none: Amounts = (0, 0, 0, 0)
        if self.switch:
            inp, _, cache_creation, cache_read = amounts
            long = inp + cache_creation + cache_read > self.threshold
            return (none, amounts) if long else (amounts, none)
        i, o, cc, cr = (min(n, self.threshold) for n in amounts)
        base: Amounts = (i, o, cc, cr)
        return base, (amounts[0] - i, amounts[1] - o, amounts[2] - cc, amounts[3] - cr)

    def price(self, base: Amounts, above: Amounts) -> float:
        """Cost of token sums billed at the base and above rates."""
        return sum(n * r for n, r in zip(base, self.base, strict=True)) + sum(
            n * r for n, r in zip(above, self.above, strict=True)
        )


def cost_plan(pricing: ModelPricing) -> CostPlan:
    """Derive the billing plan for *pricing* as ccusage does for LiteLLM data.

    LiteLLM ``*_above_200k_tokens`` rates are marginal per token type. Rates only
    published above 272k (the models.dev tier ccusage uses) switch the whole
    request. OpenAI-accounting models always switch, as in ccusage's Codex adapter.
    """
    openai = _uses_openai_token_accounting(pricing)
    inp = pricing.input_cost_per_token
    cache_read = pricing.cache_read_input_token_cost
    base = Rates(
        input=inp,
        output=pricing.output_cost_per_token,
        cache_creation=_or(pricing.cache_creation_input_token_cost, inp * 1.25),
        cache_read=_or(cache_read, inp if openai else inp * 0.1),
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
    above = Rates(*(_or(a, b) for a, b in zip(tier, base, strict=True)))
    if openai and cache_read is None:
        # Codex bills unpriced cache reads of long requests at the long input rate.
        above = above._replace(cache_read=above.input)
    return CostPlan(openai, switch, threshold, base, above)


def _or(value: float | None, default: float) -> float:
    return default if value is None else value


def compute_cost(
    tokens: TokenCounts,
    pricing: ModelPricing,
    *,
    speed: Literal["standard", "fast"] = "standard",
) -> float:
    """Return the USD cost of one request's split-form *tokens* given *pricing*.

    Tiers apply per request (see ``cost_plan``); never pass summed counts. When
    speed='fast' and pricing.provider_specific_entry.fast is set, multiplies the
    total by that value.
    """
    plan = cost_plan(pricing)
    amounts: Amounts = (
        tokens.get("input_tokens", 0),
        tokens.get("output_tokens", 0),
        tokens.get("cache_creation_input_tokens", 0),
        tokens.get("cache_read_input_tokens", 0),
    )
    total = plan.price(*plan.tier(amounts))

    multiplier = 1.0
    if (
        speed == "fast"
        and pricing.provider_specific_entry is not None
        and pricing.provider_specific_entry.fast is not None
    ):
        multiplier = pricing.provider_specific_entry.fast

    return total * multiplier


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
        raw = response.json()
    except Exception as exc:
        _log.warning("Failed to fetch pricing from %s: %s", url, exc)
        if cache_path.exists():
            return _load_cache(cache_path)
        return {}
    finally:
        if _own_client:
            client.close()  # type: ignore[union-attr]

    pricing_map = _parse_pricing_map(raw)

    # Atomic write: persist the raw JSON (not the parsed map) so all original
    # fields are preserved for future reads.
    try:
        _write_cache_atomic(cache_path, raw if isinstance(raw, dict) else {})
    except Exception as exc:
        _log.warning("Failed to write pricing cache to %s: %s", cache_path, exc)

    return pricing_map
