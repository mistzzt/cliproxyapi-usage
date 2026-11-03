// Per-row derivations for the rich rows table: metric values, mix bars, trend peaks (pure).
import type { BreakdownRow, MixEntry, ModelPrice, Totals } from '@/types/api';
import { type Change, change, fmtPct } from './format';
import { cacheHitRate, failureRate, tokenSeries, tokens } from './totals';
import type { Dimension } from '@/types/api';
import type { Metric } from './viewState';

export type MetricValues = Pick<Totals, 'cost' | 'input_tokens' | 'output_tokens'>;

/** Unpriced cost (null) counts as zero. */
export function metricValue(v: MetricValues, metric: Metric): number {
  return metric === 'cost' ? (v.cost ?? 0) : tokens(v);
}

export type Trend = BreakdownRow['trend'];

/** A trend's per-bucket values for the active metric. */
export function metricSeries(trend: Trend, metric: Metric): number[] {
  return metric === 'cost' ? trend.cost : tokenSeries(trend);
}

export interface MixPart<T> {
  entry: T;
  value: number;
  /** Fraction of the row's mix total. */
  share: number;
}

/** Mix entries valued by the active metric, zeros dropped, largest first. */
export function mixParts<T extends MetricValues>(entries: readonly T[], metric: Metric): MixPart<T>[] {
  const valued = entries.map((entry) => ({ entry, value: metricValue(entry, metric) })).filter((p) => p.value > 0);
  const total = valued.reduce((acc, p) => acc + p.value, 0);
  return valued.sort((a, b) => b.value - a.value).map((p) => ({ ...p, share: p.value / total }));
}

/** `opus-4.6 62% · sonnet-4.5 30% · +3`. */
export function mixCaption<T>(parts: readonly MixPart<T>[], name: (entry: T) => string, top = 2): string {
  const named = parts.slice(0, top).map((p) => `${name(p.entry)} ${fmtPct(p.share)}`);
  if (parts.length > top) named.push(`+${parts.length - top}`);
  return named.join(' · ');
}

/** First bucket holding the maximum, or null when every bucket is zero. */
export function trendPeak(values: readonly number[]): { index: number; value: number } | null {
  let index = -1;
  let value = 0;
  values.forEach((v, i) => {
    if (v > value) {
      index = i;
      value = v;
    }
  });
  return index < 0 ? null : { index, value };
}

export interface RichRowInput {
  key: string;
  label: string;
  current: Totals;
  previous: Totals | null;
  trend: Trend;
  mix: MixEntry[];
  price: ModelPrice | null;
}

export interface RichRow {
  key: string;
  label: string;
  totals: Totals;
  /** Active metric value's fraction of the sum over all rows. */
  share: number;
  change: Change;
  trend: number[];
  peak: { index: number; value: number } | null;
  parts: MixPart<MixEntry>[];
  fail: number;
  /** Models only. */
  cacheHit: number | null;
  price: ModelPrice | null;
  /** Epoch ms. */
  lastUsed: number | null;
}

/** Derives the displayed columns; rows with no requests and no value are dropped. */
export function buildRichRows(rows: readonly RichRowInput[], dim: Dimension, metric: Metric): RichRow[] {
  const kept = rows.filter((r) => r.current.requests > 0 || metricValue(r.current, metric) > 0);
  const total = kept.reduce((acc, r) => acc + metricValue(r.current, metric), 0);
  return kept.map((r) => {
    const value = metricValue(r.current, metric);
    const trend = metricSeries(r.trend, metric);
    return {
      key: r.key,
      label: r.label,
      totals: r.current,
      share: total > 0 ? value / total : 0,
      change: change(value, r.previous ? metricValue(r.previous, metric) : null),
      trend,
      peak: trendPeak(trend),
      parts: mixParts(r.mix, metric),
      fail: failureRate(r.current),
      cacheHit: dim === 'model' ? cacheHitRate(r.current, r.price?.cached_in_input ?? false) : null,
      price: dim === 'model' ? r.price : null,
      lastUsed: r.current.last_used === null ? null : Date.parse(r.current.last_used),
    };
  });
}

export type SortKey = 'name' | 'share' | 'cost' | 'tokens' | 'change' | 'fail' | 'cacheHit' | 'latency' | 'lastUsed';

export interface SortState {
  key: SortKey;
  desc: boolean;
}

export const DEFAULT_SORT: SortState = { key: 'share', desc: true };

// Missing values (null) sort below every real value.
function sortValue(r: RichRow, key: Exclude<SortKey, 'name'>): number {
  switch (key) {
    case 'share':
      return r.share;
    case 'cost':
      return r.totals.cost ?? -Infinity;
    case 'tokens':
      return tokens(r.totals);
    case 'change':
      return r.change === 'new' ? Infinity : (r.change ?? -Infinity);
    case 'fail':
      return r.fail;
    case 'cacheHit':
      return r.cacheHit ?? -Infinity;
    case 'latency':
      return r.totals.avg_latency_ms;
    case 'lastUsed':
      return r.lastUsed ?? -Infinity;
  }
}

/** Stable sort; `name` compares the given display names. */
export function sortRichRows(rows: readonly RichRow[], sort: SortState, name: (r: RichRow) => string = (r) => r.label): RichRow[] {
  const { key } = sort;
  const cmp =
    key === 'name'
      ? (a: RichRow, b: RichRow) => name(a).localeCompare(name(b))
      : (a: RichRow, b: RichRow) => {
          const x = sortValue(a, key);
          const y = sortValue(b, key);
          return x === y ? 0 : x < y ? -1 : 1;
        };
  return [...rows].sort((a, b) => (sort.desc ? -cmp(a, b) : cmp(a, b)));
}

/** Clicking the active column flips direction; a new column starts descending (names ascending). */
export function nextSort(current: SortState, key: SortKey): SortState {
  return current.key === key ? { key, desc: !current.desc } : { key, desc: key !== 'name' };
}
