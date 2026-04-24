import '@/components/charts/chart-setup';
import { useMemo } from 'react';
import type { ChartOptions, ChartData } from 'chart.js';
import { Line } from 'react-chartjs-2';
import type { TimeseriesResponse } from '@/types/api';
import Card from '@/components/ui/Card';
import Button from '@/components/ui/Button';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { modelColor, ALL_MODEL_COLOR } from '@/components/charts/palette';
import { useThemeStore } from '@/stores';
import { readThemeColors, buildTooltipConfig } from '@/components/charts/theme-colors';
import styles from './UsageChart.module.scss';

function buildChartOptions(buckets: string[], isMobile: boolean): ChartOptions<'line'> {
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
        ticks: {
          color: tick,
          maxTicksLimit: Math.min(maxTicks, buckets.length),
          maxRotation: isMobile ? 45 : 0,
          autoSkip: true,
        },
        grid: { color: grid },
      },
      y: {
        beginAtZero: true,
        ticks: { color: tick },
        grid: { color: grid },
      },
    },
  };
}

export interface UsageChartProps {
  title: string;
  data: TimeseriesResponse | null;
  loading: boolean;
  period: 'hour' | 'day';
  onPeriodChange: (p: 'hour' | 'day') => void;
  isMobile: boolean;
}

export default function UsageChart({
  title,
  data,
  loading,
  period,
  onPeriodChange,
  isMobile,
}: UsageChartProps) {
  const theme = useThemeStore((s) => s.theme);
  const { chartData, chartOptions, isEmpty } = useMemo(() => {
    if (!data || data.buckets.length === 0) {
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
        backgroundColor: color,
        pointBackgroundColor: color,
        pointBorderColor: color,
        borderWidth: isAll ? 3 : 1.5,
        pointRadius: data.buckets.length > 60 ? 0 : isAll ? 3 : 2,
        fill: false,
        tension: 0.3,
      };
    });

    return {
      chartData: { labels: data.buckets, datasets } as ChartData<'line'>,
      chartOptions: buildChartOptions(data.buckets, isMobile),
      isEmpty: false,
    };
    // theme → re-read CSS vars on toggle (buildChartOptions reads live DOM)
  }, [data, isMobile, theme]);

  const periodToggle = (
    <div className={styles.periodToggle}>
      <Button
        variant={period === 'hour' ? 'primary' : 'secondary'}
        onClick={() => onPeriodChange('hour')}
      >
        Hour
      </Button>
      <Button
        variant={period === 'day' ? 'primary' : 'secondary'}
        onClick={() => onPeriodChange('day')}
      >
        Day
      </Button>
    </div>
  );

  return (
    <Card title={title} action={periodToggle}>
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
            {chartData.datasets.map((ds, i) => (
              <div key={`${ds.label ?? ''}-${i}`} className={styles.legendItem}>
                <span
                  className={styles.legendDot}
                  style={{ backgroundColor: ds.borderColor as string }}
                />
                <span className={styles.legendLabel} title={ds.label}>
                  {ds.label}
                </span>
              </div>
            ))}
          </div>
          <div className={styles.chartArea}>
            <Line data={chartData} options={chartOptions} />
          </div>
        </div>
      )}
    </Card>
  );
}
