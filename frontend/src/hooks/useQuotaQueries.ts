import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { fetchQuota, fetchQuotaAccounts, resetQuota } from '@/services/quotaApi';
import type { QuotaAccount } from '@/types/api';

const quotaKey = ({ provider, auth_name }: QuotaAccount) => ['quota', provider, auth_name] as const;

// Quotas change over time, so each visit to the page fetches them again, failed ones included (never on focus).
const PER_VISIT = { staleTime: 0, retryOnMount: true } as const;

export function useQuotaAccounts() {
  return useQuery({ queryKey: ['quota-accounts'], queryFn: ({ signal }) => fetchQuotaAccounts(signal), ...PER_VISIT });
}

export function useQuota(account: QuotaAccount) {
  return useQuery({
    queryKey: quotaKey(account),
    queryFn: ({ signal }) => fetchQuota(account.provider, account.auth_name, signal),
    ...PER_VISIT,
  });
}

/** Consumes one manual reset; it settles once the account's quota is refetched, replacing any refresh in flight. */
export function useQuotaReset(account: QuotaAccount) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => resetQuota(account.provider, account.auth_name),
    onSuccess: () => qc.invalidateQueries({ queryKey: quotaKey(account), exact: true }),
  });
}
