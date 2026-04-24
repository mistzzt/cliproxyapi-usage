"""Provider implementations and registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from cliproxy_usage_server.quota.providers.claude import ClaudeProvider
from cliproxy_usage_server.quota.providers.codex import CodexProvider

if TYPE_CHECKING:
    from cliproxy_usage_server.quota.providers.base import Provider

# Registry of provider instances. Populated by Tasks 2.2 and 2.3.
PROVIDERS: Mapping[str, Provider] = {
    "claude": ClaudeProvider(),
    "codex": CodexProvider(),
}
