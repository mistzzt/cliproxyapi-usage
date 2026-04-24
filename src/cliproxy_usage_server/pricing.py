"""Pricing model, resolver, cost computation, and disk-cached fetcher.

Ports the liteLLM pricing logic from ccusage's pricing.ts into Python.
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
from typing import Literal, TypedDict

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

__all__ = [
    "PREFIX_CANDIDATES",
    "ModelPricing",
    "ProviderEntry",
    "TokenCounts",
    "compute_cost",
    "fetch_pricing",
    "resolve",
]

_log = logging.getLogger(__name__)

_TIERED_THRESHOLD = 200_000

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

    input_cost_per_token: float | None = None
    output_cost_per_token: float | None = None
    cache_creation_input_token_cost: float | None = None
    cache_read_input_token_cost: float | None = None
    input_cost_per_token_above_200k_tokens: float | None = None
    output_cost_per_token_above_200k_tokens: float | None = None
    cache_creation_input_token_cost_above_200k_tokens: float | None = None
    cache_read_input_token_cost_above_200k_tokens: float | None = None
    provider_specific_entry: ProviderEntry | None = None


class TokenCounts(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def resolve(
    model_name: str, pricing: Mapping[str, ModelPricing]
) -> ModelPricing | None:
    """Return the ModelPricing for *model_name* from *pricing*, or None.

    Match order:
    1. Exact key lookup.
    2. Each prefix in PREFIX_CANDIDATES prepended to model_name.
    3. First case-insensitive substring match (key contains name, or name
       contains key).
    """
    # 1. Exact match
    if model_name in pricing:
        return pricing[model_name]

    # 2. Prefix candidates
    for prefix in PREFIX_CANDIDATES:
        candidate = f"{prefix}{model_name}"
        if candidate in pricing:
            return pricing[candidate]

    # 3. Substring fallback (case-insensitive)
    lower = model_name.lower()
    for key, value in pricing.items():
        key_lower = key.lower()
        if key_lower in lower or lower in key_lower:
            return value

    return None


def _tiered_cost(
    total_tokens: int,
    base_price: float | None,
    tiered_price: float | None,
    threshold: int = _TIERED_THRESHOLD,
) -> float:
    """Compute cost with optional tiered pricing at *threshold*.

    Mirrors ccusage's calculateTieredCost:
    - If total_tokens <= threshold OR tiered_price is None → flat at base_price (or 0).
    - If total_tokens > threshold AND tiered_price is not None:
        - Tokens above threshold charged at tiered_price.
        - Tokens at or below threshold charged at base_price (if present), else 0.
    """
    if total_tokens <= 0:
        return 0.0

    if total_tokens > threshold and tiered_price is not None:
        above = total_tokens - threshold
        cost = above * tiered_price
        if base_price is not None:
            cost += threshold * base_price
        return cost

    # Flat (no tiered or below threshold)
    if base_price is not None:
        return total_tokens * base_price
    return 0.0


def compute_cost(
    tokens: TokenCounts,
    pricing: ModelPricing,
    *,
    speed: Literal["standard", "fast"] = "standard",
) -> float:
    """Return total USD cost for *tokens* given *pricing*.

    Applies tiered pricing at 200k where the *_above_200k_tokens fields are
    present.  When speed='fast' and pricing.provider_specific_entry.fast is
    set, multiplies the total by that value.
    """
    input_tokens = tokens.get("input_tokens", 0)
    output_tokens = tokens.get("output_tokens", 0)
    cache_creation = tokens.get("cache_creation_input_tokens", 0)
    cache_read = tokens.get("cache_read_input_tokens", 0)

    total = (
        _tiered_cost(
            input_tokens,
            pricing.input_cost_per_token,
            pricing.input_cost_per_token_above_200k_tokens,
        )
        + _tiered_cost(
            output_tokens,
            pricing.output_cost_per_token,
            pricing.output_cost_per_token_above_200k_tokens,
        )
        + _tiered_cost(
            cache_creation,
            pricing.cache_creation_input_token_cost,
            pricing.cache_creation_input_token_cost_above_200k_tokens,
        )
        + _tiered_cost(
            cache_read,
            pricing.cache_read_input_token_cost,
            pricing.cache_read_input_token_cost_above_200k_tokens,
        )
    )

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
