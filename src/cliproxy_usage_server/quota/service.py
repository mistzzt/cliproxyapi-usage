"""QuotaService: ties together CliProxyClient, Provider registry, and TtlCache.

Design decisions
----------------
Two-cache strategy to handle different TTLs for success vs error:

1. ``_quota_cache``: a ``TtlCache[str, QuotaResponse]`` used for *successful*
   responses with ``success_ttl``.  The fetcher raises on any non-200 upstream
   status or upstream error so that ``TtlCache`` never stores an error envelope.

2. ``_error_cache``: a plain ``dict[str, tuple[QuotaResponse, datetime]]``
   (key → (response, expires_at)) for *error* envelopes with ``error_ttl``.
   On each ``get_quota`` call we check the error cache first; on a TtlCache
   miss we attempt a fetch; any error is caught, stored in the error cache,
   and returned as a ``QuotaResponse`` with ``error`` populated.

This keeps the happy-path deduplication (from TtlCache) intact while giving
full control over the shorter error TTL without patching TtlCache.

Unknown provider or auth-file name both raise ``QuotaConfigError`` (the plan
says "or QuotaConfigError"; we use QuotaConfigError exclusively for clarity
and HTTP-404 mapping in the route layer).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cliproxy_usage_server.quota.cache import TtlCache
from cliproxy_usage_server.quota.client import (
    ApiCallResponse,
    AuthFileEntry,
)
from cliproxy_usage_server.quota.errors import (
    QuotaConfigError,
    QuotaSchemaError,
    QuotaUpstreamError,
)
from cliproxy_usage_server.quota.providers.base import Provider
from cliproxy_usage_server.schemas import (
    QuotaAccount,
    QuotaError,
    QuotaResponse,
)

__all__ = ["QuotaService"]


class _ClientProtocol(Protocol):
    """Structural protocol for the CLIProxy management client.

    Allows tests to pass a duck-typed fake without subclassing CliProxyClient.
    """

    async def list_auth_files(self) -> list[AuthFileEntry]: ...

    async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse: ...

    async def aclose(self) -> None: ...


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _map_api_call_error(response: ApiCallResponse) -> QuotaError:
    """Map a non-200 ApiCallResponse.status_code to a QuotaError."""
    status = response.status_code
    if status in (401, 403):
        kind = "auth"
    elif status == 429:
        kind = "rate_limited"
    else:
        kind = "upstream"
    return QuotaError(
        kind=kind,
        message=f"OAuth endpoint returned HTTP {status}",
        upstream_status=status,
    )


def _map_upstream_error(exc: QuotaUpstreamError) -> QuotaError:
    """Map a QuotaUpstreamError from the management endpoint to a QuotaError.

    If the error carries an upstream HTTP 5xx status we use kind="upstream";
    otherwise (no status or non-5xx) we use kind="transient".
    """
    status = exc.upstream_status
    kind = "upstream" if status is not None and status >= 500 else "transient"
    return QuotaError(
        kind=kind,
        message=str(exc),
        upstream_status=status,
    )


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

        # Success cache: key = "provider_id::auth_name"
        # Uses monotonic time internally (TtlCache default).
        self._quota_cache: TtlCache[str, QuotaResponse] = TtlCache()

        # Error cache: key → (response, wall-clock expiry)
        self._error_cache: dict[str, tuple[QuotaResponse, datetime]] = {}

        # Accounts cache: simple (list | None, expiry) pair
        self._accounts_cache: tuple[list[QuotaAccount], datetime] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_accounts(self) -> list[QuotaAccount]:
        """Return quota accounts for all known-provider auth files.

        Results are cached for ``success_ttl`` seconds.
        """
        now = self._clock()
        if self._accounts_cache is not None:
            cached_list, expires_at = self._accounts_cache
            if expires_at > now:
                return cached_list

        auth_files = await self._client.list_auth_files()
        accounts: list[QuotaAccount] = []
        for entry in auth_files:
            if entry.type not in self._providers:
                continue
            # entry.type is "claude" or "codex" here because we only reach this
            # branch when entry.type is a key in self._providers, which only
            # contains those two values.  Cast is safe.
            accounts.append(
                QuotaAccount(
                    provider=entry.type,  # type: ignore[arg-type]
                    auth_name=entry.name,
                    display_name=entry.label or entry.email,
                )
            )

        expiry = now + timedelta(seconds=self._success_ttl)
        self._accounts_cache = (accounts, expiry)
        return accounts

    async def get_quota(self, provider_id: str, auth_name: str) -> QuotaResponse:
        """Fetch (and cache) quota for a specific provider + auth file.

        Raises
        ------
        QuotaConfigError
            When ``provider_id`` is not in the provider registry, or when
            ``auth_name`` does not appear in the upstream auth-files list.
        """
        if provider_id not in self._providers:
            raise QuotaConfigError(
                f"Unknown provider '{provider_id}'. "
                f"Known providers: {sorted(self._providers)}"
            )

        # Resolve the auth-file entry (also validates auth_name).
        entry = await self._resolve_auth_entry(provider_id, auth_name)

        cache_key = f"{provider_id}::{auth_name}"

        # 1. Check error cache first (errors have a shorter TTL).
        now = self._clock()
        if cache_key in self._error_cache:
            cached_error_resp, error_expires = self._error_cache[cache_key]
            if error_expires > now:
                return cached_error_resp
            # Expired — remove from error cache and retry via success path.
            del self._error_cache[cache_key]

        # 2. Try success cache / fetch.
        try:
            return await self._quota_cache.get_or_fetch(
                cache_key,
                lambda: self._fetch_quota(provider_id, auth_name, entry),
                ttl=self._success_ttl,
            )
        except (QuotaUpstreamError, QuotaSchemaError, _OAuthError) as exc:
            # Build an error envelope and stash it in the error cache.
            quota_error = self._exc_to_quota_error(exc)
            fetched_at = self._clock()
            stale_at = fetched_at + timedelta(seconds=self._error_ttl)
            error_resp = QuotaResponse(
                quota=None,
                error=quota_error,
                fetched_at=fetched_at,
                stale_at=stale_at,
            )
            self._error_cache[cache_key] = (error_resp, stale_at)
            return error_resp

    async def aclose(self) -> None:
        """Close the underlying client."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_auth_entry(
        self, provider_id: str, auth_name: str
    ) -> AuthFileEntry:
        """Find the auth-file entry for (provider, auth_name).

        Raises QuotaConfigError if no matching entry exists.
        """
        auth_files = await self._client.list_auth_files()
        for entry in auth_files:
            if entry.type == provider_id and entry.name == auth_name:
                return entry
        known = sorted(e.name for e in auth_files if e.type == provider_id)
        raise QuotaConfigError(
            f"Auth file '{auth_name}' not found for provider '{provider_id}'. "
            f"Known files: {known}"
        )

    async def _fetch_quota(
        self,
        provider_id: str,
        auth_name: str,
        entry: AuthFileEntry,
    ) -> QuotaResponse:
        """Call the management API and parse the result.

        This is the fetcher passed to TtlCache.get_or_fetch.  It raises on
        any failure so TtlCache does NOT cache the result.
        """
        provider = self._providers[provider_id]
        # CLIProxyAPI's /api-call identifies the auth file by its auth_index
        # (an opaque hex handle), not by filename.  Fall back to the filename
        # for fakes/tests that don't populate auth_index.
        auth_ref = entry.auth_index or entry.name
        payload = provider.build_api_call_payload(auth_ref)
        api_response: ApiCallResponse = await self._client.api_call(payload)

        if api_response.status_code != 200:
            raise _OAuthError(api_response)

        # May raise QuotaSchemaError — propagated to caller.
        quota = provider.parse(
            api_response.body,
            api_response.status_code,
            auth_name=auth_name,
        )

        fetched_at = self._clock()
        stale_at = fetched_at + timedelta(seconds=self._success_ttl)
        return QuotaResponse(
            quota=quota,
            error=None,
            fetched_at=fetched_at,
            stale_at=stale_at,
        )

    def _exc_to_quota_error(
        self, exc: QuotaUpstreamError | QuotaSchemaError | _OAuthError
    ) -> QuotaError:
        if isinstance(exc, _OAuthError):
            return _map_api_call_error(exc.response)
        if isinstance(exc, QuotaUpstreamError):
            return _map_upstream_error(exc)
        # QuotaSchemaError
        return QuotaError(
            kind="schema",
            message=str(exc),
            upstream_status=None,
        )


class _OAuthError(Exception):
    """Internal sentinel: the OAuth endpoint returned a non-200 status.

    Raised inside ``_fetch_quota`` so that ``TtlCache`` doesn't cache the
    response, yet the service can still convert it to a ``QuotaError``
    envelope and write it to the error cache.
    """

    def __init__(self, response: ApiCallResponse) -> None:
        self.response = response
        super().__init__(f"OAuth returned HTTP {response.status_code}")
