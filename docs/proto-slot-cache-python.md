# Prototype: in-process slot cache for the usage API

Every usage endpoint aggregates cells per (15-minute UTC slot, api_key, model, source). This prototype makes those cell sums independent of pricing and keeps them in memory, so a request reads whole slots from the cache and queries `requests` only for the partial slots at the window edges.

## Plan-independent sums

`cell_cache.scan` is the only grouped query. Per cell it sums request counts, failures, input, reasoning and successful latency, every priced token type's total, and, for each shape in `pricing.PLAN_SHAPES` (marginal 200k, whole-request 200k, whole-request 272k), every type's base-rate share and the count of long requests. `aggregate._Pricer` picks each model's shape from its `CostPlan` and prices the sums exactly as before. The per-call `SELECT DISTINCT model` and the plan CTE join are gone; the direct query computes 18 extra integer sums instead.

## Cache

`cell_cache.CellCache` holds `slot -> {(api_key, model, source) -> sums}` plus a sorted slot list. One `threading.Lock` guards it.

- Startup: the app lifespan starts a background build thread (one scan in a read transaction). Until it is installed, requests use the direct query.
- Sync: each cached read first folds in rows with `rowid` above the last seen maximum, under the lock, in one read transaction. The collector only appends, so this is exact.
- Rebuild: the cache is dropped and rebuilt in the background when the DB file's inode changes, `PRAGMA schema_version` changes, `MAX(rowid)` goes down, the row at the cached max rowid has a different timestamp (a reused rowid), or, at most every `verify_seconds`, `COUNT(*)` differs from the cached request count.
- Reads: `[lo, hi)` whole slots come from the cache, filtered by raw key in Python; head and tail edges run `scan` bounded by the cached max rowid. A window inside fewer than one whole slot uses the direct query. Priced cells are memoized per slot for the current pricing map object and dropped when a fold touches that slot.

Results are identical to the direct query, floats included: cells arrive in the same slot-then-key order with the same integer sums, so every accumulation runs in the same order. The new direct query was also checked byte for byte against the previous commit on a randomized 3,000-row DB (all windows and filters in `tests/test_cell_cache.py`).

## Knobs

- `USAGE_CELL_CACHE` (default `true`): set `0` or `false` to disable the cache; every request then runs the direct query, which still uses the plan-independent sums.
- `USAGE_CELL_CACHE_VERIFY_SECONDS` (default `60`): how often a read also compares `COUNT(*)` to detect deletions below the max rowid.

## Known gaps

- In-place `UPDATE`s of existing rows are not detected (the collector never updates).
- Memory grows with the number of distinct cells, and the priced memo roughly doubles it.
- The lock serializes cached reads, including the periodic `COUNT(*)`.
