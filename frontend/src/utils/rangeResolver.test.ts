import { describe, test, expect } from 'bun:test';
import type { RangeSpec } from '../types/api';
import {
  resolveRange,
  stepAnchor,
  isCurrentPeriod,
  anchorFor,
  formatCalendarLabel,
} from './rangeResolver';

// Note: calendar/custom assertions check *local* date components (via native
// Date getters), so they hold regardless of the machine timezone. Rolling/all
// assertions compare against `now` derived from the same clock.

describe('resolveRange - rolling', () => {
  const now = new Date(Date.UTC(2026, 6, 3, 12, 0, 0));

  test('24h subtracts 24 hours', () => {
    const r = resolveRange({ kind: 'rolling', preset: '24h' }, now);
    expect(r.end).toBe(now.toISOString());
    expect(r.start).toBe(new Date(now.getTime() - 24 * 3600 * 1000).toISOString());
  });

  test('7h subtracts 7 hours', () => {
    const r = resolveRange({ kind: 'rolling', preset: '7h' }, now);
    expect(r.start).toBe(new Date(now.getTime() - 7 * 3600 * 1000).toISOString());
  });

  test('tzOffsetMinutes mirrors -getTimezoneOffset', () => {
    const r = resolveRange({ kind: 'rolling', preset: '24h' }, now);
    expect(r.tzOffsetMinutes).toBe(-now.getTimezoneOffset());
  });
});

describe('resolveRange - all', () => {
  const now = new Date(Date.UTC(2026, 6, 3, 12, 0, 0));

  test('omits start entirely (open-ended)', () => {
    const r = resolveRange({ kind: 'all' }, now);
    expect('start' in r).toBe(false);
    expect(r.start).toBeUndefined();
    expect(r.end).toBe(now.toISOString());
  });
});

describe('resolveRange - calendar day', () => {
  test('spans local midnight to next local midnight', () => {
    const spec: RangeSpec = { kind: 'calendar', unit: 'day', anchor: '2026-07-15' };
    const r = resolveRange(spec, new Date());
    const start = new Date(r.start!);
    const end = new Date(r.end);
    expect(start.getFullYear()).toBe(2026);
    expect(start.getMonth()).toBe(6);
    expect(start.getDate()).toBe(15);
    expect(start.getHours()).toBe(0);
    expect(start.getMinutes()).toBe(0);
    expect(end.getDate()).toBe(16);
    expect(end.getHours()).toBe(0);
  });
});

describe('resolveRange - calendar week (Sunday start)', () => {
  test('Jul 1 2026 (Wed) resolves to Sun Jun 28 .. Sun Jul 5', () => {
    const spec: RangeSpec = { kind: 'calendar', unit: 'week', anchor: '2026-07-01' };
    const r = resolveRange(spec, new Date());
    const start = new Date(r.start!);
    const end = new Date(r.end);
    // Start is the Sunday on/before the anchor.
    expect(start.getDay()).toBe(0); // Sunday
    expect(start.getMonth()).toBe(5); // June
    expect(start.getDate()).toBe(28);
    expect(start.getHours()).toBe(0);
    // Half-open end is the following Sunday.
    expect(end.getDay()).toBe(0);
    expect(end.getMonth()).toBe(6); // July
    expect(end.getDate()).toBe(5);
  });
});

describe('resolveRange - calendar month', () => {
  test('mid-month anchor resolves to 1st .. next 1st', () => {
    const spec: RangeSpec = { kind: 'calendar', unit: 'month', anchor: '2026-07-15' };
    const r = resolveRange(spec, new Date());
    const start = new Date(r.start!);
    const end = new Date(r.end);
    expect(start.getMonth()).toBe(6); // July
    expect(start.getDate()).toBe(1);
    expect(start.getHours()).toBe(0);
    expect(end.getMonth()).toBe(7); // August
    expect(end.getDate()).toBe(1);
  });
});

describe('resolveRange - custom', () => {
  test('start-of-day .. half-open start of the day after endDate', () => {
    const spec: RangeSpec = { kind: 'custom', startDate: '2026-07-10', endDate: '2026-07-12' };
    const r = resolveRange(spec, new Date());
    const start = new Date(r.start!);
    const end = new Date(r.end);
    expect(start.getDate()).toBe(10);
    expect(start.getHours()).toBe(0);
    expect(start.getMinutes()).toBe(0);
    // Half-open: end is midnight starting the day *after* the inclusive endDate
    // (the 13th), so the whole of the 12th is covered without dropping its
    // final-second rows to SQLite's sub-second truncation on `< end`.
    expect(end.getDate()).toBe(13);
    expect(end.getHours()).toBe(0);
    expect(end.getMinutes()).toBe(0);
    expect(end.getSeconds()).toBe(0);
    expect(end.getMilliseconds()).toBe(0);
  });
});

describe('stepAnchor', () => {
  test('day +/- 1', () => {
    expect(stepAnchor('day', '2026-07-15', -1)).toBe('2026-07-14');
    expect(stepAnchor('day', '2026-07-15', 1)).toBe('2026-07-16');
  });

  test('week +/- 1 (7 days)', () => {
    expect(stepAnchor('week', '2026-07-15', -1)).toBe('2026-07-08');
    expect(stepAnchor('week', '2026-07-15', 1)).toBe('2026-07-22');
  });

  test('month +/- 1', () => {
    expect(stepAnchor('month', '2026-07-15', -1)).toBe('2026-06-15');
    expect(stepAnchor('month', '2026-07-15', 1)).toBe('2026-08-15');
  });

  test('month step clamps to end of shorter month', () => {
    expect(stepAnchor('month', '2026-01-31', 1)).toBe('2026-02-28');
  });
});

describe('isCurrentPeriod', () => {
  // Wed Jul 15 2026, local noon.
  const now = new Date(2026, 6, 15, 12, 0, 0);

  test('day', () => {
    expect(isCurrentPeriod('day', '2026-07-15', now)).toBe(true);
    expect(isCurrentPeriod('day', '2026-07-14', now)).toBe(false);
  });

  test('week (Sunday start): Jul 12 - Jul 18 is current', () => {
    expect(isCurrentPeriod('week', '2026-07-13', now)).toBe(true);
    expect(isCurrentPeriod('week', '2026-07-11', now)).toBe(false); // prior week (Sat)
  });

  test('month', () => {
    expect(isCurrentPeriod('month', '2026-07-01', now)).toBe(true);
    expect(isCurrentPeriod('month', '2026-06-30', now)).toBe(false);
  });
});

describe('anchorFor / formatCalendarLabel', () => {
  test('anchorFor formats local yyyy-MM-dd', () => {
    expect(anchorFor(new Date(2026, 6, 3, 12))).toBe('2026-07-03');
  });

  test('labels per unit', () => {
    expect(formatCalendarLabel('day', '2026-07-15')).toBe('Jul 15, 2026');
    expect(formatCalendarLabel('week', '2026-07-15')).toBe('Jul 12 - Jul 18');
    expect(formatCalendarLabel('month', '2026-07-15')).toBe('July 2026');
  });
});
