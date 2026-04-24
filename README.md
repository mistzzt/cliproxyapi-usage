# cliproxy-usage

A cron-runnable collector that pulls request records from a CLIProxyAPI management
endpoint (`/usage/export`) and stores them in a local SQLite database. Each run
fetches all available records and upserts them by timestamp, so running the
collector repeatedly is safe. Transient errors (network, server) exit non-zero so
a cron scheduler will retry on the next tick.

## Environment variables

| Variable                  | Default                               | Required |
| ------------------------- | ------------------------------------- | -------- |
| `CLIPROXY_BASE_URL`       | `http://localhost:8317/v0/management` | no       |
| `CLIPROXY_MANAGEMENT_KEY` | —                                     | **yes**  |
| `USAGE_DB_PATH`           | `./usage.db`                          | no       |

## Install and run

```sh
uv sync
CLIPROXY_MANAGEMENT_KEY=your-key uv run cliproxy-usage-collect
```

With every variable set explicitly (values shown are the defaults for the two
optional ones):

```sh
CLIPROXY_MANAGEMENT_KEY=your-key \
CLIPROXY_BASE_URL=http://localhost:8317/v0/management \
USAGE_DB_PATH=./usage.db \
uv run cliproxy-usage-collect
```

- `CLIPROXY_MANAGEMENT_KEY` — required. Sent as `Authorization: Bearer <key>`.
- `CLIPROXY_BASE_URL` — optional, defaults to `http://localhost:8317/v0/management`.
  Override if your proxy is on a different host/port (e.g.
  `https://proxy.internal.example.com/v0/management`).
- `USAGE_DB_PATH` — optional, defaults to `./usage.db` (relative to the working
  directory). For a cron deployment, point it at a stable absolute path like
  `/var/lib/cliproxy/usage.db`.

## Cron example (every 5 minutes)

```cron
*/5 * * * * CLIPROXY_MANAGEMENT_KEY=REPLACE_WITH_YOUR_KEY USAGE_DB_PATH=/var/lib/cliproxy/usage.db /path/to/uv --directory /path/to/cliproxyapi-usage run cliproxy-usage-collect >> /var/log/cliproxy-usage.log 2>&1
```

The tool writes logs to stderr. Redirecting both stdout and stderr (`2>&1`) to a
log file captures everything. Alternatively, omit the redirect and let cron mail
you the output.

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

| Variable                   | Default                                                                                         | Required |
| -------------------------- | ----------------------------------------------------------------------------------------------- | -------- |
| `USAGE_DB_PATH`            | `./usage.db`                                                                                    | no       |
| `USAGE_SERVER_HOST`        | `127.0.0.1`                                                                                     | no       |
| `USAGE_SERVER_PORT`        | `8318`                                                                                          | no       |
| `USAGE_PRICING_CACHE`      | `<db_parent>/pricing.json`                                                                      | no       |
| `USAGE_PRICING_TTL_SECONDS`| `86400`                                                                                         | no       |
| `USAGE_PRICING_URL`        | litellm `model_prices_and_context_window.json` raw URL                                          | no       |
| `CLIPROXY_BASE_URL`        | —                                                                                               | for `/quota` |
| `CLIPROXY_MANAGEMENT_KEY`  | —                                                                                               | for `/quota` |
| `QUOTA_CACHE_TTL_SECONDS`  | `300`                                                                                           | no       |

`CLIPROXY_BASE_URL` and `CLIPROXY_MANAGEMENT_KEY` use the same values as the
collector. When both are set, the server serves live OAuth quota for Claude
and Codex auth-files at `/quota` (UI) and `/api/quota/*` (JSON). Successful
quota responses are cached for `QUOTA_CACHE_TTL_SECONDS` (error envelopes for
60 s) — clicking "Refresh" within the TTL returns the cached value rather
than re-hitting the upstream OAuth endpoints. When either variable is unset,
`/api/quota/*` returns `503` and the UI shows a disabled banner; the rest of
the app is unaffected.

### Production run (single process)

```sh
cd frontend && bun install && bun run build && cd ..
uv run cliproxy-usage-server
```

The SPA is built into `frontend/dist/` and served at `/`;
FastAPI handles `/api/*`. Visit `http://127.0.0.1:8318/` and browse.

### Dev workflow

Run backend and frontend in two terminals:

```sh
# terminal 1
uv run cliproxy-usage-server

# terminal 2
cd frontend && bun run dev
```

Open `http://localhost:5173` — Vite proxies `/api/*` to `127.0.0.1:8318` so
the SPA stays in sync with the live backend. Hot-module reload works for both
TS and SCSS.

### Authentication

The server itself does not authenticate `/api/*` requests. Deploy it behind a
reverse proxy (e.g. nginx + oauth2-proxy, or Cloudflare Access) that enforces
authentication before traffic reaches uvicorn. The included bind default of
`127.0.0.1` ensures the server is only reachable through such a proxy on a
single host; override `USAGE_SERVER_HOST` only after you have put that proxy
in place.

> **TODO:** CI not configured. When a workflow is added (e.g.
> `.github/workflows/ci.yml`), include both a Python job (`uv run ruff check`,
> `uv run basedpyright`, `uv run pytest`) and a frontend job
> (`bun install --frozen-lockfile && bun run tsc --noEmit && bun run build`).
