import type { QuotaAccountsResponse, QuotaProvider, QuotaResponse } from '@/types/api';
import { apiFetch, getJson } from './api';

const accountPath = (provider: QuotaProvider, authName: string) => `/quota/${provider}/${encodeURIComponent(authName)}`;

export function fetchQuotaAccounts(signal: AbortSignal): Promise<QuotaAccountsResponse> {
  return getJson('/quota/accounts', signal);
}

export function fetchQuota(provider: QuotaProvider, authName: string, signal: AbortSignal): Promise<QuotaResponse> {
  return getJson(accountPath(provider, authName), signal);
}

export async function resetQuota(provider: QuotaProvider, authName: string): Promise<void> {
  await apiFetch(`${accountPath(provider, authName)}/reset`, { method: 'POST' });
}
