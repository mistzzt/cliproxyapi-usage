"""Parser for cliproxy usage-export JSON blobs."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from cliproxy_usage.schemas import RequestRecord


class SchemaError(Exception):
    """Raised when the export payload is missing required fields or has wrong types."""


class _Tokens(BaseModel):
    model_config = ConfigDict(extra="ignore")
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    total_tokens: int


class _Detail(BaseModel):
    model_config = ConfigDict(extra="ignore")
    timestamp: str
    source: str
    auth_index: str
    latency_ms: int
    failed: bool
    tokens: _Tokens


class _ModelEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    details: list[_Detail]


class _ApiEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")
    models: dict[str, _ModelEntry]


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    apis: dict[str, _ApiEntry]


class _Export(BaseModel):
    model_config = ConfigDict(extra="ignore")
    usage: _Usage


def iter_records(export: dict[str, Any]) -> Iterator[RequestRecord]:
    """Yield one RequestRecord per detail row in the export.

    Raises SchemaError if required fields are absent or have wrong types.
    """
    try:
        parsed = _Export.model_validate(export)
    except ValidationError as exc:
        raise SchemaError(str(exc)) from exc

    for api_key, api in parsed.usage.apis.items():
        for model, model_entry in api.models.items():
            for d in model_entry.details:
                yield RequestRecord(
                    timestamp=d.timestamp,
                    api_key=api_key,
                    model=model,
                    source=d.source,
                    auth_index=d.auth_index,
                    latency_ms=d.latency_ms,
                    input_tokens=d.tokens.input_tokens,
                    output_tokens=d.tokens.output_tokens,
                    reasoning_tokens=d.tokens.reasoning_tokens,
                    cached_tokens=d.tokens.cached_tokens,
                    total_tokens=d.tokens.total_tokens,
                    failed=d.failed,
                )
