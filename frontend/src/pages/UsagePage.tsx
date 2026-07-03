import { useState, useMemo, useEffect } from 'react';
import { CHART_TOP_N } from './usage-constants';
import type { RangeSpec } from '@/types/api';
import { resolveRange } from '@/utils/rangeResolver';
import {
  getOverview,
  getTimeseries,
  getTokenBreakdown,
  getApiStats,
  getModelStats,
  getCredentialStats,
  getHealth,
  getModels,
  getApiKeys,
  getPricing,
} from '@/services/api';
import { useApi } from '@/hooks/useApi';
import { useLocalStorage } from '@/hooks/useLocalStorage';
import { useMediaQuery } from '@/hooks/useMediaQuery';
import AppHeader from '@/components/ui/AppHeader';
import Button from '@/components/ui/Button';
import FilterSidebar from '@/components/usage/FilterSidebar';
import StatCards from '@/components/usage/StatCards';
import UsageChart from '@/components/usage/UsageChart';
import TokenBreakdownChart from '@/components/usage/TokenBreakdownChart';
import CostTrendChart from '@/components/usage/CostTrendChart';
import ApiDetailsCard from '@/components/usage/ApiDetailsCard';
import ModelStatsCard from '@/components/usage/ModelStatsCard';
import CredentialStatsCard from '@/components/usage/CredentialStatsCard';
import ServiceHealthCard from '@/components/usage/ServiceHealthCard';
import styles from './UsagePage.module.scss';

function defaultPeriod(spec: RangeSpec): 'hour' | 'day' {
  switch (spec.kind) {
    case 'all':
      return 'day';
    case 'calendar':
      return spec.unit === 'day' ? 'hour' : 'day';
    case 'custom':
      return spec.startDate === spec.endDate ? 'hour' : 'day';
    case 'rolling':
      return 'hour';
  }
}

