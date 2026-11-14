"""Parser for cliproxy usage queue payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from cliproxy_usage_collect.schemas import RequestRecord


class SchemaError(Exception):
    """Raised when a queue payload is malformed or has wrong types."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)


class _InputBreakdown(_Model):
    total_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


class _OutputBreakdown(_Model):
    total_tokens: int
    reasoning_tokens: int


class _TokenBreakdown(_Model):
    schema_version: Literal[2]
    quality: str
    total_tokens: int
    input: _InputBreakdown
    output: _OutputBreakdown


class _Tokens(_Model):
    """The legacy ``tokens`` object; only its 1-hour cache writes are read."""

    cache_creation_1h_tokens: int = 0


class _QueuePayload(_Model):
    timestamp: str
    api_key: str
    model: str
    source: str
    auth_index: str
    latency_ms: int
    ttft_ms: int = 0  # 0 when neither a first token nor packet was observed
    response_service_tier: str = ""
    failed: bool
    tokens: _Tokens = _Tokens()
    token_breakdown: _TokenBreakdown


class ParsedBatch(NamedTuple):
    records: list[RequestRecord]
    incomplete: int  # records whose breakdown had no split, stored with 0 tokens


def _record(payload: _QueuePayload) -> RequestRecord:
    tokens = payload.token_breakdown
    complete = tokens.quality == "complete"
    cache_creation = tokens.input.cache_write_tokens if complete else 0
    return RequestRecord(
        timestamp=payload.timestamp,
        api_key=payload.api_key,
        model=payload.model,
        source=payload.source,
        auth_index=payload.auth_index,
        latency_ms=payload.latency_ms,
        input_tokens=tokens.input.total_tokens if complete else 0,
        output_tokens=tokens.output.total_tokens if complete else 0,
        reasoning_tokens=tokens.output.reasoning_tokens if complete else 0,
        cache_read_tokens=tokens.input.cache_read_tokens if complete else 0,
        cache_creation_tokens=cache_creation,
        # 1-hour writes are part of the stored cache creation, never more.
        cache_creation_1h_tokens=min(
            payload.tokens.cache_creation_1h_tokens, cache_creation
        ),
        total_tokens=tokens.total_tokens,
        service_tier=payload.response_service_tier or None,
        ttft_ms=payload.ttft_ms or None,
        failed=payload.failed,
    )


def parse_records(queue_elements: Iterable[Mapping[str, Any]]) -> ParsedBatch:
    """One RequestRecord per queue element, token columns from its ``token_breakdown``.

    Raises SchemaError if required fields are absent or have wrong types, the
    breakdown is missing, or its ``schema_version`` is not 2.
    """
    records: list[RequestRecord] = []
    incomplete = 0
    for index, element in enumerate(queue_elements):
        try:
            payload = _QueuePayload.model_validate(element)
        except ValidationError as exc:
            msg = f"Queue element {index} failed validation: {exc}"
            raise SchemaError(msg) from exc
        if payload.token_breakdown.quality != "complete":
            incomplete += 1
        records.append(_record(payload))
    return ParsedBatch(records, incomplete)
