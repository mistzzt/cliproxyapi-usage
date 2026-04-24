import '@/components/charts/chart-setup';
import { useMemo } from 'react';
import type { ChartOptions, ChartData } from 'chart.js';
import { Bar } from 'react-chartjs-2';
import type { TokenBreakdownResponse } from '@/types/api';
import Card from '@/components/ui/Card';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { useThemeStore } from '@/stores';
import { readThemeColors, buildTooltipConfig } from '@/components/charts/theme-colors';
import styles from './TokenBreakdownChart.module.scss';

const STACK_COLORS = {
  input: { border: '#8b8680', bg: 'rgba(139,134,128,0.7)' },
  output: { border: '#22c55e', bg: 'rgba(34,197,94,0.7)' },
  cached: { border: '#f59e0b', bg: 'rgba(245,158,11,0.7)' },
  reasoning: { border: '#8b5cf6', bg: 'rgba(139,92,246,0.7)' },
} as const;

type StackKey = keyof typeof STACK_COLORS;
const STACK_KEYS: StackKey[] = ['input', 'output', 'cached', 'reasoning'];
const STACK_LABELS: Record<StackKey, string> = {
  input: 'Input',
  output: 'Output',
  cached: 'Cached',
  reasoning: 'Reasoning',
};

function buildChartOptions(isMobile: boolean): ChartOptions<'bar'> {
  const colors = readThemeColors();
  const { grid, tick } = colors;
  const maxTicks = isMobile ? 5 : 10;

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
        stacked: true,
        ticks: {
          color: tick,
          maxTicksLimit: maxTicks,
          maxRotation: isMobile ? 45 : 0,
          autoSkip: true,
        },
        grid: { color: grid },
      },
      y: {
        stacked: true,
        beginAtZero: true,
        ticks: { color: tick },
        grid: { color: grid },
      },
    },
  };
}

export interface TokenBreakdownChartProps {
  data: TokenBreakdownResponse | null;
  loading: boolean;
  isMobile: boolean;
}

export default function TokenBreakdownChart({
  data,
  loading,
  isMobile,
}: TokenBreakdownChartProps) {
  const theme = useThemeStore((s) => s.theme);
  const { chartData, chartOptions, isEmpty } = useMemo(() => {
    if (!data || data.buckets.length === 0) {
      return {
        chartData: { labels: [], datasets: [] } as ChartData<'bar'>,
        chartOptions: {} as ChartOptions<'bar'>,
        isEmpty: true,
      };
    }

    const datasets = STACK_KEYS.map((key) => ({
      label: STACK_LABELS[key],
      data: data[key],
      backgroundColor: STACK_COLORS[key].bg,
      borderColor: STACK_COLORS[key].border,
      borderWidth: 1,
      stack: 'tokens',
    }));

    return {
      chartData: { labels: data.buckets, datasets } as ChartData<'bar'>,
      chartOptions: buildChartOptions(isMobile),
      isEmpty: false,
    };
    // theme → re-read CSS vars on toggle (buildChartOptions reads live DOM)
  }, [data, isMobile, theme]);

  return (
    <Card title="Token Breakdown">
      {loading ? (
        <div className={styles.center}>
          <LoadingSpinner />
        </div>
      ) : isEmpty ? (
        <div className={styles.center}>
          <span className={styles.emptyText}>No data available</span>
        </div>
      ) : (
        <div className={styles.chartWrapper}>
          <div className={styles.legend} aria-label="Chart legend">
            {STACK_KEYS.map((key) => (
              <div key={key} className={styles.legendItem}>
                <span
                  className={styles.legendDot}
                  style={{ backgroundColor: STACK_COLORS[key].border }}
                />
                <span className={styles.legendLabel}>{STACK_LABELS[key]}</span>
              </div>
            ))}
          </div>
          <div className={styles.chartArea}>
            <Bar data={chartData} options={chartOptions} />
          </div>
        </div>
      )}
    </Card>
  );
}
