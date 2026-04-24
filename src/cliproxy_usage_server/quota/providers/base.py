"""Base protocol for quota providers."""

from __future__ import annotations

from typing import ClassVar, Literal, Protocol, runtime_checkable

from cliproxy_usage_server.schemas import ProviderQuota


@runtime_checkable
class Provider(Protocol):
    """Protocol for quota providers."""

    provider_id: ClassVar[Literal["claude", "codex"]]
    auth_type: ClassVar[str]

    def build_api_call_payload(self, auth_name: str) -> dict[str, object]:
        """Build the payload to send to the provider's OAuth quota endpoint."""
        ...

    def parse(
        self, upstream_body: object, upstream_status: int, *, auth_name: str
    ) -> ProviderQuota:
        """Parse the upstream OAuth response into a ProviderQuota."""
        ...
