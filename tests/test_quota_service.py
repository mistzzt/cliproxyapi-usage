"""Tests for QuotaService orchestrator."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cliproxy_usage_server.quota.client import ApiCallResponse, AuthFileEntry
from cliproxy_usage_server.quota.errors import (
    QuotaCapabilityError,
    QuotaConfigError,
    QuotaUpstreamError,
)
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


class _SequenceClient:
    def __init__(
        self, auth_files: list[AuthFileEntry], responses: list[ApiCallResponse]
    ) -> None:
        self.auth_files = auth_files
        self.responses = responses
        self.payloads: list[Mapping[str, object]] = []

    async def list_auth_files(self) -> list[AuthFileEntry]:
        return self.auth_files

    async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse:
        self.payloads.append(payload)
        return self.responses.pop(0)

    async def aclose(self) -> None:
        pass


def _codex_usage(used_percent: int) -> ApiCallResponse:
    return ApiCallResponse(
        status_code=200,
        header={},
        body={
            "rate_limit": {
                "primary_window": {
                    "used_percent": used_percent,
                    "limit_window_seconds": 604800,
                    "reset_at": 1777410854,
                }
            }
        },
    )


_CREDITS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"


def _codex_credits(
    *expires_at: str, available_count: int | None = None
) -> ApiCallResponse:
    body: dict[str, Any] = {
        "credits": [
            {
                "id": f"credit-{i}",
                "reset_type": "codex_rate_limits",
                "status": "available",
                "granted_at": "2026-04-01T00:00:00Z",
                "expires_at": value,
            }
            for i, value in enumerate(expires_at)
        ]
    }
    if available_count is not None:
        body["available_count"] = available_count
    return ApiCallResponse(status_code=200, header={}, body=body)


def _codex_usage_with_resets(count: int) -> ApiCallResponse:
    usage = _codex_usage(10)
    assert isinstance(usage.body, dict)
    return ApiCallResponse(
        200, {}, {**usage.body, "rate_limit_reset_credits": {"available_count": count}}
    )


def _codex_credits_failure() -> ApiCallResponse:
    return ApiCallResponse(500, {}, {"error": "boom"})


def test_reset_quota_uses_capability_and_invalidates_success_cache() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [
                AuthFileEntry(
                    name="codex.json",
                    type="codex",
                    auth_index="auth-opaque",
                    chatgpt_account_id="acct-1",
                )
            ],
            [
                _codex_usage(70),
                _codex_credits_failure(),
                ApiCallResponse(204, {}, None),
                _codex_usage(0),
                _codex_credits_failure(),
            ],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        first = await service.get_quota("codex", "codex.json")
        await service.reset_quota("codex", "codex.json")
        fresh = await service.get_quota("codex", "codex.json")

        assert first.quota is not None and first.quota.windows[0].used_percent == 70
        assert fresh.quota is not None and fresh.quota.windows[0].used_percent == 0
        reset_payload = client.payloads[2]
        assert reset_payload["authIndex"] == "auth-opaque"
        headers = reset_payload["header"]
        assert isinstance(headers, dict)
        assert headers["Chatgpt-Account-Id"] == "acct-1"

    asyncio.run(run())


def test_failed_reset_preserves_cached_quota() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [
                _codex_usage(70),
                _codex_credits_failure(),
                ApiCallResponse(409, {}, {"error": "no credits"}),
            ],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        first = await service.get_quota("codex", "codex.json")
        with pytest.raises(QuotaUpstreamError) as exc_info:
            await service.reset_quota("codex", "codex.json")
        cached = await service.get_quota("codex", "codex.json")

        assert exc_info.value.upstream_status == 409
        assert cached is first
        assert len(client.payloads) == 3

    asyncio.run(run())


def test_successful_reset_invalidates_cached_error() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [
                ApiCallResponse(500, {}, {"error": "old failure"}),
                ApiCallResponse(204, {}, None),
                _codex_usage(0),
                _codex_credits_failure(),
            ],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        failed = await service.get_quota("codex", "codex.json")
        await service.reset_quota("codex", "codex.json")
        fresh = await service.get_quota("codex", "codex.json")

        assert failed.error is not None
        assert fresh.quota is not None
        assert len(client.payloads) == 4

    asyncio.run(run())


def test_reset_rejects_provider_without_capability() -> None:
    async def run() -> None:
        client = FakeCliProxyClient(
            auth_files=[AuthFileEntry(name="claude.json", type="claude")]
        )
        service = _make_service(client)
        with pytest.raises(QuotaCapabilityError):
            await service.reset_quota("claude", "claude.json")

    asyncio.run(run())


def test_reset_dispatch_is_capability_based_for_non_codex_provider() -> None:
    class ResetCapableProvider:
        provider_id = "custom"
        auth_type = "custom"

        def build_api_call_payload(self, auth_name: str) -> dict[str, object]:
            return {"authIndex": auth_name}

        def build_reset_api_call_payload(
            self, auth_name: str, *, account_id: str | None
        ) -> dict[str, object]:
            return {"authIndex": auth_name, "operation": "custom-reset"}

        def parse(
            self, upstream_body: object, upstream_status: int, *, auth_name: str
        ) -> Any:
            raise AssertionError("not used")

    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="custom.json", type="custom")],
            [ApiCallResponse(204, {}, None)],
        )
        service = QuotaService(
            client,  # type: ignore[arg-type]
            {"custom": ResetCapableProvider()},  # type: ignore[dict-item]
            success_ttl=300,
        )
        await service.reset_quota("custom", "custom.json")
        assert client.payloads == [
            {"authIndex": "custom.json", "operation": "custom-reset"}
        ]

    asyncio.run(run())


def test_reset_prevents_in_flight_quota_fetch_from_repopulating_cache() -> None:
    class InFlightClient:
        def __init__(self) -> None:
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()
            self.usage_calls = 0

        async def list_auth_files(self) -> list[AuthFileEntry]:
            return [AuthFileEntry(name="codex.json", type="codex")]

        async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse:
            if payload.get("method") == "POST":
                return ApiCallResponse(204, {}, None)
            if payload.get("url") == _CREDITS_URL:
                return _codex_credits_failure()
            self.usage_calls += 1
            if self.usage_calls == 1:
                self.first_started.set()
                await self.release_first.wait()
                return _codex_usage(90)
            return _codex_usage(0)

        async def aclose(self) -> None:
            pass

    async def run() -> None:
        client = InFlightClient()
        service = _make_service(client)  # type: ignore[arg-type]
        old_fetch = asyncio.create_task(service.get_quota("codex", "codex.json"))
        await client.first_started.wait()
        await service.reset_quota("codex", "codex.json")
        fresh = await service.get_quota("codex", "codex.json")
        client.release_first.set()
        old = await old_fetch
        cached = await service.get_quota("codex", "codex.json")

        assert old.quota is not None and old.quota.windows[0].used_percent == 90
        assert fresh.quota is not None and fresh.quota.windows[0].used_percent == 0
        assert cached.quota is not None and cached.quota.windows[0].used_percent == 0
        assert client.usage_calls == 2

    asyncio.run(run())


def test_codex_credits_are_merged_into_manual_resets() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [
                AuthFileEntry(
                    name="codex.json",
                    type="codex",
                    auth_index="auth-opaque",
                    chatgpt_account_id="acct-1",
                )
            ],
            [
                _codex_usage_with_resets(2),
                _codex_credits("2026-06-15T00:00:00Z", "2026-06-01T00:00:00Z"),
            ],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("codex", "codex.json")

        assert result.quota is not None
        resets = result.quota.manual_resets
        assert resets is not None
        assert resets.available_count == 2
        assert resets.credits_error is None
        assert [c.expires_at.isoformat() for c in resets.credits] == [
            "2026-06-01T00:00:00+00:00",
            "2026-06-15T00:00:00+00:00",
        ]
        credits_payload = client.payloads[1]
        assert credits_payload["url"] == _CREDITS_URL
        assert credits_payload["authIndex"] == "auth-opaque"
        headers = credits_payload["header"]
        assert isinstance(headers, dict)
        assert headers["Chatgpt-Account-Id"] == "acct-1"

    asyncio.run(run())


def test_codex_credits_failure_keeps_usage_count_and_sets_error() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [_codex_usage_with_resets(2), _codex_credits_failure()],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("codex", "codex.json")

        assert result.error is None
        assert result.quota is not None
        resets = result.quota.manual_resets
        assert resets is not None
        assert resets.available_count == 2
        assert resets.credits == []
        assert resets.credits_error == "Reset credits endpoint returned HTTP 500"
        assert result.stale_at == _NOW + timedelta(seconds=_SUCCESS_TTL)

    asyncio.run(run())


def test_codex_credits_transport_error_sets_error_only() -> None:
    class FlakyCreditsClient(_SequenceClient):
        async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse:
            if payload.get("url") == _CREDITS_URL:
                raise QuotaUpstreamError(
                    "api-call returned HTTP 502", upstream_status=502
                )
            return await super().api_call(payload)

    async def run() -> None:
        client = FlakyCreditsClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [_codex_usage_with_resets(1)],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("codex", "codex.json")

        assert result.quota is not None
        assert result.quota.manual_resets is not None
        assert result.quota.manual_resets.available_count == 1
        assert result.quota.manual_resets.credits_error == "api-call returned HTTP 502"

    asyncio.run(run())


def test_codex_credits_supply_count_when_usage_payload_has_none() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [
                _codex_usage(10),
                _codex_credits("2026-06-01T00:00:00Z", available_count=3),
            ],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("codex", "codex.json")

        assert result.quota is not None
        assert result.quota.manual_resets is not None
        assert result.quota.manual_resets.available_count == 3
        assert len(result.quota.manual_resets.credits) == 1

    asyncio.run(run())


def test_codex_credits_length_is_count_fallback() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [_codex_usage(10), _codex_credits("2026-06-01T00:00:00Z")],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("codex", "codex.json")

        assert result.quota is not None
        assert result.quota.manual_resets is not None
        assert result.quota.manual_resets.available_count == 1

    asyncio.run(run())


def test_codex_without_any_reset_count_keeps_manual_resets_none() -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="codex.json", type="codex")],
            [_codex_usage(10), _codex_credits_failure()],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("codex", "codex.json")

        assert result.quota is not None
        assert result.quota.manual_resets is None

    asyncio.run(run())


def test_claude_provider_makes_no_credits_call(
    claude_api_call_fixture: dict[str, Any],
) -> None:
    async def run() -> None:
        client = _SequenceClient(
            [AuthFileEntry(name="claude.json", type="claude")],
            [_claude_200_response(claude_api_call_fixture)],
        )
        service = _make_service(client)  # type: ignore[arg-type]
        result = await service.get_quota("claude", "claude.json")

        assert result.quota is not None
        assert len(client.payloads) == 1

    asyncio.run(run())
