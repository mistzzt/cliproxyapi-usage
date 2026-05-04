"""Parser for cliproxy Redis usage queue payloads."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from pydantic import BaseModel, ConfigDict, ValidationError

from cliproxy_usage_collect.schemas import RequestRecord


class SchemaError(Exception):
    """Raised when a queue payload is malformed or has wrong types."""


class _Tokens(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    total_tokens: int


class _QueuePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    timestamp: str
    api_key: str
    model: str
    source: str
    auth_index: str
    latency_ms: int
    failed: bool
    tokens: _Tokens


def iter_records(queue_elements: Iterable[str | bytes]) -> Iterator[RequestRecord]:
    """Yield one RequestRecord per Redis queue payload element.

    Raises SchemaError if required fields are absent or have wrong types.
    """
    for index, element in enumerate(queue_elements):
        try:
            payload = _QueuePayload.model_validate_json(element)
        except ValidationError as exc:
            msg = f"Queue element {index} failed validation: {exc}"
            raise SchemaError(msg) from exc

        yield RequestRecord(
            timestamp=payload.timestamp,
            api_key=payload.api_key,
            model=payload.model,
            source=payload.source,
            auth_index=payload.auth_index,
            latency_ms=payload.latency_ms,
            input_tokens=payload.tokens.input_tokens,
            output_tokens=payload.tokens.output_tokens,
            reasoning_tokens=payload.tokens.reasoning_tokens,
            cached_tokens=payload.tokens.cached_tokens,
            total_tokens=payload.tokens.total_tokens,
            failed=payload.failed,
        )
