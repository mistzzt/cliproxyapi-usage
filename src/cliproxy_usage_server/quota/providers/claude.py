"""Claude OAuth quota provider."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal

from cliproxy_usage_server.quota.errors import QuotaSchemaError
from cliproxy_usage_server.schemas import ProviderQuota, QuotaWindow

# Keys that always appear as non-window special values.
_WINDOW_LABELS: dict[str, str] = {
    "five_hour": "Five Hour",
    "seven_day": "Seven Day",
    "seven_day_oauth_apps": "Seven Day (OAuth Apps)",
    "seven_day_opus": "Seven Day (Opus)",
    "seven_day_sonnet": "Seven Day (Sonnet)",
    "seven_day_cowork": "Seven Day (Cowork)",
    "seven_day_omelette": "Seven Day (Omelette)",
    "iguana_necktie": "Iguana Necktie",
    "omelette_promotional": "Omelette Promotional",
}

_EXTRA_KEYS = {"extra_usage"}


def _key_to_label(key: str) -> str:
    """Convert a snake_case key to a title-cased label."""
    return _WINDOW_LABELS.get(key, key.replace("_", " ").title())


def _is_window_shape(value: object) -> bool:
    """Return True if value looks like a quota window dict.

    Requires:
      - value is a dict
      - has a numeric 'utilization' key
      - has a 'resets_at' key (value may be null/None or a string)
    """
    if not isinstance(value, dict):
        return False
    if "resets_at" not in value:
        return False
    utilization = value.get("utilization")
    return isinstance(utilization, (int, float))


def _parse_window(key: str, value: dict[str, object]) -> QuotaWindow:
    utilization = float(value["utilization"])  # type: ignore[arg-type]
    # Normalize: if 0 <= utilization <= 1, it's a fraction — convert to percent.
    # Values > 1 are already percent-scale.
    if utilization > 1 or utilization < 0:
        used_percent = utilization
    else:
        used_percent = utilization * 100

    resets_at_raw = value.get("resets_at")
    if resets_at_raw is None:
        resets_at: datetime | None = None
    else:
        resets_at = datetime.fromisoformat(str(resets_at_raw))

    return QuotaWindow(
        id=key,
        label=_key_to_label(key),
        used_percent=used_percent,
        resets_at=resets_at,
    )


class ClaudeProvider:
    """Quota provider for the Anthropic Claude API."""

    provider_id: ClassVar[Literal["claude", "codex"]] = "claude"
    auth_type: ClassVar[str] = "oauth"

    def build_api_call_payload(self, auth_name: str) -> dict[str, object]:
        return {
            "authIndex": auth_name,
            "method": "GET",
            "url": "https://api.anthropic.com/api/oauth/usage",
            "header": {
                "Authorization": "Bearer $TOKEN$",
                "anthropic-beta": "oauth-2025-04-20",
            },
        }

    def parse(
        self, upstream_body: object, upstream_status: int, *, auth_name: str
    ) -> ProviderQuota:
        if not isinstance(upstream_body, dict):
            raise QuotaSchemaError(
                "Expected a dict for Claude quota body, "
                f"got {type(upstream_body).__name__}"
            )

        windows: list[QuotaWindow] = []
        extra: dict[str, object] = {}

        for key, value in upstream_body.items():
            # Collect known extra blocks
            if key in _EXTRA_KEYS:
                extra[key] = value
                continue

            # Skip null values (provider signals "not applicable")
            if value is None:
                continue

            # Accept any dict whose shape matches a window
            if _is_window_shape(value):
                windows.append(_parse_window(key, value))  # type: ignore[arg-type]

        return ProviderQuota(
            provider="claude",
            auth_name=auth_name,
            plan_type=None,
            windows=windows,
            extra=extra,
        )
