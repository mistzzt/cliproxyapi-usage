import { useEffect, useMemo } from 'react';
import b from '@/components/ui/Busy.module.scss';
import Button from '@/components/ui/Button';
import FailureBanner from '@/components/ui/FailureBanner';
import Loaded from '@/components/ui/Loaded';
import LoadingSpinner from '@/components/ui/LoadingSpinner';
import { useUsage } from '@/hooks/useUsageQueries';
import type { BreakdownResponse, Dimension, Totals } from '@/types/api';
import { DIMENSION_LABEL, DRAWER_DIMENSIONS, type EntityLookup } from '@/utils/usage/entities';
import { type Change, change, fmtCompact, fmtPct } from '@/utils/usage/format';
import { breakdownKey, summaryKey, type ResolvedWindow } from '@/utils/usage/queryKeys';
import { shareItems } from '@/utils/usage/share';
import { breakdownChart } from '@/utils/usage/stack';
import { failureRate, fmtSpend, tokens } from '@/utils/usage/totals';
import { scopeToEntity, type DrawerTarget, type Metric, type ViewState } from '@/utils/usage/viewState';
import ChangeBadge from './ChangeBadge';
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
                label="Tokens"
                value={fmtCompact(tokens(cur))}
                foot={
                  <Foot
                    sub={`${fmtPct(share(tokens(cur), pageTotals && tokens(pageTotals)))} of total`}
                    change={change(tokens(cur), prev && tokens(prev))}
                  />
                }
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
            </div>
            <h3 className={s.section}>Over time by {DIMENSION_LABEL[splitDim].toLowerCase()}</h3>
            <Loaded query={{ data: chart, isError: split.isError }}>
              {(c) => <TimeChart buckets={c.buckets} bucket={c.bucket} layers={c.layers} metric={metric} theme={lookup.theme} />}
            </Loaded>
            <ShareSection dim={splitDim} query={split} metric={metric} lookup={lookup} />
            <ShareSection dim={otherDim} query={other} metric={metric} lookup={lookup} />
            <h3 className={s.section}>Token mix</h3>
            <div className={s.tokenMix}>
              <span>Input {fmtCompact(cur.input_tokens)}</span>
              <span>Output {fmtCompact(cur.output_tokens)}</span>
              <span>Cached {fmtCompact(cur.cached_tokens)}</span>
              <span>Reasoning {fmtCompact(cur.reasoning_tokens)}</span>
            </div>
          </div>
        )}
      </aside>
    </>
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
