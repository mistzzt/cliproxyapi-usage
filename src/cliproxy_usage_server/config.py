"""Runtime configuration for the usage server, loaded from environment variables."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import AliasChoices, Field, TypeAdapter, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cliproxy_usage_collect.config import ConfigError, load_settings

_log = logging.getLogger(__name__)

LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"


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
    page_title: str = Field(
        default="CLIProxyAPI Usage Dashboard",
        validation_alias=AliasChoices("page_title", "USAGE_PAGE_TITLE"),
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
    key_aliases: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("key_aliases", "USAGE_KEY_ALIASES"),
    )
    quota_cache_ttl_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices(
            "quota_cache_ttl_seconds", "QUOTA_CACHE_TTL_SECONDS"
        ),
    )

    cell_cache: bool = Field(
        default=True,
        validation_alias=AliasChoices("cell_cache", "USAGE_CELL_CACHE"),
    )
    cell_cache_max_mb: float = Field(
        default=512.0,
        ge=0,
        validation_alias=AliasChoices("cell_cache_max_mb", "USAGE_CELL_CACHE_MAX_MB"),
    )
    cell_cache_verify_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "cell_cache_verify_seconds", "USAGE_CELL_CACHE_VERIFY_SECONDS"
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
    """Load the server's configuration from environment variables."""
    return load_settings(ServerConfig)


_ALIASES_ADAPTER = TypeAdapter(dict[str, str])


def load_key_aliases(path: Path | None) -> dict[str, str]:
    """Load the redacted-key to display-name map from the ``USAGE_KEY_ALIASES`` file.

    An unset or absent file yields an empty map; malformed content raises ConfigError.
    """
    if path is None:
        return {}
    try:
        raw = path.read_text()
    except FileNotFoundError:
        _log.warning("USAGE_KEY_ALIASES file %s not found; using redacted keys", path)
        return {}
    try:
        return _ALIASES_ADAPTER.validate_python(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ConfigError(
            "Invalid value for environment variable: USAGE_KEY_ALIASES "
            f"({path} must hold a JSON object of string to string)"
        ) from exc
