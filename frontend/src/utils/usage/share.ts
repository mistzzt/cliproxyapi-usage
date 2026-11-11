// 100% share bar segments, one per entity; slot-less entities share the Other gray (pure).
import type { BreakdownResponse } from '@/types/api';
import type { EntityLookup } from './entities';
import { metricValue } from './richRows';
import type { Metric } from './viewState';

export interface ShareInput {
  key: string;
  value: number;
  /** Color slot from the options endpoint; null draws in the Other gray. */
  slot: number | null;
}

export interface ShareItem extends ShareInput {
  label: string;
}

/** One item per breakdown row, valued by the active metric and colored by its options slot. */
export function shareItems(data: BreakdownResponse, metric: Metric, lookup: EntityLookup): ShareItem[] {
  return data.rows.map((r) => ({
    key: r.key,
    label: r.label,
    value: metricValue(r.current, metric),
    slot: lookup.entity(data.dim, r.key).slot,
  }));
}

export interface ShareSegment extends ShareInput {
  share: number;
}

/** One segment per entity by descending value; zero-valued entries are dropped. */
export function shareSegments(items: readonly ShareInput[]): ShareSegment[] {
  const positive = items.filter((it) => it.value > 0);
  const total = positive.reduce((acc, it) => acc + it.value, 0);
  return positive
    .map((it) => ({ ...it, share: total > 0 ? it.value / total : 0 }))
    .sort((a, b) => b.value - a.value);
}
