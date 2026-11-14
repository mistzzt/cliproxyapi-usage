# Plan: store each token count with one meaning for every row

## Problem

The collector copies token counts from CLIProxyAPI's usage queue into the `requests` table, and the dashboard prices them. CLIProxyAPI has changed what its `total_tokens` and `cached_tokens` fields count over time, so the same stored column means different things depending on the row's model and when it was recorded. For Claude rows, `input_tokens` holds only the input that was not served from the prompt cache, and `cached_tokens` mixes cache reads with cache writes (tokens written into the cache, which bill at about 125% of the input rate, not the cache-read rate of about 10%). For other models, `input_tokens` already holds all input. The server therefore guesses from the pricing data which of the two a row uses, and it never stores or prices Claude cache writes.

After this change each token column counts one defined thing for every row, whatever CLIProxyAPI reports, and "tokens" means what OpenAI's `total_tokens` and Anthropic's usage mean: all input plus all output.

## What the `requests` table stores

The token columns of `requests` in `src/cliproxy_usage_collect/db.py` (the authoritative schema) become:

```sql
  input_tokens          INTEGER NOT NULL,            -- all input: uncached + cache read + cache creation
  output_tokens         INTEGER NOT NULL,            -- all output, reasoning included
  reasoning_tokens      INTEGER NOT NULL,            -- part of output_tokens
  cache_read_tokens     INTEGER NOT NULL,            -- part of input_tokens (renamed from cached_tokens)
  cache_creation_tokens INTEGER NOT NULL DEFAULT 0,  -- part of input_tokens (new)
  cache_creation_1h_tokens INTEGER NOT NULL DEFAULT 0, -- part of cache_creation_tokens written with a 1-hour lifetime (new)
  total_tokens          INTEGER NOT NULL,            -- upstream's reported total; never read
  service_tier          TEXT,                        -- tier the response was billed at; NULL when unknown (new, stored only)
  ttft_ms               INTEGER,                     -- time to first token; NULL when not measured (new, stored only)
```

"Cache creation" is the name Anthropic's usage uses for cache writes; the two terms mean the same tokens. Uncached input, the part billed at the plain input rate, is `input_tokens - cache_read_tokens - cache_creation_tokens`.

`RequestRecord` (`src/cliproxy_usage_collect/schemas.py`, the row type the collector inserts) uses the new names. `_INSERT` and every other `INSERT INTO requests`, tests included, name their columns, so column order does not matter: an upgraded table has the new columns last, and a new one lists them as above.

**Where the numbers come from** (`src/cliproxy_usage_collect/parser.py`). Since v7.2.97, CLIProxyAPI adds a `token_breakdown` object to each queue record that splits input into its total, cache reads and cache writes, and output into its total and reasoning. The parser reads token counts only from it: `input.total_tokens`, `input.cache_read_tokens`, `input.cache_write_tokens`, `output.total_tokens`, `output.reasoning_tokens` and `total_tokens` fill the columns above. A record without `token_breakdown`, or with a `schema_version` other than 2, raises `SchemaError` (collector exit 4).

CLIProxyAPI sets the breakdown's `quality` field to something other than `complete` when it could not split a request's tokens. Such a row stores 0 in every token column except `total_tokens`, so it still counts as a request but adds no tokens or cost, and the collector's summary line reports how many such rows the batch had.

Three columns come from optional payload fields:

- `cache_creation_1h_tokens` from `tokens.cache_creation_1h_tokens`: missing means 0, and it is capped at the stored `cache_creation_tokens`, so it is 0 when the breakdown is not `complete`.
- `service_tier` from `response_service_tier`: missing or empty means NULL.
- `ttft_ms` from `ttft_ms`: missing or 0 means NULL. CLIProxyAPI reports 0 when it saw neither a first token nor a first packet from upstream.

## How an existing DB is upgraded

Every existing DB has the old layout, so the collector converts it once, and both programs need a way to tell the layouts apart. The DB records its layout number in SQLite's `PRAGMA user_version`, a single integer stored in the file: 0 is the old `cached_tokens` layout (every existing DB), 1 is the new one. A small module that imports nothing else from the project, `src/cliproxy_usage_collect/schema_version.py`, holds `SCHEMA_VERSION = 1`, the function that reads `user_version`, the error for a DB from a newer version and the one message wording for a DB at the wrong version. `db.py`, `migration.py` and the server import it; the server does not import `db.py`.

1. **Collector `open_db`** handles three cases. A DB whose `user_version` is above `SCHEMA_VERSION` raises an error that the CLI maps to exit 2 (config), before the queue is popped. A DB with no `requests` table gets the table, its indexes and version 1 in one transaction. A version-0 DB with a `requests` table is migrated (step 2). Every schema change runs under one write lock: take it (`BEGIN IMMEDIATE`), read the version again (a newer one raises), for a new DB check again whether the table exists, act or do nothing, commit. This way two collectors that start at the same time cannot both create or migrate the DB. Any other SQLite error from `open_db` makes the collector exit 1.

