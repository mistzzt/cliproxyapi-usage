import { useMemo } from 'react';
import { fmtMetric, fmtPct } from '@/utils/usage/format';
import { inkOn } from '@/utils/usage/palette';
import { shareSegments, type ShareItem } from '@/utils/usage/share';
import type { Metric } from '@/utils/usage/viewState';
import shared from './Shared.module.scss';
import s from './ShareBar.module.scss';

/** 100% part-to-whole bar (top slots plus Other) with a legend; compact drops in-bar labels and legend values. */
export default function ShareBar({
  items,
  metric,
  slotColor,
  onSelect,
  compact = false,
}: {
  items: readonly ShareItem[];
  metric: Metric;
  slotColor: (slot: number | null) => string;
  onSelect?: (key: string) => void;
  compact?: boolean;
}) {
  const segments = useMemo(() => {
    const labels = new Map(items.map((it) => [it.key, it.label]));
    return shareSegments(items).map((sg) => ({
      ...sg,
      id: sg.kind === 'entity' ? sg.key : 'other',
      name: sg.kind === 'entity' ? (labels.get(sg.key) ?? sg.key) : sg.label,
      color: slotColor(sg.kind === 'entity' ? sg.slot : null),
    }));
  }, [items, slotColor]);

  return (
    <div className={s.block}>
      <div className={`${s.bar} ${compact ? s.compact : ''}`}>
        {segments.map((sg) => (
          <button
            type="button"
            key={sg.id}
            className={s.segment}
            style={{ flexGrow: sg.value, background: sg.color, color: inkOn(sg.color) }}
            title={`${sg.name}: ${fmtMetric(sg.value, metric)} (${fmtPct(sg.share)})`}
            disabled={sg.kind === 'other' || !onSelect}
            onClick={() => sg.kind === 'entity' && onSelect?.(sg.key)}
          >
            {!compact && sg.share >= 0.07 ? fmtPct(sg.share) : ''}
          </button>
        ))}
      </div>
      <div className={s.legend}>
        {segments.map((sg) => (
          <span key={sg.id} className={s.legendItem}>
            <i style={{ background: sg.color }} />
            <span className={s.legendName}>{sg.name}</span>
            <span className={shared.muted}>
              {fmtPct(sg.share)}
              {!compact && ` · ${fmtMetric(sg.value, metric)}`}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
