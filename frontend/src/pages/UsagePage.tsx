import { useState, useMemo } from 'react';
import type { Bucket, RangeSpec } from '@/types/api';
import { resolveRange, formatCalendarLabel } from '@/utils/rangeResolver';
import {
  getOverview,
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
import UsageExplorer from '@/components/usage/explorer/UsageExplorer';
import {
  DEFAULT_EXPLORER_STATE,
  EXPLORER_STATE_KEY,
  normalizeExplorerState,
  parseExplorerState,
  type ExplorerState,
} from '@/components/usage/explorer/explorerState';
import ApiDetailsCard from '@/components/usage/ApiDetailsCard';
import ModelStatsCard from '@/components/usage/ModelStatsCard';
import CredentialStatsCard from '@/components/usage/CredentialStatsCard';
import ServiceHealthCard from '@/components/usage/ServiceHealthCard';
import styles from './UsagePage.module.scss';

/** Range-appropriate default bucket, used when explorer granularity is 'auto'. */
function autoBucketFor(spec: RangeSpec): Bucket {
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

/** Short human label for the active range, for the explorer's text summary. */
function rangeLabelFor(spec: RangeSpec): string {
  switch (spec.kind) {
    case 'rolling':
      return spec.preset === '7h' ? 'the last 7 hours' : 'the last 24 hours';
    case 'all':
      return 'all time';
    case 'calendar':
      return formatCalendarLabel(spec.unit, spec.anchor);
    case 'custom':
      return `${spec.startDate} to ${spec.endDate}`;
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
  const [explorerRaw, setExplorerRaw] = useLocalStorage<ExplorerState>(
    EXPLORER_STATE_KEY,
    DEFAULT_EXPLORER_STATE,
  );
  const explorerState = useMemo(() => parseExplorerState(explorerRaw), [explorerRaw]);
  const setExplorerState = (next: ExplorerState) =>
    setExplorerRaw(normalizeExplorerState(next));

  // Session state
  const [mobileDrawerOpen, setMobileDrawerOpen] = useState(false);
  // `now` is recomputed once per load/refresh, then held stable so the resolved
  // window doesn't drift on unrelated re-renders.
  const [now, setNow] = useState<Date>(() => new Date());
  // Bumped on Refresh so the explorer refetches even for fixed (calendar/custom)
  // ranges whose resolved window does not change with `now`.
  const [refreshToken, setRefreshToken] = useState(0);

  // Resolve the selection into concrete instants + tz offset for the API.
  const resolved = useMemo(() => resolveRange(range, now), [range, now]);
  const autoBucket = useMemo(() => autoBucketFor(range), [range]);
  const rangeLabel = useMemo(() => rangeLabelFor(range), [range]);

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

  // --- Data hooks (explorer fetches its own active dataset) ---
  const overview = useApi(
    () => getOverview({ range: resolved, ...filterArgs }),
    [resolved, filterArgs],
  );
  const models = useApi(() => getModels(), []);
  const apiKeys = useApi(() => getApiKeys(), []);
  const pricing = useApi(() => getPricing(), []);

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

  // Derived hasPricing
  const hasPricing = pricing.data !== null && Object.keys(pricing.data.pricing).length > 0;

  // Refresh all (preserves filters and explorer state).
  function handleRefresh() {
    setNow(new Date());
    setRefreshToken((t) => t + 1);
    overview.reload();
    models.reload();
    apiKeys.reload();
    pricing.reload();
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
  }

  // Page-level error banner excludes the explorer, which surfaces its own
  // fetch errors inside its card without hiding the summary/tables.
  const pageHooks = [overview, models, apiKeys, pricing, apiStats, modelStats, credentialStats, health];
  const errors = pageHooks.map((h) => h.error).filter((e): e is string => e !== null);

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

            <UsageExplorer
              state={explorerState}
              onStateChange={setExplorerState}
              range={resolved}
              autoBucket={autoBucket}
              isAllModels={isAllModels}
              selectedModels={selectedModels}
              {...(apiKeysParam ? { apiKeys: apiKeysParam } : {})}
              hasPricing={hasPricing}
              isMobile={isMobile}
              rangeLabel={rangeLabel}
              refreshToken={refreshToken}
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
