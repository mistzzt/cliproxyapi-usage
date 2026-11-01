import { expect, test } from 'bun:test';
import { resolveRange } from './range';

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
