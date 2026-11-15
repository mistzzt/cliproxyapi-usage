"""Tests for HTTP usage queue draining without a real CLIProxyAPI server."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
import pytest

from cliproxy_usage_collect.config import Config
from cliproxy_usage_collect.queue_client import (
    AuthError,
    TransientError,
    UnsupportedVersionError,
    parse_version,
    pop_usage_records,
)


class _ClientFactory(Protocol):
    def __call__(self, *, timeout: float) -> httpx.Client: ...


def _cfg(
    *,
    base_url: str = "http://localhost:8317",
    management_key: str = "secret",
    db_path: Path = Path("./usage.db"),
    queue_pop_count: int = 500,
    http_timeout_seconds: float = 10.0,
) -> Config:
    return Config(
        base_url=base_url,
        management_key=management_key,
        db_path=db_path,
        queue_pop_count=queue_pop_count,
        http_timeout_seconds=http_timeout_seconds,
    )


def _client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> _ClientFactory:
    def factory(*, timeout: float) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

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

    assert [(r.method, str(r.url)) for r in seen] == [
        ("GET", "http://proxy.example:8317/v0/management/usage-statistics-enabled"),
        ("GET", "http://proxy.example:8317/v0/management/usage-queue?count=37"),
    ]
    assert all(r.headers["authorization"] == "Bearer secret" for r in seen)
    assert records == [{"timestamp": "2026-01-01T00:00:00Z"}]


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
    assert str(seen[-1].url) == expected


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


def test_invalid_url_maps_to_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    with pytest.raises(TransientError):
        pop_usage_records(
            _cfg(base_url="http://proxy.example:abc"),
            http_client_factory=_client_factory(handler),
        )


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("8.0.72", (8, 0, 72)),
        ("v8.0.72", (8, 0, 72)),
        ("v8.0.72-3-gabc1234-dirty", (8, 0, 72)),
        ("v6.5.29-0", (6, 5, 29)),
        ("7.2.100", (7, 2, 100)),
        ("abc1234", None),
        ("dev", None),
        ("", None),
    ],
)
def test_parse_version(value: str, expected: tuple[int, int, int] | None):
    assert parse_version(value) == expected


def _versioned_handler(
    version: str | None, seen: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        headers = {} if version is None else {"X-CPA-VERSION": version}
        return httpx.Response(200, json=[], headers=headers)

    return handler


@pytest.mark.parametrize("version", ["v7.2.96", "7.1.30", "v6.5.29-0"])
def test_old_proxy_raises_before_popping_the_queue(version: str):
    seen: list[str] = []

    with pytest.raises(UnsupportedVersionError, match=version):
        pop_usage_records(
            _cfg(),
            http_client_factory=_client_factory(_versioned_handler(version, seen)),
        )

    assert seen == ["/v0/management/usage-statistics-enabled"]


@pytest.mark.parametrize("version", ["7.2.97", "v8.0.72-3-gabc1234-dirty"])
def test_supported_proxy_pops_without_warning(version: str, capsys):
    seen: list[str] = []

    pop_usage_records(
        _cfg(), http_client_factory=_client_factory(_versioned_handler(version, seen))
    )

    assert seen[-1] == "/v0/management/usage-queue"
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("version", [None, "", "dev", "abc1234"])
def test_unreadable_version_warns_and_pops(version: str | None, capsys):
    seen: list[str] = []

    pop_usage_records(
        _cfg(), http_client_factory=_client_factory(_versioned_handler(version, seen))
    )

    assert seen[-1] == "/v0/management/usage-queue"
    assert capsys.readouterr().err.startswith("Warning: cannot read")
