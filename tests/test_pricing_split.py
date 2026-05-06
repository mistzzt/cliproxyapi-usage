"""Unit tests for split_tokens_for_cost (ccusage-style cached/input split)."""

from __future__ import annotations

import pytest

from cliproxy_usage_server.pricing import split_tokens_for_cost


@pytest.mark.parametrize(
    "source",
    ["codex:user@gmail.com", "openai:sk-abc", "openai-compat:foo", "Codex:Bar", "OPENAI:baz"],
)
def test_openai_sources_subtract_cached_from_input(source: str) -> None:
    """For OpenAI-convention sources cached_tokens is a subset of input_tokens."""
    out = split_tokens_for_cost(source, input_tokens=1000, output_tokens=500, cached_tokens=200)
    assert out == {
        "input_tokens": 800,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }


def test_openai_cached_zero_passthrough() -> None:
    out = split_tokens_for_cost("codex:foo", 1000, 500, 0)
    assert out == {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0}


def test_openai_cached_equal_input() -> None:
    out = split_tokens_for_cost("codex:foo", 1000, 500, 1000)
    assert out == {"input_tokens": 0, "output_tokens": 500, "cache_read_input_tokens": 1000}


def test_openai_cached_exceeds_input_clamps() -> None:
    """Defensive: if upstream sends cached > input, clamp cache_read at input."""
    out = split_tokens_for_cost("codex:foo", 1000, 500, 2000)
    assert out == {"input_tokens": 0, "output_tokens": 500, "cache_read_input_tokens": 1000}


@pytest.mark.parametrize("source", ["claude:user@x.io", "anthropic:sk-ant", "gemini:foo", "openrouter:bar"])
def test_non_openai_sources_passthrough(source: str) -> None:
    """Non-OpenAI sources keep cached_tokens in cache_read and input untouched."""
    out = split_tokens_for_cost(source, input_tokens=1000, output_tokens=500, cached_tokens=200)
    assert out == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }


def test_empty_source_passthrough() -> None:
    out = split_tokens_for_cost("", 1000, 500, 200)
    assert out == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }
