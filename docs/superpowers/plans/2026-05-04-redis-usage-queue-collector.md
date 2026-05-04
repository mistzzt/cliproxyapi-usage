# Redis Usage Queue Collector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the deprecated CLIProxyAPI `/usage/export` collector with a Redis RESP usage-queue collector and rename the collector package from `cliproxy_usage` to `cliproxy_usage_collect`.

**Architecture:** Keep the existing SQLite `requests` table and `RequestRecord` shape as the stable contract consumed by the dashboard. The collector becomes a one-shot queue drainer: connect to CLIProxyAPI's partial RESP endpoint with redis-py, pop a bounded batch of JSON records from the `queue` list, validate each record, and insert new rows by timestamp. No HTTP usage-export path, compatibility wrapper, migration shim, or dual collector mode should remain.

**Tech Stack:** Python 3.12+, pydantic v2, pydantic-settings, sqlite3, redis-py (`redis` package, current stable 7.4.x as of 2026-05-04), pytest, ruff, basedpyright, uv.

---

## Source Facts

- CLIProxyAPI's Redis usage queue is a minimal RESP interface on the same TCP listener as the HTTP API, default port `8317`.
- It is available only when Management is enabled and uses the same Management key.
- Authentication supports `AUTH <password>` and `AUTH <username> <password>`.
- Supported commands are only `AUTH`, `LPOP <key> [count]`, and `RPOP <key> [count]`.
- The key is currently ignored upstream; use `queue` consistently for readability.
- With `count`, `LPOP`/`RPOP` returns an array of JSON bulk strings; empty queue returns an empty array.
- Queue retention is in memory and controlled by `redis-usage-queue-retention-seconds`, default `60`, max `3600`; the collector should be cron-friendly and fast.
- Each JSON element contains: `timestamp`, `latency_ms`, `source`, `auth_index`, `tokens`, `failed`, `provider`, `model`, `endpoint`, `auth_type`, `api_key`, and `request_id`.
- Only the fields already represented by `RequestRecord` need to be stored in the existing DB.

## File Structure

### Create

- `src/cliproxy_usage_collect/__init__.py`
  - Package marker for the renamed collector package.
- `src/cliproxy_usage_collect/queue_client.py`
  - Owns Redis RESP connection setup and queue popping.
  - Exposes collector-facing errors: `AuthError`, `TransientError`.
  - Exposes a small function that returns raw JSON strings/bytes for one configured drain batch.
- `src/cliproxy_usage_collect/parser.py`
  - Owns validation of individual queue payload JSON objects.
  - Exposes `SchemaError`, `iter_records`, and the private pydantic models for queue payloads.
- `src/cliproxy_usage_collect/config.py`
  - Owns collector config loaded from environment variables.
  - Preserves env-var-name error translation pattern from the current config.
- `src/cliproxy_usage_collect/db.py`
  - Same SQLite schema and insert behavior as today, importing `RequestRecord` from the renamed package.
- `src/cliproxy_usage_collect/schemas.py`
  - Same shared `RequestRecord` model as today.
- `src/cliproxy_usage_collect/cli.py`
  - One-shot command entry point that wires config, queue client, parser, and DB insert.
- `tests/test_queue_client.py`
  - Unit tests for queue client behavior using a fake redis client/connection factory.
- `tests/test_queue_parser.py`
  - Unit tests for parsing queue payloads into `RequestRecord`.

### Modify

- `pyproject.toml`
  - Add `redis>=7.4.0,<8.0.0` to dependencies.
  - Change script target to `cliproxy_usage_collect.cli:_entry`.
  - Change uv build `module-name` from `cliproxy_usage` to `cliproxy_usage_collect`.
- `README.md`
  - Replace `/usage/export` collector docs with Redis usage queue docs.
  - Document queue-specific env vars and cron frequency implications.
  - Keep `cliproxy-usage-collect` as the command name unless the user later requests a CLI command rename.
- `AGENTS.md`
  - Update collector architecture notes from HTTP export to RESP queue.
  - Update package path references from `src/cliproxy_usage/` to `src/cliproxy_usage_collect/`.
- `flake.nix`
  - Update Python module names for packaging.
  - Add the redis dependency if the flake packages Python dependencies explicitly.
- `tests/conftest.py`
  - Update collector imports to `cliproxy_usage_collect`.
  - Add queue payload fixtures if useful.
- `tests/test_cli.py`
  - Replace HTTP mock transport tests with queue-client injection tests.
- `tests/test_config.py`
  - Update package imports and add tests for any new queue config fields.
- `tests/test_db.py`
  - Update package imports only unless DB behavior changes.
- `tests/test_server_aggregate.py`
  - Update collector imports.
- `tests/test_routes_usage.py`
  - Update collector imports.
