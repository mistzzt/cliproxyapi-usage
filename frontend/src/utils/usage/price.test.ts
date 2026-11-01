import { expect, test } from 'bun:test';
import { priceCell } from './price';

test('price cell lists input / output / cache read and flags tiered rates', () => {
  const price = { input: 5, output: 25, cache_read: 0.5, cache_write: null, input_above_200k: null, output_above_200k: null };
  expect(priceCell(price)).toEqual({ text: '$5 / $25 / $0.50', tiered: false, title: 'Cache write: n/a' });
  expect(priceCell({ ...price, output_above_200k: 37.5 }).tiered).toBe(true);
  expect(priceCell(null)).toEqual({ text: 'n/a', tiered: false, title: null });
});
