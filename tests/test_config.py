from pathlib import Path

import pytest
from pydantic import ValidationError

from cliproxy_usage_collect.config import Config, ConfigError, load_config


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [
        "CLIPROXY_MANAGEMENT_KEY",
        "CLIPROXY_BASE_URL",
        "USAGE_DB_PATH",
        "USAGE_QUEUE_POP_COUNT",
        "USAGE_HTTP_TIMEOUT_SECONDS",
    ]:
        monkeypatch.delenv(name, raising=False)


def test_missing_management_key_raises(monkeypatch):
    _clear_env(monkeypatch)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "CLIPROXY_MANAGEMENT_KEY" in str(exc_info.value)


def test_defaults_when_only_key_is_set(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "secret")

    config = load_config()

    assert config.management_key == "secret"
    assert config.base_url == "http://localhost:8317"
    assert config.db_path == Path("./usage.db")
    assert config.queue_pop_count == 500
    assert config.http_timeout_seconds == 10.0


def test_explicit_overrides_win(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://example.com/api")
    monkeypatch.setenv("USAGE_DB_PATH", "/tmp/custom.db")
    monkeypatch.setenv("USAGE_QUEUE_POP_COUNT", "37")
    monkeypatch.setenv("USAGE_HTTP_TIMEOUT_SECONDS", "2.5")

    config = load_config()

    assert config.management_key == "mykey"
    assert config.base_url == "http://example.com/api"
    assert config.db_path == Path("/tmp/custom.db")
    assert config.queue_pop_count == 37
    assert config.http_timeout_seconds == 2.5


@pytest.mark.parametrize("value", ["1", "500", "10000"])
def test_queue_pop_count_accepts_valid_values(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_QUEUE_POP_COUNT", value)

    config = load_config()

    assert config.queue_pop_count == int(value)


@pytest.mark.parametrize("value", ["0", "10001", "-1"])
def test_queue_pop_count_rejects_out_of_range_values(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_QUEUE_POP_COUNT", value)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "USAGE_QUEUE_POP_COUNT" in str(exc_info.value)


@pytest.mark.parametrize("value", ["0.1", "10.0", "45"])
def test_http_timeout_accepts_positive_values(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_HTTP_TIMEOUT_SECONDS", value)

    config = load_config()

    assert config.http_timeout_seconds == float(value)


@pytest.mark.parametrize("value", ["0", "-0.5"])
def test_http_timeout_rejects_non_positive_values(monkeypatch, value):
    _clear_env(monkeypatch)
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_HTTP_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "USAGE_HTTP_TIMEOUT_SECONDS" in str(exc_info.value)


def test_config_is_frozen():
    config = Config(
        base_url="http://localhost:8317",
        management_key="k",
        db_path=Path("./usage.db"),
        queue_pop_count=500,
        http_timeout_seconds=10.0,
    )
    with pytest.raises(ValidationError):
        config.management_key = "changed"  # type: ignore[misc]
