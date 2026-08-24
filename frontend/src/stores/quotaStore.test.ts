import { afterEach, describe, expect, test } from 'bun:test';
import { useQuotaStore } from './quotaStore';

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  useQuotaStore.setState({ accounts: { status: 'idle' }, slots: {}, resets: {} });
});

describe('quota reset actions', () => {
  test('requires confirmation and deduplicates submissions', async () => {
    const key = { provider: 'codex' as const, authName: 'codex.json' };
    useQuotaStore.setState({
      slots: {
        'codex/codex.json': {
          status: 'success',
          response: {
            quota: {
              provider: 'codex',
              auth_name: 'codex.json',
              plan_type: 'pro',
              windows: [],
              manual_resets: { available_count: 1 },
              extra: {},
            },
            error: null,
            fetched_at: '2026-01-01T00:00:00Z',
            stale_at: '2026-01-01T00:01:00Z',
          },
        },
      },
    });
    let releaseReset: (() => void) | undefined;
    const resetDone = new Promise<void>((resolve) => { releaseReset = resolve; });
    let postCount = 0;
    globalThis.fetch = (async (_input, init) => {
      if (init?.method === 'POST') {
        postCount += 1;
        await resetDone;
        return new Response(null, { status: 204 });
      }
      return new Response(JSON.stringify({
        quota: null,
        error: { kind: 'upstream', message: 'refresh failed', upstream_status: 502 },
        fetched_at: '2026-01-01T00:00:00Z',
        stale_at: '2026-01-01T00:01:00Z',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;

    await useQuotaStore.getState().submitReset(key);
    expect(postCount).toBe(0);
    useQuotaStore.getState().requestResetConfirmation(key);
    const first = useQuotaStore.getState().submitReset(key);
    const duplicate = useQuotaStore.getState().submitReset(key);
    expect(first).toBe(duplicate);
    expect(useQuotaStore.getState().resets['codex/codex.json']?.status).toBe('loading');
    releaseReset?.();
    await first;
    expect(postCount).toBe(1);
    expect(useQuotaStore.getState().resets['codex/codex.json']?.status).toBe('success');
  });

  test('failure preserves the prior quota and records account-local feedback', async () => {
    const key = { provider: 'codex' as const, authName: 'codex.json' };
    const response = {
      quota: {
        provider: 'codex' as const,
        auth_name: 'codex.json',
        plan_type: 'pro',
        windows: [],
        manual_resets: { available_count: 1 },
        extra: {},
      },
      error: null,
      fetched_at: '2026-01-01T00:00:00Z',
      stale_at: '2026-01-01T00:01:00Z',
    };
    useQuotaStore.setState({
      slots: { 'codex/codex.json': { status: 'success', response } },
    });
    globalThis.fetch = (() => Promise.resolve(
      new Response('consume failed', { status: 502 }),
    )) as unknown as typeof fetch;
    useQuotaStore.getState().requestResetConfirmation(key);
    await useQuotaStore.getState().submitReset(key);
    expect(useQuotaStore.getState().slots['codex/codex.json']?.response).toBe(response);
    expect(useQuotaStore.getState().resets['codex/codex.json']?.status).toBe('error');
  });

  test('an older refresh cannot overwrite the post-reset quota', async () => {
    const key = { provider: 'codex' as const, authName: 'codex.json' };
    let releaseOld: ((response: Response) => void) | undefined;
    const oldResponse = new Promise<Response>((resolve) => { releaseOld = resolve; });
    let getCount = 0;
    const quotaResponse = (usedPercent: number) => ({
      quota: {
        provider: 'codex' as const,
        auth_name: 'codex.json',
        plan_type: 'pro',
        windows: [{ id: 'weekly', label: 'Weekly limit', used_percent: usedPercent, resets_at: null }],
        manual_resets: { available_count: 1 },
        extra: {},
      },
      error: null,
      fetched_at: '2026-01-01T00:00:00Z',
      stale_at: '2026-01-01T00:01:00Z',
    });
    useQuotaStore.setState({
      slots: { 'codex/codex.json': { status: 'success', response: quotaResponse(80) } },
    });
    globalThis.fetch = (async (_input, init) => {
      if (init?.method === 'POST') return new Response(null, { status: 204 });
      getCount += 1;
      if (getCount === 1) return oldResponse;
      return new Response(JSON.stringify(quotaResponse(0)), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;

    const oldLoad = useQuotaStore.getState().loadQuota(key);
    useQuotaStore.getState().requestResetConfirmation(key);
    await useQuotaStore.getState().submitReset(key);
    releaseOld?.(new Response(JSON.stringify(quotaResponse(80)), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    await oldLoad;

    const window = useQuotaStore.getState().slots['codex/codex.json']?.response?.quota?.windows[0];
    expect(window?.used_percent).toBe(0);
    expect(getCount).toBe(2);
  });

  test('a refresh to zero resets cancels pending confirmation', async () => {
    const key = { provider: 'codex' as const, authName: 'codex.json' };
    const response = {
      quota: {
        provider: 'codex' as const,
        auth_name: 'codex.json',
        plan_type: 'pro',
        windows: [],
        manual_resets: { available_count: 1 },
        extra: {},
      },
      error: null,
      fetched_at: '2026-01-01T00:00:00Z',
      stale_at: '2026-01-01T00:01:00Z',
    };
    useQuotaStore.setState({
      slots: { 'codex/codex.json': { status: 'success', response } },
    });
    globalThis.fetch = (() => Promise.resolve(new Response(JSON.stringify({
      ...response,
      quota: { ...response.quota, manual_resets: { available_count: 0 } },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }))) as unknown as typeof fetch;

    useQuotaStore.getState().requestResetConfirmation(key);
    await useQuotaStore.getState().loadQuota(key);
    expect(useQuotaStore.getState().resets['codex/codex.json']?.status).toBe('idle');
  });
});
