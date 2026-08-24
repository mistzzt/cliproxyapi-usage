import { afterEach, describe, expect, test } from 'bun:test';
import { resetQuota } from './quotaApi';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe('resetQuota', () => {
  test('uses provider and encoded account in a POST request', async () => {
    let request: {
      input: Parameters<typeof fetch>[0];
      init: Parameters<typeof fetch>[1];
    } | undefined;
    globalThis.fetch = ((input, init) => {
      request = { input, init };
      return Promise.resolve(new Response(null, { status: 204 }));
    }) as typeof fetch;
    await resetQuota('claude', 'team account.json');
    expect(String(request?.input)).toBe('/api/quota/claude/team%20account.json/reset');
    expect(request?.init?.method).toBe('POST');
  });
});
