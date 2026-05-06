"""HTTP client for draining usage telemetry queue records."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from cliproxy_usage_collect.config import Config


class AuthError(Exception):
    """Raised when management API authentication or authorization fails."""


class TransientError(Exception):
    """Raised for retryable HTTP connection, protocol, or response failures."""


HttpClientFactory = Callable[..., httpx.Client]

_USAGE_QUEUE_PATH = "/v0/management/usage-queue"
_MANAGEMENT_PREFIX = "/v0/management"


def pop_usage_records(
    cfg: Config,
    *,
    http_client_factory: HttpClientFactory = httpx.Client,
) -> list[str]:
    """Pop one configured batch of raw queue records from CLIProxyAPI."""
    url = _usage_queue_url(cfg.base_url)
    headers = {"Authorization": f"Bearer {cfg.management_key}"}

    try:
        with http_client_factory(timeout=cfg.http_timeout_seconds) as client:
            response = client.get(
                url,
                params={"count": cfg.queue_pop_count},
                headers=headers,
            )
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise TransientError(str(exc)) from exc

    if response.status_code in {401, 403}:
        raise AuthError(_response_message(response))
    if response.status_code >= 400:
        raise TransientError(_response_message(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise TransientError("Usage queue response was not valid JSON") from exc

    return _normalize_payload(payload)


def _usage_queue_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith(_MANAGEMENT_PREFIX):
        return f"{normalized}/usage-queue"
    return f"{normalized}{_USAGE_QUEUE_PATH}"


def _response_message(response: httpx.Response) -> str:
    text = response.text.strip()
    if text:
        return f"HTTP {response.status_code}: {text}"
    return f"HTTP {response.status_code}"


def _normalize_payload(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        raise TransientError(
            f"Unexpected usage queue response type: {type(payload).__name__}"
        )
    if not all(isinstance(item, dict) for item in payload):
        raise TransientError("Usage queue response must be an array of objects")
    return [json.dumps(item, separators=(",", ":")) for item in payload]
