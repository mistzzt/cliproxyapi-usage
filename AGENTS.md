# CLAUDE.md

Guidance for coding agents in this repository. `CLAUDE.md` is a symlink to `AGENTS.md`; edit `AGENTS.md`. User-facing behavior and env vars are in `README.md`; release notes and upgrade steps in `CHANGELOG.md`.

## Commands

Python uses `uv` (`requires-python >=3.12`, `.python-version` pins 3.14); the frontend uses `bun`. When `bun` is not on PATH, run frontend commands in the flake dev shell: `nix develop -c bash -c 'cd frontend && ...'`.

- Install: `uv sync`; frontend `cd frontend && bun install`.
- Collector: `uv run cliproxy-usage-collect` (needs `CLIPROXY_MANAGEMENT_KEY`). The gitignored `.envrc` exports a working `CLIPROXY_BASE_URL` and key for the upstream dev proxy.
- Server: `uv run cliproxy-usage-server` (`http://127.0.0.1:8318/usage`). Frontend dev: `cd frontend && bun run dev` (Vite on `:5173` proxies `/api` to `:8318`).
- Backend tests: `uv run pytest`; one test: `uv run pytest tests/test_queue_parser.py::test_name -q`.
- Frontend: `cd frontend && bun test && bun run lint && bun run build`. `build` runs `tsc -b`, which is where frontend type errors (test files included) show up, and writes `frontend/dist/`.
- Browser tests (Playwright, `e2e/`): `nix develop -c bash -c '(cd frontend && bun run build) && python -m pytest e2e -q'`. They need the dev shell's nixpkgs Python because PyPI's Playwright driver cannot start on NixOS; `uv run pytest` does not collect them.
- Lint and format: `uv run ruff check`, `uv run ruff format`. Types: `uv run basedpyright` (covers `src` and `tests`).
- Nix package: `nix build`, which builds the frontend into the Python package. A frontend dependency change must update the x86_64-linux `outputHash` in `flake.nix`.

No CI is configured, so run the relevant checks above before committing.

## Rules

- **Real data.** `test/cpa-usage.tar.gz` (gitignored) holds a real `usage.db` and `pricing.json` for manual testing: extract it outside the repo and point `USAGE_DB_PATH` at it. It contains real API keys and accounts, so never copy anything from it into tests, fixtures, docs, commits or PR text. Test data is synthetic only.
- **Collector exit codes are stable** because cron and alerts depend on them: 0 ok, 1 transient (network, SQLite and migration errors), 2 config (including a DB from a newer version), 3 auth, 4 schema. DB errors return before the queue is popped.
- **The schema in `src/cliproxy_usage_collect/db.py` is authoritative and versioned.** `_DDL` is the layout of `SCHEMA_VERSION` in `schema_version.py`, stored in `PRAGMA user_version` and read only through `user_version(conn)`. Changing the layout means bumping `SCHEMA_VERSION`. Run `_DDL` statements one `execute` at a time inside `write_transaction(conn)`, because `executescript` would commit the open transaction. Every insert into the current layout names its columns.
- **No migration or backward-compatibility code** unless asked. The one exception is `migration.py` (0.3.x only, removed in 0.4.0 with `tests/test_migration.py`); it is the only code that knows the pre-0.3.0 layout or classifies rows by model name.
- **The server opens `usage.db` read-only** (`open_ro`) and never imports the collector's `db.py`; it shares only `config.py` and `schema_version.py`.
- **Token columns have fixed meanings.** `input_tokens` is all input (uncached + cache read + cache creation); `cache_read_tokens` and `cache_creation_tokens` are parts of it, and `cache_creation_1h_tokens` is the 1-hour part of cache creation. `output_tokens` includes `reasoning_tokens`. `total_tokens` is upstream's reported total and nothing reads it. `service_tier` and `ttft_ms` are stored only; NULL means unknown.
- **One cost implementation.** The grouped query in `aggregate._fetch_cells` is the only per-request cost code. It splits each request into five token types (uncached input, cache read, 5-minute cache creation, 1-hour cache creation, output) and into base-rate and above-threshold amounts per the model's `CostPlan`, so tiers apply per request, never to sums. Every endpoint sums the same cells, so totals, rows, trends and mixes agree. In `pricing.Amounts` and `pricing.Rates`, `cache_creation_5m` is the 5-minute part, while `cache_creation` in the DB column, `Costs` and `cost_by_type` means both parts. Cost tests price requests through the production query (`request_cost` in `tests/conftest.py`) against fixed, hand-checked amounts and never reimplement cost logic.
- **Tokens are derived in one place.** The API returns only raw token components; "tokens" means input + output and is computed only by `tokens`/`tokenSeries` in `frontend/src/utils/usage/totals.ts`. Cache hit rate is cache read over input everywhere.
- **Shared schemas.** Response DTOs live in `src/cliproxy_usage_server/schemas.py`. `frontend/src/types/api.ts` mirrors them by hand; change both together. The collector's `schemas.py` holds `RequestRecord`, the row shape shared with the DB; the queue JSON models stay private to `parser.py`.

## Architecture

The collector is a one-shot CLI meant for cron: it pops a bounded batch from CLIProxyAPI's management usage queue and inserts rows with `INSERT OR IGNORE` on the `timestamp` primary key, so re-running is safe. Modules in `src/cliproxy_usage_collect/`:

