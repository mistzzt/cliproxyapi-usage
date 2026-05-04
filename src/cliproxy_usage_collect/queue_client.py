"""Redis queue client for draining raw usage records."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import urlparse

import redis
from redis import exceptions as redis_exceptions
from redis.backoff import NoBackoff
from redis.driver_info import DriverInfo
from redis.retry import Retry

from cliproxy_usage_collect.config import Config


class AuthError(Exception):
    """Raised when Redis authentication or authorization fails."""


class TransientError(Exception):
    """Raised for retryable Redis connection, protocol, or response failures."""


RedisClientFactory = Callable[..., Any]

_DEFAULT_PORT = 8317


def pop_usage_records(
    cfg: Config,
    *,
    redis_client_factory: RedisClientFactory = redis.Redis,
) -> list[str | bytes]:
    """Pop one configured batch of raw queue records from Redis."""
    origin = _parse_origin(cfg.base_url)
    client = redis_client_factory(
        host=origin.host,
        port=origin.port,
        ssl=origin.uses_tls,
        password=cfg.management_key,
        protocol=2,
        socket_timeout=cfg.redis_socket_timeout_seconds,
        socket_connect_timeout=cfg.redis_socket_timeout_seconds,
        health_check_interval=0,
        driver_info=DriverInfo(name="", lib_version=""),
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        retry_on_error=[],
    )

    try:
        if cfg.queue_pop_side == "left":
            result = client.lpop(cfg.queue_key, cfg.queue_pop_count)
        else:
            result = client.rpop(cfg.queue_key, cfg.queue_pop_count)
    except (
        redis_exceptions.AuthenticationError,
        redis_exceptions.AuthorizationError,
        redis_exceptions.NoPermissionError,
    ) as exc:
        raise AuthError(str(exc)) from exc
    except (
        redis_exceptions.ConnectionError,
        redis_exceptions.TimeoutError,
        redis_exceptions.InvalidResponse,
        redis_exceptions.ResponseError,
    ) as exc:
        raise TransientError(str(exc)) from exc

    return _normalize_result(result)


class _Origin:
    def __init__(self, host: str, port: int, uses_tls: bool) -> None:
        self.host = host
        self.port = port
        self.uses_tls = uses_tls


def _parse_origin(base_url: str) -> _Origin:
    try:
        parsed = urlparse(base_url)
        host = parsed.hostname
        port = parsed.port or _DEFAULT_PORT
    except ValueError as exc:
        raise TransientError(f"Invalid Redis origin: {base_url}") from exc

    if host is None:
        raise TransientError(f"Invalid Redis origin: {base_url}")
    return _Origin(host=host, port=port, uses_tls=parsed.scheme == "https")


def _normalize_result(result: Any) -> list[str | bytes]:
    if result is None:
        return []
    if isinstance(result, bytes | str):
        return [result]
    if isinstance(result, Sequence) and all(
        isinstance(item, bytes | str) for item in result
    ):
        return list(result)
    raise TransientError(
        f"Unexpected Redis queue response type: {type(result).__name__}"
    )
