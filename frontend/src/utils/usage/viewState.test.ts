import { expect, test } from 'bun:test';
import {
  DEFAULT_VIEW_STATE,
  VIEW_STORAGE_KEY,
  loadViewState,
  parseViewState,
  saveViewState,
  serializeViewState,
  type ViewState,
} from './viewState';

const full: ViewState = {
  range: { kind: 'custom', start: '2026-08-04T17:00', end: '2026-09-03T17:00' },
  people: ['sk-*******-alice'],
  models: ['claude-t'],
  credentials: ['bob@example.com'],
  granularity: 'week',
  metric: 'tokens',
  tab: 'credentials',
  drawer: { dim: 'person', key: 'sk-*******-alice' },
};

test('URL round-trips; invalid values fall back per field', () => {
  expect(parseViewState(serializeViewState(full))).toEqual(full);
  expect(parseViewState('range=1y&gran=month&metric=requests&people=a&detail=team:x')).toEqual({
    ...DEFAULT_VIEW_STATE,
    people: ['a'],
  });
});

test('storage restores everything but the drawer, only when the URL has no query', () => {
  const data = new Map<string, string>();
  const storage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) };
  saveViewState(full, storage);
  expect(data.has(VIEW_STORAGE_KEY)).toBe(true);
  expect(loadViewState('', storage)).toEqual({ ...full, drawer: null });
  expect(loadViewState('?range=7d', storage)).toEqual({ ...DEFAULT_VIEW_STATE, range: { kind: 'preset', id: '7d' } });
});
