# Codex Weekly Quota and Manual Reset Plan

## Outcome

Update the quota page so Codex windows are identified by `limit_window_seconds`, not by whether OpenAI placed them in `primary_window` or `secondary_window`. A weekly-only payload in `primary_window` must render as `Weekly limit`, including the same shape inside `additional_rate_limits`. Windows without a recognized duration remain visible with a neutral limit label and must never inherit a five-hour or weekly label from their position.

Expose the Codex manual reset count from `rate_limit_reset_credits.available_count` and let a user consume one reset from the corresponding Codex card. Reset-credit expiry details from the separate `/wham/rate-limit-reset-credits` feed are outside this change.

## API contract

Add a typed optional manual-reset summary to `ProviderQuota` in `src/cliproxy_usage_server/schemas.py` and mirror it in `frontend/src/types/api.ts`:

```text
ManualResetSummary {
  available_count: integer
}

ProviderQuota.manual_resets: ManualResetSummary | null
```

Add `POST /api/quota/{provider}/{auth_name}/reset`, returning `204 No Content` after the provider accepts the reset. Quota providers expose reset as an optional capability, with Codex as the first implementation; the route and service dispatch through that capability instead of branching on the provider name. Unknown providers or accounts return 404, registered providers without reset support return 405, disabled quota returns 503, and management transport errors or non-success responses from OpenAI return 502 with the upstream status in the error detail. A successful request invalidates the success and error quota caches for that account, including any fetch that began before invalidation, so the next quota read must go upstream instead of returning or repopulating the pre-reset response.

The CLIProxyAPI management `api-call` payload uses `authIndex`, method `POST`, URL `https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume`, the existing Codex authorization, content type, and user-agent headers, and JSON text in `data` containing a unique `redeem_request_id`. Extend the internal auth-file model to resolve an optional `Chatgpt-Account-Id` from the `id_token`, `metadata.id_token`, or `attributes.id_token` payload supplied by the management API. Authentication material, the CLIProxyAPI `auth_index`, decoded token data, and the account identifier remain server-side.

## Work

1. Update `src/cliproxy_usage_server/quota/providers/codex.py` to classify account-wide and named additional windows by their declared duration. Recognize 18,000 seconds as the five-hour limit and 604,800 seconds as the weekly limit, regardless of source field; use neutral account or named-limit labels for missing and unknown durations instead of positional fallback. Parse a non-negative manual reset count from the usage payload into the typed summary.

2. Add an optional reset-capability boundary alongside the existing quota provider protocol in `src/cliproxy_usage_server/quota/`, implement it in the Codex provider, and make the service dispatch through it. Resolve the same auth-file reference used by quota reads, forward the defined consume request through CLIProxyAPI, and invalidate both cache paths only after successful consumption. A quota fetch started before the reset must not publish stale data afterward, and a failed consume must not discard the last valid cached quota.

3. Add the provider-parameterized reset route in `src/cliproxy_usage_server/routes/quota.py` and cover it with the configured and disabled router wiring in `src/cliproxy_usage_server/main.py`. Apply the status contract above so the frontend can distinguish a completed reset, an unsupported provider capability, and upstream failures.

4. Extend `frontend/src/services/quotaApi.ts` and `frontend/src/stores/quotaStore.ts` with a provider-parameterized reset request and per-account reset state. Prevent duplicate reset submissions, keep the existing quota visible if the action fails, and force a fresh quota load after success so the window and reset count update immediately.

5. Update `frontend/src/components/quota/QuotaCard.tsx` and its styles to derive reset UI from the typed `manual_resets` capability instead of a Codex name check. Show `Manual resets: N` whenever the count is known, including zero; show the reset control only when the total `available_count > 0`, require explicit confirmation that one reset will be consumed, disable reset and refresh while the action is running, and present an account-local success or failure result.

6. Add a current weekly-only Codex fixture and focused backend tests covering top-level and additional `primary_window` values with `limit_window_seconds: 604800`, five-hour plus weekly payloads in either order, neutral handling of missing or unknown durations, reset-count parsing, capability dispatch, the 405 response for Claude, consume request construction, cache invalidation during an in-flight read, disabled-route behavior, and reset route error mapping. Prove dispatch is capability-based with a reset-capable test provider whose ID is not `codex`. Add colocated frontend pure-state tests for confirmation, reset availability, provider-parameterized requests, duplicate-submit prevention, and action transitions. Use the existing React DOM server renderer for markup tests covering the capability-derived count, button states, and account-local feedback, including a fixture whose provider is not Codex but whose response advertises `manual_resets`.

7. Add a shared serialized quota-response fixture containing `manual_resets` that backend schema tests produce or validate and frontend tests consume with the hand-maintained `QuotaResponse` type. Add a dedicated frontend test type-check configuration and package script because the production TypeScript configuration excludes `*.test.ts` and `*.test.tsx`; this makes drift in the new cross-boundary field fail either Python tests or TypeScript compilation.

8. Update `README.md` to describe duration-based Codex window names, neutral unknown-duration behavior, the manual reset count and confirmation, the provider-parameterized reset endpoint and capability behavior, and the post-reset cache behavior.

## Success criteria

- A fixture matching the current OpenAI shape, with a weekly `primary_window`, no `secondary_window`, and a weekly additional limit, produces only weekly labels. Check: `nix develop -c uv run pytest tests/test_quota_providers_codex.py -q`.

- A reset count of zero is visible but offers no reset action, a positive count offers one confirmed action even when the fixture provider is not Codex, duplicate clicks cannot consume twice, success refreshes the card, and failure preserves the prior quota while showing an error. Store and server-render tests check these states with `nix develop -c bash -lc 'cd frontend && bun test'`.

- The reset API dispatches a reset-capable non-Codex test provider without route changes, returns 405 for the real Claude provider, forwards a unique Codex redemption request with the defined management payload and optional account header, does not invalidate caches on failure, and prevents an in-flight pre-reset fetch from restoring stale quota. Check: `nix develop -c uv run pytest tests/test_quota_client.py tests/test_quota_cache.py tests/test_quota_service.py tests/test_quota_routes.py tests/test_server_app.py -q`.

- The shared response fixture validates against the backend DTO and compiles against the frontend type, and the regression suite remains green after obsolete positional-label expectations are replaced. Check: `nix develop -c uv run pytest && nix develop -c bash -lc 'cd frontend && bun test && bun run type-check:test && bun run build'`.

- Backend lint and type checks remain green. Check: `nix develop -c uv run ruff check && nix develop -c uv run basedpyright`.

- The frontend remains type-safe, lint-clean, and buildable. Check: `nix develop -c bash -lc 'cd frontend && bun run lint && bun run build'`.
