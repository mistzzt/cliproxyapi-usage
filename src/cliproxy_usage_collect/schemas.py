"""Shared schemas used by the collector and (eventually) the webapp.

These are the user-facing shapes — the wire format the webapp exposes and
the row shape the collector writes. Keep ingestion-only validation models
(the ones that mirror the proxy's export JSON) private to ``parser``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class RequestRecord(BaseModel):
    """A single request row.

    Immutable. Used by the parser (yield), the DB layer (insert), and the
    webapp (FastAPI response models can embed or subclass this).
    """

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
