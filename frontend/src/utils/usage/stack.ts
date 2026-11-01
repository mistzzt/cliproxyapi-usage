// Stacked over-time series: slotted entities in slot order, the rest summed into one "Other" layer (pure).

export interface StackInput {
  key: string;
  label: string;
  slot: number | null;
  values: readonly number[];
}

export interface StackLayer {
  key: string;
  label: string;
  /** Null for the Other layer. */
  slot: number | null;
  values: number[];
}

export const OTHER_KEY = '__other__';

/** All-zero inputs are dropped; Other is last and only present when non-zero. */
export function stackLayers(inputs: readonly StackInput[], length: number): StackLayer[] {
  const live = inputs.filter((it) => it.values.some((v) => v > 0));
  const pad = (vs: readonly number[]) => Array.from({ length }, (_, i) => vs[i] ?? 0);
  const layers: StackLayer[] = live
    .filter((it) => it.slot !== null)
    .sort((a, b) => (a.slot ?? 0) - (b.slot ?? 0))
    .map((it) => ({ key: it.key, label: it.label, slot: it.slot, values: pad(it.values) }));
  const rest = live.filter((it) => it.slot === null);
  if (rest.length > 0) {
    const values = new Array<number>(length).fill(0);
    for (const it of rest) for (let i = 0; i < length; i++) values[i]! += it.values[i] ?? 0;
    layers.push({ key: OTHER_KEY, label: 'Other', slot: null, values });
  }
  return layers;
}
