import type { QuotaAccountsResponse, QuotaProvider, QuotaResponse } from '@/types/api';

export class QuotaFetchError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function quotaRequest<T>(path: string): Promise<T> {
  const resp = await fetch(path);

  if (!resp.ok) {
    throw new QuotaFetchError(resp.status, (await resp.text()) || resp.statusText);
  }

  return resp.json() as Promise<T>;
}

export function fetchQuotaAccounts(): Promise<QuotaAccountsResponse> {
  return quotaRequest<QuotaAccountsResponse>('/api/quota/accounts');
}

export function fetchQuota(provider: QuotaProvider, authName: string): Promise<QuotaResponse> {
  return quotaRequest<QuotaResponse>(`/api/quota/${provider}/${encodeURIComponent(authName)}`);
}
