import { useEffect } from 'react';
import { useQuotaStore } from '@/stores';
import type { QuotaProvider } from '@/types/api';
import AppHeader from '@/components/ui/AppHeader';
import QuotaCard from '@/components/quota/QuotaCard';
import styles from './QuotaPage.module.scss';

const PROVIDERS: { id: QuotaProvider; label: string }[] = [
  { id: 'claude', label: 'Claude' },
  { id: 'codex', label: 'Codex' },
];

export default function QuotaPage() {
  const { accounts, slots, loadAccounts, loadQuota, slotKey } = useQuotaStore();

  useEffect(() => {
    void loadAccounts().then(() => {
      const current = useQuotaStore.getState();
      if (current.accounts.status === 'success' && current.accounts.data !== undefined) {
        for (const acct of current.accounts.data) {
          void current.loadQuota({ provider: acct.provider, authName: acct.auth_name });
        }
      }
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleRefresh(provider: QuotaProvider, authName: string) {
    void loadQuota({ provider, authName });
  }

  function renderBanner() {
    if (accounts.status === 'error') {
      const status = accounts.error?.status ?? 0;
      if (status === 503) {
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

    if (accounts.status === 'success' && (accounts.data === undefined || accounts.data.length === 0)) {
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
      <AppHeader />
      <div className={styles.header}>
        <h1 className={styles.title}>OAuth Quota</h1>
      </div>

      {renderBanner()}

      {accounts.status === 'success' && accounts.data !== undefined && (
        <div className={styles.providers}>
          {PROVIDERS.map(({ id: providerId, label }) => {
            const providerAccounts = accounts.data!.filter((a) => a.provider === providerId);
            if (providerAccounts.length === 0) return null;

            return (
              <section key={providerId} className={styles.providerSection}>
                <h2 className={styles.providerTitle}>{label}</h2>
                <div className={styles.grid}>
                  {providerAccounts.map((acct) => {
                    const key = slotKey({ provider: acct.provider, authName: acct.auth_name });
                    const slot = slots[key] ?? { status: 'loading' as const };
                    return (
                      <QuotaCard
                        key={key}
                        account={acct}
                        slot={slot}
                        onRefresh={() => handleRefresh(acct.provider, acct.auth_name)}
                      />
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </div>
  );
}
