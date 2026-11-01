import { useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { DayPicker, type DateRange } from 'react-day-picker';
import 'react-day-picker/style.css';
import type { Bucket, Entity, OptionsResponse } from '@/types/api';
import {
  isValidCustomRange,
  PRESETS,
  rangeLabel,
  toLocalInput,
  type RangeSel,
  type RangeWindow,
} from '@/utils/usage/range';
import type { Granularity, Metric, ViewState } from '@/utils/usage/viewState';
import Segmented from './Segmented';
import s from './FilterBar.module.scss';

const DAY = 86_400_000;

interface FilterBarProps {
  state: ViewState;
  update: (patch: Partial<ViewState>) => void;
  win: RangeWindow;
  now: number;
  /** Bucket echoed by the API, shown on the Auto button. */
  effectiveBucket: Bucket | null;
  options: OptionsResponse | null;
  /** Advances `now` and refetches without touching the URL. */
  onRefresh: () => void;
}

export default function FilterBar({ state, update, win, now, effectiveBucket, options, onRefresh }: FilterBarProps) {
  return (
    <div className={s.bar}>
      <RangePicker value={state.range} win={win} now={now} onChange={(range) => update({ range })} />
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
        options={[
          { value: 'auto', label: effectiveBucket ? `Auto (${effectiveBucket[0]})` : 'Auto' },
          { value: 'hour', label: '1h' },
          { value: 'day', label: '1d' },
          { value: 'week', label: '1w' },
        ]}
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
      <button type="button" className={s.iconButton} aria-label="Refresh" title="Refresh" onClick={onRefresh}>
        <RefreshIcon />
      </button>
    </div>
  );
}

/** Replaces the date part of a local `YYYY-MM-DDTHH:mm` string, keeping its time of day. */
function withDate(input: string, day: Date): string {
  return `${toLocalInput(day.getTime()).slice(0, 10)}T${input.slice(11) || '00:00'}`;
}

function RangePicker({
  value,
  win,
  now,
  onChange,
}: {
  value: RangeSel;
  win: RangeWindow;
  now: number;
  onChange: (r: RangeSel) => void;
}) {
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');

  const handleOpen = (next: boolean) => {
    setOpen(next);
    if (!next) return;
    setFrom(toLocalInput(win.start ?? win.end - 30 * DAY));
    setTo(toLocalInput(win.end));
    setCustom(value.kind === 'custom');
  };

  const valid = isValidCustomRange(from, to);
  const toDate = new Date(to);
  const selected: DateRange = { from: new Date(from), to: toDate };
  const onCalendar = (r: DateRange | undefined) => {
    if (r?.from) setFrom(withDate(from, r.from));
    if (r?.to) setTo(withDate(to, r.to));
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
                <button type="button" className={s.btnGhost} onClick={() => setCustom(false)}>
                  ‹ Presets
                </button>
                <button
                  type="button"
                  className={s.btnPrimary}
                  disabled={!valid}
                  onClick={() => {
                    onChange({ kind: 'custom', start: from, end: to });
                    setOpen(false);
                  }}
                >
                  Apply
                </button>
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

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden>
      <path d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M12.5 1.5v3h-3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
