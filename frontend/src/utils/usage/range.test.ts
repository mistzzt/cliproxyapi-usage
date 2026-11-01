import { expect, test } from 'bun:test';
import { dayRange, rangeDays, resolveRange } from './range';

test('preset, last month and custom selections resolve to local-time API params', () => {
  const now = new Date(2026, 8, 15, 10, 30).getTime();
  const iso = (d: Date) => d.toISOString();
  expect(resolveRange({ kind: 'preset', id: '7d' }, now, 'UTC')).toEqual({
    start: iso(new Date(now - 7 * 86_400_000)),
    end: iso(new Date(now)),
    tz: 'UTC',
  });
  expect(resolveRange({ kind: 'preset', id: 'last_month' }, now, 'UTC')).toEqual({
    start: iso(new Date(2026, 7, 1)),
    end: iso(new Date(2026, 8, 1)),
    tz: 'UTC',
  });
  const custom = { kind: 'custom', start: '2026-08-04T17:00', end: '2026-09-03T17:00' } as const;
  expect(resolveRange(custom, now, 'UTC')).toEqual({
    start: iso(new Date(2026, 7, 4, 17)),
    end: iso(new Date(2026, 8, 3, 17)),
    tz: 'UTC',
  });
});

test('calendar picks cover whole local days and round-trip to the same days', () => {
  const day = (d: number) => new Date(2026, 7, d, 14, 23);
  const now = day(20).getTime();
  expect(dayRange(day(4), day(4), now)).toEqual({ start: '2026-08-04T00:00', end: '2026-08-05T00:00' });
  expect(dayRange(day(4), day(6), now)).toEqual({ start: '2026-08-04T00:00', end: '2026-08-07T00:00' });
  // A range ending today stops at now rather than tomorrow's midnight.
  expect(dayRange(day(19), day(20), now)).toEqual({ start: '2026-08-19T00:00', end: '2026-08-20T14:23' });
  expect(rangeDays('2026-08-04T00:00', '2026-08-07T00:00')).toEqual({
    from: new Date(2026, 7, 4),
    to: new Date(new Date(2026, 7, 7).getTime() - 1),
  });
  expect(rangeDays('2026-08-04T09:00', '2026-08-06T14:23')?.to.getDate()).toBe(6);
  expect(rangeDays('bad', '2026-08-06T14:23')).toBeUndefined();
});
