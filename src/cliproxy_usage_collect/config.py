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
        default="http://localhost:8317",
        validation_alias=AliasChoices("base_url", "CLIPROXY_BASE_URL"),
    )
    management_key: str = Field(
        validation_alias=AliasChoices("management_key", "CLIPROXY_MANAGEMENT_KEY"),
    )
    db_path: Path = Field(
        default=Path("./usage.db"),
        validation_alias=AliasChoices("db_path", "USAGE_DB_PATH"),
    )
    queue_pop_count: int = Field(
        default=500,
        ge=1,
        le=10000,
        validation_alias=AliasChoices("queue_pop_count", "USAGE_QUEUE_POP_COUNT"),
    )
    http_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        validation_alias=AliasChoices(
            "http_timeout_seconds", "USAGE_HTTP_TIMEOUT_SECONDS"
        ),
    )


def load_config() -> Config:
    """Load the collector's configuration from environment variables."""
    return load_settings(Config)


def load_settings[T: BaseSettings](cls: type[T]) -> T:
    """Instantiate settings from the environment.

    Raises ConfigError naming the env vars (not fields) that are missing or invalid.
    """
    try:
        return cls()  # pyright: ignore[reportCallIssue]  # fields come from env
    except ValidationError as exc:
        errors = exc.errors()
        missing = [
            _env_name(cls, err["loc"][0]) for err in errors if err["type"] == "missing"
        ]
        if missing:
            raise ConfigError(
                f"Missing required environment variable: {', '.join(missing)}"
            ) from exc
        invalid = dict.fromkeys(_env_name(cls, err["loc"][0]) for err in errors)
        raise ConfigError(
            f"Invalid value for environment variable: {', '.join(invalid)}\n{exc}"
        ) from exc


def _env_name(cls: type[BaseSettings], field_name: object) -> str:
    """Return a settings field's upper-case env-var alias, else the field name."""
    field = cls.model_fields.get(str(field_name))
    if field is not None and isinstance(field.validation_alias, AliasChoices):
        for choice in field.validation_alias.choices:
            if isinstance(choice, str) and choice.isupper():
                return choice
    return str(field_name)


_MANAGEMENT_PATH = "/v0/management"


def management_url(base_url: str, path: str) -> str:
    """URL of management API `path`, from the CLIProxyAPI origin or management base."""
    base = base_url.rstrip("/").removesuffix(_MANAGEMENT_PATH)
    return f"{base}{_MANAGEMENT_PATH}/{path}"
