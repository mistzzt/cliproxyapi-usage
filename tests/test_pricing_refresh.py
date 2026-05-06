"""Tests for rate-limited background pricing refresh."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from cliproxy_usage_server.pricing import ModelPricing
from cliproxy_usage_server.pricing_refresh import (
    REFRESH_MIN_INTERVAL_SECONDS,
    PricingRefreshState,
    maybe_refresh_pricing,
)


def _make_state() -> PricingRefreshState:
    return PricingRefreshState()


def test_refresh_fires_when_state_is_fresh() -> None:
    state = _make_state()
    fetched = {"gpt-5": ModelPricing(input_cost_per_token=1e-6)}
    fetcher = MagicMock(return_value=fetched)
    target_pricing: dict[str, ModelPricing] = {}

    fired = maybe_refresh_pricing(
        state=state,
        fetcher=fetcher,
        target=target_pricing,
        now=datetime.now(UTC),
    )

    assert fired is True
    fetcher.assert_called_once()
    assert target_pricing == fetched
    assert state.last_refresh is not None


def test_refresh_skipped_within_rate_limit() -> None:
    state = _make_state()
    state.last_refresh = datetime.now(UTC)
    fetcher = MagicMock(return_value={})
    target: dict[str, ModelPricing] = {}

    fired = maybe_refresh_pricing(
        state=state,
        fetcher=fetcher,
        target=target,
        now=datetime.now(UTC),
    )

    assert fired is False
    fetcher.assert_not_called()


def test_refresh_fires_after_rate_limit_expires() -> None:
    state = _make_state()
    state.last_refresh = datetime.now(UTC) - timedelta(
        seconds=REFRESH_MIN_INTERVAL_SECONDS + 1
    )
    fetcher = MagicMock(return_value={"gpt-5": ModelPricing()})
    target: dict[str, ModelPricing] = {}

    fired = maybe_refresh_pricing(
        state=state,
        fetcher=fetcher,
        target=target,
        now=datetime.now(UTC),
    )

    assert fired is True
    fetcher.assert_called_once()


def test_fetcher_exception_does_not_propagate() -> None:
    state = _make_state()
    fetcher = MagicMock(side_effect=RuntimeError("boom"))
    target: dict[str, ModelPricing] = {}

    fired = maybe_refresh_pricing(
        state=state,
        fetcher=fetcher,
        target=target,
        now=datetime.now(UTC),
    )

    assert fired is True
    assert state.last_refresh is not None
    assert target == {}


def test_concurrent_refresh_only_runs_once() -> None:
    state = _make_state()
    call_count = 0

    def slow_fetcher() -> dict[str, ModelPricing]:
        nonlocal call_count
        call_count += 1
        time.sleep(0.05)
        return {}

    target: dict[str, ModelPricing] = {}
    threads = [
        threading.Thread(
            target=maybe_refresh_pricing,
            kwargs={
                "state": state,
                "fetcher": slow_fetcher,
                "target": target,
                "now": datetime.now(UTC),
            },
        )
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count == 1, f"expected exactly one fetch, got {call_count}"


def test_route_dispatches_refresh_on_missing(tmp_path: Path) -> None:
    """A request that resolves a missing model schedules a background refresh."""
    from dataclasses import dataclass

    from fastapi.testclient import TestClient

    from cliproxy_usage_collect.db import open_db
    from cliproxy_usage_server.config import ServerConfig
    from cliproxy_usage_server.main import create_app

    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-05-01T00:00:00.000000Z",
            "sk-test",
            "unknown-model",
            "openai:sk-x",
            "0",
            100,
            100,
            50,
            0,
            0,
            150,
            0,
        ),
    )
    conn.commit()
    conn.close()

    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    fetched: list[bool] = []

    def fetcher() -> dict[str, ModelPricing]:
        fetched.append(True)
        return {"unknown-model": ModelPricing(input_cost_per_token=1e-6)}

    @dataclass
    class StubConfig:
        fetcher: object

    app = create_app(cfg, pricing_provider=lambda: {})
    app.state.pricing_config = StubConfig(fetcher=fetcher)

    with TestClient(app) as client:
        resp = client.get("/api/api-stats?range=all")
        assert resp.status_code == 200

    assert fetched == [True], "expected one fetch"
