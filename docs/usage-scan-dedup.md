# Plan: read usage from a rollup table

## The problem

Every usage endpoint gets its numbers from `_fetch_cells` in `src/cliproxy_usage_server/aggregate.py`. It groups every `requests` row in the window into priced cells, one per 15-minute UTC slot, API key, model and source. On a DB with 1.5M rows, each all-time request takes about 2.9 s. One page load sends `summary` and two `breakdown`s, and `/options` scans all history again. The cost grows with the number of rows, so it gets worse every day.

## The idea

Keep a summary table, `requests_rollup`, inside `usage.db`. It holds one row per 15-minute UTC slot, model, API key and source: about 15k rows for 1.5M requests. An `AFTER INSERT` trigger on `requests` keeps it current. The collector only appends with `INSERT OR IGNORE`, and ignored duplicates do not fire the trigger. The rollup is derived data: it can always be dropped and rebuilt from `requests`, so a change to it needs no data migration, only a schema version step that rebuilds it. The server reads whole slots from the rollup and the partial slots at each window edge from `requests`, so every result stays exact.

Costs are the hard part. Each request's cost depends on its own size compared to its model's threshold, and the pricing that decides each model's `CostPlan` is refreshed from LiteLLM at runtime. So the rollup cannot store costs. It stores token sums that let any plan that `cost_plan` in `src/cliproxy_usage_server/pricing.py` can produce price a slot exactly as the per-request query does today.

## Rollup schema

`cost_plan` produces three plan shapes. Each needs its own sums:

| Shape | Who | Billed at the long rate |
| --- | --- | --- |
| Marginal, 200k | non-OpenAI models with `*_above_200k_tokens` rates or none | each token type's excess over 200k |
| Whole request, 200k | OpenAI models without 272k rates | the whole request when input > 200k |
| Whole request, 272k | OpenAI models with 272k rates, and non-OpenAI models with only `*_above_272k_tokens` rates | the whole request when input > 272k |

Every model's `input_tokens` includes its cache reads and cache creation, so uncached input is `input_tokens - cache_read_tokens - cache_creation_tokens`, and no clamp is needed.

```sql
CREATE TABLE requests_rollup (
  slot                  INTEGER NOT NULL,  -- CAST(strftime('%s', timestamp) AS INTEGER) / 900, as _fetch_cells today
  api_key               TEXT    NOT NULL,
  model                 TEXT    NOT NULL,
  source                TEXT    NOT NULL,
  requests              INTEGER NOT NULL,
  failed                INTEGER NOT NULL,
  input_tokens          INTEGER NOT NULL,  -- all input, as in requests
  output_tokens         INTEGER NOT NULL,
  reasoning_tokens      INTEGER NOT NULL,
  cache_read_tokens     INTEGER NOT NULL,
  cache_creation_tokens INTEGER NOT NULL,
  latency_ms            INTEGER NOT NULL,  -- sum over successful requests (failed = 0)
  last_ts               TEXT    NOT NULL,  -- MAX(timestamp), as _fetch_cells today
  -- marginal 200k: SUM(MIN(x, 200000)) per token type, x = uncached input, output, cache read, cache creation
  uncached_200k         INTEGER NOT NULL,
  output_200k           INTEGER NOT NULL,
  cache_read_200k       INTEGER NOT NULL,
  cache_creation_200k   INTEGER NOT NULL,
  -- whole request: sums over requests with input_tokens > 200000 (long200) or > 272000 (long272)
  long200_uncached       INTEGER NOT NULL,
  long200_output         INTEGER NOT NULL,
  long200_cache_read     INTEGER NOT NULL,
  long200_cache_creation INTEGER NOT NULL,
  long272_uncached       INTEGER NOT NULL,
  long272_output         INTEGER NOT NULL,
  long272_cache_read     INTEGER NOT NULL,
  long272_cache_creation INTEGER NOT NULL,
  -- successful requests with tokens at the long rate, one count per shape:
  -- marginal 200k: any of the four token types > 200000; the others as their long sums above
  marginal200_requests  INTEGER NOT NULL,
  long200_requests      INTEGER NOT NULL,
  long272_requests      INTEGER NOT NULL,
  PRIMARY KEY (slot, api_key, model, source)
) WITHOUT ROWID;
```

The base-rate amounts of a cell are its totals minus its long sums (whole-request shapes) or its `*_200k` sums (marginal). Every number is a plain sum, so slots add up, and a group without the slot column (`/options`) prices exactly too.

Each per-request expression has exactly one definition, shared by the trigger, the refill and the server's edge query, and the 200k and 272k thresholds live in one place that `cost_plan` uses as well. A test enumerates the plan shapes that `cost_plan` can produce and fails when a shape has no rollup columns, so a new tier cannot price wrongly in silence. (Today `_base_rate` holds this logic; it goes away.)

Rows whose timestamp SQLite cannot parse (`strftime` gives NULL) are skipped by the trigger and the refill. Window queries already ignore them, and `/options` now ignores them too.

## Work

### 1. Collector: maintain the rollup

In `src/cliproxy_usage_collect/db.py`, which stays the authoritative schema:

