import { expect, test } from 'bun:test';
import type { Totals } from '@/types/api';
import {
  avgLatency,
  cacheHitRate,
  cacheWriteShare,
  failureRateSeries,
  latencySeries,
  longContext,
  outputSpeed,
  reasoningShare,
  tokens,
} from './totals';

test('tokens are all input + output; cache reads and writes are already in input', () => {
  const totals = { input_tokens: 300, output_tokens: 30, cache_read_tokens: 200, cache_creation_tokens: 50 };
  expect(tokens(totals)).toBe(330);
});

const base: Totals = {
  requests: 10,
  failed: 4,
  input_tokens: 4000,
  cache_read_tokens: 3000,
  cache_creation_tokens: 400,
  output_tokens: 600,
  reasoning_tokens: 450,
  cost: 2,
  cost_status: 'live',
  cost_by_type: { input: 0.25, output: 1, cache_read: 0.5, cache_creation: 0.25 },
  long_context_cost: 0.5,
  cache_savings: 1,
  latency_ms: 12_000,
  long_context_requests: 3,
  last_used: null,
};

test('latency and output speed count successful requests only', () => {
  expect(avgLatency(base)).toBe(2000);
  expect(outputSpeed(base)).toBe(50);
  expect(avgLatency({ requests: 3, failed: 3, latency_ms: 0 })).toBeNull();
  expect(avgLatency({ requests: 0, failed: 0, latency_ms: 0 })).toBeNull();
  expect(outputSpeed({ output_tokens: 0, latency_ms: 0 })).toBeNull();
});

test('cache hit rate, cache write share and reasoning share', () => {
  expect(cacheHitRate(base)).toBe(0.75);
  expect(cacheHitRate({ input_tokens: 0, cache_read_tokens: 0 })).toBeNull();
  expect(cacheWriteShare(base)).toBe(0.1);
  expect(cacheWriteShare({ input_tokens: 0, cache_creation_tokens: 0 })).toBeNull();
  expect(reasoningShare(base)).toBe(0.75);
  expect(reasoningShare({ output_tokens: 0, reasoning_tokens: 0 })).toBeNull();
});

test('long context shares divide by successful requests', () => {
  expect(longContext(base)).toEqual({ spendShare: 0.25, requestShare: 0.5, avgContext: 4000 / 6 });
  expect(longContext({ ...base, long_context_cost: 0 })).toBeNull();
  expect(longContext({ ...base, cost: null, long_context_cost: null })).toBeNull();
  expect(longContext({ ...base, failed: 10 })).toBeNull();
});

test('per-bucket rates leave empty buckets as gaps', () => {
  const s = { requests: [4, 0, 2], failed: [1, 0, 2], latency_ms: [900, 0, 0] };
  expect(failureRateSeries(s)).toEqual([0.25, null, 1]);
  expect(latencySeries(s)).toEqual([300, null, null]);
});
