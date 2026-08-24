"""Async HTTP client for the CLIProxyAPI management endpoints.

Design note: ``CliProxyClient`` accepts an optional ``http_client`` keyword
argument so tests can inject an ``httpx.AsyncClient`` built with
``httpx.MockTransport``.  In production the argument is omitted and a real
client is built internally with the given timeout.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from cliproxy_usage_server.quota.errors import QuotaConfigError, QuotaUpstreamError


@dataclass(frozen=True)
class AuthFileEntry:
    name: str
    type: str
    auth_index: str | None = None
    label: str | None = None
    email: str | None = None
    chatgpt_account_id: str | None = None


def _decode_id_token(value: object) -> Mapping[str, object] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError, json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _chatgpt_account_id(entry: Mapping[str, object]) -> str | None:
    candidates = [entry.get("id_token")]
    for container_name in ("metadata", "attributes"):
        container = entry.get(container_name)
        if isinstance(container, dict):
            candidates.append(container.get("id_token"))
    for candidate in candidates:
        token = _decode_id_token(candidate)
        if token is None:
            continue
        auth_claim = token.get("https://api.openai.com/auth")
        values = [token]
        if isinstance(auth_claim, dict):
            values.insert(0, auth_claim)
        for value in values:
            account_id = value.get("chatgpt_account_id")
            if isinstance(account_id, str) and account_id:
                return account_id
    return None


@dataclass(frozen=True)
class ApiCallResponse:
    status_code: int
    header: Mapping[str, list[str]]
    body: object  # dict | list | str | None


class CliProxyClient:
    """Async client for CLIProxyAPI management endpoints."""

    def __init__(
        self,
        base_url: str,
        management_key: str,
        *,
        timeout_seconds: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise QuotaConfigError("base_url must not be empty")
        if not management_key:
            raise QuotaConfigError("management_key must not be empty")

        # Normalise: strip trailing slash so joins are predictable.
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {management_key}"}

        self._own_client = http_client is None
        self._client = (
            httpx.AsyncClient(timeout=timeout_seconds)
            if http_client is None
            else http_client
        )

    async def list_auth_files(self) -> list[AuthFileEntry]:
        """GET <base_url>/auth-files → list of AuthFileEntry.

        ``CLIPROXY_BASE_URL`` already points at the management API root
        for quota calls (e.g. ``http://host:port/v0/management``).
        """
        url = f"{self._base_url}/auth-files"
        response = await self._client.get(url, headers=self._headers)
        if response.status_code >= 300:
            raise QuotaUpstreamError(
                f"auth-files returned HTTP {response.status_code}",
                upstream_status=response.status_code,
            )
        data = response.json()
        return [
            AuthFileEntry(
                name=entry["name"],
                type=entry["type"],
                auth_index=entry.get("auth_index"),
                label=entry.get("label"),
                email=entry.get("email"),
                chatgpt_account_id=_chatgpt_account_id(entry),
            )
            for entry in data["files"]
        ]

    async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse:
        """POST /v0/management/api-call → ApiCallResponse.

        Raises
        ------
        QuotaUpstreamError
            When the management endpoint itself responds with a non-2xx status.
        """
        url = f"{self._base_url}/api-call"
        response = await self._client.post(
            url, json=dict(payload), headers=self._headers
        )
        if response.status_code >= 300:
            raise QuotaUpstreamError(
                f"api-call returned HTTP {response.status_code}",
                upstream_status=response.status_code,
            )
        data = response.json()
        raw_body = data.get("body")
        parsed_body: object
        if isinstance(raw_body, str):
            try:
                parsed_body = json.loads(raw_body)
            except json.JSONDecodeError, ValueError:
                parsed_body = raw_body
        else:
            parsed_body = raw_body

        return ApiCallResponse(
            status_code=data["status_code"],
            header=data.get("header", {}),
            body=parsed_body,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client if it was created internally."""
        if self._own_client:
            await self._client.aclose()
