import type { ReactNode } from 'react';
import b from '@/components/ui/Busy.module.scss';
import type { SummaryResponse } from '@/types/api';
import { change, fmtCompact, fmtPct } from '@/utils/usage/format';
import {
  avgLatency,
  failureRate,
  failureRateSeries,
  fmtLatency,
  fmtSpend,
  latencySeries,
  SPEND_LOWER_BOUND_NOTE,
  tokenSeries,
  tokens,
} from '@/utils/usage/totals';
import ChangeBadge from './ChangeBadge';
import shared from './Shared.module.scss';
import s from './KpiStrip.module.scss';

export default function KpiStrip({ summary, accent, busy }: { summary: SummaryResponse; accent: string; busy: boolean }) {
  const cur = summary.current;
  const prev = summary.previous;
  const latency = avgLatency(cur);
  const tiles = [
    {
      label: 'Spend',
      value: fmtSpend(cur),
      title: cur.cost_status === 'live' ? undefined : SPEND_LOWER_BOUND_NOTE,
      change: change(cur.cost ?? 0, prev?.cost),
      spark: summary.series.cost,
    },
    { label: 'Tokens', value: fmtCompact(tokens(cur)), change: change(tokens(cur), prev && tokens(prev)), spark: tokenSeries(summary.series) },
    {
      label: 'Requests',
      value: fmtCompact(cur.requests),
      change: change(cur.requests, prev?.requests),
      spark: summary.series.requests,
    },
    {
      label: 'Failure rate',
      value: fmtPct(failureRate(cur)),
      change: change(failureRate(cur), prev && failureRate(prev)),
      spark: failureRateSeries(summary.series),
    },
    {
      label: 'Avg latency',
      value: fmtLatency(latency),
      title: 'Successful requests only',
      change: latency === null ? null : change(latency, prev && avgLatency(prev)),
      spark: latencySeries(summary.series),
    },
  ];
  return (
    <div className={busy ? `${s.strip} ${b.busy}` : s.strip} aria-busy={busy}>
      {tiles.map((t) => (
        <StatTile
          key={t.label}
          label={t.label}
          value={t.value}
          title={t.title}
          foot={
            t.change !== null && (
              <>
                <ChangeBadge value={t.change} />
                <span className={shared.muted}>vs prev. period</span>
              </>
            )
          }
        >
          {t.spark && <Sparkline values={t.spark} color={accent} />}
        </StatTile>
      ))}
    </div>
  );
}

export function StatTile({
  label,
  value,
  title,
  foot,
  children,
  className,
}: {
  label: string;
  value: string;
  title?: string | undefined;
  foot: ReactNode;
  children?: ReactNode;
  className?: string | undefined;
}) {
  return (
    <div className={`${s.tile} ${className ?? ''}`}>
      <div className={s.label}>{label}</div>
      <div className={s.value} title={title}>
        {value}
      </div>
      <div className={s.foot}>{foot}</div>
      {children}
    </div>
  );
}

const SPARK_W = 120;
const SPARK_H = 28;

/** A line over `values`; null values break it into separate runs. */
function Sparkline({ values, color }: { values: readonly (number | null)[]; color: string }) {
  const max = Math.max(0, ...values.map((v) => v ?? 0));
  const path =
    values.length < 2 || max === 0
      ? null
      : values
          .map((v, i) => {
            if (v === null) return '';
            const cmd = i > 0 && values[i - 1] !== null ? 'L' : 'M';
            return `${cmd}${(i / (values.length - 1)) * SPARK_W},${SPARK_H - 2 - (v / max) * (SPARK_H - 4)}`;
          })
          .join('');
  return (
    <svg width={SPARK_W} height={SPARK_H} className={s.spark} aria-hidden>
      {path && (
        <path d={path} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      )}
    </svg>
  );
}
