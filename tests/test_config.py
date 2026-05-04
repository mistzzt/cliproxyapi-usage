from pathlib import Path

import pytest
from pydantic import ValidationError

from cliproxy_usage_collect.config import Config, ConfigError, load_config


def test_missing_management_key_raises(monkeypatch):
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("USAGE_DB_PATH", raising=False)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "CLIPROXY_MANAGEMENT_KEY" in str(exc_info.value)


def test_defaults_when_only_key_is_set(monkeypatch):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "secret")
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("USAGE_DB_PATH", raising=False)

    config = load_config()

    assert config.management_key == "secret"
    assert config.base_url == "http://localhost:8317/v0/management"
    assert config.db_path == Path("./usage.db")


def test_explicit_overrides_win(monkeypatch):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://example.com/api")
    monkeypatch.setenv("USAGE_DB_PATH", "/tmp/custom.db")

    config = load_config()

    assert config.management_key == "mykey"
    assert config.base_url == "http://example.com/api"
    assert config.db_path == Path("/tmp/custom.db")


def test_config_is_frozen():
    config = Config(
        base_url="http://localhost:8317/v0/management",
        management_key="k",
        db_path=Path("./usage.db"),
    )
    with pytest.raises(ValidationError):
        config.management_key = "changed"  # type: ignore[misc]
