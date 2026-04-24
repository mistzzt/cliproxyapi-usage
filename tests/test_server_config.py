"""Tests for cliproxy_usage_server.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from cliproxy_usage_server.config import ConfigError, load_config

# Env vars that must be cleared so tests stay hermetic even if the process env
# happens to have them set (e.g. from .envrc).
_ALL_ENVS = (
    "USAGE_DB_PATH",
    "USAGE_SERVER_HOST",
    "USAGE_SERVER_PORT",
    "USAGE_PRICING_CACHE",
    "USAGE_PRICING_TTL_SECONDS",
    "USAGE_PRICING_URL",
    "CLIPROXY_BASE_URL",
    "CLIPROXY_MANAGEMENT_KEY",
    "QUOTA_CACHE_TTL_SECONDS",
)


def _clear_all(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_ENVS:
        monkeypatch.delenv(var, raising=False)


def test_load_config_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch)
    monkeypatch.setenv("USAGE_DB_PATH", "/tmp/test.db")
    monkeypatch.setenv("USAGE_SERVER_HOST", "0.0.0.0")
    monkeypatch.setenv("USAGE_SERVER_PORT", "9000")
    monkeypatch.setenv("USAGE_PRICING_CACHE", "/tmp/pricing.json")
    monkeypatch.setenv("USAGE_PRICING_TTL_SECONDS", "3600")
    monkeypatch.setenv("USAGE_PRICING_URL", "https://example.com/prices.json")

    cfg = load_config()

    assert cfg.db_path == Path("/tmp/test.db")
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9000
    assert cfg.pricing_cache == Path("/tmp/pricing.json")
    assert cfg.pricing_ttl_seconds == 3600
    assert cfg.pricing_url == "https://example.com/prices.json"


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch)

    cfg = load_config()

    assert cfg.db_path == Path("./usage.db")
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8318
    assert cfg.pricing_cache is None
    assert cfg.pricing_ttl_seconds == 86400


def test_load_config_bad_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch)
    monkeypatch.setenv("USAGE_SERVER_PORT", "notanumber")

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "USAGE_SERVER_PORT" in str(exc_info.value)


def test_quota_settings_default_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch)
    cfg = load_config()
    assert cfg.cliproxy_base_url is None
    assert cfg.cliproxy_management_key is None
    assert cfg.quota_cache_ttl_seconds == 300


def test_quota_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch)
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://localhost:8317")
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "secret")
    monkeypatch.setenv("QUOTA_CACHE_TTL_SECONDS", "60")
    cfg = load_config()
    assert cfg.cliproxy_base_url == "http://localhost:8317"
    assert cfg.cliproxy_management_key == "secret"
    assert cfg.quota_cache_ttl_seconds == 60


def test_quota_settings_invalid_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_all(monkeypatch)
    monkeypatch.setenv("QUOTA_CACHE_TTL_SECONDS", "not-a-number")
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "QUOTA_CACHE_TTL_SECONDS" in str(excinfo.value)
