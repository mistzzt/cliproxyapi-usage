import { describe, expect, test } from 'bun:test';
import { renderToStaticMarkup } from 'react-dom/server';
import type { ProviderQuota, QuotaAccount } from '@/types/api';
import type { ResetActionState } from '@/stores/quotaResetState';
import { formatAbsolute } from '@/utils/time';
import QuotaCard from './QuotaCard';

const account: QuotaAccount = { provider: 'claude', auth_name: 'other.json', display_name: null };

function render(quota: ProviderQuota, reset: ResetActionState = { status: 'idle' }): string {
  return renderToStaticMarkup(
    <QuotaCard
      account={account}
      slot={{
        status: 'success',
        response: {
          quota,
          error: null,
          fetched_at: '2026-01-01T00:00:00Z',
          stale_at: '2026-01-01T00:01:00Z',
        },
      }}
      reset={reset}
      onRefresh={() => {}}
      onRequestReset={() => {}}
      onCancelReset={() => {}}
      onConfirmReset={() => {}}
    />,
  );
}

describe('QuotaCard manual reset capability', () => {
  test('renders count and action from a non-Codex response capability', () => {
    const markup = render({
      provider: 'claude', auth_name: 'other.json', plan_type: null, windows: [],
      manual_resets: { available_count: 2, credits: [], credits_error: null }, extra: {},
    });
    expect(markup).toContain('Manual resets: 2');
    expect(markup).toContain('Reset quota');
  });

  test('shows zero without an action', () => {
    const markup = render({
      provider: 'claude', auth_name: 'other.json', plan_type: null, windows: [],
      manual_resets: { available_count: 0, credits: [], credits_error: null }, extra: {},
    });
    expect(markup).toContain('Manual resets: 0');
    expect(markup).not.toContain('Reset quota');
    expect(render({
      provider: 'claude', auth_name: 'other.json', plan_type: null, windows: [],
      manual_resets: { available_count: 0, credits: [], credits_error: null }, extra: {},
    }, { status: 'confirming' })).not.toContain('Consume one manual reset?');
  });

  test('renders confirmation and account-local feedback states', () => {
    const quota: ProviderQuota = {
      provider: 'claude', auth_name: 'other.json', plan_type: null, windows: [],
      manual_resets: { available_count: 1, credits: [], credits_error: null }, extra: {},
    };
    expect(render(quota, { status: 'confirming' })).toContain('Consume one manual reset?');
    expect(render(quota, { status: 'loading' })).toContain('disabled');
    expect(render(quota, { status: 'error', message: 'No reset credit' })).toContain('No reset credit');
  });
});

describe('QuotaCard manual reset credits', () => {
  const base: ProviderQuota = {
    provider: 'codex', auth_name: 'codex.json', plan_type: 'pro', windows: [],
    manual_resets: { available_count: 2, credits: [], credits_error: null }, extra: {},
  };
  const credits = [
    { id: 'a', granted_at: '2026-04-01T00:00:00Z', expires_at: '2026-06-01T00:00:00Z' },
    { id: 'b', granted_at: null, expires_at: '2026-06-15T00:00:00Z' },
  ];

  test('renders one expiry row per credit and names the first in the confirmation', () => {
    const quota = { ...base, manual_resets: { available_count: 2, credits, credits_error: null } };
    const markup = render(quota, { status: 'confirming' });
    expect(markup).toContain('Reset 1');
    expect(markup).toContain('Reset 2');
    expect(markup).toContain(formatAbsolute('2026-06-01T00:00:00Z'));
    expect(markup).toContain(formatAbsolute('2026-06-15T00:00:00Z'));
    expect(markup).toMatch(/\((in .+|.+ ago|just now)\)/);
    expect(markup).toContain(
      `This spends the credit expiring ${formatAbsolute('2026-06-01T00:00:00Z')}.`,
    );
  });

  test('shows the error text when credits are unavailable', () => {
    const markup = render({
      ...base, manual_resets: { available_count: 2, credits: [], credits_error: 'HTTP 500' },
    });
    expect(markup).toContain('Manual resets: 2');
    expect(markup).toContain('Expiry unavailable: HTTP 500');
    expect(markup).not.toContain('Reset 1');
  });
});
