import '@/components/charts/chart-setup';
import { useMemo } from 'react';
import type { ChartData, ChartOptions } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import { buildTooltipConfig, readThemeColors } from '@/components/charts/theme-colors';
import { BAR_GEOMETRY, Y_AXIS_WIDTH } from '@/components/charts/time-axis';
import type { Bucket } from '@/types/api';
import { bucketLabel } from '@/utils/usage/dates';
import { fmtCompact, fmtPct } from '@/utils/usage/format';
import type { Theme } from '@/utils/usage/palette';
import { failureRateSeries } from '@/utils/usage/totals';

/** Failure rate per bucket on a fixed 0 to 100% scale, aligned with a `TimeChart` above it. */
export default function FailureStrip({
  buckets,
  bucket,
  requests,
  failed,
  theme,
}: {
  buckets: readonly number[];
  bucket: Bucket;
  requests: readonly number[];
  failed: readonly number[];
  theme: Theme;
}) {
  const colors = useMemo(() => {
    void theme;
    return readThemeColors();
  }, [theme]);

  const data = useMemo<ChartData<'bar', (number | null)[]>>(
    () => ({
      labels: [...buckets],
      datasets: [{ label: 'Failure rate', data: failureRateSeries({ requests, failed }), backgroundColor: colors.error, ...BAR_GEOMETRY }],
    }),
    [buckets, requests, failed, colors],
  );

  const options = useMemo<ChartOptions<'bar'>>(
    () => ({
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...buildTooltipConfig(colors),
          callbacks: {
            title: (items) => bucketLabel(buckets[items[0]?.dataIndex ?? 0] ?? 0, bucket),
            label: (item) => {
              const i = item.dataIndex;
              return ` Failed: ${fmtPct(item.raw as number)} (${fmtCompact(failed[i] ?? 0)} of ${fmtCompact(requests[i] ?? 0)})`;
            },
          },
        },
      },
      scales: {
        x: { display: false },
        y: {
          min: 0,
          max: 1,
          afterFit: (axis) => {
            axis.width = Y_AXIS_WIDTH;
          },
          grid: { display: false },
          border: { color: colors.grid },
          ticks: { color: colors.tick, stepSize: 1, callback: (v) => (Number(v) === 0 ? '0%' : '100%') },
        },
      },
    }),
    [buckets, bucket, requests, failed, colors],
  );

  return (
    <div style={{ height: 48 }}>
      <Bar data={data} options={options} />
    </div>
  );
}
