import { expect, test } from 'bun:test';
import { priceCell } from './price';

test('price cell lists input / output / cache read and describes long-context rates', () => {
  const price = { input: 5, output: 25, cache_read: 0.5, cache_write: 6.25, long_context: null };
  expect(priceCell(price, true)).toEqual({ text: '$5 / $25 / $0.50', tiered: false, title: 'Cache write: $6.25' });
  const long = { input: 10, output: 45, cache_read: 1, cache_write: 6.25, threshold: 272_000, whole_request: true };
  expect(priceCell({ ...price, long_context: long }, true)).toEqual({
    text: '$5 / $25 / $0.50',
    tiered: true,
    title: 'Above 272k (whole request): $10 / $45 / $1\nCache write: $6.25',
  });
  expect(priceCell(null, true)).toEqual({ text: 'n/a', tiered: false, title: null });
});

test('price cell omits the cache write rate when the model has no cache writes', () => {
  const price = { input: 2, output: 8, cache_read: 0.2, cache_write: 2.5, long_context: null };
  expect(priceCell(price, false)).toEqual({ text: '$2 / $8 / $0.20', tiered: false, title: null });
  const long = { input: 4, output: 12, cache_read: 0.4, cache_write: 5, threshold: 272_000, whole_request: true };
  expect(priceCell({ ...price, long_context: long }, false).title).toBe('Above 272k (whole request): $4 / $12 / $0.40');
});
