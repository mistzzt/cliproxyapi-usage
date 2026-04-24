"""Runtime configuration, loaded from environment variables via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Config(BaseSettings):
    """Immutable runtime configuration sourced from the process environment."""

    model_config = SettingsConfigDict(frozen=True)

    base_url: str = Field(
        default="http://localhost:8317/v0/management",
        validation_alias=AliasChoices("base_url", "CLIPROXY_BASE_URL"),
    )
    management_key: str = Field(
        validation_alias=AliasChoices("management_key", "CLIPROXY_MANAGEMENT_KEY"),
    )
    db_path: Path = Field(
        default=Path("./usage.db"),
        validation_alias=AliasChoices("db_path", "USAGE_DB_PATH"),
    )


def load_config() -> Config:
    """Load configuration from environment variables.

    Raises ConfigError, with env-var names in the message, if required
    variables are missing or values are invalid.
    """
    try:
        return Config()  # pyright: ignore[reportCallIssue]  # fields come from env
    except ValidationError as exc:
        missing = [
            _env_name(str(err["loc"][0]))
            for err in exc.errors()
            if err.get("type") == "missing"
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variable: {', '.join(missing)}"
            ) from exc
        raise ConfigError(str(exc)) from exc


def _env_name(field_name: str) -> str:
    """Return the env-var alias for a Config field, falling back to the field name."""
    field = Config.model_fields.get(field_name)
    if field is not None and isinstance(field.validation_alias, AliasChoices):
        for choice in field.validation_alias.choices:
            if isinstance(choice, str) and choice.isupper():
                return choice
    return field_name
