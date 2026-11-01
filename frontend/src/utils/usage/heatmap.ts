// Heatmap layout: top-N columns grouped by family, a "+N more" column, sqrt-scaled color bins (pure).

export interface HeatCellInput {
  row: string;
  col: string;
  value: number;
}

export type HeatColumn =
  | { kind: 'entity'; key: string; total: number }
  | { kind: 'more'; keys: string[]; total: number };

export interface HeatGroup {
  name: string;
  span: number;
}

export interface HeatmapLayout {
  /** Rows with a positive total, largest first. */
  rows: { key: string; total: number }[];
  columns: HeatColumn[];
  /** Header groups spanning `columns` in order; the "more" column gets an unnamed group. */
  groups: HeatGroup[];
  /** `values[rowIndex][columnIndex]`. */
  values: number[][];
  grand: number;
  /** Largest displayed cell, the top of the color scale. */
  max: number;
}

export const HEATMAP_MAX_COLUMNS = 9;
/** Mix percentage of the heat color for each of the six bins, lightest first. */
export const HEAT_STEPS = [14, 28, 44, 60, 78, 100] as const;
export const NOTABLE_SHARE = 0.1;

function sumBy(cells: readonly HeatCellInput[], pick: (c: HeatCellInput) => string): Map<string, number> {
  const out = new Map<string, number>();
  for (const c of cells) out.set(pick(c), (out.get(pick(c)) ?? 0) + c.value);
  return out;
}

function rankedPositive(totals: Map<string, number>): [string, number][] {
  return [...totals].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
}

/** Families are ordered by their largest kept column; columns within a family by total. */
export function buildHeatmap(
  cells: readonly HeatCellInput[],
  opts: { maxColumns?: number; family?: (col: string) => string } = {},
): HeatmapLayout {
  const { maxColumns = HEATMAP_MAX_COLUMNS, family = () => '' } = opts;
  const positive = cells.filter((c) => c.value > 0);
  const rows = rankedPositive(sumBy(positive, (c) => c.row)).map(([key, total]) => ({ key, total }));
  const ranked = rankedPositive(sumBy(positive, (c) => c.col));
  const kept = ranked.slice(0, maxColumns);
  const rest = ranked.slice(maxColumns);

  const families = [...new Set(kept.map(([key]) => family(key)))];
  const columns: HeatColumn[] = [];
  const groups: HeatGroup[] = [];
  for (const f of families) {
    const members = kept.filter(([key]) => family(key) === f);
    groups.push({ name: f, span: members.length });
    for (const [key, total] of members) columns.push({ kind: 'entity', key, total });
  }
  if (rest.length > 0) {
    columns.push({ kind: 'more', keys: rest.map(([key]) => key), total: rest.reduce((acc, [, v]) => acc + v, 0) });
    groups.push({ name: '', span: 1 });
  }

  const rowIndex = new Map(rows.map((r, i) => [r.key, i]));
  const colIndex = new Map<string, number>();
  columns.forEach((c, i) => {
    for (const key of c.kind === 'entity' ? [c.key] : c.keys) colIndex.set(key, i);
  });
  const values = rows.map(() => new Array<number>(columns.length).fill(0));
  for (const c of positive) {
    const r = rowIndex.get(c.row);
    const k = colIndex.get(c.col);
    if (r !== undefined && k !== undefined) values[r]![k]! += c.value;
  }
  const max = Math.max(0, ...values.flat());
  const grand = rows.reduce((acc, r) => acc + r.total, 0);
  return { rows, columns, groups, values, grand, max };
}

/** Bin index into HEAT_STEPS on a sqrt scale, or -1 for an empty cell. */
export function heatBin(value: number, max: number): number {
  if (value <= 0 || max <= 0) return -1;
  return Math.min(HEAT_STEPS.length - 1, Math.floor(Math.sqrt(value / max) * HEAT_STEPS.length));
}

/** Cells at or above 10% of their row get a visible label; the rest reveal on hover. */
export function isNotable(value: number, rowTotal: number): boolean {
  return rowTotal > 0 && value / rowTotal >= NOTABLE_SHARE;
}
