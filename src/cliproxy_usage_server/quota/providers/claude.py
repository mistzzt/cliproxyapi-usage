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
    "omelette_promotional": "Omelette Promotional",
    "seven_day_fable": "7-day Fable 5",
}

_EXTRA_KEYS = {"extra_usage"}
_FABLE_WINDOW_ID = "seven_day_fable"
# Legacy top-level key that Anthropic used for the Fable window before limits[].
_LEGACY_FABLE_KEY = "iguana_necktie"
_FABLE_NAMES = {"fable", "fable 5"}


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
    resets_at_raw = value.get("resets_at")
    if resets_at_raw is None:
        resets_at: datetime | None = None
    else:
        resets_at = datetime.fromisoformat(str(resets_at_raw))

    return QuotaWindow(
        id=key,
        label=_key_to_label(key),
        used_percent=float(value["utilization"]),  # type: ignore[arg-type],
        resets_at=resets_at,
    )


def _fable_limit_window(limit: object) -> QuotaWindow | None:
    """Convert one limits[] entry into the Fable window; None if it doesn't qualify."""
    if not isinstance(limit, dict) or limit.get("kind") != "weekly_scoped":
        return None
    scope = limit.get("scope")
    model = scope.get("model") if isinstance(scope, dict) else None
    name = model.get("display_name") if isinstance(model, dict) else None
    if not isinstance(name, str) or name.lower() not in _FABLE_NAMES:
        return None
    shape: dict[str, object] = {
        "utilization": limit.get("percent"),
        "resets_at": limit.get("resets_at"),
    }
    if not _is_window_shape(shape):
        return None
    try:
        return _parse_window(_FABLE_WINDOW_ID, shape)
    except ValueError:
        return None


def _fable_window(upstream_body: dict[object, object]) -> QuotaWindow | None:
    """Prefer the active limits[] Fable entry, then the first one, then legacy key."""
    limits = upstream_body.get("limits")
    if isinstance(limits, list):
        found = [
            (limit, window)
            for limit in limits
            if isinstance(limit, dict)
            and (window := _fable_limit_window(limit)) is not None
        ]
        found.sort(key=lambda item: item[0].get("is_active") is not True)
        if found:
            return found[0][1]
    legacy = upstream_body.get(_LEGACY_FABLE_KEY)
    if _is_window_shape(legacy):
        return _parse_window(_FABLE_WINDOW_ID, legacy)  # type: ignore[arg-type]
    return None


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

            # Fable is handled separately (limits[] first, legacy key as fallback).
            if key in (_LEGACY_FABLE_KEY, "limits") or value is None:
                continue

            # Accept any dict whose shape matches a window
            if _is_window_shape(value):
                windows.append(_parse_window(key, value))  # type: ignore[arg-type]

        fable = _fable_window(upstream_body)
        if fable is not None:
            windows.append(fable)

        return ProviderQuota(
            provider="claude",
            auth_name=auth_name,
            plan_type=None,
            windows=windows,
            extra=extra,
        )
