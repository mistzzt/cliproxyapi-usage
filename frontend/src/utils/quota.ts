// Quota page helpers: provider labels and the manual reset control's state (pure).
import type { ProviderQuota, QuotaProvider } from '@/types/api';

/** Display names, in the order the Limits page lists providers. */
export const PROVIDER_LABEL: Record<QuotaProvider, string> = { claude: 'Claude', codex: 'Codex' };

export type ResetActionState =
  | { status: 'idle' }
  | { status: 'confirming' }
  | { status: 'loading' }
  | { status: 'success'; message: string }
  | { status: 'error'; message: string };

export function manualResetCount(quota: ProviderQuota | null | undefined): number | null {
  return quota?.manual_resets?.available_count ?? null;
}

export function canStartReset(quota: ProviderQuota | null | undefined): boolean {
  const count = manualResetCount(quota);
  return count !== null && count > 0;
}

/** The reset control's state, from the card's confirmation flag and the reset mutation; a running reset wins. */
export function resetState(
  confirming: boolean,
  mutation: { status: 'idle' | 'pending' | 'success' | 'error'; error: Error | null },
): ResetActionState {
  if (mutation.status === 'pending') return { status: 'loading' };
  if (confirming) return { status: 'confirming' };
  if (mutation.status === 'success') return { status: 'success', message: 'Manual reset consumed.' };
  if (mutation.status === 'error') return { status: 'error', message: mutation.error?.message ?? 'Reset failed.' };
  return { status: 'idle' };
}