- `SCHEMA_VERSION` becomes 2. A fresh DB gets `requests`, the rollup and the trigger at version 2. `open_db` takes a version-1 DB to version 2 with the rebuild below, which also sets `user_version` to 2 in its transaction; the stored version, not the definitions in `sqlite_master`, identifies the layout. A later change to the rollup or trigger is another version step.
- The rebuild drops the rollup and the trigger, creates them again, and fills the rollup from `requests` in one write transaction. In the version step it takes the write lock first (an immediate transaction) and only then checks `user_version` again, doing nothing if it is already 2, so when two collectors start the step at the same time, the second one waits for the lock and finds the DB current. Every connection that writes or rebuilds (collector, rebuild command) waits up to 60 s for the lock, not Python's default 5 s; otherwise a collector insert that collides with a rebuild fails after it has already drained the upstream queue, and those records are lost. `open_db` and the console script below use this one implementation.
- Add a console script `cliproxy-usage-rebuild-rollup` in `pyproject.toml`. It reads only `USAGE_DB_PATH` (with the collector's default), so it does not use `load_config`, which requires `CLIPROXY_MANAGEMENT_KEY`. It exits 0 on success, 2 on a config error like the collector, and 1 on other failures. Operators run it by hand after deleting or replacing `requests` rows.
- `insert_records` must still return the number of new `requests` rows. `conn.total_changes` also counts the trigger's writes, so its current delta would be wrong.
- The `requests` table, its indexes and the collector's exit codes stay as they are.

### 2. Server: read from the rollup

- `_fetch_cells` returns the same cells as today. It takes whole slots inside the window from the rollup. It takes the partial slot at each edge from `requests`, grouped by slot, API key, model and source with the same expressions, through the existing `datetime(timestamp)` index. A slot is whole when its seconds `[900 * slot, 900 * slot + 900)` lie inside the window, compared at whole seconds the way the current `datetime()` filter compares. Return cells in today's order (slot, API key, model, source), so float sums are added in the same order.
- One Python function prices a cell from its sums and its model's `CostPlan`. `CostPlan.costs` stays the only place that multiplies amounts by rates (it also gives cost by type, long-context cost and cache savings).
- The model list in `_fetch_cells` and `resolve_filter` read distinct values from the rollup, not from `requests`.
- `query_options` groups the rollup by API key, model and source only.
- The server stays read-only (`open_ro`) and never builds the rollup. Its existing startup check in `run()` compares `user_version` with `SCHEMA_VERSION`, so on a version-1 DB it exits with the message to run `cliproxy-usage-collect` once, whose `open_db` builds the rollup.
- Totals, trends, mix and ranking keep running on cells as they do today.

### 3. Tests and docs

- Collector tests on synthetic data: random sequences of new and duplicate inserts leave the rollup equal to a fresh rebuild; `insert_records` returns the new-row count; a version-1 DB is rebuilt and set to 2; the rebuild command reproduces the incremental rollup exactly; `requests` and its indexes have the same `sqlite_master` entries as before.
- Cost tests: `request_cost` in `tests/conftest.py` prices each request through both paths (a whole slot from the rollup and a partial edge slot from `requests`) and asserts they agree. `tests/test_pricing_ccusage.py` stays green with its current amounts. The plan-shape test from the schema section covers requests on both sides of each threshold.
- Edge tests: in a window that starts and ends mid-slot, rows of the same slot just inside and just outside each edge are counted correctly.
- Server startup: the server refuses a version-1 DB.
- Update `AGENTS.md`: the rollup and how it stays current, the read path, and the version-1-to-2 step in `open_db`. Update `README.md`: the server refuses a version-1 DB until the collector has run once, and the rebuild command as a one-off repair.

## Reference responses and benchmark

Before you change server code, dump reference JSON from commit `1df36fe` (a git worktree), with a fixed `end` on every request. Use the extracted `test/cpa-usage.tar.gz` (`usage.db` and its `pricing.json`, loaded instead of fetched), and work on copies when the rollup has to be written. Keep the dumps, the copies and the scripts outside the repo. Nothing from that data goes into tests, fixtures, docs or commits. Cover `summary`, `breakdown` for each dimension and `options`, on these windows: all time, a calendar week in `America/Chicago`, an `Asia/Kolkata` (+05:30) calendar day, an `Asia/Kathmandu` (+05:45) day, rolling 7h and 24h windows whose `end` is not on a 15-minute boundary, model, person and credential filters alone and together, and the `hour`, `day`, `week` and `auto` buckets.

Time the endpoints in-process through FastAPI's `TestClient`, on the same machine, with the same requests for the baseline and the new code. Each timing is the median of 5 runs after one warm-up run. Baseline on 1.5M rows: about 2.9 s per all-time request (also `/options`), 1.1 s per 30-day request and 0.29 s per rolling 7h request.

## Definition of done

| Criterion | Check |
| --- | --- |
| Backend green | `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` pass |
| Frontend green | `nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'` passes |
| Browser tests green | the e2e command in `AGENTS.md` passes |
| Same responses | every reference response from `1df36fe` (except `/matrix`) is byte-identical JSON to the new server's response (no float tolerance) |
| Fast "All" page | the 3 all-time panel requests (`summary`, `breakdown?dim=person`, `breakdown?dim=model`) plus `options`, sent at once from four threads, finish within 0.5 s of wall time |
| Other windows at least 2x faster | each 30-day, rolling 7h and filtered reference request takes at most 50% of its baseline time |
| Collector cost | inserting 10,000 new rows into a copy of the test DB through `insert_records` takes at most 1.5 s; `cliproxy-usage-rebuild-rollup` on a copy takes at most 10 s |
| `requests` unchanged | the `sqlite_master` rows for `requests` and its indexes on a DB opened by the new `open_db` match those from `1df36fe` |
