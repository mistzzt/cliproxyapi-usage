// Query keys for the usage page: the browser-computed window, and each API request made inside it (pure).
import type { BreakdownResponse, Dimension, MatrixResponse, SummaryResponse } from '@/types/api';
import { resolveWindow, viewerTimeZone, type RangeSel } from './range';
import { FILTER_FIELD, type ViewState } from './viewState';

/** Key of the browser-only query that turns the picked range into a window. */
export type WindowKey = readonly ['usage-window', { range: RangeSel; tz: string }];

/** The window that every request sends to the API. `start` null means all time. */
export interface ResolvedWindow {
  start: string | null;
  end: string;
  tz: string;
}

export interface UsageResponses {
  summary: SummaryResponse;
  breakdown: BreakdownResponse;
  matrix: MatrixResponse;
}

export type UsageEndpoint = keyof UsageResponses;

/** Key of each data request: inside which window, what it asks for (its query params minus the window). */
export type UsageQueryKey<E extends UsageEndpoint = UsageEndpoint> = readonly [
  'usage',
  ResolvedWindow,
  E,
  Readonly<Record<string, string>>,
];

/** Key of the filter options request, which has no window. */
export const OPTIONS_KEY = ['usage-options'] as const;

export function windowKey(range: RangeSel, tz: string = viewerTimeZone()): WindowKey {
  return ['usage-window', { range, tz }];
}

/** The key's range resolved at `now`. */
export function computeWindow([, { range, tz }]: WindowKey, now: number): ResolvedWindow {
  const { start, end } = resolveWindow(range, now);
  return { start: start === null ? null : new Date(start).toISOString(), end: new Date(end).toISOString(), tz };
}

/** Comma-joined filter params; unfiltered dimensions are omitted. */
function filterParams(s: ViewState): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of Object.values(FILTER_FIELD)) if (s[field].length > 0) out[field] = s[field].join(',');
  return out;
}

export function summaryKey(w: ResolvedWindow, s: ViewState): UsageQueryKey<'summary'> {
  return ['usage', w, 'summary', { ...filterParams(s), bucket: s.granularity }];
}

export function breakdownKey(w: ResolvedWindow, s: ViewState, dim: Dimension): UsageQueryKey<'breakdown'> {
  return ['usage', w, 'breakdown', { ...filterParams(s), bucket: s.granularity, dim }];
}

/** People x models; no `bucket` since the matrix has no time axis. */
export function matrixKey(w: ResolvedWindow, s: ViewState): UsageQueryKey<'matrix'> {
  return ['usage', w, 'matrix', { ...filterParams(s), rows: 'person', cols: 'model' }];
}

/** Query string params of the request that `key` names. */
export function requestParams([, w, , params]: UsageQueryKey): Record<string, string> {
  return { ...(w.start === null ? {} : { start: w.start }), end: w.end, tz: w.tz, ...params };
}

/** Prefix of every request key inside window `w`. */
export function windowPrefix(w: ResolvedWindow): readonly ['usage', ResolvedWindow] {
  return ['usage', w];
}
