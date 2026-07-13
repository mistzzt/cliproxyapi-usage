/**
 * Active-dataset request selection (pure).
 *
 * Only the active explorer dataset is fetched. This module maps a normalized
 * explorer state plus the resolved global filter context to a single request
 * descriptor: which endpoint to hit and which params to send. Changing the
 * line/stacked display never changes the descriptor, so the display toggle
 * never triggers a refetch.
 */
import type { Bucket, Metric } from '@/types/api';
// Relative import (not the `@/` alias) so this pure module resolves under
// `bun test`, which reads the root tsconfig and does not see the app project's
// path alias for value imports.
import { MAX_EXPLORER_SERIES } from '../../../pages/usage-constants';
import type { ExplorerBreakdown, ExplorerGranularity, ExplorerState } from './explorerState';

export type ExplorerEndpoint = 'timeseries' | 'token-breakdown';

/**
 * The number of named model series requested in automatic decomposition. One
 * of the seven series slots is reserved for the derived `Other` series.
 */
export const EXPLORER_TOP_N = MAX_EXPLORER_SERIES - 1; // 6

export interface ExplorerRequest {
  endpoint: ExplorerEndpoint;
  /** Effective bucket to request (before any server coarsening). */
  bucket: Bucket;
  /** Present for the timeseries endpoint only. */
  metric?: Metric;
  /** Explicit model filter (scopes the query). Absent in all-models mode. */
  models?: string[];
  /** Automatic decomposition size; present only for model + all-models mode. */
  topN?: number;
  /** Explicit api-key filter. Absent in all-users mode. */
  apiKeys?: string[];
}

export interface ExplorerRequestContext {
  /** True when the global model filter is "all". */
  isAllModels: boolean;
  /** Explicit model selection (meaningful only when `isAllModels` is false). */
  selectedModels: string[];
  /** Explicit api-key selection, or undefined for all users. */
  apiKeys?: string[];
  /** Bucket implied by the range when granularity is 'auto'. */
  autoBucket: Bucket;
}

/** Resolve the requested bucket from the granularity preference. */
export function resolveRequestBucket(
  granularity: ExplorerGranularity,
  autoBucket: Bucket,
): Bucket {
  return granularity === 'auto' ? autoBucket : granularity;
}

/**
 * Build the request descriptor for the currently active explorer dataset.
 *
 * - `token_type` breakdown → `token-breakdown` endpoint.
 * - Otherwise → `timeseries` endpoint with the active metric. The model
 *   filter is sent whenever an explicit selection is active (to scope both
 *   total and model breakdowns); `top_n` is sent only for automatic model
 *   decomposition (model breakdown + all-models).
 */
export function selectExplorerRequest(
  state: ExplorerState,
  ctx: ExplorerRequestContext,
): ExplorerRequest {
  const bucket = resolveRequestBucket(state.granularity, ctx.autoBucket);
  const apiKeys = ctx.apiKeys && ctx.apiKeys.length > 0 ? ctx.apiKeys : undefined;
  const explicitModels = !ctx.isAllModels ? ctx.selectedModels : undefined;

  if (state.breakdown === 'token_type') {
    const req: ExplorerRequest = { endpoint: 'token-breakdown', bucket };
    if (explicitModels) req.models = explicitModels;
    if (apiKeys) req.apiKeys = apiKeys;
    return req;
  }

  const req: ExplorerRequest = { endpoint: 'timeseries', bucket, metric: state.metric };
  if (explicitModels) {
    req.models = explicitModels;
  } else if (state.breakdown === 'model') {
    req.topN = EXPLORER_TOP_N;
  }
  if (apiKeys) req.apiKeys = apiKeys;
  return req;
}

/**
 * Stable string key for the descriptor, used as a data-fetch dependency.
 * Two states that produce the same descriptor share a fetch (e.g. switching
 * line <-> stacked, which is not encoded here).
 */
export function requestKey(req: ExplorerRequest): string {
  return JSON.stringify({
    e: req.endpoint,
    b: req.bucket,
    m: req.metric ?? null,
    models: req.models ?? null,
    t: req.topN ?? null,
    k: req.apiKeys ?? null,
  });
}

/**
 * Key that governs the explorer *view* (the drawn series set, the legend
 * selection, and the zoom window) rather than the fetch. It extends the fetch
 * key with the breakdown so that switching breakdown total <-> model always
 * clears zoom and legend state, even when the underlying request is byte
 * identical (e.g. with an explicit model selection, where `total` merely sums
 * the same per-model series the `model` breakdown draws separately). The
 * design requires any breakdown change to clear the zoom state, so this key,
 * not `requestKey`, drives zoom/legend resets.
 */
export function zoomClearKey(req: ExplorerRequest, breakdown: ExplorerBreakdown): string {
  return `${requestKey(req)}|${breakdown}`;
}
