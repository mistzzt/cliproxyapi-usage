/**
 * Series math for the explorer (pure).
 *
 * Covers deriving the `Other` bucket in automatic model decomposition, summing
 * per-model series into a single total, and rolling up cost pricing statuses.
 */
import type { CostStatus } from '@/types/api';
import type { ExplorerMetric } from './explorerState';

/**
 * Cost values below one tenth of a cent are treated as zero when deciding
 * whether to omit an all-zero `Other` series. Non-cost metrics use a tiny
 * epsilon to absorb floating-point residue from the subtraction.
 */
const COST_ZERO_THRESHOLD = 0.001;
const COUNT_ZERO_THRESHOLD = 1e-9;

export function zeroThreshold(metric: ExplorerMetric): number {
  return metric === 'cost' ? COST_ZERO_THRESHOLD : COUNT_ZERO_THRESHOLD;
}

/** Element-wise sum of any number of equal-length series. Missing → 0. */
export function sumSeries(seriesList: number[][], length: number): number[] {
  const out = new Array<number>(length).fill(0);
  for (const series of seriesList) {
    for (let i = 0; i < length; i++) {
      out[i]! += series[i] ?? 0;
    }
  }
  return out;
}

/**
 * Derive the `Other` series for automatic model decomposition:
 * `other[i] = max(0, all[i] - sum(namedModels[i]))`.
 *
 * Floating-point residue can push a derived value fractionally negative, so
 * every value is clamped to zero.
 *
 * Returns `null` when `Other` is zero throughout the range (per the omission
 * rule), using the metric-appropriate near-zero threshold.
 */
export function deriveOtherSeries(
  allValues: number[],
  namedSeries: number[][],
  metric: ExplorerMetric,
): number[] | null {
  const namedTotals = sumSeries(namedSeries, allValues.length);
  const other = allValues.map((v, i) => {
    const residual = v - namedTotals[i]!;
    return residual > 0 ? residual : 0;
  });

  const threshold = zeroThreshold(metric);
  const hasSignal = other.some((v) => v >= threshold);
  return hasSignal ? other : null;
}

const STATUS_RANK: Record<CostStatus, number> = {
  live: 0,
  partial_missing: 1,
  missing: 2,
};

/**
 * Conservative rollup of several cost statuses: the worst status wins, so a
 * single unpriced contributor downgrades the combined status.
 */
export function rollupCostStatus(statuses: CostStatus[]): CostStatus {
  let worst: CostStatus = 'live';
  for (const s of statuses) {
    if (STATUS_RANK[s] > STATUS_RANK[worst]) worst = s;
  }
  return worst;
}
