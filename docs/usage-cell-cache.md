# Plan: keep grouped usage in memory

## Why

Usage pages are slow because every request re-reads history from SQLite. Each usage endpoint calls `_fetch_cells` (`src/cliproxy_usage_server/aggregate.py`), which groups `requests` by 15-minute UTC interval, API key, model and source for the window and again for the previous period, and `options` scans all of history on every page load.

The collector only appends rows, so the server can group all of history once, keep those sums in memory, and on each request add only the rows inserted since. The schema stays at version 1, the collector does not change, and the server keeps opening the DB read-only.

Start from branch `proto/slot-cache-python` (commits `1ba3d77` and `6408714`), which does this and returned exactly today's results. Merge it into this branch and trim it to the design below. Delete `docs/proto-slot-cache-python.md` and `docs/usage-scan-dedup.md`, which this plan replaces; earlier plans that mention them stay as they are.

## Design

1. **Grouped sums that don't depend on pricing.** The grouped query sums the plain counts and every priced type's total, and, for each of the three long-context billing rules `cost_plan` can produce (`pricing.PLAN_SHAPES`), every type's base-rate share and the long-request count. Pricing picks each model's rule afterwards, so cached sums stay valid when pricing changes, and the query loses its plan join and per-call `SELECT DISTINCT model`. The five priced types stay defined once, in `_PRICED`.

2. **Building and updating the cache.** Each server process keeps the grouped sums for all of history in memory, one entry per (15-minute UTC interval, API key, model, source), indexed by interval first and sharing one copy of each distinct key string. The app lifespan builds it with one scan in a background thread, and requests use the direct query until it is ready. Before each cached read, the server adds rows whose `rowid` is above the last one it saw, under one lock. It throws the cache away and rebuilds it in the background when any of these happen:
   - the DB file is replaced (its device and inode change);
   - `PRAGMA schema_version` changes;
   - `MAX(rowid)` goes down;
   - the row at the last `rowid` it saw has a different timestamp (a deleted row's `rowid` was reused);
   - `COUNT(*)`, checked at most every `USAGE_CELL_CACHE_VERIFY_SECONDS` (default 60), differs from the number of rows the cache has seen, evicted ones included.

   Updates to existing rows go undetected, since the collector never updates; `AGENTS.md` says so. `app.state.cell_cache` exposes `ready` and its `builds` and `hits` counters for tests and benchmarks.

3. **Answering a request.** The 15-minute intervals fully inside the window come from the cache, filtered by raw column values. The partial intervals at the window's start and end come from the grouped query, limited to rows up to the last `rowid` the cache saw; these queries use the timestamp index whatever the filters (SQLite would otherwise pick the model, key or source index). Priced results are memoized per interval for the current pricing map and dropped when new rows change that interval. `resolve_filter` reads distinct values from the cache, after adding new rows, instead of a `SELECT DISTINCT` over `requests` on every filtered request. The all-time start date keeps its indexed SQL query, since the cache does not keep the exact earliest timestamp. Results stay exactly what today's code computes, floats included: sums reach pricing in interval-then-(API key, model, source) order with the same integers.

4. **Memory cap.** `USAGE_CELL_CACHE_MAX_MB` (default 512) bounds the cache's estimated size: the number of entries times a fixed bytes-per-entry constant covering the sums plus one memoized priced result. When a build or update leaves the cache over the cap, it evicts its oldest intervals until it fits, and is allowed to exceed the cap only while a build is running. Intervals older than the oldest one kept are read with the grouped query like the partial ones; new rows landing in them are not added but still count toward the rows seen. Rows whose timestamp SQLite cannot parse are never evicted. While anything is evicted, `resolve_filter` queries `requests` again. The first eviction is logged once, naming the setting.

5. **Off switch.** `USAGE_CELL_CACHE=0` disables the cache, and every request uses the direct query.

## Done when

- `uv run pytest`, `uv run ruff check`, `uv run ruff format --check` and `uv run basedpyright` pass. New tests on synthetic data check:
  - cached and direct results give byte-identical JSON for summary, every breakdown dimension, options and export, for windows starting and ending mid-interval, each filter, rows inserted between requests, each rebuild condition, a window inside one interval, requests during the build, and a cap small enough to evict (windows before, across and after the oldest kept interval, plus options and filters);
  - memory per entry, measured (e.g. with `tracemalloc`) with every memoized priced result filled, on realistic-length keys, models and sources and token sums above 256, stays within the bytes-per-entry constant.
- The frontend checks (`nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'`) and the Playwright suite (command in `AGENTS.md`) pass.
- A scratch benchmark outside the repo (the handoff reports its path and results) runs each configuration in its own subprocess through `create_app` and `TestClient` on a copy of the `test/cpa-usage.tar.gz` snapshot migrated to schema version 1, with tz `America/Chicago` and the end fixed at `2026-10-17T06:07:30Z` so every window starts and ends mid-interval. A page is summary, person breakdown, model breakdown and options. Compared with `USAGE_CELL_CACHE=0` and with commit `2651e77`, it shows:

  | Check | Target |
  | --- | --- |
  | JSON for all time and 30 days (summary, both breakdowns, options) | byte-identical to both |
  | Sequential page, all time | ≤ 200 ms |
  | Sequential page, 30 days: unfiltered, model-filtered, credential-filtered | ≤ 150 ms each |
  | Sequential page, 7 hours | ≤ 100 ms |
  | Cache ready, no requests during the build | ≤ 5 s |
  | Next all-time page after inserting 10,000 rows through `insert_records` (cloned from existing rows, timestamps after the snapshot's last row, page end moved past them) | ≤ 250 ms; `requests` exactly 10,000 higher; JSON byte-identical to `USAGE_CELL_CACHE=0` |
  | RSS after the cache is ready | ≤ 60 MiB above the import-only RSS |

- `AGENTS.md` and `README.md` describe the cache, its settings (`USAGE_CELL_CACHE`, `USAGE_CELL_CACHE_MAX_MB`, `USAGE_CELL_CACHE_VERIFY_SECONDS`), eviction and the startup warm-up.
