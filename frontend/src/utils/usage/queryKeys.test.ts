import { expect, test } from 'bun:test';
import { QueryClient } from '@tanstack/react-query';
import {
  breakdownKey,
  computeWindow,
  exportPath,
  OPTIONS_KEY,
  requestParams,
  summaryKey,
  windowKey,
  windowPrefix,
} from './queryKeys';
import { DEFAULT_VIEW_STATE, type ViewState } from './viewState';

const now = new Date(2026, 8, 15, 10, 30).getTime();
const iso = (d: Date) => d.toISOString();

test('rolling, calendar and custom ranges resolve to ISO windows at now', () => {
  expect(computeWindow(windowKey({ kind: 'preset', id: '7d' }, 'UTC'), now)).toEqual({
    start: iso(new Date(now - 7 * 86_400_000)),
    end: iso(new Date(now)),
    tz: 'UTC',
  });
  expect(computeWindow(windowKey({ kind: 'preset', id: 'last_month' }, 'UTC'), now)).toEqual({
    start: iso(new Date(2026, 7, 1)),
    end: iso(new Date(2026, 8, 1)),
    tz: 'UTC',
  });
  const custom = { kind: 'custom', start: '2026-08-04T17:00', end: '2026-09-03T17:00' } as const;
  expect(computeWindow(windowKey(custom, 'UTC'), now)).toEqual({
    start: iso(new Date(2026, 7, 4, 17)),
    end: iso(new Date(2026, 8, 3, 17)),
    tz: 'UTC',
  });
  expect(computeWindow(windowKey({ kind: 'preset', id: 'all' }, 'UTC'), now)).toEqual({
    start: null,
    end: iso(new Date(now)),
    tz: 'UTC',
  });
});

test('keys carry each request exactly, and requestParams rebuilds its query', () => {
  const w = computeWindow(windowKey({ kind: 'preset', id: '24h' }, 'Europe/Berlin'), now);
  const s: ViewState = { ...DEFAULT_VIEW_STATE, people: ['a', 'b'], granularity: 'day' };
  expect(requestParams(summaryKey(w, s))).toEqual({
    start: w.start!,
    end: w.end,
    tz: 'Europe/Berlin',
    people: 'a,b',
    bucket: 'day',
  });
  expect(breakdownKey(w, s, 'model')[3]).toEqual({ people: 'a,b', bucket: 'day', dim: 'model' });
  const all = computeWindow(windowKey({ kind: 'preset', id: 'all' }, 'UTC'), now);
  expect(requestParams(breakdownKey(all, DEFAULT_VIEW_STATE, 'person'))).toEqual({
    end: all.end,
    tz: 'UTC',
    bucket: 'auto',
    dim: 'person',
  });
});

test('a window prefix selects exactly the requests inside that window', () => {
  const qc = new QueryClient();
  const all = computeWindow(windowKey({ kind: 'preset', id: 'all' }, 'UTC'), now);
  const later = computeWindow(windowKey({ kind: 'preset', id: 'all' }, 'UTC'), now + 1000);
  for (const key of [summaryKey(all, DEFAULT_VIEW_STATE), breakdownKey(later, DEFAULT_VIEW_STATE, 'person'), OPTIONS_KEY]) {
    qc.setQueryData(key, {});
  }
  qc.setQueryData(windowKey({ kind: 'preset', id: 'all' }, 'UTC'), all);
  const matched = qc.getQueriesData({ queryKey: windowPrefix({ ...all }) }).map(([key]) => key);
  expect(matched).toEqual([summaryKey(all, DEFAULT_VIEW_STATE)]);
});

test('the export path carries the same window, filters and bucket as the page', () => {
  const w = { start: '2026-09-01T00:00:00.000Z', end: '2026-09-02T00:00:00.000Z', tz: 'UTC' };
  const s: ViewState = { ...DEFAULT_VIEW_STATE, models: ['m1', 'm2'], granularity: 'hour' };
  const path = exportPath(w, s);
  expect(path.startsWith('/usage/export?')).toBe(true);
  expect(Object.fromEntries(new URLSearchParams(path.split('?')[1]))).toEqual({
    ...requestParams(summaryKey(w, s)),
  });
  expect(exportPath({ ...w, start: null }, DEFAULT_VIEW_STATE)).not.toContain('start=');
});
