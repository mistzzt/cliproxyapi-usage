export type Range = '7h' | '24h' | '7d' | 'all';
export type Bucket = 'hour' | 'day';
export type Metric = 'requests' | 'tokens' | 'cost';

export interface Totals {
  requests: number;
  tokens: number;
  cost: number | null;
  rpm: number;
  tpm: number;
}

export interface SparklinePoint {
  ts: string;
  value: number;
}

export interface Sparklines {
  requests: SparklinePoint[];
  tokens: SparklinePoint[];
  rpm: SparklinePoint[];
  tpm: SparklinePoint[];
  cost: SparklinePoint[];
}

export interface OverviewResponse {
  totals: Totals;
  sparklines: Sparklines;
}

export interface TimeseriesResponse {
  buckets: string[];
  series: Record<string, number[]>;
}

export interface TokenBreakdownResponse {
  buckets: string[];
  input: number[];
  output: number[];
  cached: number[];
  reasoning: number[];
}

export interface ApiStat {
  api_key: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number | null;
  failed: number;
  avg_latency_ms: number;
}

export interface ModelStat {
  model: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  cost: number | null;
  avg_latency_ms: number;
  failed: number;
}

export interface CredentialStat {
  source: string;
  requests: number;
  total_tokens: number;
  failed: number;
  cost: number | null;
}

export interface LatencyPercentiles {
  p50: number;
  p95: number;
  p99: number;
}

export interface HealthResponse {
  total_requests: number;
  failed: number;
  failed_rate: number;
  latency: LatencyPercentiles;
}

export interface ModelsResponse {
  models: string[];
}

export interface ApiKeysResponse {
  api_keys: string[];
}

export interface PricingEntry {
  input: number | null;
  output: number | null;
  cache_read: number | null;
  cache_creation: number | null;
  tiered: boolean;
}

export interface PricingResponse {
  pricing: Record<string, PricingEntry>;
}

export type QuotaProvider = 'claude' | 'codex';

export interface QuotaAccount {
  provider: QuotaProvider;
  auth_name: string;
  display_name: string | null;
}

export interface QuotaAccountsResponse {
  accounts: QuotaAccount[];
}

export interface QuotaWindow {
  id: string;
  label: string;
  used_percent: number | null;
  resets_at: string | null; // ISO-8601
}

export interface ProviderQuota {
  provider: QuotaProvider;
  auth_name: string;
  plan_type: string | null;
  windows: QuotaWindow[];
  extra: Record<string, unknown>;
}

export type QuotaErrorKind = 'auth' | 'rate_limited' | 'upstream' | 'schema' | 'transient';

export interface QuotaError {
  kind: QuotaErrorKind;
  message: string;
  upstream_status: number | null;
}

export interface QuotaResponse {
  quota: ProviderQuota | null;
  error: QuotaError | null;
  fetched_at: string; // ISO-8601
  stale_at: string;   // ISO-8601
}
