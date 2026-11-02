// Usage DTOs, hand-mirrored from src/cliproxy_usage_server/schemas.py.

export type Dimension = 'person' | 'model' | 'credential';
export type Bucket = 'hour' | 'day' | 'week';
export type CostStatus = 'live' | 'partial_missing' | 'missing';

export interface Totals {
  requests: number;
  failed: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  reasoning_tokens: number;
  cost: number | null;
  cost_status: CostStatus;
  avg_latency_ms: number;
  last_used: string | null; // ISO-8601
}

/** Raw token components; derived "tokens" lives in `utils/usage/totals.ts`. */
export type TokenPart = 'input_tokens' | 'output_tokens' | 'cached_tokens';

export interface SummaryResponse {
  /** Effective bucket after auto selection or coarsening. */
  bucket: Bucket;
  buckets: string[]; // ISO-8601 bucket starts, dense
  current: Totals;
  /** Null when the window has no start (all time). */
  previous: Totals | null;
  series: Record<'cost' | TokenPart | 'requests', number[]>;
}

export interface MixEntry {
  key: string;
  label: string;
  cost: number | null;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
}

/** USD per 1M tokens of each type. */
export interface UnitPrices {
  input: number;
  output: number;
  cache_read: number;
  cache_write: number;
}

/** Rates past `threshold` tokens, for the whole request or each type's excess. */
export interface LongContextPrice extends UnitPrices {
  threshold: number;
  whole_request: boolean;
}

/** The rates cost is computed with (base tier, no speed multiplier). */
export interface ModelPrice extends UnitPrices {
  long_context: LongContextPrice | null;
  /** OpenAI accounting: cached tokens are a subset of input. */
  cached_in_input: boolean;
}

export interface BreakdownRow {
  key: string;
  label: string;
  current: Totals;
  previous: Totals | null;
  /** Aligned with the response buckets. */
  trend: Record<'cost' | TokenPart, number[]>;
  /** Paired dimension: person -> model, model and credential -> person. */
  mix: MixEntry[];
  /** Model rows only; null when unpriced or not a model row. */
  price: ModelPrice | null;
}

export interface BreakdownResponse {
  dim: Dimension;
  bucket: Bucket;
  buckets: string[];
  rows: BreakdownRow[];
}

export interface MatrixCell {
  row: string;
  col: string;
  cost: number | null;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  requests: number;
}

export interface MatrixResponse {
  rows: string[];
  cols: string[];
  /** Sparse; an absent cell means zero. */
  cells: MatrixCell[];
}

export interface Entity {
  key: string;
  label: string;
  /** Color slot 0..6 by all-time spend; null folds into Other. */
  slot: number | null;
}

export interface OptionsResponse {
  people: Entity[];
  models: Entity[];
  credentials: Entity[];
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

export interface ManualResetCredit {
  id: string;
  granted_at: string | null; // ISO-8601
  expires_at: string; // ISO-8601
}

export interface ManualResetSummary {
  available_count: number;
  /** Sorted by expires_at ascending; empty when per-credit detail is unavailable. */
  credits: ManualResetCredit[];
  credits_error: string | null;
}

export interface ProviderQuota {
  provider: QuotaProvider;
  auth_name: string;
  plan_type: string | null;
  windows: QuotaWindow[];
  manual_resets: ManualResetSummary | null;
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
