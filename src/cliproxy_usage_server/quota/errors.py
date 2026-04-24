"""Exception classes for the quota module."""


class QuotaModuleError(Exception):
    """Base class for quota-module errors."""


class QuotaConfigError(QuotaModuleError):
    """Raised when quota module is misconfigured.

    E.g. missing env vars or unknown provider.
    """


class QuotaUpstreamError(QuotaModuleError):
    """Raised when the CLIProxyAPI management endpoint itself fails."""

    def __init__(self, message: str, *, upstream_status: int | None = None) -> None:
        super().__init__(message)
        self.upstream_status = upstream_status


class QuotaSchemaError(QuotaModuleError):
    """Raised when a provider parser cannot decode the upstream OAuth response."""
