/**
 * Explorer state machine (pure).
 *
 * The unified usage explorer owns four independent choices. Not every
 * combination is valid, so `normalizeExplorerState` snaps any state to the
 * nearest valid choice per the design's combination table:
 *
 * | Metric   | Total | Model          | Token type   |
 * | -------- | ----- | -------------- | ------------ |
 * | Requests | Line  | Line or stacked | Not available |
 * | Tokens   | Line  | Line or stacked | Stacked bars  |
 * | Cost     | Line  | Line or stacked | Not available |
 *
 * Rules distilled from the table:
 * - `token_type` is valid only for the `tokens` metric.
 * - `total` breakdown is always drawn as a single line.
 * - `token_type` breakdown is always drawn as stacked bars.
 * - `model` breakdown may be line or stacked.
 */

export type ExplorerMetric = 'requests' | 'tokens' | 'cost';
export type ExplorerBreakdown = 'total' | 'model' | 'token_type';
export type ExplorerGranularity = 'auto' | 'hour' | 'day';
export type ExplorerDisplay = 'line' | 'stacked';

export interface ExplorerState {
  metric: ExplorerMetric;
  breakdown: ExplorerBreakdown;
  granularity: ExplorerGranularity;
  display: ExplorerDisplay;
}

/** localStorage key, following the existing `usage.*.v1` convention. */
export const EXPLORER_STATE_KEY = 'usage.explorer.v1';

export const DEFAULT_EXPLORER_STATE: ExplorerState = {
  metric: 'requests',
  breakdown: 'total',
  granularity: 'auto',
  display: 'line',
};

const METRICS: readonly ExplorerMetric[] = ['requests', 'tokens', 'cost'];
const BREAKDOWNS: readonly ExplorerBreakdown[] = ['total', 'model', 'token_type'];
const GRANULARITIES: readonly ExplorerGranularity[] = ['auto', 'hour', 'day'];
const DISPLAYS: readonly ExplorerDisplay[] = ['line', 'stacked'];

/** Breakdowns available for a given metric. */
export function availableBreakdowns(metric: ExplorerMetric): ExplorerBreakdown[] {
  if (metric === 'tokens') return ['total', 'model', 'token_type'];
  return ['total', 'model'];
}

/** Display modes available for a given breakdown. */
export function availableDisplays(breakdown: ExplorerBreakdown): ExplorerDisplay[] {
  switch (breakdown) {
    case 'total':
      return ['line'];
    case 'model':
      return ['line', 'stacked'];
    case 'token_type':
      return ['stacked'];
  }
}

/**
 * Snap a (possibly invalid) explorer state to the nearest valid combination.
 * Pure and idempotent: `normalize(normalize(s)) === normalize(s)`.
 */
export function normalizeExplorerState(state: ExplorerState): ExplorerState {
  let breakdown = state.breakdown;

  // token_type only exists for the tokens metric.
  if (breakdown === 'token_type' && state.metric !== 'tokens') {
    breakdown = 'total';
  }

  // Display is a function of the breakdown for total/token_type; model keeps
  // whatever the user chose (line or stacked).
  let display = state.display;
  const displays = availableDisplays(breakdown);
  if (!displays.includes(display)) {
    display = displays[0]!;
  }

  return {
    metric: state.metric,
    breakdown,
    granularity: state.granularity,
    display,
  };
}

function isMetric(v: unknown): v is ExplorerMetric {
  return typeof v === 'string' && (METRICS as readonly string[]).includes(v);
}
function isBreakdown(v: unknown): v is ExplorerBreakdown {
  return typeof v === 'string' && (BREAKDOWNS as readonly string[]).includes(v);
}
function isGranularity(v: unknown): v is ExplorerGranularity {
  return typeof v === 'string' && (GRANULARITIES as readonly string[]).includes(v);
}
function isDisplay(v: unknown): v is ExplorerDisplay {
  return typeof v === 'string' && (DISPLAYS as readonly string[]).includes(v);
}

/**
 * Hydrate explorer state from an untrusted (persisted) value. Each field
 * falls back to its default when missing or invalid, then the whole state is
 * normalized so a stale persisted combination can never render an invalid UI.
 */
export function parseExplorerState(raw: unknown): ExplorerState {
  const obj = (typeof raw === 'object' && raw !== null ? raw : {}) as Record<string, unknown>;
  return normalizeExplorerState({
    metric: isMetric(obj.metric) ? obj.metric : DEFAULT_EXPLORER_STATE.metric,
    breakdown: isBreakdown(obj.breakdown) ? obj.breakdown : DEFAULT_EXPLORER_STATE.breakdown,
    granularity: isGranularity(obj.granularity)
      ? obj.granularity
      : DEFAULT_EXPLORER_STATE.granularity,
    display: isDisplay(obj.display) ? obj.display : DEFAULT_EXPLORER_STATE.display,
  });
}
