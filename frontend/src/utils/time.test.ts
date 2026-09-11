import { describe, expect, test } from 'bun:test';
import { expiresSoon } from './time';

describe('expiresSoon', () => {
  const now = Date.parse('2026-09-11T00:00:00Z');

  test('flags expiries within two weeks', () => {
    expect(expiresSoon('2026-09-14T00:00:00Z', now)).toBe(true);
    expect(expiresSoon('2026-09-24T23:59:00Z', now)).toBe(true);
  });

  test('does not flag expiries further out', () => {
    expect(expiresSoon('2026-09-25T00:00:01Z', now)).toBe(false);
    expect(expiresSoon('2026-10-07T00:00:00Z', now)).toBe(false);
  });

  test('treats past expiries as soon', () => {
    expect(expiresSoon('2026-09-01T00:00:00Z', now)).toBe(true);
  });
});
