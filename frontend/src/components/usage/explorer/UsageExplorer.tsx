import '@/components/charts/chart-setup';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ChartData, ChartOptions } from 'chart.js';
import { Chart as ChartJS } from 'chart.js';
import { Bar, Line } from 'react-chartjs-2';
import type {
  Bucket,
  CostStatus,
  TimeseriesResponse,
  TokenBreakdownResponse,
} from '@/types/api';
import type { ResolvedRange } from '@/utils/rangeResolver';
import { getTimeseries, getTokenBreakdown } from '@/services/api';
import { useApi } from '@/hooks/useApi';
import { useThemeStore } from '@/stores';
import Card from '@/components/ui/Card';
import Button from '@/components/ui/Button';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import {
  ALL_MODEL_COLOR,
  OTHER_MODEL_COLOR,
  modelColor,
} from '@/components/charts/palette';
import { readThemeColors, buildTooltipConfig } from '@/components/charts/theme-colors';
import type { ExplorerMetric, ExplorerState } from './explorerState';
import { availableBreakdowns, availableDisplays } from './explorerState';
import {
  requestKey,
  selectExplorerRequest,
  zoomClearKey,
  type ExplorerRequest,
} from './requestSelection';
import { deriveOtherSeries, rollupCostStatus, sumSeries, zeroThreshold } from './otherSeries';
import styles from './UsageExplorer.module.scss';

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

type ExplorerData =
  // `key` tags each response with the fetch key it was requested under, so a
  // render mid-refetch can tell whether the retained data still matches the
  // active descriptor (see the `model` memo) and avoid reshaping stale data
  // under a newly selected metric.
  | { kind: 'timeseries'; key: string; data: TimeseriesResponse }
  | { kind: 'token_breakdown'; key: string; data: TokenBreakdownResponse };

function fetchExplorerData(
  req: ExplorerRequest,
  range: ResolvedRange,
  key: string,
): Promise<ExplorerData> {
  const filter: { models?: string[]; api_keys?: string[] } = {};
  if (req.models) filter.models = req.models;
  if (req.apiKeys) filter.api_keys = req.apiKeys;

  if (req.endpoint === 'token-breakdown') {
    return getTokenBreakdown({ range, bucket: req.bucket, ...filter }).then((data) => ({
      kind: 'token_breakdown',
      key,
      data,
    }));
  }

  const params: Parameters<typeof getTimeseries>[0] = {
    range,
    bucket: req.bucket,
    metric: req.metric ?? 'requests',
    ...filter,
  };
  if (req.topN !== undefined) params.top_n = req.topN;
  return getTimeseries(params).then((data) => ({ kind: 'timeseries', key, data }));
}

// ---------------------------------------------------------------------------
// Series shaping
// ---------------------------------------------------------------------------

const STACK_META = [
  { key: 'input', label: 'Input', color: '#8b8680' },
  { key: 'output', label: 'Output', color: '#22c55e' },
  { key: 'cached', label: 'Cached', color: '#f59e0b' },
  { key: 'reasoning', label: 'Reasoning', color: '#8b5cf6' },
] as const;

interface PlotSeries {
  key: string;
  label: string;
  color: string;
  values: number[];
  /** Cost warning treatment: dashed line / bordered bar + footnote entry. */
  warning: boolean;
}

interface ChartModel {
  empty: boolean;
  pricingUnavailable: boolean;
  labels: string[];
  series: PlotSeries[];
  chartType: 'line' | 'bar';
  stacked: boolean;
  /** input+output per bucket, for token-type tooltips. */
  tokenTotals: number[] | null;
  /** Names of series with degraded pricing, for the footnote. */
  degraded: string[];
  /** Grand total across the range, for the accessible summary. */
  total: number;
}

const ALL_KEY = '__all__';

