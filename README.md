# cliproxy-usage

A cron-runnable collector that drains request records from CLIProxyAPI's HTTP management usage queue and stores them in a local SQLite database. Each run pops a bounded batch from the upstream queue and inserts new rows by timestamp, so running the collector repeatedly is safe. Transient errors exit non-zero so a cron scheduler will retry on the next tick.

Before running the collector, enable usage publishing and the HTTP management usage queue in CLIProxyAPI. See the upstream API docs: https://help.router-for.me/management/api.html#usage-telemetry-queue

## Environment variables

| Variable                              | Default                 | Required |
| ------------------------------------- | ----------------------- | -------- |
| `CLIPROXY_BASE_URL`                   | `http://localhost:8317` | no       |
| `CLIPROXY_MANAGEMENT_KEY`             | none                    | **yes**  |
| `USAGE_DB_PATH`                       | `./usage.db`            | no       |
| `USAGE_QUEUE_POP_COUNT`               | `500`                   | no       |
| `USAGE_HTTP_TIMEOUT_SECONDS`          | `10.0`                  | no       |

## Install and run

```sh
uv sync
CLIPROXY_MANAGEMENT_KEY=your-key uv run cliproxy-usage-collect
```

With every variable set explicitly (values shown are the defaults for optional settings):

```sh
CLIPROXY_MANAGEMENT_KEY=your-key \
CLIPROXY_BASE_URL=http://localhost:8317 \
USAGE_DB_PATH=./usage.db \
USAGE_QUEUE_POP_COUNT=500 \
USAGE_HTTP_TIMEOUT_SECONDS=10.0 \
uv run cliproxy-usage-collect
```

- `CLIPROXY_MANAGEMENT_KEY`: required. Used as the Bearer token for the CLIProxyAPI management API.
- `CLIPROXY_BASE_URL`: optional, defaults to `http://localhost:8317`. This is the CLIProxyAPI origin or `/v0/management` base. Override if your proxy is on a different host/port (e.g. `https://proxy.internal.example.com` or `https://proxy.internal.example.com/v0/management`).
- `USAGE_DB_PATH`: optional, defaults to `./usage.db` (relative to the working directory). For a cron deployment, point it at a stable absolute path like `/var/lib/cliproxy/usage.db`.
- `USAGE_QUEUE_POP_COUNT`: optional, defaults to `500`. This is the maximum number of queue elements drained per collector run; valid values are 1-10000.
- `USAGE_HTTP_TIMEOUT_SECONDS`: optional, defaults to `10.0`. Applies to the management API request timeout.

## Cron example (every 5 minutes)

```cron
*/5 * * * * CLIPROXY_MANAGEMENT_KEY=REPLACE_WITH_YOUR_KEY USAGE_DB_PATH=/var/lib/cliproxy/usage.db /path/to/uv --directory /path/to/cliproxyapi-usage run cliproxy-usage-collect >> /var/log/cliproxy-usage.log 2>&1
```

The tool writes logs to stderr. Redirecting both stdout and stderr (`2>&1`) to a log file captures everything. Alternatively, omit the redirect and let cron mail you the output.

Choose a cron frequency shorter than the upstream queue retention window. The collector removes records as it drains them, but any records left in CLIProxyAPI's queue longer than its configured retention may be discarded upstream before this tool can store them.

## Example SQL queries

**Tokens per person (API key) per day**

Tokens here match the dashboard: input + output, cached tokens excluded.

