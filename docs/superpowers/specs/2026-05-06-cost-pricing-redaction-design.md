# Cost accuracy, pricing fallback, and credential redaction

Date: 2026-05-06
Scope: `cliproxy_usage_server` backend + `frontend` dashboard
Status: design — pending implementation plan

## Problem

Three independent issues in the dashboard:

1. **Codex cost is overcounted.** `compute_cost` is called with the full
   `input_tokens` *and* with `cache_read_input_tokens=cached_tokens`. For
   Codex/OpenAI rows, upstream sends `cached_tokens` as a subset of
   `input_tokens` (this matches OpenAI's Responses API and is what ccusage
   normalises in `apps/codex/src/command-utils.ts:16-17`), so cached tokens
   are billed twice — once at the full input rate (still inside
   `input_tokens`) and once at the cache-read rate. Anthropic rows are
   unaffected because Claude's `input_tokens` is already the uncached count.
2. **`/api/api-stats` (and other cost-bearing endpoints) sometimes returns
   `cost: null`.** This happens when a request includes a model that's not
   in the live liteLLM pricing map, so `resolve()` returns `None` and the
   row's cost collapses to `null`. There is no retry, no fallback, and no
   signal to the frontend to differentiate "cost is genuinely zero" from
   "we don't have pricing for this model".
3. **`/api/credential-stats` leaks raw API keys.** When the upstream auth
   is key-based (openai, openai-compat, anthropic, etc.), the `source`
   column on `CredentialStat` is something like `openai:sk-...` and the
   raw key is returned to the client unchanged. OAuth sources
   (`codex:user@gmail.com`, `claude:foo@example.com`) are not sensitive in
   the same way and can pass through.

## Non-goals

- Rewriting historical DB rows. The `requests` table stays a faithful
  mirror of the upstream queue payload — the collector continues to do a
  pure passthrough in `parser.py`. All fixes live at query / response
  time.
- Any change to the upstream CLIProxyAPI queue protocol.
- Redaction of `auth_index` or quota-route fields — flagged for a
  follow-up, not part of this spec.

## Design

### 1. Cost split for OpenAI-convention rows

Add a helper to `pricing.py`:

```python
_OPENAI_SOURCE_PREFIXES: tuple[str, ...] = (
    "codex:",
    "openai:",
    "openai-compat:",
)


def split_tokens_for_cost(
    source: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int,
) -> TokenCounts:
    """Return TokenCounts ready for compute_cost, accounting for the
    OpenAI convention where cached_tokens is a subset of input_tokens.

    For Codex/OpenAI sources (case-insensitive prefix match):
        cache_read = min(cached_tokens, input_tokens)
        input     = max(input_tokens - cache_read, 0)
    For all other sources (Anthropic, Gemini, …): pass through unchanged
    so cached_tokens stay in cache_read_input_tokens and input_tokens
    stays as the already-uncached count.
    """
```

Behaviour:

- The function is **only** used inside cost helpers. Endpoints that
  return raw token counts (`/api/token-breakdown`, the `input_tokens`
  / `cached_tokens` fields on `ModelStat`, `ApiStat`) keep showing the
  upstream values — the user-visible token displays must not change.
- Source prefix detection is case-insensitive and explicit. New
  OpenAI-convention providers must be added to
  `_OPENAI_SOURCE_PREFIXES` deliberately. Model-name detection was
  rejected as fragile against new model names.

Call-site changes in `routes/usage.py`:

- `_grouped_cost_rows` gains `source` in the `SELECT` and `GROUP BY`.
- `_compute_totals_cost`, `_query_bucket_model_costs`, `_cost_by_api_key`,
  `_cost_by_credential`, and the inline cost loop in `/api/model-stats`
  iterate per `(model, source)` cell, call `split_tokens_for_cost`, then
  `compute_cost`, then sum back up to the dimension the endpoint
  returns. `/api/model-stats` rolls the source dimension up.
- `query_model_stats` aggregate row stays per-model — only the cost
  computation widens the GROUP BY.

