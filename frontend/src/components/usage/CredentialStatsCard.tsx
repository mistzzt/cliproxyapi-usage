/**
 * CredentialStatsCard — displays per-credential request/token/cost stats.
 *
 * NOTE: Credential-health rendering (success-rate heatmap / health blocks) is
 * intentionally omitted compared to the reference implementation in
 * Cli-Proxy-API-Management-Center. The backend in this project does not expose
 * per-credential health data, so there is nothing to render.
 */
import { useMemo, useState } from 'react';
import Card from '@/components/ui/Card';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import type { CredentialStat } from '@/types/api';
import { sortRows } from './sort';
import type { SortState } from './sort';
import { CostCell } from './CostCell';
import styles from './CredentialStatsCard.module.scss';

export interface CredentialStatsCardProps {
  rows: CredentialStat[];
  loading: boolean;
  hasPricing?: boolean;
}

type CredSortKey = 'source' | 'requests' | 'total_tokens' | 'failed' | 'cost';

function arrow(state: SortState<CredSortKey>, key: CredSortKey): string {
  if (state.key !== key) return '';
  return state.order === 'asc' ? ' ▲' : ' ▼';
}

export default function CredentialStatsCard({ rows, loading }: CredentialStatsCardProps) {
  const [sort, setSort] = useState<SortState<CredSortKey>>({ key: 'requests', order: 'desc' });

  const handleSort = (key: CredSortKey) => {
    setSort((prev) => {
      if (prev.key === key) {
        return { key, order: prev.order === 'asc' ? 'desc' : 'asc' };
      }
      return { key, order: 'desc' };
    });
  };

  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);

  return (
    <Card title="Credential Stats">
      <div className={styles.body}>
        {loading ? (
          <div className={styles.center}>
            <LoadingSpinner />
          </div>
        ) : sorted.length === 0 ? (
          <div className={styles.center}>No data</div>
        ) : (
          <div className={styles.tableWrapper}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('source')}>
                      Source{arrow(sort, 'source')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('requests')}>
                      Requests{arrow(sort, 'requests')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('total_tokens')}>
                      Total Tokens{arrow(sort, 'total_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('failed')}>
                      Failed{arrow(sort, 'failed')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('cost')}>
                      Cost{arrow(sort, 'cost')}
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row, index) => (
                  <tr key={`${row.source}-${index}`}>
                    <td>{row.source}</td>
                    <td>{row.requests.toLocaleString()}</td>
                    <td>{row.total_tokens.toLocaleString()}</td>
                    <td>{row.failed.toLocaleString()}</td>
                    <td><CostCell cost={row.cost} status={row.cost_status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}
