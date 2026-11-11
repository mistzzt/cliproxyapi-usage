# Changelog

## 0.3.0

Token columns now have fixed meanings that no longer depend on how CLIProxyAPI reports usage, and Claude cache writes are stored and priced.

### Breaking changes

- The `requests` table changes: `cached_tokens` is renamed to `cache_read_tokens`, a `cache_creation_tokens` column is added, and `input_tokens` now holds all input (uncached + cache read + cache creation) for every model. `output_tokens` includes reasoning, and `total_tokens` is upstream's reported total, which nothing reads.
- The collector requires CLIProxyAPI v7.2.97 or later. It reads token counts only from the queue payload's `token_breakdown` and exits 4 (schema error) on payloads without one or with a `schema_version` other than 2.
- API: totals carry `input_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `output_tokens` and `reasoning_tokens`; series, trends and mix entries rename `cached_tokens` to `cache_read_tokens`; `cost_by_type` gains `cache_creation`; the CSV export carries all five token columns. `cached_tokens`, `uncached_input_tokens` and `ModelPrice.cached_in_input` are removed.

### Changes

- Requests whose `token_breakdown` quality is not `complete` are stored with only `total_tokens` set, and the collector's summary line reports how many each batch had.
- Cost splits every request into uncached input, cache read, cache creation and output. Cache creation is billed at the cache-write rate under the same long-context rules; whole-request plans compare all input with the threshold. The cost query no longer clamps cache reads to input; instead the migration caps them at input once, for non-Claude rows.
- "Tokens" in the dashboard means all input plus output. Cache hit rate is cache reads over input everywhere. The detail drawer shows Input (share from cache reads), Output (reasoning share) and Cache writes (share of input), and spend by token type adds a cache write part.
- The DB records its layout version in SQLite's `PRAGMA user_version`; 0.3.0 writes version 1. The server checks it once at startup and exits non-zero when it differs, saying whether to run the collector or upgrade the server. A collector exits 2 (config error), before popping the queue, on a DB written by a newer version, and exits 1 on any other SQLite error while opening the DB.
- The Nix package reads its version from `pyproject.toml`.

### Upgrading

1. Upgrade CLIProxyAPI to v7.2.97 or later.
2. Run the 0.3.0 collector once before starting the 0.3.0 server, which refuses to start on an unmigrated DB (under a restarting supervisor it comes up after the next collector run). The collector's first run migrates the DB: it writes a consistent copy of the old DB to `<db>.pre-cache-columns` (replacing an earlier file of that name), then rewrites the table in one transaction. Claude rows gain their cache reads and cache creation in input; other rows keep their input and get cache reads capped at it. If the migration fails, the DB is unchanged and the collector exits 1 before popping the queue, so cron retries. A first run killed in the middle of the migration can leave temporary `<db>.pre-cache-columns*.tmp` files next to the DB; they are safe to delete. Keep the backup until you are satisfied, then delete it.
3. The migration ships only in 0.3.x. Run a 0.3.x collector at least once before moving to 0.4 or later.
4. Do not point an older collector or server at a DB that a newer version has written or upgraded. A 0.3.0+ collector exits 2 on such a DB and a 0.3.0+ server refuses to start; 0.2.x has no check.

Known limits of migrated rows: Claude rows that wrote to the cache without reading it stay counted as cache reads, and Claude rows recorded before CLIProxyAPI v7.1.26 have no cache creation (their total did not include it).