- `config.py`: env settings via pydantic-settings; `load_settings` (shared with the server) raises `ConfigError` naming env vars; `management_url` accepts the CLIProxyAPI origin or the `/v0/management` base.
- `queue_client.py`: httpx call to `usage-queue`; maps failures to `AuthError` or `TransientError`.
- `parser.py`: validates queue records into a `ParsedBatch` (`records`, `incomplete`). Token counts come only from `token_breakdown` (its `schema_version` must be 2, else `SchemaError`); a breakdown whose quality is not `complete` stores zero tokens except `total_tokens`.
- `schema_version.py`, `db.py`, `migration.py`: version check, schema creation, insert, and the one-time 0.3.x migration (backup to `<db>.pre-cache-columns`, then one transaction).
- `cli.py`: wires these together and maps errors to exit codes.

The server (`src/cliproxy_usage_server/`) is FastAPI serving JSON under `/api` and the built SPA (`/usage`, `/limits`; other paths redirect to `/usage`). It has no access control; an upstream reverse proxy handles authentication.

- `main.py`: `create_app(config, *, pricing_provider=None, quota_service_factory=None)` and `run()`, which exits before uvicorn starts when the DB's version differs from `SCHEMA_VERSION`. The SPA is mounted only when `frontend/dist/` exists; unknown `/api/*` paths return 404. `/usage-config.js` injects base path, API base and page title at runtime.
- `routes/usage.py`: `summary`, `breakdown?dim=person|model|credential`, `export` (CSV), `options`. Range params: optional `start` (absent means all time), `end`, required IANA `tz`, filters `people`/`models`/`credentials`, and `bucket=auto|hour|day|week`.
- `db.py`: read-only open and viewer-local, DST-aware bucketing (`auto_bucket`, `effective_bucket` capped by `MAX_BUCKETS`, `bucket_starts`, `SliceIndexer` over 15-minute UTC slices).
- `aggregate.py`: `_fetch_cells` and the `query_*` helpers. `options` ranks entities by all-time spend and gives the top `SLOT_COUNT` fixed color slots, so colors never depend on filters.
- `redact.py`: `redact_key` (people are redacted API keys, labeled by `USAGE_KEY_ALIASES`), `redact_source` (credentials), `model_key` (empty model id becomes `<empty>`).
- `pricing.py`: liteLLM price fetch with disk cache and TTL, and `cost_plan`, a port of ccusage's rules (marginal `*_above_200k` rates; whole-request long-context for OpenAI models and 272k-only rates; 1-hour writes at twice the input rate; fallback cache rates).
- `quota/`: Claude and Codex OAuth quota through the management API, with per-account error envelopes and a TTL cache; `routes/quota.py` exposes it.

## Frontend

Bun, Vite, React 19, strict TypeScript (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `verbatimModuleSyntax`), Chart.js (registered in `src/components/charts/chart-setup.ts`), SCSS modules, TanStack Query for all server data, Zustand for the theme, react-router v7. Import alias `@/*` is `./src/*`.

- Pages are `src/pages/UsagePage.tsx` and `src/pages/QuotaPage.tsx` under `src/components/ui/AppLayout.tsx`, which owns the page width and side gutters (`--page-max-width`, `--page-gutter` in `src/styles/global.scss`) and the sticky header. Pages and their components never add their own horizontal padding or width cap.
- Put chart and state logic in pure modules under `src/utils/usage/` (or `src/utils/`) with colocated bun:test `*.test.ts`, not inside components.
- Usage data goes through `src/hooks/useUsageQueries.ts` with keys from `src/utils/usage/queryKeys.ts`. `useUsageWindow` resolves the picked range into a `ResolvedWindow` and is the only place a request reads the current time; every request keys on that window, so panels and the drawer share it. Responses never expire, refetch on focus or retry; only a new key or Refresh fetches.
- Panels render through `src/components/ui/Loaded.tsx`, keep the previous data dimmed while loading (`Busy.module.scss`), and show a failure note instead of older data. Use `Button.tsx`, `Banner.module.scss` and `src/components/usage/Shared.module.scss` rather than new variants.
- View state lives in the URL (`src/hooks/useUsageViewState.ts`); a load without a query string restores localStorage `usage.view.v1`. The browser's IANA time zone is sent as `tz`.
- Entity names always come from the server `label` (model ids shortened by `modelNames`), and colors from `options` slots.
- Charts on one time axis share `src/components/charts/time-axis.ts` so their bars align.

## Tests

- `tests/conftest.py` provides `synthetic_db_path` (several people and models, spanning the 2026-11-01 DST change in `America/Chicago`), `write_usage_db`, `request_cost`, `db_at_version`/`read_version`, and the quota api-call fixtures. JSON fixtures live in `tests/fixtures/`.
- Tests that call the server's `run()` use the `run_env` fixture in `tests/test_server_app.py`, which points `USAGE_DB_PATH` at a temp path and replaces `uvicorn.run`.
- Fake CLIProxyAPI with `httpx.MockTransport` (see `tests/test_queue_client.py`), not a real process.
- `e2e/` runs project code only through `uv run` subprocesses, since the dev shell's Python lacks the project packages: `seed_db.py` writes a synthetic DB relative to now, and the fixtures start `cliproxy-usage-server` on it. The Limits tests fake the quota endpoints with `page.route`.

## Conventions

- Ruff targets `py314` and basedpyright `pythonVersion = "3.14"` in mode `standard`; code must type-check there even though `requires-python` is `>=3.12`.
