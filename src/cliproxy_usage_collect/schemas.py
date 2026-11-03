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
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    total_tokens: int
    failed: bool
