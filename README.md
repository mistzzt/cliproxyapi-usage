# cliproxy-usage

A cron-runnable collector that drains request records from CLIProxyAPI's HTTP
management usage queue and stores them in a local SQLite database. Each run
pops a bounded batch from the upstream queue and inserts new rows by timestamp,
so running the collector repeatedly is safe. Transient errors exit non-zero so
a cron scheduler will retry on the next tick.

Before running the collector, enable usage publishing and the HTTP management
usage queue in CLIProxyAPI. See the upstream API docs:
https://help.router-for.me/management/api.html#usage-telemetry-queue

## Environment variables

| Variable                              | Default                 | Required |
| ------------------------------------- | ----------------------- | -------- |
| `CLIPROXY_BASE_URL`                   | `http://localhost:8317` | no       |
| `CLIPROXY_MANAGEMENT_KEY`             | —                       | **yes**  |
| `USAGE_DB_PATH`                       | `./usage.db`            | no       |
| `USAGE_QUEUE_POP_COUNT`               | `500`                   | no       |
| `USAGE_HTTP_TIMEOUT_SECONDS`          | `10.0`                  | no       |

## Install and run

```sh
uv sync
CLIPROXY_MANAGEMENT_KEY=your-key uv run cliproxy-usage-collect
```

With every variable set explicitly (values shown are the defaults for optional
settings):

```sh
CLIPROXY_MANAGEMENT_KEY=your-key \
CLIPROXY_BASE_URL=http://localhost:8317 \
USAGE_DB_PATH=./usage.db \
USAGE_QUEUE_POP_COUNT=500 \
USAGE_HTTP_TIMEOUT_SECONDS=10.0 \
uv run cliproxy-usage-collect
```

- `CLIPROXY_MANAGEMENT_KEY` — required. Used as the Bearer token for the
  CLIProxyAPI management API.
- `CLIPROXY_BASE_URL` — optional, defaults to `http://localhost:8317`. This is
  the CLIProxyAPI origin or `/v0/management` base. Override if your proxy is on
  a different host/port (e.g. `https://proxy.internal.example.com` or
  `https://proxy.internal.example.com/v0/management`).
- `USAGE_DB_PATH` — optional, defaults to `./usage.db` (relative to the working
  directory). For a cron deployment, point it at a stable absolute path like
  `/var/lib/cliproxy/usage.db`.
- `USAGE_QUEUE_POP_COUNT` — optional, defaults to `500`. This is the maximum
  number of queue elements drained per collector run; valid values are 1-10000.
- `USAGE_HTTP_TIMEOUT_SECONDS` — optional, defaults to `10.0`. Applies to the
  management API request timeout.

## Cron example (every 5 minutes)

```cron
*/5 * * * * CLIPROXY_MANAGEMENT_KEY=REPLACE_WITH_YOUR_KEY USAGE_DB_PATH=/var/lib/cliproxy/usage.db /path/to/uv --directory /path/to/cliproxyapi-usage run cliproxy-usage-collect >> /var/log/cliproxy-usage.log 2>&1
```

The tool writes logs to stderr. Redirecting both stdout and stderr (`2>&1`) to a
log file captures everything. Alternatively, omit the redirect and let cron mail
you the output.

Choose a cron frequency shorter than the upstream queue retention window. The
collector removes records as it drains them, but any records left in CLIProxyAPI's
queue longer than its configured retention may be discarded upstream before this
tool can store them.

## Example SQL queries

**Total tokens per user per day**

```sql
SELECT
    source                    AS user,
    date(timestamp)           AS day,
    SUM(input_tokens)         AS input_tokens,
    SUM(output_tokens)        AS output_tokens,
    SUM(total_tokens)         AS total_tokens
FROM requests
GROUP BY source, date(timestamp)
ORDER BY day DESC, total_tokens DESC;
```

**Top models by request count**

```sql
SELECT
    model,
    COUNT(*)  AS requests
FROM requests
GROUP BY model
ORDER BY requests DESC
LIMIT 10;
```

