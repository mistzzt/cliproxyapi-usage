"""QuotaService: ties together CliProxyClient, the provider registry, and TtlCache.

Every quota lookup yields a ``QuotaResponse``: upstream and parse failures become an
error envelope, so one account's failure never affects another's. Envelopes are cached
until their own ``stale_at`` (shorter for errors). The auth-file list is cached too.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from cliproxy_usage_server.quota.cache import TtlCache
from cliproxy_usage_server.quota.client import ApiCallResponse, AuthFileEntry
from cliproxy_usage_server.quota.errors import (
    QuotaCapabilityError,
    QuotaConfigError,
    QuotaSchemaError,
    QuotaUpstreamError,
)
from cliproxy_usage_server.quota.providers.base import (
    Provider,
    ResetCreditsProvider,
    ResetProvider,
)
from cliproxy_usage_server.schemas import (
    ProviderQuota,
    QuotaAccount,
    QuotaError,
    QuotaResponse,
)

__all__ = ["QuotaService"]


class _ClientProtocol(Protocol):
    """The management client calls the service makes; tests pass duck-typed fakes."""

    async def list_auth_files(self) -> list[AuthFileEntry]: ...

    async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse: ...

    async def aclose(self) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _oauth_error(status: int) -> QuotaError:
    """The vendor's OAuth endpoint answered with a non-200 status."""
    kind: Literal["auth", "rate_limited", "upstream"] = "upstream"
    if status in (401, 403):
        kind = "auth"
    elif status == 429:
        kind = "rate_limited"
    return QuotaError(
        kind=kind,
        message=f"OAuth endpoint returned HTTP {status}",
        upstream_status=status,
    )


def _upstream_error(exc: QuotaUpstreamError) -> QuotaError:
    """CLIProxyAPI itself failed: a 5xx is "upstream", anything else "transient"."""
    status = exc.upstream_status
    kind = "upstream" if status is not None and status >= 500 else "transient"
    return QuotaError(kind=kind, message=str(exc), upstream_status=status)


