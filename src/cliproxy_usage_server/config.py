"""Runtime configuration for the usage server, loaded from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class ServerConfig(BaseSettings):
    """Immutable runtime configuration for the usage server."""

    model_config = SettingsConfigDict(frozen=True)

    db_path: Path = Field(
        default=Path("./usage.db"),
        validation_alias=AliasChoices("db_path", "USAGE_DB_PATH"),
    )
    host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("host", "USAGE_SERVER_HOST"),
    )
    port: int = Field(
        default=8318,
        validation_alias=AliasChoices("port", "USAGE_SERVER_PORT"),
    )
    pricing_cache: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("pricing_cache", "USAGE_PRICING_CACHE"),
    )
    pricing_ttl_seconds: int = Field(
        default=86400,
        validation_alias=AliasChoices(
            "pricing_ttl_seconds", "USAGE_PRICING_TTL_SECONDS"
        ),
    )
    pricing_url: str = Field(
        default=LITELLM_PRICING_URL,
        validation_alias=AliasChoices("pricing_url", "USAGE_PRICING_URL"),
    )
    base_path: str = Field(
        default="/",
        validation_alias=AliasChoices("base_path", "USAGE_BASE_PATH"),
    )
    cliproxy_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("cliproxy_base_url", "CLIPROXY_BASE_URL"),
    )
    cliproxy_management_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "cliproxy_management_key", "CLIPROXY_MANAGEMENT_KEY"
        ),
    )
    quota_cache_ttl_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "quota_cache_ttl_seconds", "QUOTA_CACHE_TTL_SECONDS"
        ),
    )

    @field_validator("base_path")
    @classmethod
    def _normalize_base_path(cls, value: str) -> str:
        value = value.strip()
        if value == "" or value == "/":
            return "/"
        if not value.startswith("/"):
            raise ValueError("base path must start with /")
        if value.startswith("//"):
            raise ValueError("base path must not start with //")
        if "?" in value or "#" in value:
            raise ValueError("base path must not include query or fragment")
        return value.rstrip("/")


def load_config() -> ServerConfig:
    """Load configuration from environment variables.

    Raises ConfigError, with env-var names in the message, if required
    variables are missing or values are invalid.
    """
    try:
        return ServerConfig()  # pyright: ignore[reportCallIssue]  # fields come from env
    except ValidationError as exc:
        errors = exc.errors()
        missing = [
            _env_name(str(err["loc"][0]))
            for err in errors
            if err.get("type") == "missing"
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variable: {', '.join(missing)}"
            ) from exc
        # Handle other validation errors (e.g. int_parsing for bad port)
        invalid = [_env_name(str(err["loc"][0])) for err in errors if err.get("loc")]
        if invalid:
            raise ConfigError(
                f"Invalid value for environment variable: {', '.join(invalid)}"
            ) from exc
        raise ConfigError(str(exc)) from exc


def _env_name(field_name: str) -> str:
    """Return the env-var alias for a ServerConfig field, falling back to field name."""
    field = ServerConfig.model_fields.get(field_name)
    if field is not None and isinstance(field.validation_alias, AliasChoices):
        for choice in field.validation_alias.choices:
            if isinstance(choice, str) and choice.isupper():
                return choice
    return field_name
