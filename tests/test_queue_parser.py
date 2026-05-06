"""Tests for usage queue payload parsing."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from cliproxy_usage_collect.parser import SchemaError, iter_records
from cliproxy_usage_collect.schemas import RequestRecord

VALID_PAYLOAD: dict[str, Any] = {
    "timestamp": "2026-05-04T12:34:56.789Z",
    "latency_ms": 1234,
    "source": "codex-user@example.com",
    "auth_index": "auth-1",
    "tokens": {
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": 3,
        "cached_tokens": 40,
        "total_tokens": 163,
    },
    "failed": False,
    "provider": "openai",
    "model": "gpt-5.4",
    "endpoint": "/v1/responses",
    "auth_type": "api_key",
    "api_key": "sk-test",
    "request_id": "req_123",
}


def _payload(**overrides: Any) -> dict[str, Any]:
    payload = json.loads(json.dumps(VALID_PAYLOAD))
    payload.update(overrides)
    return payload


def _record_from(payload: dict[str, Any]) -> RequestRecord:
    return next(iter_records([json.dumps(payload)]))


def test_valid_single_queue_json_object_maps_to_request_record():
    record = _record_from(VALID_PAYLOAD)

    assert record == RequestRecord(
        timestamp="2026-05-04T12:34:56.789Z",
        api_key="sk-test",
        model="gpt-5.4",
        source="codex-user@example.com",
        auth_index="auth-1",
        latency_ms=1234,
        input_tokens=100,
        output_tokens=20,
        reasoning_tokens=3,
        cached_tokens=40,
        total_tokens=163,
        failed=False,
    )


def test_valid_queue_json_bytes_map_to_request_record():
    record = next(iter_records([json.dumps(VALID_PAYLOAD).encode()]))

    assert record.api_key == "sk-test"
    assert record.model == "gpt-5.4"
    assert record.total_tokens == 163


def test_iter_records_returns_iterator_not_list():
    result = iter_records([json.dumps(VALID_PAYLOAD)])

    assert isinstance(result, Iterator)
    assert not isinstance(result, list)


@pytest.mark.parametrize("queue_element", ["{", b"{"])
def test_invalid_json_string_and_bytes_raise_schema_error(queue_element: str | bytes):
    with pytest.raises(SchemaError):
        list(iter_records([queue_element]))


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
        "tokens",
    ],
)
def test_missing_required_top_level_fields_raise_schema_error(field: str):
    payload = _payload()
    del payload[field]

    with pytest.raises(SchemaError):
        list(iter_records([json.dumps(payload)]))


@pytest.mark.parametrize(
    "field",
    [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "total_tokens",
    ],
)
def test_missing_token_fields_raise_schema_error(field: str):
    payload = _payload()
    del payload["tokens"][field]

    with pytest.raises(SchemaError):
        list(iter_records([json.dumps(payload)]))


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
        list(iter_records([json.dumps(payload)]))


def test_wrong_token_type_raises_schema_error():
    payload = _payload()
    payload["tokens"]["total_tokens"] = "163"

    with pytest.raises(SchemaError):
        list(iter_records([json.dumps(payload)]))


def test_schema_error_identifies_batch_element_index():
    bad_payload = _payload(latency_ms="1234")

    with pytest.raises(SchemaError, match="Queue element 1 failed validation"):
        list(iter_records([json.dumps(VALID_PAYLOAD), json.dumps(bad_payload)]))


def test_unstored_queue_fields_are_accepted_but_ignored():
    payload = _payload(
        provider={"unexpected": "shape"},
        endpoint=None,
        auth_type=123,
        request_id=["ignored"],
    )

    record = _record_from(payload)

    assert record.model_dump() == {
        "timestamp": "2026-05-04T12:34:56.789Z",
        "api_key": "sk-test",
        "model": "gpt-5.4",
        "source": "codex-user@example.com",
        "auth_index": "auth-1",
        "latency_ms": 1234,
        "input_tokens": 100,
        "output_tokens": 20,
        "reasoning_tokens": 3,
        "cached_tokens": 40,
        "total_tokens": 163,
        "failed": False,
    }
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
        "cached_tokens",
        "total_tokens",
        "failed",
    }


def test_old_nested_usage_export_shape_raises_schema_error():
    old_export = {
        "usage": {
            "apis": {
                "sk-test": {
                    "models": {
                        "gpt-5.4": {
                            "details": [
                                {
                                    "timestamp": "2026-05-04T12:34:56.789Z",
                                    "source": "codex-user@example.com",
                                    "auth_index": "auth-1",
                                    "latency_ms": 1234,
                                    "failed": False,
                                    "tokens": VALID_PAYLOAD["tokens"],
                                }
                            ]
                        }
                    }
                }
            }
        }
    }

    with pytest.raises(SchemaError):
        list(iter_records([json.dumps(old_export)]))
