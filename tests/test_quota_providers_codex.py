"""Tests for the Codex quota provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cliproxy_usage_server.quota.errors import QuotaSchemaError
from cliproxy_usage_server.quota.providers.codex import CodexProvider


def _codex_pro_body() -> dict:  # type: ignore[type-arg]
    return {
        "email": "codex-user@example.test",
        "plan_type": "pro",
        "rate_limit": {
            "primary_window": {
                "used_percent": 0,
                "limit_window_seconds": 18000,
                "reset_at": 1779510272,
            },
            "secondary_window": {
                "used_percent": 1,
                "limit_window_seconds": 604800,
                "reset_at": 1780035598,
            },
        },
        "additional_rate_limits": [
            {
                "limit_name": "GPT-5.3-Codex-Spark",
                "metered_feature": "codex_bengalfox",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 0,
                        "limit_window_seconds": 18000,
                        "reset_at": 1779510272,
                    },
                    "secondary_window": {
                        "used_percent": 0,
                        "limit_window_seconds": 604800,
                        "reset_at": 1780097072,
                    },
                },
            }
        ],
    }


def _codex_team_body() -> dict:  # type: ignore[type-arg]
    return {
        "email": "chatgpt-team@example.test",
        "plan_type": "team",
        "rate_limit": {
            "primary_window": {"used_percent": 1, "reset_at": 1779510272},
            "secondary_window": {"used_percent": 100, "reset_at": 1779847646},
        },
        "additional_rate_limits": None,
    }


def test_build_api_call_payload_has_expected_fields() -> None:
    payload = CodexProvider().build_api_call_payload("codex.json")
    assert payload["url"] == "https://chatgpt.com/backend-api/wham/usage"
    assert payload["method"] == "GET"
    assert payload["authIndex"] == "codex.json"
    header = payload["header"]
    assert isinstance(header, dict)
    assert header["Authorization"] == "Bearer $TOKEN$"
    assert "User-Agent" in header
    assert str(header["User-Agent"]).startswith("codex_cli_rs/")


def test_build_reset_payload_has_unique_redemption_and_optional_account() -> None:
    provider = CodexProvider()
    first = provider.build_reset_api_call_payload("auth-123", account_id="acct-1")
    second = provider.build_reset_api_call_payload("auth-123", account_id=None)

    assert first["authIndex"] == "auth-123"
    assert first["method"] == "POST"
    assert (
        first["url"]
        == "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume"
    )
    first_headers = first["header"]
    assert isinstance(first_headers, dict)
    assert first_headers["Authorization"] == "Bearer $TOKEN$"
    assert first_headers["Content-Type"] == "application/json"
    assert first_headers["Chatgpt-Account-Id"] == "acct-1"
    second_headers = second["header"]
    assert isinstance(second_headers, dict)
    assert "Chatgpt-Account-Id" not in second_headers
    assert (
        json.loads(str(first["data"]))["redeem_request_id"]
        != json.loads(str(second["data"]))["redeem_request_id"]
    )


def test_parse_extracts_primary_and_secondary_windows(
    codex_api_call_fixture: dict,  # type: ignore[type-arg]
) -> None:
    body_dict = json.loads(codex_api_call_fixture["body"])
    result = CodexProvider().parse(body_dict, 200, auth_name="codex.json")

    assert result.provider == "codex"
    assert result.auth_name == "codex.json"
    assert result.plan_type == "team"

    assert {w.id for w in result.windows} == {"primary", "secondary"}

    primary = next(w for w in result.windows if w.id == "primary")
    assert primary.used_percent == 0.0
    assert primary.resets_at == datetime.fromtimestamp(1777017720, tz=UTC)

    secondary = next(w for w in result.windows if w.id == "secondary")
    assert secondary.used_percent == 47.0
    assert secondary.resets_at == datetime.fromtimestamp(1777410854, tz=UTC)


def test_parse_handles_additional_rate_limits() -> None:
    body = {
        "plan_type": "pro",
        "rate_limit": None,
        "additional_rate_limits": [
            {"limit_name": "weekly_opus", "used_percent": 30, "reset_at": 1777410854},
            {"limit_name": "daily_sonnet", "used_percent": 10, "reset_at": 1777017720},
        ],
    }
    result = CodexProvider().parse(body, 200, auth_name="codex.json")
    assert {w.id for w in result.windows} == {
        "additional:weekly_opus",
        "additional:daily_sonnet",
    }

    weekly = next(w for w in result.windows if w.id == "additional:weekly_opus")
    assert weekly.label == "weekly_opus limit"
    assert weekly.used_percent == 30.0

    daily = next(w for w in result.windows if w.id == "additional:daily_sonnet")
    assert daily.label == "daily_sonnet limit"
    assert daily.used_percent == 10.0


def test_parse_handles_codex_pro_nested_additional_rate_limits_from_draft() -> None:
    body = _codex_pro_body()
    result = CodexProvider().parse(body, 200, auth_name="codex.json")

    assert result.plan_type == "pro"
    assert result.extra["email"] == "codex-user@example.test"
    assert {w.id for w in result.windows} == {
        "primary",
        "secondary",
        "additional:GPT-5.3-Codex-Spark:primary",
        "additional:GPT-5.3-Codex-Spark:secondary",
    }

    primary = next(
        w for w in result.windows if w.id == "additional:GPT-5.3-Codex-Spark:primary"
    )
    assert primary.label == "GPT-5.3-Codex-Spark 5-hour limit"
    assert primary.used_percent == 0.0
    assert primary.resets_at == datetime.fromtimestamp(1779510272, tz=UTC)

    secondary = next(
        w for w in result.windows if w.id == "additional:GPT-5.3-Codex-Spark:secondary"
    )
    assert secondary.label == "GPT-5.3-Codex-Spark Weekly limit"
    assert secondary.used_percent == 0.0
    assert secondary.resets_at == datetime.fromtimestamp(1780097072, tz=UTC)


def test_parse_handles_codex_team_draft_without_additional_rate_limits() -> None:
    body = _codex_team_body()
    result = CodexProvider().parse(body, 200, auth_name="team.json")

    assert result.plan_type == "team"
    assert result.extra["email"] == "chatgpt-team@example.test"
    assert {w.id for w in result.windows} == {"primary", "secondary"}

    secondary = next(w for w in result.windows if w.id == "secondary")
    assert secondary.used_percent == 100.0
    assert secondary.resets_at == datetime.fromtimestamp(1779847646, tz=UTC)


def test_parse_surfaces_email_in_extra(
    codex_api_call_fixture: dict,  # type: ignore[type-arg]
) -> None:
    body_dict = json.loads(codex_api_call_fixture["body"])
    result = CodexProvider().parse(body_dict, 200, auth_name="codex.json")
    assert result.extra["email"] == "user@example.com"


def test_parse_raises_schema_error_on_garbage() -> None:
    with pytest.raises(QuotaSchemaError):
        CodexProvider().parse(42, 200, auth_name="codex.json")


def test_parse_weekly_primary_windows_and_manual_resets(
    codex_weekly_only_api_call_fixture: dict,  # type: ignore[type-arg]
) -> None:
    body = json.loads(codex_weekly_only_api_call_fixture["body"])
    result = CodexProvider().parse(body, 200, auth_name="codex.json")

    assert [window.label for window in result.windows] == [
        "Weekly limit",
        "GPT-5 Codex Weekly limit",
    ]
    assert result.manual_resets is not None
    assert result.manual_resets.available_count == 2


@pytest.mark.parametrize("key", ["primary_window", "secondary_window"])
def test_parse_uses_duration_in_either_window_position(key: str) -> None:
    other = "secondary_window" if key == "primary_window" else "primary_window"
    body = {
        "rate_limit": {
            key: {"used_percent": 1, "limit_window_seconds": 604800, "reset_at": 1},
            other: {"used_percent": 2, "limit_window_seconds": 18000, "reset_at": 2},
        }
    }
    result = CodexProvider().parse(body, 200, auth_name="codex.json")
    labels = {window.id: window.label for window in result.windows}
    assert labels[key.removesuffix("_window")] == "Weekly limit"
    assert labels[other.removesuffix("_window")] == "5-hour limit"


@pytest.mark.parametrize("duration", [None, 3600])
def test_parse_unknown_duration_uses_neutral_labels(duration: int | None) -> None:
    window = {"used_percent": 1, "reset_at": 1}
    if duration is not None:
        window["limit_window_seconds"] = duration
    body = {
        "rate_limit": {"primary_window": window},
        "additional_rate_limits": [
            {"limit_name": "Spark", "rate_limit": {"primary_window": window}}
        ],
    }
    result = CodexProvider().parse(body, 200, auth_name="codex.json")
    assert [item.label for item in result.windows] == ["Account limit", "Spark limit"]


@pytest.mark.parametrize("count", [0, 3])
def test_parse_non_negative_manual_reset_count(count: int) -> None:
    result = CodexProvider().parse(
        {"rate_limit_reset_credits": {"available_count": count}},
        200,
        auth_name="codex.json",
    )
    assert result.manual_resets is not None
    assert result.manual_resets.available_count == count
