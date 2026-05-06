# Cost accuracy, pricing-miss handling, credential redaction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop overcounting Codex costs, surface "no pricing available" to the frontend, and redact API keys in `/api/credential-stats` source values.

**Architecture:** All fixes live at query/response time. No DB migration. `pricing.py` gets a source-aware token splitter and a status-returning `resolve()`. `routes/usage.py` threads `source` through cost helpers and uses `BackgroundTasks` to trigger a rate-limited liteLLM refresh on misses. `redact.py` gains `redact_source` wired via `BeforeValidator`. Frontend renders `partial_missing` / `missing` rows in the warning color.

**Tech Stack:** Python 3.14, FastAPI, pydantic v2, pytest, sqlite3. Frontend: TypeScript, React 18, Chart.js, SCSS modules.

**Spec:** `docs/superpowers/specs/2026-05-06-cost-pricing-redaction-design.md`

---

## File map

| File | Action | Responsibility |
| --- | --- | --- |
| `src/cliproxy_usage_server/pricing.py` | modify | Add `split_tokens_for_cost`, change `resolve()` to return `(entry, status)`. |
| `src/cliproxy_usage_server/pricing_refresh.py` | create | Rate-limited background refresh helper. |
| `src/cliproxy_usage_server/redact.py` | modify | Add `redact_source`. |
| `src/cliproxy_usage_server/schemas.py` | modify | Add `CostStatus` literal, `cost_status` field on cost-bearing DTOs, `series_status` on `TimeseriesResponse`, `RedactedSource` annotation. |
| `src/cliproxy_usage_server/routes/usage.py` | modify | Thread `source` through grouped cost helpers; emit `cost_status`/`series_status`; trigger refresh on miss. |
| `src/cliproxy_usage_server/main.py` | modify | Initialize `app.state.last_pricing_refresh` and `app.state.pricing_refresh_lock`. |
| `tests/test_pricing.py` | modify | Update existing `resolve()` callers to new tuple return. |
| `tests/test_pricing_split.py` | create | Codex/Claude split unit tests. |
| `tests/test_pricing_resolve.py` | create | New `resolve()` status return + cost_status rollup helper. |
| `tests/test_pricing_refresh.py` | create | Background refresh + 60s rate limit. |
| `tests/test_redact.py` | modify | Add `redact_source` cases. |
| `tests/test_routes_usage.py` | modify | Codex cost parity, `cost_status` plumbing, redacted source, `series_status`. |
| `frontend/src/types/api.ts` | modify | Add `CostStatus` type + new fields. |
| `frontend/src/components/usage/StatCards.tsx` | modify | Render totals `cost_status`. |
| `frontend/src/components/usage/ApiDetailsCard.tsx` | modify | Render per-row `cost_status`. |
| `frontend/src/components/usage/ModelStatsCard.tsx` | modify | Render per-row `cost_status`. |
| `frontend/src/components/usage/CredentialStatsCard.tsx` | modify | Render per-row `cost_status`. |
| `frontend/src/components/usage/CostTrendChart.tsx` | modify | Apply `series_status` styling. |
| `README.md` | modify | Add "Cost accuracy" subsection under `## Webapp`. |

---

## Task 1: Add `split_tokens_for_cost` (Codex/OpenAI source split)

**Files:**
- Modify: `src/cliproxy_usage_server/pricing.py`
- Create: `tests/test_pricing_split.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing_split.py`:

```python
"""Unit tests for split_tokens_for_cost (ccusage-style cached/input split)."""

from __future__ import annotations

import pytest

from cliproxy_usage_server.pricing import split_tokens_for_cost


@pytest.mark.parametrize(
    "source",
    ["codex:user@gmail.com", "openai:sk-abc", "openai-compat:foo", "Codex:Bar", "OPENAI:baz"],
)
def test_openai_sources_subtract_cached_from_input(source: str) -> None:
    """For OpenAI-convention sources cached_tokens is a subset of input_tokens."""
    out = split_tokens_for_cost(source, input_tokens=1000, output_tokens=500, cached_tokens=200)
    assert out == {
        "input_tokens": 800,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }


def test_openai_cached_zero_passthrough() -> None:
    out = split_tokens_for_cost("codex:foo", 1000, 500, 0)
    assert out == {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 0}


def test_openai_cached_equal_input() -> None:
    out = split_tokens_for_cost("codex:foo", 1000, 500, 1000)
    assert out == {"input_tokens": 0, "output_tokens": 500, "cache_read_input_tokens": 1000}


def test_openai_cached_exceeds_input_clamps() -> None:
    """Defensive: if upstream sends cached > input, clamp cache_read at input."""
    out = split_tokens_for_cost("codex:foo", 1000, 500, 2000)
    assert out == {"input_tokens": 0, "output_tokens": 500, "cache_read_input_tokens": 1000}


@pytest.mark.parametrize("source", ["claude:user@x.io", "anthropic:sk-ant", "gemini:foo", "openrouter:bar"])
def test_non_openai_sources_passthrough(source: str) -> None:
    """Non-OpenAI sources keep cached_tokens in cache_read and input untouched."""
    out = split_tokens_for_cost(source, input_tokens=1000, output_tokens=500, cached_tokens=200)
    assert out == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }


def test_empty_source_passthrough() -> None:
    out = split_tokens_for_cost("", 1000, 500, 200)
    assert out == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    }
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `uv run pytest tests/test_pricing_split.py -v`
Expected: FAIL with `ImportError: cannot import name 'split_tokens_for_cost'`.

- [ ] **Step 3: Implement `split_tokens_for_cost`**

In `src/cliproxy_usage_server/pricing.py`, add to `__all__` and add the function near the other helpers (before `_tiered_cost`):

```python
__all__ = [
    "PREFIX_CANDIDATES",
    "ModelPricing",
    "ProviderEntry",
    "TokenCounts",
    "compute_cost",
    "fetch_pricing",
    "resolve",
    "split_tokens_for_cost",
]

_OPENAI_SOURCE_PREFIXES: tuple[str, ...] = (
    "codex:",
    "openai:",
    "openai-compat:",
)


def split_tokens_for_cost(
    source: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
) -> TokenCounts:
    """Return TokenCounts ready for compute_cost.

    For OpenAI-convention sources (Codex/OpenAI/OpenAI-compat, case-insensitive
    prefix match on `source`) cached_tokens is treated as a subset of
    input_tokens — mirrors ccusage's apps/codex/src/command-utils.ts split.

    For all other sources the values pass through: cached_tokens flow into
    cache_read_input_tokens and input_tokens stays as the already-uncached
    count (Anthropic / Gemini convention).
    """
    if source.lower().startswith(_OPENAI_SOURCE_PREFIXES):
        cache_read = min(cached_tokens, input_tokens)
        return {
            "input_tokens": max(input_tokens - cache_read, 0),
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
        }
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cached_tokens,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_pricing_split.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cliproxy_usage_server/pricing.py tests/test_pricing_split.py
git commit -m "feat(pricing): split_tokens_for_cost for ccusage-style Codex billing"
```

---

## Task 2: Change `resolve()` to return `(entry, status)`

**Files:**
- Modify: `src/cliproxy_usage_server/pricing.py`
- Create: `tests/test_pricing_resolve.py`
- Modify: `tests/test_pricing.py` (existing callers expect the old return)
- Modify: `src/cliproxy_usage_server/routes/usage.py` (existing `resolve()` callers)

This task changes the public signature of `resolve()`. To keep the diff small, every caller in the same commit gets updated to unpack the tuple and ignore the status (status is consumed for real in Task 4).

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing_resolve.py`:

