import { useCallback, useMemo } from 'react';
import FailureBanner from '@/components/ui/FailureBanner';
import Loaded from '@/components/ui/Loaded';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import DetailDrawer from '@/components/usage/DetailDrawer';
import FilterBar from '@/components/usage/FilterBar';
import Heatmap from '@/components/usage/Heatmap';
import KpiStrip from '@/components/usage/KpiStrip';
import { Panel, Tabs } from '@/components/usage/Panel';
import RichRows from '@/components/usage/RichRows';
import ShareBar from '@/components/usage/ShareBar';
import { useOptions, useUsage, useUsageWindow } from '@/hooks/useUsageQueries';
import { useUsageViewState } from '@/hooks/useUsageViewState';
import { useThemeStore } from '@/stores/theme';
import type { BreakdownResponse, Dimension } from '@/types/api';
import { createLookup, type EntityLookup } from '@/utils/usage/entities';
import { breakdownKey, summaryKey } from '@/utils/usage/queryKeys';
import { shareItems } from '@/utils/usage/share';
import { METRIC_WORD, scopeToEntity, type Metric, type SecondaryTab } from '@/utils/usage/viewState';
import s from './UsagePage.module.scss';

const TABS: readonly { value: SecondaryTab; label: string }[] = [
  { value: 'models', label: 'Models' },
  { value: 'credentials', label: 'Credentials' },
];

const TAB_DIMENSION: Record<SecondaryTab, Dimension> = { models: 'model', credentials: 'credential' };

export default function UsagePage() {
  const { state, update } = useUsageViewState();
  const { win, refresh } = useUsageWindow(state.range);
  const theme = useThemeStore((t) => t.theme);
  const tabDim = TAB_DIMENSION[state.tab];

  const options = useOptions();
  const summary = useUsage(summaryKey(win, state));
  const people = useUsage(breakdownKey(win, state, 'person'));
  const secondary = useUsage(breakdownKey(win, state, tabDim));

  const lookup = useMemo(() => createLookup(options.data ?? null, theme), [options.data, theme]);
  const select = useCallback((dim: Dimension) => (k: string) => update({ drawer: { dim, key: k } }), [update]);
  const closeDrawer = useCallback(() => update({ drawer: null }), [update]);

  const failures: [string, Error | null][] = [
    ['Filter options', options.error],
    ['Summary', summary.error],
    ['People', people.error],
    [TABS.find((t) => t.value === state.tab)?.label ?? 'Breakdown', secondary.error],
  ];
  const anyFailed = failures.some(([, e]) => e !== null);
  const totals = summary.isError ? undefined : summary.data;
  // The placeholder while a newly picked tab loads is the other tab's breakdown.
  const secondaryData = secondary.data?.dim === tabDim ? secondary.data : undefined;

  return (
    <div className={s.page}>
      <div className={s.titleRow}>
        <h1 className={s.title}>Usage</h1>
      </div>
      <FilterBar
        state={state}
        update={update}
        win={win}
        effectiveBucket={totals?.bucket ?? null}
        options={options.data ?? null}
        onRefresh={refresh}
      />
      <FailureBanner failures={failures} className={s.error} />
      {!totals ? (
        !anyFailed && (
          <div className={s.stack}>
            <LoadingSpinner />
          </div>
        )
      ) : totals.current.requests === 0 ? (
        <div className={s.empty}>No usage in this window.</div>
      ) : (
        <div className={s.stack}>
          <KpiStrip summary={totals} accent={lookup.accent} busy={summary.isFetching} />
          <Panel title={`People by ${METRIC_WORD[state.metric]}`} busy={people.isFetching}>
            <Loaded query={people}>
              {(data) => <BreakdownSection data={data} metric={state.metric} lookup={lookup} onSelect={select('person')} />}
            </Loaded>
          </Panel>
          <Panel title={`Who spends ${state.metric === 'cost' ? 'what' : 'tokens'} on which model`} busy={people.isFetching}>
            <Loaded query={people}>
              {(data) => <Heatmap people={data} metric={state.metric} lookup={lookup} onSelectRow={select('person')} />}
            </Loaded>
          </Panel>
          <Panel busy={secondary.isFetching}>
            <Tabs tabs={TABS} value={state.tab} onChange={(tab) => update({ tab })} />
            <Loaded query={{ data: secondaryData, isError: secondary.isError }}>
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
          win={win}
          pageTotals={totals?.current ?? null}
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
  const items = useMemo(() => shareItems(data, metric, lookup), [data, metric, lookup]);
  return (
    <>
      <ShareBar items={items} metric={metric} slotColor={lookup.slotColor} onSelect={onSelect} compact={compact} />
      <RichRows data={data} metric={metric} lookup={lookup} onSelect={onSelect} />
    </>
  );
}
