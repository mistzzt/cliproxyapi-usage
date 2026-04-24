"""Quota endpoints: /api/quota/accounts, /api/quota/{provider}/{auth_name}."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from cliproxy_usage_server.quota.errors import QuotaConfigError, QuotaUpstreamError
from cliproxy_usage_server.quota.service import QuotaService
from cliproxy_usage_server.schemas import QuotaAccountsResponse, QuotaResponse


def build_router(service: QuotaService) -> APIRouter:
    """Build the quota API router.

    Access control is delegated to the upstream reverse proxy.
    """
    r = APIRouter(prefix="", tags=["quota"])

    @r.get("/quota/accounts", response_model=QuotaAccountsResponse)
    async def accounts_endpoint() -> QuotaAccountsResponse:
        """Return all known quota accounts across configured providers."""
        accs = await service.list_accounts()
        return QuotaAccountsResponse(accounts=accs)

    @r.get("/quota/{provider}/{auth_name}", response_model=QuotaResponse)
    async def get_quota_endpoint(provider: str, auth_name: str) -> QuotaResponse:
        """Return quota information for a specific provider + auth file.

        OAuth-side failures (auth, rate-limit, upstream) are returned as a 200
        envelope with ``quota: null`` and ``error`` populated — the HTTP status
        is always 200 for those cases.  Only routing / config errors (unknown
        provider) produce a 404, and management-endpoint unreachability produces
        a 502.
        """
        try:
            return await service.get_quota(provider, auth_name)
        except QuotaConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except QuotaUpstreamError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return r
