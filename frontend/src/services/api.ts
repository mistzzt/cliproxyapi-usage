import type {
  ApiKeysResponse,
  ApiStat,
  Bucket,
  CredentialStat,
  HealthResponse,
  Metric,
  ModelStat,
  ModelsResponse,
  OverviewResponse,
  PricingResponse,
  TimeseriesResponse,
  TokenBreakdownResponse,
} from '@/types/api';
import type { ResolvedRange } from '@/utils/rangeResolver';
import { apiPath } from './runtimeConfig';

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function buildQuery(params: Record<string, string | undefined>): string {
  const pairs: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined) {
      pairs.push(`${encodeURIComponent(k)}=${encodeURIComponent(v)}`);
    }
  }
  return pairs.length > 0 ? `?${pairs.join('&')}` : '';
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(path, init);

  if (!resp.ok) {
    throw new ApiError(resp.status, (await resp.text()) || resp.statusText);
  }

  return resp.json() as Promise<T>;
}

function encodeCsv(values?: string[]): string | undefined {
  return values?.length ? values.join(',') : undefined;
}

interface FilterParams {
  models?: string[];
  api_keys?: string[];
}

function filterQuery(f: FilterParams): Record<string, string | undefined> {
  return {
    models: encodeCsv(f.models),
    api_keys: encodeCsv(f.api_keys),
  };
}

/**
 * Serialize a resolved range into query params. `start` is dropped by
 * `buildQuery` when absent (open start = "all time").
 */
function rangeQuery(r: ResolvedRange): Record<string, string | undefined> {
  return {
    start: r.start,
    end: r.end,
    tz_offset_minutes: String(r.tzOffsetMinutes),
  };
}

export function getOverview(
  params: { range: ResolvedRange } & FilterParams,
): Promise<OverviewResponse> {
  return request<OverviewResponse>(
    apiPath(`/overview${buildQuery({ ...rangeQuery(params.range), ...filterQuery(params) })}`),
  );
}

export function getTimeseries(params: {
  range: ResolvedRange;
  bucket: Bucket;
  metric: Metric;
  top_n?: number;
} & FilterParams): Promise<TimeseriesResponse> {
  const { range, bucket, metric, top_n } = params;
  return request<TimeseriesResponse>(
    apiPath(`/timeseries${buildQuery({
      ...rangeQuery(range),
      bucket,
      metric,
      top_n: top_n !== undefined ? String(top_n) : undefined,
      ...filterQuery(params),
    })}`),
  );
}

export function getTokenBreakdown(params: {
  range: ResolvedRange;
  bucket: Bucket;
} & FilterParams): Promise<TokenBreakdownResponse> {
  return request<TokenBreakdownResponse>(
    apiPath(`/token-breakdown${buildQuery({
      ...rangeQuery(params.range),
      bucket: params.bucket,
      ...filterQuery(params),
    })}`),
  );
}

export function getApiStats(params: { range: ResolvedRange } & FilterParams): Promise<ApiStat[]> {
  return request<ApiStat[]>(
    apiPath(`/api-stats${buildQuery({ ...rangeQuery(params.range), ...filterQuery(params) })}`),
  );
}

export function getModelStats(
  params: { range: ResolvedRange } & FilterParams,
): Promise<ModelStat[]> {
  return request<ModelStat[]>(
    apiPath(`/model-stats${buildQuery({ ...rangeQuery(params.range), ...filterQuery(params) })}`),
  );
}

export function getCredentialStats(
  params: { range: ResolvedRange } & FilterParams,
): Promise<CredentialStat[]> {
  return request<CredentialStat[]>(
    apiPath(
      `/credential-stats${buildQuery({ ...rangeQuery(params.range), ...filterQuery(params) })}`,
    ),
  );
}

export function getHealth(params: { range: ResolvedRange } & FilterParams): Promise<HealthResponse> {
  return request<HealthResponse>(
    apiPath(`/health${buildQuery({ ...rangeQuery(params.range), ...filterQuery(params) })}`),
  );
}

export function getModels(): Promise<ModelsResponse> {
  return request<ModelsResponse>(apiPath('/models'));
}

export function getApiKeys(): Promise<ApiKeysResponse> {
  return request<ApiKeysResponse>(apiPath('/api-keys'));
}

export function getPricing(): Promise<PricingResponse> {
  return request<PricingResponse>(apiPath('/pricing'));
}
