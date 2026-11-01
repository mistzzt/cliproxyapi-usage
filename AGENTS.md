# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. (`CLAUDE.md` is a symlink to `AGENTS.md`; edit `AGENTS.md`.)

## Commands

Dependency manager: `uv` (Python >=3.12; `.python-version` pins 3.14). Frontend uses `bun`. A Nix flake provides a dev shell with both.

- Install deps: `uv sync`
- Run the collector: `uv run cliproxy-usage-collect` (requires `CLIPROXY_MANAGEMENT_KEY`; see README for all env vars). `.envrc` in the repo root (gitignored) exports a working `CLIPROXY_BASE_URL` + key for the upstream dev proxy.
- Run the webapp: `uv run cliproxy-usage-server` (serves `http://127.0.0.1:8318/`).
- Frontend commands need `bun`; when it is not on PATH, run them inside the flake dev shell (Python 3.14, uv, bun): `nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'`.
- Frontend dev: `cd frontend && bun install && bun run dev` (Vite on `:5173` proxies `/api` to `:8318`).
- Frontend build: `cd frontend && bun run build` (outputs into `frontend/dist/`).
- Run backend tests: `uv run pytest`
- Single backend test: `uv run pytest tests/test_queue_parser.py::test_name -q`
- Frontend tests: `cd frontend && bun test` (bun:test suites colocated with the pure logic modules they cover)
- Lint: `uv run ruff check` / `uv run ruff format`; frontend `cd frontend && bun run lint`
- Type-check: `uv run basedpyright` (config in `pyproject.toml`: `include = ["src", "tests"]`, mode `standard`); frontend type errors surface via `bun run build` (`tsc -b`)
- Nix package: `nix build` (builds the frontend into the Python package). Frontend dependency changes must update the x86_64-linux `outputHash` in `flake.nix`.

`state/` (gitignored) holds a local `usage.db` and `pricing.json` for development.

## Architecture

One-shot CLI collector: drains a bounded batch from CLIProxyAPI's HTTP management usage queue and upserts rows into a local SQLite DB. Safe to re-run: dedup is by `timestamp` primary key via `INSERT OR IGNORE`. Designed to be invoked from cron at an interval shorter than the upstream queue retention window; transient failures exit non-zero so cron retries on the next tick.

Data flow (`src/cliproxy_usage_collect/`):

1. `config.py`: `load_config()` reads env (`CLIPROXY_BASE_URL`, `CLIPROXY_MANAGEMENT_KEY`, `USAGE_DB_PATH`, `USAGE_QUEUE_POP_COUNT`, `USAGE_HTTP_TIMEOUT_SECONDS`) via pydantic-settings. `CLIPROXY_BASE_URL` may be either the CLIProxyAPI origin or the management API base URL; default `http://localhost:8317`. Raises `ConfigError` with env-var names (not field names) in the message.
2. `queue_client.py`: `pop_usage_records(cfg)` uses httpx against the management `usage-queue` endpoint and returns raw JSON queue elements. It authenticates with `CLIPROXY_MANAGEMENT_KEY` as a bearer token, passes the configured count, and maps auth/permission failures to `AuthError`; connection/timeout/protocol/unexpected-response failures to `TransientError`.
3. `parser.py`: `iter_records(queue_elements)` validates each queue JSON element with private strict pydantic models, then yields `RequestRecord`s. Raises `SchemaError` (lives here, not in `schemas.py`) on validation failure.
4. `db.py`: `open_db(path)` creates the `requests` table + indexes if missing. `insert_records` bulk-inserts with `INSERT OR IGNORE` and returns the count of genuinely new rows (via `conn.total_changes` delta).
5. `cli.py`: `main()` wires the above. Exit codes: **0** ok, **1** transient, **2** config, **3** auth, **4** schema. Keep these stable, cron/alerts depend on them.

`schemas.py` holds the _shared_ user-facing shape (`RequestRecord`, frozen). Ingestion-only pydantic models that mirror the proxy's queue JSON stay private to `parser.py`. The server/dashboard consume the SQLite rows generated from `RequestRecord`, so don't move it back into `parser.py` or the DB module.

### Webapp (`src/cliproxy_usage_server/` + `frontend/`)

FastAPI + Vite/React dashboard that reads `usage.db` **read-only** and serves usage/quota JSON endpoints plus the built SPA at `/` (client routes: usage page at `/usage`, limits at `/limits`; `/` and unknown paths redirect to `/usage`). Single-process deployment via `uv run cliproxy-usage-server`. Env vars are documented in README.

