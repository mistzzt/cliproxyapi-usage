import { describe, expect, test } from 'bun:test';
import type { QuotaResponse } from '@/types/api';
import { beginReset, canStartReset, manualResetCount } from './quotaResetState';

describe('quota reset state', () => {
  test('shared backend fixture exposes the typed manual reset capability', async () => {
    const response: QuotaResponse = await Bun.file(
      '../tests/fixtures/quota-response-manual-resets.json',
    ).json();
    expect(manualResetCount(response.quota)).toBe(2);
    expect(canStartReset(response.quota)).toBe(true);
  });

  test('zero remains visible but is not actionable', () => {
    const quota = {
      provider: 'claude' as const,
      auth_name: 'test',
      plan_type: null,
      windows: [],
      manual_resets: { available_count: 0 },
      extra: {},
    };
    expect(manualResetCount(quota)).toBe(0);
    expect(canStartReset(quota)).toBe(false);
  });

  test('confirmation cannot replace a running action', () => {
    expect(beginReset({ status: 'idle' })).toEqual({ status: 'confirming' });
    expect(beginReset({ status: 'loading' })).toEqual({ status: 'loading' });
  });
});
