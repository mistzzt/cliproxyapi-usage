# Changelog

## 0.3.0

Token counts now mean the same thing for every model, and cache writes (including 1-hour cache writes) are stored and priced.

### Breaking changes

- The `requests` table changes. `cached_tokens` is renamed to `cache_read_tokens`, and the columns `cache_creation_tokens`, `cache_creation_1h_tokens`, `service_tier` and `ttft_ms` are added. `input_tokens` now holds all input (uncached input, cache reads and cache writes) for every model, and `output_tokens` includes reasoning.
- The collector requires CLIProxyAPI v7.2.97 or later. It reads token counts only from the queue record's `token_breakdown` and exits 4 when a record has none or its `schema_version` is not 2.
- API: totals carry `input_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `output_tokens` and `reasoning_tokens`. Series, trends and mix entries rename `cached_tokens` to `cache_read_tokens`. `cost_by_type` gains `cache_creation`. The CSV export has all five token columns plus `cache_creation_1h_tokens`. `cached_tokens`, `uncached_input_tokens` and `ModelPrice.cached_in_input` are removed.

### Changes

- Cost splits every request into uncached input, cache reads, cache writes and output. A 1-hour cache write costs twice the input rate, as in ccusage. Models whose long-context rates apply to the whole request compare all input, cache reads and writes included, with the threshold.
- The collector also stores 1-hour cache writes (CLIProxyAPI v8.0.48+), the billed service tier and the time to first token. The tier and time to first token are not shown yet.
- When CLIProxyAPI cannot split a request's tokens, the request is stored with no tokens, and the collector's log line counts such requests.
- "Tokens" on the dashboard means all input plus output, and the cache hit rate is cache reads over input. The detail panel shows input with its cache read share, output with its reasoning share, and cache writes with their share of input; spend by token type adds cache writes.
- The database records its layout version. The server checks it at startup and refuses to start on a database of another version, saying whether to run the collector or upgrade the server. The collector exits 2 on a database from a newer version and 1 on any other database error, both before touching the queue.
- The Nix package reads its version from `pyproject.toml`.

### Upgrading

1. Upgrade CLIProxyAPI to v7.2.97 or later, or v8.0.48 or later to record 1-hour cache writes.
2. Run the 0.3.0 collector once. Its first run migrates the database in place. It first writes a copy of the old database to `<db>.pre-cache-columns.tmp`, syncs it to disk and renames it to `<db>.pre-cache-columns`; if that backup already exists from an earlier attempt, it is kept. If the migration fails, the database is unchanged and the collector exits 1, so cron retries. Delete the backup once you are satisfied with the result.
3. Then start the 0.3.0 server. It refuses to start until the collector has migrated the database; under a supervisor that restarts it, it comes up after the next collector run.
4. Only 0.3.x can migrate a 0.2.x database. Run a 0.3.x collector at least once before moving to 0.4 or later.
5. Do not go back to an older version after upgrading. From 0.3.0 on, a collector exits 2 and a server refuses to start on a database written by a newer version; 0.2.x does not check.

Known limits of upgraded history:

- Before 0.3.0, cache reads and writes were stored as one number. The migration recovers Claude cache writes from each row's reported total, but Claude rows that wrote to the cache without reading it stay counted as cache reads. Other models already counted cache reads in input; the migration lowers any cache read count above a row's input to that input.
- Claude rows recorded before CLIProxyAPI v7.1.26 have no cache writes (their total did not include them), so their cost is a lower bound.
- Upgraded rows, and rows recorded before CLIProxyAPI v8.0.48, have no 1-hour cache writes, so every write is priced at the 5-minute rate.
- Upgraded rows have no service tier or time to first token.
