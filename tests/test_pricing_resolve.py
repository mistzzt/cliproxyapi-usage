"""Tests for resolve() returning (entry, status) and cost_status rollups."""

from __future__ import annotations

from cliproxy_usage_server.pricing import (
    ModelPricing,
    PricingResolution,
    resolve,
    rollup_cost_status,
)

_E = ModelPricing(input_cost_per_token=1e-6, output_cost_per_token=1e-6)


def test_resolve_exact_match_returns_live() -> None:
    entry, status = resolve("gpt-5", {"gpt-5": _E})
    assert entry is _E
    assert status == "live"


def test_resolve_prefix_match_returns_live() -> None:
    entry, status = resolve("opus-4-5", {"anthropic/opus-4-5": _E})
    assert entry is _E
    assert status == "live"


def test_resolve_substring_match_returns_live() -> None:
    entry, status = resolve("claude-sonnet-4", {"anthropic/claude-sonnet-4-5": _E})
    assert entry is _E
    assert status == "live"


def test_resolve_missing_returns_none_missing() -> None:
    entry, status = resolve("totally-new-model", {"gpt-5": _E})
    assert entry is None
    assert status == "missing"


def test_resolve_empty_pricing_returns_missing() -> None:
    entry, status = resolve("anything", {})
    assert entry is None
    assert status == "missing"


def test_rollup_all_live() -> None:
    statuses: list[PricingResolution] = ["live", "live", "live"]
    assert rollup_cost_status(statuses) == "live"


def test_rollup_all_missing() -> None:
    statuses: list[PricingResolution] = ["missing", "missing"]
    assert rollup_cost_status(statuses) == "missing"


def test_rollup_mixed_is_partial_missing() -> None:
    statuses: list[PricingResolution] = ["live", "missing", "live"]
    assert rollup_cost_status(statuses) == "partial_missing"


def test_rollup_empty_is_missing() -> None:
    """A row with zero component models is treated as missing."""
    assert rollup_cost_status([]) == "missing"
