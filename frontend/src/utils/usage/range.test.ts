import { expect, test } from 'bun:test';
import { dayRange, pickDay, rangeDays } from './range';

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

test('calendar clicks start a new range, then complete it in either order', () => {
  const day = (d: number) => new Date(2026, 9, d);
  const first = pickDay(null, day(9));
  expect(first).toEqual({ anchor: day(9), first: day(9), last: day(9) });
  expect(pickDay(first.anchor, day(5))).toEqual({ anchor: null, first: day(5), last: day(9) });
  expect(pickDay(first.anchor, day(12))).toEqual({ anchor: null, first: day(9), last: day(12) });
});
