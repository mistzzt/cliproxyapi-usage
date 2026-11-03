import { useState } from 'react';
import QuotaCard from '@/components/quota/QuotaCard';
import { useQuota, useQuotaAccounts, useQuotaReset } from '@/hooks/useQuotaQueries';
import { ApiError } from '@/services/api';
import type { QuotaAccount, QuotaProvider } from '@/types/api';
import { resetState } from '@/utils/quotaReset';
import styles from './QuotaPage.module.scss';

const PROVIDERS: { id: QuotaProvider; label: string }[] = [
  { id: 'claude', label: 'Claude' },
  { id: 'codex', label: 'Codex' },
];

export default function QuotaPage() {
  const accounts = useQuotaAccounts();

  function renderBanner() {
    if (accounts.isError) {
      if (accounts.error instanceof ApiError && accounts.error.status === 503) {
        return (
          <div className={`${styles.banner} ${styles.bannerInfo}`}>
            Quota display is disabled. Set `CLIPROXY_BASE_URL` and `CLIPROXY_MANAGEMENT_KEY` on the server.
          </div>
        );
      }
      return (
        <div className={`${styles.banner} ${styles.bannerError}`}>
          Upstream proxy is unavailable. Try again shortly.
        </div>
      );
    }

    if (accounts.data?.accounts.length === 0) {
      return (
        <div className={`${styles.banner} ${styles.bannerInfo}`}>
          No OAuth accounts found. Claude and Codex auth-files registered with CLIProxyAPI will appear here.
        </div>
      );
    }

    return null;
  }

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Plan Limits</h1>
      </div>

      {renderBanner()}

      {accounts.data && (
        <div className={styles.providers}>
          {PROVIDERS.map(({ id: providerId, label }) => {
            const providerAccounts = accounts.data.accounts.filter((a) => a.provider === providerId);
            if (providerAccounts.length === 0) return null;

            return (
              <section key={providerId} className={styles.providerSection}>
                <h2 className={styles.providerTitle}>{label}</h2>
                <div className={styles.grid}>
                  {providerAccounts.map((acct) => (
                    <AccountQuota key={`${acct.provider}/${acct.auth_name}`} account={acct} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}

function AccountQuota({ account }: { account: QuotaAccount }) {
  const quota = useQuota(account);
  const reset = useQuotaReset(account);
  // The quota response a confirmation was opened against; any refetch cancels it.
  const [confirmedAt, setConfirmedAt] = useState<number | null>(null);
  return (
    <QuotaCard
      account={account}
      query={quota}
      reset={resetState(confirmedAt === quota.dataUpdatedAt, reset)}
      onRefresh={() => void quota.refetch()}
      onRequestReset={() => {
        reset.reset();
        setConfirmedAt(quota.dataUpdatedAt);
      }}
      onCancelReset={() => setConfirmedAt(null)}
      onConfirmReset={() => {
        setConfirmedAt(null);
        reset.mutate();
      }}
    />
  );
}
