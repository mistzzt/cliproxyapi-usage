# Plan: move the usage dashboard's data loading to TanStack Query

## The problem

The usage page shows numbers for a time range that you pick, such as "Last 30 days". The page has to turn that choice into an exact start time and end time before it can ask the server for data. For "Last 30 days", the end time is "now" and the start time is 30 days earlier. Ranges like this are rolling ranges, because they move with the current time. A custom range or "Last month" is a fixed range. A rolling range needs a value for "now".

Today the page keeps that value in its own piece of React state (`clock.now` in `frontend/src/hooks/useUsageViewState.ts`). The range itself lives in the URL. When you change the range, the page must update both of them. These are two separate updates, and React can apply them in two separate renders. Between them, the page has a new "now" but the old range, and it fetches data for that wrong combination. That is the double fetch that we found. The current fix wraps both updates in `startTransition`. It works only because React Router applies URL changes the same way, and nothing guarantees that.

The page loads its data with `useApi` (`frontend/src/hooks/useApi.ts`), a small fetch hook that we wrote ourselves. It has no cache and it cannot cancel a request. Each new feature, such as dimming a panel while it reloads, is more custom code to maintain.

This plan replaces `useApi` with TanStack Query, a library that loads and caches server data in React. It also removes the separate "now" state, so that the double fetch cannot happen at all. The plan covers the usage page and its detail drawer. The Limits page and `frontend/src/stores/quotaStore.ts` keep their current code.

## The idea

TanStack Query stores each response under a key. A key is a small value that names a request, for example "the summary for 1 Oct to 31 Oct". When a component asks for a key that TanStack Query has not seen, it sends the request. When it asks for a key that it has seen, it uses the stored response.

The plan uses that rule twice. First, a "window" is the exact start and end time that a range covers. The page gets the window from a small query that runs in the browser and does not call the server. Its key is the range that you picked and your timezone. When it runs, it reads the current time once and computes the window. This query is now the only place that holds the time.

Second, every request for data uses the window in its key, together with its other parameters (filters, bucket, and which breakdown or entity it asks for). Requests wait until the window exists. Because all requests use the same window, the summary, the tables, the heatmap, and the drawer always show the same time span. The URL holds only what you picked. View changes still replace the current browser history entry, as they do today.

## What happens when you use the page

When you open the page or pick a range, the window query reads the current time and computes the window. Then each panel sends its request. If you click the range that is already selected, nothing happens.

While a range stays selected, its window does not change. The page reads the current time again when you switch to a range that you used earlier. It also does this when you come back to the usage page from another page. It never reuses a window from an earlier visit, because "Last 30 days" must mean the last 30 days from now.

When you click Refresh, the page computes a new window and fetches every data request for the current view again, and also the filter options. This also happens when the new window is the same as the old one. That is the case for fixed ranges.

When you switch between the Models and Credentials tabs, the page fetches only the tab that you selected. If you return to a tab that you saw earlier in the same window, the page shows the stored response without a request.

Stored responses never expire on their own. The page fetches only for a new key or for Refresh. Focusing the browser window or reconnecting to the network does not fetch again. When a request fails, the page does not try it again. Each request passes the cancel signal that TanStack Query gives it (`AbortSignal`) to `fetch`, so the browser stops requests that the page no longer needs.

## Loading and errors

Each panel loads and fails on its own, as it does today. While a panel waits for new data, it keeps showing its previous data, dimmed and marked with `aria-busy="true"`. If its request fails, the panel shows its failure note. It never shows data from a different window in place of the failed data. The error banner at the top names each request that failed.

## Code changes

These types show the two kinds of key:

```ts
/** Key of the browser-only query that turns the range you picked into a window. */
type WindowKey = ['usage-window', { range: RangeSel; tz: string }];

/** The window that every request sends to the API. `start` null means all time. */
interface ResolvedWindow {
  start: string | null; // ISO instant
  end: string; // ISO instant
  tz: string;
}

/** Key of each data request: inside which window, what it asks for. The window comes first, so `['usage', window]` selects every request inside it. */
type UsageQueryKey = ['usage', ResolvedWindow, 'summary' | 'breakdown' | 'matrix', Record<string, string>];
```

Do the work in this order:

1. Add `@tanstack/react-query` and give the app one `QueryClient` with the caching and retry behavior that this plan describes.
2. Put the code that computes the window and builds the keys in a module in `frontend/src/utils/usage/` that has no React code, and test it with bun.
3. Move `UsagePage`, `DetailDrawer`, and `FilterBar` to the new queries. Delete `useApi.ts`, the clock and the refresh counter in `useUsageViewState`, and the `startTransition` workaround. Keep `Loaded` and the `Busy` styles, and feed them from the query state. The calendar reads today's date when the range picker opens, not from stored state.
4. Add the browser tests that the next section describes.
5. Update `AGENTS.md` with the new data loading and the command for the browser tests.

## Browser tests

The bugs that this plan fixes appear only in a real browser. So the plan adds Playwright tests in `e2e/`, written with pytest. They stay in the repo to stop the bugs from coming back.

On NixOS, the Playwright package from PyPI cannot start its browser driver. So the tests run under a Python from nixpkgs that the flake dev shell provides with `pytest` and `playwright`. That Python does not have the project's packages. The tests therefore use project code only through `uv run` subprocesses. One subprocess builds a test `usage.db` with made-up rows in the style of `write_usage_db`. The rows have timestamps relative to the current time, so every range that the tests pick contains data. Another subprocess starts `cliproxy-usage-server` with that database. The tests must never read `test/cpa-usage.tar.gz`, because it holds real data.

A normal `uv run pytest` must not collect these tests. They run with one command that `AGENTS.md` documents. That command builds the frontend first, so the tests never run against an old `frontend/dist/`.

Each test checks the requests that the page sends (their paths and their `start` and `end` values) or the page content:

1. A range change sends exactly one request for each visible data panel (summary, person breakdown, active tab breakdown, matrix), and no request for the previous range.
2. Refresh sends one request for each visible data panel and one request for the filter options. On a rolling range, the new `end` is later than the old one. On a custom range, `start` and `end` stay the same.
3. Pick a rolling range, then another range, then the first range again. The requests for the second visit have a later `end` than the requests for the first visit.
4. All requests for one view, including the requests of the open drawer, have the same `start` and `end`.
5. A tab switch sends one request. A switch back to a tab that you saw in the same window sends no request.
6. If the matrix request returns HTTP 500, the heatmap panel shows "Failed to load." and the banner names the heatmap. The other panels still show their data.
7. If a response is slow, each waiting panel keeps its previous rows on the page with `aria-busy="true"` until its own response arrives.

## Done when

All of these pass:

1. `rg -n "useApi|refreshToken|startTransition" frontend/src` prints nothing.
2. `nix develop -c bash -c 'cd frontend && bun test && bun run lint && bun run build'` passes.
3. `uv run pytest`, `uv run ruff check`, and `uv run basedpyright` pass.
4. `nix build` succeeds. A flake sees only files that git tracks, so run `git add` on new files first. You do not have to commit them.
5. The browser-test command in `AGENTS.md` passes, with every test above.
