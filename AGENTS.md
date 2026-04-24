# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Dependency manager: `uv` (Python >=3.12; `.python-version` pins 3.14). Frontend uses `bun`.

- Install deps: `uv sync`
- Run the collector: `uv run cliproxy-usage-collect` (requires `CLIPROXY_MANAGEMENT_KEY`; see README for all env vars). `.envrc` in the repo root exports a working `CLIPROXY_BASE_URL` + key for the upstream dev proxy.
- Run the webapp: `uv run cliproxy-usage-server` (opens `http://127.0.0.1:8318/`).
- Frontend dev: `cd frontend && bun install && bun run dev` (Vite on `:5173` proxies `/api` to `:8318`).
- Frontend build: `cd frontend && bun run build` → outputs into `frontend/dist/`.
- Run tests: `uv run pytest`
- Single test: `uv run pytest tests/test_parser.py::test_name -q`
- Lint: `uv run ruff check` / `uv run ruff format`
- Type-check: `uv run basedpyright` (config in `pyproject.toml`: `include = ["src", "tests"]`, mode `standard`)

## Architecture

One-shot CLI collector: fetches `/usage/export` from a CLIProxyAPI management endpoint and upserts rows into a local SQLite DB. Safe to re-run — dedup is by `timestamp` primary key via `INSERT OR IGNORE`. Designed to be invoked from cron; transient failures exit non-zero so cron retries on the next tick.

Data flow (`src/cliproxy_usage/`):

1. `config.py` — `load_config()` reads env (`CLIPROXY_BASE_URL`, `CLIPROXY_MANAGEMENT_KEY`, `USAGE_DB_PATH`) via pydantic-settings. Raises `ConfigError` with env-var names (not field names) in the message; `_FIELD_TO_ENV` does this translation.
2. `client.py` — `fetch_export(cfg, client=...)` GETs the export. Accepts an injectable `httpx.Client` so tests can pass `httpx.MockTransport`; production path builds a client with a 10s timeout. Maps 401/403 → `AuthError`, 4xx/5xx/network/timeout/invalid-JSON → `TransientError`.
3. `parser.py` — `iter_records(export)` validates the nested proxy JSON (`usage.apis[api_key].models[model].details[]`) with private pydantic models (`_Export`, `_Usage`, `_ApiEntry`, `_ModelEntry`, `_Detail`, `_Tokens`), then yields flat `RequestRecord`s with `api_key` / `model` hoisted out of the dict keys. Raises `SchemaError` (lives here, not in `schemas.py`) on validation failure.
4. `db.py` — `open_db(path)` creates the `requests` table + indexes if missing. `insert_records` bulk-inserts with `INSERT OR IGNORE` and returns the count of genuinely new rows (via `conn.total_changes` delta).
5. `cli.py` — `main()` wires the above. Exit codes: **0** ok, **1** transient, **2** config, **3** auth, **4** schema. Keep these stable — cron/alerts depend on them.

`schemas.py` holds the _shared_ user-facing shape (`RequestRecord`, frozen). Ingestion-only pydantic models that mirror the proxy's export JSON stay private to `parser.py`. A future webapp is expected to consume `RequestRecord` directly, so don't move it back into `parser.py` or the DB module.

### Webapp (`src/cliproxy_usage_server/` + `frontend/`)

FastAPI + Vite/React dashboard that reads `usage.db` **read-only** and serves both `/api/*` JSON endpoints and the built SPA at `/`. Single-process deployment via `uv run cliproxy-usage-server`. Env vars (all prefixed `USAGE_*`) are documented in README.

- `config.py` — `ServerConfig` via pydantic-settings; same `AliasChoices` + `_env_name` error-translation pattern as the collector.
- `db.py` — `open_ro(path)` via `file:...?mode=ro` URI; `range_window(range_str, now)` maps `7h|24h|7d|all` to `(start, end)`.
- `aggregate.py` — one query helper per endpoint (`query_totals`, `query_timeseries`, etc.); buckets are **dense** (missing intervals zero-filled in Python, generated off the range window).
- `pricing.py` — hand-ported subset of ccusage's litellm pricing (`ModelPricing`, `resolve`, tiered-200k `compute_cost`, disk cache with TTL). Cache lives at `<db_parent>/pricing.json` by default.
- `main.py` — `create_app(config, *, pricing_provider=None)` + `run()`. Lifespan loads pricing on startup and schedules a background TTL refresh. SPA static mount only happens if `frontend/dist/` exists (so backend-only test runs don't need built assets). Routers live in `routes/`.
- `routes/usage.py`, `routes/pricing.py` — each exposes `build_router(...)` factories included in `main.py` under `/api`.

Shared-schema rule: response DTOs live in `cliproxy_usage_server/schemas.py`. The frontend types in `frontend/src/types/api.ts` are **hand-maintained** to match those DTOs — keep them in sync when backend schemas change.

Frontend (`frontend/`) is Bun + Vite + React 18 + TypeScript (strict + `noUncheckedIndexedAccess` + `exactOptionalPropertyTypes` + `verbatimModuleSyntax`) + Chart.js + SCSS modules + Zustand + react-router v7 + react-i18next. Path alias `@/*` → `./src/*`. Components under `src/components/{ui,usage,charts}/`; page orchestration in `src/pages/UsagePage.tsx`; API client in `src/services/api.ts`.

Authentication is delegated to an upstream reverse proxy; the app itself exposes `/api/*` without access control.

## Tests

- `tests/` — pytest suite. `conftest.py` exposes a `usage_export_json` fixture pointing at `test/usage-export-2026-04-22T19-22-30-546Z.json`.
- Note the split: **`test/`** (singular) holds JSON fixtures; **`tests/`** (plural) holds the test modules. Don't conflate them.
- `test_client.py` uses `httpx.MockTransport` via the `client=` injection point — prefer that over monkeypatching `httpx`.

## Conventions

- Ruff target is `py314`; basedpyright `pythonVersion = "3.14"`. Code must stay compatible with that even though `requires-python` is `>=3.12`.
- No migration / backward-compat shims unless explicitly asked (this is a greenfield collector).
- The SQLite schema in `db.py` is authoritative; if you change column order, update `_INSERT`'s positional `VALUES (?,...)` in lockstep.
