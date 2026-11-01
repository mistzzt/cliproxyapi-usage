"""Tests for /api/usage/{summary,breakdown,matrix,options} on the synthetic DB."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from conftest import SYNTHETIC_PRICING, write_usage_db
from fastapi.testclient import TestClient

from cliproxy_usage_server.config import ServerConfig
from cliproxy_usage_server.main import create_app
from cliproxy_usage_server.pricing import ModelPricing

ALICE = "sk-*******-alice"
BOB = "sk-*******-bob"
SECRET = "sk-*******-secret"

# 2026-10-31 00:00 CDT to 2026-11-03 00:00 CST (73 hours, across fall-back).
WINDOW = "start=2026-10-31T00:00:00-05:00&end=2026-11-03T00:00:00-06:00"
Q = f"{WINDOW}&tz=America/Chicago"
COST_TOTAL = 0.00201 + 0.00405 + 0.0142 + 0.0015 + 0.00105


def _client(
    db_path: pathlib.Path,
    pricing: Mapping[str, ModelPricing] = SYNTHETIC_PRICING,
    aliases: pathlib.Path | None = None,
) -> TestClient:
    cfg = ServerConfig(db_path=db_path, key_aliases=aliases)  # pyright: ignore[reportCallIssue]
    return TestClient(create_app(cfg, pricing_provider=lambda: pricing))


@pytest.fixture
def client(synthetic_db_path: pathlib.Path) -> Iterator[TestClient]:
    with _client(synthetic_db_path) as c:
        yield c


def _get(client: TestClient, path: str) -> Any:
    resp = client.get(path)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_summary_totals_and_previous(client: TestClient) -> None:
    body = _get(client, f"/api/usage/summary?{Q}")
    cur = body["current"]
    assert (cur["requests"], cur["input_tokens"], cur["output_tokens"]) == (
        5,
        8300,
        1730,
    )
    assert cur["cost"] == pytest.approx(COST_TOTAL)
    assert body["previous"]["requests"] == 1
    all_time = _get(client, "/api/usage/summary?tz=America/Chicago")
    assert all_time["previous"] is None


def test_breakdowns_and_matrix_sum_to_totals(client: TestClient) -> None:
    for dim in ("person", "model", "credential"):
        rows = _get(client, f"/api/usage/breakdown?dim={dim}&{Q}")["rows"]
        assert sum(r["current"]["cost"] for r in rows) == pytest.approx(COST_TOTAL)
        for r in rows:
            assert sum(r["trend"]["cost"]) == pytest.approx(r["current"]["cost"])
            assert sum(m["cost"] for m in r["mix"]) == pytest.approx(
                r["current"]["cost"]
            )
    cells = _get(client, f"/api/usage/matrix?rows=person&cols=model&{Q}")["cells"]
    assert sum(c["cost"] for c in cells) == pytest.approx(COST_TOTAL)


def test_keys_that_redact_alike_are_one_person(client: TestClient) -> None:
    rows = _get(client, f"/api/usage/breakdown?dim=person&{Q}")["rows"]
    assert {r["key"]: r["current"]["requests"] for r in rows} == {ALICE: 3, BOB: 2}


def test_alias_labels_fall_back_to_redacted_key(
    synthetic_db_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({ALICE: "Alice"}))
    with _client(synthetic_db_path, aliases=aliases) as c:
        people = _get(c, "/api/usage/options")["people"]
    assert {e["key"]: e["label"] for e in people} == {ALICE: "Alice", BOB: BOB}


@pytest.mark.parametrize(
    ("bucket", "buckets", "requests"),
    [
        (
            "day",
            [
                "2026-10-31T00:00:00-05:00",
                "2026-11-01T00:00:00-05:00",
                "2026-11-02T00:00:00-06:00",
            ],
            [2, 2, 1],
        ),
        ("week", ["2026-10-26T00:00:00-05:00", "2026-11-02T00:00:00-06:00"], [4, 1]),
    ],
)
def test_buckets_across_dst(
    client: TestClient, bucket: str, buckets: list[str], requests: list[int]
) -> None:
    body = _get(client, f"/api/usage/summary?{Q}&bucket={bucket}")
    assert (body["buckets"], body["series"]["requests"]) == (buckets, requests)


@pytest.mark.parametrize(
    ("filters", "requests"),
    [
        (f"people={ALICE}", 3),
        ("models=gpt-t", 2),
        (f"credentials={SECRET}", 2),
        (f"people={BOB}&models=claude-t", 1),
    ],
)
def test_filters(client: TestClient, filters: str, requests: int) -> None:
    body = _get(client, f"/api/usage/summary?{Q}&{filters}")
    assert body["current"]["requests"] == requests


def test_options_slots_stop_after_seven(tmp_path: pathlib.Path) -> None:
    rows = [
        (
            f"2026-10-01T00:00:0{i}Z",
            "sk-a-bob",
            f"m{i}",
            "al@x.io",
            1000 - 100 * i,
            0,
            0,
        )
        for i in range(9)
    ]
    db = write_usage_db(tmp_path / "nine.db", rows)
    pricing = {
        f"m{i}": ModelPricing(input_cost_per_token=1e-6, output_cost_per_token=0)
        for i in range(9)
    }
    with _client(db, pricing=pricing) as c:
        models = _get(c, "/api/usage/options")["models"]
    assert [(e["key"], e["slot"]) for e in models] == [
        (f"m{i}", i if i < 7 else None) for i in range(9)
    ]


def test_price_only_on_model_rows(client: TestClient) -> None:
    models = _get(client, f"/api/usage/breakdown?dim=model&{Q}")["rows"]
    assert {r["key"]: r["price"]["output"] for r in models} == {
        "claude-t": 5.0,
        "gpt-t": 8.0,
    }
    # Missing cache-write rates show the 1.25x input fallback cost uses.
    price = {r["key"]: r["price"] for r in models}
    assert (price["claude-t"]["cache_write"], price["claude-t"]["long_context"]) == (
        1.25,
        None,
    )
    # Token accounting comes from the pricing provider, not the model name.
    assert (
        price["claude-t"]["cached_in_input"],
        price["gpt-t"]["cached_in_input"],
    ) == (
        False,
        True,
    )
    people = _get(client, f"/api/usage/breakdown?dim=person&{Q}")["rows"]
    assert all(r["price"] is None for r in people)


@pytest.mark.parametrize(
    "path",
    [
        f"/api/usage/summary?{WINDOW}",
        f"/api/usage/summary?{WINDOW}&tz=Mars/Olympus",
        "/api/usage/summary?start=2026-10-31T00:00:00&tz=UTC",
        "/api/usage/summary?start=2026-11-02T00:00:00Z&end=2026-11-01T00:00:00Z&tz=UTC",
        f"/api/usage/summary?{Q}&bucket=month",
        f"/api/usage/breakdown?{Q}",
        f"/api/usage/matrix?rows=model&cols=model&{Q}",
    ],
)
def test_invalid_requests_are_422(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 422


def test_empty_model_and_source_are_addressable_and_unpriced(
    tmp_path: pathlib.Path,
) -> None:
    db = write_usage_db(
        tmp_path / "empty.db",
        [("2026-10-01T00:00:00Z", "sk-a-bob", "", "", 100, 10, 0)],
    )
    with _client(db) as c:
        opts = _get(c, "/api/usage/options")
        assert [e["key"] for e in opts["models"]] == ["<empty>"]
        assert [e["key"] for e in opts["credentials"]] == ["<empty>"]
        body = _get(
            c,
            "/api/usage/summary?tz=UTC&models=%3Cempty%3E&credentials=%3Cempty%3E",
        )
        [row] = _get(c, "/api/usage/breakdown?dim=model&tz=UTC")["rows"]
    assert body["current"]["requests"] == 1
    # "<empty>" matches no pricing entry, so cost and price agree it is unpriced.
    assert (body["current"]["cost_status"], row["price"]) == ("missing", None)


def test_unmatched_aliases_warn_once_from_options(
    synthetic_db_path: pathlib.Path,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({ALICE: "Alice", "sk-*******-ghost": "Ghost"}))
    with _client(synthetic_db_path, aliases=aliases) as c:
        _get(c, "/api/usage/options")
        _get(c, "/api/usage/options")
    warnings = [
        r.getMessage() for r in caplog.records if "USAGE_KEY_ALIASES" in r.getMessage()
    ]
    assert warnings == ["USAGE_KEY_ALIASES entry 'sk-*******-ghost' matches no API key"]


def test_startup_does_not_read_db(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "blank.db"
    db.touch()
    aliases = tmp_path / "aliases.json"
    aliases.write_text(json.dumps({ALICE: "Alice"}))
    with _client(db, aliases=aliases) as c:
        assert c.get("/api/health-check").status_code == 200


def test_all_time_axis_starts_at_earliest_instant(tmp_path: pathlib.Path) -> None:
    # The -06:00 row sorts first as text but is 40 minutes later.
    db = write_usage_db(
        tmp_path / "offsets.db",
        [
            ("2026-11-01T01:30:00-05:00", "sk-a-bob", "gpt-t", "al@x.io", 100, 0, 0),
            ("2026-11-01T01:10:00-06:00", "sk-a-bob", "gpt-t", "al@x.io", 100, 0, 0),
        ],
    )
    with _client(db) as c:
        body = _get(c, "/api/usage/summary?tz=UTC&bucket=hour")
    assert body["buckets"][0] == "2026-11-01T06:00:00Z"
    assert sum(body["series"]["requests"]) == 2


def test_tiers_apply_per_request_not_per_cell(tmp_path: pathlib.Path) -> None:
    from test_pricing_ccusage import CLAUDE, GPT_LONG

    # Every row lands in one 15-minute slice, so cell sums cross every threshold.
    rows: list[tuple[str, str, str, str, int, int, int]] = [
        (
            f"2026-10-01T00:00:{i:02d}Z",
            "sk-a-bob",
            "claude-x",
            "al@x.io",
            50_000,
            1_000,
            100_000,
        )
        for i in range(20)
    ]
    rows += [
        (
            "2026-10-01T00:01:00Z",
            "sk-a-bob",
            "gpt-x",
            "al@x.io",
            300_000,
            2_000,
            100_000,
        ),
        (
            "2026-10-01T00:02:00Z",
            "sk-a-bob",
            "gpt-x",
            "al@x.io",
            200_000,
            2_000,
            50_000,
        ),
        ("2026-10-01T00:03:00Z", "sk-a-bob", "gpt-x", "al@x.io", 250_000, 2_000, 0),
        ("2026-10-01T00:04:00Z", "sk-a-bob", "claude-x", "al@x.io", 250_000, 0, 0),
        ("2026-10-01T00:05:00Z", "sk-a-bob", "free-x", "al@x.io", 900_000, 0, 0),
    ]
    pricing = {"claude-x": CLAUDE, "gpt-x": GPT_LONG}
    # Hand-checked per-request sum: claude-x 20 x $0.195 + $0.90, gpt-x
    # $2.19 (long) + $0.835 + $1.31. Pricing the summed cell would give $14.76.
    expected = 9.135
    db = write_usage_db(tmp_path / "tiers.db", rows)
    with _client(db, pricing=pricing) as c:
        body = _get(c, "/api/usage/summary?tz=UTC")
        models = _get(c, "/api/usage/breakdown?dim=model&tz=UTC")["rows"]
    assert body["current"]["cost"] == pytest.approx(expected)
    assert sum(r["current"]["cost"] or 0 for r in models) == pytest.approx(expected)
    # The price column shows the same plan that priced the cost.
    long: dict[str, Any] = {
        r["key"]: r["price"] and r["price"]["long_context"] for r in models
    }
    assert long["free-x"] is None
    assert (long["claude-x"]["threshold"], long["claude-x"]["whole_request"]) == (
        200_000,
        False,
    )
    assert long["gpt-x"] == {
        "input": 10.0,
        "output": 45.0,
        "cache_read": 1.0,
        "cache_write": 6.25,
        "threshold": 272_000,
        "whole_request": True,
    }
