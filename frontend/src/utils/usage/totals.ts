// Ratios derived from a Totals record (pure).
import type { Totals } from '@/types/api';
import { fmtCost } from './format';

type Counts = Pick<Totals, 'requests' | 'failed'>;

type TokenCounts = Pick<Totals, 'input_tokens' | 'output_tokens'>;

/** The UI's single definition of "tokens": input + output, cached excluded. */
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

/** `cachedInInput` (the model's `price.cached_in_input`): cached tokens are a subset of input, else reported separately. */
export function cacheHitRate(t: Pick<Totals, 'input_tokens' | 'cached_tokens'>, cachedInInput: boolean): number | null {
  const denom = cachedInInput ? t.input_tokens : t.input_tokens + t.cached_tokens;
  return denom > 0 ? t.cached_tokens / denom : null;
}

export const SPEND_LOWER_BOUND_NOTE = 'Some models have no pricing; spend is a lower bound.';

/** `n/a` when nothing is priced; a trailing `*` when only part of the usage is. */
export function fmtSpend(t: Pick<Totals, 'cost' | 'cost_status'>): string {
  if (t.cost === null) return 'n/a';
  return t.cost_status === 'live' ? fmtCost(t.cost) : `${fmtCost(t.cost)}*`;
}

/** Milliseconds as seconds with one decimal: `13.4s`. */
export function fmtLatency(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}
