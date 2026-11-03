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

/** Fetches an API path; a non-2xx response throws `ApiError`. */
export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const resp = await fetch(apiPath(path), init);
  if (!resp.ok) throw new ApiError(resp.status, errorMessage(await resp.text()) || resp.statusText);
  return resp;
}

export async function getJson<T>(path: string, signal: AbortSignal, params: Record<string, string> = {}): Promise<T> {
  const query = new URLSearchParams(params).toString();
  const resp = await apiFetch(query ? `${path}?${query}` : path, { signal });
  return resp.json() as Promise<T>;
}

/** The usage request that `key` names. */
export function getUsage<E extends UsageEndpoint>(key: UsageQueryKey<E>, signal: AbortSignal): Promise<UsageResponses[E]> {
  return getJson(`/usage/${key[2]}`, signal, requestParams(key));
}

export function getOptions(signal: AbortSignal): Promise<OptionsResponse> {
  return getJson('/usage/options', signal);
}
