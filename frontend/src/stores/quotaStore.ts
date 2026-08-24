import { create } from 'zustand';
import type { QuotaAccount, QuotaProvider, QuotaResponse } from '@/types/api';
import { fetchQuota, fetchQuotaAccounts, QuotaFetchError, resetQuota } from '@/services/quotaApi';
import { beginReset, canStartReset, type ResetActionState } from './quotaResetState';

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
  resets: Record<string, ResetActionState>;
  loadAccounts(): Promise<void>;
  loadQuota(key: AccountKey): Promise<void>;
  requestResetConfirmation(key: AccountKey): void;
  cancelReset(key: AccountKey): void;
  submitReset(key: AccountKey): Promise<void>;
  slotKey(key: AccountKey): string;
}

// Track in-flight accounts promise for idempotency
let accountsInflight: Promise<void> | null = null;
const resetInflight = new Map<string, Promise<void>>();
const quotaRequestVersions = new Map<string, number>();

export const useQuotaStore = create<QuotaStoreState>((set, get) => ({
  accounts: { status: 'idle' },
  slots: {},
  resets: {},

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
    const version = (quotaRequestVersions.get(key) ?? 0) + 1;
    quotaRequestVersions.set(key, version);

    set((state) => ({
      slots: {
        ...state.slots,
        [key]: { ...state.slots[key], status: 'loading' },
      },
    }));

    return fetchQuota(provider, authName)
      .then((response) => {
        set((state) => ({
          ...(quotaRequestVersions.get(key) === version
            ? {
                slots: {
                  ...state.slots,
                  [key]: { status: 'success' as const, response },
                },
                resets:
                  state.resets[key]?.status === 'confirming' && !canStartReset(response.quota)
                    ? { ...state.resets, [key]: { status: 'idle' as const } }
                    : state.resets,
              }
            : {}),
        }));
      })
      .catch((err: unknown) => {
        const fetchError = err instanceof QuotaFetchError ? err : new QuotaFetchError(0, String(err));
        if (quotaRequestVersions.get(key) !== version) return;
        set((state) => ({
          slots: {
            ...state.slots,
            [key]: { ...state.slots[key], status: 'error', fetchError },
          },
        }));
      });
  },

  requestResetConfirmation(key: AccountKey): void {
    const slot = get().slotKey(key);
    set((state) => ({
      resets: {
        ...state.resets,
        [slot]: beginReset(state.resets[slot] ?? { status: 'idle' }),
      },
    }));
  },

  cancelReset(key: AccountKey): void {
    const slot = get().slotKey(key);
    if (get().resets[slot]?.status === 'loading') return;
    set((state) => ({ resets: { ...state.resets, [slot]: { status: 'idle' } } }));
  },

  submitReset(key: AccountKey): Promise<void> {
    const slot = get().slotKey(key);
    const existing = resetInflight.get(slot);
    if (existing !== undefined) return existing;
    if (get().resets[slot]?.status !== 'confirming') return Promise.resolve();
    if (!canStartReset(get().slots[slot]?.response?.quota)) {
      set((state) => ({ resets: { ...state.resets, [slot]: { status: 'idle' } } }));
      return Promise.resolve();
    }

    set((state) => ({ resets: { ...state.resets, [slot]: { status: 'loading' } } }));
    const promise = resetQuota(key.provider, key.authName)
      .then(async () => {
        await get().loadQuota(key);
        set((state) => ({
          resets: {
            ...state.resets,
            [slot]: { status: 'success', message: 'Manual reset consumed.' },
          },
        }));
      })
      .catch((err: unknown) => {
        const message = err instanceof Error ? err.message : String(err);
        set((state) => ({
          resets: { ...state.resets, [slot]: { status: 'error', message } },
        }));
      })
      .finally(() => {
        resetInflight.delete(slot);
      });
    resetInflight.set(slot, promise);
    return promise;
  },
}));
