import { expect, test } from 'bun:test';
import { cacheHitRate, tokens } from './totals';

test('tokens are input + output, cached excluded', () => {
  const totals = { input_tokens: 300, output_tokens: 30, cached_tokens: 5000 };
  expect(tokens(totals)).toBe(330);
});

test('cache hit rate follows the model accounting the API reports', () => {
  const t = { input_tokens: 800, cached_tokens: 200 };
  expect(cacheHitRate(t, true)).toBe(0.25);
  expect(cacheHitRate(t, false)).toBe(0.2);
  expect(cacheHitRate({ input_tokens: 0, cached_tokens: 0 }, false)).toBeNull();
});
