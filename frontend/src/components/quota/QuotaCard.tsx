import type { ProviderQuota, QuotaAccount, QuotaResponse } from '@/types/api';
import { canStartReset, manualResetCount, PROVIDER_LABEL, type ResetActionState } from '@/utils/quota';
import { expiresSoon, formatAbsolute, formatRelative } from '@/utils/time';
import Card from '@/components/ui/Card';
import Button from '@/components/ui/Button';
import QuotaWindowBar from './QuotaWindowBar';
import b from '@/components/ui/Banner.module.scss';
import styles from './QuotaCard.module.scss';

const errorBanner = `${b.banner} ${b.error} ${styles.errorRow}`;

interface QuotaCardProps {
  account: QuotaAccount;
  /** The account's quota query; a refetch keeps showing the last response. */
  query: { data: QuotaResponse | undefined; error: Error | null };
  reset: ResetActionState;
  onRefresh: () => void;
  onRequestReset: () => void;
  onCancelReset: () => void;
  onConfirmReset: () => void;
}

const ERROR_MESSAGES = {
  auth: 'OAuth token expired — re-link this account in CLIProxyAPI.',
  rate_limited: 'Vendor rate-limited this request. Try again in a moment.',
  schema: "Couldn't parse vendor response.",
  transient: 'Temporary error talking to CLIProxyAPI.',
  unknown: 'An unknown error occurred.',
  no_data: 'No quota data available.',
} as const;

function SkeletonBars() {
  return (
    <div className={styles.skeleton}>
      {[0, 1, 2].map((i) => (
        <div key={i} className={styles.skeletonRow}>
          <div className={styles.skeletonLabel} />
          <div className={styles.skeletonBar} />
        </div>
      ))}
    </div>
  );
}

export default function QuotaCard({
  account,
  query,
  reset,
  onRefresh,
  onRequestReset,
  onCancelReset,
  onConfirmReset,
}: QuotaCardProps) {
  const title = account.display_name ?? PROVIDER_LABEL[account.provider];

  const response = query.data;
  const planPill =
    response?.quota?.plan_type != null ? <span className={styles.planPill}>{response.quota.plan_type}</span> : null;

  function renderBody() {
    if (response === undefined && query.error === null) {
      return <SkeletonBars />;
    }

    if (response === undefined) {
      const msg = query.error?.message || ERROR_MESSAGES.unknown;
      return (
        <div className={errorBanner}>
          <span>{msg}</span>
          <Button variant="secondary" onClick={onRefresh}>
            Retry
          </Button>
        </div>
      );
    }

    if (response.error !== null) {
      const kind = response.error.kind;
      const msg =
        kind === 'upstream'
          ? `Upstream vendor error (${response.error.upstream_status ?? '?'}).`
          : ERROR_MESSAGES[kind];
      return (
        <div className={errorBanner}>
          <span>{msg}</span>
        </div>
      );
    }

    if (response.quota === null) {
      return <div className={styles.empty}>{ERROR_MESSAGES.no_data}</div>;
    }

    return (
      <div>
        <div className={styles.windows}>
          {response.quota.windows.map((w) => (
            <QuotaWindowBar key={w.id} window={w} />
          ))}
        </div>
        {renderReset(response.quota)}
      </div>
    );
  }

  function renderCredits({ manual_resets }: ProviderQuota, confirming: boolean) {
    if (manual_resets === null) return null;
    const { credits } = manual_resets;
    if (credits.length > 0) {
      return (
        <ol className={styles.creditList} aria-label="Manual reset credits">
          {credits.map((credit, index) => {
            const soon = expiresSoon(credit.expires_at);
            // Credits arrive sorted by expiry, so the first row is the credit a reset spends.
            const spending = confirming && index === 0;
            const rowClass = [
              styles.creditRow,
              soon && styles.creditSoon,
              spending && styles.creditSpending,
            ]
              .filter(Boolean)
              .join(' ');
            return (
              <li
                key={credit.id || index}
                className={rowClass}
                data-soon={soon || undefined}
                data-spending={spending || undefined}
              >
                <span className={styles.creditOrdinal} aria-label={`Reset ${index + 1}`}>
                  {index + 1}
                </span>
                <span className={styles.creditAbsolute}>
                  <span className={styles.creditDate}>{formatAbsolute(credit.expires_at)}</span>
                  {soon && <span className={styles.creditSoonTag}>expiring</span>}
                </span>
                <span className={styles.creditRelative}>{formatRelative(credit.expires_at)}</span>
              </li>
            );
          })}
        </ol>
      );
    }
    if (manual_resets.credits_error !== null) {
      return <div className={styles.creditError}>Expiry unavailable: {manual_resets.credits_error}</div>;
    }
    return null;
  }

  function renderReset(quota: ProviderQuota) {
    const count = manualResetCount(quota);
    if (count === null) return null;
    const running = reset.status === 'loading';
    const confirming = reset.status === 'confirming' && canStartReset(quota);
    const hasCredits = (quota.manual_resets?.credits.length ?? 0) > 0;
    const prompt = hasCredits ? 'Consume reset 1?' : 'Consume one manual reset?';
    return (
      <div className={styles.manualReset}>
        <div className={styles.resetHeader}>
          <span className={styles.resetTitle}>
            Manual resets
            <span
              className={count > 0 ? styles.countBadge : `${styles.countBadge} ${styles.countZero}`}
              data-testid="manual-reset-count"
            >
              {count}
            </span>
          </span>
          {canStartReset(quota) && reset.status !== 'confirming' && (
            <Button variant="secondary" onClick={onRequestReset} disabled={running}>
              Reset quota
            </Button>
          )}
        </div>
        {renderCredits(quota, confirming)}
        {confirming && (
          <div className={styles.confirmation}>
            <span>{prompt}</span>
            <div className={styles.confirmActions}>
              <Button variant="secondary" onClick={onCancelReset}>Cancel</Button>
              <Button onClick={onConfirmReset}>Consume reset</Button>
            </div>
          </div>
        )}
        {reset.status === 'success' && <div className={styles.successMessage}>{reset.message}</div>}
        {reset.status === 'error' && <div className={styles.resetError}>{reset.message}</div>}
      </div>
    );
  }

  function renderFooter() {
    return (
      <div className={styles.footer}>
        <span className={styles.footerMeta}>
          {response &&
            `Last checked ${formatRelative(response.fetched_at)} · Next refresh ${formatRelative(response.stale_at)}`}
        </span>
        <Button variant="secondary" onClick={onRefresh} disabled={reset.status === 'loading'}>
          Refresh
        </Button>
      </div>
    );
  }

  return (
    <Card
      title={title}
      action={<div className={styles.headerAction}>{planPill}</div>}
    >
      {renderBody()}
      {renderFooter()}
    </Card>
  );
}
