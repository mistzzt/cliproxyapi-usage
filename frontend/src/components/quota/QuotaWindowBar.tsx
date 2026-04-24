import type { QuotaWindow } from '@/types/api';
import { formatRelative } from '@/utils/time';
import styles from './QuotaWindowBar.module.scss';

interface QuotaWindowBarProps {
  window: QuotaWindow;
}

export default function QuotaWindowBar({ window }: QuotaWindowBarProps) {
  const percent = window.used_percent;
  const percentLabel = percent !== null ? `${Math.round(percent)}%` : '—';
  const barWidth = percent !== null ? Math.min(100, Math.max(0, percent)) : 0;

  return (
    <div className={styles.row}>
      <div className={styles.labelRow}>
        <span className={styles.label}>{window.label}</span>
        <span className={styles.percent}>{percentLabel}</span>
      </div>
      <div className={styles.track}>
        <div className={styles.fill} style={{ width: `${barWidth}%` }} />
      </div>
      {window.resets_at !== null && (
        <div className={styles.resetLine}>
          resets {formatRelative(window.resets_at)}
        </div>
      )}
    </div>
  );
}
