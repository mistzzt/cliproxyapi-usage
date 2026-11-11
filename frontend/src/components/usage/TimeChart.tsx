import '@/components/charts/chart-setup';
import { useMemo } from 'react';
import type { ChartData, ChartOptions } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import { buildTooltipConfig, readThemeColors } from '@/components/charts/theme-colors';
import { BAR_GEOMETRY, Y_AXIS_WIDTH } from '@/components/charts/time-axis';
import type { Bucket } from '@/types/api';
import { bucketLabel, tickLabel } from '@/utils/usage/dates';
import { fmtMetric } from '@/utils/usage/format';
import type { Theme } from '@/utils/usage/palette';
import type { ChartLayer } from '@/utils/usage/stack';
import type { Metric } from '@/utils/usage/viewState';

/** Stacked bars over time; `theme` only forces theme colors to be re-read. */
export default function TimeChart({
  buckets,
  bucket,
  layers,
  metric,
  theme,
  height = 200,
}: {
  buckets: readonly number[];
  bucket: Bucket;
  layers: readonly ChartLayer[];
  metric: Metric;
  theme: Theme;
  height?: number;
}) {
  const colors = useMemo(() => {
    void theme;
    return readThemeColors();
  }, [theme]);

  const data = useMemo<ChartData<'bar'>>(
    () => ({
      labels: [...buckets],
      datasets: layers.map((l) => ({
        label: l.label,
        data: l.values,
        backgroundColor: l.color,
        borderColor: colors.surface,
        borderWidth: { top: 2, bottom: 0, left: 0, right: 0 },
        borderSkipped: false,
        ...BAR_GEOMETRY,
      })),
    }),
    [buckets, layers, colors],
  );

  const options = useMemo<ChartOptions<'bar'>>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: {
          position: 'bottom',
          labels: { color: colors.text, boxWidth: 10, boxHeight: 10, usePointStyle: true, pointStyle: 'rectRounded' },
        },
        tooltip: {
          ...buildTooltipConfig(colors),
          itemSort: (a, b) => (b.raw as number) - (a.raw as number),
          filter: (item) => (item.raw as number) > 0,
          callbacks: {
            title: (items) => bucketLabel(buckets[items[0]?.dataIndex ?? 0] ?? 0, bucket),
            label: (item) => ` ${item.dataset.label}: ${fmtMetric(item.raw as number, metric)}`,
            footer: (items) => {
              const total = items.reduce((acc, i) => acc + (i.raw as number), 0);
              return items.length > 1 ? `Total: ${fmtMetric(total, metric)}` : '';
            },
          },
        },
      },
      scales: {
        x: {
          stacked: true,
          grid: { display: false },
          border: { color: colors.grid },
          ticks: {
            color: colors.tick,
            maxRotation: 0,
            autoSkipPadding: 16,
            callback: (_v, i) => tickLabel(buckets[i] ?? 0, bucket),
          },
        },
        y: {
          stacked: true,
          afterFit: (axis) => {
            axis.width = Y_AXIS_WIDTH;
          },
          grid: { color: colors.grid },
          border: { display: false },
          ticks: { color: colors.tick, maxTicksLimit: 5, callback: (v) => fmtMetric(Number(v), metric) },
        },
      },
    }),
    [buckets, bucket, metric, colors],
  );

  return (
    <div style={{ height }}>
      <Bar data={data} options={options} />
    </div>
  );
}
