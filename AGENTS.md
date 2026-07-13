# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository. (`CLAUDE.md` is a symlink to `AGENTS.md`; edit `AGENTS.md`.)

## Commands

Dependency manager: `uv` (Python >=3.12; `.python-version` pins 3.14). Frontend uses `bun`. A Nix flake provides a dev shell with both.

- Install deps: `uv sync`
- Run the collector: `uv run cliproxy-usage-collect` (requires `CLIPROXY_MANAGEMENT_KEY`; see README for all env vars). `.envrc` in the repo root (gitignored) exports a working `CLIPROXY_BASE_URL` + key for the upstream dev proxy.
- Run the webapp: `uv run cliproxy-usage-server` (serves `http://127.0.0.1:8318/`).
- Frontend dev: `cd frontend && bun install && bun run dev` (Vite on `:5173` proxies `/api` to `:8318`).
- Frontend build: `cd frontend && bun run build` (outputs into `frontend/dist/`).
- Run backend tests: `uv run pytest`
- Single backend test: `uv run pytest tests/test_queue_parser.py::test_name -q`
- Frontend tests: `cd frontend && bun test` (bun:test suites colocated with the pure logic modules they cover)
- Lint: `uv run ruff check` / `uv run ruff format`; frontend `cd frontend && bun run lint`
- Type-check: `uv run basedpyright` (config in `pyproject.toml`: `include = ["src", "tests"]`, mode `standard`); frontend type errors surface via `bun run build` (`tsc -b`)

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

FastAPI + Vite/React dashboard that reads `usage.db` **read-only** and serves usage/pricing/quota JSON endpoints plus the built SPA at `/`. Single-process deployment via `uv run cliproxy-usage-server`. Env vars are documented in README.

- `config.py`: `ServerConfig` via pydantic-settings; same `AliasChoices` + `_env_name` error-translation pattern as the collector.
- `db.py`: `open_ro(path)` via `file:...?mode=ro` URI; bucket helpers `bucket_for_span` (span <= 48h gets hour buckets, else day) and `coarsen_bucket` (auto-coarsens hour to day beyond ~10 days to cap bucket counts); `tz_sql_modifier` builds the SQLite strftime timezone modifier.
- Time windows: range-consuming endpoints take an explicit `(start, end)` pair of ISO-8601 instants plus `tz_offset_minutes` (viewer-local bucketing), not a rolling-range enum. `start` absent means all time; naive datetimes and `start > end` are 422s. The effective bucket is echoed back in responses.
- `aggregate.py`: one query helper per endpoint (`query_totals`, `query_timeseries`, `query_token_breakdown`, `query_api_stats`, `query_model_stats`, `query_credential_stats`, `query_health`, ...); buckets are **dense** (missing intervals zero-filled in Python, generated off the range window).
- `pricing.py`: hand-ported subset of ccusage's litellm pricing (`ModelPricing`, `resolve`, tiered-200k `compute_cost`, disk cache with TTL). Cache lives at `<db_parent>/pricing.json` by default. Cost series are computed by applying `compute_cost` to per-bucket-per-model token splits (see `routes/usage.py` module docstring).
- `redact.py`: API keys and credential sources are redacted before leaving the server; `resolve_redacted_api_keys` in `aggregate.py` maps redacted filter values back to real keys.
- `main.py`: `create_app(config, *, pricing_provider=None)` + `run()`. Lifespan loads pricing on startup and schedules a background TTL refresh. SPA static mount only happens if `frontend/dist/` exists (so backend-only test runs don't need built assets). Routers live in `routes/`.
- `quota/`: quota API client, provider parsers, TTL cache, and service layer for Claude/Codex OAuth quota lookups through CLIProxyAPI's management API.
- `routes/usage.py`, `routes/pricing.py`, `routes/quota.py`: each exposes `build_router(...)` factories included in `main.py` under `/api`.

Shared-schema rule: response DTOs live in `cliproxy_usage_server/schemas.py`. The frontend types in `frontend/src/types/api.ts` are **hand-maintained** to match those DTOs; keep them in sync when backend schemas change.

### Frontend (`frontend/`)

Bun + Vite + React 19 + TypeScript (strict + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` + `verbatimModuleSyntax`) + Chart.js (with chartjs-plugin-zoom) + SCSS modules + Zustand + react-router v7 + react-day-picker/date-fns for the range calendar. Path alias `@/*` maps to `./src/*`.

- Pages: `src/pages/UsagePage.tsx` (orchestration) and `src/pages/QuotaPage.tsx`; API clients in `src/services/`.
- Components under `src/components/{ui,usage,quota,charts}/`.
- The unified usage explorer (`src/components/usage/explorer/`) replaced the old per-metric charts. Its state machine is a **pure module** (`explorerState.ts`): metric (requests/tokens/cost) x breakdown (total/model/token_type) x granularity x display (line/stacked), with `normalizeExplorerState` snapping invalid combinations to valid ones. Companion pure modules `otherSeries.ts` and `requestSelection.ts` handle top-N "other" bucketing and series selection.
- Pure logic lives in plain `.ts` modules with colocated `*.test.ts` files run by `bun test` (also `src/utils/rangeResolver.ts`). Keep new chart/state logic in this testable pure-module style rather than inside components.
- Persistent UI state uses localStorage keys following the `usage.*.v1` convention.

Authentication is delegated to an upstream reverse proxy; the app itself exposes `/api/*` without access control.

## Tests

- `tests/`: pytest suite; JSON fixtures live in `tests/fixtures/`. `conftest.py` exposes queue-record fixtures for the collector and a `seeded_db_path` fixture for server/dashboard DB seeding.
- `test_queue_client.py` uses `httpx.MockTransport`; prefer that over a real CLIProxyAPI process.
- Frontend tests are bun:test, colocated with the modules they test.

## Conventions

- Ruff target is `py314`; basedpyright `pythonVersion = "3.14"`. Code must stay compatible with that even though `requires-python` is `>=3.12`.
- No migration / backward-compat shims unless explicitly asked (this is a greenfield project).
- The SQLite schema in the collector's `db.py` is authoritative; if you change column order, update `_INSERT`'s positional `VALUES (?,...)` in lockstep.
