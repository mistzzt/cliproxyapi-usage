"""Tests for /api/quota routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from cliproxy_usage_server.quota.errors import QuotaConfigError, QuotaUpstreamError
from cliproxy_usage_server.routes.quota import build_router
from cliproxy_usage_server.schemas import (
    QuotaAccount,
    QuotaError,
    QuotaResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 23, 12, 0, 0, tzinfo=UTC)
_LATER = datetime(2026, 4, 23, 13, 0, 0, tzinfo=UTC)

_ACCOUNT_A = QuotaAccount(
    provider="claude",
    auth_name="claude.json",
    display_name="Claude account",
)
_ACCOUNT_B = QuotaAccount(
    provider="codex",
    auth_name="codex.json",
    display_name="Codex account",
)

_SUCCESS_RESPONSE = QuotaResponse(
    quota=None,
    error=QuotaError(kind="auth", message="test auth error", upstream_status=401),
    fetched_at=_NOW,
    stale_at=_LATER,
)

_ERROR_RESPONSE = QuotaResponse(
    quota=None,
    error=QuotaError(kind="auth", message="token expired", upstream_status=401),
    fetched_at=_NOW,
    stale_at=_LATER,
)


# ---------------------------------------------------------------------------
# Fake service
# ---------------------------------------------------------------------------


class _FakeService:
    """Stub QuotaService for route isolation tests."""

    def __init__(
        self,
        *,
        accounts: list[QuotaAccount] | None = None,
        quota_response: QuotaResponse | None = None,
        raise_on_get_quota: Exception | None = None,
    ) -> None:
        self._accounts = accounts or []
        self._quota_response = quota_response
        self._raise_on_get_quota = raise_on_get_quota

    async def list_accounts(self) -> list[QuotaAccount]:
        return self._accounts

    async def get_quota(self, provider_id: str, auth_name: str) -> QuotaResponse:
        if self._raise_on_get_quota is not None:
            raise self._raise_on_get_quota
        assert self._quota_response is not None
        return self._quota_response


def _make_client(service: _FakeService) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(service), prefix="/api")  # type: ignore[arg-type]
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_accounts_endpoint_returns_accounts_payload() -> None:
    """GET /api/quota/accounts returns 200 with an accounts list."""
    service = _FakeService(accounts=[_ACCOUNT_A, _ACCOUNT_B])
    client = _make_client(service)

    resp = client.get("/api/quota/accounts")

    assert resp.status_code == 200
    body = resp.json()
    assert "accounts" in body
    assert len(body["accounts"]) == 2
    providers = {a["provider"] for a in body["accounts"]}
    assert providers == {"claude", "codex"}


def test_get_quota_endpoint_returns_success_response() -> None:
    """GET /api/quota/{provider}/{auth_name} returns 200 with a QuotaResponse."""
    # Use an error envelope as the "successful" return from the service — the
    # service itself wraps OAuth errors in QuotaResponse, so this is realistic.
    service = _FakeService(quota_response=_SUCCESS_RESPONSE)
    client = _make_client(service)

    resp = client.get("/api/quota/claude/claude.json")

    assert resp.status_code == 200
    body = resp.json()
    assert "quota" in body
    assert "error" in body
    assert "fetched_at" in body
    assert "stale_at" in body


def test_get_quota_endpoint_returns_error_envelope_with_200() -> None:
    """OAuth-side errors are wrapped in a 200 envelope — never a 4xx/5xx."""
    service = _FakeService(quota_response=_ERROR_RESPONSE)
    client = _make_client(service)

    resp = client.get("/api/quota/claude/claude.json")

    assert resp.status_code == 200
    body = resp.json()
    assert body["quota"] is None
    assert body["error"] is not None
    assert body["error"]["kind"] == "auth"


def test_get_quota_endpoint_unknown_provider_returns_404() -> None:
    """QuotaConfigError raised by the service maps to HTTP 404."""
    service = _FakeService(
        raise_on_get_quota=QuotaConfigError("Unknown provider 'gemini'")
    )
    client = _make_client(service)

    resp = client.get("/api/quota/gemini/foo")

    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_get_quota_endpoint_mgmt_unreachable_returns_502() -> None:
    """QuotaUpstreamError with no cached fallback maps to HTTP 502."""
    service = _FakeService(
        raise_on_get_quota=QuotaUpstreamError("management endpoint unreachable")
    )
    client = _make_client(service)

    resp = client.get("/api/quota/claude/claude.json")

    assert resp.status_code == 502
    assert "detail" in resp.json()


def test_path_params_rejected_with_bad_provider() -> None:
    """Unknown provider in the path → 404 (service raises QuotaConfigError)."""
    service = _FakeService(
        raise_on_get_quota=QuotaConfigError("Unknown provider 'gemini'")
    )
    client = _make_client(service)

    resp = client.get("/api/quota/gemini/foo")

    assert resp.status_code == 404
