# Usage Dashboard Unified Explorer Design

Status: Proposed

Decision: Replace the four separate time-series charts with one unified usage explorer. Retain the summary cards and the full per-user, per-model, provider-credential, and service-health sections.

## Summary

The current dashboard repeats requests, tokens, token composition, and cost across four full-width charts. This creates a long page, duplicates controls, and makes it difficult to focus on one question. At the same time, the detailed tables are valuable and should not be compressed into small explanatory panels.

The redesigned page will use one large explorer for time-series analysis. Users select a metric, breakdown, granularity, and display mode within that explorer. The existing detailed statistics remain below it as full sections.

The redesign is intentionally incremental. It uses the existing usage API response shapes and database schema. Limited backend behavior changes improve cost sparklines and top-model ranking, but no new endpoint or response DTO is required.

## Terminology

The following meanings are contractual throughout the interface and implementation:

- User: an API key from `requests.api_key`, displayed in redacted form.
- Provider credential: the upstream account or credential from `requests.source`.
- Model: the model recorded on a request.
- Global filter: a range, model, or user selection that scopes every summary, chart, table, and health calculation on the page.
- Explorer breakdown: a visualization choice inside the unified explorer. It does not change the global filter.
- Total tokens: input tokens plus output tokens. Cached tokens are never counted toward a token total. This applies to every place the interface reports a token quantity: the Tokens summary card and sparkline, TPM, the Tokens explorer metric, token-based model ranking, and the total-token columns in the user, model, and provider-credential tables.

Provider credentials must never be presented as users. Email-like provider account names belong only in the provider-credential statistics section.

## Problem

The existing page has several related problems:

1. Requests and tokens use nearly identical line charts with duplicated legends and granularity controls.
2. Token composition and cost add two more full-width charts with the same time axis.
3. The page fetches all chart datasets even when the user is interested in only one metric.
4. All-model mode plots the aggregate plus many model lines, which creates visual clutter.
5. Large or sparse ranges can flatten the useful part of the chart against long stretches of zero.
6. The cost summary sparkline is currently zero-filled rather than calculated from real per-bucket cost.
7. User identity is easy to confuse with upstream provider credentials unless the interface labels API keys explicitly.

## Goals

- Provide one focused place to explore requests, tokens, and cost over time.
- Let users zoom into a sub-range of the explorer chart so sparse or spiky ranges do not flatten the useful part of the plot.
- Count token totals as input plus output everywhere, never including cached tokens.
- Preserve the full information density of user, model, provider-credential, and health statistics.
- Make individual API-key filtering a first-class sidebar feature.
- Ensure every global filter scopes every section consistently.
- Limit automatic chart decomposition to a readable number of series.
- Load only the active explorer dataset.
- Preserve current pricing-status warnings and timezone-aware dense buckets.
- Work on desktop and mobile without horizontal page overflow.

## Non-goals

- Previous-period comparison, anomaly detection, or automated explanations.
- Saved views, cross-filtering, or shareable query URLs.
- New authentication or authorization behavior.
- Database schema changes or migrations.
- User naming or API-key alias management. Users remain redacted API keys.
- Combining users with provider credentials.
- Removing the provider-credential or service-health sections.
- Adding chart or state-management dependencies, with one exception: the established Chart.js zoom plugin may be added to implement explorer zoom. No other new dependency is permitted.

## Proposed page structure

The page is ordered as follows:

1. Application header.
2. Persistent filter sidebar on desktop and filter drawer on mobile.
3. Summary cards.
4. Unified usage explorer.
5. User statistics.
6. Model statistics.
7. Provider credential statistics.
8. Service health.

The detailed sections remain full-width cards. They may use horizontal table scrolling on narrow screens, but they are not collapsed into summary lists or hidden behind the explorer.

## Global filter sidebar

### Time range

Retain the current rolling, calendar, all-time, and custom range controls. The selected range scopes the entire page.

### Models

Retain a searchable multi-select with an All models option.

One series limit governs the explorer: at most seven named series may be drawn. Explicit model selection is capped at seven models, and automatic decomposition draws at most six models plus `Other` (also seven series). The existing chart-series constants change to match this limit; there is no separate cap for explicit selection versus automatic decomposition.

### Users

Rename the API-key filter section to `Users (API keys)`.

The control contains:

- Search over redacted API keys.
- An All users checkbox.
- One checkbox per redacted API key.
- A selected count.
- The existing persisted selection behavior.

There is no provider grouping in this control. Provider information cannot be inferred reliably from an API key and is not user identity.

### Filter semantics

Range, model, and user selections apply to:

- Summary totals and sparklines.
- The active explorer query.
- User statistics.
- Model statistics.
- Provider credential statistics.
- Service health.

Changing a filter refreshes all scoped data. Refresh preserves the selected filters and explorer state.

## Summary cards

Retain the five existing cards:

- Requests.
- Tokens.
- RPM.
- TPM.
- Cost.

Each card continues to show a total and sparkline for the selected global scope. The cost sparkline must use actual per-bucket cost rather than zero-filled placeholder values.

Previous-period deltas shown in the static prototype are not part of this design.

## Unified usage explorer

The explorer is the only large time-series card on the page.

### Explorer state

```ts
type ExplorerMetric = 'requests' | 'tokens' | 'cost';
type ExplorerBreakdown = 'total' | 'model' | 'token_type';
type ExplorerGranularity = 'auto' | 'hour' | 'day';
type ExplorerDisplay = 'line' | 'stacked';
```

Default state:

- Metric: Requests.
- Breakdown: Total.
- Granularity: Auto.
- Display: Line.

Explorer state should persist locally independently from the global filters.

### Valid combinations

| Metric | Total | Model | Token type |
| --- | --- | --- | --- |
| Requests | Line | Line or stacked | Not available |
| Tokens | Line | Line or stacked | Stacked bars |
| Cost | Line with pricing status | Line or stacked with pricing status | Not available |

When a user selects an invalid combination, the interface changes to the nearest valid choice. For example, changing from Tokens with Token type to Cost changes the breakdown to Total.

### Metric selector

Requests, Tokens, and Cost appear as tabs in the explorer header. Changing the metric changes the chart data, vertical-axis formatting, tooltip formatting, and available breakdowns.

Only the active metric dataset is fetched. The page must not issue requests for all three metric time series on initial load.

### Breakdown selector

Total displays one aggregate series for the selected global scope.

Model displays per-model series within the selected global scope:

- With explicit global model selections, display the selected models directly.
- With All models selected, display the top six models for the active metric plus an `Other` series.
- `Other` is derived from the aggregate minus the displayed model series for each bucket. Floating-point residue can make a derived bucket value fractionally negative; clamp derived values to zero.
- The aggregate is used to derive `Other` but is not drawn as an additional line in Model mode.
- The pricing status for a derived Cost `Other` series uses the aggregate `__all__` status as a conservative warning.
- If fewer than six models have data, display only the available models and omit `Other` when it is zero throughout the range. For Cost, treat a value below one tenth of a cent as zero for this omission test.

Token type is available only for Tokens. It displays input, output, cached, and reasoning tokens as stacked bars.

### Top-model ranking

Automatic model selection must match the active metric:

- Requests ranks by request count.
- Tokens ranks by total tokens (input plus output).
- Cost ranks by computed cost, considering only models whose pricing resolves. Models without pricing are excluded from the named Cost series and fall into `Other`; the conservative `Other` pricing status communicates that unpriced usage is present. If no model has pricing, the Cost breakdown displays the pricing-unavailable state instead of a ranked decomposition.

Ties rank the affected models lexicographically by name so the selection is deterministic.

The current behavior ranks by total tokens regardless of metric. That behavior must change so a Requests or Cost breakdown does not present a misleading model order.

### Granularity

Auto is the default. It selects the current range-appropriate bucket and accepts server coarsening.

Hour and Day allow an explicit preference. If the server coarsens Hour to Day for a wide range, the control displays Day as the effective granularity and communicates that the range was too wide for hourly buckets.

### Display mode

Line is the default for Total and Model breakdowns. Stacked is available for Model and required for Token type.

The display selector is hidden when only one display is valid.

### Zoom

The explorer supports zooming into a sub-range of the time axis, implemented with the established Chart.js zoom plugin rather than a hand-rolled interaction. This addresses ranges where long stretches of near-zero activity flatten the interesting part of the chart.

- Zoom is a client-side view change over already-fetched buckets. It does not refetch data and does not change the global time range.
- A visible reset control restores the full range. Resetting is also implied by any change to metric, breakdown, granularity, or global filters, which clears the zoom state.
- Zoom state is session-only and is not persisted.
- Zoom must be operable without a scroll-wheel gesture (for example through drag-to-zoom plus the reset control) so touch and keyboard-adjacent users are not locked out of the flattened-range fix.

### Legend and tooltips

- The legend is outside the chart canvas and supports toggling individual series locally.
- Toggling a series does not change global filters or refetch data.
- Model colors remain stable across metrics and reloads.
- Tooltips show the bucket, formatted value, and series name.
- Token-type tooltips show the component value together with the bucket's total tokens, where total tokens means input plus output as defined in Terminology. Cached and reasoning components appear in the stack and tooltip but do not contribute to the displayed total.
- Cost series with partial or missing pricing use the existing warning treatment and explanatory text.

### Empty, loading, and error states

- Loading keeps the explorer card height stable.
- No matching records displays `No data for the selected filters`.
- Cost without pricing displays `Pricing data unavailable`.
- A failed active explorer request displays an error inside the explorer without removing successfully loaded summary cards or tables.

## Detailed statistics

### User statistics

Rename `API Details` to `User Statistics` and label the identity column `User (API key)`.

Retain the existing fields:

- Requests.
- Input tokens.
- Output tokens.
- Total tokens.
- Cost and pricing status.
- Failed requests.
- Average latency.

Rows are API keys in redacted form. This section respects the global model and user filters.

### Model statistics

Retain the existing full model table and its fields. It respects the selected API-key users as well as the model filter.

### Provider credential statistics

Rename `Credential Stats` to `Provider Credential Statistics` to make its role explicit.

Rows continue to use `source`. This section reports upstream routing/account usage and is not a user list.

### Service health

Retain total requests, failures, failure rate, and latency percentiles. These values remain scoped by the global filters.

## API and data changes

### Existing endpoints retained

The design continues to use:

- `/api/overview`.
- `/api/timeseries`.
- `/api/token-breakdown`.
- `/api/api-stats`.
- `/api/model-stats`.
- `/api/credential-stats`.
- `/api/health`.
- `/api/models`.
- `/api/api-keys`.
- `/api/pricing`.

No response DTO or frontend API type needs a new field.

### Token totals count input plus output

Every token total the API reports must be computed as input tokens plus output tokens. Cached tokens are excluded. The stored `total_tokens` column comes from the upstream proxy and may include cached tokens, so aggregation queries must sum `input_tokens + output_tokens` instead of reading `total_tokens`.

This applies to the overview tokens total and sparkline, TPM, the `tokens` timeseries metric, token-based `top_n` ranking, and the `total_tokens` fields in the API-stat, model-stat, and credential-stat responses. Response field names do not change; only the computed values do. The database column and its collector-side meaning are untouched.

### Overview cost sparkline

`OverviewResponse.sparklines.cost` must contain real per-bucket cost values using the same pricing resolution and token split rules as `metric=cost` time series.

`Totals.cost` retains the existing missing-pricing rules. Cost sparkline points remain numeric, while the existing total `cost_status` communicates live, partial, or missing pricing for the scoped result.

### Metric-specific top-model ranking

The `top_n` behavior of `/api/timeseries` must rank models using the requested metric, following the ranking and pricing-exclusion rules in the Top-model ranking section. The response shape remains unchanged:

```ts
interface TimeseriesResponse {
  bucket: 'hour' | 'day';
  buckets: string[];
  series: Record<string, number[]>;
  series_status: Record<string, CostStatus>;
}
```

When `top_n` is requested, `__all__` still represents the complete filtered population. The additional model keys are the top models for the active metric. The frontend uses `__all__` to derive `Other`.

### Request strategy

On initial page load, fetch:

- Overview.
- Model and API-key filter options.
- Pricing metadata.
- The active explorer dataset only.
- User, model, provider-credential, and health statistics.

Fetch token breakdown only when Token type is active. Changing between line and stacked display does not refetch. Changing metric, breakdown data source, granularity, or global filters does refetch the active explorer data.

## Frontend architecture

Introduce one `UsageExplorer` feature that owns explorer controls, request selection, chart configuration, legend state, and explorer-specific empty/error handling.

The page continues to own global filters and the resolved time range. Global filter state is passed consistently to the explorer and detailed-statistics requests.

The existing chart-specific components are replaced after the explorer reaches feature parity:

- `UsageChart`.
- `TokenBreakdownChart`.
- `CostTrendChart`.

Shared chart registration, theme colors, tooltip styling, and model palette remain reusable.

No compatibility wrapper or parallel legacy chart path is retained after the replacement is complete.

## Responsive behavior

### Desktop

- Sidebar remains sticky and independently collapsible.
- Explorer controls fit in the card header or wrap to a second toolbar row.
- Explorer chart height is approximately 300 pixels.
- Detailed tables remain full width.

### Mobile

- Global filters remain available through the existing drawer pattern.
- Explorer controls wrap without horizontal page scrolling.
- Metric tabs remain directly visible.
- Secondary controls may use compact labels.
- Chart legend wraps and long model names truncate with accessible titles.
- Tables scroll within their cards.

## Accessibility

- Every control has a visible label or accessible name.
- Metric, breakdown, and display selectors expose pressed or selected state.
- The chart has a text summary containing metric, range, total, and active series names.
- Color is not the sole indicator of pricing status or series selection.
- Keyboard users can operate legend toggles and all filter controls.
- Loading and error updates use appropriate live-region behavior without repeatedly announcing chart hover changes.

## Implementation plan

1. Change backend token totals to input plus output, correct cost sparkline generation, and implement metric-specific top-model ranking so the explorer can rely on accurate existing contracts.
2. Add explorer state and request selection, then replace the separate requests, tokens, token-breakdown, and cost chart stack with the unified explorer, including zoom.
3. Update sidebar and table terminology so user always means API key and provider credential remains separate.
4. Preserve and reconnect every detailed statistics section under the same global filter state.
5. Remove the superseded chart components after the explorer satisfies the acceptance criteria.
6. Verify responsive, pricing-degraded, empty, and filtered states.

## Implementation contract

### Hard boundaries

- Do not change the SQLite schema or add migrations.
- Do not add or replace frontend dependencies, except the established Chart.js zoom plugin for explorer zoom.
- Do not add a new API endpoint or response field without review.
- Do not remove or condense the user, model, provider-credential, or service-health sections.
- Do not treat `source` as user identity. User identity is `api_key` only.
- Do not change authentication, redaction, pricing semantics, or URL base-path behavior.
- Do not introduce previous-period comparison, anomaly detection, or saved views as part of this work.

### Soft boundaries

- Component boundaries and file organization may change while preserving the agreed interfaces and behavior.
- Exact spacing, chart height, and control wrapping may be adjusted for responsive fit.
- Focused tests and pure formatting helpers may be added within the existing dependency set.

## Acceptance criteria

### Functional criteria

- Exactly one large time-series explorer is rendered on the usage page.
- Requests, Tokens, and Cost switch within that explorer.
- Total, Model, and valid Token type breakdowns render according to the combination table.
- All-model decomposition draws no more than six named models plus `Other`, and explicit model selection is capped at seven models.
- Automatic top models are ranked by the active metric, and Cost ranking excludes models without resolved pricing.
- Every reported token total equals input tokens plus output tokens; cached tokens are excluded.
- The explorer chart can be zoomed into a sub-range and reset, without refetching or changing global filters.
- Individual redacted API keys can be searched and selected in `Users (API keys)`.
- API-key selections scope overview, explorer, all detailed tables, and service health.
- User statistics display API keys, while provider credential statistics display sources.
- The full user, model, provider-credential, and health sections remain present.
- Only the active explorer dataset is requested.
- Cost sparklines and cost explorer values use real pricing calculations and retain missing-pricing warnings.
- Desktop and mobile layouts have no horizontal page overflow.

### Automated checks

- Frontend unit tests pass: `cd frontend && bun test`.
- Frontend lint passes: `cd frontend && bun run lint`.
- Frontend type-check and production build pass: `cd frontend && bun run build`.
- Usage route and aggregation tests pass: `uv run pytest tests/test_routes_usage.py tests/test_server_aggregate.py`.
- Full Python tests pass: `uv run pytest`.
- Python lint passes: `uv run ruff check`.
- Python type-check passes: `uv run basedpyright`.

Focused automated tests must cover:

- Explorer combination normalization.
- Active-dataset request selection.
- `Other` series derivation, including the clamp-to-zero rule.
- Metric-specific top-model ranking, including the Cost pricing-exclusion and tie-break rules.
- Token totals computed as input plus output across overview, timeseries, and stat endpoints.
- Real overview cost sparkline values.
- API-key filter propagation to every usage request.
- The distinction between user API-key labels and provider-credential labels.

## Deferred extensions

The following may be reconsidered after the unified explorer is in use:

- Previous-period overlays and deltas.
- Error-rate and latency time series.
- Anomaly markers.
- Saved explorer views.
- Click-to-filter chart series.
- API-key aliases or administrator-managed user labels.

These extensions require separate product decisions and are not pre-authorized by this design.
