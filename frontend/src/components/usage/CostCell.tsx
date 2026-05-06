import type { CostStatus } from '@/types/api';
import styles from './CostCell.module.scss';

export interface CostCellProps {
  cost: number | null;
  status: CostStatus;
  /** Number of decimal places for $-formatting. Defaults to 4. */
  decimals?: number;
}

const TOOLTIP: Record<CostStatus, string> = {
  live: '',
  partial_missing: 'Pricing unavailable for some models — partial total',
  missing: 'No pricing available for this model',
};

export function CostCell({ cost, status, decimals = 4 }: CostCellProps) {
  const className = status === 'live' ? undefined : styles.warning;
  const tooltip = TOOLTIP[status] || undefined;
  const text = cost === null ? '—' : `$${cost.toFixed(decimals)}`;
  return (
    <span className={className} title={tooltip}>
      {text}
    </span>
  );
}
