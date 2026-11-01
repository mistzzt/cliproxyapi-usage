"""Per-request cost semantics ported from ccusage (cost.rs and the Codex adapter)."""

from __future__ import annotations

import math

import pytest

from cliproxy_usage_server.pricing import (
    ModelPricing,
    compute_cost,
    cost_plan,
    split_tokens_for_cost,
)

CLAUDE = ModelPricing(
    litellm_provider="anthropic",
    input_cost_per_token=3e-6,
    output_cost_per_token=15e-6,
    cache_read_input_token_cost=3e-7,
    input_cost_per_token_above_200k_tokens=6e-6,
    output_cost_per_token_above_200k_tokens=22.5e-6,
    cache_read_input_token_cost_above_200k_tokens=6e-7,
)
GPT_LONG = ModelPricing(
    litellm_provider="openai",
    input_cost_per_token=5e-6,
    output_cost_per_token=3e-5,
    cache_read_input_token_cost=5e-7,
    input_cost_per_token_above_272k_tokens=1e-5,
    output_cost_per_token_above_272k_tokens=4.5e-5,
    cache_read_input_token_cost_above_272k_tokens=1e-6,
)


def _cost(pricing: ModelPricing, inp: int, out: int, cached: int) -> float:
    """Cost of one request given the raw counts stored in the DB."""
    return compute_cost(split_tokens_for_cost(pricing, inp, out, cached), pricing)


def test_200k_tier_applies_per_token_type() -> None:
    # 150k input + 150k cache read is 300k combined, but neither type crosses 200k.
    assert math.isclose(
        _cost(CLAUDE, 150_000, 1_000, 150_000),
        150_000 * 3e-6 + 1_000 * 15e-6 + 150_000 * 3e-7,
    )
    assert math.isclose(_cost(CLAUDE, 250_000, 0, 0), 200_000 * 3e-6 + 50_000 * 6e-6)


@pytest.mark.parametrize(
    ("inp", "long"),
    [(272_000, False), (272_001, True)],
)
def test_openai_long_context_bills_whole_request(inp: int, long: bool) -> None:
    # Raw OpenAI input includes the 100k cached tokens; it alone picks the tier.
    rates = (1e-5, 4.5e-5, 1e-6) if long else (5e-6, 3e-5, 5e-7)
    expected = (inp - 100_000) * rates[0] + 2_000 * rates[1] + 100_000 * rates[2]
    assert math.isclose(_cost(GPT_LONG, inp, 2_000, 100_000), expected)


def test_openai_without_long_rates_is_flat() -> None:
    p = ModelPricing(
        litellm_provider="openai", input_cost_per_token=2e-6, output_cost_per_token=0
    )
    assert math.isclose(_cost(p, 500_000, 0, 0), 500_000 * 2e-6)


def test_272k_only_rates_switch_on_whole_context() -> None:
    p = GPT_LONG.model_copy(update={"litellm_provider": "xai"})
    plan = cost_plan(p)
    assert (plan.switch, plan.threshold) == (True, 272_000)
    # Uncached input + cache read = 300k context, so everything bills long.
    assert math.isclose(
        _cost(p, 100_000, 1_000, 200_000),
        100_000 * 1e-5 + 1_000 * 4.5e-5 + 200_000 * 1e-6,
    )


def test_missing_cache_rates_fall_back_like_ccusage() -> None:
    claude = cost_plan(
        ModelPricing(
            litellm_provider="anthropic",
            input_cost_per_token=1e-6,
            output_cost_per_token=1e-6,
        )
    )
    assert math.isclose(claude.base.cache_read, 1e-7)
    assert math.isclose(claude.base.cache_creation, 1.25e-6)

    gpt = cost_plan(GPT_LONG.model_copy(update={"cache_read_input_token_cost": None}))
    assert gpt.base.cache_read == 5e-6
    # Codex bills unpriced cache reads of long requests at the long input rate.
    assert gpt.above.cache_read == 1e-5


def test_missing_above_rate_falls_back_to_base() -> None:
    p = CLAUDE.model_copy(update={"output_cost_per_token_above_200k_tokens": None})
    assert math.isclose(_cost(p, 0, 300_000, 0), 300_000 * 15e-6)
