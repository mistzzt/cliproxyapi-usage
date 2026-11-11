import { useMemo } from 'react';
import { fmtMetric, fmtPct } from '@/utils/usage/format';
import { inkOn } from '@/utils/usage/palette';
import { shareSegments, type ShareItem } from '@/utils/usage/share';
import type { Metric } from '@/utils/usage/viewState';
import shared from './Shared.module.scss';
import s from './ShareBar.module.scss';

/** 100% part-to-whole bar, one segment per entity, with a legend; compact drops in-bar labels and legend values. */
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
    return shareSegments(items).map((sg) => {
      const name = labels.get(sg.key) ?? sg.key;
      return { ...sg, name, color: slotColor(sg.slot), detail: `${name}: ${fmtMetric(sg.value, metric)} (${fmtPct(sg.share)})` };
    });
  }, [items, slotColor, metric]);

  return (
    <div className={s.block}>
      <div className={`${s.bar} ${compact ? s.compact : ''}`}>
        {segments.map((sg) => {
          const style = { flexGrow: sg.value, background: sg.color, color: inkOn(sg.color) };
          const label = !compact && sg.share >= 0.07 ? fmtPct(sg.share) : '';
          // A span when not clickable: browsers suppress the title tooltip on disabled buttons.
          return onSelect ? (
            <button type="button" key={sg.key} className={s.segment} style={style} title={sg.detail} onClick={() => onSelect(sg.key)}>
              {label}
            </button>
          ) : (
            <span key={sg.key} className={`${s.segment} ${s.static}`} style={style} title={sg.detail}>
              {label}
            </span>
          );
        })}
      </div>
      <div className={s.legend}>
        {segments.map((sg) => (
          <span key={sg.key} className={s.legendItem} title={sg.detail}>
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
