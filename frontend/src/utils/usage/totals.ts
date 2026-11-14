// Ratios derived from a Totals record (pure).
import type { CostByType, Totals } from '@/types/api';
import { fmtCost } from './format';
import type { ShareItem } from './share';

type Counts = Pick<Totals, 'requests' | 'failed'>;

type TokenCounts = Pick<Totals, 'input_tokens' | 'output_tokens'>;

/** The UI's single definition of "tokens": all input (cache reads and writes included) + output. */
export function tokens(t: TokenCounts): number {
  return t.input_tokens + t.output_tokens;
}

/** `tokens` per bucket of aligned input and output series. */
export function tokenSeries(s: { input_tokens: readonly number[]; output_tokens: readonly number[] }): number[] {
  return s.input_tokens.map((input, i) => tokens({ input_tokens: input, output_tokens: s.output_tokens[i] ?? 0 }));
}

export function failureRate(t: Counts): number {
  return t.requests > 0 ? t.failed / t.requests : 0;
}

/** Share of input served from cache reads; null without input. */
export function cacheHitRate(t: Pick<Totals, 'input_tokens' | 'cache_read_tokens'>): number | null {
  return t.input_tokens > 0 ? t.cache_read_tokens / t.input_tokens : null;
}

/** Share of input written to the cache; null without input. */
export function cacheWriteShare(t: Pick<Totals, 'input_tokens' | 'cache_creation_tokens'>): number | null {
  return t.input_tokens > 0 ? t.cache_creation_tokens / t.input_tokens : null;
}

type LatencyCounts = Pick<Totals, 'requests' | 'failed' | 'latency_ms'>;

/** Mean latency of successful requests; null when none succeeded. */
export function avgLatency(t: LatencyCounts): number | null {
  const ok = t.requests - t.failed;
  return ok > 0 ? t.latency_ms / ok : null;
}

/** Output tokens per second of successful-request latency. */
export function outputSpeed(t: Pick<Totals, 'output_tokens' | 'latency_ms'>): number | null {
  return t.latency_ms > 0 ? t.output_tokens / (t.latency_ms / 1000) : null;
}

/** Share of output tokens spent on reasoning; stored reasoning is a subset of output. */
export function reasoningShare(t: Pick<Totals, 'output_tokens' | 'reasoning_tokens'>): number | null {
  return t.output_tokens > 0 ? t.reasoning_tokens / t.output_tokens : null;
}

export interface LongContext {
  spendShare: number;
  requestShare: number;
  avgContext: number;
}

/** Long-context figures; null when nothing was billed at long-context rates. */
export function longContext(t: Totals): LongContext | null {
  const ok = t.requests - t.failed;
  if (!t.long_context_cost || !t.cost || ok <= 0) return null;
  return {
    spendShare: t.long_context_cost / t.cost,
    requestShare: t.long_context_requests / ok,
    avgContext: t.input_tokens / ok,
  };
}

type CountSeries = Record<'requests' | 'failed', readonly number[]>;

/** Failure rate per bucket; null where a bucket has no requests. */
export function failureRateSeries(s: CountSeries): (number | null)[] {
  return s.requests.map((requests, i) => (requests > 0 ? (s.failed[i] ?? 0) / requests : null));
}

/** Mean successful-request latency per bucket; null where none succeeded. */
export function latencySeries(s: CountSeries & { latency_ms: readonly number[] }): (number | null)[] {
  return s.requests.map((requests, i) =>
    avgLatency({ requests, failed: s.failed[i] ?? 0, latency_ms: s.latency_ms[i] ?? 0 }),
  );
}

/** Spend by token type as share-bar items, on fixed palette slots; Input is the uncached part. */
export function spendByType(c: CostByType): ShareItem[] {
  return [
    { key: 'input', label: 'Input', value: c.input, slot: 0 },
    { key: 'output', label: 'Output', value: c.output, slot: 6 },
    { key: 'cache_read', label: 'Cache read', value: c.cache_read, slot: 2 },
    { key: 'cache_creation', label: 'Cache write', value: c.cache_creation, slot: 3 },
  ];
}

export const SPEND_LOWER_BOUND_NOTE = 'Some models have no pricing; spend is a lower bound.';

/** `n/a` when nothing is priced; a trailing `*` when only part of the usage is. */
export function fmtSpend(t: Pick<Totals, 'cost' | 'cost_status'>): string {
  if (t.cost === null) return 'n/a';
  return t.cost_status === 'live' ? fmtCost(t.cost) : `${fmtCost(t.cost)}*`;
}

/** Milliseconds as seconds with one decimal: `13.4s`; `–` when unknown. */
export function fmtLatency(ms: number | null): string {
  return ms === null ? '–' : `${(ms / 1000).toFixed(1)}s`;
}

/** Tokens per second, rounded: `64 tok/s`. */
export function fmtSpeed(tps: number | null): string {
  return tps === null ? '–' : `${tps.toFixed(0)} tok/s`;
}
