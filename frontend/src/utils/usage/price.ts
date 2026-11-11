// Model price display for the models table (pure).
import type { ModelPrice, UnitPrices } from '@/types/api';

/** `$5`, `$0.50`, `$0.075`: whole dollars bare, otherwise 2 to 4 decimals. */
export function fmtUnitPrice(v: number): string {
  if (Number.isInteger(v)) return `$${v}`;
  return `$${v.toFixed(4).replace(/(\.\d\d\d*?)0+$/, '$1')}`;
}

export interface PriceCell {
  /** `input / output / cache read`, or `n/a` when the model is unpriced. */
  text: string;
  /** True when the model has long-context rates. */
  tiered: boolean;
  title: string | null;
}

const rates = (p: UnitPrices) => [p.input, p.output, p.cache_read].map(fmtUnitPrice).join(' / ');

/** The cache write rate is listed only when the model has cache writes in the range. */
export function priceCell(p: ModelPrice | null, hasCacheWrites: boolean): PriceCell {
  if (p === null) return { text: 'n/a', tiered: false, title: null };
  const long = p.long_context;
  const lines: string[] = [];
  if (long) {
    const scope = long.whole_request ? 'whole request' : 'excess only';
    lines.push(`Above ${long.threshold / 1000}k (${scope}): ${rates(long)}`);
  }
  if (hasCacheWrites) lines.push(`Cache write: ${fmtUnitPrice(p.cache_write)}`);
  return { text: rates(p), tiered: long !== null, title: lines.length > 0 ? lines.join('\n') : null };
}
