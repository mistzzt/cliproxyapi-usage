import { expect, test } from 'bun:test';
import { tokens } from './totals';

test('tokens are input + output, cached excluded', () => {
  expect(tokens({ input_tokens: 300, output_tokens: 30, cached_tokens: 5000 })).toBe(330);
});
