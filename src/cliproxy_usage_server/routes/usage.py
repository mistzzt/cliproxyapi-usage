"""Usage endpoints: /api/usage/summary, /breakdown, /export, /options.

Range-consuming endpoints take ``start`` (optional; absent = all time) and
``end`` (defaults to server now) as ISO-8601 instants with an offset, ``tz`` (a
required IANA name that drives bucketing), and optional comma-separated
``people`` (redacted keys), ``models`` and ``credentials`` (redacted sources)
filters. Naive datetimes, ``start > end`` and unknown ``tz`` are 422s.

``bucket`` is ``auto`` (default) or an explicit ``hour``/``day``/``week``;
explicit buckets coarsen past 1,000 buckets and the effective one is echoed.
"""

import logging
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from cliproxy_usage_server.aggregate import (
    Filters,
    Window,
    query_breakdown,
    query_export,
    query_options,
    query_summary,
    resolve_filter,
)
from cliproxy_usage_server.cell_cache import CellCache
from cliproxy_usage_server.db import Bucket, open_ro
from cliproxy_usage_server.pricing import ModelPricing
from cliproxy_usage_server.schemas import (
    BreakdownResponse,
    Dimension,
    OptionsResponse,
    SummaryResponse,
)

_log = logging.getLogger(__name__)

_ListParam = Annotated[str | None, Query()]
_BucketParam = Annotated[Bucket | Literal["auto"], Query()]


def _parse_list(value: str | None) -> list[str] | None:
    """Split a comma-separated filter; absent or blank means no filter."""
    if value is None:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def _resolve_window(start: datetime | None, end: datetime | None) -> Window:
    """Validate the window and normalise it to UTC."""
    if start is not None and start.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="start must include a timezone offset"
        )
    if end is not None and end.tzinfo is None:
        raise HTTPException(
            status_code=422, detail="end must include a timezone offset"
        )
    resolved_start = start.astimezone(UTC) if start is not None else None
    resolved_end = (end if end is not None else datetime.now(UTC)).astimezone(UTC)
    if resolved_start is not None and resolved_start > resolved_end:
        raise HTTPException(status_code=422, detail="start must be <= end")
    return Window(resolved_start, resolved_end)


def _resolve_tz(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"unknown timezone: {tz}") from exc


def _bucket(value: Bucket | Literal["auto"]) -> Bucket | None:
    return None if value == "auto" else value


def _get_pricing(request: Request) -> Mapping[str, ModelPricing]:
    return request.app.state.pricing  # type: ignore[no-any-return]


class _RangeParams:
    """Shared query parameters for range-consuming endpoints."""

    def __init__(
        self,
        tz: Annotated[str, Query()],
        start: Annotated[datetime | None, Query()] = None,
        end: Annotated[datetime | None, Query()] = None,
        people: _ListParam = None,
        models: _ListParam = None,
        credentials: _ListParam = None,
    ) -> None:
        self.window = _resolve_window(start, end)
        self.tz = _resolve_tz(tz)
        self.people = _parse_list(people)
        self.models = _parse_list(models)
        self.credentials = _parse_list(credentials)

    def filters(self, conn: sqlite3.Connection, cache: CellCache | None) -> Filters:
        """Resolve the display-key filters to raw column values."""
        return Filters(
            api_keys=resolve_filter(conn, "api_key", self.people, cache),
            models=resolve_filter(conn, "model", self.models, cache),
            sources=resolve_filter(conn, "source", self.credentials, cache),
        )


_Pricing = Annotated[Mapping[str, ModelPricing], Depends(_get_pricing)]
_Range = Annotated[_RangeParams, Depends()]


def build_router(
    db_path: Path, aliases: Mapping[str, str], cache: CellCache | None = None
) -> APIRouter:
    """Build the usage API router.

    DB connections are opened read-only from *db_path* per-request; *aliases*
    maps redacted keys to display names; *cache*, when given, serves whole intervals.
    Access control is delegated upstream.
    """
    r = APIRouter(prefix="/usage", tags=["usage"])
    warned: set[str] = set()

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = open_ro(db_path)
        try:
            yield conn
        finally:
            conn.close()

    @r.get("/summary", response_model=SummaryResponse)
    def summary(
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: _Pricing,
        rng: _Range,
        bucket: _BucketParam = "auto",
    ) -> SummaryResponse:
        """KPI totals with the previous period and dense per-bucket series."""
        return query_summary(
            conn,
            rng.window,
            _bucket(bucket),
            rng.tz,
            rng.filters(conn, cache),
            pricing,
            cache=cache,
        )

    @r.get("/breakdown", response_model=BreakdownResponse)
    def breakdown(
        dim: Annotated[Dimension, Query()],
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: _Pricing,
        rng: _Range,
        bucket: _BucketParam = "auto",
    ) -> BreakdownResponse:
        """Per-entity rows for *dim* with trend strips and paired-dimension mix."""
        return query_breakdown(
            conn,
            dim,
            rng.window,
            _bucket(bucket),
            rng.tz,
            rng.filters(conn, cache),
            pricing,
            aliases,
            cache=cache,
        )

    @r.get("/export", response_class=Response)
    def export(
        conn: Annotated[sqlite3.Connection, Depends(get_conn)],
        pricing: _Pricing,
        rng: _Range,
        bucket: _BucketParam = "auto",
    ) -> Response:
        """CSV of the range: one row per bucket, person, model and credential."""
        body = query_export(
            conn,
            rng.window,
            _bucket(bucket),
            rng.tz,
            rng.filters(conn, cache),
            pricing,
            aliases,
            cache=cache,
        )
        return Response(
            body,
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="usage.csv"'},
        )

    @r.get("/options", response_model=OptionsResponse)
    def options(
        conn: Annotated[sqlite3.Connection, Depends(get_conn)], pricing: _Pricing
    ) -> OptionsResponse:
        """Every person, model and credential with its all-time color slot."""
        body = query_options(conn, pricing, aliases, cache=cache)
        unmatched = set(aliases) - {e.key for e in body.people} - warned
        for key in sorted(unmatched):
            _log.warning("USAGE_KEY_ALIASES entry %r matches no API key", key)
        warned.update(unmatched)
        return body

    return r
