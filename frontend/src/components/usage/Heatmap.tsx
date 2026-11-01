import { useMemo } from 'react';
import type { MatrixResponse } from '@/types/api';
import type { EntityLookup } from '@/utils/usage/entities';
import { fmtMetric, fmtPct } from '@/utils/usage/format';
import { buildHeatmap, HEAT_STEPS, heatBin, isNotable, type HeatColumn } from '@/utils/usage/heatmap';
import { modelFamily, shortModel } from '@/utils/usage/modelNames';
import { metricValue } from '@/utils/usage/richRows';
import type { Metric } from '@/utils/usage/viewState';
import s from './Heatmap.module.scss';

const DEEP_BIN = 3;

function fill(bin: number): string {
  return `color-mix(in oklab, var(--heat) ${HEAT_STEPS[bin]}%, var(--color-surface))`;
}

function columnLabel(c: HeatColumn): string {
  return c.kind === 'more' ? `+${c.keys.length} more` : shortModel(c.key);
}

/** People x models with absolute values, model columns grouped by family, and a per-model footer. */
export default function Heatmap({
  matrix,
  metric,
  lookup,
  onSelectRow,
}: {
  matrix: MatrixResponse;
  metric: Metric;
  lookup: EntityLookup;
  onSelectRow: (key: string) => void;
}) {
  const m = useMemo(
    () =>
      buildHeatmap(
        matrix.cells.map((c) => ({ row: c.row, col: c.col, value: metricValue(c, metric) })),
        { family: modelFamily },
      ),
    [matrix, metric],
  );
  const maxCol = Math.max(0, ...m.columns.map((c) => c.total));

  return (
    <div className={s.root}>
      <div className={s.legend}>
        <span>Less</span>
        {HEAT_STEPS.map((_, b) => (
          <i key={b} style={{ background: fill(b) }} />
        ))}
        <span>More</span>
        <span className={s.legendNote}>{metric === 'cost' ? 'spend' : 'tokens'}, sqrt scale. Hover a cell for its value.</span>
      </div>
      <div className={s.scroll}>
        <table className={s.grid}>
          <thead>
            <tr className={s.groupRow}>
              <th />
              {m.groups.map((g, i) => (
                <th key={i} colSpan={g.span}>
                  {g.name && <span>{g.name}</span>}
                </th>
              ))}
            </tr>
            <tr className={s.colRow}>
              <th />
              {m.columns.map((c) => (
                <th key={c.kind === 'more' ? '+more' : c.key} title={c.kind === 'more' ? c.keys.join('\n') : c.key}>
                  {columnLabel(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {m.rows.map((r, ri) => {
              const person = lookup.entity('person', r.key);
              return (
                <tr key={r.key}>
                  <th className={s.rowHead}>
                    <button type="button" onClick={() => onSelectRow(r.key)}>
                      <i style={{ background: lookup.color('person', r.key) }} />
                      {person.label}
                    </button>
                  </th>
                  {m.columns.map((c, ci) => {
                    const v = m.values[ri]?.[ci] ?? 0;
                    const b = heatBin(v, m.max);
                    const classes = [s.cell, b < 0 && s.empty, isNotable(v, r.total) && s.notable, b >= DEEP_BIN && s.deep];
                    return (
                      <td
                        key={ci}
                        className={classes.filter(Boolean).join(' ')}
                        style={b >= 0 ? { background: fill(b) } : undefined}
                        title={
                          b >= 0
                            ? `${person.label} × ${columnLabel(c)}\n${fmtMetric(v, metric)} · ${fmtPct(v / r.total)} of person · ${fmtPct(v / c.total)} of model`
                            : undefined
                        }
                      >
                        {b >= 0 ? fmtMetric(v, metric) : ''}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th className={s.rowHead}>All people</th>
              {m.columns.map((c, ci) => (
                <td key={ci} className={s.colFoot}>
                  <span className={s.colFootBar}>
                    <span style={{ width: `${maxCol ? (c.total / maxCol) * 100 : 0}%` }} />
                  </span>
                  {fmtMetric(c.total, metric)}
                  <span className={s.muted}> {fmtPct(m.grand ? c.total / m.grand : 0)}</span>
                </td>
              ))}
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}