### 2. Pricing fallback, retry, and status surfacing

#### Fallback table

New module `cliproxy_usage_server/pricing_fallback.py`:

```python
FALLBACK_PRICING: dict[str, ModelPricing] = {
    # hand-curated entries for models we expect to see but liteLLM may lag on.
    # Keys use the same naming convention as the live liteLLM map.
}
```

The table starts small and is grown by hand. It is a Python literal —
not fetched, not cached on disk. Edits ship in normal commits.

#### Resolve

`resolve()` is widened to return a status:

```python
PricingResolution = Literal["live", "fallback", "missing"]

def resolve(
    model_name: str,
    pricing: Mapping[str, ModelPricing],
    fallback: Mapping[str, ModelPricing] = FALLBACK_PRICING,
) -> tuple[ModelPricing | None, PricingResolution]:
    ...
```

Lookup order: live exact / live prefix / live substring / fallback
exact / fallback prefix / fallback substring / `(None, "missing")`.

#### Background refresh on miss

When any row in the response resolves as `fallback` or `missing`, the
endpoint triggers a non-blocking pricing refresh by calling a
rate-limited helper on `app.state`:

- `app.state.last_pricing_refresh: datetime`
- Helper: at most one refresh per 60 seconds; runs the existing
  `fetch_pricing` with `cache_path` mtime cleared (so TTL is bypassed).
- The current request is **not** delayed; it returns with the fallback
  data. The next request reaps the refreshed map.

Threading: the existing pricing fetcher is sync (`httpx.Client`). Route
handlers in `routes/usage.py` are currently `def` (sync). Dispatch the
refresh by accepting a `BackgroundTasks` parameter on each cost-bearing
endpoint and calling `background_tasks.add_task(refresh_fn)` when any
fallback/missing row is detected. `refresh_fn` itself runs on FastAPI's
threadpool, takes a lock on `app.state.last_pricing_refresh`, checks the
60-second rate limit, calls `fetch_pricing` with the cache treated as
expired, and writes the result back to `app.state.pricing`. All
exceptions are caught and logged. Startup-path fetch is unchanged.

#### Schema additions

`schemas.py`:

```python
CostStatus = Literal["live", "fallback", "partial_fallback", "missing"]
```

New field added to:

- `Totals.cost_status`
- `ApiStat.cost_status`
- `ModelStat.cost_status`
- `CredentialStat.cost_status`

Per-row endpoints (`/api/model-stats`) only ever produce `live`,
`fallback`, or `missing` — never `partial_fallback`, since each row is
one model. Roll-up endpoints (`/api/overview` totals, `/api/api-stats`,
`/api/credential-stats`) compute `cost_status` as:

- All component models `live` → `live`
- All component models `fallback` → `fallback`
- All component models `missing` → `missing` and `cost = None`
- Mix of `live` + `fallback` (no missing) → `partial_fallback`
- Any `missing` mixed with anything → `partial_fallback` for
  cost-bearing components, with the missing models contributing 0 to
  the sum. Document this clearly: a `missing` model in a roll-up does
  not null the whole row, it just lowers the displayed cost.

`cost` is `None` only when `cost_status == "missing"` *and* the row has
no other models contributing.

For `/api/timeseries?metric=cost`: add a sibling field
`series_status: dict[str, CostStatus]` keyed by the same series name as
`series` (`__all__`, `<model>`). Per-bucket statuses are not surfaced —
the worst case across buckets in the window wins. The `cost` series
itself never contains `null`; missing-pricing models contribute `0.0`
and the series status records the degradation.

`TimeseriesResponse` gets the new field as optional with a default of
empty dict for backward-compat shape on non-cost metrics.

#### Frontend

`frontend/src/types/api.ts` adds the matching string-literal type and
optional fields on each DTO. Components:

- `components/usage/StatsTable.tsx` (and any cost-rendering component)
  — when `cost_status` is `fallback` or `partial_fallback`, render the
  cost number in a warning color (existing theme token; pick the red
  used for failed-rate alerts) with a tooltip "Estimated — upstream
  pricing unavailable for some models". When `missing`, render `—` in
  the same color with the same tooltip.
