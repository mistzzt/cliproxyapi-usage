// Number, cost, percentage, and change formatting for the usage dashboard (pure).

export type FormatMetric = 'cost' | 'tokens' | 'requests';

// Two decimals below 10, one above; `toFixed` rounding can cross either boundary.
function scaled(x: number): string {
  const two = x.toFixed(2);
  return Number(two) >= 10 ? x.toFixed(1) : two;
}

const COMPACT_UNITS = [
  { div: 1e3, suffix: 'K' },
  { div: 1e6, suffix: 'M' },
  { div: 1e9, suffix: 'B' },
] as const;

/** `950` -> `950`, `52_310` -> `52.3K`, `38_420_000` -> `38.4M`. */
export function fmtCompact(v: number): string {
  const abs = Math.abs(v);
  if (Math.round(abs) < 1000) return `${Math.round(v) || 0}`;
  const sign = v < 0 ? '-' : '';
  let i = COMPACT_UNITS.findLastIndex((u) => abs >= u.div);
  if (i < 0) i = 0;
  for (; ; i++) {
    const unit = COMPACT_UNITS[i]!;
    const text = scaled(abs / unit.div);
    if (Number(text) < 1000 || i === COMPACT_UNITS.length - 1) return `${sign}${text}${unit.suffix}`;
  }
}

/** `$4.27`, `$1,534`, `$184K`. */
export function fmtCost(v: number): string {
  if (v >= 100_000) return `$${(v / 1000).toFixed(0)}K`;
  if (v >= 100) return `$${Math.round(v).toLocaleString('en-US')}`;
  return `$${v.toFixed(2)}`;
}

export function fmtMetric(v: number, metric: FormatMetric): string {
  return metric === 'cost' ? fmtCost(v) : fmtCompact(v);
}

/** Fraction to percent: whole numbers from 10%, one decimal below, `<0.1%` for tiny positives. */
export function fmtPct(v: number): string {
  if (v > 0 && v < 0.001) return '<0.1%';
  return `${(v * 100).toFixed(Math.abs(v) >= 0.1 ? 0 : 1)}%`;
}

/** Signed relative change; `new` when growing from zero, null when there is no previous period. */
export type Change = number | 'new' | null;

export function change(current: number, previous: number | null | undefined): Change {
  if (previous == null) return null;
  if (previous > 0) return (current - previous) / previous;
  return current > 0 ? 'new' : 0;
}

export type ChangeDirection = 'up' | 'down' | 'flat' | 'new';

export interface ChangeDisplay {
  direction: ChangeDirection;
  /** Unsigned magnitude (`25%`, `3.2×`); `new` when the previous value was zero. */
  text: string;
}

const FLAT_EPSILON = 0.005;

/** At or above +100% the change reads as a multiple of the previous value. */
export function fmtChange(value: number | 'new'): ChangeDisplay {
  if (value === 'new') return { direction: 'new', text: 'new' };
  const direction = value > FLAT_EPSILON ? 'up' : value < -FLAT_EPSILON ? 'down' : 'flat';
  if (value < 1) return { direction, text: fmtPct(Math.abs(value)) };
  const ratio = 1 + value;
  const one = ratio.toFixed(1);
  return { direction, text: `${Number(one) >= 10 ? ratio.toFixed(0) : one}×` };
}
