// Dashboard view state: URL query string round-trip plus the localStorage fallback (pure).
import type { Bucket, Dimension } from '@/types/api';
import { isPresetId, isValidCustomRange, type RangeSel } from './range';

export type Metric = 'cost' | 'tokens';

/** How labels name the active metric's quantity. */
export const METRIC_WORD: Record<Metric, string> = { cost: 'spend', tokens: 'tokens' };
export type Granularity = 'auto' | Bucket;
export type SecondaryTab = 'models' | 'credentials';

export interface DrawerTarget {
  dim: Dimension;
  key: string;
}

export interface ViewState {
  range: RangeSel;
  /** Selected keys per dimension; empty means unfiltered. */
  people: string[];
  models: string[];
  credentials: string[];
  granularity: Granularity;
  metric: Metric;
  tab: SecondaryTab;
  drawer: DrawerTarget | null;
}

export const VIEW_STORAGE_KEY = 'usage.view.v1';

export const DEFAULT_VIEW_STATE: ViewState = {
  range: { kind: 'preset', id: '30d' },
  people: [],
  models: [],
  credentials: [],
  granularity: 'auto',
  metric: 'cost',
  tab: 'models',
  drawer: null,
};

const DIMENSIONS: readonly Dimension[] = ['person', 'model', 'credential'];
const GRANULARITIES: readonly Granularity[] = ['auto', 'hour', 'day', 'week'];

const SHORT: Record<Bucket, string> = { hour: '1h', day: '1d', week: '1w' };

export interface GranularityOption {
  value: Granularity;
  label: string;
  title?: string;
}

/** Granularity buttons labeled with the bucket the server used (`effective`), which may coarsen a fixed choice; only a selected Auto names it. */
export function granularityOptions(selected: Granularity, effective: Bucket | null): GranularityOption[] {
  return GRANULARITIES.map((value): GranularityOption => {
    if (value === 'auto') return { value, label: selected === 'auto' && effective ? `Auto (${SHORT[effective]})` : 'Auto' };
    if (value !== selected || !effective || effective === value) return { value, label: SHORT[value] };
    return {
      value,
      label: `${SHORT[value]} → ${SHORT[effective]}`,
      title: `Too many ${value} buckets for this range; showing ${effective}s`,
    };
  });
}

const LIST_FIELDS = ['people', 'models', 'credentials'] as const;

/** The filter list holding each dimension's selected keys. */
export const FILTER_FIELD: Record<Dimension, (typeof LIST_FIELDS)[number]> = {
  person: 'people',
  model: 'models',
  credential: 'credentials',
};

function oneOf<T extends string>(allowed: readonly T[], v: string | null, fallback: T): T {
  return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
}

function parseRange(p: URLSearchParams): RangeSel {
  const id = p.get('range');
  if (id === 'custom') {
    const start = p.get('from') ?? '';
    const end = p.get('to') ?? '';
    return isValidCustomRange(start, end) ? { kind: 'custom', start, end } : DEFAULT_VIEW_STATE.range;
  }
  return id !== null && isPresetId(id) ? { kind: 'preset', id } : DEFAULT_VIEW_STATE.range;
}

function parseDrawer(v: string | null): DrawerTarget | null {
  if (v === null) return null;
  const sep = v.indexOf(':');
  const dim = v.slice(0, sep);
  const key = v.slice(sep + 1);
  return sep > 0 && key !== '' && (DIMENSIONS as readonly string[]).includes(dim)
    ? { dim: dim as Dimension, key }
    : null;
}

/** Invalid or missing fields fall back to their defaults individually. */
export function parseViewState(search: string | URLSearchParams): ViewState {
  const p = typeof search === 'string' ? new URLSearchParams(search) : search;
  const list = (name: string) => [...new Set(p.getAll(name).filter((v) => v !== ''))];
  return {
    range: parseRange(p),
    people: list('people'),
    models: list('models'),
    credentials: list('credentials'),
    granularity: oneOf(GRANULARITIES, p.get('gran'), DEFAULT_VIEW_STATE.granularity),
    metric: oneOf(['cost', 'tokens'], p.get('metric'), DEFAULT_VIEW_STATE.metric),
    tab: oneOf(['models', 'credentials'], p.get('tab'), DEFAULT_VIEW_STATE.tab),
    drawer: parseDrawer(p.get('detail')),
  };
}

/** Always writes `range` so a serialized state is never an empty query string; other defaults are omitted. */
export function serializeViewState(s: ViewState): string {
  const p = new URLSearchParams();
  if (s.range.kind === 'preset') p.set('range', s.range.id);
  else {
    p.set('range', 'custom');
    p.set('from', s.range.start);
    p.set('to', s.range.end);
  }
  if (s.metric !== DEFAULT_VIEW_STATE.metric) p.set('metric', s.metric);
  if (s.granularity !== DEFAULT_VIEW_STATE.granularity) p.set('gran', s.granularity);
  for (const field of LIST_FIELDS) for (const v of s[field]) p.append(field, v);
  if (s.tab !== DEFAULT_VIEW_STATE.tab) p.set('tab', s.tab);
  if (s.drawer) p.set('detail', `${s.drawer.dim}:${s.drawer.key}`);
  return p.toString();
}

/** A non-empty query string wins; otherwise the stored state, else defaults. */
export function loadViewState(search: string, storage: Pick<Storage, 'getItem'> | null): ViewState {
  const query = search.startsWith('?') ? search.slice(1) : search;
  if (query !== '') return parseViewState(query);
  let stored: string | null = null;
  try {
    stored = storage?.getItem(VIEW_STORAGE_KEY) ?? null;
  } catch {
    // Storage can throw (e.g. disabled cookies); treat as empty.
  }
  return stored ? parseViewState(stored) : DEFAULT_VIEW_STATE;
}

/** Persists everything except the open drawer, which is transient. */
export function saveViewState(s: ViewState, storage: Pick<Storage, 'setItem'> | null): void {
  try {
    storage?.setItem(VIEW_STORAGE_KEY, serializeViewState({ ...s, drawer: null }));
  } catch {
    // Quota or disabled storage; persistence is best-effort.
  }
}

/** Page state narrowed to one entity (replacing that dimension's filter), for the detail drawer. */
export function scopeToEntity(s: ViewState, target: DrawerTarget): ViewState {
  return { ...s, [FILTER_FIELD[target.dim]]: [target.key], drawer: null };
}