- `components/usage/Totals.tsx` — same rule applied to the totals row.
- `components/charts/CostChart.tsx` — when
  `series_status[<model>]` is `fallback` / `partial_fallback`, render
  the line dashed and add a chip in the legend ("estimated"). When
  `missing`, render the line dotted and grey it out.

### 3. Credential redaction

Add to `redact.py`:

```python
def redact_source(source: str) -> str:
    """Redact the credential identifier in a `<provider>:<id>` source.

    - id contains '@' (OAuth email)  -> return source unchanged.
    - id has no '@'                  -> treat as API key, run redact_key
                                        on it; rejoin as f"{provider}:{redacted}".
    - no ':' separator               -> redact_key on the whole string.
    - Idempotent: any '***' in id    -> return source unchanged.
    """
```

Wiring in `schemas.py`:

```python
RedactedSource = Annotated[str, BeforeValidator(redact_source)]


class CredentialStat(BaseModel):
    source: RedactedSource
    ...
```

Mirrors the existing `RedactedApiKey` pattern. No backend filter logic
keys off `source`, so redaction at response-serialisation time is
sufficient.

## Tests

- `tests/test_pricing_split.py`
  - Codex split: cached < input, cached == input, cached > input,
    cached == 0.
  - Claude / unknown source: cached_tokens flow into
    `cache_read_input_tokens` unchanged.
  - Source-prefix matching is case-insensitive
    (`Codex:abc`, `OPENAI:def`).
- `tests/test_pricing_fallback.py`
  - `resolve()` returns `(entry, "live")` for live-map hits.
  - Returns `(entry, "fallback")` when only the fallback table has the
    model.
  - Returns `(None, "missing")` otherwise.
  - Roll-up `cost_status` computation: all-live, all-fallback,
    live+fallback (`partial_fallback`), any-missing (`partial_fallback`
    with reduced cost), all-missing (`missing` + `cost=None`).
- `tests/test_pricing_refresh.py`
  - Background refresh fires when a request resolves any
    fallback/missing row.
  - Refresh is rate-limited to one per 60s.
  - Refresh failures don't surface to the user.
- `tests/test_redact_source.py`
  - Email passthrough: `codex:user@gmail.com` → unchanged;
    `claude:a@b.io` → unchanged.
  - Key redaction: `openai:sk-abc-1234567890` →
    `openai:sk-*******-1234567890` (uses `redact_key` rules).
  - Provider-prefix variants: `openai-compat:`, `anthropic:`.
  - Missing colon: `sk-rawkey…` → bare `redact_key` result.
  - Idempotency: redacting an already-redacted source is a no-op.
- `tests/test_routes_usage.py`
  - Codex fixture row produces ccusage-equivalent cost (small fixture
    ported from ccusage tests).
  - `/api/api-stats`, `/api/model-stats`, `/api/credential-stats`,
    `/api/overview` all populate `cost_status`.
  - `/api/credential-stats` returns redacted `source` for key-based
    rows and unchanged source for email rows.
  - `/api/timeseries?metric=cost` populates `series_status`.

## Migration / rollout

- No DB migration. Schema unchanged.
- Backwards-compat: `cost_status` is a new required field on the
  response DTOs. The frontend ships in lockstep — there is no external
  consumer of these endpoints (auth is delegated to the upstream
  reverse proxy, the SPA is the only client), so a hard cut-over is
  acceptable.
- `series_status` on `TimeseriesResponse` defaults to `{}` for
  non-cost metrics so existing tests/clients reading those metrics see
  no change.

## Open follow-ups (out of scope for this spec)

- Redaction of email-style identifiers in quota responses
  (`auth_name`).
- Surfacing per-bucket pricing status in cost timeseries (currently
  rolled up to one status per series).
- A periodic background refresh of pricing independent of request
  traffic, so dashboards left open eventually heal even without a
  fallback hit.