class QuotaService:
    """Async service that fetches and caches quota data for known providers."""

    def __init__(
        self,
        client: _ClientProtocol,
        providers: Mapping[str, Provider],
        *,
        success_ttl: float,
        error_ttl: float = 60.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._client = client
        self._providers = providers
        self._success_ttl = success_ttl
        self._error_ttl = error_ttl
        self._clock: Callable[[], datetime] = clock or _utcnow
        self._quotas: TtlCache[str, QuotaResponse] = TtlCache()
        self._auth_files: TtlCache[None, list[AuthFileEntry]] = TtlCache()

    async def list_accounts(self) -> list[QuotaAccount]:
        """Return quota accounts for all known-provider auth files."""
        return [
            QuotaAccount(
                provider=entry.type,  # type: ignore[arg-type]  # a registry key
                auth_name=entry.name,
                display_name=entry.label or entry.email,
            )
            for entry in await self._list_auth_files()
            if entry.type in self._providers
        ]

    async def get_quota(self, provider_id: str, auth_name: str) -> QuotaResponse:
        """Fetch (and cache) quota for one account.

        Raises ``QuotaConfigError`` for an unknown provider or auth file, and
        ``QuotaUpstreamError`` when the auth-file list cannot be read.
        """
        provider = self._provider(provider_id)
        entry = await self._resolve_auth_entry(provider_id, auth_name)
        return await self._quotas.get_or_fetch(
            f"{provider_id}::{auth_name}",
            lambda: self._fetch_quota(provider, auth_name, entry),
            ttl=lambda r: (r.stale_at - r.fetched_at).total_seconds(),
        )

    async def reset_quota(self, provider_id: str, auth_name: str) -> None:
        """Consume one manual reset for an account and drop its cached quota."""
        provider = self._provider(provider_id)
        entry = await self._resolve_auth_entry(provider_id, auth_name)
        if not isinstance(provider, ResetProvider):
            raise QuotaCapabilityError(
                f"Provider '{provider_id}' does not support manual quota resets"
            )
        response = await self._client.api_call(
            provider.build_reset_api_call_payload(
                entry.api_ref, account_id=entry.chatgpt_account_id
            )
        )
        if not 200 <= response.status_code < 300:
            raise QuotaUpstreamError(
                f"Reset endpoint returned HTTP {response.status_code}",
                upstream_status=response.status_code,
            )
        self._quotas.invalidate(f"{provider_id}::{auth_name}")

    async def aclose(self) -> None:
        """Close the underlying client."""
        await self._client.aclose()

    def _provider(self, provider_id: str) -> Provider:
        provider = self._providers.get(provider_id)
        if provider is None:
            raise QuotaConfigError(
                f"Unknown provider '{provider_id}'. "
                f"Known providers: {sorted(self._providers)}"
            )
        return provider

    async def _list_auth_files(self) -> list[AuthFileEntry]:
        return await self._auth_files.get_or_fetch(
            None, self._client.list_auth_files, ttl=lambda _: self._success_ttl
        )

    async def _resolve_auth_entry(
        self, provider_id: str, auth_name: str
    ) -> AuthFileEntry:
        auth_files = await self._list_auth_files()
        for entry in auth_files:
            if entry.type == provider_id and entry.name == auth_name:
                return entry
        known = sorted(e.name for e in auth_files if e.type == provider_id)
        raise QuotaConfigError(
            f"Auth file '{auth_name}' not found for provider '{provider_id}'. "
            f"Known files: {known}"
        )

    async def _fetch_quota(
        self, provider: Provider, auth_name: str, entry: AuthFileEntry
    ) -> QuotaResponse:
        try:
            response = await self._client.api_call(
                provider.build_api_call_payload(entry.api_ref)
            )
            if response.status_code != 200:
                return self._envelope(error=_oauth_error(response.status_code))
            quota = provider.parse(response.body, auth_name=auth_name)
        except QuotaUpstreamError as exc:
            return self._envelope(error=_upstream_error(exc))
        except QuotaSchemaError as exc:
            return self._envelope(
                error=QuotaError(kind="schema", message=str(exc), upstream_status=None)
            )
        if isinstance(provider, ResetCreditsProvider):
            quota = await self._attach_reset_credits(provider, quota, entry)
        return self._envelope(quota=quota)

    def _envelope(
        self, *, quota: ProviderQuota | None = None, error: QuotaError | None = None
    ) -> QuotaResponse:
        fetched_at = self._clock()
        ttl = self._success_ttl if error is None else self._error_ttl
        return QuotaResponse(
            quota=quota,
            error=error,
            fetched_at=fetched_at,
            stale_at=fetched_at + timedelta(seconds=ttl),
        )

    async def _attach_reset_credits(
        self, provider: ResetCreditsProvider, quota: ProviderQuota, entry: AuthFileEntry
    ) -> ProviderQuota:
        """Merge credit expiries into manual_resets; failure only sets credits_error."""
        payload = provider.build_reset_credits_api_call_payload(
            entry.api_ref, account_id=entry.chatgpt_account_id
        )
        try:
            response = await self._client.api_call(payload)
            if not 200 <= response.status_code < 300:
                raise QuotaUpstreamError(
                    f"Reset credits endpoint returned HTTP {response.status_code}"
                )
            summary = provider.parse_reset_credits(response.body)
        except (QuotaUpstreamError, QuotaSchemaError) as exc:
            if quota.manual_resets is None:
                return quota
            summary = quota.manual_resets.model_copy(update={"credits_error": str(exc)})
        else:
            if quota.manual_resets is not None:
                summary = summary.model_copy(
                    update={"available_count": quota.manual_resets.available_count}
                )
        return quota.model_copy(update={"manual_resets": summary})
