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


def _fable_limit(
    percent: object = 64.0,
    *,
    resets_at: object = "2026-05-01T00:00:00+00:00",
    is_active: object = True,
    display_name: object = "Fable",
    kind: object = "weekly_scoped",
) -> dict[str, object]:
    return {
        "kind": kind,
        "group": "weekly",
        "percent": percent,
        "resets_at": resets_at,
        "is_active": is_active,
        "scope": {"model": {"id": None, "display_name": display_name}},
    }


_FIVE_HOUR = {"utilization": 17.0, "resets_at": "2026-04-24T04:30:00+00:00"}


def test_parse_emits_fable_row_from_limits_after_other_windows() -> None:
    body = {
        "five_hour": _FIVE_HOUR,
        "iguana_necktie": None,
        "limits": [_fable_limit()],
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")

    assert [w.id for w in result.windows] == ["five_hour", "seven_day_fable"]
    fable = result.windows[-1]
    assert fable.label == "7-day Fable 5"
    assert fable.used_percent == 64.0
    assert fable.resets_at == datetime(2026, 5, 1, tzinfo=UTC)
    assert "limits" not in result.extra


def test_parse_falls_back_to_legacy_fable_key() -> None:
    body = {
        "iguana_necktie": {"utilization": 30, "resets_at": "2026-05-02T00:00:00+00:00"},
        "five_hour": _FIVE_HOUR,
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")

    assert [w.id for w in result.windows] == ["five_hour", "seven_day_fable"]
    fable = result.windows[-1]
    assert fable.label == "7-day Fable 5"
    assert fable.used_percent == 30.0
    assert fable.resets_at == datetime(2026, 5, 2, tzinfo=UTC)


def test_parse_prefers_limits_over_legacy_without_duplicating() -> None:
    body = {
        "iguana_necktie": {"utilization": 30, "resets_at": None},
        "limits": [_fable_limit(64)],
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    fable_rows = [w for w in result.windows if w.id == "seven_day_fable"]
    assert len(fable_rows) == 1
    assert fable_rows[0].used_percent == 64.0


def test_parse_prefers_active_fable_limit() -> None:
    body = {
        "limits": [
            _fable_limit(10, is_active=False),
            _fable_limit(55, is_active=True, display_name="fable 5"),
            _fable_limit(90, is_active=False),
        ]
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    assert [w.used_percent for w in result.windows] == [55.0]


def test_parse_uses_first_fable_limit_when_none_active() -> None:
    body = {
        "limits": [_fable_limit(10, is_active=False), _fable_limit(20, is_active=None)]
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    assert [w.used_percent for w in result.windows] == [10.0]


def test_parse_fable_limit_null_resets_at_yields_none() -> None:
    body = {"limits": [_fable_limit(resets_at=None)]}
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    assert result.windows[0].id == "seven_day_fable"
    assert result.windows[0].resets_at is None


def test_parse_ignores_malformed_and_non_fable_limits() -> None:
    body = {
        "five_hour": _FIVE_HOUR,
        "limits": [
            _fable_limit(kind="five_hour_scoped"),
            _fable_limit(display_name="Opus"),
            _fable_limit(percent="64"),
            _fable_limit(percent=None),
            _fable_limit(resets_at="not-a-date"),
            {"kind": "weekly_scoped", "percent": 5},
            {"kind": "weekly_scoped", "percent": 5, "scope": "Fable"},
            "garbage",
            None,
        ],
    }
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    assert [w.id for w in result.windows] == ["five_hour"]
    assert "limits" not in result.extra


@pytest.mark.parametrize(
    "body",
    [
        {"five_hour": _FIVE_HOUR},
        {"five_hour": _FIVE_HOUR, "iguana_necktie": None},
        {"five_hour": _FIVE_HOUR, "iguana_necktie": None, "limits": []},
        {"five_hour": _FIVE_HOUR, "limits": "nope"},
    ],
)
def test_parse_emits_no_fable_row_without_any_source(body: dict[str, object]) -> None:
    result = ClaudeProvider().parse(body, 200, auth_name="test")
    assert [w.id for w in result.windows] == ["five_hour"]