- `config.py`: `ServerConfig` via pydantic-settings; same `AliasChoices` + `_env_name` error-translation pattern as the collector. `load_key_aliases` reads `USAGE_KEY_ALIASES` (path to a JSON object, redacted key to display name); unset or absent file gives `{}`, malformed content raises `ConfigError`.
- Usage endpoints (`routes/usage.py`, under `/api/usage`): `summary` (KPI totals, previous period, dense `cost`/`input_tokens`/`output_tokens`/`cached_tokens`/`requests` series), `breakdown?dim=person|model|credential` (one row per entity with current/previous totals, `cost`/`input_tokens`/`output_tokens`/`cached_tokens` trend aligned to `buckets`, paired-dimension `mix`: person to model, model and credential to person), `matrix?rows=&cols=` (sparse cross tab, default person x model, no time axis, `rows == cols` is 422), `options` (no params; every entity per dimension with `label` and color `slot`).
- Range params (all but `options`): `start` (optional; absent means all time, and then `previous` is `None`), `end` (default now), `tz` (required IANA name), comma-separated `people` (redacted keys), `models`, `credentials` (redacted sources); `summary`/`breakdown` also take `bucket=auto|hour|day|week`. Naive datetimes, `start > end`, and unknown `tz` are 422s. The previous period is the equal-length window ending at `start`. The effective bucket is echoed back.
- `db.py`: `open_ro(path)` via `file:...?mode=ro` URI; viewer-local bucketing in the IANA `tz` (DST aware): day buckets run local midnight to midnight, week buckets start Monday 00:00 local. `auto_bucket` picks hour up to 2 days, day up to 120 days, week beyond; `effective_bucket` coarsens an explicit bucket to the next unit past `MAX_BUCKETS` (1,000). `bucket_starts` builds the dense axis; `SliceIndexer` maps 15-minute UTC slices onto it.
- `aggregate.py`: every endpoint aggregates one grouped query, `_fetch_cells`: rows per (15-minute UTC slice, api_key, model, source). Tiers apply per request, never to sums: the query splits each request's tokens into base-rate and above-rate amounts using its model's `CostPlan` and sums those, so a cell's cost equals the sum of per-request `compute_cost`. Totals, rows, trends, and matrix cells are sums of the same cells, so they always agree and cost does not depend on the bucket. Helpers: `query_summary`, `query_breakdown`, `query_matrix`, `query_options`. All-time axes start at the first recorded request. Rows, mixes, and matrix axes sort by cost desc, then tokens desc.
- Tokens everywhere mean input + output (cached excluded); the stored `total_tokens` column is never summed. The API returns only the raw components (`input_tokens`, `output_tokens`, `cached_tokens` in totals, series, trends, mix entries, and matrix cells); the frontend derives tokens with `tokens`/`tokenSeries` in `src/utils/usage/totals.ts`, the single definition. Latency is average only.
- People are API keys redacted with `redact_key` (raw keys that redact alike merge into one person); `label` is the `USAGE_KEY_ALIASES` name or the redacted key as-is. Unmatched alias entries are logged once, the first time `options` runs. Models and credentials use their key as label. Credentials are sources redacted with `redact_source` (email sources pass through). `resolve_redacted_api_keys` / `resolve_redacted_sources` map redacted filter values back to raw values.
- Color slots: `options` ranks each dimension by all-time spend; the top 7 (`SLOT_COUNT`) get slots 0..6, the rest `None` ("Other"), so colors never change with filters.
- `pricing.py`: hand-ported subset of ccusage's LiteLLM pricing and per-request cost (`ModelPricing`, `resolve`, `cost_plan`, `compute_cost`, disk cache with TTL). `*_above_200k_tokens` rates are marginal per token type; rates only published above 272k, and every OpenAI-accounting model (ccusage's Codex adapter), bill the whole request at the long rate once its context passes the threshold. Entries without base input and output rates are dropped; missing cache rates fall back as in ccusage. `tests/test_pricing_ccusage.py` pins these semantics. Cache lives at `<db_parent>/pricing.json` by default. Missing pricing is signalled by `cost_status` (`live`/`partial_missing`/`missing`); there is no pricing endpoint. Model breakdown rows carry `price` (LiteLLM list price per 1M tokens: input, output, cache read/write, above-200k input/output) from the same `resolve` used for cost; `None` when unpriced or not a model row.
- `main.py`: `create_app(config, *, pricing_provider=None, quota_service_factory=None)` + `run()`. Lifespan loads pricing on startup and schedules a background TTL refresh. SPA static mount only happens if `frontend/dist/` exists (so backend-only test runs don't need built assets); unknown `/api/*` paths 404 instead of returning the SPA shell. `/usage-config.js` injects base path, API base, and page title at runtime.
- `quota/`: quota API client, provider parsers, TTL cache, and service layer for Claude/Codex OAuth quota lookups through CLIProxyAPI's management API.
- `routes/usage.py`, `routes/quota.py`: each exposes a `build_router(...)` factory included in `main.py` under `/api`.

Shared-schema rule: response DTOs live in `cliproxy_usage_server/schemas.py`. The frontend types in `frontend/src/types/api.ts` are **hand-maintained** to match those DTOs; keep them in sync when backend schemas change.

### Frontend (`frontend/`)

Bun + Vite + React 19 + TypeScript (strict + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` + `verbatimModuleSyntax`) + Chart.js (registered in `src/components/charts/chart-setup.ts`) + SCSS modules + Zustand (theme and quota stores) + react-router v7 + Radix popover + react-day-picker/date-fns for the range calendar. Path alias `@/*` maps to `./src/*`.

- Pages: `src/pages/UsagePage.tsx` (route `/`) and `src/pages/QuotaPage.tsx` (route `/limits`), rendered under a layout route in `src/router.tsx` that draws the shared top bar (`src/components/ui/AppHeader.tsx`, which owns its own inset); pages pad only their own content. API clients in `src/services/` (`api.ts`: `getSummary`, `getBreakdown`, `getMatrix`, `getOptions`; `quotaApi.ts`).
- Usage page, top to bottom: sticky `FilterBar` (range presets or custom date+time, people/models/credentials multi-selects, granularity, metric cost/tokens), `KpiStrip` (spend, tokens, requests, failure rate, avg latency, change vs previous period), "People by {metric}" (`ShareBar` + `RichRows`), `Heatmap` (people x models matrix), Models/Credentials tabs (`ShareBar` + `RichRows`), and `DetailDrawer` (opened from any row, segment, or heatmap row; calls `summary`/`breakdown` with the entity added to the filters; "Filter page to this" applies it). Components live in `src/components/usage/`; `TimeChart.tsx` is the Chart.js stacked bar chart.
- `RichRows` is generic over dimension: share, spend, tokens, change, trend strip with shared hover bucket, mix bar, failure rate; models add cache hit rate, list price, latency; credentials add last used.
- Pure logic lives in `src/utils/usage/*.ts` with colocated `*.test.ts` (bun:test): `range` (presets, custom windows, `resolveRange` to API params with `viewerTimeZone()`), `viewState` (URL parse/serialize, `usageQueryParams`, `scopeToEntity`), `format`, `dates`, `modelNames` (short names, families), `palette` (7 light + 7 dark slots plus Other gray), `entities` (label/slot lookup from `options`), `share` (Other folding), `stack`, `richRows` (row derivation, mix sorting, sort state), `heatmap` (column selection, bins), `price`, `totals` (failure and cache hit rates, `tokens`/`tokenSeries`). Keep new chart/state logic in this testable pure-module style rather than inside components.
- View state (range, filters, granularity, metric, tab, open drawer) lives in the URL query string via `src/hooks/useUsageViewState.ts`; a load with no query string restores localStorage key `usage.view.v1`. The browser's IANA timezone is sent as `tz` on every range request.
- Every place the UI names an entity uses the server `label` (model ids shortened by `modelNames` for display); colors come from `options` slots, never from filtered ranks.

Authentication is delegated to an upstream reverse proxy; the app itself exposes `/api/*` without access control.

## Tests

- `tests/`: pytest suite; JSON fixtures live in `tests/fixtures/`. `conftest.py` exposes queue-record fixtures for the collector, `seeded_db_path` (one key, 40 minutes of data), and `synthetic_db_path` (multi-person, multi-model, spans the 2026-11-01 DST change in `America/Chicago`) for server tests; `write_usage_db` builds custom DBs.
- `test_queue_client.py` uses `httpx.MockTransport`; prefer that over a real CLIProxyAPI process.
- Frontend tests are bun:test, colocated with the modules they test.

## Conventions

- Ruff target is `py314`; basedpyright `pythonVersion = "3.14"`. Code must stay compatible with that even though `requires-python` is `>=3.12`.
- No migration / backward-compat shims unless explicitly asked (this is a greenfield project).
- The SQLite schema in the collector's `db.py` is authoritative; if you change column order, update `_INSERT`'s positional `VALUES (?,...)` in lockstep.
