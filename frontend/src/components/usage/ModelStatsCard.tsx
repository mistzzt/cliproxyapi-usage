import { useMemo, useState } from 'react';
import Card from '@/components/ui/Card';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import type { ModelStat } from '@/types/api';
import { sortRows } from './sort';
import type { SortState } from './sort';
import { CostCell } from './CostCell';
import styles from './ModelStatsCard.module.scss';

export interface ModelStatsCardProps {
  rows: ModelStat[];
  loading: boolean;
  hasPricing?: boolean;
}

type ModelSortKey =
  | 'model'
  | 'requests'
  | 'input_tokens'
  | 'output_tokens'
  | 'cached_tokens'
  | 'reasoning_tokens'
  | 'total_tokens'
  | 'cost'
  | 'avg_latency_ms'
  | 'failed';

function arrow(state: SortState<ModelSortKey>, key: ModelSortKey): string {
  if (state.key !== key) return '';
  return state.order === 'asc' ? ' ▲' : ' ▼';
}

export default function ModelStatsCard({ rows, loading }: ModelStatsCardProps) {
  const [sort, setSort] = useState<SortState<ModelSortKey>>({ key: 'requests', order: 'desc' });

  const handleSort = (key: ModelSortKey) => {
    setSort((prev) => {
      if (prev.key === key) {
        return { key, order: prev.order === 'asc' ? 'desc' : 'asc' };
      }
      return { key, order: 'desc' };
    });
  };

  const sorted = useMemo(() => sortRows(rows, sort), [rows, sort]);

  return (
    <Card title="Model Stats">
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
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('model')}>
                      Model{arrow(sort, 'model')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('requests')}>
                      Requests{arrow(sort, 'requests')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('input_tokens')}>
                      Input{arrow(sort, 'input_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('output_tokens')}>
                      Output{arrow(sort, 'output_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('cached_tokens')}>
                      Cached{arrow(sort, 'cached_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('reasoning_tokens')}>
                      Reasoning{arrow(sort, 'reasoning_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('total_tokens')}>
                      Total{arrow(sort, 'total_tokens')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('cost')}>
                      Cost{arrow(sort, 'cost')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('avg_latency_ms')}>
                      Avg Latency{arrow(sort, 'avg_latency_ms')}
                    </button>
                  </th>
                  <th>
                    <button type="button" className={styles.sortBtn} onClick={() => handleSort('failed')}>
                      Failed{arrow(sort, 'failed')}
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((row) => (
                  <tr key={row.model}>
                    <td className={styles.modelCell}>{row.model}</td>
                    <td>{row.requests.toLocaleString()}</td>
                    <td>{row.input_tokens.toLocaleString()}</td>
                    <td>{row.output_tokens.toLocaleString()}</td>
                    <td>{row.cached_tokens.toLocaleString()}</td>
                    <td>{row.reasoning_tokens.toLocaleString()}</td>
                    <td>{row.total_tokens.toLocaleString()}</td>
                    <td><CostCell cost={row.cost} status={row.cost_status} /></td>
                    <td>{row.avg_latency_ms.toFixed(0)}</td>
                    <td>{row.failed.toLocaleString()}</td>
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