## Webapp

A local dashboard (`cliproxy-usage-server`) serves a read-only FastAPI backend
plus a React SPA built with Vite, backed by the same `usage.db` produced by the
collector.

### Server environment variables

| Variable                    | Default                                                | Required     |
| --------------------------- | ------------------------------------------------------ | ------------ |
| `USAGE_DB_PATH`             | `./usage.db`                                           | no           |
| `USAGE_SERVER_HOST`         | `127.0.0.1`                                            | no           |
| `USAGE_SERVER_PORT`         | `8318`                                                 | no           |
| `USAGE_BASE_PATH`           | `/`                                                    | no           |
| `USAGE_PRICING_CACHE`       | `<db_parent>/pricing.json`                             | no           |
| `USAGE_PRICING_TTL_SECONDS` | `86400`                                                | no           |
| `USAGE_PRICING_URL`         | litellm `model_prices_and_context_window.json` raw URL | no           |
| `CLIPROXY_BASE_URL`         | —                                                      | for `/quota` |
| `CLIPROXY_MANAGEMENT_KEY`   | —                                                      | for `/quota` |
| `QUOTA_CACHE_TTL_SECONDS`   | `300`                                                  | no           |

`CLIPROXY_BASE_URL` and `CLIPROXY_MANAGEMENT_KEY` are only needed for the quota
API. For quota, `CLIPROXY_BASE_URL` should be the CLIProxyAPI management API base
URL, for example `http://localhost:8317/v0/management`. When both are set, the
server serves live OAuth quota for Claude and Codex auth-files at `/quota` (UI)
and `/api/quota/*` (JSON). Successful quota responses are cached for
`QUOTA_CACHE_TTL_SECONDS` (error envelopes for 60 s); clicking "Refresh" within
the TTL returns the cached value rather than re-hitting the upstream OAuth
endpoints. When either variable is unset, `/api/quota/*` returns `503` and the
UI shows a disabled banner; the dashboard remains available.

### Production run (single process)

```sh
cd frontend && bun install && bun run build && cd ..
uv run cliproxy-usage-server
```

The SPA is built into `frontend/dist/` and served at `/` by default;
FastAPI handles `/api/*`. Visit `http://127.0.0.1:8318/` and browse.

To deploy the same build under a URL prefix, set `USAGE_BASE_PATH` before
starting the server. For example, `USAGE_BASE_PATH=/api-usage` serves the SPA
at `/api-usage/`, client routes such as `/api-usage/quota`, static assets at
`/api-usage/assets/*`, and JSON endpoints at `/api-usage/api/*`.

### Dev workflow

Run backend and frontend in two terminals:

```sh
# terminal 1
uv run cliproxy-usage-server

# terminal 2
cd frontend && bun run dev
```

Open `http://localhost:5173` — root mode is the local default, and Vite proxies
`/api/*` to `127.0.0.1:8318` so the SPA stays in sync with the live backend.
Hot-module reload works for both TS and SCSS.

### Authentication

The server itself does not authenticate its API requests. Deploy it behind a
reverse proxy (e.g. nginx + oauth2-proxy, or Cloudflare Access) that enforces
authentication before traffic reaches uvicorn. If this dashboard shares a host
with CLIProxyAPI, set `USAGE_BASE_PATH=/api-usage` and route only
`/api-usage/` to the dashboard/auth proxy; `/api/` then remains available for
CLIProxyAPI. The included bind default of `127.0.0.1` ensures the server is
only reachable through such a proxy on a single host; override
`USAGE_SERVER_HOST` only after you have put that proxy in place.

> **TODO:** CI not configured. When a workflow is added (e.g.
> `.github/workflows/ci.yml`), include both a Python job (`uv run ruff check`,
> `uv run basedpyright`, `uv run pytest`) and a frontend job
> (`bun install --frozen-lockfile && bun run tsc --noEmit && bun run build`).
