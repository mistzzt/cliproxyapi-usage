import { create } from 'zustand';
import type { QuotaAccount, QuotaProvider, QuotaResponse } from '@/types/api';
import { fetchQuota, fetchQuotaAccounts, QuotaFetchError } from '@/services/quotaApi';

export interface AccountKey {
  provider: QuotaProvider;
  authName: string;
}

export interface QuotaSlotState {
  status: 'idle' | 'loading' | 'success' | 'error';
  response?: QuotaResponse;
  fetchError?: QuotaFetchError;
}

interface AccountsState {
  status: 'idle' | 'loading' | 'success' | 'error';
  data?: QuotaAccount[];
  error?: QuotaFetchError;
}

interface QuotaStoreState {
  accounts: AccountsState;
  slots: Record<string, QuotaSlotState>;
  loadAccounts(): Promise<void>;
  loadQuota(key: AccountKey): Promise<void>;
  slotKey(key: AccountKey): string;
}

// Track in-flight accounts promise for idempotency
let accountsInflight: Promise<void> | null = null;

export const useQuotaStore = create<QuotaStoreState>((set, get) => ({
  accounts: { status: 'idle' },
  slots: {},

  slotKey({ provider, authName }: AccountKey): string {
    return `${provider}/${authName}`;
  },

  loadAccounts(): Promise<void> {
    // Idempotent while in-flight
    if (accountsInflight !== null) {
      return accountsInflight;
    }
    if (get().accounts.status === 'loading') {
      // Should be covered by accountsInflight, but guard anyway
      return Promise.resolve();
    }

    set((state) => ({ accounts: { ...state.accounts, status: 'loading' } }));

    accountsInflight = fetchQuotaAccounts()
      .then((resp) => {
        set({ accounts: { status: 'success', data: resp.accounts } });
      })
      .catch((err: unknown) => {
        const fetchError = err instanceof QuotaFetchError ? err : new QuotaFetchError(0, String(err));
        set({ accounts: { status: 'error', error: fetchError } });
      })
      .finally(() => {
        accountsInflight = null;
      });

    return accountsInflight;
  },

  loadQuota({ provider, authName }: AccountKey): Promise<void> {
    const key = get().slotKey({ provider, authName });

    set((state) => ({
      slots: {
        ...state.slots,
        [key]: { status: 'loading' },
      },
    }));

    return fetchQuota(provider, authName)
      .then((response) => {
        set((state) => ({
          slots: {
            ...state.slots,
            [key]: { status: 'success', response },
          },
        }));
      })
      .catch((err: unknown) => {
        const fetchError = err instanceof QuotaFetchError ? err : new QuotaFetchError(0, String(err));
        set((state) => ({
          slots: {
            ...state.slots,
            [key]: { status: 'error', fetchError },
          },
        }));
      });
  },
}));
