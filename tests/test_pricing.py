"""Tests for cliproxy_usage_server.pricing."""

from __future__ import annotations

import json
import math
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from cliproxy_usage_server.pricing import (
    ModelPricing,
    TokenCounts,
    compute_cost,
    fetch_pricing,
    resolve,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "litellm-pricing-fixture.json"


@pytest.fixture(scope="module")
def raw_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture(scope="module")
def pricing_map(raw_fixture: dict[str, Any]) -> dict[str, ModelPricing]:
    return {key: ModelPricing.model_validate(val) for key, val in raw_fixture.items()}


# ---------------------------------------------------------------------------
# resolve() tests
# ---------------------------------------------------------------------------


def test_resolve_exact(pricing_map: dict[str, ModelPricing]) -> None:
    """Name present as-is → returns that entry."""
    result, _ = resolve("gpt-5", pricing_map)
    assert result is not None
    assert result.input_cost_per_token == 3e-6


def test_resolve_via_prefix(pricing_map: dict[str, ModelPricing]) -> None:
    """'claude-4-sonnet-20250514' → resolves to 'anthropic/claude-4-sonnet-20250514'."""
    result, _ = resolve("claude-4-sonnet-20250514", pricing_map)
    assert result is not None
    # Should be the anthropic entry (has tiered fields)
    assert result.input_cost_per_token_above_200k_tokens == 6e-6


def test_resolve_substring_fallback(pricing_map: dict[str, ModelPricing]) -> None:
    """Model name is a unique substring of a key → returns that entry."""
    # "gemini-2.5-flash" is in pricing_map; "gemini-2.5" should match it
    result, _ = resolve("gemini-2.5", pricing_map)
    assert result is not None
    assert result.input_cost_per_token == 1.25e-7


def test_resolve_missing(pricing_map: dict[str, ModelPricing]) -> None:
    """Unrelated name → None."""
    result, _ = resolve("totally-unknown-model-xyz", pricing_map)
    assert result is None


# ---------------------------------------------------------------------------
# compute_cost() tests
# ---------------------------------------------------------------------------


def test_compute_cost_flat(pricing_map: dict[str, ModelPricing]) -> None:
    """gpt-5 flat pricing: input=1000, output=500, cache_read=200."""
    pricing = pricing_map["gpt-5"]
    tokens: TokenCounts = {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }
    cost = compute_cost(tokens, pricing)
    assert pricing.input_cost_per_token is not None
    assert pricing.output_cost_per_token is not None
    assert pricing.cache_read_input_token_cost is not None
    expected = (
        1000 * pricing.input_cost_per_token
        + 500 * pricing.output_cost_per_token
        + 200 * pricing.cache_read_input_token_cost
    )
    assert math.isclose(cost, expected, rel_tol=1e-9)


def test_compute_cost_tiered_all_above_200k(
    raw_fixture: dict[str, Any], pricing_map: dict[str, ModelPricing]
) -> None:
    """300k/250k/300k/250k tokens with tiered pricing for claude-4-sonnet."""
    pricing = pricing_map["anthropic/claude-4-sonnet-20250514"]
    tokens: TokenCounts = {
        "input_tokens": 300_000,
        "output_tokens": 250_000,
        "cache_creation_input_tokens": 300_000,
        "cache_read_input_tokens": 250_000,
    }
    cost = compute_cost(tokens, pricing)

    p = raw_fixture["anthropic/claude-4-sonnet-20250514"]
    base_in = p["input_cost_per_token"]
    tier_in = p["input_cost_per_token_above_200k_tokens"]
    base_out = p["output_cost_per_token"]
    tier_out = p["output_cost_per_token_above_200k_tokens"]
    base_cc = p["cache_creation_input_token_cost"]
    tier_cc = p["cache_creation_input_token_cost_above_200k_tokens"]
    base_cr = p["cache_read_input_token_cost"]
    tier_cr = p["cache_read_input_token_cost_above_200k_tokens"]

    expected = (
        200_000 * base_in
        + 100_000 * tier_in
        + 200_000 * base_out
        + 50_000 * tier_out
        + 200_000 * base_cc
        + 100_000 * tier_cc
        + 200_000 * base_cr
        + 50_000 * tier_cr
    )
    assert math.isclose(cost, expected, rel_tol=1e-9)


def test_compute_cost_no_tiered_fallback(pricing_map: dict[str, ModelPricing]) -> None:
    """gpt-5 (no tiered fields) with 300k input + 250k output → flat pricing."""
    pricing = pricing_map["gpt-5"]
    tokens: TokenCounts = {
        "input_tokens": 300_000,
        "output_tokens": 250_000,
    }
    cost = compute_cost(tokens, pricing)
    assert pricing.input_cost_per_token is not None
    assert pricing.output_cost_per_token is not None
    expected = (
        300_000 * pricing.input_cost_per_token + 250_000 * pricing.output_cost_per_token
    )
    assert math.isclose(cost, expected, rel_tol=1e-9)


def test_compute_cost_200k_boundary(pricing_map: dict[str, ModelPricing]) -> None:
    """Exactly 200k uses base only; 200_001 charges 1 token at tiered rate."""
    pricing = pricing_map["anthropic/claude-4-sonnet-20250514"]
    assert pricing.input_cost_per_token is not None
    assert pricing.input_cost_per_token_above_200k_tokens is not None

    # Exactly 200k → base only
    tokens_at: TokenCounts = {"input_tokens": 200_000, "output_tokens": 0}
    cost_at = compute_cost(tokens_at, pricing)
    assert math.isclose(cost_at, 200_000 * pricing.input_cost_per_token, rel_tol=1e-9)

    # 200_001 → base for 200k + tiered for 1 token
    tokens_above: TokenCounts = {"input_tokens": 200_001, "output_tokens": 0}
    cost_above = compute_cost(tokens_above, pricing)
    expected = (
        200_000 * pricing.input_cost_per_token
        + 1 * pricing.input_cost_per_token_above_200k_tokens
    )
    assert math.isclose(cost_above, expected, rel_tol=1e-9)


def test_compute_cost_only_tiered_rates(pricing_map: dict[str, ModelPricing]) -> None:
    """theoretical-tiered-only: below 200k → 0; above 200k → only excess charged."""
    pricing = pricing_map["theoretical-tiered-only"]
    assert pricing.input_cost_per_token_above_200k_tokens is not None
    assert pricing.output_cost_per_token_above_200k_tokens is not None

    # Below threshold → 0
    tokens_below: TokenCounts = {"input_tokens": 100_000, "output_tokens": 100_000}
    assert compute_cost(tokens_below, pricing) == 0.0

    # Above threshold → only excess charged
    tokens_above: TokenCounts = {"input_tokens": 300_000, "output_tokens": 250_000}
    cost = compute_cost(tokens_above, pricing)
    expected = (
        100_000 * pricing.input_cost_per_token_above_200k_tokens
        + 50_000 * pricing.output_cost_per_token_above_200k_tokens
    )
    assert math.isclose(cost, expected, rel_tol=1e-9)


def test_compute_cost_fast_multiplier(pricing_map: dict[str, ModelPricing]) -> None:
    """Model with provider_specific_entry.fast=6.0 → speed='fast' gives 6x standard."""
    pricing = pricing_map["anthropic/claude-3-5-haiku-fast"]
    tokens: TokenCounts = {"input_tokens": 1000, "output_tokens": 500}

    standard = compute_cost(tokens, pricing, speed="standard")
    fast = compute_cost(tokens, pricing, speed="fast")
    assert math.isclose(fast, standard * 6.0, rel_tol=1e-9)


def test_compute_cost_speed_standard_no_multiplier(
    pricing_map: dict[str, ModelPricing],
) -> None:
    """speed='standard' equals no-speed (default)."""
    pricing = pricing_map["anthropic/claude-3-5-haiku-fast"]
    tokens: TokenCounts = {"input_tokens": 1000, "output_tokens": 500}

    default = compute_cost(tokens, pricing)
    standard = compute_cost(tokens, pricing, speed="standard")
    assert math.isclose(default, standard, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# fetch_pricing() tests
# ---------------------------------------------------------------------------

_SAMPLE_PAYLOAD = {
    "model-a": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6},
    "model-b": {"input_cost_per_token": 3e-6, "output_cost_per_token": 4e-6},
}


def _make_transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _make_client(handler) -> httpx.Client:
    return httpx.Client(transport=_make_transport(handler))


def _raising_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("boom")


def test_fetch_hits_network_on_first_call(tmp_path: Path) -> None:
    """No cache file → fetches from network; returns parsed map keyed by model name."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json=_SAMPLE_PAYLOAD)

    cache_path = tmp_path / "pricing.json"
    result = fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=3600,
        client=_make_client(handler),
    )

    assert len(calls) == 1
    assert "model-a" in result
    assert result["model-a"].input_cost_per_token == 1e-6
    assert "model-b" in result
    assert result["model-b"].output_cost_per_token == 4e-6


def test_fetch_uses_cache_when_fresh(tmp_path: Path) -> None:
    """Cache file present and within TTL → no network call; returns cache contents."""
    cache_path = tmp_path / "pricing.json"
    cache_path.write_text(json.dumps(_SAMPLE_PAYLOAD))

    # Make client that raises if called
    result = fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=3600,
        client=_make_client(_raising_handler),
        now=datetime.now(UTC),
    )

    assert result["model-a"].input_cost_per_token == 1e-6
    assert result["model-b"].input_cost_per_token == 3e-6


def test_fetch_refreshes_after_ttl(tmp_path: Path) -> None:
    """Stale cache (mtime > ttl ago) → network fetch; map reflects fresh data."""
    cache_path = tmp_path / "pricing.json"
    # Seed stale cache
    cache_path.write_text(json.dumps({"stale-model": {"input_cost_per_token": 1.0}}))
    ttl = 60
    stale_time = time.time() - ttl - 1
    os.utime(cache_path, (stale_time, stale_time))

    fresh_payload = {"fresh-model": {"input_cost_per_token": 5e-6}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=fresh_payload)

    result = fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=ttl,
        client=_make_client(handler),
        now=datetime.now(UTC),
    )

    assert "fresh-model" in result
    assert "stale-model" not in result
    # Cache should have been rewritten with fresh data
    on_disk = json.loads(cache_path.read_text())
    assert "fresh-model" in on_disk


def test_fetch_falls_back_to_cache_on_network_error(tmp_path: Path) -> None:
    """Cache present, network raises ConnectError → returns cache (ignores TTL)."""
    cache_path = tmp_path / "pricing.json"
    cache_path.write_text(json.dumps(_SAMPLE_PAYLOAD))
    # Make cache stale so we know TTL is ignored in fallback
    stale_time = time.time() - 99999
    os.utime(cache_path, (stale_time, stale_time))

    result = fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=60,
        client=_make_client(_raising_handler),
        now=datetime.now(UTC),
    )

    assert result["model-a"].input_cost_per_token == 1e-6


def test_fetch_returns_empty_on_error_without_cache(tmp_path: Path) -> None:
    """No cache, network raises → returns empty dict."""
    cache_path = tmp_path / "pricing.json"

    result = fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=3600,
        client=_make_client(_raising_handler),
    )

    assert result == {}


def test_fetch_atomic_write(tmp_path: Path) -> None:
    """After successful fetch, cache file exists with correct content; no .tmp files."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_SAMPLE_PAYLOAD)

    cache_path = tmp_path / "pricing.json"
    fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=3600,
        client=_make_client(handler),
    )

    assert cache_path.exists()
    on_disk = json.loads(cache_path.read_text())
    assert set(on_disk.keys()) >= {"model-a", "model-b"}
    # No leftover .tmp files in parent dir
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_fetch_drops_invalid_entries(tmp_path: Path) -> None:
    """Upstream JSON with one malformed entry: it is omitted; valid entries returned."""
    payload_with_bad = {
        "good-model": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6},
        # 'input_cost_per_token' expects float, not a string
        "bad-model": {"input_cost_per_token": "not-a-number"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload_with_bad)

    cache_path = tmp_path / "pricing.json"
    result = fetch_pricing(
        url="http://test.local/pricing.json",
        cache_path=cache_path,
        ttl_seconds=3600,
        client=_make_client(handler),
    )

    assert "good-model" in result
    assert "bad-model" not in result
