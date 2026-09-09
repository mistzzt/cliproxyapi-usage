import type { ProviderQuota } from '@/types/api';

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

export function beginReset(state: ResetActionState): ResetActionState {
  return state.status === 'loading' ? state : { status: 'confirming' };
}
