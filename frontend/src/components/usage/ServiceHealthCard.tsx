import Card from '@/components/ui/Card';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import type { HealthResponse } from '@/types/api';
import styles from './ServiceHealthCard.module.scss';

export interface ServiceHealthCardProps {
  data: HealthResponse | null;
  loading: boolean;
}

export default function ServiceHealthCard({ data, loading }: ServiceHealthCardProps) {
  const renderContent = () => {
    if (loading) {
      return (
        <div className={styles.center}>
          <LoadingSpinner />
        </div>
      );
    }

    if (data === null) {
      return <div className={styles.center}>No data</div>;
    }

    const failedRate = (data.failed_rate * 100).toFixed(1);

    return (
      <div className={styles.tiles}>
        <div className={styles.tile}>
          <span className={styles.tileLabel}>Total Requests</span>
          <span className={styles.tileValue}>{data.total_requests.toLocaleString()}</span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}>Failed</span>
          <span className={`${styles.tileValue} ${data.failed > 0 ? styles.valueError : ''}`}>
            {data.failed.toLocaleString()}
          </span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}>Failed Rate</span>
          <span className={`${styles.tileValue} ${data.failed_rate > 0 ? styles.valueError : ''}`}>
            {failedRate}%
          </span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}>Latency p50 (ms)</span>
          <span className={styles.tileValue}>{data.latency.p50.toFixed(0)}</span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}>Latency p95 (ms)</span>
          <span className={styles.tileValue}>{data.latency.p95.toFixed(0)}</span>
        </div>
        <div className={styles.tile}>
          <span className={styles.tileLabel}>Latency p99 (ms)</span>
          <span className={styles.tileValue}>{data.latency.p99.toFixed(0)}</span>
        </div>
      </div>
    );
  };

  return <Card title="Service Health">{renderContent()}</Card>;
}
