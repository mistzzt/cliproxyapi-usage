import { useMemo, useState, type ReactNode } from 'react';
import type { BreakdownResponse } from '@/types/api';
import { bucketLabel, fmtTimestamp } from '@/utils/usage/dates';
import { displayName, DIMENSION_LABEL, MIX_DIMENSION, type EntityLookup } from '@/utils/usage/entities';
import { fmtCompact, fmtMetric, fmtPct } from '@/utils/usage/format';
import { priceCell } from '@/utils/usage/price';
import {
  buildRichRows,
  DEFAULT_SORT,
  mixCaption,
  nextSort,
  sortRichRows,
  type RichRow,
  type SortKey,
  type SortState,
} from '@/utils/usage/richRows';
import { avgLatency, fmtLatency, fmtSpend, tokens } from '@/utils/usage/totals';
import { METRIC_WORD, type Metric } from '@/utils/usage/viewState';
import ChangeBadge from './ChangeBadge';
import shared from './Shared.module.scss';
import s from './RichRows.module.scss';

const FAIL_HIGHLIGHT = 0.1;

/** One row per entity: share, change, a time-aligned trend strip, and a 100% mix bar of the paired dimension. */
export default function RichRows({
  data,
  metric,
  lookup,
  onSelect,
}: {
  data: BreakdownResponse;
  metric: Metric;
  lookup: EntityLookup;
  onSelect: (key: string) => void;
}) {
  const { dim, bucket } = data;
  const mixDim = MIX_DIMENSION[dim];
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [hover, setHover] = useState<number | null>(null);

  const buckets = useMemo(() => data.buckets.map((b) => Date.parse(b)), [data.buckets]);
  const rows = useMemo(() => buildRichRows(data.rows, dim, metric), [data.rows, dim, metric]);
  const sorted = useMemo(() => sortRichRows(rows, sort, (r) => displayName(dim, r.label)), [rows, sort, dim]);
  const maxShare = Math.max(0, ...rows.map((r) => r.share));
  const hasPrevious = data.rows.some((r) => r.previous !== null);
  const th = (key: SortKey, label: string, num = true) => (
    <SortHeader sortKey={key} sort={sort} onSort={setSort} num={num}>
      {label}
    </SortHeader>
  );

  return (
    <div className={s.wrap}>
      <table className={s.table}>
        <thead>
          <tr>
            {th('name', DIMENSION_LABEL[dim], false)}
            {th('share', `Share of ${METRIC_WORD[metric]}`)}
            {th('cost', 'Spend')}
            {th('tokens', 'Tokens')}
            {hasPrevious && th('change', 'Δ vs prev.')}
            <th className={s.trendHead}>
              {hover !== null ? <b>{bucketLabel(buckets[hover] ?? 0, bucket)}</b> : <>Trend by {bucket}, own scale</>}
            </th>
            <th>{DIMENSION_LABEL[mixDim]} mix</th>
            {dim === 'model' && th('cacheHit', 'Cache hit')}
            {dim === 'model' && (
              <th className={s.num} title="Price per 1M tokens used for cost: input / output / cache read">
                Price / 1M
              </th>
            )}
            {dim === 'model' && th('latency', 'Latency')}
            {dim === 'credential' && th('lastUsed', 'Last used')}
            {th('fail', 'Fail')}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <Row
              key={r.key}
              row={r}
              dim={dim}
              mixDim={mixDim}
              metric={metric}
              lookup={lookup}
              buckets={buckets}
              bucket={bucket}
              maxShare={maxShare}
              hasPrevious={hasPrevious}
              hover={hover}
              onHover={setHover}
              onSelect={onSelect}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({
  row: r,
  dim,
  mixDim,
  metric,
  lookup,
  buckets,
  bucket,
  maxShare,
  hasPrevious,
  hover,
  onHover,
  onSelect,
}: {
  row: RichRow;
  dim: BreakdownResponse['dim'];
  mixDim: BreakdownResponse['dim'];
  metric: Metric;
  lookup: EntityLookup;
  buckets: readonly number[];
  bucket: BreakdownResponse['bucket'];
  maxShare: number;
  hasPrevious: boolean;
  hover: number | null;
  onHover: (i: number | null) => void;
  onSelect: (key: string) => void;
}) {
  const color = lookup.color(dim, r.key);
  const mixName = (e: { label: string }) => displayName(mixDim, e.label);
  let caption = '';
  if (hover !== null) {
    const v = r.trend[hover] ?? 0;
    caption = v > 0 ? fmtMetric(v, metric) : 'none';
  } else if (r.peak) {
    caption = `peak ${fmtMetric(r.peak.value, metric)} · ${bucketLabel(buckets[r.peak.index] ?? 0, bucket)}`;
  }

  return (
    <tr className={s.clickRow} onClick={() => onSelect(r.key)}>
      <td>
        <span className={s.nameCell}>
          <i className={shared.swatch} style={{ background: color }} />
          <span title={r.key}>{displayName(dim, r.label)}</span>
        </span>
      </td>
      <td className={s.num}>
        <span className={s.shareCell}>
          <span className={s.shareTrack}>
            <span className={s.shareFill} style={{ width: `${maxShare ? (r.share / maxShare) * 100 : 0}%`, background: color }} />
          </span>
          <b>{fmtPct(r.share)}</b>
        </span>
      </td>
      <td className={s.num}>{fmtSpend(r.totals)}</td>
      <td className={s.num}>{fmtCompact(tokens(r.totals))}</td>
      {hasPrevious && (
        <td className={s.num}>
          <ChangeBadge value={r.change} />
        </td>
      )}
      <td className={s.trendCell}>
        <MiniColumns values={r.trend} color={color} hover={hover} onHover={onHover} />
        <div className={s.caption}>{caption}</div>
      </td>
      <td>
        <div className={s.mixBar}>
          {r.parts.map((p) => (
            <span
              key={p.entry.key}
              style={{ flexGrow: p.value, background: lookup.color(mixDim, p.entry.key) }}
              title={`${mixName(p.entry)}: ${fmtMetric(p.value, metric)} (${fmtPct(p.share)})`}
            />
          ))}
        </div>
        <div className={s.mixCaption}>{mixCaption(r.parts, mixName)}</div>
      </td>
      {dim === 'model' && <td className={s.num}>{r.cacheHit === null ? 'n/a' : `${(r.cacheHit * 100).toFixed(1)}%`}</td>}
      {dim === 'model' && <PriceTd row={r} />}
      {dim === 'model' && <td className={s.num}>{fmtLatency(avgLatency(r.totals))}</td>}
      {dim === 'credential' && <td className={s.num}>{r.lastUsed === null ? 'never' : fmtTimestamp(r.lastUsed)}</td>}
      <td className={`${s.num} ${r.fail >= FAIL_HIGHLIGHT ? s.failHigh : ''}`}>{fmtPct(r.fail)}</td>
    </tr>
  );
}

function PriceTd({ row }: { row: RichRow }) {
  const cell = priceCell(row.price);
  return (
    <td className={s.num} title={cell.title ?? undefined}>
      {cell.text}
      {cell.tiered && <span className={s.tieredTag}>tiered</span>}
    </td>
  );
}

function SortHeader({
  sortKey,
  sort,
  onSort,
  num,
  children,
}: {
  sortKey: SortKey;
  sort: SortState;
  onSort: (next: SortState) => void;
  num: boolean;
  children: ReactNode;
}) {
  const active = sort.key === sortKey;
  return (
    <th className={num ? s.num : undefined} aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : undefined}>
      <button type="button" className={s.sortBtn} onClick={() => onSort(nextSort(sort, sortKey))}>
        {children}
        {active ? (sort.desc ? ' ↓' : ' ↑') : ''}
      </button>
    </th>
  );
}

const TREND_W = 200;
const TREND_H = 30;

/** Time-aligned column strip on its own scale; hovering a column highlights that bucket in every row. */
function MiniColumns({
  values,
  color,
  hover,
  onHover,
}: {
  values: readonly number[];
  color: string;
  hover: number | null;
  onHover: (i: number | null) => void;
}) {
  const max = Math.max(0, ...values);
  const n = Math.max(values.length, 1);
  const bw = TREND_W / n;
  const inset = bw > 3 ? bw * 0.15 : 0;
  const barW = Math.max(bw - 2 * inset, 1);
  return (
    <svg
      width={TREND_W}
      height={TREND_H}
      className={s.mini}
      onMouseMove={(e) => {
        const x = e.clientX - e.currentTarget.getBoundingClientRect().left;
        onHover(Math.min(n - 1, Math.max(0, Math.floor(x / bw))));
      }}
      onMouseLeave={() => onHover(null)}
    >
      {hover !== null && <rect x={hover * bw} width={Math.max(bw, 2)} y={0} height={TREND_H} className={s.miniHover} />}
      <line x1={0} x2={TREND_W} y1={TREND_H - 0.5} y2={TREND_H - 0.5} className={s.miniBase} />
      {max > 0 &&
        values.map((v, i) => {
          if (v <= 0) return null;
          const h = Math.max((v / max) * (TREND_H - 2), 1);
          return (
            <rect
              key={i}
              x={i * bw + inset}
              width={barW}
              y={TREND_H - 1 - h}
              height={h}
              rx={1}
              fill={color}
              opacity={hover === null || hover === i ? 1 : 0.45}
            />
          );
        })}
    </svg>
  );
}
