import { expect, test } from 'bun:test';
import { priceCell } from './price';

test('price cell lists input / output / cache read and describes long-context rates', () => {
  const price = { input: 5, output: 25, cache_read: 0.5, cache_write: 6.25, long_context: null };
  expect(priceCell(price)).toEqual({ text: '$5 / $25 / $0.50', tiered: false, title: 'Cache write: $6.25' });
  const long = { input: 10, output: 45, cache_read: 1, cache_write: 6.25, threshold: 272_000, whole_request: true };
  expect(priceCell({ ...price, long_context: long })).toEqual({
    text: '$5 / $25 / $0.50',
    tiered: true,
    title: 'Above 272k (whole request): $10 / $45 / $1\nCache write: $6.25',
  });
  expect(priceCell(null)).toEqual({ text: 'n/a', tiered: false, title: null });
});