- `tests/test_routes_pricing.py`
  - Update collector imports.

### Delete

- `src/cliproxy_usage/client.py`
  - The HTTP `/usage/export` client is obsolete.
- `src/cliproxy_usage/parser.py`
  - Replaced by queue payload parser in renamed package.
- `src/cliproxy_usage/config.py`
  - Replaced by renamed package config.
- `src/cliproxy_usage/db.py`
  - Replaced by renamed package DB module.
- `src/cliproxy_usage/schemas.py`
  - Replaced by renamed package schema module.
- `src/cliproxy_usage/cli.py`
  - Replaced by renamed package CLI.
- `src/cliproxy_usage/__init__.py`
  - Remove the old package entirely; no compatibility shim.
- `tests/test_client.py`
  - Replaced by `tests/test_queue_client.py`.
- `tests/test_parser.py`
  - Replaced by `tests/test_queue_parser.py`.

## Config Contract

The collector config should contain these fields:

```python
class Config(BaseSettings):
    base_url: str
    management_key: str
    db_path: Path
    queue_key: str
    queue_pop_count: int
    queue_pop_side: Literal["left", "right"]
    redis_socket_timeout_seconds: float
```

Environment variables:

- `CLIPROXY_BASE_URL`
  - Default: `http://localhost:8317`
  - Meaning: CLIProxyAPI origin for the RESP listener. This is no longer the `/v0/management` HTTP base URL for the collector.
- `CLIPROXY_MANAGEMENT_KEY`
  - Required.
  - Meaning: password passed to Redis `AUTH`.
- `USAGE_DB_PATH`
  - Default: `./usage.db`.
- `USAGE_QUEUE_KEY`
  - Default: `queue`.
- `USAGE_QUEUE_POP_COUNT`
  - Default: `500`.
  - Validation: integer from `1` to `10000`.
- `USAGE_QUEUE_POP_SIDE`
  - Default: `left`.
  - Allowed: `left`, `right`.
  - Maps to `LPOP` or `RPOP`.
- `USAGE_REDIS_SOCKET_TIMEOUT_SECONDS`
  - Default: `10.0`.
  - Validation: positive float.

Config errors should still return exit code `2` and mention environment variable names, not pydantic field names.

## Data Contract

Keep `RequestRecord` unchanged:

```python
class RequestRecord(BaseModel):
    timestamp: str
    api_key: str
    model: str
    source: str
    auth_index: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_tokens: int
    total_tokens: int
    failed: bool
```

Queue payload validation should require every upstream field that feeds `RequestRecord`:

- top level: `timestamp`, `api_key`, `model`, `source`, `auth_index`, `latency_ms`, `failed`, `tokens`
- tokens: `input_tokens`, `output_tokens`, `reasoning_tokens`, `cached_tokens`, `total_tokens`

Queue payload validation should ignore fields not yet stored:

- `provider`
- `endpoint`
- `auth_type`
- `request_id`

Invalid JSON and pydantic validation errors should raise `SchemaError` and return CLI exit code `4`.

## Queue Client Contract

Use redis-py because Redis documents redis-py as the recommended Python client and PyPI lists the maintained `redis` package with Python 3.10+ support.

The queue client should:

- Parse `CLIPROXY_BASE_URL` into host, port, TLS, and optional path-free origin.
- Use TLS when the scheme is `https`.
- Use port from the URL if present; otherwise default to `8317`.
- Authenticate with `password=management_key`.
- Use RESP2 unless testing proves RESP3 is required.
- Avoid any dependency on unsupported Redis commands:
  - no `PING`
  - no `LLEN`
  - no blocking pop
  - no stream APIs
  - no pub/sub
  - no pipelines
- Pop exactly one bounded batch per collector run using `LPOP queue count` or `RPOP queue count`.
- Normalize redis-py return shapes:
  - empty queue -> empty list
  - single bulk string -> one-item list
  - array -> list
  - bytes -> decode as UTF-8 before JSON parsing, or leave decoding to parser consistently
- Map authentication failures to `AuthError`.
- Map connection errors, socket timeouts, protocol errors, and unexpected return types to `TransientError`.

Implementation note for the worker: redis-py may issue optional client metadata commands in some versions. If that breaks against the partial RESP endpoint, configure the connection to disable client info/health-check behavior rather than adding a handwritten RESP client.

## CLI Contract

Keep the public console script name:

- `cliproxy-usage-collect`

Exit codes remain stable:

- `0`: success, including empty queue
- `1`: transient queue/connection/protocol error
- `2`: config error
- `3`: auth error
- `4`: schema error

Stderr output should be queue-oriented:

- Success: `inserted N new records from M queued records`
- Empty queue: `inserted 0 new records from 0 queued records`
- Errors: preserve the existing `Configuration error:`, `Authentication error:`, `Transient error:`, and `Schema error:` prefixes.