function shapeTokenBreakdown(data: TokenBreakdownResponse): ChartModel {
  const series: PlotSeries[] = STACK_META.map((m) => ({
    key: m.key,
    label: m.label,
    color: m.color,
    values: data[m.key],
    warning: false,
  }));
  // Dense bucketing zero-fills every interval in the range, so a non-empty
  // `buckets` array does not imply activity. Treat the breakdown as empty when
  // all four token components are zero throughout the range.
  const componentSum = series.reduce(
    (acc, s) => acc + s.values.reduce((a, b) => a + b, 0),
    0,
  );
  const empty = data.buckets.length === 0 || componentSum < zeroThreshold('tokens');
  const tokenTotals = data.buckets.map((_, i) => (data.input[i] ?? 0) + (data.output[i] ?? 0));
  const total = tokenTotals.reduce((a, b) => a + b, 0);
  return {
    empty,
    pricingUnavailable: false,
    labels: data.buckets,
    series,
    chartType: 'bar',
    stacked: true,
    tokenTotals,
    degraded: [],
    total,
  };
}

function shapeTimeseries(
  data: TimeseriesResponse,
  state: ExplorerState,
  ctx: { isAllModels: boolean; selectedModels: string[]; hasPricing: boolean },
): ChartModel {
  const { metric, breakdown, display } = state;
  const isCost = metric === 'cost';
  const labels = data.buckets;
  const statusOf = (key: string): CostStatus => data.series_status?.[key] ?? 'live';
  const warnOf = (key: string) => isCost && statusOf(key) !== 'live';

  const chartType: 'line' | 'bar' =
    breakdown === 'model' && display === 'stacked' ? 'bar' : 'line';
  const stacked = chartType === 'bar';

  const base: Omit<ChartModel, 'series' | 'pricingUnavailable' | 'total' | 'empty'> = {
    labels,
    chartType,
    stacked,
    tokenTotals: null,
    degraded: [],
  };

  // Cost with no pricing at all: nothing meaningful to draw.
  if (isCost && !ctx.hasPricing) {
    return { ...base, empty: labels.length === 0, series: [], pricingUnavailable: true, total: 0 };
  }

  let series: PlotSeries[];

  if (breakdown === 'total') {
    let values: number[];
    let warning: boolean;
    if (ctx.isAllModels) {
      values = data.series[ALL_KEY] ?? [];
      warning = warnOf(ALL_KEY);
    } else {
      // Explicit models: the backend returns one series per model and no
      // aggregate, so sum them into a single total and roll up the status.
      const keys = Object.keys(data.series);
      values = sumSeries(keys.map((k) => data.series[k] ?? []), labels.length);
      warning = isCost && rollupCostStatus(keys.map(statusOf)) !== 'live';
    }
    series = [{ key: ALL_KEY, label: 'Total', color: ALL_MODEL_COLOR, values, warning }];
  } else {
    // Model breakdown.
    if (ctx.isAllModels) {
      const namedKeys = Object.keys(data.series).filter((k) => k !== ALL_KEY);
      // Cost decomposition with no priced models -> pricing unavailable state.
      if (isCost && namedKeys.length === 0) {
        return {
          ...base,
          empty: labels.length === 0,
          series: [],
          pricingUnavailable: true,
          total: 0,
        };
      }
      const named: PlotSeries[] = namedKeys.map((k) => ({
        key: k,
        label: k,
        color: modelColor(k),
        values: data.series[k] ?? [],
        warning: warnOf(k),
      }));
      const allValues = data.series[ALL_KEY] ?? [];
      const other = deriveOtherSeries(
        allValues,
        named.map((s) => s.values),
        metric,
      );
      series = named;
      if (other) {
        series = [
          ...named,
          {
            key: '__other__',
            label: 'Other',
            color: OTHER_MODEL_COLOR,
            values: other,
            // Conservative: the aggregate status covers unpriced models.
            warning: warnOf(ALL_KEY),
          },
        ];
      }
    } else {
      series = ctx.selectedModels.map((k) => ({
        key: k,
        label: k,
        color: modelColor(k),
        values: data.series[k] ?? new Array<number>(labels.length).fill(0),
        warning: warnOf(k),
      }));
    }
  }

  const degraded = series.filter((s) => s.warning).map((s) => s.label);
  const total = series.reduce((acc, s) => acc + s.values.reduce((a, b) => a + b, 0), 0);
  // Dense bucketing zero-fills the whole range, so a filter (or idle window)
  // that matches no records still returns non-empty `labels`. Detect real
  // activity from the summed series total, using the metric-appropriate
  // near-zero threshold (cost below one tenth of a cent counts as empty).
  const empty = labels.length === 0 || total < zeroThreshold(metric);
  return { ...base, empty, series, pricingUnavailable: false, degraded, total };
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

function formatValue(metric: ExplorerMetric, v: number): string {
  if (metric === 'cost') return `$${v.toFixed(4)}`;
  return Math.round(v).toLocaleString();
}

function metricLabel(metric: ExplorerMetric): string {
  return metric === 'requests' ? 'Requests' : metric === 'tokens' ? 'Tokens' : 'Cost';
}

// ---------------------------------------------------------------------------
// Small control primitives
// ---------------------------------------------------------------------------

interface Option<T extends string> {
  value: T;
  label: string;
}

function Segmented<T extends string>({
  legend,
  options,
  value,
  onChange,
}: {
  legend: string;
  options: Option<T>[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className={styles.segment} role="group" aria-label={legend}>
      <span className={styles.segmentLabel}>{legend}</span>
      <div className={styles.segmentButtons}>
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            className={`${styles.segmentButton} ${
              value === opt.value ? styles.segmentButtonActive : ''
            }`}
            aria-pressed={value === opt.value}
            onClick={() => onChange(opt.value)}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  );
}

const BREAKDOWN_LABELS: Record<string, string> = {
  total: 'Total',
  model: 'Model',
  token_type: 'Token type',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface UsageExplorerProps {
  state: ExplorerState;
  onStateChange: (next: ExplorerState) => void;
  range: ResolvedRange;
  autoBucket: Bucket;
  isAllModels: boolean;
  selectedModels: string[];
  apiKeys?: string[];
  hasPricing: boolean;
  isMobile: boolean;
  /** Human label for the active range, used in the accessible summary. */
  rangeLabel: string;
  /** Bumped by the page's Refresh action to force a refetch. */
  refreshToken: number;
}

export default function UsageExplorer({
  state,
  onStateChange,
  range,
  autoBucket,
  isAllModels,
  selectedModels,
  apiKeys,
  hasPricing,
  isMobile,
  rangeLabel,
  refreshToken,
}: UsageExplorerProps) {
  const theme = useThemeStore((s) => s.theme);

  const request = useMemo(
    () =>
      selectExplorerRequest(state, {
        isAllModels,
        selectedModels,
        ...(apiKeys ? { apiKeys } : {}),
        autoBucket,
      }),
    [state, isAllModels, selectedModels, apiKeys, autoBucket],
  );

  const rangeSig = `${range.start ?? ''}|${range.end}|${range.tzOffsetMinutes}|${refreshToken}`;

  // Shape key: identifies how a response must be shaped (endpoint, metric,
  // decomposition). Independent of range/refresh, so a range change or manual
  // refresh keeps the prior chart visible (correct units) during reload, while
  // a metric/breakdown-source change marks the retained data stale.
  const shapeKey = requestKey(request);

  // Fetch key: what actually drives a refetch. Adds range + refresh to the
  // shape key. Two states that map to the same request (e.g. total vs model
  // with an explicit model selection, or line vs stacked) share a fetch.
  const fetchKey = `${shapeKey}|${rangeSig}`;

  // View key: governs the drawn series set, legend selection, and zoom window.
  // It adds the breakdown so a breakdown change clears zoom/legend even when
  // the request (and thus fetchKey) is identical.
  const viewKey = `${zoomClearKey(request, state.breakdown)}|${rangeSig}`;

  const { data, loading, error } = useApi<ExplorerData>(
    () => fetchExplorerData(request, range, shapeKey),
    [fetchKey],
  );

  // While a refetch is in flight, useApi retains the prior response. Only treat
  // it as usable when its shape matches the active descriptor, so we never
  // reshape an old metric's data under the new metric's axis/tooltip/summary.
  const freshData = data && data.key === shapeKey ? data : null;
  const stale = data !== null && data.key !== shapeKey;
  // Stale data means a refetch under a new descriptor is (about to be) in
  // flight, so present the loading state instead of the mismatched old data.
  // Suppress once a request has terminally errored (which retains the stale
  // data) so the error surfaces instead of a perpetual spinner.
  const showLoading = loading || (stale && !error);

  // Server may coarsen hour -> day for wide windows; reflect the effective
  // bucket (derived from the fresh response, no effect/setState needed).
  const effectiveBucket: Bucket | null = freshData?.data.bucket ?? null;
  const coarsened = request.bucket === 'hour' && effectiveBucket === 'day';

  // Legend visibility + zoom state, both keyed by the view key so a
  // series-set or breakdown change resets them without a setState-in-effect.
  const [hiddenState, setHiddenState] = useState<{ key: string; hidden: string[] }>({
    key: viewKey,
    hidden: [],
  });
  const hidden = useMemo(
    () => (hiddenState.key === viewKey ? hiddenState.hidden : []),
    [hiddenState, viewKey],
  );
  const toggleSeries = useCallback(
    (seriesKey: string) => {
      setHiddenState((prev) => {
        const current = prev.key === viewKey ? prev.hidden : [];
        const next = current.includes(seriesKey)
          ? current.filter((k) => k !== seriesKey)
          : [...current, seriesKey];
        return { key: viewKey, hidden: next };
      });
    },
    [viewKey],
  );

  const [zoomState, setZoomState] = useState<{ key: string; zoomed: boolean }>({
    key: viewKey,
    zoomed: false,
  });
  const zoomed = zoomState.key === viewKey ? zoomState.zoomed : false;

  const chartRef = useRef<ChartJS | null>(null);
  const storeChart = useCallback(
    (instance: ChartJS<'line'> | ChartJS<'bar'> | null | undefined) => {
      chartRef.current = (instance as ChartJS | null | undefined) ?? null;
    },
    [],
  );

  // Keep the current view key in a ref so the (memoized) onZoomComplete
  // callback always records zoom against the active view, even when the chart
  // options are not rebuilt (e.g. a breakdown toggle that keeps the same axis
  // formatting).
  const viewKeyRef = useRef(viewKey);
  useEffect(() => {
    viewKeyRef.current = viewKey;
  });

  const resetZoom = useCallback(() => {
    // resetZoom fires onZoomComplete, which reconciles zoomState from
    // chart.isZoomedOrPanned(); no explicit setZoomState needed here.
    chartRef.current?.resetZoom();
  }, []);

  // Any change to the view (metric, breakdown, granularity, or global filters)
  // clears an active zoom on the live chart instance. react-chartjs-2 keeps
  // the instance across data-only updates, and a breakdown toggle may not
  // rebuild the options at all, so clearing here (not via an options rebuild)
  // guarantees the design's "any breakdown change clears zoom" rule.
  useEffect(() => {
    chartRef.current?.resetZoom();
  }, [viewKey]);

  const model = useMemo<ChartModel | null>(() => {
    if (!freshData) return null;
    if (freshData.kind === 'token_breakdown') return shapeTokenBreakdown(freshData.data);
    return shapeTimeseries(freshData.data, state, { isAllModels, selectedModels, hasPricing });
  }, [freshData, state, isAllModels, selectedModels, hasPricing]);

  // Chart options are deliberately independent of legend `hidden` state. A
  // legend toggle must not rebuild `options` (and thus a fresh `scales`
  // object): react-chartjs-2 applies new options via Object.assign, which
  // replaces `scales` and drops the zoom plugin's per-scale min/max, silently
  // unzooming the chart. Keeping options stable across toggles preserves zoom;
  // visibility is instead carried on each dataset's `hidden` flag below.
  // `themeKey` (carried here since `theme` drives the CSS-variable palette
  // re-read) remounts the canvas so Chart.js picks up new colors cleanly.
  const chartOptions = useMemo<{
    options: ChartOptions<'line' | 'bar'>;
    themeKey: string;
  } | null>(() => {
    if (!model || model.empty || model.pricingUnavailable) return null;
    const colors = readThemeColors();
    const { grid, tick } = colors;
    const metric = state.metric;
    const tokenTotals = model.tokenTotals;

    const options: ChartOptions<'line' | 'bar'> = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'index', intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          ...buildTooltipConfig(colors),
          callbacks: {
            label: (item) =>
              `${item.dataset.label ?? ''}: ${formatValue(metric, item.parsed.y ?? 0)}`,
            ...(tokenTotals
              ? {
                  footer: (items) => {
                    const idx = items[0]?.dataIndex ?? 0;
                    const t = tokenTotals[idx] ?? 0;
                    return `Total tokens: ${Math.round(t).toLocaleString()}`;
                  },
                }
              : {}),
          },
        },
        zoom: {
          zoom: {
            wheel: { enabled: true },
            drag: { enabled: true },
            mode: 'x',
            // Derive the zoomed flag from the plugin's own state rather than
            // hardcoding `true`: resetZoom() also fires this callback, and a
            // programmatic reset must report "not zoomed", not re-enable the
            // Reset button. viewKeyRef keeps the key accurate even when these
            // options are not rebuilt for the active view.
            onZoomComplete: ({ chart }) =>
              setZoomState({ key: viewKeyRef.current, zoomed: chart.isZoomedOrPanned() }),
          },
          pan: { enabled: false },
        },
      },
      scales: {
        x: {
          stacked: model.stacked,
          ticks: {
            color: tick,
            maxTicksLimit: Math.min(isMobile ? 5 : 10, model.labels.length),
            maxRotation: isMobile ? 45 : 0,
            autoSkip: true,
          },
          grid: { color: grid },
        },
        y: {
          stacked: model.stacked,
          beginAtZero: true,
          ticks: {
            color: tick,
            ...(metric === 'cost'
              ? { callback: (value: string | number) => `$${Number(value).toFixed(4)}` }
              : {}),
          },
          grid: { color: grid },
        },
      },
    };

    return { options, themeKey: theme };
    // setZoomState (a useState setter) and viewKeyRef (a ref) are stable and
    // intentionally excluded so `options` stays referentially stable across
    // legend toggles, letting an active zoom survive.
  }, [model, theme, state.metric, isMobile]);

  const chartData = useMemo(() => {
    if (!model || model.empty || model.pricingUnavailable) return null;
    const stackId = model.chartType === 'bar' ? 'explorer' : undefined;
    const datasets = model.series.map((s) => {
      const isHidden = hidden.includes(s.key);
      const drawColor = s.warning ? 'rgba(214, 69, 61, 0.9)' : s.color;
      if (model.chartType === 'bar') {
        return {
          label: s.label,
          data: s.values,
          backgroundColor: drawColor,
          borderColor: drawColor,
          borderWidth: 1,
          stack: stackId,
          hidden: isHidden,
        };
      }
      return {
        label: s.label,
        data: s.values,
        borderColor: drawColor,
        backgroundColor: drawColor,
        pointBackgroundColor: drawColor,
        pointBorderColor: drawColor,
        borderWidth: s.key === ALL_KEY ? 2.5 : 1.5,
        borderDash: s.warning ? [6, 4] : undefined,
        pointRadius: model.labels.length > 60 ? 0 : 2,
        fill: false,
        tension: 0.3,
        hidden: isHidden,
      };
    });

    return {
      chartType: model.chartType,
      data: { labels: model.labels, datasets } as ChartData<'line' | 'bar'>,
    };
  }, [model, hidden]);

  const chartConfig =
    chartOptions && chartData
      ? {
          chartType: chartData.chartType,
          data: chartData.data,
          options: chartOptions.options,
          themeKey: chartOptions.themeKey,
        }
      : null;

  // --- Controls ---
  const breakdownOptions: Option<ExplorerState['breakdown']>[] = availableBreakdowns(
    state.metric,
  ).map((b) => ({ value: b, label: BREAKDOWN_LABELS[b]! }));
  const displayOptions = availableDisplays(state.breakdown);
  const showDisplay = displayOptions.length > 1;

  // When the server coarsens an explicit Hour request to Day, show Day as the
  // effective granularity in the control while leaving the stored preference
  // on Hour, so narrowing the range later restores the Hour selection.
  const displayedGranularity: ExplorerState['granularity'] =
    coarsened && state.granularity === 'hour' ? 'day' : state.granularity;

  const set = (patch: Partial<ExplorerState>) => onStateChange({ ...state, ...patch });

  // A simple group of toggle buttons with aria-pressed rather than ARIA
  // tablist/tab: the tabs pattern would require roving tabindex, Arrow-key
  // navigation, and a tabpanel, none of which apply here. The design only
  // requires an accessible name plus an exposed selected state.
  const metricTabs = (
    <div className={styles.metricTabs} role="group" aria-label="Metric">
      {(['requests', 'tokens', 'cost'] as ExplorerMetric[]).map((m) => (
        <button
          key={m}
          type="button"
          aria-pressed={state.metric === m}
          className={`${styles.metricTab} ${
            state.metric === m ? styles.metricTabActive : ''
          }`}
          onClick={() => set({ metric: m })}
        >
          {metricLabel(m)}
        </button>
      ))}
    </div>
  );

  const summary = model
    ? `${metricLabel(state.metric)} over ${rangeLabel}. Total ${formatValue(
        state.metric,
        model.total,
      )}. Series: ${model.series.map((s) => s.label).join(', ') || 'none'}.`
    : '';

  return (
    <Card title="Usage explorer" action={metricTabs}>
      <div className={styles.toolbar}>
        <Segmented
          legend="Breakdown"
          options={breakdownOptions}
          value={state.breakdown}
          onChange={(breakdown) => set({ breakdown })}
        />
        <Segmented
          legend="Granularity"
          options={[
            { value: 'auto', label: 'Auto' },
            { value: 'hour', label: 'Hour' },
            { value: 'day', label: 'Day' },
          ]}
          value={displayedGranularity}
          onChange={(granularity) => set({ granularity })}
        />
        {showDisplay && (
          <Segmented
            legend="Display"
            options={[
              { value: 'line', label: 'Line' },
              { value: 'stacked', label: 'Stacked' },
            ]}
            value={state.display}
            onChange={(display) => set({ display })}
          />
        )}
        <div className={styles.zoomControl}>
          <Button variant="secondary" onClick={resetZoom} disabled={!zoomed}>
            Reset zoom
          </Button>
        </div>
      </div>

      {coarsened && (
        <p className={styles.coarsenNote}>
          Range too wide for hourly buckets; showing daily granularity.
        </p>
      )}

      <p className={styles.srSummary} role="status" aria-live="polite">
        {showLoading ? 'Loading chart data.' : error ? `Error: ${error}` : summary}
      </p>

      <div className={styles.chartShell}>
        {showLoading && !model ? (
          <div className={styles.center}>
            <LoadingSpinner />
          </div>
        ) : error && !freshData ? (
          <div className={styles.center}>
            <span className={styles.errorText}>Could not load explorer data: {error}</span>
          </div>
        ) : model?.pricingUnavailable ? (
          <div className={styles.center}>
            <span className={styles.emptyText}>Pricing data unavailable</span>
          </div>
        ) : !model || model.empty || !chartConfig ? (
          <div className={styles.center}>
            <span className={styles.emptyText}>No data for the selected filters</span>
          </div>
        ) : (
          <>
            <div className={styles.legend} role="group" aria-label="Chart legend">
              {model.series.map((s) => {
                const isHidden = hidden.includes(s.key);
                return (
                  <button
                    key={s.key}
                    type="button"
                    className={`${styles.legendItem} ${isHidden ? styles.legendItemOff : ''}`}
                    aria-pressed={!isHidden}
                    onClick={() => toggleSeries(s.key)}
                    title={s.label}
                  >
                    <span
                      className={styles.legendDot}
                      style={{ backgroundColor: s.warning ? 'rgba(214, 69, 61, 0.9)' : s.color }}
                    />
                    <span className={styles.legendLabel}>{s.label}</span>
                  </button>
                );
              })}
            </div>
            <div className={styles.chartArea} key={chartConfig.themeKey}>
              {chartConfig.chartType === 'bar' ? (
                <Bar
                  ref={storeChart}
                  data={chartConfig.data as ChartData<'bar'>}
                  options={chartConfig.options as ChartOptions<'bar'>}
                />
              ) : (
                <Line
                  ref={storeChart}
                  data={chartConfig.data as ChartData<'line'>}
                  options={chartConfig.options as ChartOptions<'line'>}
                />
              )}
            </div>
            {model.degraded.length > 0 && (
              <small className={styles.warning}>
                Partial/missing pricing: {model.degraded.join(', ')}
              </small>
            )}
          </>
        )}
      </div>
    </Card>
  );
}
