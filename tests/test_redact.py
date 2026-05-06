import pytest

from cliproxy_usage_server.redact import redact_key, redact_source
from cliproxy_usage_server.schemas import ApiStat


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("sk-proj-abc123xyz", "sk-*******-abc123xyz"),
        ("sk-abc123xyz9", "*******xyz9"),
        ("abc123xyz9", "*******xyz9"),
        ("xy", "*******xy"),
        ("", "*******"),
        ("sk-team-proj-abcd", "sk-*******-abcd"),  # >3 parts: first + last
    ],
)
def test_redact_key(raw: str, expected: str) -> None:
    assert redact_key(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "sk-proj-abc123xyz",
        "sk-abc123xyz9",
        "abc123xyz9",
        "xy",
        "",
        "sk-team-proj-abcd",
    ],
)
def test_redact_key_is_idempotent(raw: str) -> None:
    once = redact_key(raw)
    assert redact_key(once) == once


def test_api_stat_redacts_api_key_on_construction() -> None:
    stat = ApiStat(
        api_key="sk-proj-abc123xyz",
        requests=1,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        failed=0,
        avg_latency_ms=0.0,
        cost=None,
        cost_status="missing",
    )
    assert stat.api_key == "sk-*******-abc123xyz"


@pytest.mark.parametrize(
    "raw,expected",
    [
        # OAuth emails pass through unchanged
        ("codex-user@example.test", "codex-user@example.test"),
        ("claude-user@example.test", "claude-user@example.test"),
        ("team-account@example.test", "team-account@example.test"),
        # Non-email values are treated as keys/opaque secrets
        ("openai:sk-proj-abc123xyz", "openai:sk-*******-abc123xyz"),
        ("anthropic:sk-ant-12345678", "anthropic:sk-*******-12345678"),
        ("openai-compat:sk-team-proj-abcd", "openai-*******-abcd"),
        ("openai:abc123xyz9", "*******xyz9"),
        ("sk-rawkey-abc-1234", "sk-*******-1234"),
        ("rawkey1234", "*******1234"),
        # Empty
        ("", ""),
    ],
)
def test_redact_source(raw: str, expected: str) -> None:
    assert redact_source(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "codex-user@example.test",
        "openai:sk-proj-abc123xyz",
        "openai:abc123xyz9",
        "sk-rawkey-abc-1234",
        "",
    ],
)
def test_redact_source_idempotent(raw: str) -> None:
    once = redact_source(raw)
    twice = redact_source(once)
    assert once == twice
