"""Per-request cost semantics ported from ccusage (cost.rs and the Codex adapter).

Each case prices one request through the production query (``request_cost``:
input with its cache reads and creation, output, cache read, cache creation, and
its 1-hour part) and compares it with a fixed, hand-checked dollar amount.
"""

from __future__ import annotations

import pytest
from conftest import request_cost

from cliproxy_usage_server.pricing import ModelPricing, cost_plan

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


def test_flat_rates() -> None:
    p = ModelPricing(
        input_cost_per_token=3e-6,
        output_cost_per_token=15e-6,
        cache_read_input_token_cost=3e-7,
    )
    assert request_cost(p, 1_200, 500, 200, 0) == pytest.approx(0.01056)


@pytest.mark.parametrize(
    ("inp", "expected"),
    [(200_000, 0.6), (200_001, 0.600006), (250_000, 0.9)],
)
def test_200k_tier_charges_only_the_excess(inp: int, expected: float) -> None:
    assert request_cost(CLAUDE, inp, 0, 0, 0) == pytest.approx(expected)


def test_200k_tier_applies_per_token_type() -> None:
    # 150k uncached + 150k cache read is 300k input, but neither type crosses 200k.
    assert request_cost(CLAUDE, 300_000, 1_000, 150_000, 0) == pytest.approx(0.51)


@pytest.mark.parametrize(("inp", "expected"), [(272_000, 0.97), (272_001, 1.91001)])
def test_openai_long_context_bills_whole_request(inp: int, expected: float) -> None:
    # Input includes the 100k cache reads; it alone picks the tier.
    assert request_cost(GPT_LONG, inp, 2_000, 100_000, 0) == pytest.approx(expected)


def test_openai_without_long_rates_is_flat() -> None:
    p = ModelPricing(
        litellm_provider="openai", input_cost_per_token=2e-6, output_cost_per_token=0
    )
    assert request_cost(p, 500_000, 0, 0, 0) == pytest.approx(1.0)


def test_272k_only_rates_switch_on_whole_context() -> None:
    p = GPT_LONG.model_copy(update={"litellm_provider": "xai"})
    plan = cost_plan(p)
    assert (plan.switch, plan.threshold) == (True, 272_000)
    # 100k uncached + 200k cache read is 300k input, which bills long.
    assert request_cost(p, 300_000, 1_000, 200_000, 0) == pytest.approx(1.245)


def test_missing_cache_rates_fall_back_like_ccusage() -> None:
    claude = ModelPricing(
        litellm_provider="anthropic", input_cost_per_token=1e-6, output_cost_per_token=0
    )
    assert request_cost(claude, 1_000, 0, 1_000, 0) == pytest.approx(0.0001)
    assert cost_plan(claude).base.cache_creation_5m == pytest.approx(1.25e-6)

    gpt = GPT_LONG.model_copy(update={"cache_read_input_token_cost": None})
    assert request_cost(gpt, 1_000, 0, 1_000, 0) == pytest.approx(0.005)
    # Codex bills unpriced cache reads of long requests at the long input rate.
    assert request_cost(gpt, 300_000, 0, 300_000, 0) == pytest.approx(3.0)


def test_missing_above_rate_falls_back_to_base() -> None:
    p = CLAUDE.model_copy(update={"output_cost_per_token_above_200k_tokens": None})
    assert request_cost(p, 0, 300_000, 0, 0) == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# Cache creation under each CostPlan shape
# ---------------------------------------------------------------------------


def test_cache_creation_flat_rate() -> None:
    p = ModelPricing(
        input_cost_per_token=3e-6,
        output_cost_per_token=15e-6,
        cache_read_input_token_cost=3e-7,
        cache_creation_input_token_cost=3.75e-6,
    )
    # 1000 uncached, 200 cache read and 300 cache creation make 1500 input.
    assert request_cost(p, 1_500, 500, 200, 300) == pytest.approx(0.011685)
    # 100 of the creation lasts an hour, at twice the input rate.
    assert request_cost(p, 1_500, 500, 200, 300, 100) == pytest.approx(0.01191)


@pytest.mark.parametrize(
    ("inp", "creation", "expected"),
    [
        # 50k of the 250k creation bills at the above rate; 10k uncached stays base.
        (260_000, 250_000, 1.155),
        # 150k uncached + 150k creation is 300k input, but neither type crosses 200k.
        (300_000, 150_000, 1.0125),
    ],
)
def test_cache_creation_200k_tier_charges_only_its_excess(
    inp: int, creation: int, expected: float
) -> None:
    p = CLAUDE.model_copy(
        update={
            "cache_creation_input_token_cost": 3.75e-6,
            "cache_creation_input_token_cost_above_200k_tokens": 7.5e-6,
        }
    )
    assert request_cost(p, inp, 0, 0, creation) == pytest.approx(expected)


@pytest.mark.parametrize(("inp", "expected"), [(272_000, 1.485), (272_001, 2.34501)])
def test_openai_cache_creation_counts_toward_the_whole_request_switch(
    inp: int, expected: float
) -> None:
    # 100k of the input is cache creation, at the 1.25x input fallback in both tiers.
    assert request_cost(GPT_LONG, inp, 0, 0, 100_000) == pytest.approx(expected)


def test_272k_only_rates_bill_cache_creation_at_the_long_rate() -> None:
    p = GPT_LONG.model_copy(
        update={
            "litellm_provider": "xai",
            "cache_creation_input_token_cost": 6.25e-6,
            "cache_creation_input_token_cost_above_272k_tokens": 1.25e-5,
        }
    )
    # 100k uncached + 200k creation is 300k input, which bills long.
    assert request_cost(p, 300_000, 0, 0, 200_000) == pytest.approx(3.5)


# ---------------------------------------------------------------------------
# 1-hour cache creation (2x the input rate of its tier) under each CostPlan shape
# ---------------------------------------------------------------------------


def test_cache_creation_1h_flat_rate() -> None:
    p = ModelPricing(input_cost_per_token=3e-6, output_cost_per_token=15e-6)
    assert request_cost(p, 100_000, 0, 0, 100_000, 100_000) == pytest.approx(0.6)


@pytest.mark.parametrize(
    ("creation", "creation_1h", "expected"),
    [
        # 50k of the 250k 1-hour creation bills at twice the above input rate.
        (250_000, 250_000, 1.8),
        # 150k 5-minute + 150k 1-hour creation: neither part crosses 200k.
        (300_000, 150_000, 1.4625),
    ],
)
def test_cache_creation_1h_200k_tier_charges_only_its_excess(
    creation: int, creation_1h: int, expected: float
) -> None:
    cost = request_cost(CLAUDE, creation, 0, 0, creation, creation_1h)
    assert cost == pytest.approx(expected)


@pytest.mark.parametrize(("inp", "expected"), [(272_000, 1.86), (272_001, 3.72001)])
def test_openai_cache_creation_1h_follows_the_whole_request_switch(
    inp: int, expected: float
) -> None:
    # 100k of the input is 1-hour creation, at twice the input rate of either tier.
    cost = request_cost(GPT_LONG, inp, 0, 0, 100_000, 100_000)
    assert cost == pytest.approx(expected)


def test_272k_only_rates_bill_cache_creation_1h_at_twice_the_long_input_rate() -> None:
    p = GPT_LONG.model_copy(update={"litellm_provider": "xai"})
    # 100k uncached + 200k 1-hour creation is 300k input, which bills long.
    assert request_cost(p, 300_000, 0, 0, 200_000, 200_000) == pytest.approx(5.0)
