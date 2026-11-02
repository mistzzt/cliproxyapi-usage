import type { OptionsResponse } from '@/types/api';
import { requestParams, type UsageEndpoint, type UsageQueryKey, type UsageResponses } from '@/utils/usage/queryKeys';
import { apiPath } from './runtimeConfig';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** FastAPI's `{"detail": "..."}` message when present, else the raw body. */
function errorMessage(body: string): string {
  try {
    const detail: unknown = (JSON.parse(body) as { detail?: unknown }).detail;
    if (typeof detail === 'string') return detail;
  } catch {
    // Not JSON: fall through to the raw body.
  }
  return body;
}

async function get<T>(path: string, params: Record<string, string>, signal: AbortSignal): Promise<T> {
  const query = new URLSearchParams(params).toString();
  const resp = await fetch(apiPath(query ? `${path}?${query}` : path), { signal });
  if (!resp.ok) throw new ApiError(resp.status, errorMessage(await resp.text()) || resp.statusText);
  return resp.json() as Promise<T>;
}

/** The usage request that `key` names. */
export function getUsage<E extends UsageEndpoint>(key: UsageQueryKey<E>, signal: AbortSignal): Promise<UsageResponses[E]> {
  return get(`/usage/${key[2]}`, requestParams(key), signal);
}

export function getOptions(signal: AbortSignal): Promise<OptionsResponse> {
  return get('/usage/options', {}, signal);
}
