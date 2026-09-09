import type { ProviderQuota, QuotaAccount, QuotaProvider } from '@/types/api';
import type { QuotaSlotState } from '@/stores/quotaStore';
import {
  canStartReset,
  manualResetCount,
  nextExpiringCredit,
  type ResetActionState,
} from '@/stores/quotaResetState';
import { formatAbsolute, formatRelative } from '@/utils/time';
import Card from '@/components/ui/Card';
import Button from '@/components/ui/Button';
import QuotaWindowBar from './QuotaWindowBar';
import styles from './QuotaCard.module.scss';

interface QuotaCardProps {
  account: QuotaAccount;
  slot: QuotaSlotState;
  reset: ResetActionState;
  onRefresh: () => void;
  onRequestReset: () => void;
  onCancelReset: () => void;
  onConfirmReset: () => void;
}

const PROVIDER_LABELS: Record<QuotaProvider, string> = {
  claude: 'Claude',
  codex: 'Codex',
};

const ERROR_MESSAGES = {
  auth: 'OAuth token expired — re-link this account in CLIProxyAPI.',
  rate_limited: 'Vendor rate-limited this request. Try again in a moment.',
  upstream: 'Upstream vendor error.',
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
  slot,
  reset,
  onRefresh,
  onRequestReset,
  onCancelReset,
  onConfirmReset,
}: QuotaCardProps) {
  const title = account.display_name ?? PROVIDER_LABELS[account.provider];

  const planPill =
    slot.status === 'success' && slot.response?.quota?.plan_type != null ? (
      <span className={styles.planPill}>{slot.response.quota.plan_type}</span>
    ) : null;

  function renderBody() {
    if ((slot.status === 'idle' || slot.status === 'loading') && slot.response === undefined) {
      return <SkeletonBars />;
    }

    if (slot.status === 'error' && slot.response === undefined) {
      const msg = slot.fetchError?.message ?? ERROR_MESSAGES.unknown;
      return (
        <div className={styles.errorBanner}>
          <span>{msg}</span>
          <Button variant="secondary" onClick={onRefresh}>
            Retry
          </Button>
        </div>
      );
    }

    const response = slot.response;
    if (response === undefined) return null;

    if (response.error !== null) {
      const kind = response.error.kind;
      const msg =
        kind === 'upstream'
          ? `Upstream vendor error (${response.error.upstream_status ?? '?'}).`
          : ERROR_MESSAGES[kind];
      return (
        <div className={styles.errorBanner}>
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

  function renderCredits(quota: ProviderQuota) {
    const credits = quota.manual_resets?.credits ?? [];
    const error = quota.manual_resets?.credits_error ?? null;
    if (credits.length > 0) {
      return (
        <ul className={styles.creditList}>
          {credits.map((credit, index) => (
            <li key={credit.id || index} className={styles.creditRow}>
              <span>Reset {index + 1}</span>
              <span className={styles.creditExpiry}>
                expires {formatAbsolute(credit.expires_at)} ({formatRelative(credit.expires_at)})
              </span>
            </li>
          ))}
        </ul>
      );
    }
    if (error !== null) {
      return <div className={styles.creditError}>Expiry unavailable: {error}</div>;
    }
    return null;
  }

  function renderReset(quota: ProviderQuota) {
    const count = manualResetCount(quota);
    if (count === null) return null;
    const running = reset.status === 'loading';
    const spending = nextExpiringCredit(quota);
    const prompt =
      spending === null
        ? 'Consume one manual reset?'
        : `Consume one manual reset? This spends the credit expiring ${formatAbsolute(spending.expires_at)}.`;
    return (
      <div className={styles.manualReset}>
        <div className={styles.resetHeader}>
          <span>Manual resets: {count}</span>
          {canStartReset(quota) && reset.status !== 'confirming' && (
            <Button variant="secondary" onClick={onRequestReset} disabled={running}>
              Reset quota
            </Button>
          )}
        </div>
        {renderCredits(quota)}
        {reset.status === 'confirming' && canStartReset(quota) && (
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
    if (slot.status !== 'success' || slot.response === undefined) {
      return (
        <div className={styles.footer}>
          <span />
          <Button variant="secondary" onClick={onRefresh} disabled={reset.status === 'loading'}>
            Refresh
          </Button>
        </div>
      );
    }

    const { fetched_at, stale_at } = slot.response;
    return (
      <div className={styles.footer}>
        <span className={styles.footerMeta}>
          Last checked {formatRelative(fetched_at)} · Next refresh {formatRelative(stale_at)}
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
