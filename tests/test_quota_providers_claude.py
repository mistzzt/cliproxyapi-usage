"""Tests for the Claude quota provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cliproxy_usage_server.quota.errors import QuotaSchemaError
from cliproxy_usage_server.quota.providers.claude import ClaudeProvider


def test_build_api_call_payload_has_expected_fields() -> None:
    payload = ClaudeProvider().build_api_call_payload("claude-zw.json")
    assert payload["authIndex"] == "claude-zw.json"
    assert payload["method"] == "GET"
    assert payload["url"] == "https://api.anthropic.com/api/oauth/usage"
    header = payload["header"]
    assert isinstance(header, dict)
    assert header["Authorization"] == "Bearer $TOKEN$"
    assert header["anthropic-beta"] == "oauth-2025-04-20"


def test_parse_extracts_populated_windows(
    claude_api_call_fixture: dict,  # type: ignore[type-arg]
) -> None:
    body_dict = json.loads(claude_api_call_fixture["body"])
    result = ClaudeProvider().parse(body_dict, 200, auth_name="claude-zw.json")

    assert result.provider == "claude"
    assert result.auth_name == "claude-zw.json"
    assert result.plan_type is None

    window_ids = {w.id for w in result.windows}
    assert window_ids == {
        "five_hour",
        "seven_day",
        "seven_day_sonnet",
        "seven_day_omelette",
    }

    five_hour = next(w for w in result.windows if w.id == "five_hour")
    assert five_hour.used_percent == 17.0
    expected_dt = datetime(2026, 4, 24, 4, 30, 0, 860373, tzinfo=UTC)
    assert five_hour.resets_at == expected_dt

    omelette = next(w for w in result.windows if w.id == "seven_day_omelette")
    assert omelette.resets_at is None


def test_parse_includes_extra_usage(
    claude_api_call_fixture: dict,  # type: ignore[type-arg]
) -> None:
    body_dict = json.loads(claude_api_call_fixture["body"])
    result = ClaudeProvider().parse(body_dict, 200, auth_name="claude-zw.json")

    extra_usage = result.extra["extra_usage"]
    assert isinstance(extra_usage, dict)
    assert extra_usage["currency"] == "USD"
    assert extra_usage["monthly_limit"] == 10000
    assert extra_usage["used_credits"] == 10151.0
    assert extra_usage["utilization"] == 100.0
    assert extra_usage["is_enabled"] is True


def test_parse_unknown_keys_are_ignored() -> None:
    body = {
        "five_hour": {"utilization": 1.0, "resets_at": "2026-01-01T00:00:00+00:00"},
        "mystery_window": {"utilization": 99, "resets_at": "2026-01-01T00:00:00+00:00"},
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    assert {w.id for w in result.windows} == {"five_hour", "mystery_window"}


def test_parse_raises_schema_error_on_garbage() -> None:
    with pytest.raises(QuotaSchemaError):
        ClaudeProvider().parse("not-a-dict", 200, auth_name="test")
