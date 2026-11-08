# Plan: more detail in the usage drawer

## Goal

Detailed usage belongs in the drawer; the main page keeps its layout. Everything here is computed from columns `requests` already stores. Failed requests (about half in the snapshot, almost all near-instant zero-token rejections) must not drag down latency.

## Changes

1. **Latency counts successful requests only**, everywhere it appears (KPI strip, model rows' Latency column, drawer). Output speed is `output_tokens` (the all-request sum) per second of successful-request latency.
2. **KPI strip:** same five tiles. Failure rate and Avg latency get sparklines like the other three; buckets without requests (for failure rate) or without successful requests (for latency) are gaps, not zeros.
3. **Drawer** (`frontend/src/components/usage/DetailDrawer.tsx`), for every entity it opens on (people, models, credentials). It shows token types separately and never a token total:
   - Six tiles in two rows, each with a value, one caption, and change vs previous period: Spend (share of total), Requests (share of total, failure rate), Avg latency (output speed), Input (share of total), Output (reasoning share of output; stored reasoning tokens are a subset of output), Cached (cache hit rate). The Tokens tile and the token mix line are removed.
   - Under the over-time chart, a failure-rate strip aligned with its buckets, on a fixed 0 to 100% scale so a 2% entity stays flat; buckets without requests stay empty. The over-time chart itself is unchanged in both metric modes.
   - After the share bars, spend by token type: input, output, cache read as a 100% bar with amounts. Under it, one line with cache savings: what cache reads would have cost at the same tier's uncached input rate, minus what they cost.
   - Long context: share of spend billed at long-context rates, share of successful requests with any tokens billed at the long rate, and average context: (`uncached_input_tokens` + `cache_read_tokens`) / (`requests` - `failed`), using the all-request sums. Only models whose long rates differ from their base rates (`plan.above != plan.base`, as `_model_price` decides) contribute to `long_context_cost` and `long_context_requests`; the request share's denominator and the average context cover all of the entity's successful requests. Hidden when the entity has no long-context spend.
4. **CSV export:** a download button in the filter bar next to Refresh exports the current range and filters as one row per bucket x person x model x credential, for combinations with at least one request. It uses the page's shared `ResolvedWindow` (from `useUsageWindow`), so the file matches the screen. Keys are redacted exactly as on screen.

The main page's tables and heatmap do not change.

## API shape

`Totals` (`src/cliproxy_usage_server/schemas.py`, mirrored by hand in `frontend/src/types/api.ts`) changes as below; ratios are derived in `frontend/src/utils/usage/totals.ts`. Uncached input and cache read follow each model's token accounting (as `_fetch_cells` splits `inp` and `cr`; unpriced models use the non-OpenAI accounting). The Input and Cached tiles show these two, and hit rate is cache read over (uncached input + cache read).

```python
class CostByType(BaseModel):
    input: float
    output: float
    cache_read: float

class Totals(BaseModel):
    ...                                # existing fields, minus avg_latency_ms
    latency_ms: int                    # sum over successful requests
    uncached_input_tokens: int
    cache_read_tokens: int
    cost_by_type: CostByType | None    # cost is the sum of these three parts
    long_context_cost: float | None
    cache_savings: float | None        # these three are None exactly when cost is None
    long_context_requests: int         # successful requests with tokens billed at the long rate
```

Summary `series` adds `failed` and `latency_ms`. Export is `GET /api/usage/export`, taking the same range, filter and `bucket` params as `breakdown` and returning `text/csv` with columns `bucket_start` (ISO with the viewer's offset), `person`, `person_label`, `model`, `credential`, `requests`, `failed`, `input_tokens`, `output_tokens`, `cached_tokens`, `reasoning_tokens`, `cost` (empty for unpriced models).

## Constraints

- Every new cost figure comes from the per-request split `_fetch_cells` (`src/cliproxy_usage_server/aggregate.py`) already computes.
- The rollup planned in `docs/usage-scan-dedup.md` must stay possible: update its schema with the sums the new fields need (for `long_context_requests`, one count of successful requests per plan shape, using the same per-request condition that decides long-rate tokens, since the plan is chosen from pricing at runtime).

## Done when

- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` pass. New tests cover, through the production query: latency excluding failed requests; cost by type summing to cost; uncached input, cache read, cache savings, long-context cost and long-context request count for single requests of each `CostPlan` shape and a model without a long tier, against fixed hand-checked amounts; the export CSV's columns, redaction, and agreement with `breakdown` totals.
- `nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'` passes, with bun:test coverage for every new derived ratio (including zero-request, all-failed and missing-cost cases).
- The Playwright suite passes (command in `AGENTS.md`), plus a new test that the export button downloads a CSV for the current filters.
- `AGENTS.md` describes the new `Totals` fields, series, export endpoint, and drawer layout; `docs/usage-scan-dedup.md`'s rollup schema has a column for every new sum.
- With the server on the extracted snapshot, `GET /api/usage/summary?start=2026-09-17T00:00:00Z&end=2026-10-18T00:00:00Z&tz=UTC` gives `latency_ms / (requests - failed)` between 27,000 and 29,000.
