import type { ManualResetCredit, ProviderQuota } from '@/types/api';

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

/** The credit a reset consumes: the one that expires soonest. */
export function nextExpiringCredit(
  quota: ProviderQuota | null | undefined,
): ManualResetCredit | null {
  const credits = quota?.manual_resets?.credits ?? [];
  let earliest: ManualResetCredit | null = null;
  for (const credit of credits) {
    if (earliest === null || Date.parse(credit.expires_at) < Date.parse(earliest.expires_at)) {
      earliest = credit;
    }
  }
  return earliest;
}

export function beginReset(state: ResetActionState): ResetActionState {
  return state.status === 'loading' ? state : { status: 'confirming' };
}
