import { expect, test } from 'bun:test';
import type { Totals } from '@/types/api';
import { buildRichRows, sortRichRows, type RichRowInput } from './richRows';

function row(key: string, cost: number, output_tokens: number): RichRowInput {
  const current: Totals = {
    requests: 1,
    failed: 0,
    input_tokens: 0,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    output_tokens,
    reasoning_tokens: 0,
    cost,
    cost_status: 'live',
    cost_by_type: { input: 0, output: cost, cache_read: 0, cache_creation: 0 },
    long_context_cost: 0,
    cache_savings: 0,
    latency_ms: 0,
    long_context_requests: 0,
    last_used: null,
  };
  const trend = { cost: [cost], input_tokens: [0], output_tokens: [output_tokens], cache_read_tokens: [0] };
  return { key, label: key, current, previous: null, trend, mix: [], price: null };
}

test('rows take share from the active metric and sort by it', () => {
  const inputs = [row('alice', 1, 30), row('bob', 3, 10)];
  const sorted = (metric: 'cost' | 'tokens') =>
    sortRichRows(buildRichRows(inputs, 'person', metric), { key: 'share', desc: true }).map((r) => [r.key, r.share]);
  expect(sorted('cost')).toEqual([
    ['bob', 0.75],
    ['alice', 0.25],
  ]);
  expect(sorted('tokens')).toEqual([
    ['alice', 0.75],
    ['bob', 0.25],
  ]);
});

test('model rows show cache reads over all input; other rows show none', () => {
  const input = row('m', 1, 10);
  const current = { ...input.current, input_tokens: 800, cache_read_tokens: 200, cache_creation_tokens: 100 };
  const rows = [{ ...input, current }];
  expect(buildRichRows(rows, 'model', 'cost').map((r) => r.cacheHit)).toEqual([0.25]);
  expect(buildRichRows(rows, 'person', 'cost').map((r) => r.cacheHit)).toEqual([null]);
});
