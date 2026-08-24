"""Codex OAuth quota provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar, Literal
from uuid import uuid4

from cliproxy_usage_server.quota.errors import QuotaSchemaError
from cliproxy_usage_server.schemas import ManualResetSummary, ProviderQuota, QuotaWindow

_USER_AGENT = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
_FIVE_HOURS = 18_000
_WEEK = 604_800


def _window_label(limit_name: str | None, duration: object) -> str:
    if duration == _FIVE_HOURS:
        return f"{limit_name} 5-hour limit" if limit_name else "5-hour limit"
    if duration == _WEEK:
        return f"{limit_name} Weekly limit" if limit_name else "Weekly limit"
    return f"{limit_name} limit" if limit_name else "Account limit"


def _window_from_raw(
    window_id: str,
    label: str,
    used_percent: object,
    reset_at: object,
) -> QuotaWindow:
    if not isinstance(used_percent, (int, float)):
        raise QuotaSchemaError(
            f"Expected numeric used_percent for Codex window '{window_id}'"
        )
    if not isinstance(reset_at, (int, float)):
        raise QuotaSchemaError(
            f"Expected numeric reset_at for Codex window '{window_id}'"
        )

    return QuotaWindow(
        id=window_id,
        label=label,
        used_percent=float(used_percent),
        resets_at=datetime.fromtimestamp(reset_at, tz=UTC),
    )


def _append_window_from_rate_limit(
    windows: list[QuotaWindow],
    *,
    window_id: str,
    limit_name: str | None,
    rate_limit: dict[object, object],
    key: str,
) -> None:
    raw_window = rate_limit.get(key)
    if not isinstance(raw_window, dict):
        return

    windows.append(
        _window_from_raw(
            window_id,
            _window_label(limit_name, raw_window.get("limit_window_seconds")),
            raw_window.get("used_percent"),
            raw_window.get("reset_at"),
        )
    )


class CodexProvider:
    """Quota provider for the OpenAI Codex API (chatgpt.com)."""

    provider_id: ClassVar[Literal["claude", "codex"]] = "codex"
    auth_type: ClassVar[str] = "codex"

    def build_api_call_payload(self, auth_name: str) -> dict[str, object]:
        return {
            "authIndex": auth_name,
            "method": "GET",
            "url": "https://chatgpt.com/backend-api/wham/usage",
            "header": {
                "Authorization": "Bearer $TOKEN$",
                "User-Agent": _USER_AGENT,
            },
        }

    def build_reset_api_call_payload(
        self, auth_name: str, *, account_id: str | None
    ) -> dict[str, object]:
        headers = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        }
        if account_id is not None:
            headers["Chatgpt-Account-Id"] = account_id
        return {
            "authIndex": auth_name,
            "method": "POST",
            "url": "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume",
            "header": headers,
            "data": json.dumps(
                {"redeem_request_id": str(uuid4())}, separators=(",", ":")
            ),
        }

    def parse(
        self, upstream_body: object, upstream_status: int, *, auth_name: str
    ) -> ProviderQuota:
        if not isinstance(upstream_body, dict):
            raise QuotaSchemaError(
                "Expected a dict for Codex quota body, "
                f"got {type(upstream_body).__name__}"
            )

        windows: list[QuotaWindow] = []
        extra: dict[str, object] = {}

        # Extract plan_type
        plan_type = upstream_body.get("plan_type")
        plan_type_str = str(plan_type) if plan_type is not None else None

        # Extract email into extra
        email = upstream_body.get("email")
        if email is not None:
            extra["email"] = email

        # Parse primary/secondary windows from rate_limit
        rate_limit = upstream_body.get("rate_limit")
        if isinstance(rate_limit, dict):
            _append_window_from_rate_limit(
                windows,
                window_id="primary",
                limit_name=None,
                rate_limit=rate_limit,
                key="primary_window",
            )
            _append_window_from_rate_limit(
                windows,
                window_id="secondary",
                limit_name=None,
                rate_limit=rate_limit,
                key="secondary_window",
            )

        # Parse additional_rate_limits
        additional = upstream_body.get("additional_rate_limits")
        if isinstance(additional, list):
            for entry in additional:
                if not isinstance(entry, dict):
                    continue
                limit_name = entry.get("limit_name")
                if not isinstance(limit_name, str):
                    continue

                if "used_percent" in entry or "reset_at" in entry:
                    windows.append(
                        _window_from_raw(
                            f"additional:{limit_name}",
                            _window_label(
                                limit_name, entry.get("limit_window_seconds")
                            ),
                            entry.get("used_percent"),
                            entry.get("reset_at"),
                        )
                    )
                    continue

                additional_rate_limit = entry.get("rate_limit")
                if isinstance(additional_rate_limit, dict):
                    _append_window_from_rate_limit(
                        windows,
                        window_id=f"additional:{limit_name}:primary",
                        limit_name=limit_name,
                        rate_limit=additional_rate_limit,
                        key="primary_window",
                    )
                    _append_window_from_rate_limit(
                        windows,
                        window_id=f"additional:{limit_name}:secondary",
                        limit_name=limit_name,
                        rate_limit=additional_rate_limit,
                        key="secondary_window",
                    )

        manual_resets = None
        raw_reset_credits = upstream_body.get("rate_limit_reset_credits")
        if isinstance(raw_reset_credits, dict):
            available_count = raw_reset_credits.get("available_count")
            if (
                isinstance(available_count, int)
                and not isinstance(available_count, bool)
                and available_count >= 0
            ):
                manual_resets = ManualResetSummary(available_count=available_count)

        return ProviderQuota(
            provider="codex",
            auth_name=auth_name,
            plan_type=plan_type_str,
            windows=windows,
            manual_resets=manual_resets,
            extra=extra,
        )
