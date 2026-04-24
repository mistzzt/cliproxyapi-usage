"""Tests for QuotaService orchestrator."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cliproxy_usage_server.quota.client import ApiCallResponse, AuthFileEntry
from cliproxy_usage_server.quota.errors import QuotaConfigError, QuotaUpstreamError
from cliproxy_usage_server.quota.providers import PROVIDERS
from cliproxy_usage_server.quota.service import QuotaService

# ---------------------------------------------------------------------------
# Fake client helpers
# ---------------------------------------------------------------------------


class FakeCliProxyClient:
    """Minimal fake for CliProxyClient that doesn't touch httpx."""

    def __init__(
        self,
        auth_files: list[AuthFileEntry] | None = None,
        api_call_response: ApiCallResponse | None = None,
        api_call_side_effect: Exception | None = None,
    ) -> None:
        self._auth_files = auth_files or []
        self._api_call_response = api_call_response
        self._api_call_side_effect = api_call_side_effect
        self.api_call_count = 0

    async def list_auth_files(self) -> list[AuthFileEntry]:
        return self._auth_files

    async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse:
        self.api_call_count += 1
        if self._api_call_side_effect is not None:
            raise self._api_call_side_effect
        assert self._api_call_response is not None
        return self._api_call_response

    async def aclose(self) -> None:
        pass


def _fixed_clock(dt: datetime) -> Any:
    """Returns a clock function that always returns *dt*."""

    def clock() -> datetime:
        return dt

    return clock


_KNOWN_AUTH_FILES = [
    AuthFileEntry(name="claude.json", type="claude"),
    AuthFileEntry(name="codex.json", type="codex"),
    AuthFileEntry(name="gemini.json", type="gemini"),
    AuthFileEntry(name="kimi.json", type="kimi"),
]

_SUCCESS_TTL = 300.0
_ERROR_TTL = 60.0

_NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)


def _make_service(
    fake_client: FakeCliProxyClient,
    clock: Any = None,
) -> QuotaService:
    return QuotaService(
        fake_client,
        PROVIDERS,
        success_ttl=_SUCCESS_TTL,
        error_ttl=_ERROR_TTL,
        clock=clock or _fixed_clock(_NOW),
    )


def _claude_200_response(fixture: dict[str, Any]) -> ApiCallResponse:
    """Build a 200 ApiCallResponse containing the Claude fixture body."""
    body = json.loads(fixture["body"])
    return ApiCallResponse(
        status_code=200,
        header={},
        body=body,
    )


# ---------------------------------------------------------------------------
# Test 1: list_accounts filters to known provider types
# ---------------------------------------------------------------------------


def test_list_accounts_filters_by_known_provider_types() -> None:
    async def run() -> None:
        fake = FakeCliProxyClient(auth_files=_KNOWN_AUTH_FILES)
        service = _make_service(fake)

        accounts = await service.list_accounts()

        provider_ids = {a.provider for a in accounts}
        assert provider_ids == {"claude", "codex"}

        auth_names = {a.auth_name for a in accounts}
        assert auth_names == {"claude.json", "codex.json"}

        # display_name is None when no email/label is available; auth_name
        # (the filename) must not leak through as a fallback display value.
        for account in accounts:
            assert account.display_name is None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 2: get_quota happy path
# ---------------------------------------------------------------------------


def test_get_quota_happy_path(
    claude_api_call_fixture: dict[str, Any],
) -> None:
    async def run() -> None:
        api_response = _claude_200_response(claude_api_call_fixture)
        fake = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")],
            api_call_response=api_response,
        )
        service = _make_service(fake)

        result = await service.get_quota("claude", "claude.json")

        assert result.quota is not None
        assert result.error is None
        assert result.fetched_at == _NOW
        assert result.stale_at == _NOW + timedelta(seconds=_SUCCESS_TTL)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 3: successful response is cached (2 calls → 1 upstream)
# ---------------------------------------------------------------------------


def test_get_quota_caches_successful_response(
    claude_api_call_fixture: dict[str, Any],
) -> None:
    async def run() -> None:
        api_response = _claude_200_response(claude_api_call_fixture)
        fake = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")],
            api_call_response=api_response,
        )
        service = _make_service(fake)

        r1 = await service.get_quota("claude", "claude.json")
        r2 = await service.get_quota("claude", "claude.json")

        assert fake.api_call_count == 1
        assert r1.quota is not None
        assert r2.quota is not None

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 4: error response cached with short TTL
# ---------------------------------------------------------------------------


def test_get_quota_caches_error_with_short_ttl() -> None:
    async def run() -> None:
        fake = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")],
            api_call_side_effect=QuotaUpstreamError(
                "upstream error", upstream_status=500
            ),
        )
        service = _make_service(fake)

        result = await service.get_quota("claude", "claude.json")

        assert result.quota is None
        assert result.error is not None
        assert result.error.kind == "upstream"
        assert result.error.upstream_status == 500
        assert result.stale_at - result.fetched_at == timedelta(seconds=_ERROR_TTL)

        # Second call within error TTL should use cache
        result2 = await service.get_quota("claude", "claude.json")
        assert fake.api_call_count == 1
        assert result2.error is not None
        assert result2.error.kind == "upstream"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 5: OAuth 401 → kind == "auth"
# ---------------------------------------------------------------------------


def test_get_quota_maps_oauth_401_to_auth_error() -> None:
    async def run() -> None:
        # Management endpoint returned 200, but the OAuth endpoint returned 401
        api_response = ApiCallResponse(
            status_code=401,
            header={},
            body={"error": "expired"},
        )
        fake = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")],
            api_call_response=api_response,
        )
        service = _make_service(fake)

        result = await service.get_quota("claude", "claude.json")

        assert result.error is not None
        assert result.error.kind == "auth"
        assert result.error.upstream_status == 401

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 6: OAuth 429 → kind == "rate_limited"
# ---------------------------------------------------------------------------


def test_get_quota_maps_oauth_429_to_rate_limited() -> None:
    async def run() -> None:
        api_response = ApiCallResponse(
            status_code=429,
            header={},
            body={"error": "rate limited"},
        )
        fake = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")],
            api_call_response=api_response,
        )
        service = _make_service(fake)

        result = await service.get_quota("claude", "claude.json")

        assert result.error is not None
        assert result.error.kind == "rate_limited"
        assert result.error.upstream_status == 429

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 7: Unknown provider raises QuotaConfigError
# ---------------------------------------------------------------------------


def test_get_quota_unknown_provider_raises() -> None:
    async def run() -> None:
        fake = FakeCliProxyClient(auth_files=[])
        service = _make_service(fake)

        with pytest.raises(QuotaConfigError):
            await service.get_quota("gemini", "x.json")

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 8: Unknown auth-file name raises QuotaConfigError
# ---------------------------------------------------------------------------


def test_get_quota_unknown_auth_name_raises() -> None:
    async def run() -> None:
        # Auth-file list does not include "unknown.json"
        fake = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")]
        )
        service = _make_service(fake)

        with pytest.raises(QuotaConfigError):
            await service.get_quota("claude", "unknown.json")

    asyncio.run(run())
