import { useMemo, useState } from 'react';
import Card from '@/components/ui/Card';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import type { ApiStat } from '@/types/api';
import { sortRows } from './sort';
import type { SortState } from './sort';
import styles from './ApiDetailsCard.module.scss';

export interface ApiDetailsCardProps {
  rows: ApiStat[];
  loading: boolean;
  hasPricing: boolean;
}

type ApiSortKey = 'api_key' | 'requests' | 'input_tokens' | 'output_tokens' | 'total_tokens' | 'cost' | 'failed' | 'avg_latency_ms';

function arrow(state: SortState<ApiSortKey>, key: ApiSortKey): string {
  if (state.key !== key) return '';
  return state.order === 'asc' ? ' ▲' : ' ▼';
}

export default function ApiDetailsCard({ rows, loading, hasPricing }: ApiDetailsCardProps) {
  const [sort, setSort] = useState<SortState<ApiSortKey>>({ key: 'requests', order: 'desc' });

  const handleSort = (key: ApiSortKey) => {
    setSort((prev) => {
      if (prev.key === key) {
        return { key, order: prev.order === 'asc' ? 'desc' : 'asc' };
      }
      return { key, order: 'desc' };
    });
  };

  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);

  const renderCost = (cost: number | null) => {
    if (!hasPricing || cost === null) return '—';
    return `$${cost.toFixed(4)}`;
  };

  return (
    <Card title="API Details">
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
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('api_key')}>
                      API Key{arrow(sort, 'api_key')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('requests')}>
                      Requests{arrow(sort, 'requests')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('input_tokens')}>
                      Input Tokens{arrow(sort, 'input_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('output_tokens')}>
                      Output Tokens{arrow(sort, 'output_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('total_tokens')}>
                      Total Tokens{arrow(sort, 'total_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('cost')}>
                      Cost{arrow(sort, 'cost')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('failed')}>
                      Failed{arrow(sort, 'failed')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('avg_latency_ms')}>
                      Avg Latency (ms){arrow(sort, 'avg_latency_ms')}
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={row.api_key}>
                    <td className={styles.mono} title={row.api_key}>
                      {row.api_key.length > 20 ? `${row.api_key.slice(0, 8)}…${row.api_key.slice(-8)}` : row.api_key}
                    </td>
                    <td>{row.requests.toLocaleString()}</td>
                    <td>{row.input_tokens.toLocaleString()}</td>
                    <td>{row.output_tokens.toLocaleString()}</td>
                    <td>{row.total_tokens.toLocaleString()}</td>
                    <td>{renderCost(row.cost)}</td>
                    <td>{row.failed.toLocaleString()}</td>
                    <td>{row.avg_latency_ms.toFixed(0)}</td>
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
