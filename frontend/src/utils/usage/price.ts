// LiteLLM list price display for the models table (pure).
import type { ModelPrice } from '@/types/api';

/** `$5`, `$0.50`, `$0.075`: whole dollars bare, otherwise 2 to 4 decimals. */
export function fmtUnitPrice(v: number | null): string {
  if (v === null) return 'n/a';
  if (Number.isInteger(v)) return `$${v}`;
  return `$${v.toFixed(4).replace(/(\.\d\d\d*?)0+$/, '$1')}`;
}

export interface PriceCell {
  /** `input / output / cache read`, or `n/a` when the model is unpriced. */
  text: string;
  /** True when either above-200k rate is set. */
  tiered: boolean;
  title: string | null;
}

export function priceCell(p: ModelPrice | null): PriceCell {
  if (p === null) return { text: 'n/a', tiered: false, title: null };
  const text = [p.input, p.output, p.cache_read].map(fmtUnitPrice).join(' / ');
  const tiered = p.input_above_200k !== null || p.output_above_200k !== null;
  const lines = [`Cache write: ${fmtUnitPrice(p.cache_write)}`];
  if (tiered) {
    lines.unshift(`Above 200k: ${fmtUnitPrice(p.input_above_200k)} / ${fmtUnitPrice(p.output_above_200k)}`);
  }
  return { text, tiered, title: lines.join('\n') };
}
