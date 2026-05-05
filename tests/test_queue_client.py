"""Tests for HTTP usage queue draining without a real CLIProxyAPI server."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from cliproxy_usage_collect.config import Config
from cliproxy_usage_collect.queue_client import (
    AuthError,
    TransientError,
    pop_usage_records,
)


def _cfg(**overrides: object) -> Config:
    values: dict[str, object] = {
        "base_url": "http://localhost:8317",
        "management_key": "secret",
        "db_path": Path("./usage.db"),
        "queue_pop_count": 500,
        "http_timeout_seconds": 10.0,
    }
    values.update(overrides)
    return Config(**values)


def _client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[..., httpx.Client]:
    def factory(**kwargs: object) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def test_gets_usage_queue_with_count_and_bearer_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[{"timestamp": "2026-01-01T00:00:00Z"}])

    records = pop_usage_records(
        _cfg(base_url="http://proxy.example:8317", queue_pop_count=37),
        http_client_factory=_client_factory(handler),
    )

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "GET"
    assert str(request.url) == (
        "http://proxy.example:8317/v0/management/usage-queue?count=37"
    )
    assert request.headers["authorization"] == "Bearer secret"
    assert records == [
        json.dumps({"timestamp": "2026-01-01T00:00:00Z"}, separators=(",", ":"))
    ]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (
            "http://proxy.example:8317/",
            "http://proxy.example:8317/v0/management/usage-queue?count=500",
        ),
        (
            "http://proxy.example:8317/v0/management",
            "http://proxy.example:8317/v0/management/usage-queue?count=500",
        ),
        (
            "http://proxy.example:8317/v0/management/",
            "http://proxy.example:8317/v0/management/usage-queue?count=500",
        ),
    ],
)
def test_base_url_normalization(base_url: str, expected: str):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[])

    records = pop_usage_records(
        _cfg(base_url=base_url),
        http_client_factory=_client_factory(handler),
    )

    assert records == []
    assert str(seen[0].url) == expected


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_failures_map_to_auth_error(status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "no"})

    with pytest.raises(AuthError):
        pop_usage_records(_cfg(), http_client_factory=_client_factory(handler))


@pytest.mark.parametrize("status_code", [400, 404, 429, 500, 502])
def test_other_http_failures_map_to_transient_error(status_code: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": "boom"})

    with pytest.raises(TransientError):
        pop_usage_records(_cfg(), http_client_factory=_client_factory(handler))


def test_transport_error_maps_to_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect failed", request=request)

    with pytest.raises(TransientError):
        pop_usage_records(_cfg(), http_client_factory=_client_factory(handler))


def test_invalid_json_maps_to_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    with pytest.raises(TransientError):
        pop_usage_records(_cfg(), http_client_factory=_client_factory(handler))


def test_non_array_json_maps_to_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"records": []})

    with pytest.raises(TransientError):
        pop_usage_records(_cfg(), http_client_factory=_client_factory(handler))


def test_non_object_items_map_to_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not-an-object"])

    with pytest.raises(TransientError):
        pop_usage_records(_cfg(), http_client_factory=_client_factory(handler))