2. **Migration** lives in its own module, `src/cliproxy_usage_collect/migration.py`, which `open_db` calls for version 0. It is the only code that knows the old layout. It ships in 0.3.x only and a follow-up release deletes it once the deployed DB is migrated. It first writes a consistent copy of the DB to a temporary file with `VACUUM INTO` (which cannot run inside the write transaction). Then, under the write lock, it checks that `user_version` is still 0. If another run migrated the DB in the meantime, it deletes its copy and does nothing. Otherwise it syncs the copy to disk, moves it to `<db>.pre-cache-columns` (replacing an earlier file of that name), and in the same transaction:
   - sets `user_version` to 1;
   - renames `cached_tokens` to `cache_read_tokens`;
   - adds `cache_creation_tokens`, `cache_creation_1h_tokens`, `service_tier` and `ttft_ms` (old rows keep the defaults 0, 0, NULL, NULL);
   - rewrites rows whose model starts with `claude`, whose `input_tokens` held only uncached input (every other model in the data already stores all input in `input_tokens`). From the old values: cache creation = `MAX(0, total_tokens - input_tokens - output_tokens - cache_read_tokens)`, and input = input + cache read + cache creation;
   - sets every other row's cache read to `MIN(cache_read_tokens, input_tokens)`, so no row has negative uncached input.

   Any failure, including another process holding the lock, leaves the DB unchanged and makes the collector exit 1 (transient) before the queue is popped, so cron retries on the next run.

3. **Server startup** (`run()` in `src/cliproxy_usage_server/main.py`, before uvicorn starts). The server opens the DB read-only and never changes it, so it cannot migrate. When the DB file exists, it reads `user_version` through `open_ro`; if that differs from `SCHEMA_VERSION`, or cannot be read, the server exits non-zero with a message naming both versions and what to do (below: run `cliproxy-usage-collect` once to migrate; above: upgrade the server). It does not check again after startup.

4. **Release 0.3.0.** Bump `pyproject.toml` to `0.3.0`; `flake.nix` reads the version from `pyproject.toml` (`builtins.fromTOML`) instead of repeating it. The `migration.py` docstring states that it ships in 0.3.x and is removed in 0.4.0. Start `CHANGELOG.md` with a 0.3.0 entry listing the changes in this plan, the breaking schema change, and the upgrade steps: CLIProxyAPI v7.2.97 or later; the collector migrates on its first run and leaves the backup file; run a 0.3.x collector once before moving to 0.4 or later; the server refuses an unmigrated DB at startup; a collector exits 2 on a DB from a newer version. `README.md` links to it. Tag `v0.3.0` on the merge commit.

## How cost and the dashboard change

**Cost of each request** (`_fetch_cells` in `src/cliproxy_usage_server/aggregate.py`, the one query that prices requests). Every request splits into five token types, each billed at its own rate: uncached input, cache read, 5-minute cache creation (`cache_creation_tokens - cache_creation_1h_tokens`), 1-hour cache creation, and output. The same split applies to every model, so the query no longer reads the plan's `openai` flag (the `CostPlan.openai` field is removed) and no longer caps cache reads at input. Each model's `CostPlan` (its rates, long-context threshold and how the higher rates apply) still decides the long-context rule. Plans that bill the whole request at the higher rates compare `input_tokens` with the threshold. Plans that bill only the tokens above the threshold apply it to each type's own amount, and a request counts as long-context when any of the five types is above it.

**Rates.** Cache creation bills at `Rates.cache_creation`, and its 1-hour part at twice `Rates.input` of the tier it falls in (`base`, or `above` for the long-context tier; `above.input` already falls back to the base rate). This follows ccusage (`rust/crates/ccusage-core/src/cost.rs` in `/home/zwang/personal/ccusage`: `CACHE_CREATE_1H_INPUT_MULTIPLIER`, `tiered_cost`) and uses the same `CostPlan` rules. `cost_by_type` (the cost split by token type) gains `cache_creation`, which includes both parts. `ModelPrice` is unchanged.

