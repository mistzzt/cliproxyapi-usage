"""Tests for usage queue payload parsing."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from cliproxy_usage_collect.parser import SchemaError, parse_records
from cliproxy_usage_collect.schemas import RequestRecord

# Shaped like CLIProxyAPI's queuedUsageDetail; only 1-hour writes are read from tokens.
VALID_PAYLOAD: dict[str, Any] = {
    "timestamp": "2026-05-04T12:34:56.789Z",
    "latency_ms": 1234,
    "ttft_ms": 300,
    "source": "codex-user@example.com",
    "auth_index": "auth-1",
    "tokens": {
        "input_tokens": 900,
        "output_tokens": 20,
        "reasoning_tokens": 3,
        "cached_tokens": 40,
        "cache_read_tokens": 40,
        "cache_read_tokens_present": True,
        "cache_creation_tokens": 60,
        "cache_creation_1h_tokens": 25,
        "total_tokens": 1020,
    },
    "failed": False,
    "generate": True,
    "stream": True,
    "fail": {"status_code": 0, "body": ""},
    "accounting_version": 2,
    "token_breakdown": {
        "schema_version": 2,
        "quality": "complete",
        "total_tokens": 1020,
        "input": {
            "total_tokens": 1000,
            "uncached_tokens": 900,
            "cache_read_tokens": 40,
            "cache_write_tokens": 60,
        },
        "output": {
            "total_tokens": 20,
            "non_reasoning_tokens": 17,
            "reasoning_tokens": 3,
        },
        "unclassified_tokens": 0,
    },
    "provider": "claude",
    "executor_type": "claude",
    "model": "claude-sonnet-4-5",
    "service_tier": "",
    "response_service_tier": "standard",
    "endpoint": "/v1/messages",
    "auth_type": "oauth",
    "api_key": "sk-test",
    "request_id": "req_123",
}


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = copy.deepcopy(VALID_PAYLOAD)
    payload.update(overrides)
    return payload


def _record_from(payload: dict[str, Any]) -> RequestRecord:
    return parse_records([payload]).records[0]


def _breakdown(payload: dict[str, Any]) -> dict[str, Any]:
    return payload["token_breakdown"]


def test_complete_breakdown_fills_each_token_column():
    record = _record_from(VALID_PAYLOAD)

    assert record == RequestRecord(
        timestamp="2026-05-04T12:34:56.789Z",
        api_key="sk-test",
        model="claude-sonnet-4-5",
        source="codex-user@example.com",
        auth_index="auth-1",
        latency_ms=1234,
        input_tokens=1000,
        output_tokens=20,
        reasoning_tokens=3,
        cache_read_tokens=40,
        cache_creation_tokens=60,
        cache_creation_1h_tokens=25,
        total_tokens=1020,
        service_tier="standard",
        ttft_ms=300,
        failed=False,
    )


def test_legacy_token_fields_are_ignored():
    payload = _payload()
    payload["tokens"] = {"input_tokens": "not read", "cache_creation_1h_tokens": 25}

    assert _record_from(payload) == _record_from(VALID_PAYLOAD)


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [({}, 0), ({"cache_creation_1h_tokens": 90}, 60), (None, 0)],
)
def test_cache_creation_1h_defaults_to_0_and_is_capped(
    tokens: dict[str, Any] | None, expected: int
):
    payload = _payload()
    if tokens is None:
        del payload["tokens"]
    else:
        payload["tokens"] = tokens

    assert _record_from(payload).cache_creation_1h_tokens == expected


@pytest.mark.parametrize("tier", [None, ""])
def test_missing_or_empty_service_tier_is_none(tier: str | None):
    payload = _payload()
    if tier is None:
        del payload["response_service_tier"]
    else:
        payload["response_service_tier"] = tier

    assert _record_from(payload).service_tier is None


@pytest.mark.parametrize("ttft", [None, 0])
def test_missing_or_zero_ttft_is_none(ttft: int | None):
    payload = _payload()
    if ttft is None:
        del payload["ttft_ms"]
    else:
        payload["ttft_ms"] = ttft

    assert _record_from(payload).ttft_ms is None


@pytest.mark.parametrize("quality", ["inconsistent", "unclassified"])
def test_incomplete_breakdown_stores_only_total_and_is_counted(quality: str):
    payload = _payload()
    _breakdown(payload).update(quality=quality, total_tokens=1500)
    _breakdown(payload)["unclassified_tokens"] = 480

    batch = parse_records([VALID_PAYLOAD, payload])

    assert batch.incomplete == 1
    record = batch.records[1]
    assert (
        record.input_tokens,
        record.output_tokens,
        record.reasoning_tokens,
        record.cache_read_tokens,
        record.cache_creation_tokens,
        record.cache_creation_1h_tokens,
        record.total_tokens,
    ) == (0, 0, 0, 0, 0, 0, 1500)


def test_missing_breakdown_raises_schema_error():
    payload = _payload()
    del payload["token_breakdown"]

    with pytest.raises(SchemaError):
        parse_records([payload])


@pytest.mark.parametrize("version", [1, 3, "2"])
def test_other_schema_version_raises_schema_error(version: Any):
    payload = _payload()
    _breakdown(payload)["schema_version"] = version

    with pytest.raises(SchemaError):
        parse_records([payload])


@pytest.mark.parametrize(
    "field",
    [
        "timestamp",
        "api_key",
        "model",
        "source",
        "auth_index",
        "latency_ms",
        "failed",
    ],
)
def test_missing_required_top_level_fields_raise_schema_error(field: str):
    payload = _payload()
    del payload[field]

    with pytest.raises(SchemaError):
        parse_records([payload])


@pytest.mark.parametrize(
    "path",
    [
        ("total_tokens",),
        ("quality",),
        ("input", "total_tokens"),
        ("input", "cache_read_tokens"),
        ("input", "cache_write_tokens"),
        ("output", "total_tokens"),
        ("output", "reasoning_tokens"),
    ],
)
def test_missing_breakdown_fields_raise_schema_error(path: tuple[str, ...]):
    payload = _payload()
    parent = _breakdown(payload)
    for key in path[:-1]:
        parent = parent[key]
    del parent[path[-1]]

    with pytest.raises(SchemaError):
        parse_records([payload])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latency_ms", "1234"),
        ("failed", "false"),
    ],
)
def test_wrong_top_level_types_raise_schema_error(field: str, value: Any):
    payload = _payload(**{field: value})

    with pytest.raises(SchemaError):
        parse_records([payload])


def test_wrong_token_type_raises_schema_error():
    payload = _payload()
    _breakdown(payload)["input"]["total_tokens"] = "1000"

    with pytest.raises(SchemaError):
        parse_records([payload])


def test_schema_error_identifies_batch_element_index():
    bad_payload = _payload(latency_ms="1234")

    with pytest.raises(SchemaError, match="Queue element 1 failed validation"):
        parse_records([VALID_PAYLOAD, bad_payload])


def test_unstored_queue_fields_are_accepted_but_ignored():
    payload = _payload(
        provider={"unexpected": "shape"},
        endpoint=None,
        auth_type=123,
        request_id=["ignored"],
    )

    assert _record_from(payload) == _record_from(VALID_PAYLOAD)
    assert set(RequestRecord.model_fields) == {
        "timestamp",
        "api_key",
        "model",
        "source",
        "auth_index",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "cache_creation_1h_tokens",
        "total_tokens",
        "service_tier",
        "ttft_ms",
        "failed",
    }