export default function UsagePage() {
  // Persisted state
  const [range, setRange] = useLocalStorage<RangeSpec>('usage.range.v2', {
    kind: 'rolling',
    preset: '24h',
  });
  const [selectedModels, setSelectedModels] = useLocalStorage<string[]>(
    'usage.models-filter.v1',
    ['all'],
  );
  const [selectedApiKeys, setSelectedApiKeys] = useLocalStorage<string[]>(
    'usage.api-keys-filter.v1',
    ['all'],
  );
  const [sidebarCollapsed, setSidebarCollapsed] = useLocalStorage<boolean>(
    'usage.sidebar.collapsed.v1',
    false,
  );

  // Session state
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  const [period, setPeriod] = useState<'hour' | 'day'>(() => defaultPeriod(range));
  // `now` is recomputed once per load/refresh, then held stable so the resolved
  // window doesn't drift on unrelated re-renders.
  const [now, setNow] = useState<Date>(() => new Date());

  // Resolve the selection into concrete instants + tz offset for the API.
  const resolved = useMemo(() => resolveRange(range, now), [range, now]);

  // Media queries
  const isMobile = useMediaQuery('(max-width: 768px)');

  // Derived
  const isAllModels = selectedModels.includes('all') || selectedModels.length === 0;
  const modelsParam = useMemo(
    () => (isAllModels ? undefined : selectedModels),
    [isAllModels, selectedModels],
  );

  const isAllApiKeys = selectedApiKeys.includes('all') || selectedApiKeys.length === 0;
  const apiKeysParam = useMemo(
    () => (isAllApiKeys ? undefined : selectedApiKeys),
    [isAllApiKeys, selectedApiKeys],
  );

  const filterArgs = useMemo(
    () => ({
      ...(modelsParam ? { models: modelsParam } : {}),
      ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
    }),
    [modelsParam, apiKeysParam],
  );

  // --- Data hooks ---
  const overview = useApi(
    () => getOverview({ range: resolved, ...filterArgs }),
    [resolved, filterArgs],
  );
  const models = useApi(() => getModels(), []);
  const apiKeys = useApi(() => getApiKeys(), []);
  const pricing = useApi(() => getPricing(), []);

  const timeseriesRequests = useApi(
    () =>
      getTimeseries({
        range: resolved,
        bucket: period,
        metric: 'requests',
        ...(isAllModels ? { top_n: CHART_TOP_N } : { models: selectedModels }),
        ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
      }),
    [resolved, period, isAllModels, selectedModels, apiKeysParam],
  );
  const timeseriesTokens = useApi(
    () =>
      getTimeseries({
        range: resolved,
        bucket: period,
        metric: 'tokens',
        ...(isAllModels ? { top_n: CHART_TOP_N } : { models: selectedModels }),
        ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
      }),
    [resolved, period, isAllModels, selectedModels, apiKeysParam],
  );
  const timeseriesCost = useApi(
    () =>
      getTimeseries({
        range: resolved,
        bucket: period,
        metric: 'cost',
        ...(isAllModels ? { top_n: CHART_TOP_N } : { models: selectedModels }),
        ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
      }),
    [resolved, period, isAllModels, selectedModels, apiKeysParam],
  );

  const tokenBreakdown = useApi(
    () => getTokenBreakdown({ range: resolved, bucket: period, ...filterArgs }),
    [resolved, period, filterArgs],
  );

  const apiStats = useApi(
    () => getApiStats({ range: resolved, ...filterArgs }),
    [resolved, filterArgs],
  );
  const modelStats = useApi(
    () => getModelStats({ range: resolved, ...filterArgs }),
    [resolved, filterArgs],
  );
  const credentialStats = useApi(
    () => getCredentialStats({ range: resolved, ...filterArgs }),
    [resolved, filterArgs],
  );
  const health = useApi(() => getHealth({ range: resolved, ...filterArgs }), [resolved, filterArgs]);

  // Honor the server's effective bucket: wide windows auto-coarsen hour -> day,
  // so reflect the actual bucket in the period toggle. Key off the response
  // object identity (which changes on every fetch) rather than the bucket value,
  // so a coarsen hour -> day is picked up even when the previous response was
  // already day-bucketed. useApi keeps the prior data during reload, so this only
  // fires once fresh data arrives, never on the in-flight toggle itself.
  const requestsData = timeseriesRequests.data;
  useEffect(() => {
    const effectiveBucket = requestsData?.bucket;
    if (effectiveBucket && effectiveBucket !== period) {
      setPeriod(effectiveBucket);
    }
    // Only react to new server responses; including `period` would revert
    // the user's toggle before the refetch resolves.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestsData]);

  // Derived hasPricing
  const hasPricing = pricing.data !== null && Object.keys(pricing.data.pricing).length > 0;

  // Refresh all
  function handleRefresh() {
    setNow(new Date());
    overview.reload();
    models.reload();
    apiKeys.reload();
    pricing.reload();
    timeseriesRequests.reload();
    timeseriesTokens.reload();
    timeseriesCost.reload();
    tokenBreakdown.reload();
    apiStats.reload();
    modelStats.reload();
    credentialStats.reload();
    health.reload();
  }

  function handleRangeChange(next: RangeSpec) {
    // Refresh the clock too: rolling/`all` windows end at `now`, so a stale
    // `now` (from page load or the last refresh) would ask for an old `end` and
    // miss recent records until the user hits Refresh.
    setNow(new Date());
    setRange(next);
    setPeriod(defaultPeriod(next));
  }

  const allHooks = [
    overview,
    models,
    apiKeys,
    pricing,
    timeseriesRequests,
    timeseriesTokens,
    timeseriesCost,
    tokenBreakdown,
    apiStats,
    modelStats,
    credentialStats,
    health,
  ];
  const errors = allHooks.map((h) => h.error).filter((e): e is string => e !== null);

  return (
    <div className={styles.page}>
      <AppHeader />
      <div className={styles.header}>
        <h1 className={styles.title}>Usage statistics</h1>
        {isMobile && <Button onClick={() => setMobileDrawerOpen(true)}>Filter</Button>}
      </div>

      <div className={styles.layout}>
        <FilterSidebar
          range={range}
          onRangeChange={handleRangeChange}
          models={models.data?.models ?? []}
          selectedModels={selectedModels}
          onModelsChange={setSelectedModels}
          apiKeys={apiKeys.data?.api_keys ?? []}
          selectedApiKeys={selectedApiKeys}
          onApiKeysChange={setSelectedApiKeys}
          onRefresh={handleRefresh}
          collapsed={sidebarCollapsed}
          onCollapsedChange={setSidebarCollapsed}
          mobileOpen={mobileDrawerOpen}
          onMobileClose={() => setMobileDrawerOpen(false)}
        />

        <div className={styles.content}>
          {errors.length > 0 && (
            <div className={styles.errorBanner}>
              <strong>API error</strong>
              <ul>
                {errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            </div>
          )}

          <div className={styles.stack}>
            <StatCards overview={overview.data} loading={overview.loading} />

            <UsageChart
              title="Requests"
              data={timeseriesRequests.data}
              loading={timeseriesRequests.loading}
              period={period}
              onPeriodChange={setPeriod}
              isMobile={isMobile}
            />

            <UsageChart
              title="Tokens"
              data={timeseriesTokens.data}
              loading={timeseriesTokens.loading}
              period={period}
              onPeriodChange={setPeriod}
              isMobile={isMobile}
            />

            <TokenBreakdownChart
              data={tokenBreakdown.data}
              loading={tokenBreakdown.loading}
              isMobile={isMobile}
            />

            <CostTrendChart
              data={timeseriesCost.data}
              loading={timeseriesCost.loading}
              hasPricing={hasPricing}
            />

            <ApiDetailsCard
              rows={apiStats.data ?? []}
              loading={apiStats.loading}
              hasPricing={hasPricing}
            />

            <ModelStatsCard
              rows={modelStats.data ?? []}
              loading={modelStats.loading}
              hasPricing={hasPricing}
            />

            <CredentialStatsCard
              rows={credentialStats.data ?? []}
              loading={credentialStats.loading}
              hasPricing={hasPricing}
            />

            <ServiceHealthCard data={health.data} loading={health.loading} />
          </div>
        </div>
      </div>
    </div>
  );
}
