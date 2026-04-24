import type { QuotaAccount, QuotaProvider } from '@/types/api';
import type { QuotaSlotState } from '@/stores/quotaStore';
import { formatRelative } from '@/utils/time';
import Card from '@/components/ui/Card';
import Button from '@/components/ui/Button';
import QuotaWindowBar from './QuotaWindowBar';
import styles from './QuotaCard.module.scss';

interface QuotaCardProps {
  account: QuotaAccount;
  slot: QuotaSlotState;
  onRefresh: () => void;
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

export default function QuotaCard({ account, slot, onRefresh }: QuotaCardProps) {
  const title = account.display_name ?? PROVIDER_LABELS[account.provider];

  const planPill =
    slot.status === 'success' && slot.response?.quota?.plan_type != null ? (
      <span className={styles.planPill}>{slot.response.quota.plan_type}</span>
    ) : null;

  function renderBody() {
    if (slot.status === 'idle' || slot.status === 'loading') {
      return <SkeletonBars />;
    }

    if (slot.status === 'error') {
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
      <div className={styles.windows}>
        {response.quota.windows.map((w) => (
          <QuotaWindowBar key={w.id} window={w} />
        ))}
      </div>
    );
  }

  function renderFooter() {
    if (slot.status !== 'success' || slot.response === undefined) {
      return (
        <div className={styles.footer}>
          <span />
          <Button variant="secondary" onClick={onRefresh}>
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
        <Button variant="secondary" onClick={onRefresh}>
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
