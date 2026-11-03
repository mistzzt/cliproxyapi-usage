// Stacked over-time series: slotted entities in slot order, the rest summed into one "Other" layer (pure).
import type { BreakdownResponse, Bucket } from '@/types/api';
import { displayName, type EntityLookup } from './entities';
import { metricSeries } from './richRows';
import type { Metric } from './viewState';

export interface StackInput {
  label: string;
  slot: number | null;
  values: readonly number[];
}

export interface StackLayer {
  label: string;
  /** Null for the Other layer. */
  slot: number | null;
  values: number[];
}

/** All-zero inputs are dropped; Other is last and only present when non-zero. */
export function stackLayers(inputs: readonly StackInput[], length: number): StackLayer[] {
  const live = inputs.filter((it) => it.values.some((v) => v > 0));
  const pad = (vs: readonly number[]) => Array.from({ length }, (_, i) => vs[i] ?? 0);
  const layers: StackLayer[] = live
    .filter((it) => it.slot !== null)
    .sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0))
    .map((it) => ({ label: it.label, slot: it.slot, values: pad(it.values) }));
  const rest = live.filter((it) => it.slot === null);
  if (rest.length > 0) {
    const values = new Array<number>(length).fill(0);
    for (const it of rest) for (let i = 0; i < length; i++) values[i]! += it.values[i] ?? 0;
    layers.push({ label: 'Other', slot: null, values });
  }
  return layers;
}

export interface ChartLayer {
  label: string;
  color: string;
  values: number[];
}

export interface BreakdownChart {
  /** Epoch ms bucket starts. */
  buckets: number[];
  bucket: Bucket;
  layers: ChartLayer[];
}

/** A breakdown over time: one stacked layer per slotted entity, the rest as Other. */
export function breakdownChart(data: BreakdownResponse, metric: Metric, lookup: EntityLookup): BreakdownChart {
  const layers = stackLayers(
    data.rows.map((r) => ({
      label: displayName(data.dim, r.label),
      slot: lookup.entity(data.dim, r.key).slot,
      values: metricSeries(r.trend, metric),
    })),
    data.buckets.length,
  );
  return {
    buckets: data.buckets.map((b) => Date.parse(b)),
    bucket: data.bucket,
    layers: layers.map((l) => ({ label: l.label, color: lookup.slotColor(l.slot), values: l.values })),
  };
}
