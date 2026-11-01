// Range presets and custom date+time windows, resolved against an explicit `now`.
import { addDays, format, startOfDay } from 'date-fns';

export type PresetId = '24h' | '7d' | '30d' | '90d' | 'this_month' | 'last_month' | 'all';

export type RangeSel =
  | { kind: 'preset'; id: PresetId }
  // Viewer-local `YYYY-MM-DDTHH:mm` strings (the datetime-local format); end is exclusive.
  | { kind: 'custom'; start: string; end: string };

export const PRESETS: readonly { id: PresetId; label: string }[] = [
  { id: '24h', label: 'Last 24 hours' },
  { id: '7d', label: 'Last 7 days' },
  { id: '30d', label: 'Last 30 days' },
  { id: '90d', label: 'Last 90 days' },
  { id: 'this_month', label: 'This month' },
  { id: 'last_month', label: 'Last month' },
  { id: 'all', label: 'All time' },
];

export function isPresetId(v: string): v is PresetId {
  return PRESETS.some((p) => p.id === v);
}

/** Epoch-ms window; `start` null means all time (no previous period). */
export interface RangeWindow {
  start: number | null;
  end: number;
}

/** Query parameters for range-consuming endpoints. */
export interface ApiRange {
  start?: string;
  end: string;
  tz: string;
}

const HOUR = 3_600_000;
const DAY = 24 * HOUR;
const ROLLING: Partial<Record<PresetId, number>> = { '24h': DAY, '7d': 7 * DAY, '30d': 30 * DAY, '90d': 90 * DAY };
const LOCAL_INPUT = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/;

/** Parses a local `YYYY-MM-DDTHH:mm` string to epoch ms, or null if malformed or out of range. */
export function parseLocalInput(s: string): number | null {
  const m = LOCAL_INPUT.exec(s);
  if (!m) return null;
  const [y, mo, d, h, mi] = m.slice(1).map(Number) as [number, number, number, number, number];
  if (h > 23 || mi > 59) return null;
  const date = new Date(y, mo - 1, d, h, mi);
  if (date.getFullYear() !== y || date.getMonth() !== mo - 1 || date.getDate() !== d) return null;
  return date.getTime();
}

export function toLocalInput(ms: number): string {
  return format(ms, "yyyy-MM-dd'T'HH:mm");
}

/** Calendar day picks as whole local days: first day's midnight to the next midnight after the last, capped at now. */
export function dayRange(first: Date, last: Date, now: number): { start: string; end: string } {
  return {
    start: toLocalInput(startOfDay(first).getTime()),
    end: toLocalInput(Math.min(addDays(startOfDay(last), 1).getTime(), now)),
  };
}

/** Days a custom range covers on the calendar; an end at midnight excludes that day. */
export function rangeDays(start: string, end: string): { from: Date; to: Date } | undefined {
  const s = parseLocalInput(start);
  const e = parseLocalInput(end);
  if (s === null || e === null) return undefined;
  return { from: new Date(s), to: new Date(Math.max(s, e - 1)) };
}

/** A custom range is valid when both ends parse and start is strictly before end. */
export function isValidCustomRange(start: string, end: string): boolean {
  const s = parseLocalInput(start);
  const e = parseLocalInput(end);
  return s !== null && e !== null && s < e;
}

/** Throws RangeError for an invalid custom range; validate with `isValidCustomRange` first. */
export function resolveWindow(sel: RangeSel, now: number): RangeWindow {
  if (sel.kind === 'custom') {
    const start = parseLocalInput(sel.start);
    const end = parseLocalInput(sel.end);
    if (start === null || end === null || start >= end) {
      throw new RangeError(`invalid custom range ${sel.start} -> ${sel.end}`);
    }
    return { start, end };
  }
  const rolling = ROLLING[sel.id];
  if (rolling !== undefined) return { start: now - rolling, end: now };
  const d = new Date(now);
  const monthStart = new Date(d.getFullYear(), d.getMonth(), 1).getTime();
  switch (sel.id) {
    case 'this_month':
      return { start: monthStart, end: now };
    case 'last_month':
      return { start: new Date(d.getFullYear(), d.getMonth() - 1, 1).getTime(), end: monthStart };
    default:
      return { start: null, end: now };
  }
}

export function viewerTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

export function toApiRange(w: RangeWindow, tz: string = viewerTimeZone()): ApiRange {
  const end = new Date(w.end).toISOString();
  return w.start === null ? { end, tz } : { start: new Date(w.start).toISOString(), end, tz };
}

export function resolveRange(sel: RangeSel, now: number, tz: string = viewerTimeZone()): ApiRange {
  return toApiRange(resolveWindow(sel, now), tz);
}

/** Preset label, or `Aug 4 → Sep 3` (times shown unless both ends are midnight). */
export function rangeLabel(sel: RangeSel): string {
  if (sel.kind === 'preset') return PRESETS.find((p) => p.id === sel.id)?.label ?? sel.id;
  const start = parseLocalInput(sel.start);
  const end = parseLocalInput(sel.end);
  if (start === null || end === null) return 'Custom';
  const midnight = (ms: number) => new Date(ms).getHours() === 0 && new Date(ms).getMinutes() === 0;
  const year = new Date(start).getFullYear() !== new Date(end).getFullYear() ? ', yyyy' : '';
  const pattern = midnight(start) && midnight(end) ? `MMM d${year}` : `MMM d${year}, HH:mm`;
  return `${format(start, pattern)} → ${format(end, pattern)}`;
}