```python
"""Tests for resolve() returning (entry, status) and cost_status rollups."""

from __future__ import annotations

from cliproxy_usage_server.pricing import (
    ModelPricing,
    PricingResolution,
    resolve,
    rollup_cost_status,
)


_E = ModelPricing(input_cost_per_token=1e-6, output_cost_per_token=1e-6)


def test_resolve_exact_match_returns_live() -> None:
    entry, status = resolve("gpt-5", {"gpt-5": _E})
    assert entry is _E
    assert status == "live"


def test_resolve_prefix_match_returns_live() -> None:
    entry, status = resolve("opus-4-5", {"anthropic/opus-4-5": _E})
    assert entry is _E
    assert status == "live"


def test_resolve_substring_match_returns_live() -> None:
    entry, status = resolve("claude-sonnet-4", {"anthropic/claude-sonnet-4-5": _E})
    assert entry is _E
    assert status == "live"


def test_resolve_missing_returns_none_missing() -> None:
    entry, status = resolve("totally-new-model", {"gpt-5": _E})
    assert entry is None
    assert status == "missing"


def test_resolve_empty_pricing_returns_missing() -> None:
    entry, status = resolve("anything", {})
    assert entry is None
    assert status == "missing"


def test_rollup_all_live() -> None:
    statuses: list[PricingResolution] = ["live", "live", "live"]
    assert rollup_cost_status(statuses) == "live"


def test_rollup_all_missing() -> None:
    statuses: list[PricingResolution] = ["missing", "missing"]
    assert rollup_cost_status(statuses) == "missing"


def test_rollup_mixed_is_partial_missing() -> None:
    statuses: list[PricingResolution] = ["live", "missing", "live"]
    assert rollup_cost_status(statuses) == "partial_missing"


def test_rollup_empty_is_missing() -> None:
    """A row with zero component models is treated as missing."""
    assert rollup_cost_status([]) == "missing"
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `uv run pytest tests/test_pricing_resolve.py -v`
Expected: FAIL with `ImportError` for `PricingResolution` / `rollup_cost_status`.

- [ ] **Step 3: Update `pricing.py` — change `resolve` signature, add helpers**

In `src/cliproxy_usage_server/pricing.py`:

```python
from typing import Literal, TypedDict

PricingResolution = Literal["live", "missing"]
CostStatus = Literal["live", "partial_missing", "missing"]


def resolve(
    model_name: str, pricing: Mapping[str, ModelPricing]
) -> tuple[ModelPricing | None, PricingResolution]:
    """Return (ModelPricing, "live") for a hit, (None, "missing") for a miss.

    Match order:
    1. Exact key lookup.
    2. Each prefix in PREFIX_CANDIDATES prepended to model_name.
    3. First case-insensitive substring match (key contains name, or name
       contains key).
    """
    if model_name in pricing:
        return pricing[model_name], "live"

    for prefix in PREFIX_CANDIDATES:
        candidate = f"{prefix}{model_name}"
        if candidate in pricing:
            return pricing[candidate], "live"

    lower = model_name.lower()
    for key, value in pricing.items():
        key_lower = key.lower()
        if key_lower in lower or lower in key_lower:
            return value, "live"

    return None, "missing"


def rollup_cost_status(statuses: list[PricingResolution]) -> CostStatus:
    """Roll up a list of per-component statuses into one row-level CostStatus.

    - empty list           -> "missing" (no components contribute)
    - all "live"           -> "live"
    - all "missing"        -> "missing"
    - mix of live + missing -> "partial_missing"
    """
    if not statuses:
        return "missing"
    has_live = any(s == "live" for s in statuses)
    has_miss = any(s == "missing" for s in statuses)
    if has_live and has_miss:
        return "partial_missing"
    return "live" if has_live else "missing"
```

Add `"CostStatus"`, `"PricingResolution"`, `"rollup_cost_status"` to `__all__`.

- [ ] **Step 4: Update existing `resolve()` callers in `routes/usage.py`**

In `src/cliproxy_usage_server/routes/usage.py`, find every `resolve(...)` call and unpack the tuple. There are five call sites; here is the full list with the new shape (replace each `entry = resolve(...)` with the corresponding two-line unpack — the status is intentionally `_` here, Task 4 starts using it):

```python
# inside _compute_totals_cost (around line 149)
entry, _status = resolve(row.model, pricing)
if entry is None:
    continue

# inside _query_bucket_model_costs (around line 226)
entry, _status = resolve(model, pricing)

# inside _cost_by_api_key (around line 336)
entry, _status = resolve(model, pricing)

# inside _cost_by_credential (around line 381)
entry, _status = resolve(model, pricing)

# inside model_stats handler (around line 759)
entry, _status = resolve(row.model, pricing) if pricing else (None, "missing")
```

- [ ] **Step 5: Update existing `tests/test_pricing.py` callers**

`tests/test_pricing.py` calls `resolve()` directly. Update each call site to unpack the tuple. Search-and-replace pattern: any line of the form `result = resolve(name, pricing)` becomes `result, _ = resolve(name, pricing)`. After editing run `grep -n "resolve(" tests/test_pricing.py` to confirm there are no bare assignments left.

- [ ] **Step 6: Run all pricing tests**

Run: `uv run pytest tests/test_pricing.py tests/test_pricing_resolve.py tests/test_pricing_split.py -v`
Expected: all PASS.

- [ ] **Step 7: Run the full test suite to catch other callers**

Run: `uv run pytest -x`
Expected: PASS. If any test fails with a tuple-unpacking error, update that caller to unpack `(entry, status)` and re-run.

- [ ] **Step 8: Commit**

```bash
git add src/cliproxy_usage_server/pricing.py src/cliproxy_usage_server/routes/usage.py tests/test_pricing.py tests/test_pricing_resolve.py
git commit -m "refactor(pricing): resolve returns (entry, status); add rollup helper"
```

---

## Task 3: Wire Codex split into cost helpers (kills the overcount)

**Files:**
- Modify: `src/cliproxy_usage_server/routes/usage.py`
- Modify: `tests/test_routes_usage.py`

The four cost helpers (`_compute_totals_cost`, `_query_bucket_model_costs`, `_cost_by_api_key`, `_cost_by_credential`) and the inline cost loop in `model_stats` all need to:
1. Group SQL rows by `(model, source)` instead of just model.
2. Run each cell through `split_tokens_for_cost(source, ...)` before `compute_cost`.
3. Sum costs back up to the dimension the endpoint returns.

`/api/model-stats` rolls the source dimension up — the response is still per-model.

- [ ] **Step 1: Write the failing parity test**

Append to `tests/test_routes_usage.py`:

```python
def test_codex_cost_split_matches_ccusage_formula(tmp_path: pathlib.Path) -> None:
    """A single Codex row's cost must match ccusage's split formula.

    ccusage: cache_read = min(cached, input); input -= cache_read
    Then: cost = input*input_rate + output*output_rate + cache_read*cache_read_rate
    """
    # Seed a hand-made row instead of the JSON fixture so the math is obvious.
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-05-01T00:00:00.000000Z",
            "sk-test",
            "gpt-5",
            "codex:tester@example.com",
            "0",
            100,
            1000,  # input_tokens (includes cached)
            500,   # output_tokens
            0,
            200,   # cached_tokens (subset of input)
            1500,
            0,
        ),
    )
    conn.commit()
    conn.close()

    pricing_map = {
        "gpt-5": ModelPricing(
            input_cost_per_token=1.25e-6,
            output_cost_per_token=1e-5,
            cache_read_input_token_cost=1.25e-7,
        )
    }
    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing_map)

    expected_input = 1000 - 200  # 800
    expected = (
        expected_input * 1.25e-6
        + 500 * 1e-5
        + 200 * 1.25e-7
    )

    with TestClient(app) as client:
        resp = client.get("/api/api-stats?range=all")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["cost"] == pytest.approx(expected)