The CLI should accept dependency injection for tests:

```python
def main(
    argv: list[str] | None = None,
    *,
    queue_client: QueueClientProtocol | None = None,
) -> int:
    ...
```

The exact protocol can be a typing `Protocol` or a simple callable, but tests must not require a real CLIProxyAPI process.

## Task Breakdown

### Task 1: Rename Collector Package and Imports

**Files:**
- Create: `src/cliproxy_usage_collect/__init__.py`
- Move/rename ownership from: `src/cliproxy_usage/*`
- Modify: `pyproject.toml`
- Modify: collector imports in `tests/`
- Delete: `src/cliproxy_usage/`

- [ ] Move the collector package to `src/cliproxy_usage_collect/`.
- [ ] Update imports in tests and server-side test helpers from `cliproxy_usage` to `cliproxy_usage_collect`.
- [ ] Update `pyproject.toml` script target and uv build module list.
- [ ] Run the narrow import-oriented tests to confirm no old package imports remain.
- [ ] Search the repo for `cliproxy_usage` and resolve every remaining collector reference except historical text intentionally changed in docs.
- [ ] Commit with message: `refactor: rename collector package`.

**Acceptance Criteria:**
- Importing `cliproxy_usage_collect.schemas.RequestRecord` works.
- Importing `cliproxy_usage` fails; no compatibility shim exists.
- `cliproxy-usage-collect` points at `cliproxy_usage_collect.cli:_entry`.

### Task 2: Add Queue Payload Parser

**Files:**
- Create/modify: `src/cliproxy_usage_collect/parser.py`
- Create: `tests/test_queue_parser.py`
- Modify: `tests/conftest.py` if shared fixtures are useful
- Delete/stop using: old export parser tests

- [ ] Write tests for a valid single queue JSON object that maps to `RequestRecord`.
- [ ] Write tests for `iter_records` returning an iterator, not a list.
- [ ] Write tests for invalid JSON string/bytes raising `SchemaError`.
- [ ] Write tests for missing required top-level fields raising `SchemaError`.
- [ ] Write tests for missing token fields raising `SchemaError`.
- [ ] Write tests showing `provider`, `endpoint`, `auth_type`, and `request_id` are accepted but ignored.
- [ ] Replace the export parser with queue payload parsing.
- [ ] Run the parser test module.
- [ ] Commit with message: `feat: parse redis usage queue payloads`.

**Acceptance Criteria:**
- Parser accepts `str` and `bytes` queue elements.
- Parser yields the existing `RequestRecord` shape.
- Parser does not accept the old nested `/usage/export` shape.

### Task 3: Add Redis Queue Client

**Files:**
- Create: `src/cliproxy_usage_collect/queue_client.py`
- Create: `tests/test_queue_client.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] Add the maintained `redis` dependency.
- [ ] Define `AuthError` and `TransientError` in `queue_client.py`.
- [ ] Write tests using a fake redis client factory for:
  - constructing HTTP origin as non-TLS host/port
  - constructing HTTPS origin as TLS host/port
  - default port `8317`
  - configured pop side `left` calls `LPOP`
  - configured pop side `right` calls `RPOP`
  - empty queue returns empty list
  - bytes and strings are returned as a normalized sequence
  - auth/permission failure maps to `AuthError`
  - connection/timeout/protocol failure maps to `TransientError`
- [ ] Implement the queue client behind those tests.
- [ ] Run the queue client test module.
- [ ] Commit with message: `feat: read usage records from redis queue`.

**Acceptance Criteria:**
- No tests use a real Redis server.
- The production client uses redis-py.
- The production path does not call unsupported Redis commands beyond the pop operation and authentication performed by the client connection.

### Task 4: Update Collector Config

**Files:**
- Modify: `src/cliproxy_usage_collect/config.py`
- Modify: `tests/test_config.py`

- [ ] Update config tests for the new default `CLIPROXY_BASE_URL` of `http://localhost:8317`.
- [ ] Add config tests for `USAGE_QUEUE_KEY`.
- [ ] Add config tests for `USAGE_QUEUE_POP_COUNT` valid and invalid values.
- [ ] Add config tests for `USAGE_QUEUE_POP_SIDE` valid and invalid values.
- [ ] Add config tests for `USAGE_REDIS_SOCKET_TIMEOUT_SECONDS` valid and invalid values.
- [ ] Preserve the missing `CLIPROXY_MANAGEMENT_KEY` behavior and env-var naming in errors.
- [ ] Implement config changes.
- [ ] Run the config test module.
- [ ] Commit with message: `feat: configure redis usage queue collection`.

**Acceptance Criteria:**
- Invalid queue config returns a `ConfigError`.
- Required/missing config messages name env vars.
- Server config for `/quota` remains untouched.

