import { describe, expect, test } from 'bun:test';
import { formatAbsolute } from './time';

describe('formatAbsolute', () => {
  test('renders a local date and time with the year', () => {
    const label = formatAbsolute('2026-05-01T12:34:00Z');
    expect(label).toContain('2026');
    expect(label).toMatch(/\d{1,2}:\d{2}/);
  });
});
