"""Tests for quota provider registry."""

from cliproxy_usage_server.quota.providers import PROVIDERS
from cliproxy_usage_server.quota.providers.base import Provider


def test_registry_contains_claude_and_codex():
    assert set(PROVIDERS.keys()) == {"claude", "codex"}


def test_every_registered_entry_implements_protocol():
    for key, provider in PROVIDERS.items():
        assert isinstance(provider, Provider)
        assert provider.provider_id == key
