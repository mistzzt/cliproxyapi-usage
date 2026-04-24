import { useState, useMemo } from 'react';
import { CHART_TOP_N } from './usage-constants';
import type { Range } from '@/types/api';
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

function defaultPeriod(range: Range): 'hour' | 'day' {
  return range === '7d' || range === 'all' ? 'day' : 'hour';
}

export default function UsagePage() {
  // Persisted state
  const [range, setRange] = useLocalStorage<Range>('usage.range.v1', '24h');
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
  const overview = useApi(() => getOverview({ range, ...filterArgs }), [range, filterArgs]);
  const models = useApi(() => getModels(), []);
  const apiKeys = useApi(() => getApiKeys(), []);
  const pricing = useApi(() => getPricing(), []);

  const timeseriesRequests = useApi(
    () =>
      getTimeseries({
        range,
        bucket: period,
        metric: 'requests',
        ...(isAllModels ? { top_n: CHART_TOP_N } : { models: selectedModels }),
        ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
      }),
    [range, period, isAllModels, selectedModels, apiKeysParam],
  );
  const timeseriesTokens = useApi(
    () =>
      getTimeseries({
        range,
        bucket: period,
        metric: 'tokens',
        ...(isAllModels ? { top_n: CHART_TOP_N } : { models: selectedModels }),
        ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
      }),
    [range, period, isAllModels, selectedModels, apiKeysParam],
  );
  const timeseriesCost = useApi(
    () =>
      getTimeseries({
        range,
        bucket: period,
        metric: 'cost',
        ...(isAllModels ? { top_n: CHART_TOP_N } : { models: selectedModels }),
        ...(apiKeysParam ? { api_keys: apiKeysParam } : {}),
      }),
    [range, period, isAllModels, selectedModels, apiKeysParam],
  );

  const tokenBreakdown = useApi(
    () => getTokenBreakdown({ range, bucket: period, ...filterArgs }),
    [range, period, filterArgs],
  );

  const apiStats = useApi(() => getApiStats({ range, ...filterArgs }), [range, filterArgs]);
  const modelStats = useApi(() => getModelStats({ range, ...filterArgs }), [range, filterArgs]);
  const credentialStats = useApi(
    () => getCredentialStats({ range, ...filterArgs }),
    [range, filterArgs],
  );
  const health = useApi(() => getHealth({ range, ...filterArgs }), [range, filterArgs]);

  // Derived hasPricing
  const hasPricing = pricing.data !== null && Object.keys(pricing.data.pricing).length > 0;

  // Refresh all
  function handleRefresh() {
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

  function handleRangeChange(next: Range) {
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
