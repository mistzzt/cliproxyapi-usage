from pathlib import Path

import pytest
from pydantic import ValidationError

from cliproxy_usage_collect.config import Config, ConfigError, load_config


def test_missing_management_key_raises(monkeypatch):
    monkeypatch.delenv("CLIPROXY_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("USAGE_DB_PATH", raising=False)
    monkeypatch.delenv("USAGE_QUEUE_KEY", raising=False)
    monkeypatch.delenv("USAGE_QUEUE_POP_COUNT", raising=False)
    monkeypatch.delenv("USAGE_QUEUE_POP_SIDE", raising=False)
    monkeypatch.delenv("USAGE_REDIS_SOCKET_TIMEOUT_SECONDS", raising=False)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "CLIPROXY_MANAGEMENT_KEY" in str(exc_info.value)


def test_defaults_when_only_key_is_set(monkeypatch):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "secret")
    monkeypatch.delenv("CLIPROXY_BASE_URL", raising=False)
    monkeypatch.delenv("USAGE_DB_PATH", raising=False)
    monkeypatch.delenv("USAGE_QUEUE_KEY", raising=False)
    monkeypatch.delenv("USAGE_QUEUE_POP_COUNT", raising=False)
    monkeypatch.delenv("USAGE_QUEUE_POP_SIDE", raising=False)
    monkeypatch.delenv("USAGE_REDIS_SOCKET_TIMEOUT_SECONDS", raising=False)

    config = load_config()

    assert config.management_key == "secret"
    assert config.base_url == "http://localhost:8317"
    assert config.db_path == Path("./usage.db")
    assert config.queue_key == "queue"
    assert config.queue_pop_count == 500
    assert config.queue_pop_side == "left"
    assert config.redis_socket_timeout_seconds == 10.0


def test_explicit_overrides_win(monkeypatch):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("CLIPROXY_BASE_URL", "http://example.com/api")
    monkeypatch.setenv("USAGE_DB_PATH", "/tmp/custom.db")
    monkeypatch.setenv("USAGE_QUEUE_KEY", "usage:queue")
    monkeypatch.setenv("USAGE_QUEUE_POP_COUNT", "37")
    monkeypatch.setenv("USAGE_QUEUE_POP_SIDE", "right")
    monkeypatch.setenv("USAGE_REDIS_SOCKET_TIMEOUT_SECONDS", "2.5")

    config = load_config()

    assert config.management_key == "mykey"
    assert config.base_url == "http://example.com/api"
    assert config.db_path == Path("/tmp/custom.db")
    assert config.queue_key == "usage:queue"
    assert config.queue_pop_count == 37
    assert config.queue_pop_side == "right"
    assert config.redis_socket_timeout_seconds == 2.5


@pytest.mark.parametrize("value", ["1", "500", "10000"])
def test_queue_pop_count_accepts_valid_values(monkeypatch, value):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_QUEUE_POP_COUNT", value)

    config = load_config()

    assert config.queue_pop_count == int(value)


@pytest.mark.parametrize("value", ["0", "10001", "-1"])
def test_queue_pop_count_rejects_out_of_range_values(monkeypatch, value):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_QUEUE_POP_COUNT", value)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "USAGE_QUEUE_POP_COUNT" in str(exc_info.value)


@pytest.mark.parametrize("value", ["left", "right"])
def test_queue_pop_side_accepts_valid_values(monkeypatch, value):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_QUEUE_POP_SIDE", value)

    config = load_config()

    assert config.queue_pop_side == value


def test_queue_pop_side_rejects_invalid_values(monkeypatch):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_QUEUE_POP_SIDE", "middle")

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "USAGE_QUEUE_POP_SIDE" in str(exc_info.value)


@pytest.mark.parametrize("value", ["0.1", "10.0", "45"])
def test_redis_socket_timeout_accepts_positive_values(monkeypatch, value):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_REDIS_SOCKET_TIMEOUT_SECONDS", value)

    config = load_config()

    assert config.redis_socket_timeout_seconds == float(value)


@pytest.mark.parametrize("value", ["0", "-0.5"])
def test_redis_socket_timeout_rejects_non_positive_values(monkeypatch, value):
    monkeypatch.setenv("CLIPROXY_MANAGEMENT_KEY", "mykey")
    monkeypatch.setenv("USAGE_REDIS_SOCKET_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert "USAGE_REDIS_SOCKET_TIMEOUT_SECONDS" in str(exc_info.value)


def test_config_is_frozen():
    config = Config(
        base_url="http://localhost:8317",
        management_key="k",
        db_path=Path("./usage.db"),
        queue_key="queue",
        queue_pop_count=500,
        queue_pop_side="left",
        redis_socket_timeout_seconds=10.0,
    )
    with pytest.raises(ValidationError):
        config.management_key = "changed"  # type: ignore[misc]
