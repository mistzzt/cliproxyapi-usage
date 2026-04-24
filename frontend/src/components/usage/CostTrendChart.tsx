import '@/components/charts/chart-setup';
import { useMemo } from 'react';
import type { ChartOptions, ChartData, ScriptableContext } from 'chart.js';
import { Line } from 'react-chartjs-2';
import type { TimeseriesResponse } from '@/types/api';
import Card from '@/components/ui/Card';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { modelColor, ALL_MODEL_COLOR } from '@/components/charts/palette';
import { useThemeStore } from '@/stores';
import { readThemeColors, buildTooltipConfig } from '@/components/charts/theme-colors';
import styles from './CostTrendChart.module.scss';

const ALL_BG_FALLBACK = 'rgba(245,158,11,0.15)';

function buildAllGradient(ctx: ScriptableContext<'line'>): CanvasGradient | string {
  const chart = ctx.chart;
  const area = chart.chartArea;
  if (!area) return ALL_BG_FALLBACK;
  const gradient = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
  gradient.addColorStop(0, 'rgba(245,158,11,0.28)');
  gradient.addColorStop(0.6, 'rgba(245,158,11,0.12)');
  gradient.addColorStop(1, 'rgba(245,158,11,0.02)');
  return gradient;
}

function buildChartOptions(buckets: string[]): ChartOptions<'line'> {
  const colors = readThemeColors();
  const { grid, tick } = colors;

  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: buildTooltipConfig(colors),
    },
    scales: {
      x: {
        ticks: {
          color: tick,
          maxTicksLimit: Math.min(10, buckets.length),
          maxRotation: 0,
          autoSkip: true,
        },
        grid: { color: grid },
      },
      y: {
        beginAtZero: true,
        ticks: {
          color: tick,
          callback: (value) => `$${Number(value).toFixed(4)}`,
        },
        grid: { color: grid },
      },
    },
  };
}

export interface CostTrendChartProps {
  data: TimeseriesResponse | null;
  loading: boolean;
  hasPricing: boolean;
}

export default function CostTrendChart({ data, loading, hasPricing }: CostTrendChartProps) {
  const theme = useThemeStore((s) => s.theme);
  const { chartData, chartOptions, isEmpty } = useMemo(() => {
    if (!hasPricing || !data || data.buckets.length === 0) {
      return {
        chartData: { labels: [], datasets: [] } as ChartData<'line'>,
        chartOptions: {} as ChartOptions<'line'>,
        isEmpty: true,
      };
    }

    // Sort entries so __all__ is last — Chart.js renders datasets in array
    // order (last on top), keeping the aggregate visually prominent.
    const entries = Object.entries(data.series).sort(([a], [b]) => {
      if (a === '__all__') return 1;
      if (b === '__all__') return -1;
      return 0;
    });

    const datasets = entries.map(([key, values]) => {
      const isAll = key === '__all__';
      const color = isAll ? ALL_MODEL_COLOR : modelColor(key);
      return {
        label: isAll ? 'All' : key,
        data: values,
        borderColor: color,
        backgroundColor: isAll ? buildAllGradient : 'transparent',
        pointBackgroundColor: color,
        pointBorderColor: color,
        borderWidth: isAll ? 3 : 1.5,
        pointRadius: data.buckets.length > 60 ? 0 : isAll ? 3 : 2,
        fill: isAll,
        tension: 0.35,
      };
    });

    return {
      chartData: { labels: data.buckets, datasets } as ChartData<'line'>,
      chartOptions: buildChartOptions(data.buckets),
      isEmpty: false,
    };
    // theme → re-read CSS vars on toggle (buildChartOptions reads live DOM)
  }, [data, hasPricing, theme]);

  return (
    <Card title="Cost Trend">
      {loading ? (
        <div className={styles.center}>
          <LoadingSpinner />
        </div>
      ) : !hasPricing ? (
        <div className={styles.center}>
          <span className={styles.emptyText}>Pricing data unavailable</span>
        </div>
      ) : isEmpty ? (
        <div className={styles.center}>
          <span className={styles.emptyText}>No data available</span>
        </div>
      ) : (
        <div className={styles.chartArea}>
          <Line data={chartData} options={chartOptions} />
        </div>
      )}
    </Card>
  );
}
