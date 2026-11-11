import { useEffect, useMemo } from 'react';
import b from '@/components/ui/Busy.module.scss';
import Button from '@/components/ui/Button';
import FailureBanner from '@/components/ui/FailureBanner';
import Loaded from '@/components/ui/Loaded';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { useUsage } from '@/hooks/useUsageQueries';
import type { BreakdownResponse, Dimension, Totals } from '@/types/api';
import { DIMENSION_LABEL, DRAWER_DIMENSIONS, type EntityLookup } from '@/utils/usage/entities';
import { type Change, change, fmtCompact, fmtCost, fmtPct } from '@/utils/usage/format';
import { breakdownKey, summaryKey, type ResolvedWindow } from '@/utils/usage/queryKeys';
import { shareItems } from '@/utils/usage/share';
import { breakdownChart } from '@/utils/usage/stack';
import {
  avgLatency,
  cacheReadRate,
  failureRate,
  fmtLatency,
  fmtSpeed,
  fmtSpend,
  longContext,
  outputSpeed,
  reasoningShare,
  spendByType,
} from '@/utils/usage/totals';
import { scopeToEntity, type DrawerTarget, type Metric, type ViewState } from '@/utils/usage/viewState';
import ChangeBadge from './ChangeBadge';
import FailureStrip from './FailureStrip';
import { StatTile } from './KpiStrip';
import ShareBar from './ShareBar';
import TimeChart from './TimeChart';
import shared from './Shared.module.scss';
import s from './DetailDrawer.module.scss';

const share = (part: number | null, whole: number | null | undefined) => (whole ? (part ?? 0) / whole : 0);

