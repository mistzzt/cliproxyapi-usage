# Plan: serve usage from an in-process cell cache

## Why

The usage pages are slow because every request re-reads history from SQLite. Each usage endpoint calls `_fetch_cells` (`src/cliproxy_usage_server/aggregate.py`), which runs one grouped query over `requests` for the window and another for the previous period, and `options` scans all of history on every page load. On the 1.5M-row snapshot (`test/cpa-usage.tar.gz`, migrated to schema version 1), an all-time page takes about 13 s, almost all of it in SQL.

History rarely changes: the collector only appends rows. So the server can group history once, keep the result in memory, and on each request read only what it doesn't have yet. A prototype of this design (branch `proto/slot-cache-python`, commits `1ba3d77` and `6408714`) returned exactly the same results as today's code, served an all-time page in about 90 ms, and used about 20 MiB of memory.

The schema stays at version 1, the collector does not change, and the server keeps opening the DB read-only.

## Terms

- **Slot:** a 15-minute UTC interval. Every request belongs to exactly one slot.
- **Cell:** the sums for one (slot, api_key, model, source). Today's `_fetch_cells` already returns cells; the cache stores them.
- **Edge:** a slot only partly inside the requested window, at its start or end. The cache can't answer for part of a slot, so edges are always read from SQLite.
- **Fold:** adding newly inserted rows into the cached cells.
- **Plan shape:** one of the three ways `cost_plan` can bill long-context requests (`pricing.PLAN_SHAPES`: marginal 200k, whole-request 200k, whole-request 272k).

## Design

### 1. A query whose cells don't depend on pricing

Cached cells must stay valid when pricing changes, so the grouped query stops depending on per-model plans. For each cell it sums the plain counts and every priced type's total, plus, for each plan shape, every type's base-rate share and the long-request count. Pricing then picks each model's shape when it prices the cell. This also removes today's plan join and the `SELECT DISTINCT model` that runs on every call. The five priced types stay defined once, in `_PRICED`.

### 2. The cache and how it stays current

One cache per server process holds every cell of the DB, keyed by slot and then by (api_key, model, source). Cells share one copy of each distinct key string.

- **Startup:** the app lifespan builds the cache with one scan in a background thread. Until it is ready, requests use the direct query.
- **New rows:** before each cached read, the cache folds in rows whose `rowid` is above the last one it saw, under one lock. This is exact because the collector only appends.
- **Rewrites:** anything else that changes `requests` makes the cache drop itself and rebuild in the background. It checks for five signs of that:
  1. the DB file was replaced (its device and inode changed);
  2. `PRAGMA schema_version` changed;
  3. `MAX(rowid)` went down;
  4. the row at the cached maximum `rowid` has a different timestamp (a deleted row's `rowid` was reused);
  5. `COUNT(*)` no longer equals the number of rows the cache has seen, evicted rows included. This check runs at most every `USAGE_CELL_CACHE_VERIFY_SECONDS` (default 60).

  In-place `UPDATE`s of existing rows are not detected, since the collector never updates; `AGENTS.md` says so.
- **Visibility:** `app.state.cell_cache` exposes `ready` and its `builds` and `hits` counters, so tests and benchmarks can tell when the cache is in use.

### 3. Answering a request

- **Whole slots** inside the window come from the cache, filtered by raw key.
- **Edges** come from the grouped query, limited to rows up to the cache's maximum `rowid` so nothing is counted twice. Edge queries use the timestamp index whatever the filters, not the model, key or source indexes, which SQLite would otherwise pick for a filtered request.
- **Priced cells** are memoized per slot for the current pricing map, and a slot's memo is dropped when a fold changes that slot.
- **Filter lookups:** `resolve_filter` (the distinct raw values of a column) reads them from the cache after folding in new rows. Today it runs a `SELECT DISTINCT` over `requests` on every filtered request, which is most of the time a filtered page takes.
- **The all-time axis start** keeps its indexed SQL query, because cells don't record the exact earliest timestamp.

Results stay exactly what today's code computes, floats included: cells reach pricing in the same slot-then-key order with the same integer sums.

### 4. Memory cap

`USAGE_CELL_CACHE_MAX_MB` (default 512) bounds the cache's estimated size, which is its cell count times a fixed bytes-per-cell constant covering a cell's sums plus one priced-memo entry. When the cache is over the cap after a build or fold, it evicts its oldest slots until it fits; a build may run over the cap until it finishes.

- The cache remembers the first slot it still holds. Whole slots before it are read with the grouped query, limited to the cache's maximum `rowid` like edges.
- New rows landing in an evicted slot are not folded in, but they still count toward the rows the cache has seen, so check 5 does not trigger rebuilds.
- The group of rows whose timestamp SQLite cannot parse is never evicted, because no timestamp range can reach it.
- While anything is evicted, `resolve_filter` queries `requests` again.
- The first eviction is logged once, naming the setting.

### 5. Off switch

`USAGE_CELL_CACHE=0` disables the cache, and every request uses the direct query.

## Starting point

Merge `proto/slot-cache-python` into this branch and trim it to the design above. Delete `docs/proto-slot-cache-python.md` and `docs/usage-scan-dedup.md`, which this plan replaces; earlier plans that mention them stay as they are.

## Done when

**Tests.** `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` pass, and new tests on synthetic data cover:

- Cached and direct results giving byte-identical JSON for summary, every breakdown dimension, options and export, in these cases: windows with mid-slot edges, each filter, rows inserted between requests, each of the five rebuild checks, a window inside one slot, requests during the build, and a cap small enough to evict (with windows before, across and after the first cached slot, plus options and filters).
- Real per-cell memory, measured (e.g. with `tracemalloc`) with every slot's priced memo filled, on cells with realistic-length keys, models and sources and token sums above 256. The test fails if it exceeds the bytes-per-cell constant.

The frontend checks (`nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'`) and the Playwright suite (command in `AGENTS.md`) also pass.

**Benchmark.** Measure with a scratch benchmark kept outside the repo, and report its path and results table in the handoff. Setup:

- A copy of the migrated snapshot; each configuration runs in its own subprocess through `create_app` and `TestClient`.
- tz `America/Chicago`, with the fixed end `2026-10-17T06:07:30Z`. It falls mid-slot, so every window has edges at both ends.
- A page is summary, person breakdown, model breakdown and options.
- Each run is compared with `USAGE_CELL_CACHE=0` and with commit `2651e77`.

| Check | Target |
| --- | --- |
| JSON for all time and 30 days (summary, both breakdowns, options) | byte-identical to both baselines |
| Sequential page, all time | ≤ 200 ms |
| Sequential page, 30 days, unfiltered, model-filtered and credential-filtered | ≤ 150 ms each |
| Sequential page, 7 hours | ≤ 100 ms |
| Cache ready, with no requests during the build | ≤ 5 s |
| After inserting 10,000 rows through the collector's `insert_records` into the DB copy (cloned from existing rows, timestamps after the snapshot's last row) and moving the page end past them: the next all-time page | ≤ 250 ms; summary `requests` exactly 10,000 higher; JSON byte-identical to `USAGE_CELL_CACHE=0` |
| RSS after the cache is ready | ≤ 60 MiB above the import-only RSS |

**Docs.** `AGENTS.md` and `README.md` describe the cache, its settings (`USAGE_CELL_CACHE`, `USAGE_CELL_CACHE_MAX_MB`, `USAGE_CELL_CACHE_VERIFY_SECONDS`), eviction and the startup warm-up.
