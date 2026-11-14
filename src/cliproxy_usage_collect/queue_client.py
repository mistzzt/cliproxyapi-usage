"""HTTP client for draining usage telemetry queue records."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from typing import Any

import httpx

from cliproxy_usage_collect.config import Config, management_url


class AuthError(Exception):
    """Raised when management API authentication or authorization fails."""


class TransientError(Exception):
    """Raised for retryable HTTP connection, protocol, or response failures."""


class UnsupportedVersionError(Exception):
    """Raised when CLIProxyAPI is older than the collector supports."""


HttpClientFactory = Callable[..., httpx.Client]

# The first release whose queue records carry `token_breakdown`.
MIN_PROXY_VERSION = (7, 2, 97)
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")


def pop_usage_records(
    cfg: Config,
    *,
    http_client_factory: HttpClientFactory = httpx.Client,
) -> list[dict[str, Any]]:
    """Check the CLIProxyAPI version, then pop one configured batch of raw records."""
    headers = {"Authorization": f"Bearer {cfg.management_key}"}

    try:
        with http_client_factory(timeout=cfg.http_timeout_seconds) as client:
            probe = client.get(
                management_url(cfg.base_url, "usage-statistics-enabled"),
                headers=headers,
            )
            _raise_for_status(probe)
            _check_version(probe.headers.get("X-CPA-VERSION", ""))
            response = client.get(
                management_url(cfg.base_url, "usage-queue"),
                params={"count": cfg.queue_pop_count},
                headers=headers,
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise TransientError(str(exc)) from exc

    _raise_for_status(response)

    try:
        payload = response.json()
    except ValueError as exc:
        raise TransientError("Usage queue response was not valid JSON") from exc

    return _normalize_payload(payload)


def parse_version(value: str) -> tuple[int, int, int] | None:
    """Return the leading `major.minor.patch` of a CLIProxyAPI version, if any."""
    match = _VERSION_RE.match(value)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _check_version(value: str) -> None:
    version = parse_version(value)
    required = ".".join(map(str, MIN_PROXY_VERSION))
    if version is None:
        print(
            f"Warning: cannot read the CLIProxyAPI version {value!r}; "
            f"v{required} or later is required",
            file=sys.stderr,
        )
    elif version < MIN_PROXY_VERSION:
        raise UnsupportedVersionError(
            f"CLIProxyAPI {value} is older than the required v{required}; "
            "upgrade CLIProxyAPI"
        )


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code in {401, 403}:
        raise AuthError(_response_message(response))
    if response.status_code >= 400:
        raise TransientError(_response_message(response))


def _response_message(response: httpx.Response) -> str:
    text = response.text.strip()
    if text:
        return f"HTTP {response.status_code}: {text}"
    return f"HTTP {response.status_code}"


def _normalize_payload(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise TransientError(
            f"Unexpected usage queue response type: {type(payload).__name__}"
        )
    if not all(isinstance(item, dict) for item in payload):
        raise TransientError("Usage queue response must be an array of objects")
    return payload
