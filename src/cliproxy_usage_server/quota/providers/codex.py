"""Codex OAuth quota provider."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar, Literal

from cliproxy_usage_server.quota.errors import QuotaSchemaError
from cliproxy_usage_server.schemas import ProviderQuota, QuotaWindow

_USER_AGENT = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"


def _window_from_raw(
    window_id: str,
    label: str,
    used_percent: float,
    reset_at: int | float,
) -> QuotaWindow:
    return QuotaWindow(
        id=window_id,
        label=label,
        used_percent=float(used_percent),
        resets_at=datetime.fromtimestamp(reset_at, tz=UTC),
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
            primary_window = rate_limit.get("primary_window")
            if isinstance(primary_window, dict):
                windows.append(
                    _window_from_raw(
                        "primary",
                        "Primary 5h Window",
                        primary_window["used_percent"],  # type: ignore[arg-type]
                        primary_window["reset_at"],  # type: ignore[arg-type]
                    )
                )

            secondary_window = rate_limit.get("secondary_window")
            if isinstance(secondary_window, dict):
                windows.append(
                    _window_from_raw(
                        "secondary",
                        "Secondary 7d Window",
                        secondary_window["used_percent"],  # type: ignore[arg-type]
                        secondary_window["reset_at"],  # type: ignore[arg-type]
                    )
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
                windows.append(
                    _window_from_raw(
                        f"additional:{limit_name}",
                        limit_name,
                        entry["used_percent"],  # type: ignore[arg-type]
                        entry["reset_at"],  # type: ignore[arg-type]
                    )
                )

        return ProviderQuota(
            provider="codex",
            auth_name=auth_name,
            plan_type=plan_type_str,
            windows=windows,
            extra=extra,
        )