### Task 5: Wire CLI to Queue Collection

**Files:**
- Modify: `src/cliproxy_usage_collect/cli.py`
- Modify: `src/cliproxy_usage_collect/db.py`
- Modify: `tests/test_cli.py`
- Delete: `tests/test_client.py`

- [ ] Rewrite CLI tests around injected queue data instead of injected `httpx.Client`.
- [ ] Test happy path inserts all valid queued records and returns `0`.
- [ ] Test second run with duplicate timestamps inserts zero new rows and returns `0`.
- [ ] Test empty queue returns `0`.
- [ ] Test config error returns `2`.
- [ ] Test auth error returns `3`.
- [ ] Test transient queue error returns `1`.
- [ ] Test malformed queued payload returns `4`.
- [ ] Wire CLI to open DB, drain one queue batch, parse records, and insert rows.
- [ ] Run CLI and DB tests.
- [ ] Commit with message: `feat: collect usage from redis queue`.

**Acceptance Criteria:**
- Existing SQLite schema stays unchanged.
- Duplicate handling remains `INSERT OR IGNORE` by `timestamp`.
- CLI no longer imports `httpx` or old HTTP client code.

### Task 6: Update Server/Test Imports

**Files:**
- Modify: `tests/test_server_aggregate.py`
- Modify: `tests/test_routes_usage.py`
- Modify: `tests/test_routes_pricing.py`
- Modify: `tests/conftest.py`
- Modify: any remaining collector imports found by search

- [ ] Update server-facing tests that seed usage DBs via collector helpers.
- [ ] Confirm the FastAPI server package remains `cliproxy_usage_server`.
- [ ] Run server aggregate and route tests that depend on seeded collector records.
- [ ] Commit with message: `test: update server fixtures for renamed collector`.

**Acceptance Criteria:**
- Dashboard behavior remains unchanged because the DB schema and `requests` rows are unchanged.
- No application code imports the old collector package.

### Task 7: Update Documentation and Agent Instructions

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

- [ ] Rewrite README intro to describe Redis queue draining instead of `/usage/export`.
- [ ] Document enabling usage publishing in CLIProxyAPI.
- [ ] Document that cron frequency should be shorter than upstream queue retention.
- [ ] Document `CLIPROXY_BASE_URL` as the CLIProxyAPI origin for RESP, not `/v0/management`.
- [ ] Document `USAGE_QUEUE_KEY`, `USAGE_QUEUE_POP_COUNT`, `USAGE_QUEUE_POP_SIDE`, and `USAGE_REDIS_SOCKET_TIMEOUT_SECONDS`.
- [ ] Keep webapp docs scoped to the dashboard and quota API.
- [ ] Update AGENTS collector architecture and file map.
- [ ] Search docs for `/usage/export` and replace obsolete collector references.
- [ ] Commit with message: `docs: document redis usage queue collector`.

**Acceptance Criteria:**
- README no longer tells users to configure `/v0/management` for collection.
- README mentions the upstream queue retention risk.
- AGENTS points future agents at `src/cliproxy_usage_collect/`.

### Task 8: Full Verification

**Files:**
- Whole repo

- [ ] Run ruff check.
- [ ] Run basedpyright.
- [ ] Run the full pytest suite.
- [ ] Run the frontend type/build checks only if touched files require it; otherwise document that frontend was not changed.
- [ ] Search for obsolete symbols:
  - `cliproxy_usage`
  - `fetch_export`
  - `/usage/export`
  - `http_client=`
- [ ] Inspect `uv.lock` to confirm the redis dependency is locked.
- [ ] Commit final cleanup if verification required changes.

**Acceptance Criteria:**
- Python lint, type-check, and tests pass.
- No old collector package remains.
- No migration/backward-compatibility code was added.

## Self-Review

### Spec Coverage

- Deprecation of management usage endpoint: covered by deleting HTTP client and removing `/usage/export` docs.
- Use partial RESP: covered by queue client contract and Redis queue source facts.
- Use established Python library: covered by redis-py dependency and queue client contract.
- Populate existing database: covered by unchanged `RequestRecord`, DB schema, CLI wiring, and dashboard test import updates.
- Rename `cliproxy_usage` to `cliproxy_usage_collect`: covered by Task 1 and follow-up import tasks.
- No migration/backward compatibility: covered by deletion list and acceptance criteria.

### Placeholder Scan

No tasks contain open-ended placeholder work. Each task has explicit files, behavior, tests, acceptance criteria, and commit boundaries.

### Type Consistency

The plan consistently uses:

- `Config`
- `RequestRecord`
- `SchemaError`
- `AuthError`
- `TransientError`
- `queue_client`
- `cliproxy_usage_collect`

