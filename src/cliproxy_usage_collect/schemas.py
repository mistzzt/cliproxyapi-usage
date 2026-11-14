"""The row shape the collector writes, which the server reads back from SQLite.

Ingestion-only models that mirror the proxy's queue JSON stay private to ``parser``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RequestRecord(BaseModel):
    """One request row: yielded by the parser, inserted by the DB layer."""

    model_config = ConfigDict(frozen=True)

    timestamp: str
    api_key: str
    model: str
    source: str
    auth_index: str
    latency_ms: int
    input_tokens: int  # all input: uncached + cache read + cache creation
    output_tokens: int  # all output, reasoning included
    reasoning_tokens: int  # part of output_tokens
    cache_read_tokens: int  # part of input_tokens
    cache_creation_tokens: int  # part of input_tokens
    cache_creation_1h_tokens: int  # part of cache_creation_tokens, 1-hour lifetime
    total_tokens: int  # upstream's reported total; never read
    service_tier: str | None  # tier the response was billed at; None when unknown
    ttft_ms: int | None  # time to first token; None when not measured
    failed: bool