```sql
SELECT
    api_key                               AS person,
    date(timestamp)                       AS day,
    SUM(input_tokens)                     AS input_tokens,
    SUM(output_tokens)                    AS output_tokens,
    SUM(input_tokens + output_tokens)     AS tokens
FROM requests
GROUP BY api_key, date(timestamp)
ORDER BY day DESC, tokens DESC;
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

A local dashboard (`cliproxy-usage-server`) serves a read-only FastAPI backend plus a React SPA built with Vite, backed by the same `usage.db` produced by the collector.

The usage page (`/usage`; `/` redirects there) answers how much each person uses and what share of the total that is; a person is one API key, shown redacted or by its alias from `USAGE_KEY_ALIASES`. It shows spend, tokens, requests, failure rate, and average latency with the change against the previous period of equal length, a share bar and per-row trends for people, a people by model heatmap, and Models / Credentials tabs. Clicking a row opens a detail drawer with that entity's trend and mix. Tokens always mean input + output tokens (cached tokens excluded). Time buckets (hour, day, or week) follow the browser's timezone. Filters and view settings live in the URL, so links are shareable, and opening the page without them restores the last view. Entity colors are fixed by all-time spend, so they do not change with filters.

The JSON endpoints are `/api/usage/summary`, `/api/usage/breakdown`, `/api/usage/options`, and the quota endpoints under `/api/quota/*`. The usage endpoints report only the raw token components (`input_tokens`, `output_tokens`, `cached_tokens`); the dashboard derives tokens from them.

### Server environment variables

| Variable                    | Default                                                | Required      |
| --------------------------- | ------------------------------------------------------ | ------------- |
| `USAGE_DB_PATH`             | `./usage.db`                                           | no            |
| `USAGE_SERVER_HOST`         | `127.0.0.1`                                            | no            |
| `USAGE_SERVER_PORT`         | `8318`                                                 | no            |
| `USAGE_BASE_PATH`           | `/`                                                    | no            |
| `USAGE_PAGE_TITLE`          | `CLIProxyAPI Usage Dashboard`                          | no            |
| `USAGE_PRICING_CACHE`       | `<db_parent>/pricing.json`                             | no            |
| `USAGE_PRICING_TTL_SECONDS` | `86400`                                                | no            |
| `USAGE_PRICING_URL`         | litellm `model_prices_and_context_window.json` raw URL | no            |
| `USAGE_KEY_ALIASES`         | unset (redacted keys are shown)                        | no            |
| `CLIPROXY_BASE_URL`         | none                                                   | for `/limits` |
| `CLIPROXY_MANAGEMENT_KEY`   | none                                                   | for `/limits` |
| `QUOTA_CACHE_TTL_SECONDS`   | `300`                                                  | no            |

`CLIPROXY_BASE_URL` and `CLIPROXY_MANAGEMENT_KEY` are only needed for the quota API. As for the collector, `CLIPROXY_BASE_URL` may be the CLIProxyAPI origin or its `/v0/management` base. When both are set, the server serves live OAuth quota for Claude and Codex auth-files at `/limits` (UI) and `/api/quota/*` (JSON). Successful quota responses and the auth-file list are cached for `QUOTA_CACHE_TTL_SECONDS` (error envelopes for 60 s); a failure to reach CLIProxyAPI or read a vendor's answer shows as an error on that account's card only; clicking "Refresh" within the TTL returns the cached value rather than re-hitting the upstream OAuth endpoints. When either variable is unset, `/api/quota/*` returns `503` and the UI shows a disabled banner; the dashboard remains available.

Codex window labels come from each window's declared duration. A duration of 18,000 seconds is a five-hour limit, 604,800 seconds is a weekly limit, and missing or unknown durations use a neutral account or named-limit label. The Codex card shows the manual reset count when OpenAI supplies it, including zero, and lists the expiry of each available reset credit (fetched from the `rate-limit-reset-credits` endpoint after the usage call; if that call fails the count still shows with a note that expiry is unavailable). When resets are available, the user must confirm before consuming one; the prompt names the expiry of the earliest-expiring credit, which is the one spent. Claude accounts get a "7-day Fable 5" row read from Anthropic's `limits[]` array (weekly Fable entry, active one preferred). `POST /api/quota/{provider}/{auth_name}/reset` exposes this as a provider capability, returns `405` for registered providers without reset support, and returns `204` after a supported provider accepts the reset. A successful reset invalidates both successful and error quota cache entries, including older in-flight fetches, so the following quota read goes upstream. A failed reset preserves the last cached quota.

`USAGE_PAGE_TITLE` sets the browser tab title (default `CLIProxyAPI Usage Dashboard`). It is injected at runtime, so a single prebuilt `frontend/dist/` can be relabeled by restarting the server, no rebuild required.

`USAGE_KEY_ALIASES` is an optional path to a JSON file that gives people display names. The file holds one JSON object that maps a redacted key, exactly as the UI shows it (for example `sk-*******-alice` or `*******0a1b`), to a display name. Full API keys are never needed in this file. Keys without an entry keep their redacted label. If the file does not exist, the server logs a warning and shows redacted keys; if it is not a JSON object of strings, the server refuses to start. Entries that match no recorded key are logged as warnings once, the first time the dashboard loads its filter options.

```json
{
  "sk-*******-alice": "Alice",
  "*******0a1b": "Shared CI key"
}
```

### Cost accuracy

The webapp prices every request on its own with liteLLM's pricing data (`https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json`, disk-cached locally), following the cost rules of [ccusage](https://github.com/ccusage/ccusage). What to know:

- **Long-context rates apply per request.** For models with liteLLM `*_above_200k_tokens` rates, only the tokens of each type above 200k in a single request bill at the higher rate. Models whose higher rates are only published above 272k (such as gpt-5.x), and every OpenAI-accounting model, bill the whole request at the long-context rates once its context passes the threshold. Summed usage never crosses a threshold on its own.
- **Codex/OpenAI rows** report `cached_tokens` as a *subset* of `input_tokens`, so the cached portion bills at the cache-read rate and only the rest at the input rate. Which models use this accounting comes from liteLLM's `litellm_provider` (`openai`, `azure`), and the models table's cache hit rate uses the same convention.
- **Fallback rates.** A missing cache-read rate is 10% of the input rate (the full input rate for OpenAI-accounting models), and a missing cache-write rate is 125% of the input rate. liteLLM entries without both a base input and output rate are ignored.
- **Anthropic rows undercount cache writes.** Upstream CLIProxyAPI collapses Anthropic's `cache_creation_input_tokens` and `cache_read_input_tokens` into a single `cached_tokens` value before pushing to its usage queue. The webapp bills that combined value at the cache-read rate (~10% of input) rather than the cache-creation rate (~125% of input) for the creation portion. Claude totals are therefore a **lower bound** when prompt caching is in use; the magnitude of the undercount depends on your cache-write/read ratio. There is no purely-backend fix because the data needed to split the two buckets is not in the queue payload.
- **Missing pricing.** When liteLLM has no usable entry for a model, or the model id is empty (shown as `<empty>`), the dashboard renders that model's cost as `n/a` ("missing"). Totals that combine some priced and some unpriced models show the priced part with a trailing `*` ("partial_missing"), so they are a lower bound. The models table shows the rates each model is billed at per 1M tokens (input / output / cache read, with cache write and any long-context rates in the tooltip), the same rates the cost uses, or `n/a` when unpriced. The pricing map is refreshed periodically (`USAGE_PRICING_TTL_SECONDS`, default daily); restart the server or shorten the TTL if you need a newly-released model picked up sooner.

### Production run (single process)

```sh
cd frontend && bun install && bun run build && cd ..
uv run cliproxy-usage-server
```

The SPA is built into `frontend/dist/` and served at `/` by default; FastAPI handles `/api/*`. Visit `http://127.0.0.1:8318/usage` (or `/`, which redirects there) and browse.

To deploy the same build under a URL prefix, set `USAGE_BASE_PATH` before starting the server. For example, `USAGE_BASE_PATH=/api-usage` serves the SPA at `/api-usage/`, client routes such as `/api-usage/usage` and `/api-usage/limits`, static assets at `/api-usage/assets/*`, and JSON endpoints at `/api-usage/api/*`.

### Dev workflow

Run backend and frontend in two terminals:

```sh
# terminal 1
uv run cliproxy-usage-server

# terminal 2
cd frontend && bun run dev
```

Open `http://localhost:5173`. Root mode is the local default, and Vite proxies `/api/*` to `127.0.0.1:8318` so the SPA stays in sync with the live backend. Hot-module reload works for both TS and SCSS.

### Authentication

The server itself does not authenticate its API requests. Deploy it behind a reverse proxy (e.g. nginx + oauth2-proxy, or Cloudflare Access) that enforces authentication before traffic reaches uvicorn. If this dashboard shares a host with CLIProxyAPI, set `USAGE_BASE_PATH=/api-usage` and route only `/api-usage/` to the dashboard/auth proxy; `/api/` then remains available for CLIProxyAPI. The included bind default of `127.0.0.1` ensures the server is only reachable through such a proxy on a single host; override `USAGE_SERVER_HOST` only after you have put that proxy in place.

> **TODO:** CI not configured. When a workflow is added (e.g. `.github/workflows/ci.yml`), include both a Python job (`uv run ruff check`, `uv run basedpyright`, `uv run pytest`) and a frontend job (`bun install --frozen-lockfile && bun test && bun run lint && bun run build`, where `build` runs `tsc -b`).
