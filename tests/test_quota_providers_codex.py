"""Tests for the Codex quota provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from cliproxy_usage_server.quota.errors import QuotaSchemaError
from cliproxy_usage_server.quota.providers.codex import CodexProvider


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
    assert weekly.label == "weekly_opus"
    assert weekly.used_percent == 30.0

    daily = next(w for w in result.windows if w.id == "additional:daily_sonnet")
    assert daily.label == "daily_sonnet"
    assert daily.used_percent == 10.0


def test_parse_surfaces_email_in_extra(
    codex_api_call_fixture: dict,  # type: ignore[type-arg]
) -> None:
    body_dict = json.loads(codex_api_call_fixture["body"])
    result = CodexProvider().parse(body_dict, 200, auth_name="codex.json")
    assert result.extra["email"] == "user@example.com"


def test_parse_raises_schema_error_on_garbage() -> None:
    with pytest.raises(QuotaSchemaError):
        CodexProvider().parse(42, 200, auth_name="codex.json")
