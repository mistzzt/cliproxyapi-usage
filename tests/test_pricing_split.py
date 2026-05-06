"""Unit tests for split_tokens_for_cost (provider-aware cached/input split)."""

from __future__ import annotations

import pytest

from cliproxy_usage_server.pricing import ModelPricing, split_tokens_for_cost


@pytest.mark.parametrize(
    "provider",
    [
        "openai",
        "OpenAI",
        "azure",
    ],
)
def test_openai_providers_subtract_cached_from_input(provider: str) -> None:
    """For OpenAI-convention providers cached_tokens is a subset of input_tokens."""
    out = split_tokens_for_cost(
        ModelPricing(litellm_provider=provider),
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=200,
    )
    assert out == {
        "input_tokens": 800,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }


def test_openai_cached_zero_passthrough() -> None:
    out = split_tokens_for_cost(ModelPricing(litellm_provider="openai"), 1000, 500, 0)
    assert out == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 0,
    }


def test_openai_cached_equal_input() -> None:
    out = split_tokens_for_cost(
        ModelPricing(litellm_provider="openai"), 1000, 500, 1000
    )
    assert out == {
        "input_tokens": 0,
        "output_tokens": 500,
        "cache_read_input_tokens": 1000,
    }


def test_openai_cached_exceeds_input_clamps() -> None:
    """Defensive: if upstream sends cached > input, clamp cache_read at input."""
    out = split_tokens_for_cost(
        ModelPricing(litellm_provider="openai"), 1000, 500, 2000
    )
    assert out == {
        "input_tokens": 0,
        "output_tokens": 500,
        "cache_read_input_tokens": 1000,
    }


@pytest.mark.parametrize(
    "provider", ["anthropic", "gemini", "openrouter", None]
)
def test_non_openai_providers_passthrough(provider: str | None) -> None:
    """Non-OpenAI providers keep cached_tokens in cache_read and input untouched."""
    out = split_tokens_for_cost(
        ModelPricing(litellm_provider=provider),
        input_tokens=1000,
        output_tokens=500,
        cached_tokens=200,
    )
    assert out == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }
