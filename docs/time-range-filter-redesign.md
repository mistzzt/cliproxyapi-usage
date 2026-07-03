# Configurable time ranges: calendar periods + custom range picker

## Context

The usage dashboard's time filter only offers **rolling** windows (`7h`, `24h`, `7d`, `all`) chosen from a single `<Select>` dropdown. The user wants:

- **Calendar** day / week / month (not rolling), with the ability to step back to *past* periods.
- Keep rolling `7h` and `24h`.
- Drop the rolling `7d` (least useful).
- A control better suited to the richer option set + a custom date range.

Decisions already made with the user:

- **Control**: preset chips (`7h`, `24h`, `Today`, `This week`, `This month`, `All`) + a `Custom` chip opening a 2-month calendar range picker. When a calendar chip is active, a `‹ label ›` stepper appears to navigate to prior periods.
- **Navigation**: prev/next past periods for calendar ranges (no future).
- **Timezone**: calendar boundaries computed in the **browser-local** timezone.
- **Week start**: Sunday.
- **No hand-rolling**: use established libraries for the date math, the calendar control, and the popover (see Dependencies).

> Note: the project `CLAUDE.md` frontend blurb is stale — the app is **React 19** and does **not** use `react-i18next` (nothing in `frontend/src` imports it). No i18n plumbing is involved in this change; user-facing strings follow the existing inline pattern.

Outcome: users can scope the dashboard to a specific calendar day/week/month (including past ones) or an arbitrary custom range, while keeping the quick rolling windows.

## Architectural decision: frontend computes concrete instants

Today the backend owns the enum→window map (`db.py:range_window`) and derives sparkline bucketing from the enum (`usage.py:_SPARKLINE_BUCKET`). Calendar boundaries depend on the viewer's timezone, which only the browser knows. **The frontend resolves the active range into concrete `start`/`end` UTC instants** (using local time for calendar math) and sends those. This makes the backend range-agnostic for *filtering* and future-proofs any new range shape without touching route signatures again.

**Timezone also affects bucket grouping, not just window edges.** The dense buckets are grouped/labeled by UTC calendar day via `strftime('%Y-%m-%d', timestamp)` (`aggregate.py:245-248,348,413,456`) and the label generators `_hour_labels`/`_day_labels` (`aggregate.py:202-221`) floor to UTC midnight/hour. For a non-UTC viewer, a "calendar week/month" window (which uses **day** buckets) would emit UTC-dated columns that straddle local days — a single local day split across two `YYYY-MM-DD` columns, edge columns mis-attributed. To keep calendar semantics honest in the charts, the frontend also sends **`tz_offset_minutes`** (from `-new Date().getTimezoneOffset()`), and the day/hour bucket grouping + label generation apply that offset (SQLite supports a `strftime(..., '<±HH:MM>')` modifier; the Python label floor mirrors it). This is a single scalar param, not full IANA tz plumbing. DST caveat: one fixed offset per request is used; a month window crossing a DST boundary can be off by an hour at the seam — acceptable, documented.

Per project convention this is a clean schema change (no backward-compat shim for the old `range` param).

## Dependencies (new)

Add to `frontend/package.json` (installed with `bun add`):

- **`date-fns`** — all date math: period boundaries (`startOfDay`/`startOfWeek`/`startOfMonth` with `{ weekStartsOn: 0 }`), stepping (`addDays`/`addWeeks`/`addMonths`, negated to go back), and label formatting (`format`). Tree-shakeable; only used functions are bundled. It also ships transitively with react-day-picker, but declare it directly since the resolver uses it.
- **`react-day-picker`** (pin **`^10`**) — the calendar control for `Custom` range selection. Use `mode="range"` + `weekStartsOn={0}` (Sunday), `numberOfMonths={2}`, `disabled={{ after: today }}`, `selected`/`onSelect`, and the `DateRange` type — all intact in v10 (verified against `react-day-picker@10.x`). v10 is a cleanup release; going straight to it avoids a later migration. Two v10 specifics to honor:
  - **Package namespace**: v10 introduces `@daypicker/react` (the `react-day-picker` name still resolves for compat). Prefer importing from `@daypicker/react` for a new integration; keep imports consistent.
  - **Renamed `classNames` element keys**: style via the `classNames` prop with a local SCSS module using v10 keys — `month_grid` (was `table`), `button_previous`/`button_next` (was `nav_button`), `selected` (was `day_selected`), `disabled` (was `day_disabled`), etc. Do not rely on removed v9 compatibility keys. Either skip the default stylesheet or import and override it (`@daypicker/react/style.css`), matching the app light/dark theme.
- **`@radix-ui/react-popover`** — accessible popover wrapper for the `Custom` calendar (focus management, click-outside, Escape, positioning). React 19 compatible. This replaces hand-rolling popover behavior; the existing hand-rolled focus-trap in `FilterSidebar` stays for the mobile *drawer* and is out of scope.

Alternative considered: skip the popover lib and render the calendar as an **inline collapsible panel** below the chips (no `@radix-ui/react-popover`, more robust inside the mobile drawer). Recommended path is the Radix popover per the user's explicit ask; fall back to inline only if popover positioning inside the drawer proves fiddly.

## Backend changes (`src/cliproxy_usage_server/`)

**`routes/usage.py`** — the 7 range-consuming endpoints (`overview`, `timeseries`, `token-breakdown`, `api-stats`, `model-stats`, `credential-stats`, `health`):

- Replace the `_RangeParam` (`range` enum) query param with three params:
  - `start`: optional ISO-8601 instant (absent = open start, i.e. "all time").
  - `end`: ISO-8601 instant (default server `now` if absent).
  - `tz_offset_minutes`: optional int (default `0` = UTC), used only for day/hour bucket grouping/labeling (see Architectural decision).
- FastAPI parses ISO strings typed as `datetime` into tz-aware `datetime`s. **Reject naive datetimes** (no `Z`/offset) with 422 rather than letting `.astimezone(UTC)` assume the server's local tz — the frontend always emits `Z`, so this only guards misuse. The existing `usage.py:186-199` / `260-277` blocks are the inline `datetime → SQL-string` conversion (the `_range_where` replication with the `MIN(timestamp)` fallback for `None`), not ISO parsing; feed the parsed `(start, end)` straight into them. All downstream aggregation already accepts `(start: datetime | None, end: datetime)`.
- Validate `start <= end`; return HTTP 422 on ordering violation (FastAPI won't check this). `start == end` yields an empty half-open window (may emit one all-zero bucket from the label generators — benign).
- **Sparkline bucket** (`overview`): replace the `_SPARKLINE_BUCKET[range_]` lookup with a span-derived rule: **if `start is None` → `"day"`** (open-ended `all`, no span to measure); else span `<= 48h` → `"hour"`, else `"day"`. Yields hour buckets for `7h`/`24h`/calendar-day and day buckets for week/month/all, matching current behavior. `timeseries`/`token-breakdown` keep their explicit `bucket` param.
- **Bucket-count guard (new, important).** `_hour_labels`/`_day_labels` (`aggregate.py:202-221`) are unbounded `while cur < end` loops. Making arbitrarily wide `Custom`/`All` ranges first-class means a user can pick a 31-day range (or all-time spanning months) and flip the `period` toggle to **hour**, generating 744+ (or 10k+) labels, SQL groups, and Chart.js points. Add a guard: when the requested `bucket="hour"` window exceeds a cap (e.g. > ~10 days / ~240 buckets), **auto-coarsen to `"day"`** (server-side, and reflect it in the response so the toggle shows the effective bucket). **Open-start blind spot:** for `All`+hour there is no `start`, so span is unmeasurable and a naive span check can't fire — this is the worst runaway (all-time hourly). Treat **`start is None && bucket=="hour"` as auto-coarsen-to-day unconditionally** (equivalently: resolve `start` via `MIN(timestamp)` before the span check). Applies to `timeseries`/`token-breakdown`; the `overview` span rule already avoids this.

**`db.py`**:

- Remove `range_window` and `_RANGE_DELTAS` (no longer referenced once routes send explicit instants). Extract shared helpers as needed: bucket-by-span selection, the bucket-count guard, and the tz-offset-aware label/`strftime` logic (these are used by multiple endpoints).
- **Stale docstrings to fix:** the `usage.py:1-21` module docstring and the `overview` docstring (`:418-424`) hard-code `7h/24h/7d/all` and claim "all → day buckets, capped at 30." That 30-cap is **not implemented** (`_bucket_labels` with `start=None` walks `MIN(timestamp)`→`now` uncapped, `aggregate.py:231-238`). Update these to the new contract; "all" is uncapped day buckets over full history (day granularity is naturally bounded; the hour explosion is what the new guard addresses).

**`aggregate.py`**:

- Thread `tz_offset_minutes` (and the coarsened effective bucket) into the day/hour bucket queries. The `strftime('%Y-%m-%d', timestamp)` / `strftime('%Y-%m-%dT%H:00:00', timestamp)` calls (`aggregate.py:245-248,348,413,456`) take a `'<±HH:MM>'` modifier to shift into local time; the `_hour_labels`/`_day_labels` floor math must mirror the same offset so dense zero-fill columns line up with the shifted groups. Keep the dense zero-fill contract (missing intervals → 0) unchanged.

**`routes/usage.py` — cost path (do not miss this).** The `timeseries?metric=cost` / `token-breakdown` cost branch does **its own** bucketing separate from `aggregate.py`: `_query_bucket_model_costs` (`usage.py:171-245`) has `strftime('{bucket_fmt}', timestamp)` at `:218`, and the `bfmt` string is built inline at `:543`. It fetches dense labels from the aggregate query and maps its own `bkt` cell keys onto those labels. **`tz_offset_minutes` and the coarsened effective bucket must be threaded here too** — if the offset/coarsening is applied to the aggregate labels but not to `_query_bucket_model_costs`/`bfmt`, the keys won't match the labels and every cost series silently zeroes out. Update `_bucket_fmt`/`bfmt` construction to include the offset modifier and use the same effective bucket.

**Schemas** (`schemas.py`): if the bucket-count guard can auto-coarsen `hour → day`, add an effective-`bucket` field to `TimeseriesResponse`/`TokenBreakdownResponse` so the frontend can show the bucket actually used. Keep `frontend/src/types/api.ts` in sync (hand-maintained per the shared-schema rule). Other DTOs unaffected except the `Range` type (below).

## Frontend changes (`frontend/`)

**Range model** — replace `types/api.ts:Range` (`'7h' | '24h' | '7d' | 'all'`) with a discriminated union describing the *selection*, plus a resolver to instants:

```ts
type RangeSpec =
  | { kind: 'rolling'; preset: '7h' | '24h' }
  | { kind: 'all' }
  | { kind: 'calendar'; unit: 'day' | 'week' | 'month'; anchor: string } // anchor = ISO date of period start, local
  | { kind: 'custom'; startDate: string; endDate: string };              // local calendar dates, inclusive
```

A pure resolver `resolveRange(spec, now): { start?: string; end: string; tzOffsetMinutes: number }` produces UTC ISO instants + the tz offset (`-now.getTimezoneOffset()`), using **date-fns** for all boundary math (local-time by default, which is what we want):
- `rolling` → `subHours(now, 7|24)` .. `now`.
- `all` → `{ end: now.toISOString() }` (no `start`).
- `calendar` → `startOf{Day,Week,Month}(anchor)` .. `startOf…(add{Days:1,Weeks:1,Months:1}(anchor))`, converted to UTC via `.toISOString()`. Week uses `startOfWeek(d, { weekStartsOn: 0 })` / `endOfWeek` (**Sunday**).
- `custom` → `startOfDay(startDate)` .. `endOfDay(endDate)` → ISO.

The stepper's prev/next uses the matching `add{Days,Weeks,Months}(anchor, ±1)`; "at current period" (next disabled) is `isSameDay/Week/Month(anchor, now)`.

**`services/api.ts`** — the 7 `get*` functions currently take `{ range: Range }`. Change them to accept resolved `{ start?: string; end: string }` (call `resolveRange` in `UsagePage`, or pass `RangeSpec` and resolve inside `api.ts`). `buildQuery` already drops `undefined`, so an absent `start` naturally yields open-start. Bucket/period params for `timeseries`/`token-breakdown` unchanged.

**`pages/UsagePage.tsx`**:
- Replace `useLocalStorage<Range>('usage.range.v1', '24h')` with `useLocalStorage<RangeSpec>('usage.range.v2', { kind: 'rolling', preset: '24h' })`. **Persist the full spec verbatim** (including a stepped-back `anchor`) — do *not* force re-anchor on load, which would silently discard a stepped-back position. Instead derive "is this the live current period" at render by comparing `anchor` to `now`'s period (`isSameDay/Week/Month`); the matching chip highlights only when the anchor equals the current period, and the stepper shows whatever stored anchor was persisted. Result: `This month` clicked → current month; reload while stepped back to June → still June, no chip lit as "live".
- `defaultPeriod(spec)` (hour vs day for the timeseries `period` toggle) → derive from `RangeSpec`: `day` for week/month/all and multi-day custom; `hour` otherwise. Note the server may still auto-coarsen hour→day for wide windows (bucket guard); honor the response's effective bucket.
- Recompute `now` once per load/refresh and thread it (or compute inside resolver at call time).

**`components/usage/FilterSidebar.tsx`** — replace the `RANGE_OPTIONS` `<Select>` block with a new range control:
- Chip row: `7h`, `24h`, `Today`, `This week`, `This month`, `All`, `Custom`. Active chip highlighted. Reuse `Button`/existing UI primitives and SCSS-module styling; add chip styles to `FilterSidebar.module.scss`.
- When `kind === 'calendar'`, render a `‹ label ›` stepper below the chips. Label formats per unit via `date-fns` `format` (`MMM d, yyyy` / `MMM d – MMM d` / `MMMM yyyy`). `‹` decrements the anchor by one unit; `›` increments, **disabled** when the anchor is the current period (no future data). Plain inline strings (no i18n).
- `Custom` opens a `@radix-ui/react-popover` anchored to the chip, containing the day-picker calendar from `@daypicker/react` (`mode="range"`, `weekStartsOn={0}`, `numberOfMonths={2}`, `disabled={{ after: today }}`). Confirm/apply the selected `DateRange` → `{ kind: 'custom', startDate, endDate }`. Style the calendar via `classNames` (v10 element keys) in a local SCSS module honoring the app's light/dark theme tokens.
- Keep the mobile drawer / collapse behavior intact; the control just swaps in for the current `Range` block.

Update `FilterSidebarProps` (`range: Range` → `range: RangeSpec`, `onRangeChange: (r: RangeSpec) => void`).

**Strict-tsconfig notes** (`exactOptionalPropertyTypes`, `verbatimModuleSyntax`, `noUncheckedIndexedAccess` all on): `resolveRange`'s `all` branch must **omit** `start` (return `{ end, tzOffsetMinutes }`), never set `start: undefined`. Import the day-picker `DateRange` type (`@daypicker/react`) with `import type`. `buildQuery`'s `Record<string, string | undefined>` already accepts the optional param, so plumbing typechecks.

## Tests

- **`tests/test_server_db.py`** — remove the `range_window` tests (`test_range_window_*`) and the `range_window` import. Add unit tests for the extracted helpers: bucket-by-span (incl. `start is None → day` and the 48h threshold) and the bucket-count guard (hour auto-coarsens to day past the cap).
- **Route tests (`tests/test_routes_usage.py`)** — **all ~37 `range=` occurrences must be rewritten** to send explicit `start`/`end`, not just "any that pass range." Critical: FastAPI **silently ignores unknown query params**, so a leftover `range=24h` will NOT 422 — it returns all-time data and the assertion fails confusingly (e.g. `test_overview_sparkline_length` expects 24-25 hour buckets; `test_range_invalid_returns_422` loses its premise entirely). Rewrite each, and add coverage for: open-start (`all`, no `start`), explicit window, `start > end` → 422, naive-datetime → 422, and tz-offset bucket alignment (a non-zero `tz_offset_minutes` shifts day-bucket boundaries).
- **Frontend** — add unit tests for `resolveRange` (each kind, Sunday week-start boundaries, month boundaries) and stepper anchor math if a test setup exists (the repo is bun/vite; no frontend test runner is currently configured — either add one via `bun test`/vitest or rely on the type-checker + manual verification).

## Verification

1. `uv run pytest -q` — backend + route tests green.
2. `uv run ruff check` and `uv run basedpyright` — clean (note `py314` target).
3. `cd frontend && bun run build` — type-checks (strict) and builds.
4. Manual end-to-end via `uv run cliproxy-usage-server` (or `bun run dev` proxying to `:8318`):
   - Click each chip; confirm charts/totals update and the network requests carry the expected `start`/`end` (DevTools).
   - `This month` then step `‹` to a prior month; confirm data shifts and `›` disables at the current month.
   - `Custom` a wide range (e.g. 31 days) and flip the `period` toggle to hour; confirm the server auto-coarsens to day (no 700+ point series) and the effective bucket is reflected in the UI.
   - Confirm calendar "Today"/"This week"/"This month" buckets align to your **local** day boundaries, not UTC (test in a non-UTC tz, e.g. set the browser/OS to UTC-8, and verify week columns don't straddle days).
   - Reload while stepped back to a prior month: confirm it stays on that month (position not lost) and no chip is lit as "live".

## Open items to confirm during implementation

- **Timezone-correct buckets vs. simplicity**: recommended path threads `tz_offset_minutes` through bucket grouping so calendar week/month columns align to local days. The simpler fallback is UTC-aligned buckets (drop `tz_offset_minutes`, document that week/month columns are UTC days). Default is the tz-correct path since calendar-local semantics are the feature's whole point.
- **Bucket-count cap value** (proposed ~240 hour buckets / ~10 days before auto-coarsening) — tune to taste.
- Radix popover vs. inline collapsible calendar panel (see Dependencies) — default is the Radix popover.
- Whether to add a frontend test runner (vitest) for `resolveRange`, or cover it by types + manual verification only.
