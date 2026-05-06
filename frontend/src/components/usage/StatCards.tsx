import {
  Chart as ChartJS,
  LineElement,
  PointElement,
  LinearScale,
  CategoryScale,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import type { ReactNode } from 'react';
import type { OverviewResponse, SparklinePoint } from '@/types/api';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { CostCell } from './CostCell';
import styles from './StatCards.module.scss';

ChartJS.register(LineElement, PointElement, LinearScale, CategoryScale);

const SPARKLINE_OPTIONS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false as const,
  plugins: { legend: { display: false }, tooltip: { enabled: false } },
  scales: {
    x: { display: false },
    y: { display: false },
  },
  elements: { point: { radius: 0 }, line: { tension: 0.3 } },
};

function sparklineData(points: SparklinePoint[], color: string) {
  return {
    labels: points.map((p) => p.ts),
    datasets: [
      {
        data: points.map((p) => p.value),
        borderColor: color,
        borderWidth: 1.5,
        fill: false,
      },
    ],
  };
}

interface CardDef {
  key: keyof OverviewResponse['sparklines'];
  label: string;
  color: string;
  renderValue: (o: OverviewResponse) => ReactNode;
}

const CARDS: CardDef[] = [
  {
    key: 'requests',
    label: 'Requests',
    color: '#6aa7ff',
    renderValue: (o) => o.totals.requests.toLocaleString(),
  },
  {
    key: 'tokens',
    label: 'Tokens',
    color: '#a78bfa',
    renderValue: (o) => o.totals.tokens.toLocaleString(),
  },
  {
    key: 'rpm',
    label: 'RPM',
    color: '#34d399',
    renderValue: (o) => o.totals.rpm.toFixed(2),
  },
  {
    key: 'tpm',
    label: 'TPM',
    color: '#fb923c',
    renderValue: (o) => o.totals.tpm.toFixed(1),
  },
  {
    key: 'cost',
    label: 'Cost',
    color: '#fbbf24',
    renderValue: (o) => (
      <CostCell cost={o.totals.cost} status={o.totals.cost_status} decimals={2} />
    ),
  },
];

interface StatCardsProps {
  overview: OverviewResponse | null;
  loading: boolean;
}

export default function StatCards({ overview, loading }: StatCardsProps) {
  return (
    <div className={styles.grid}>
      {CARDS.map((card) => {
        const points = overview?.sparklines[card.key] ?? [];
        const value = overview ? card.renderValue(overview) : '—';
        return (
          <div key={card.key} className={styles.card}>
            <div className={styles.label}>{card.label}</div>
            <div className={styles.value}>
              {loading ? <LoadingSpinner /> : value}
            </div>
            <div className={styles.sparkline}>
              {loading ? null : points.length > 0 ? (
                <Line
                  data={sparklineData(points, card.color)}
                  options={SPARKLINE_OPTIONS}
                />
              ) : (
                <div className={styles.placeholder} />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
