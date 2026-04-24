"""Tests for cliproxy_usage.client — uses httpx MockTransport for isolation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from cliproxy_usage.client import AuthError, TransientError, fetch_export
from cliproxy_usage.config import Config

_CFG = Config(
    base_url="http://test.local/v0/management",
    management_key="test-key-abc",
    db_path=Path("/tmp/test.db"),
)

_VALID_PAYLOAD = {"usage": {"apis": {}}}


def _make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_parsed_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_VALID_PAYLOAD)

    client = _make_client(handler)
    result = fetch_export(_CFG, client=client)
    assert result == _VALID_PAYLOAD


def test_happy_path_sends_bearer_auth():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_VALID_PAYLOAD)

    client = _make_client(handler)
    fetch_export(_CFG, client=client)
    assert captured["auth"] == "Bearer test-key-abc"


def test_happy_path_uses_correct_url():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_VALID_PAYLOAD)

    client = _make_client(handler)
    fetch_export(_CFG, client=client)
    assert captured["url"] == "http://test.local/v0/management/usage/export"


# ---------------------------------------------------------------------------
# Auth errors
# ---------------------------------------------------------------------------


def test_401_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    client = _make_client(handler)
    with pytest.raises(AuthError):
        fetch_export(_CFG, client=client)


def test_403_raises_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Forbidden")

    client = _make_client(handler)
    with pytest.raises(AuthError):
        fetch_export(_CFG, client=client)


# ---------------------------------------------------------------------------
# Transient errors (server-side)
# ---------------------------------------------------------------------------


def test_500_raises_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal Server Error")

    client = _make_client(handler)
    with pytest.raises(TransientError):
        fetch_export(_CFG, client=client)


def test_503_raises_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Service Unavailable")

    client = _make_client(handler)
    with pytest.raises(TransientError):
        fetch_export(_CFG, client=client)


# ---------------------------------------------------------------------------
# Transient errors (network / transport)
# ---------------------------------------------------------------------------


def test_connection_error_raises_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = _make_client(handler)
    with pytest.raises(TransientError):
        fetch_export(_CFG, client=client)


# ---------------------------------------------------------------------------
# Non-JSON body on 200 → TransientError
# ---------------------------------------------------------------------------


def test_non_json_body_raises_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json!!!")

    client = _make_client(handler)
    with pytest.raises(TransientError):
        fetch_export(_CFG, client=client)


# ---------------------------------------------------------------------------
# Other non-2xx (e.g. 404) → TransientError
# ---------------------------------------------------------------------------


def test_404_raises_transient_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = _make_client(handler)
    with pytest.raises(TransientError):
        fetch_export(_CFG, client=client)


# ---------------------------------------------------------------------------
# base_url is honoured (different config)
# ---------------------------------------------------------------------------


def test_custom_base_url_is_used():
    captured = {}
    cfg2 = Config(
        base_url="https://proxy.example.com/api",
        management_key="key2",
        db_path=Path("/tmp/test2.db"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_VALID_PAYLOAD)

    client = _make_client(handler)
    fetch_export(cfg2, client=client)
    assert captured["url"] == "https://proxy.example.com/api/usage/export"
