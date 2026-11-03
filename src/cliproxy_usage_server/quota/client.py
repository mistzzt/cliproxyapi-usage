"""Async HTTP client for the CLIProxyAPI management endpoints.

Design note: ``CliProxyClient`` accepts an optional ``http_client`` keyword
argument so tests can inject an ``httpx.AsyncClient`` built with
``httpx.MockTransport``.  In production the argument is omitted and a real
client is built internally with the given timeout.
"""

from __future__ import annotations

import base64
import contextlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from cliproxy_usage_collect.config import management_url
from cliproxy_usage_server.quota.errors import QuotaConfigError, QuotaUpstreamError


@dataclass(frozen=True)
class AuthFileEntry:
    name: str
    type: str
    auth_index: str | None = None
    label: str | None = None
    email: str | None = None
    chatgpt_account_id: str | None = None

    @property
    def api_ref(self) -> str:
        """How /api-call names this file: its opaque auth_index, else its file name."""
        return self.auth_index or self.name


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
    """Async client for CLIProxyAPI management endpoints.

    Every failure to reach or read an endpoint raises ``QuotaUpstreamError``.
    """

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
        self._base_url = base_url
        self._headers = {"Authorization": f"Bearer {management_key}"}
        self._own_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = management_url(self._base_url, path)
        try:
            response = await self._client.request(
                method, url, headers=self._headers, **kwargs
            )
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise QuotaUpstreamError(f"{path} request failed: {exc}") from exc
        if response.status_code >= 300:
            raise QuotaUpstreamError(
                f"{path} returned HTTP {response.status_code}",
                upstream_status=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise QuotaUpstreamError(f"{path} returned invalid JSON") from exc

    async def list_auth_files(self) -> list[AuthFileEntry]:
        """GET auth-files: every auth file registered with CLIProxyAPI."""
        data = await self._request("GET", "auth-files")
        try:
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
        except (KeyError, TypeError, AttributeError) as exc:
            raise QuotaUpstreamError(
                f"auth-files has an unexpected shape: {exc}"
            ) from exc

    async def api_call(self, payload: Mapping[str, object]) -> ApiCallResponse:
        """POST api-call: relay one request through an auth file's credentials."""
        data = await self._request("POST", "api-call", json=dict(payload))
        try:
            raw_body = data.get("body")
            status_code = data["status_code"]
            header = data.get("header", {})
        except (KeyError, AttributeError) as exc:
            raise QuotaUpstreamError(
                f"api-call has an unexpected shape: {exc}"
            ) from exc
        parsed_body: object = raw_body
        if isinstance(raw_body, str):
            with contextlib.suppress(ValueError):
                parsed_body = json.loads(raw_body)
        return ApiCallResponse(status_code=status_code, header=header, body=parsed_body)

    async def aclose(self) -> None:
        """Close the underlying HTTP client if it was created internally."""
        if self._own_client:
            await self._client.aclose()
