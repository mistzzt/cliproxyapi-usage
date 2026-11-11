import { useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { DayPicker, type DateRange } from 'react-day-picker';
import 'react-day-picker/style.css';
import Button from '@/components/ui/Button';
import type { Bucket, Entity, OptionsResponse } from '@/types/api';
import { apiPath } from '@/services/runtimeConfig';
import { exportPath, type ResolvedWindow } from '@/utils/usage/queryKeys';
import {
  dayRange,
  isValidCustomRange,
  pickDay,
  PRESETS,
  rangeDays,
  rangeLabel,
  toLocalInput,
  type RangeSel,
} from '@/utils/usage/range';
import { granularityOptions, type Granularity, type Metric, type ViewState } from '@/utils/usage/viewState';
import Segmented from './Segmented';
import s from './FilterBar.module.scss';

const DAY = 86_400_000;

interface FilterBarProps {
  state: ViewState;
  update: (patch: Partial<ViewState>) => void;
  win: ResolvedWindow;
  /** Bucket echoed by the API, shown on the Auto button. */
  effectiveBucket: Bucket | null;
  options: OptionsResponse | null;
  /** Computes a fresh window and refetches without touching the URL. */
  onRefresh: () => void;
}

export default function FilterBar({ state, update, win, effectiveBucket, options, onRefresh }: FilterBarProps) {
  return (
    <div className={s.bar}>
      <RangePicker value={state.range} win={win} onChange={(range) => update({ range })} />
      <MultiPill
        noun="people"
        allLabel="All people"
        options={options?.people ?? []}
        value={state.people}
        onChange={(people) => update({ people })}
      />
      <MultiPill
        noun="models"
        allLabel="All models"
        options={options?.models ?? []}
        value={state.models}
        onChange={(models) => update({ models })}
      />
      <MultiPill
        noun="credentials"
        allLabel="All credentials"
        options={options?.credentials ?? []}
        value={state.credentials}
        onChange={(credentials) => update({ credentials })}
      />
      <div className={s.spacer} />
      <Segmented<Granularity>
        label="Granularity"
        value={state.granularity}
        options={granularityOptions(state.granularity, effectiveBucket)}
        onChange={(granularity) => update({ granularity })}
      />
      <Segmented<Metric>
        label="Metric"
        value={state.metric}
        options={[
          { value: 'cost', label: 'Cost' },
          { value: 'tokens', label: 'Tokens' },
        ]}
        onChange={(metric) => update({ metric })}
      />
      <a
        className={s.iconButton}
        href={apiPath(exportPath(win, state))}
        download="usage.csv"
        aria-label="Download CSV"
        title="Download CSV: one row per bucket, person, model and credential"
      >
        <DownloadIcon />
      </a>
      <button type="button" className={s.iconButton} aria-label="Refresh" title="Refresh" onClick={onRefresh}>
        <RefreshIcon />
      </button>
    </div>
  );
}

function RangePicker({
  value,
  win,
  onChange,
}: {
  value: RangeSel;
  win: ResolvedWindow;
  onChange: (r: RangeSel) => void;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [anchor, setAnchor] = useState<Date | null>(null);
  // Read when the picker opens, so the calendar never offers days past the real today.
  const [now, setNow] = useState(0);

  const handleOpen = (next: boolean) => {
    setOpen(next);
    if (!next) return;
    const end = Date.parse(win.end);
    setNow(Date.now());
    setFrom(toLocalInput(win.start === null ? end - 30 * DAY : Date.parse(win.start)));
    setTo(toLocalInput(end));
    setCustom(value.kind === 'custom');
    setAnchor(null);
  };

  const valid = isValidCustomRange(from, to);
  const selected = rangeDays(from, to);
  const toDate = selected?.to ?? new Date(now);
  // Uses the clicked day rather than DayPicker's range, which would extend the shown window.
  const onCalendar = (_: DateRange | undefined, day: Date) => {
    const pick = pickDay(anchor, day);
    const days = dayRange(pick.first, pick.last, now);
    setAnchor(pick.anchor);
    setFrom(days.start);
    setTo(days.end);
  };

  return (
    <Popover.Root open={open} onOpenChange={handleOpen}>
      <Popover.Trigger asChild>
        <button type="button" className={s.pill}>
          <CalendarIcon /> {rangeLabel(value)} <span className={s.caret}>▾</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content className={s.popover} sideOffset={6} align="start" collisionPadding={12}>
          {!custom ? (
            <div className={s.presetList}>
              {PRESETS.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={s.presetRow}
                  onClick={() => {
                    onChange({ kind: 'preset', id: p.id });
                    setOpen(false);
                  }}
                >
                  <span className={s.check}>{value.kind === 'preset' && value.id === p.id ? '✓' : ''}</span>
                  {p.label}
                </button>
              ))}
              <div className={s.hairline} />
              <button type="button" className={s.presetRow} onClick={() => setCustom(true)}>
                <span className={s.check}>{value.kind === 'custom' ? '✓' : ''}</span>
                Custom range…
              </button>
            </div>
          ) : (
            <div className={s.customRange}>
              <DayPicker
                mode="range"
                numberOfMonths={2}
                defaultMonth={new Date(toDate.getFullYear(), toDate.getMonth() - 1)}
                disabled={{ after: new Date(now) }}
                selected={selected}
                onSelect={onCalendar}
                className={s.calendar ?? ''}
              />
              <div className={s.timeRow}>
                <label>
                  From
                  <input type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
                </label>
                <label>
                  To
                  <input type="datetime-local" value={to} onChange={(e) => setTo(e.target.value)} />
                </label>
              </div>
              <div className={s.popoverActions}>
                <Button variant="secondary" onClick={() => setCustom(false)}>
                  ‹ Presets
                </Button>
                <Button
                  disabled={!valid}
                  onClick={() => {
                    onChange({ kind: 'custom', start: from, end: to });
                    setOpen(false);
                  }}
                >
                  Apply
                </Button>
              </div>
            </div>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function MultiPill({
  noun,
  allLabel,
  options,
  value,
  onChange,
}: {
  noun: string;
  allLabel: string;
  options: readonly Entity[];
  value: readonly string[];
  onChange: (v: string[]) => void;
}) {
  const [query, setQuery] = useState('');
  const selected = new Set(value);
  const labelOf = (key: string) => options.find((o) => o.key === key)?.label ?? key;
  const text = value.length === 0 ? allLabel : value.length === 1 ? labelOf(value[0]!) : `${value.length} ${noun}`;
  const q = query.toLowerCase();
  const visible = options.filter((o) => o.label.toLowerCase().includes(q) || o.key.toLowerCase().includes(q));
  const toggle = (key: string) => onChange(selected.has(key) ? value.filter((k) => k !== key) : [...value, key]);

  return (
    <Popover.Root>
      <div className={`${s.pill} ${value.length > 0 ? s.pillActive : ''}`}>
        <Popover.Trigger asChild>
          <button type="button" className={s.pillInner}>
            {text} <span className={s.caret}>▾</span>
          </button>
        </Popover.Trigger>
        {value.length > 0 && (
          <button type="button" className={s.pillClear} aria-label={`Clear ${noun}`} onClick={() => onChange([])}>
            ×
          </button>
        )}
      </div>
      <Popover.Portal>
        <Popover.Content className={s.popover} sideOffset={6} align="start" collisionPadding={12}>
          <input
            className={s.search}
            placeholder={`Search ${noun}`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <div className={s.optionList}>
            {visible.map((o) => (
              <label key={o.key} className={s.optionRow}>
                <input type="checkbox" checked={selected.has(o.key)} onChange={() => toggle(o.key)} />
                <span>{o.label}</span>
              </label>
            ))}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function CalendarIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <rect x="2" y="3" width="12" height="11" rx="2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <path d="M8 2v8M4.5 6.5 8 10l3.5-3.5M2.5 13.5h11" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12.5 1.5v3h-3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