def test_anthropic_cost_unaffected_by_split(tmp_path: pathlib.Path) -> None:
    """Non-OpenAI sources still bill cached_tokens at cache-read rate without
    subtracting from input_tokens (Anthropic's input is already uncached)."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-05-01T00:00:00.000000Z",
            "sk-test",
            "claude-sonnet-4-5",
            "claude:tester@example.com",
            "0",
            100,
            1000, 500, 0, 200, 1500, 0,
        ),
    )
    conn.commit()
    conn.close()

    pricing_map = {
        "claude-sonnet-4-5": ModelPricing(
            input_cost_per_token=3e-6,
            output_cost_per_token=1.5e-5,
            cache_read_input_token_cost=3e-7,
        )
    }
    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: pricing_map)

    expected = 1000 * 3e-6 + 500 * 1.5e-5 + 200 * 3e-7

    with TestClient(app) as client:
        resp = client.get("/api/api-stats?range=all")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert rows[0]["cost"] == pytest.approx(expected)
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `uv run pytest tests/test_routes_usage.py::test_codex_cost_split_matches_ccusage_formula -v`
Expected: FAIL — current code overcounts (it bills the 200 cached tokens at full input rate AND at cache-read rate).

- [ ] **Step 3: Update `_grouped_cost_rows` to include `source`**

In `src/cliproxy_usage_server/routes/usage.py`, modify `_grouped_cost_rows` so its SELECT and GROUP BY include `source`. Replace the function body with:

```python
def _grouped_cost_rows(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    group_cols: str,
    order_cols: str,
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> list[tuple]:
    """Run a grouped (group_cols, model, source) token-sum query.

    Returns rows of (group_col_values..., model, source, input_tokens,
    output_tokens, cached_tokens). Source is needed so the caller can apply
    the OpenAI-convention split via split_tokens_for_cost.
    """
    if start is not None:
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        norm = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%S.000000Z', MIN(timestamp)) FROM requests"
        ).fetchone()
        s = norm[0] if (norm and norm[0]) else "1970-01-01T00:00:00.000000Z"

    e = end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    where = (
        "WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)"
    )
    params: list[str] = [s, e]

    mfrag = ""
    if models:
        placeholders = ", ".join("?" * len(models))
        mfrag = f" AND model IN ({placeholders})"
        params.extend(models)

    kfrag = ""
    if api_keys is not None:
        if not api_keys:
            kfrag = " AND 0=1"
        else:
            placeholders_k = ", ".join("?" * len(api_keys))
            kfrag = f" AND api_key IN ({placeholders_k})"
            params.extend(api_keys)

    return conn.execute(
        f"""
        SELECT
            {group_cols},
            model,
            source,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cached_tokens), 0) AS cached_tokens
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY {group_cols}, model, source
        ORDER BY {order_cols}, model ASC, source ASC
        """,
        params,
    ).fetchall()
```

- [ ] **Step 4: Update `_cost_by_api_key` to use source**

Replace the row-iteration loop:

```python
def _cost_by_api_key(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> dict[str, tuple[float | None, CostStatus]]:
    """Compute (cost, cost_status) per api_key.

    Iterates per (api_key, model, source) cell, applies split_tokens_for_cost,
    runs compute_cost, and rolls per-model statuses up via rollup_cost_status.
    """
    if not pricing:
        return {}

    rows = _grouped_cost_rows(
        conn, start, end, "api_key", "api_key ASC", models=models, api_keys=api_keys
    )
    # Per api_key: list of statuses + running cost sum
    per_key_cost: dict[str, float] = {}
    per_key_statuses: dict[str, list[PricingResolution]] = {}
    for api_key, model, source, inp, out, cached in rows:
        entry, status = resolve(model, pricing)
        per_key_statuses.setdefault(api_key, []).append(status)
        if entry is not None:
            tc = split_tokens_for_cost(source, inp, out, cached)
            per_key_cost[api_key] = per_key_cost.get(api_key, 0.0) + compute_cost(tc, entry)
        else:
            per_key_cost.setdefault(api_key, 0.0)

    result: dict[str, tuple[float | None, CostStatus]] = {}
    for api_key, statuses in per_key_statuses.items():
        roll = rollup_cost_status(statuses)
        cost: float | None = None if roll == "missing" else per_key_cost.get(api_key, 0.0)
        result[api_key] = (cost, roll)
    return result
```

Add the necessary imports at the top of `routes/usage.py`:

```python
from cliproxy_usage_server.pricing import (
    CostStatus,
    ModelPricing,
    PricingResolution,
    TokenCounts,
    compute_cost,
    resolve,
    rollup_cost_status,
    split_tokens_for_cost,
)
```

(Replace the existing `from cliproxy_usage_server.pricing import (...)` block with this expanded one. Remove `TokenCounts` references that were only used inline if they become unused — `TokenCounts` is still useful as a return-type hint elsewhere, leave it imported.)

- [ ] **Step 5: Update `_cost_by_credential` symmetrically**

```python
def _cost_by_credential(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> dict[str, tuple[float | None, CostStatus]]:
    """Compute (cost, cost_status) per ``source``."""
    if not pricing:
        return {}

    rows = _grouped_cost_rows(
        conn, start, end, "source", "source ASC", models=models, api_keys=api_keys
    )
    per_src_cost: dict[str, float] = {}
    per_src_statuses: dict[str, list[PricingResolution]] = {}
    # NB: rows here have shape (group_source, model, row_source, inp, out, cached).
    # The first column equals the third for this grouping; we use the first
    # as the dict key and pass the third (which is identical) into the splitter.
    for grouping_source, model, row_source, inp, out, cached in rows:
        entry, status = resolve(model, pricing)
        per_src_statuses.setdefault(grouping_source, []).append(status)
        if entry is not None:
            tc = split_tokens_for_cost(row_source, inp, out, cached)
            per_src_cost[grouping_source] = per_src_cost.get(grouping_source, 0.0) + compute_cost(tc, entry)
        else:
            per_src_cost.setdefault(grouping_source, 0.0)

    result: dict[str, tuple[float | None, CostStatus]] = {}
    for src, statuses in per_src_statuses.items():
        roll = rollup_cost_status(statuses)
        cost: float | None = None if roll == "missing" else per_src_cost.get(src, 0.0)
        result[src] = (cost, roll)
    return result
```

- [ ] **Step 6: Update `_compute_totals_cost`**

```python
def _compute_totals_cost(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> tuple[float | None, CostStatus]:
    """Sum costs across all (model, source) cells in the window.

    Returns (cost, cost_status). cost is None only when status == "missing".
    Empty pricing map -> (None, "missing").
    """
    if not pricing:
        return None, "missing"

    rows = _grouped_cost_rows(
        conn,
        start,
        end,
        "1",  # constant group key — we want a single-row roll-up
        "1",
        models=models,
        api_keys=api_keys,
    )
    statuses: list[PricingResolution] = []
    total = 0.0
    for _const, model, source, inp, out, cached in rows:
        entry, status = resolve(model, pricing)
        statuses.append(status)
        if entry is not None:
            tc = split_tokens_for_cost(source, inp, out, cached)
            total += compute_cost(tc, entry)

    roll = rollup_cost_status(statuses)
    cost: float | None = None if roll == "missing" else total
    return cost, roll
```

- [ ] **Step 7: Update `_query_bucket_model_costs`**

Replace its body so it groups by `(bkt, model, source)` and applies the split per cell, then aggregates back to `(bkt, model)`:

```python
def _query_bucket_model_costs(
    conn: sqlite3.Connection,
    start: datetime | None,
    end: datetime,
    bucket_fmt: str,
    pricing: Mapping[str, ModelPricing],
    models: list[str] | None = None,
    api_keys: list[str] | None = None,
) -> tuple[dict[tuple[str, str], float], dict[str, list[PricingResolution]]]:
    """Compute per-(bucket, model) cost and per-model status list.

    Groups SQL by (bucket, model, source) and applies split_tokens_for_cost
    per cell, then rolls source up so the caller sees (bucket, model) -> cost.
    The second return value maps model -> list of per-cell statuses; the
    caller passes those through rollup_cost_status to derive series_status.
    """
    if start is not None:
        s = start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    else:
        norm = conn.execute(
            "SELECT strftime('%Y-%m-%dT%H:%M:%S.000000Z', MIN(timestamp)) FROM requests"
        ).fetchone()
        s = norm[0] if (norm and norm[0]) else "1970-01-01T00:00:00.000000Z"

    e = end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    where = (
        "WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) < datetime(?)"
    )
    params: list[str] = [s, e]

    mfrag = ""
    if models:
        placeholders = ", ".join("?" * len(models))
        mfrag = f" AND model IN ({placeholders})"
        params.extend(models)

    kfrag = ""
    if api_keys is not None:
        if not api_keys:
            kfrag = " AND 0=1"
        else:
            placeholders_k = ", ".join("?" * len(api_keys))
            kfrag = f" AND api_key IN ({placeholders_k})"
            params.extend(api_keys)

    rows = conn.execute(
        f"""
        SELECT
            strftime('{bucket_fmt}', timestamp) AS bkt,
            model,
            source,
            COALESCE(SUM(input_tokens), 0)  AS inp,
            COALESCE(SUM(output_tokens), 0) AS out,
            COALESCE(SUM(cached_tokens), 0) AS cac
        FROM requests
        {where}{mfrag}{kfrag}
        GROUP BY bkt, model, source
        ORDER BY bkt, model, source
        """,
        params,
    ).fetchall()

    cell_cost: dict[tuple[str, str], float] = {}
    model_statuses: dict[str, list[PricingResolution]] = {}
    for bkt, model, source, inp, out, cac in rows:
        entry, status = resolve(model, pricing)
        model_statuses.setdefault(model, []).append(status)
        if entry is None:
            cell_cost[(bkt, model)] = cell_cost.get((bkt, model), 0.0)
            continue
        tc = split_tokens_for_cost(source, inp, out, cac)
        cell_cost[(bkt, model)] = cell_cost.get((bkt, model), 0.0) + compute_cost(tc, entry)
    return cell_cost, model_statuses
```

- [ ] **Step 8: Update the `model_stats` handler inline cost loop**

In `routes/usage.py`'s `model_stats` route handler (around line 750), replace the cost computation. The handler currently calls `query_model_stats` for token totals and resolves pricing per model. We now also need to fetch per-source breakdowns to apply the split. Replace the body:

```python
@r.get("/model-stats", response_model=list[ModelStat])
def model_stats(
    range_: _RangeParam,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    pricing: Annotated[Mapping[str, ModelPricing], Depends(get_pricing)],
    models: Annotated[str | None, Query()] = None,
    api_keys: Annotated[str | None, Query()] = None,
) -> list[ModelStat]:
    """Return per-model aggregate stats with cost + cost_status."""
    now = datetime.now(UTC)
    start, end = range_window(range_, now)
    models_list = _parse_models(models)
    raw_keys = _resolve_api_keys(conn, _parse_api_keys(api_keys))
    rows = query_model_stats(
        conn, start, end, models=models_list, api_keys=raw_keys
    )

    # Per-(model, source) split for cost; aggregate token displays stay raw.
    grouped = _grouped_cost_rows(
        conn, start, end, "1", "1", models=models_list, api_keys=raw_keys
    )
    per_model_cost: dict[str, float] = {}
    per_model_status: dict[str, list[PricingResolution]] = {}
    if pricing:
        for _const, model, source, inp, out, cached in grouped:
            entry, status = resolve(model, pricing)
            per_model_status.setdefault(model, []).append(status)
            if entry is not None:
                tc = split_tokens_for_cost(source, inp, out, cached)
                per_model_cost[model] = per_model_cost.get(model, 0.0) + compute_cost(tc, entry)

    result: list[ModelStat] = []
    for row in rows:
        if not pricing:
            cost: float | None = None
            cost_status: CostStatus = "missing"
        else:
            statuses = per_model_status.get(row.model, [])
            roll = rollup_cost_status(statuses)
            cost_status = roll
            cost = None if roll == "missing" else per_model_cost.get(row.model, 0.0)
        result.append(
            ModelStat(
                model=row.model,
                requests=row.requests,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cached_tokens=row.cached_tokens,
                reasoning_tokens=row.reasoning_tokens,
                total_tokens=row.total_tokens,
                failed=row.failed,
                avg_latency_ms=row.avg_latency_ms,
                cost=cost,
                cost_status=cost_status,
            )
        )
    return result
```

(`ModelStat` schema gets `cost_status` in Task 4; this is a forward reference. The plan order works because Task 4 lands before Task 5 runs the full test suite end-to-end.)

- [ ] **Step 9: Update `api_stats`, `credential_stats`, `overview` handlers to consume the new return shape**

Replace the relevant blocks:

```python
# api_stats handler
cost_map = _cost_by_api_key(
    conn, start, end, pricing, models=models_list, api_keys=raw_keys
)
return [
    ApiStat(
        api_key=row.api_key,
        requests=row.requests,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        failed=row.failed,
        avg_latency_ms=row.avg_latency_ms,
        cost=cost_map.get(row.api_key, (None, "missing"))[0],
        cost_status=cost_map.get(row.api_key, (None, "missing"))[1],
    )
    for row in rows
]
```

```python
# credential_stats handler
cost_map = _cost_by_credential(
    conn, start, end, pricing, models=models_list, api_keys=raw_keys
)
return [
    CredentialStat(
        source=row.source,
        requests=row.requests,
        total_tokens=row.total_tokens,
        failed=row.failed,
        cost=cost_map.get(row.source, (None, "missing"))[0],
        cost_status=cost_map.get(row.source, (None, "missing"))[1],
    )
    for row in rows
]
```

```python
# overview handler — replace the totals block
cost, cost_status = _compute_totals_cost(
    conn, start, end, pricing, models=models_list, api_keys=raw_keys
)
totals = Totals(
    requests=raw.requests,
    tokens=raw.total_tokens,
    cost=cost,
    cost_status=cost_status,
    rpm=rpm,
    tpm=tpm,
)
```

- [ ] **Step 10: Run the parity tests**

Run: `uv run pytest tests/test_routes_usage.py::test_codex_cost_split_matches_ccusage_formula tests/test_routes_usage.py::test_anthropic_cost_unaffected_by_split -v`
Expected: PASS. (The schema field `cost_status` does not yet exist on the DTOs, but pydantic accepts unknown fields when `model_config` does not forbid them; if the test fails with a `cost_status` field error, jump to Task 4 first then return.)

- [ ] **Step 11: Commit**

```bash
git add src/cliproxy_usage_server/routes/usage.py tests/test_routes_usage.py
git commit -m "fix(cost): apply ccusage Codex split per (model, source) cell"
```

---

## Task 4: Add `CostStatus` to response schemas

**Files:**
- Modify: `src/cliproxy_usage_server/schemas.py`

- [ ] **Step 1: Add `CostStatus` import + fields**

Edit `src/cliproxy_usage_server/schemas.py`:

```python
from cliproxy_usage_server.pricing import CostStatus
```

Add `cost_status: CostStatus` to:

```python
class Totals(BaseModel):
    model_config = ConfigDict(frozen=True)
    requests: int
    tokens: int
    cost: float | None
    cost_status: CostStatus
    rpm: float
    tpm: float


class ApiStat(BaseModel):
    model_config = ConfigDict(frozen=True)
    api_key: RedactedApiKey
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float | None
    cost_status: CostStatus
    failed: int
    avg_latency_ms: float


class ModelStat(BaseModel):
    model_config = ConfigDict(frozen=True, protected_namespaces=())
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    total_tokens: int
    cost: float | None
    cost_status: CostStatus
    avg_latency_ms: float
    failed: int


class CredentialStat(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: str  # will become RedactedSource in Task 6
    requests: int
    total_tokens: int
    failed: int
    cost: float | None
    cost_status: CostStatus
```

Add `series_status` to `TimeseriesResponse`:

```python
class TimeseriesResponse(BaseModel):
    model_config = ConfigDict(frozen=True)
    buckets: list[str]
    series: dict[str, list[float]]
    series_status: dict[str, CostStatus] = Field(default_factory=dict)
```

- [ ] **Step 2: Run schema tests**

Run: `uv run pytest tests/test_routes_usage.py -v`
Expected: PASS for the parity tests; existing tests that check the response shape may need to be re-asserted (but `cost_status` is required, so any test that deserialises an ApiStat without `cost_status` will fail). If a test fails because the JSON has `cost_status` it didn't expect, that's fine — pydantic just adds the field. If a test fails because the route handler did not populate `cost_status`, fix the handler.

- [ ] **Step 3: Run full backend suite**

Run: `uv run pytest -x`
Expected: PASS. Fix any handler that returns a DTO without `cost_status`.

- [ ] **Step 4: Commit**

```bash
git add src/cliproxy_usage_server/schemas.py
git commit -m "feat(schemas): cost_status on cost-bearing DTOs; series_status on timeseries"
```

---

## Task 5: Plumb `series_status` through `/api/timeseries?metric=cost`

**Files:**
- Modify: `src/cliproxy_usage_server/routes/usage.py`
- Modify: `tests/test_routes_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_routes_usage.py`:

```python
def test_timeseries_cost_emits_series_status_live(client_with_full_pricing) -> None:
    """When every model in the window has live pricing, series_status is all 'live'."""
    resp = client_with_full_pricing.get(
        "/api/timeseries?range=all&bucket=day&metric=cost"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "series_status" in body
    assert body["series_status"]
    assert all(v == "live" for v in body["series_status"].values())


def test_timeseries_cost_emits_series_status_missing(client_no_pricing) -> None:
    """With empty pricing every series is 'missing'."""
    resp = client_no_pricing.get(
        "/api/timeseries?range=all&bucket=day&metric=cost"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # __all__ aggregates everything; with no pricing, status is missing.
    assert body["series_status"]["__all__"] == "missing"


def test_timeseries_non_cost_metric_has_empty_series_status(client_with_full_pricing) -> None:
    resp = client_with_full_pricing.get(
        "/api/timeseries?range=all&bucket=day&metric=tokens"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["series_status"] == {}
```

(`client_with_full_pricing` is a new fixture — add it to the test module:)

```python
@pytest.fixture()
def app_with_full_pricing(seeded_db_path: pathlib.Path):
    """App with pricing for every model present in the seed."""
    records = _load_records()
    distinct_models = sorted({r.model for r in records})
    pricing = {m: ModelPricing(input_cost_per_token=1e-6, output_cost_per_token=1e-6) for m in distinct_models}
    cfg = ServerConfig(db_path=seeded_db_path)  # pyright: ignore[reportCallIssue]
    return create_app(cfg, pricing_provider=lambda: pricing)


@pytest.fixture()
def client_with_full_pricing(app_with_full_pricing):
    with TestClient(app_with_full_pricing) as c:
        yield c
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/test_routes_usage.py::test_timeseries_cost_emits_series_status_live -v`
Expected: FAIL — `series_status` is empty for cost metric until the route populates it.

- [ ] **Step 3: Update the `/timeseries` cost handler**

In `routes/usage.py`'s `timeseries` route, the `metric == "cost"` branch already calls `_query_bucket_model_costs`. After Task 3 that helper now returns `(cell_costs, model_statuses)`. Replace both branches:

```python
if metric == "cost":
    bfmt = "%Y-%m-%dT%H:00:00Z" if bucket == "hour" else "%Y-%m-%d"

    if is_all_mode:
        ts = query_timeseries(
            conn, start, end, bucket, "tokens",
            models_list, top_n, api_keys=raw_keys,
        )
        labels = ts.buckets
        output_model_keys = [k for k in ts.series if k != "__all__"]

        cell_costs, model_statuses = _query_bucket_model_costs(
            conn, start, end, bfmt, pricing, models=None, api_keys=raw_keys
        )

        all_cost: dict[str, float] = {}
        for (bkt, _model), cost_val in cell_costs.items():
            all_cost[bkt] = all_cost.get(bkt, 0.0) + cost_val

        model_costs: dict[str, dict[str, float]] = {m: {} for m in output_model_keys}
        for (bkt, mdl), cost_val in cell_costs.items():
            if mdl in model_costs:
                prev = model_costs[mdl].get(bkt, 0.0)
                model_costs[mdl][bkt] = prev + cost_val

        cost_series: dict[str, list[float]] = {
            "__all__": [all_cost.get(lbl, 0.0) for lbl in labels]
        }
        for mdl in output_model_keys:
            cost_series[mdl] = [model_costs[mdl].get(lbl, 0.0) for lbl in labels]

        # series_status: __all__ rolls up every model's status; per-model
        # series roll up that model's per-cell statuses.
        all_statuses: list[PricingResolution] = []
        for sts in model_statuses.values():
            all_statuses.extend(sts)
        series_status: dict[str, CostStatus] = {
            "__all__": rollup_cost_status(all_statuses),
        }
        for mdl in output_model_keys:
            series_status[mdl] = rollup_cost_status(model_statuses.get(mdl, []))

        # Trigger background refresh on miss (Task 7 wires this up).
        _maybe_refresh_pricing(request, all_statuses)

        return TimeseriesResponse(
            buckets=labels, series=cost_series, series_status=series_status
        )

    else:
        ts = query_timeseries(
            conn, start, end, bucket, "tokens",
            models_list, api_keys=raw_keys,
        )
        labels = ts.buckets

        cell_costs, model_statuses = _query_bucket_model_costs(
            conn, start, end, bfmt, pricing,
            models=models_list, api_keys=raw_keys,
        )

        assert models_list is not None
        cost_series_explicit: dict[str, list[float]] = {}
        for mdl in models_list:
            mdl_bkt: dict[str, float] = {}
            for (bkt, m), cost_val in cell_costs.items():
                if m == mdl:
                    mdl_bkt[bkt] = mdl_bkt.get(bkt, 0.0) + cost_val
            cost_series_explicit[mdl] = [mdl_bkt.get(lbl, 0.0) for lbl in labels]

        series_status_explicit: dict[str, CostStatus] = {
            mdl: rollup_cost_status(model_statuses.get(mdl, []))
            for mdl in models_list
        }
        all_explicit: list[PricingResolution] = []
        for mdl in models_list:
            all_explicit.extend(model_statuses.get(mdl, []))
        _maybe_refresh_pricing(request, all_explicit)

        return TimeseriesResponse(
            buckets=labels,
            series=cost_series_explicit,
            series_status=series_status_explicit,
        )
```

The `_maybe_refresh_pricing` helper is added in Task 7; for now stub it at module top:

```python
def _maybe_refresh_pricing(
    request: Request, statuses: list[PricingResolution]
) -> None:
    """Stub — Task 7 implements rate-limited background refresh."""
    return
```

The `timeseries` handler must accept `request: Request` if it doesn't already. Add to its signature:

```python
def timeseries(
    request: Request,
    range_: _RangeParam,
    ...
)
```

- [ ] **Step 4: Run timeseries tests**

Run: `uv run pytest tests/test_routes_usage.py -k timeseries -v`
Expected: PASS for the three new tests.

- [ ] **Step 5: Commit**

```bash
git add src/cliproxy_usage_server/routes/usage.py tests/test_routes_usage.py
git commit -m "feat(timeseries): emit series_status for cost metric"
```

---

## Task 6: `redact_source` and `RedactedSource` annotation

**Files:**
- Modify: `src/cliproxy_usage_server/redact.py`
- Modify: `src/cliproxy_usage_server/schemas.py`
- Modify: `tests/test_redact.py`
- Modify: `tests/test_routes_usage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_redact.py`:

```python
from cliproxy_usage_server.redact import redact_source


@pytest.mark.parametrize(
    "raw,expected",
    [
        # OAuth emails pass through unchanged
        ("codex:user@gmail.com", "codex:user@gmail.com"),
        ("claude:foo@example.org", "claude:foo@example.org"),
        ("anthropic:a@b.io", "anthropic:a@b.io"),
        # Key-based provider:key splits and applies redact_key to id
        ("openai:sk-proj-abc123xyz", "openai:sk-*******-abc123xyz"),
        ("anthropic:sk-ant-12345678", "anthropic:sk-*******-12345678"),
        ("openai-compat:sk-team-proj-abcd", "openai-compat:sk-*******-abcd"),
        ("openai:abc123xyz9", "openai:*******xyz9"),
        # No colon -> redact_key on the whole string
        ("sk-rawkey-abc-1234", "sk-*******-1234"),
        ("rawkey1234", "*******1234"),
        # Empty
        ("", ""),
    ],
)
def test_redact_source(raw: str, expected: str) -> None:
    assert redact_source(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "codex:user@gmail.com",
        "openai:sk-proj-abc123xyz",
        "openai:abc123xyz9",
        "sk-rawkey-abc-1234",
        "",
    ],
)
def test_redact_source_idempotent(raw: str) -> None:
    once = redact_source(raw)
    twice = redact_source(once)
    assert once == twice
```

- [ ] **Step 2: Run test to confirm failure**

Run: `uv run pytest tests/test_redact.py -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `redact_source`**

Edit `src/cliproxy_usage_server/redact.py`:

```python
def redact_key(key: str) -> str:
    """Redact an API key for safe display.

    Rules:
      - 3+ dash-parts  → "{first}-*******-{last}"
      - <= 2 parts    → "*******{last 4 chars of the whole key}"
      - Idempotent: redacting a redacted value returns the same value.
    """
    parts = key.split("-")
    if len(parts) >= 3:
        return f"{parts[0]}-*******-{parts[-1]}"
    base = key.removeprefix("*******")
    return f"*******{base[-4:]}"


def redact_source(source: str) -> str:
    """Redact the credential identifier in a `<provider>:<id>` source.

    - id contains '@' (OAuth email)  -> return source unchanged.
    - id has no '@'                  -> treat as API key; rejoin as
                                        f"{provider}:{redact_key(id)}".
    - no ':' separator               -> redact_key on the whole string.
    - Idempotent: any '***' inside the id returns input unchanged.
    """
    if "***" in source:
        return source
    if ":" not in source:
        return redact_key(source)
    provider, _, ident = source.partition(":")
    if "@" in ident:
        return source
    return f"{provider}:{redact_key(ident)}"
```

- [ ] **Step 4: Wire `RedactedSource` into the schema**

Edit `src/cliproxy_usage_server/schemas.py`:

```python
from cliproxy_usage_server.redact import redact_key, redact_source

RedactedApiKey = Annotated[str, BeforeValidator(redact_key)]
RedactedSource = Annotated[str, BeforeValidator(redact_source)]


class CredentialStat(BaseModel):
    model_config = ConfigDict(frozen=True)
    source: RedactedSource
    requests: int
    total_tokens: int
    failed: int
    cost: float | None
    cost_status: CostStatus
```

- [ ] **Step 5: Add an end-to-end redaction test**

Append to `tests/test_routes_usage.py`:

```python
def test_credential_stats_redacts_key_sources(tmp_path: pathlib.Path) -> None:
    """API-key sources get redacted; email sources pass through."""
    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    rows = [
        ("openai:sk-proj-secret-abc12345", "gpt-5"),
        ("codex:tester@example.com", "gpt-5"),
        ("anthropic:sk-ant-01-tail9999", "claude-sonnet-4-5"),
    ]
    for source, model in rows:
        conn.execute(
            "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
            "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
            "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "2026-05-01T00:00:00.000000Z",
                "sk-test", model, source, "0", 100, 100, 50, 0, 0, 150, 0,
            ),
        )
    conn.commit()
    conn.close()

    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    app = create_app(cfg, pricing_provider=lambda: {})

    with TestClient(app) as client:
        resp = client.get("/api/credential-stats?range=all")
        assert resp.status_code == 200, resp.text
        sources = {row["source"] for row in resp.json()}
        assert "openai:sk-*******-abc12345" in sources
        assert "codex:tester@example.com" in sources
        assert "anthropic:sk-*******-tail9999" in sources
        # Raw key must NOT be present.
        assert "openai:sk-proj-secret-abc12345" not in sources
```

- [ ] **Step 6: Run all redaction tests**

Run: `uv run pytest tests/test_redact.py tests/test_routes_usage.py -k redact -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cliproxy_usage_server/redact.py src/cliproxy_usage_server/schemas.py tests/test_redact.py tests/test_routes_usage.py
git commit -m "feat(redact): redact_source for key-based providers; OAuth emails pass through"
```

---

## Task 7: Background pricing refresh on miss (rate-limited 60s)

**Files:**
- Create: `src/cliproxy_usage_server/pricing_refresh.py`
- Modify: `src/cliproxy_usage_server/main.py`
- Modify: `src/cliproxy_usage_server/routes/usage.py`
- Create: `tests/test_pricing_refresh.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing_refresh.py`:

```python
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

    # We did attempt; rate-limit should still advance so we don't hot-loop.
    assert fired is True
    assert state.last_refresh is not None
    # target stays empty because fetcher failed
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
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_pricing_refresh.py -v`
Expected: FAIL with `ImportError` for `pricing_refresh` module.

- [ ] **Step 3: Implement `pricing_refresh.py`**

Create `src/cliproxy_usage_server/pricing_refresh.py`:

```python
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
```

- [ ] **Step 4: Run the unit tests**

Run: `uv run pytest tests/test_pricing_refresh.py -v`
Expected: PASS.

- [ ] **Step 5: Initialize state in `main.py`**

Edit `src/cliproxy_usage_server/main.py` — inside `create_app`'s `lifespan`, after `app.state.pricing = ...` is set (in **both** the `pricing_provider` and production branches), add:

```python
from cliproxy_usage_server.pricing_refresh import PricingRefreshState
...
app.state.pricing_refresh = PricingRefreshState()
```

(Place the import at the top of the file with the others.)

- [ ] **Step 6: Wire the route helper**

Replace the stub `_maybe_refresh_pricing` in `src/cliproxy_usage_server/routes/usage.py` with the real version, and call it from every cost-bearing route handler:

```python
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request

from cliproxy_usage_server.pricing_refresh import (
    PricingRefreshState,
    maybe_refresh_pricing,
)


def _maybe_refresh_pricing(
    request: Request,
    background_tasks: BackgroundTasks,
    statuses: list[PricingResolution],
) -> None:
    """Schedule a non-blocking pricing refresh if any status is 'missing'."""
    if not any(s == "missing" for s in statuses):
        return
    state: PricingRefreshState = request.app.state.pricing_refresh
    target = request.app.state.pricing
    config = getattr(request.app.state, "pricing_config", None)
    if config is None:
        # Tests that pass pricing_provider don't supply a fetcher; no-op.
        return
    background_tasks.add_task(
        maybe_refresh_pricing,
        state=state,
        fetcher=config.fetcher,
        target=target,
    )
```

The `pricing_config` lookup expects `app.state.pricing_config.fetcher` to be set in production. Add to `main.py`'s production lifespan branch (right after `app.state.pricing = fetch_pricing(...)`):

```python
from dataclasses import dataclass

@dataclass
class _PricingConfig:
    fetcher: Callable[[], dict[str, ModelPricing]]

def _build_fetcher(config: ServerConfig) -> Callable[[], dict[str, ModelPricing]]:
    def fetch() -> dict[str, ModelPricing]:
        return fetch_pricing(
            url=config.pricing_url,
            cache_path=_resolve_cache_path(config),
            ttl_seconds=0,  # bypass TTL — caller already decided to refresh
        )
    return fetch

app.state.pricing_config = _PricingConfig(fetcher=_build_fetcher(config))
```

(Place `_PricingConfig` and `_build_fetcher` at module scope alongside `_resolve_cache_path`.)

- [ ] **Step 7: Add `BackgroundTasks` parameters to handlers**

Each cost-bearing handler in `routes/usage.py` (`overview`, `timeseries`, `api_stats`, `model_stats`, `credential_stats`) gains a `background_tasks: BackgroundTasks` parameter and calls `_maybe_refresh_pricing(request, background_tasks, all_statuses)` once it has computed the statuses. For non-`/timeseries` endpoints the statuses come from `_compute_totals_cost`, `_cost_by_api_key`, etc. Plumb them out:

- `_compute_totals_cost` already returns `(cost, cost_status)`. To trigger refresh we need the per-cell statuses, not just the rollup. Change its return to `(cost, cost_status, statuses)`:

```python
def _compute_totals_cost(...) -> tuple[float | None, CostStatus, list[PricingResolution]]:
    ...
    return cost, roll, statuses
```

- `_cost_by_api_key` and `_cost_by_credential` likewise return their flat `list[PricingResolution]` alongside the dict:

```python
def _cost_by_api_key(...) -> tuple[dict[str, tuple[float | None, CostStatus]], list[PricingResolution]]:
    ...
    flat = [s for sts in per_key_statuses.values() for s in sts]
    return result, flat
```

Update each call site in the handlers to unpack the extra return value and pass it to `_maybe_refresh_pricing`. For `model_stats`, build the flat list from `per_model_status.values()`.

- [ ] **Step 8: Add an integration test for refresh dispatch**

Append to `tests/test_pricing_refresh.py`:

```python
def test_route_dispatches_refresh_on_missing(tmp_path: Path, monkeypatch) -> None:
    """A request that resolves a missing model schedules a background refresh."""
    from cliproxy_usage_collect.db import open_db
    from cliproxy_usage_server.config import ServerConfig
    from cliproxy_usage_server.main import create_app
    from fastapi.testclient import TestClient

    db_path = tmp_path / "usage.db"
    conn = open_db(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, api_key, model, source, auth_index, "
        "latency_ms, input_tokens, output_tokens, reasoning_tokens, cached_tokens, "
        "total_tokens, failed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "2026-05-01T00:00:00.000000Z",
            "sk-test", "unknown-model", "openai:sk-x", "0",
            100, 100, 50, 0, 0, 150, 0,
        ),
    )
    conn.commit()
    conn.close()

    cfg = ServerConfig(db_path=db_path)  # pyright: ignore[reportCallIssue]
    fetched: list[bool] = []

    def fetcher() -> dict[str, ModelPricing]:
        fetched.append(True)
        return {"unknown-model": ModelPricing(input_cost_per_token=1e-6)}

    # Stub pricing_config so the route can fetch.
    from dataclasses import dataclass

    @dataclass
    class StubConfig:
        fetcher: object

    app = create_app(cfg, pricing_provider=lambda: {})
    app.state.pricing_config = StubConfig(fetcher=fetcher)

    with TestClient(app) as client:
        resp = client.get("/api/api-stats?range=all")
        assert resp.status_code == 200

    # BackgroundTasks runs synchronously after response in TestClient.
    assert fetched == [True], "expected one fetch"
```

- [ ] **Step 9: Run all refresh tests**

Run: `uv run pytest tests/test_pricing_refresh.py -v`
Expected: PASS.

- [ ] **Step 10: Run the full backend suite**

Run: `uv run pytest -x`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/cliproxy_usage_server/pricing_refresh.py src/cliproxy_usage_server/main.py src/cliproxy_usage_server/routes/usage.py tests/test_pricing_refresh.py
git commit -m "feat(pricing): rate-limited background refresh on missing-model responses"
```

---

## Task 8: Frontend types

**Files:**
- Modify: `frontend/src/types/api.ts`

- [ ] **Step 1: Add `CostStatus` and field updates**

```typescript
export type CostStatus = 'live' | 'partial_missing' | 'missing';

export interface Totals {
  requests: number;
  tokens: number;
  cost: number | null;
  cost_status: CostStatus;
  rpm: number;
  tpm: number;
}

export interface ApiStat {
  api_key: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number | null;
  cost_status: CostStatus;
  failed: number;
  avg_latency_ms: number;
}

export interface ModelStat {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cost: number | null;
  cost_status: CostStatus;
  avg_latency_ms: number;
  failed: number;
}

export interface CredentialStat {
  source: string;
  requests: number;
  total_tokens: number;
  failed: number;
  cost: number | null;
  cost_status: CostStatus;
}

export interface TimeseriesResponse {
  buckets: string[];
  series: Record<string, number[]>;
  series_status: Record<string, CostStatus>;
}
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && bun run tsc --noEmit`
Expected: failures in components that don't yet handle `cost_status` — they'll be fixed in Tasks 9-12.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/types/api.ts
git commit -m "feat(types): cost_status / series_status on cost-bearing DTOs"
```

---

## Task 9: Render `cost_status` in totals + per-row cards

**Files:**
- Modify: `frontend/src/components/usage/StatCards.tsx`
- Modify: `frontend/src/components/usage/StatCards.module.scss`
- Modify: `frontend/src/components/usage/ApiDetailsCard.tsx`
- Modify: `frontend/src/components/usage/ApiDetailsCard.module.scss`
- Modify: `frontend/src/components/usage/ModelStatsCard.tsx`
- Modify: `frontend/src/components/usage/ModelStatsCard.module.scss`
- Modify: `frontend/src/components/usage/CredentialStatsCard.tsx`
- Modify: `frontend/src/components/usage/CredentialStatsCard.module.scss`

The repeated rendering rule lives best as a tiny shared helper. Add it once in a new file then import.

- [ ] **Step 1: Create a shared cost-cell helper**

Create `frontend/src/components/usage/CostCell.tsx`:

```typescript
import type { CostStatus } from '@/types/api';
import styles from './CostCell.module.scss';

export interface CostCellProps {
  cost: number | null;
  status: CostStatus;
  /** Number of decimal places for $-formatting. Defaults to 4. */
  decimals?: number;
}

const TOOLTIP: Record<CostStatus, string> = {
  live: '',
  partial_missing: 'Pricing unavailable for some models — partial total',
  missing: 'No pricing available for this model',
};

export function CostCell({ cost, status, decimals = 4 }: CostCellProps) {
  const className = status === 'live' ? undefined : styles.warning;
  const tooltip = TOOLTIP[status] || undefined;
  const text = cost === null ? '—' : `$${cost.toFixed(decimals)}`;
  return (
    <span className={className} title={tooltip}>
      {text}
    </span>
  );
}
```

Create `frontend/src/components/usage/CostCell.module.scss`:

```scss
.warning {
  color: var(--color-danger, #d6453d);
  cursor: help;
}
```

- [ ] **Step 2: Update `StatCards.tsx` totals rendering**

Find the `cost` formatter (around line 78). Replace with a render that uses the helper. Example diff:

```typescript
import { CostCell } from './CostCell';

// In the cards array:
{
  key: 'cost',
  label: 'Cost',
  render: (o: OverviewResponse) => (
    <CostCell cost={o.totals.cost} status={o.totals.cost_status} decimals={2} />
  ),
},
```

(If `StatCards.tsx` formats inline rather than via a `render` prop, replace the inline `${...}` expression with `<CostCell .../>`. Show the JSX inline where the price is rendered.)

- [ ] **Step 3: Update `ApiDetailsCard.tsx`**

Replace the local `renderCost` helper:

```typescript
import { CostCell } from './CostCell';

// remove the old renderCost function

// in the row:
<td><CostCell cost={row.cost} status={row.cost_status} /></td>
```

Remove the now-unused `hasPricing` prop / argument if it was only feeding `renderCost`. If `hasPricing` is still used elsewhere in the file, leave it.

- [ ] **Step 4: Update `ModelStatsCard.tsx`**

Same replacement as ApiDetailsCard:

```typescript
import { CostCell } from './CostCell';

<td><CostCell cost={row.cost} status={row.cost_status} /></td>
```

- [ ] **Step 5: Update `CredentialStatsCard.tsx`**

Same pattern:

```typescript
import { CostCell } from './CostCell';

<td><CostCell cost={row.cost} status={row.cost_status} /></td>
```

- [ ] **Step 6: Run typecheck and dev build**

Run: `cd frontend && bun run tsc --noEmit && bun run build`
Expected: PASS. If `tsc` complains about a missing `cost_status` on a mock or fixture, update the fixture inline.

- [ ] **Step 7: Verify in the browser**

Run: `cd frontend && bun run dev` (in a separate terminal, with the backend running). Open `http://localhost:5173`. Confirm:
- Cost columns render normally for `live` rows.
- A row with no pricing shows `—` in red with a hover tooltip.
- (Optional) seed a row with an unknown model name and verify the tooltip text.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/usage/CostCell.tsx frontend/src/components/usage/CostCell.module.scss frontend/src/components/usage/StatCards.tsx frontend/src/components/usage/ApiDetailsCard.tsx frontend/src/components/usage/ModelStatsCard.tsx frontend/src/components/usage/CredentialStatsCard.tsx
git commit -m "feat(ui): render cost_status warning state via shared CostCell"
```

---

## Task 10: Style the cost-trend chart for `series_status`

**Files:**
- Modify: `frontend/src/components/usage/CostTrendChart.tsx`
- Modify: `frontend/src/components/usage/CostTrendChart.module.scss`

- [ ] **Step 1: Read the current chart**

Run: `cat frontend/src/components/usage/CostTrendChart.tsx | head -120`
Identify where `series` is converted into Chart.js datasets.

- [ ] **Step 2: Apply per-series styling based on `series_status`**

For each dataset built from the response, look up `data.series_status?.[seriesName] ?? 'live'`. When the status is `partial_missing` or `missing`, set:

```typescript
borderDash: [6, 4],         // dashed
borderColor: 'var(--color-danger, #d6453d)' or the dataset's existing color with reduced opacity for missing,
```

Concrete edit (inside the dataset-building map):

```typescript
const datasets = Object.entries(data.series).map(([name, points]) => {
  const status = data.series_status?.[name] ?? 'live';
  const isWarning = status !== 'live';
  return {
    label: name === '__all__' ? 'Total' : name,
    data: points,
    borderColor: isWarning ? 'rgba(214, 69, 61, 0.9)' : /* existing color */,
    borderDash: isWarning ? [6, 4] : undefined,
    // …
  };
});
```

- [ ] **Step 3: Add a legend chip indicating partial/missing**

Add a `<small>` below the chart title summarising any non-live series:

```tsx
const degraded = Object.entries(data.series_status ?? {}).filter(([, s]) => s !== 'live');
{degraded.length > 0 && (
  <small className={styles.warning}>
    Partial/missing pricing: {degraded.map(([n]) => (n === '__all__' ? 'total' : n)).join(', ')}
  </small>
)}
```

Add a `.warning` rule to `CostTrendChart.module.scss`:

```scss
.warning {
  color: var(--color-danger, #d6453d);
  display: block;
  margin-top: 0.25rem;
}
```

- [ ] **Step 4: Build and visual-check**

Run: `cd frontend && bun run build`
Expected: PASS. Then `bun run dev`, navigate to the page, confirm:
- All-live cost trend looks unchanged.
- A series with `partial_missing` renders dashed and the warning footer lists it.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/usage/CostTrendChart.tsx frontend/src/components/usage/CostTrendChart.module.scss
git commit -m "feat(ui): dashed lines + footer chip for partial_missing/missing cost series"
```

---

## Task 11: README "Cost accuracy" subsection

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the subsection under `## Webapp`**

Insert a new `### Cost accuracy` section between the existing `### Server environment variables` and `### Production run (single process)`:

```markdown
### Cost accuracy

The webapp computes per-row cost using liteLLM's pricing data
(`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`,
disk-cached locally) and a few provider-aware adjustments. Three things to know:

- **Codex/OpenAI rows** apply ccusage's split: upstream returns
  `cached_tokens` as a *subset* of `input_tokens`, so the dashboard subtracts
  the cached portion from input before billing input rate, and bills the cached
  portion at the cache-read rate. This matches the math in
  `apps/codex/src/command-utils.ts` of `ghcr.io/ryoppippi/ccusage`.
- **Anthropic rows undercount cache writes.** Upstream CLIProxyAPI collapses
  Anthropic's `cache_creation_input_tokens` and `cache_read_input_tokens` into
  a single `cached_tokens` value before pushing to its usage queue. The
  webapp bills that combined value at the cache-read rate (~10% of input)
  rather than the cache-creation rate (~125% of input) for the
  creation portion. Claude totals are therefore a **lower bound** when prompt
  caching is in use; the magnitude of the undercount depends on your
  cache-write/read ratio. There is no purely-backend fix because the data
  needed to split the two buckets is not in the queue payload.
- **Missing pricing.** When liteLLM has no entry for a model the dashboard
  renders that row's cost as `—` in red ("missing") and triggers a
  background re-fetch (rate-limited to one per minute). Roll-up rows that
  combine some live and some missing models render their partial total in
  red ("partial_missing").
```

- [ ] **Step 2: Verify rendering**

Run: `cat README.md | sed -n '/### Cost accuracy/,/^### /p' | head -30`
Expected: the subsection prints cleanly, no markdown breakage.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README Cost accuracy subsection (Codex split, Claude lower bound, missing)"
```

---

## Task 12: Final cross-suite verification

**Files:** none.

- [ ] **Step 1: Run the full backend suite + lint + types**

```bash
uv run ruff check
uv run ruff format --check
uv run basedpyright
uv run pytest
```

Expected: all green. Fix any formatting or type errors inline before continuing.

- [ ] **Step 2: Run the frontend build**

```bash
cd frontend && bun run tsc --noEmit && bun run build
```

Expected: PASS.

- [ ] **Step 3: Manual smoke**

Start backend and frontend; load the dashboard against a populated dev DB. Confirm:
- Codex rows now show smaller costs than before (subtraction took effect).
- A row pointing at a model not in liteLLM shows `—` red with tooltip.
- `/api/credential-stats` keys are redacted; emails pass through.

- [ ] **Step 4: Final commit if any fixups landed**

```bash
git add -A
git diff --cached --stat
git commit -m "chore: post-verification fixups" || true
```

---

## Self-review notes

- All five spec sections (0 README, 1 Codex split, 2 missing/refresh, 3 redaction, plus cost_status schema/frontend) are mapped to tasks: §0 → Task 11, §1 → Tasks 1+3, §2 → Tasks 2+4+5+7+8+9+10, §3 → Task 6.
- `resolve()` signature changes in Task 2 and is consumed coherently in Tasks 3, 5, 7. Property names align: `cost_status`, `series_status`, `PricingResolution`, `CostStatus`, `rollup_cost_status`, `split_tokens_for_cost`, `redact_source`, `RedactedSource`, `PricingRefreshState`, `maybe_refresh_pricing`, `REFRESH_MIN_INTERVAL_SECONDS` are referenced consistently across tasks.
- No DB migration; collector schema untouched (per non-goals).
- Task 7's `_PricingConfig` indirection is needed because `pricing_provider`-mode tests don't supply a real fetcher; the route helper no-ops in that case.
