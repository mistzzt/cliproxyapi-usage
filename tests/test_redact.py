import pytest

from cliproxy_usage_server.redact import redact_key
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
    )
    assert stat.api_key == "sk-*******-abc123xyz"


