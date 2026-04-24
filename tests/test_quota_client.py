"""Tests for cliproxy_usage_server.quota.client — uses httpx MockTransport."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from cliproxy_usage_server.quota.client import AuthFileEntry, CliProxyClient
from cliproxy_usage_server.quota.errors import QuotaConfigError, QuotaUpstreamError

_BASE_URL = "http://test.local/v0/management"
_KEY = "testkey"


def _make_async_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# test_list_auth_files_passes_bearer_and_decodes_entries
# ---------------------------------------------------------------------------


def test_list_auth_files_passes_bearer_and_decodes_entries() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "files": [
                    {"name": "claude.json", "type": "claude"},
                    {"name": "misc.json", "type": "gemini"},
                ]
            },
        )

    async def run() -> None:
        client = CliProxyClient(
            base_url=_BASE_URL,
            management_key=_KEY,
            http_client=_make_async_client(handler),
        )
        result = await client.list_auth_files()
        assert captured["url"] == f"{_BASE_URL}/auth-files"
        assert captured["auth"] == "Bearer testkey"
        assert len(result) == 2
        assert result[0] == AuthFileEntry(name="claude.json", type="claude")
        assert result[1] == AuthFileEntry(name="misc.json", type="gemini")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# test_api_call_forwards_payload
# ---------------------------------------------------------------------------


def test_api_call_forwards_payload() -> None:
    payload: dict[str, Any] = {"model": "claude-3", "prompt": "hello"}
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "header": {},
                "body": '{"hello":"world"}',
            },
        )

    async def run() -> None:
        client = CliProxyClient(
            base_url=_BASE_URL,
            management_key=_KEY,
            http_client=_make_async_client(handler),
        )
        result = await client.api_call(payload)
        assert captured["url"] == f"{_BASE_URL}/api-call"
        assert captured["body"] == payload
        assert result.status_code == 200
        assert result.body == {"hello": "world"}

    asyncio.run(run())


# ---------------------------------------------------------------------------
# test_api_call_propagates_non_2xx_from_management_endpoint
# ---------------------------------------------------------------------------


def test_api_call_propagates_non_2xx_from_management_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "boom"})

    async def run() -> None:
        client = CliProxyClient(
            base_url=_BASE_URL,
            management_key=_KEY,
            http_client=_make_async_client(handler),
        )
        with pytest.raises(QuotaUpstreamError) as exc_info:
            await client.api_call({"key": "value"})
        assert exc_info.value.upstream_status == 502

    asyncio.run(run())


# ---------------------------------------------------------------------------
# test_api_call_parses_body_string_as_json_when_possible
# ---------------------------------------------------------------------------


def test_api_call_parses_body_string_as_json_when_possible() -> None:
    async def run() -> None:
        # JSON body → parsed dict
        def json_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status_code": 200, "header": {}, "body": '{"a":1}'},
            )

        client = CliProxyClient(
            base_url=_BASE_URL,
            management_key=_KEY,
            http_client=_make_async_client(json_handler),
        )
        result = await client.api_call({"x": "y"})
        assert result.body == {"a": 1}

        # Non-JSON body → raw string
        def raw_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"status_code": 200, "header": {}, "body": "not json"},
            )

        client2 = CliProxyClient(
            base_url=_BASE_URL,
            management_key=_KEY,
            http_client=_make_async_client(raw_handler),
        )
        result2 = await client2.api_call({"x": "y"})
        assert result2.body == "not json"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# test_init_requires_base_url_and_key
# ---------------------------------------------------------------------------


def test_init_requires_base_url_and_key() -> None:
    with pytest.raises(QuotaConfigError):
        CliProxyClient(base_url="", management_key="x")
