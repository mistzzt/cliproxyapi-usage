// Bucket and timestamp labels in the viewer's locale and timezone (pure given a locale).
import type { Bucket } from '@/types/api';

type Locale = string | undefined;

const BUCKET_OPTIONS: Record<Bucket, Intl.DateTimeFormatOptions> = {
  hour: { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false },
  day: { weekday: 'short', month: 'short', day: 'numeric' },
  week: { month: 'short', day: 'numeric' },
};

const TICK_OPTIONS: Record<Bucket, Intl.DateTimeFormatOptions> = {
  hour: { month: 'short', day: 'numeric', hour: '2-digit', hour12: false },
  day: { month: 'short', day: 'numeric' },
  week: { month: 'short', day: 'numeric' },
};

/** Full bucket label for headers and captions: `Tue, Aug 4` for a day bucket. */
export function bucketLabel(ms: number, bucket: Bucket, locale?: Locale): string {
  return new Intl.DateTimeFormat(locale, BUCKET_OPTIONS[bucket]).format(ms);
}

/** Compact chart axis label: `Aug 4`, or `Aug 4, 17` for hours. */
export function tickLabel(ms: number, bucket: Bucket, locale?: Locale): string {
  return new Intl.DateTimeFormat(locale, TICK_OPTIONS[bucket]).format(ms);
}

/** `Sep 3, 16:54`. */
export function fmtTimestamp(ms: number, locale?: Locale): string {
  return new Intl.DateTimeFormat(locale, BUCKET_OPTIONS.hour).format(ms);
}
