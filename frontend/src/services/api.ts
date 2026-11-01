import type {
  BreakdownResponse,
  Dimension,
  MatrixResponse,
  OptionsResponse,
  SummaryResponse,
} from '@/types/api';
import { apiPath } from './runtimeConfig';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Range, `tz`, `bucket` and filter params, as built by `usageQueryParams`. */
export type UsageParams = Readonly<Record<string, string>>;

async function get<T>(path: string, params: UsageParams = {}): Promise<T> {
  const query = new URLSearchParams(params).toString();
  const resp = await fetch(apiPath(query ? `${path}?${query}` : path));
  if (!resp.ok) throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
  return resp.json() as Promise<T>;
}

export function getSummary(params: UsageParams): Promise<SummaryResponse> {
  return get('/usage/summary', params);
}

export function getBreakdown(dim: Dimension, params: UsageParams): Promise<BreakdownResponse> {
  return get('/usage/breakdown', { ...params, dim });
}

/** The matrix has no time axis, so any `bucket` param is dropped. */
export function getMatrix(params: UsageParams, rows: Dimension = 'person', cols: Dimension = 'model'): Promise<MatrixResponse> {
  const query: Record<string, string> = { ...params, rows, cols };
  delete query.bucket;
  return get('/usage/matrix', query);
}

export function getOptions(): Promise<OptionsResponse> {
  return get('/usage/options');
}
