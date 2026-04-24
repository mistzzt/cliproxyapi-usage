/**
 * Stable per-model color palette for chart series.
 *
 * modelColor(name) hashes the model name to a deterministic palette index,
 * so the same model always gets the same color regardless of series order.
 *
 * ALL_MODEL_COLOR is reserved for the aggregate '__all__' series and must
 * not overlap with the MODEL_PALETTE entries.
 */

const MODEL_PALETTE = [
  '#6aa7ff', // blue
  '#a78bfa', // violet
  '#34d399', // green
  '#fb923c', // orange
  '#f472b6', // pink
  '#38bdf8', // sky
  '#facc15', // yellow
  '#f87171', // red
  '#4ade80', // lime
  '#e879f9', // fuchsia
];

/** Reserved color for the aggregate '__all__' series. */
export const ALL_MODEL_COLOR = '#f59e0b'; // amber — warm, visually distinct from palette

/**
 * Returns a deterministic color for a model name using djb2 hash + palette lookup.
 * Stable across renders and metric tab switches.
 */
export function modelColor(name: string): string {
  let hash = 5381;
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) + hash + name.charCodeAt(i)) >>> 0;
  }
  return MODEL_PALETTE[hash % MODEL_PALETTE.length]!;
}
