# cliproxy-usage

cliproxy-usage records who uses a [CLIProxyAPI](https://help.router-for.me/) instance, and how much it costs. It has two parts:

- `cliproxy-usage-collect`, a collector you run from cron. Each run takes a batch of request records from CLIProxyAPI's usage queue and stores them in a local SQLite database.
- `cliproxy-usage-server`, a dashboard that reads that database and shows spend, tokens, requests, failures and latency by person, model and credential. It can also show the live OAuth quota of the proxy's Claude and Codex accounts.

## Requirements

- CLIProxyAPI v7.2.97 or later, with usage publishing and the HTTP management usage queue enabled (see the [upstream docs](https://help.router-for.me/management/api.html#usage-telemetry-queue)). Use v8.0.48 or later to record 1-hour cache writes; with an earlier proxy, every cache write is priced at the 5-minute rate.
- [uv](https://docs.astral.sh/uv/) for Python 3.12 or later, and [bun](https://bun.sh/) to build the dashboard. The Nix flake provides both (`nix develop`), and `nix build` produces a package with the dashboard already built.

## Collector

```sh
uv sync
CLIPROXY_MANAGEMENT_KEY=your-key uv run cliproxy-usage-collect
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLIPROXY_MANAGEMENT_KEY` | required | Bearer token for the CLIProxyAPI management API. |
| `CLIPROXY_BASE_URL` | `http://localhost:8317` | The CLIProxyAPI origin or its `/v0/management` base URL. |
| `USAGE_DB_PATH` | `./usage.db` | SQLite database file, relative to the working directory. Use an absolute path under cron. |
| `USAGE_QUEUE_POP_COUNT` | `500` | Most queue records taken per run (1 to 10000). |
| `USAGE_HTTP_TIMEOUT_SECONDS` | `10.0` | Timeout for the management API request. |

Records leave CLIProxyAPI's queue when the collector takes them, and records left in the queue longer than its retention window are dropped upstream. Run the collector more often than that window, for example every 5 minutes:

```cron
*/5 * * * * CLIPROXY_MANAGEMENT_KEY=your-key USAGE_DB_PATH=/var/lib/cliproxy/usage.db /path/to/uv --directory /path/to/cliproxyapi-usage run cliproxy-usage-collect >> /var/log/cliproxy-usage.log 2>&1
```

Running it again is safe: a record whose timestamp is already stored is skipped. The collector logs to stderr, ending each run with how many records it inserted. Its exit code tells cron or an alert what went wrong:

| Code | Meaning |
| --- | --- |
| 0 | Success. |
| 1 | Temporary failure (network, timeout, database or migration error). The next run retries. |
| 2 | Configuration error, including a database written by a newer version. |
| 3 | CLIProxyAPI rejected the management key. |
| 4 | A queue record has an unexpected shape, for example from a CLIProxyAPI older than v7.2.97. |

Exit codes 1 and 2 caused by the database happen before the queue is touched, so no records are lost.

## Dashboard

```sh
(cd frontend && bun install && bun run build)
uv run cliproxy-usage-server
```

Then open `http://127.0.0.1:8318/usage`. The server serves the dashboard and its JSON API (`/api/*`) from one process.

| Variable | Default | Meaning |
| --- | --- | --- |
| `USAGE_DB_PATH` | `./usage.db` | The collector's database, opened read-only. |
| `USAGE_SERVER_HOST` | `127.0.0.1` | Address to listen on. |
| `USAGE_SERVER_PORT` | `8318` | Port to listen on. |
| `USAGE_BASE_PATH` | `/` | URL prefix, for example `/api-usage`. |
| `USAGE_PAGE_TITLE` | `CLIProxyAPI Usage Dashboard` | Browser tab title. Applied at startup, so no rebuild is needed. |
| `USAGE_KEY_ALIASES` | unset | Path to a JSON file of display names for API keys (see below). |
| `USAGE_PRICING_URL` | liteLLM's `model_prices_and_context_window.json` on GitHub | Source of model prices. |
| `USAGE_PRICING_CACHE` | `pricing.json` next to the database | Local copy of the prices. |
| `USAGE_PRICING_TTL_SECONDS` | `86400` | How often prices are fetched again. |
| `CLIPROXY_BASE_URL` | unset | Needed for the Limits page, same meaning as for the collector. |
| `CLIPROXY_MANAGEMENT_KEY` | unset | Needed for the Limits page. |
| `QUOTA_CACHE_TTL_SECONDS` | `300` | How long a quota answer is reused. |

**Authentication.** The server does not check who is calling. Put it behind a reverse proxy that does (for example nginx with oauth2-proxy, or Cloudflare Access), and keep the default `127.0.0.1` bind until that proxy is in place. If the dashboard shares a host with CLIProxyAPI, set `USAGE_BASE_PATH=/api-usage` and route only `/api-usage/` to it, so `/api/` stays with CLIProxyAPI.

**Names for people.** Each API key counts as one person, shown redacted (for example `sk-*******-alice`). To show names instead, point `USAGE_KEY_ALIASES` at a JSON object that maps the redacted key, exactly as the dashboard shows it, to a name. Full keys are never needed. A missing file logs a warning; a file that is not a JSON object of strings stops the server from starting.

```json
{ "sk-*******-alice": "Alice", "*******0a1b": "Shared CI key" }
```

## How it works

**Token counting.** Every stored request splits its input into uncached input, cache reads and cache writes, and a cache write can have a 5-minute or a 1-hour lifetime. Output includes reasoning. Wherever the dashboard says "tokens", it means all input plus all output, and the cache hit rate is cache reads divided by input. When CLIProxyAPI cannot split a request's tokens, the request is stored with no tokens: it counts as a request but adds no tokens or cost, and the collector's log line says how many such requests each run had.

**Pricing.** The dashboard prices each request on its own with liteLLM's price list, following the rules of [ccusage](https://github.com/ccusage/ccusage). Each token type has its own rate; a 1-hour cache write costs twice the input rate. A missing cache read rate falls back to 10% of the input rate (the full input rate for OpenAI models), and a missing cache write rate to 125%. Long-context rates apply per request, never to summed usage. For a model with higher rates above 200k tokens, only the tokens of each type beyond 200k cost more. OpenAI models, and models whose higher rates start at 272k, instead pay the higher rates on the whole request once its input passes the threshold. A model with no usable price shows its cost as `n/a`, and a total that includes such a model is marked with `*` because it is a lower bound. The models table lists the rates each model was priced at. Prices are fetched again every `USAGE_PRICING_TTL_SECONDS`; restart the server to pick up a new model sooner.

**The usage page.** For the chosen time range, the page shows spend, tokens, requests, failure rate and average latency, each compared with the previous range of the same length. Below that, people, models and credentials each get a share bar and a table with trends, and a heatmap shows which people use which models. Clicking a row opens a detail panel for that person, model or credential. Filters live in the URL, so a link reproduces the view, and opening the page without filters restores your last view. Time buckets follow your browser's time zone, and each entity keeps one color regardless of filters. The CSV button downloads the current view.

**The Limits page.** When `CLIPROXY_BASE_URL` and `CLIPROXY_MANAGEMENT_KEY` are set, `/limits` shows each Claude and Codex account's quota windows, read through CLIProxyAPI's management API. Without them, the page shows a disabled notice and the rest of the dashboard still works. A failure to reach an account shows as an error on that account's card only. Answers are reused for `QUOTA_CACHE_TTL_SECONDS` (errors for 60 seconds), so Refresh within that time does not call the vendor again. Codex cards show the account's manual reset credits and when they expire; using one asks for confirmation, spends the credit that expires first, and fetches that account's quota fresh.

**Database version check.** The database records its layout version. The server checks it at startup and refuses to start when the database is older (run the collector once to migrate it) or newer (upgrade the server). The collector migrates an older database itself and refuses a newer one with exit code 2.

## Upgrading

Upgrade steps for each release are in [CHANGELOG.md](CHANGELOG.md). Upgrading from 0.2.x changes the database: run the 0.3.x collector once before starting the 0.3.x server.

## Development

Run the server and the Vite dev server side by side, then open `http://localhost:5173`. Vite forwards `/api/*` to the server on port 8318.

```sh
uv run cliproxy-usage-server
cd frontend && bun run dev
```

The token columns of the `requests` table can be queried directly; `input_tokens` already includes `cache_read_tokens` and `cache_creation_tokens`:

```sql
SELECT api_key, date(timestamp) AS day, SUM(input_tokens + output_tokens) AS tokens
FROM requests GROUP BY api_key, day ORDER BY day DESC, tokens DESC;
```
