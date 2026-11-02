import { useCallback, useMemo } from 'react';
import Loaded from '@/components/ui/Loaded';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import DetailDrawer from '@/components/usage/DetailDrawer';
import FilterBar from '@/components/usage/FilterBar';
import Heatmap from '@/components/usage/Heatmap';
import KpiStrip from '@/components/usage/KpiStrip';
import { Panel, Tabs } from '@/components/usage/Panel';
import RichRows from '@/components/usage/RichRows';
import ShareBar, { type ShareItem } from '@/components/usage/ShareBar';
import { useApi } from '@/hooks/useApi';
import { useUsageViewState } from '@/hooks/useUsageViewState';
import { getBreakdown, getMatrix, getOptions, getSummary } from '@/services/api';
import { useThemeStore } from '@/stores';
import type { BreakdownResponse, Dimension } from '@/types/api';
import { createLookup, type EntityLookup } from '@/utils/usage/entities';
import { resolveWindow } from '@/utils/usage/range';
import { metricValue } from '@/utils/usage/richRows';
import { scopeToEntity, usageQueryParams, type Metric, type SecondaryTab } from '@/utils/usage/viewState';
import s from './UsagePage.module.scss';

const TABS: readonly { value: SecondaryTab; label: string }[] = [
  { value: 'models', label: 'Models' },
  { value: 'credentials', label: 'Credentials' },
];

const TAB_DIMENSION: Record<SecondaryTab, Dimension> = { models: 'model', credentials: 'credential' };

export default function UsagePage() {
  const { state, update, now, refreshToken, refresh } = useUsageViewState();
  const theme = useThemeStore((t) => t.theme);
  const tabDim = TAB_DIMENSION[state.tab];

  const params = useMemo(() => usageQueryParams(state, now), [state, now]);
  const key = JSON.stringify(params);
  const options = useApi(getOptions, [refreshToken]);
  const summary = useApi(() => getSummary(params), [key, refreshToken]);
  const people = useApi(() => getBreakdown('person', params), [key, refreshToken]);
  const secondary = useApi(() => getBreakdown(tabDim, params), [key, tabDim, refreshToken]);
  const matrix = useApi(() => getMatrix(params), [key, refreshToken]);

  const lookup = useMemo(() => createLookup(options.data, theme), [options.data, theme]);
  const win = useMemo(() => resolveWindow(state.range, now), [state.range, now]);
  const select = useCallback((dim: Dimension) => (k: string) => update({ drawer: { dim, key: k } }), [update]);
  const closeDrawer = useCallback(() => update({ drawer: null }), [update]);

  const failures: [string, string | null][] = [
    ['Filter options', options.error],
    ['Summary', summary.error],
    ['People', people.error],
    ['Heatmap', matrix.error],
    [TABS.find((t) => t.value === state.tab)?.label ?? 'Breakdown', secondary.error],
  ];
  const errors = failures.flatMap(([what, e]) => (e === null ? [] : [`${what}: ${e}`]));
  const metricWord = state.metric === 'cost' ? 'spend' : 'tokens';
  const secondaryData = secondary.data?.dim === tabDim ? secondary.data : null;

  return (
    <div className={s.page}>
      <div className={s.titleRow}>
        <h1 className={s.title}>Usage</h1>
      </div>
      <FilterBar
        state={state}
        update={update}
        win={win}
        now={now}
        effectiveBucket={summary.data?.bucket ?? null}
        options={options.data}
        onRefresh={refresh}
      />
      {errors.length > 0 && (
        <div className={s.error}>
          <strong>API error</strong>
          <ul>
            {[...new Set(errors)].map((e) => (
              <li key={e}>{e}</li>
            ))}
          </ul>
        </div>
      )}
      {!summary.data ? (
        errors.length === 0 && (
          <div className={s.stack}>
            <LoadingSpinner />
          </div>
        )
      ) : summary.data.current.requests === 0 ? (
        <div className={s.empty}>No usage in this window.</div>
      ) : (
        <div className={s.stack}>
          <KpiStrip summary={summary.data} accent={lookup.accent} busy={summary.loading} />
          <Panel title={`People by ${metricWord}`} busy={people.loading}>
            <Loaded api={people}>
              {(data) => <BreakdownSection data={data} metric={state.metric} lookup={lookup} onSelect={select('person')} />}
            </Loaded>
          </Panel>
          <Panel title={`Who spends ${state.metric === 'cost' ? 'what' : 'tokens'} on which model`} busy={matrix.loading}>
            <Loaded api={matrix}>
              {(data) => <Heatmap matrix={data} metric={state.metric} lookup={lookup} onSelectRow={select('person')} />}
            </Loaded>
          </Panel>
          <Panel busy={secondary.loading}>
            <Tabs tabs={TABS} value={state.tab} onChange={(tab) => update({ tab })} />
            <Loaded api={{ data: secondaryData, error: secondary.error }}>
              {(data) => <BreakdownSection data={data} metric={state.metric} lookup={lookup} onSelect={select(tabDim)} compact />}
            </Loaded>
          </Panel>
        </div>
      )}
      {state.drawer && (
        <DetailDrawer
          key={`${state.drawer.dim}:${state.drawer.key}`}
          target={state.drawer}
          state={state}
          now={now}
          refreshToken={refreshToken}
          pageTotals={summary.data?.current ?? null}
          lookup={lookup}
          onClose={closeDrawer}
          onFilter={() => state.drawer && update(scopeToEntity(state, state.drawer))}
        />
      )}
    </div>
  );
}

/** Share bar plus the rich rows table for one dimension. */
function BreakdownSection({
  data,
  metric,
  lookup,
  onSelect,
  compact = false,
}: {
  data: BreakdownResponse;
  metric: Metric;
  lookup: EntityLookup;
  onSelect: (key: string) => void;
  compact?: boolean;
}) {
  const items = useMemo<ShareItem[]>(
    () =>
      data.rows.map((r) => ({
        key: r.key,
        label: r.label,
        value: metricValue(r.current, metric),
        slot: lookup.entity(data.dim, r.key).slot,
      })),
    [data, metric, lookup],
  );
  return (
    <>
      <ShareBar items={items} metric={metric} slotColor={lookup.slotColor} onSelect={onSelect} compact={compact} />
      <RichRows data={data} metric={metric} lookup={lookup} onSelect={onSelect} />
    </>
  );
}
