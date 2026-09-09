# Quota page follow-ups: Codex reset expiry and Claude Fable window

Two gaps in the quota page (`/quota`). The reference implementation is the dashboard at `~/personal/Cli-Proxy-API-Management-Center` (see `src/features/quota/providers/codex/data.ts`, `src/utils/quota/resetCredits.ts`, and `src/features/quota/providers/claude/data.ts` there).

Out of scope: gating the "Reset quota" button on usage thresholds, and the reference's `applicable_available_count`. Whether a reset is worth spending is the user's call.

## 1. Show the expiry of each Codex manual reset

### Why

The Codex card shows only `manual_resets.available_count`. Each reset credit expires on its own date, and a user deciding whether to spend one now or save it needs those dates.

### Upstream data

The usage payload (`GET https://chatgpt.com/backend-api/wham/usage`) only carries counts. Per-credit detail comes from a second OAuth call through the same CLIProxyAPI `api-call` mechanism:

- `GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits`
- Headers: `Authorization: Bearer $TOKEN$`, the same `User-Agent` as the usage call, `Accept: application/json`, `OpenAI-Beta: codex-1`, `Originator: Codex Desktop`, and `Chatgpt-Account-Id` when the auth file yields one (same rule as the reset consume call).
- Body shape:

```json
{
  "available_count": 2,
  "applicable_available_count": 1,
  "credits": [
    {"id": "...", "reset_type": "codex_rate_limits", "status": "available",
     "granted_at": "2026-05-01T00:00:00Z", "expires_at": "2026-06-01T00:00:00Z"}
  ]
}
```

Only credits with `reset_type == "codex_rate_limits"` and `status == "available"` count. Any other entry, or one without a parseable `expires_at`, is dropped.

### Backend

- Add a separate `ResetCreditsProvider` Protocol in `quota/providers/base.py` (build the credits `api-call` payload, parse the credits body into typed credits). `CodexProvider` implements it alongside `ResetProvider`. Do not widen `ResetProvider`: the fake in `tests/test_quota_service.py::test_reset_dispatch_is_capability_based_for_non_codex_provider` relies on its current single-method shape.
- `QuotaService._fetch_quota` calls the credits endpoint after a successful usage fetch, only for providers with the capability. The credits call failing (non-2xx, transport error, unparseable body) must not fail the quota response: the card still shows the count from the usage payload, with `credits_error` explaining why expiry is unavailable.
- `available_count` comes from the usage payload. If the usage payload has no valid count, use the credits endpoint's `available_count`, else `len(credits)`. If the usage payload has no count and the credits call fails, `manual_resets` stays `None`.
- The credits result is part of the same cached `QuotaResponse`, so it shares the success TTL and is invalidated by `reset_quota` like everything else.
- Existing Codex tests in `tests/test_quota_service.py` that drive a `_SequenceClient` with a fixed response list need one extra credits response (success or failure) per Codex usage fetch.

Schema change in `cliproxy_usage_server/schemas.py`, frozen like the neighbouring models (mirror in `frontend/src/types/api.ts`):

```python
class ManualResetCredit(BaseModel):
    id: str
    granted_at: datetime | None
    expires_at: datetime

class ManualResetSummary(BaseModel):
    available_count: int            # ge=0, unchanged
    credits: list[ManualResetCredit] = []   # sorted by expires_at ascending
    credits_error: str | None = None        # set when the credits call failed
```

### Frontend

In the manual-reset block of `QuotaCard.tsx`, under the count, render one line per credit: an ordinal ("Reset 1", "Reset 2") and the expiry as an absolute local date-time (viewer timezone, date and time, so the user can compare against their own plans) followed by the existing relative form from `utils/time.ts` in parentheses. When `credits_error` is set and `credits` is empty, show the error text in place of the list. When both are empty and the count is zero, render nothing extra.

The confirmation prompt ("Consume one manual reset?") should name the expiry of the credit that will be spent, which is the earliest-expiring one.

### Tests

- `tests/test_quota_providers_codex.py`: credits payload parsing (filtering by type and status, dropping malformed entries, ordering by expiry); the credits `api-call` payload carries the extra headers and the optional account id.
- `tests/test_quota_service.py`: credits call failure yields a success envelope with `credits_error` set and the usage count intact.
- Update `tests/fixtures/quota-response-manual-resets.json` to include `credits`. It is consumed by `tests/test_schemas_quota.py` and `frontend/src/stores/quotaResetState.test.ts`.
- `frontend/src/components/quota/QuotaCard.test.tsx`: expiry rows render; error text renders when `credits_error` is set.

## 2. Show the weekly Fable limit for Claude accounts

### Why

Anthropic reports Fable usage in a newer `limits[]` array, and leaves the legacy `iguana_necktie` key null. `ClaudeProvider.parse` only reads top-level window-shaped keys, so the Fable row never appears even when the account has Fable usage.

### Upstream data

Entries in `limits[]` look like:

```json
{"kind": "weekly_scoped", "group": "weekly", "percent": 64,
 "resets_at": "2026-05-01T00:00:00+00:00", "is_active": true,
 "scope": {"model": {"id": null, "display_name": "Fable"}}}
```

A Fable entry is one with `kind == "weekly_scoped"`, `scope.model.display_name` case-insensitively equal to `"Fable"` or `"Fable 5"`, and a numeric `percent`. A null or missing `resets_at` yields `resets_at=None`; a non-parseable `resets_at` disqualifies the entry. Prefer the entry with `is_active == true`, else the first qualifying one.

### Backend

`ClaudeProvider.parse` in `quota/providers/claude.py`:

- Parse `limits[]` for a Fable entry and emit it as a `QuotaWindow` with `id="seven_day_fable"` and label `"7-day Fable 5"`, `used_percent` from `percent`, `resets_at` from `resets_at`.
- When no `limits[]` Fable entry qualifies, fall back to the legacy `iguana_necktie` window, emitted under the same id and label (rename from the current "Iguana Necktie" label).
- Never emit both. When neither source has data, emit no Fable row (a null window from Anthropic means the account has no Fable window).
- Append the Fable row (from either source) after all other windows.
- Ignore other `limits[]` entries (non-weekly kinds, other models, malformed items); do not put `limits` into `extra`.

No schema change and no frontend change.

### Tests

`tests/test_quota_providers_claude.py`, with a new fixture or inline payloads: modern `limits[]` Fable row; legacy fallback; active entry preferred over inactive without a duplicate; malformed and non-Fable limits ignored while standard windows survive; neither source present yields no Fable row.

## Definition of done

All of the following pass from the repo root:

- `uv run pytest` green, including the new tests listed above.
- `uv run ruff check && uv run ruff format --check && uv run basedpyright` clean.
- `cd frontend && bun test && bun run lint && bun run build` clean (build covers `tsc -b`, which catches a stale `api.ts`).
- In `README.md`, the paragraph beginning "Codex window labels" mentions the per-credit expiry list, and a sentence covers the Claude "7-day Fable 5" row.

Human verification (not an executor gate): against the dev proxy in `.envrc`, a Codex account with resets available shows one expiry line per credit, and a Claude account with Fable usage shows a "7-day Fable 5" row.
