"""Tests for Redis queue draining without a real Redis server."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest

from cliproxy_usage_collect.config import Config
from cliproxy_usage_collect.queue_client import (
    AuthError,
    TransientError,
    pop_usage_records,
)


class FakeRedis:
    def __init__(self, result: Any = None, exc: Exception | None = None) -> None:
        self.result = result
        self.exc = exc
        self.calls: list[tuple[str, str, int]] = []

    def lpop(self, key: str, count: int) -> Any:
        self.calls.append(("lpop", key, count))
        if self.exc is not None:
            raise self.exc
        return self.result

    def rpop(self, key: str, count: int) -> Any:
        self.calls.append(("rpop", key, count))
        if self.exc is not None:
            raise self.exc
        return self.result


class CapturingFactory:
    def __init__(self, client: FakeRedis) -> None:
        self.client = client
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> FakeRedis:
        self.calls.append(kwargs)
        return self.client


def _cfg(**overrides: Any) -> Config:
    values = {
        "base_url": "http://localhost:8317",
        "management_key": "secret",
        "db_path": Path("./usage.db"),
        "queue_key": "queue",
        "queue_pop_count": 500,
        "queue_pop_side": "left",
        "redis_socket_timeout_seconds": 10.0,
    }
    values.update(overrides)
    return Config(**values)


def test_http_origin_constructs_non_tls_host_and_port():
    client = FakeRedis([])
    factory = CapturingFactory(client)

    pop_usage_records(
        _cfg(base_url="http://redis.example:9000"),
        redis_client_factory=factory,
    )

    assert factory.calls == [
        {
            "host": "redis.example",
            "port": 9000,
            "ssl": False,
            "password": "secret",
            "protocol": 2,
            "socket_timeout": 10.0,
            "socket_connect_timeout": 10.0,
            "health_check_interval": 0,
            "driver_info": ANY,
            "retry": ANY,
            "retry_on_timeout": False,
            "retry_on_error": [],
        }
    ]
    assert factory.calls[0]["driver_info"].formatted_name == ""
    assert factory.calls[0]["driver_info"].lib_version == ""
    assert factory.calls[0]["retry"]._retries == 0


def test_https_origin_constructs_tls_host_and_port():
    client = FakeRedis([])
    factory = CapturingFactory(client)

    pop_usage_records(
        _cfg(base_url="https://redis.example:9443"),
        redis_client_factory=factory,
    )

    assert factory.calls[0]["host"] == "redis.example"
    assert factory.calls[0]["port"] == 9443
    assert factory.calls[0]["ssl"] is True


def test_default_port_is_8317():
    client = FakeRedis([])
    factory = CapturingFactory(client)

    pop_usage_records(
        _cfg(base_url="https://redis.example"),
        redis_client_factory=factory,
    )

    assert factory.calls[0]["port"] == 8317


def test_invalid_url_port_maps_to_transient_error():
    with pytest.raises(TransientError):
        pop_usage_records(
            _cfg(base_url="http://redis.example:abc"),
            redis_client_factory=CapturingFactory(FakeRedis([])),
        )


def test_malformed_ipv6_url_maps_to_transient_error():
    with pytest.raises(TransientError):
        pop_usage_records(
            _cfg(base_url="http://[redis.example"),
            redis_client_factory=CapturingFactory(FakeRedis([])),
        )


def test_left_pop_calls_lpop_with_key_and_count():
    client = FakeRedis([])

    pop_usage_records(
        _cfg(queue_key="usage:queue", queue_pop_count=37, queue_pop_side="left"),
        redis_client_factory=CapturingFactory(client),
    )

    assert client.calls == [("lpop", "usage:queue", 37)]


def test_right_pop_calls_rpop_with_key_and_count():
    client = FakeRedis([])

    pop_usage_records(
        _cfg(queue_key="usage:queue", queue_pop_count=37, queue_pop_side="right"),
        redis_client_factory=CapturingFactory(client),
    )

    assert client.calls == [("rpop", "usage:queue", 37)]


@pytest.mark.parametrize("result", [None, [], ()])
def test_empty_queue_returns_empty_list(result: Any):
    records = pop_usage_records(
        _cfg(),
        redis_client_factory=CapturingFactory(FakeRedis(result)),
    )

    assert records == []


@pytest.mark.parametrize("result", [b"{}", "{}", [b"{}", "{}"], (b"{}", "{}")])
def test_bytes_and_strings_normalize_to_list(result: Any):
    records = pop_usage_records(
        _cfg(),
        redis_client_factory=CapturingFactory(FakeRedis(result)),
    )

    expected = [b"{}", "{}"] if isinstance(result, list | tuple) else [result]
    assert records == expected


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda qc: qc.redis_exceptions.AuthenticationError("bad password"),
        lambda qc: qc.redis_exceptions.AuthorizationError("permission denied"),
        lambda qc: qc.redis_exceptions.NoPermissionError("noperm denied"),
    ],
)
def test_auth_and_permission_failures_map_to_auth_error(exc_factory: Any):
    from cliproxy_usage_collect import queue_client as qc

    with pytest.raises(AuthError):
        pop_usage_records(
            _cfg(),
            redis_client_factory=CapturingFactory(FakeRedis(exc=exc_factory(qc))),
        )


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda qc: qc.redis_exceptions.ConnectionError("connect failed"),
        lambda qc: qc.redis_exceptions.TimeoutError("timed out"),
        lambda qc: qc.redis_exceptions.InvalidResponse("bad resp"),
        lambda qc: qc.redis_exceptions.ResponseError("unknown command"),
    ],
)
def test_connection_timeout_protocol_and_response_failures_map_to_transient_error(
    exc_factory: Any,
):
    from cliproxy_usage_collect import queue_client as qc

    with pytest.raises(TransientError):
        pop_usage_records(
            _cfg(),
            redis_client_factory=CapturingFactory(FakeRedis(exc=exc_factory(qc))),
        )


def test_unexpected_return_type_maps_to_transient_error():
    with pytest.raises(TransientError):
        pop_usage_records(
            _cfg(),
            redis_client_factory=CapturingFactory(FakeRedis({"not": "valid"})),
        )
