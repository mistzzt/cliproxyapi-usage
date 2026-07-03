import {
  subHours,
  startOfDay,
  startOfWeek,
  startOfMonth,
  addDays,
  addWeeks,
  addMonths,
  isSameDay,
  isSameWeek,
  isSameMonth,
  parseISO,
  format,
} from 'date-fns';
import type { RangeSpec } from '@/types/api';

/** Sunday-first weeks, per product decision. */
const WEEK_OPTIONS = { weekStartsOn: 0 } as const;

/** The concrete instants + tz scalar sent to the backend. */
export interface ResolvedRange {
  /** ISO-8601 UTC instant; omitted entirely for open-start ("all"). */
  start?: string;
  /** ISO-8601 UTC instant. */
  end: string;
  /** Minutes east of UTC, i.e. `-Date#getTimezoneOffset()`. */
  tzOffsetMinutes: number;
}

type CalendarUnit = 'day' | 'week' | 'month';

/**
 * Resolve a `RangeSpec` selection into concrete UTC instants using date-fns.
 * All calendar math is done in the browser-local timezone (date-fns default),
 * which is exactly the intended semantics.
 */
export function resolveRange(spec: RangeSpec, now: Date): ResolvedRange {
  switch (spec.kind) {
    case 'rolling': {
      const hours = spec.preset === '7h' ? 7 : 24;
      return {
        start: subHours(now, hours).toISOString(),
        end: now.toISOString(),
        tzOffsetMinutes: -now.getTimezoneOffset(),
      };
    }
    case 'all':
      // Open start: omit `start` entirely (never `start: undefined`, per
      // exactOptionalPropertyTypes).
      return { end: now.toISOString(), tzOffsetMinutes: -now.getTimezoneOffset() };
    case 'calendar': {
      const anchor = parseISO(spec.anchor);
      const start = startOfUnit(spec.unit, anchor);
      // Half-open window: end = start of the *next* period.
      const end = startOfUnit(spec.unit, stepUnit(spec.unit, anchor, 1));
      return {
        start: start.toISOString(),
        end: end.toISOString(),
        // Offset from the selected period's start, not `now`: a date in a
        // different DST regime than today would otherwise be bucketed with the
        // wrong offset (extra day column / boundary-hour mis-attribution).
        tzOffsetMinutes: -start.getTimezoneOffset(),
      };
    }
    case 'custom': {
      const start = startOfDay(parseISO(spec.startDate));
      // Half-open through the next local day: the backend filters with
      // `datetime(ts) < datetime(end)` and SQLite's datetime() truncates
      // sub-second precision, so an inclusive 23:59:59.999 end would drop rows
      // recorded in the final second. Use start-of-next-day like calendar presets.
      const end = startOfDay(addDays(parseISO(spec.endDate), 1));
      return {
        start: start.toISOString(),
        end: end.toISOString(),
        tzOffsetMinutes: -start.getTimezoneOffset(),
      };
    }
  }
}

function startOfUnit(unit: CalendarUnit, d: Date): Date {
  switch (unit) {
    case 'day':
      return startOfDay(d);
    case 'week':
      return startOfWeek(d, WEEK_OPTIONS);
    case 'month':
      return startOfMonth(d);
  }
}

function stepUnit(unit: CalendarUnit, d: Date, delta: number): Date {
  switch (unit) {
    case 'day':
      return addDays(d, delta);
    case 'week':
      return addWeeks(d, delta);
    case 'month':
      return addMonths(d, delta);
  }
}

/**
 * Return a fresh anchor (YYYY-MM-DD) advanced by `delta` periods.
 * `delta` is typically -1 (prev) or +1 (next).
 */
export function stepAnchor(unit: CalendarUnit, anchor: string, delta: number): string {
  return format(stepUnit(unit, parseISO(anchor), delta), 'yyyy-MM-dd');
}

/** True when `anchor` falls in the same period as `now` (i.e. the live period). */
export function isCurrentPeriod(unit: CalendarUnit, anchor: string, now: Date): boolean {
  const d = parseISO(anchor);
  switch (unit) {
    case 'day':
      return isSameDay(d, now);
    case 'week':
      return isSameWeek(d, now, WEEK_OPTIONS);
    case 'month':
      return isSameMonth(d, now);
  }
}

/** A local calendar-date anchor (YYYY-MM-DD) for the period containing `now`. */
export function anchorFor(now: Date): string {
  return format(now, 'yyyy-MM-dd');
}

/** Human label for a calendar anchor, formatted per unit. */
export function formatCalendarLabel(unit: CalendarUnit, anchor: string): string {
  const start = startOfUnit(unit, parseISO(anchor));
  switch (unit) {
    case 'day':
      return format(start, 'MMM d, yyyy');
    case 'week': {
      const end = addDays(start, 6);
      return `${format(start, 'MMM d')} - ${format(end, 'MMM d')}`;
    }
    case 'month':
      return format(start, 'MMMM yyyy');
  }
}
