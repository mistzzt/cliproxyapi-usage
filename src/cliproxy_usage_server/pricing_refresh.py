"""Rate-limited, thread-safe background refresh of the pricing map.

Used by route handlers to opportunistically refresh liteLLM pricing when a
request resolves at least one `missing` model. The refresh runs on FastAPI's
threadpool via BackgroundTasks; this module just provides the rate-limit and
locking primitives so two concurrent requests don't fan out into two fetches.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cliproxy_usage_server.pricing import ModelPricing

__all__ = [
    "REFRESH_MIN_INTERVAL_SECONDS",
    "PricingRefreshState",
    "maybe_refresh_pricing",
]

REFRESH_MIN_INTERVAL_SECONDS = 60

_log = logging.getLogger(__name__)


@dataclass
class PricingRefreshState:
    """Per-app refresh bookkeeping. Lives on app.state.pricing_refresh."""

    last_refresh: datetime | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def maybe_refresh_pricing(
    *,
    state: PricingRefreshState,
    fetcher: Callable[[], dict[str, ModelPricing]],
    target: MutableMapping[str, ModelPricing],
    now: datetime | None = None,
) -> bool:
    """Run *fetcher* iff at least REFRESH_MIN_INTERVAL_SECONDS have passed.

    On success, replace *target*'s contents with the fetcher's return value.
    On failure, log and continue — the request is not blocked. The
    rate-limit timestamp advances regardless of success so failed fetches
    can't hot-loop.

    Returns True if a fetch was attempted, False if rate-limited.
    """
    if now is None:
        now = datetime.now(UTC)

    with state.lock:
        if state.last_refresh is not None:
            age = (now - state.last_refresh).total_seconds()
            if age < REFRESH_MIN_INTERVAL_SECONDS:
                return False
        state.last_refresh = now

    try:
        fetched = fetcher()
    except Exception as exc:  # noqa: BLE001 — defensive; never propagates
        _log.warning("Background pricing refresh failed: %s", exc)
        return True

    target.clear()
    target.update(fetched)
    return True