/** Entity details from `summary` and `breakdown` calls scoped to the page filters plus the entity. */
export default function DetailDrawer({
  target,
  state,
  win,
  pageTotals,
  lookup,
  onClose,
  onFilter,
}: {
  target: DrawerTarget;
  state: ViewState;
  win: ResolvedWindow;
  pageTotals: Totals | null;
  lookup: EntityLookup;
  onClose: () => void;
  onFilter: () => void;
}) {
  const { dim, key } = target;
  const [splitDim, otherDim] = DRAWER_DIMENSIONS[dim];
  const scoped = scopeToEntity(state, target);
  const summary = useUsage(summaryKey(win, scoped));
  const split = useUsage(breakdownKey(win, scoped, splitDim));
  const other = useUsage(breakdownKey(win, scoped, otherDim));
  const entity = lookup.entity(dim, key);
  const metric = state.metric;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const chart = useMemo(() => split.data && breakdownChart(split.data, metric, lookup), [split.data, metric, lookup]);
  const failures = [
    ['Summary', summary.error],
    [`By ${DIMENSION_LABEL[splitDim].toLowerCase()}`, split.error],
    [`By ${DIMENSION_LABEL[otherDim].toLowerCase()}`, other.error],
  ] as const;
  const anyFailed = failures.some(([, e]) => e !== null);
  const busy = summary.isFetching || split.isFetching || other.isFetching;
  const cur = summary.isError ? undefined : summary.data?.current;
  const prev = summary.data?.previous ?? null;
  const latency = cur ? avgLatency(cur) : null;
  const reasoning = cur ? reasoningShare(cur) : null;
  const hitRate = cur ? cacheReadRate(cur) : null;
  const long = cur ? longContext(cur) : null;

  return (
    <>
      <div className={s.scrim} onClick={onClose} />
      <aside className={s.drawer} aria-label={`${DIMENSION_LABEL[dim]} details`}>
        <div className={s.head}>
          <div>
            <div className={shared.muted}>{DIMENSION_LABEL[dim]}</div>
            <h2 className={s.title} title={key}>
              <i className={shared.swatch} style={{ background: lookup.color(dim, key) }} />
              {entity.label}
            </h2>
          </div>
          <div className={s.actions}>
            <Button variant="secondary" onClick={onFilter}>
              Filter page to this
            </Button>
            <button type="button" className={s.iconBtn} aria-label="Close" onClick={onClose}>
              ×
            </button>
          </div>
        </div>
        <FailureBanner failures={failures} />
        {!cur ? (
          !anyFailed && <LoadingSpinner />
        ) : (
          <div className={busy ? `${s.body} ${b.busy}` : s.body} aria-busy={busy}>
            <div className={s.tiles}>
              <StatTile
                className={s.tile}
                label="Spend"
                value={fmtSpend(cur)}
                foot={<Foot sub={`${fmtPct(share(cur.cost, pageTotals?.cost))} of total`} change={change(cur.cost ?? 0, prev?.cost)} />}
              />
              <StatTile
                className={s.tile}
                label="Requests"
                value={fmtCompact(cur.requests)}
                foot={
                  <Foot
                    sub={`${fmtPct(share(cur.requests, pageTotals?.requests))} of total, ${fmtPct(failureRate(cur))} failed`}
                    change={change(cur.requests, prev?.requests)}
                  />
                }
              />
              <StatTile
                className={s.tile}
                label="Avg latency"
                value={fmtLatency(latency)}
                title="Successful requests only"
                foot={
                  <Foot
                    sub={`${fmtSpeed(outputSpeed(cur))} output`}
                    change={latency === null ? null : change(latency, prev && avgLatency(prev))}
                  />
                }
              />
              <StatTile
                className={s.tile}
                label="Input"
                value={fmtCompact(cur.uncached_input_tokens)}
                title="Uncached input tokens"
                foot={
                  <Foot
                    sub={`${fmtPct(share(cur.uncached_input_tokens, pageTotals?.uncached_input_tokens))} of total`}
                    change={change(cur.uncached_input_tokens, prev?.uncached_input_tokens)}
                  />
                }
              />
              <StatTile
                className={s.tile}
                label="Output"
                value={fmtCompact(cur.output_tokens)}
                foot={
                  <Foot
                    sub={reasoning === null ? 'no output' : `${fmtPct(reasoning)} reasoning`}
                    change={change(cur.output_tokens, prev?.output_tokens)}
                  />
                }
              />
              <StatTile
                className={s.tile}
                label="Cached"
                value={fmtCompact(cur.cache_read_tokens)}
                title="Cache read tokens"
                foot={
                  <Foot
                    sub={hitRate === null ? 'no input' : `${fmtPct(hitRate)} hit rate`}
                    change={change(cur.cache_read_tokens, prev?.cache_read_tokens)}
                  />
                }
              />
            </div>
            <h3 className={s.section}>Over time by {DIMENSION_LABEL[splitDim].toLowerCase()}</h3>
            <Loaded query={{ data: chart, isError: split.isError }}>
              {(c) => <TimeChart buckets={c.buckets} bucket={c.bucket} layers={c.layers} metric={metric} theme={lookup.theme} />}
            </Loaded>
            {summary.data && (
              <div>
                <div className={`${shared.muted} ${s.stripHead}`}>Failure rate by {summary.data.bucket}</div>
                <FailureStrip
                  buckets={summary.data.buckets.map((b) => Date.parse(b))}
                  bucket={summary.data.bucket}
                  requests={summary.data.series.requests}
                  failed={summary.data.series.failed}
                  theme={lookup.theme}
                />
              </div>
            )}
            <ShareSection dim={splitDim} query={split} metric={metric} lookup={lookup} />
            <ShareSection dim={otherDim} query={other} metric={metric} lookup={lookup} />
            {cur.cost_by_type && (cur.cost ?? 0) > 0 && (
              <>
                <h3 className={s.section}>Spend by token type</h3>
                <ShareBar items={spendByType(cur.cost_by_type)} metric="cost" slotColor={lookup.slotColor} />
                {(cur.cache_savings ?? 0) > 0 && (
                  <div className={`${shared.muted} ${s.note}`}>
                    Cache reads saved {fmtCost(cur.cache_savings ?? 0)} compared with uncached input rates.
                  </div>
                )}
              </>
            )}
            {long && (
              <>
                <h3 className={s.section}>Long context</h3>
                <div className={s.facts}>
                  <Fact label="Spend at long rate" value={fmtPct(long.spendShare)} />
                  <Fact label="Requests at long rate" value={fmtPct(long.requestShare)} />
                  <Fact label="Avg context" value={`${fmtCompact(long.avgContext)} tokens`} />
                </div>
              </>
            )}
          </div>
        )}
      </aside>
    </>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className={s.fact}>
      <span className={shared.muted}>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function Foot({ sub, change }: { sub: string; change: Change }) {
  return (
    <>
      <span className={shared.muted}>{sub}</span>
      <ChangeBadge value={change} />
    </>
  );
}

function ShareSection({
  dim,
  query,
  metric,
  lookup,
}: {
  dim: Dimension;
  query: { data: BreakdownResponse | undefined; isError: boolean };
  metric: Metric;
  lookup: EntityLookup;
}) {
  return (
    <>
      <h3 className={s.section}>By {DIMENSION_LABEL[dim].toLowerCase()}</h3>
      <Loaded query={query}>
        {(data) => <ShareBar items={shareItems(data, metric, lookup)} metric={metric} slotColor={lookup.slotColor} />}
      </Loaded>
    </>
  );
}
