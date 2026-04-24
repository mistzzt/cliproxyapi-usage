"""HTTP client for the cliproxy management API.

Design note: ``fetch_export`` accepts an optional ``client`` keyword argument
so tests can inject an ``httpx.Client`` built with ``httpx.MockTransport``.
In production the argument is omitted and a real client is built internally
with a 10-second timeout.
"""

from __future__ import annotations

import httpx

from cliproxy_usage.config import Config


class AuthError(Exception):
    """Raised on HTTP 401 or 403 responses."""


class TransientError(Exception):
    """Raised on network errors, timeouts, 5xx, or unparseable responses."""


_TIMEOUT = 10.0


def fetch_export(cfg: Config, *, client: httpx.Client | None = None) -> dict:
    """Fetch /usage/export and return the parsed JSON dict.

    Parameters
    ----------
    cfg:
        Runtime configuration (base_url, management_key, …).
    client:
        Optional pre-built ``httpx.Client``.  Pass one with a
        ``MockTransport`` in tests; leave *None* in production.

    Raises
    ------
    AuthError
        HTTP 401 or 403.
    TransientError
        Network / transport errors, timeouts, 5xx responses, or a 200
        response whose body is not valid JSON.
    """
    url = f"{cfg.base_url}/usage/export"
    headers = {"Authorization": f"Bearer {cfg.management_key}"}

    _own_client = client is None
    if _own_client:
        client = httpx.Client(timeout=_TIMEOUT)

    try:
        response = client.get(url, headers=headers)
    except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError) as exc:
        raise TransientError(str(exc)) from exc
    finally:
        if _own_client:
            client.close()

    status = response.status_code
    if status in (401, 403):
        raise AuthError(f"HTTP {status}")
    if status >= 500:
        raise TransientError(f"HTTP {status}")
    if status >= 400:
        raise TransientError(f"HTTP {status}")

    try:
        return response.json()
    except Exception as exc:
        raise TransientError(f"Response body is not valid JSON: {exc}") from exc
