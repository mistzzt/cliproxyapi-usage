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

export function priceCell(p: ModelPrice | null): PriceCell {
  if (p === null) return { text: 'n/a', tiered: false, title: null };
  const long = p.long_context;
  const lines = [`Cache write: ${fmtUnitPrice(p.cache_write)}`];
  if (long) {
    const scope = long.whole_request ? 'whole request' : 'excess only';
    lines.unshift(`Above ${long.threshold / 1000}k (${scope}): ${rates(long)}`);
  }
  return { text: rates(p), tiered: long !== null, title: lines.join('\n') };
}
