"""Tests for the /api/pricing endpoint."""

from __future__ import annotations

import json
import pathlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from cliproxy_usage.db import insert_records, open_db
from cliproxy_usage.parser import iter_records
from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.main import create_app
from cliproxy_usage_server.pricing import ModelPricing
from cliproxy_usage_server.schemas import PricingResponse

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_FIXTURE = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "usage-export-2026-04-23T05-01-45-283Z.json"
)


@pytest.fixture()
def seeded_db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    """Seeded SQLite DB populated from the fixture JSON."""
    export = json.loads(_FIXTURE.read_text())
    records = list(iter_records(export))
    db_path = tmp_path / "usage.db"
    conn: sqlite3.Connection = open_db(db_path)
    insert_records(conn, records)
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Test: pricing response shape
# ---------------------------------------------------------------------------


def test_pricing_response_shape(seeded_db_path: pathlib.Path) -> None:
    """With stub pricing, tiered booleans are reflected correctly in response."""
    stub_pricing = {
        "flat-model": ModelPricing(
            input_cost_per_token=1e-6,
            output_cost_per_token=2e-6,
        ),
        "tiered-model": ModelPricing(
            input_cost_per_token=1e-6,
            output_cost_per_token=2e-6,
            input_cost_per_token_above_200k_tokens=0.5e-6,
        ),
    }

    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: stub_pricing)

    with TestClient(app) as client:
        resp = client.get("/api/pricing")

    assert resp.status_code == 200, resp.text
    body = PricingResponse.model_validate(resp.json())

    assert "flat-model" in body.pricing
    assert "tiered-model" in body.pricing

    flat = body.pricing["flat-model"]
    assert flat.tiered is False
    assert flat.input == 1e-6
    assert flat.output == 2e-6

    tiered = body.pricing["tiered-model"]
    assert tiered.tiered is True
    assert tiered.input == 1e-6
    assert tiered.output == 2e-6