**API** (`src/cliproxy_usage_server/schemas.py`, mirrored by hand in `frontend/src/types/api.ts`). Totals carry `input_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `output_tokens` and `reasoning_tokens`. Series, trends and mix entries rename `cached_tokens` to `cache_read_tokens`. The CSV export carries all five plus `cache_creation_1h_tokens`. `cached_tokens`, `uncached_input_tokens` and `ModelPrice.cached_in_input` are removed.

**Dashboard.** `tokens()` stays input + output, and every place that shows tokens follows it, now with cache reads and writes inside input. Cache hit rate is cache reads over input everywhere, model rows included. The drawer's token tiles are Input (caption: share from cache reads), Output (caption: reasoning share) and Cache writes (`cache_creation_tokens`; caption: share of input). The Cache writes tile appears only when the entity has cache writes in the range, and the models table lists the cache write rate under the same rule. Spend by token type has four parts (Input, meaning the uncached part, Output, Cache read and Cache write); the cache savings line is unchanged.

## Known limits

Record these in `AGENTS.md`:

- Migrated Claude rows that wrote to the cache without reading it stay counted as cache reads.
- Claude rows recorded before CLIProxyAPI v7.1.26 have no cache creation, because their total did not include it.
- 1-hour cache writes are known only from CLIProxyAPI v8.0.48 on; earlier and migrated rows price every write at the 5-minute rate.
- `ttft_ms` exists only for requests where CLIProxyAPI saw a first token or packet.

## Constraints

- Only the migration may know the old layout or decide anything from a row's model name; every other read uses the columns as defined above.
- Update `AGENTS.md`: the `user_version` behavior (`open_db`'s three cases, the migration's check that the version is still 0, exit 2 for a newer DB, `run()`'s startup check) and its schema, token and pricing text, which still describes `cached_tokens`, `price.cached_in_input`, the rule that cached tokens are part of input for OpenAI models, and the rule that `_INSERT` lists values by position (which no longer applies).
- Update `docs/usage-scan-dedup.md`, the plan for a summary table of token sums: its table schema (including 1-hour cache creation sums for each way a `CostPlan` can apply long-context rates), its token and cap text, and how it detects the layout. Instead of comparing table definitions in `sqlite_master`, building the summary table becomes the step from version 1 to 2 in the collector's `open_db`. Its rebuild and write at server startup are dropped in favor of the startup check above, and its tests become "a version-1 DB is rebuilt and set to 2" and "the server refuses a version-1 DB".
- Update `README.md`: its token, API and pricing notes (the wording that excludes cached tokens, the note that OpenAI cached tokens are part of input, the note that Anthropic cache writes are undercounted), the proxy requirement (CLIProxyAPI v7.2.97 or later; v8.0.48 or later for 1-hour cache writes), the backup file, and that the new server refuses an unmigrated DB at startup until the collector has run once (under a supervisor that restarts it, it comes up after the next collector run).

## Done when

- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` pass, with these new or changed tests:
  - Parser: a complete breakdown fills each column; `cache_creation_1h_tokens` is stored when present, 0 when missing, capped at cache creation, and 0 for a breakdown that is not `complete`; `service_tier` is NULL when missing or empty; `ttft_ms` is NULL when missing or 0; a missing breakdown and a wrong `schema_version` raise `SchemaError`; a breakdown that is not `complete` stores 0 in every token column but `total_tokens`, and the collector's summary line counts it.
  - Migration, in its own test file that is deleted with the module and fails once the package version reaches 0.4.0. On a DB created with the old schema holding a Claude row with cache reads and creation in its total, an older Claude row (`total_tokens = input + output`) and a GPT row: the values match the rules in step 2; the backup file exists; a second `open_db` changes nothing; the migrated DB and a new one have the same columns with the same types, `NOT NULL` flags and defaults in `PRAGMA table_info(requests)`; both are at `user_version` 1; and a failed migration leaves it at 0.
  - Schema version, in the regular test files: a new DB is created at version 1; a DB above `SCHEMA_VERSION` makes the collector exit 2 without calling the queue client; the server's startup check exits non-zero with the message for a DB below and above `SCHEMA_VERSION`, and proceeds for a DB at it and for a missing file (with `uvicorn.run` replaced in the test). Every test that calls `run()` sets `USAGE_DB_PATH` to a temp path; the test for two migrations at once patches the version read.
  - Cost: `tests/test_pricing_ccusage.py` keeps its dollar amounts, with inputs restated in the new column meanings (`test_openai_cached_tokens_are_clamped_to_input` is deleted). `request_cost` and `request_totals` in `tests/conftest.py` take input, output, cache read, cache creation and 1-hour cache creation. New cases price cache creation and 1-hour cache creation under each way a `CostPlan` can apply long-context rates (no higher rates, only the tokens above the threshold, the whole request once input passes it), against fixed, hand-checked amounts that ccusage's rules give. Example: input = cache creation = 1-hour cache creation = 100,000 and output = cache read = 0, on a $3/M-input model with no long-context rates, costs $0.60.
- `nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'` passes.
- `nix build` succeeds and `nix eval --raw .#packages.x86_64-linux.default.version` prints `0.3.0`.
- The Playwright suite passes (command in `AGENTS.md`); `e2e/seed_db.py` writes the new columns.
- On a copy of the extracted snapshot outside the repo, migrated by running `open_db` on it, with the server running on the copy:
  - `summary` for `start=2026-09-17T00:00:00Z&end=2026-10-18T00:00:00Z&tz=UTC` has `input_tokens + output_tokens` equal to `SUM(total_tokens)` over the same rows of the original DB (queried with Python's `sqlite3` module) and `cost_by_type.cache_creation > 0`.
  - For `start=2026-05-01T00:00:00Z&end=2026-06-01T00:00:00Z&tz=UTC`, `input_tokens + output_tokens` equals `SUM(total_tokens)` over all rows plus `SUM(cached_tokens)` over Claude rows of the original, and `cache_creation_tokens` is 0.
  - For both windows, `cost_by_type.input`, `.output` and `.cache_read` on the migrated copy match, to 1e-9 relative, what the server at commit `0ebf45f` (before this change) reports on the original. Both servers price from one copy of the snapshot's `pricing.json` kept outside the original directory (`USAGE_PRICING_CACHE` pointing at it, `USAGE_PRICING_TTL_SECONDS` large enough that neither refreshes it).
