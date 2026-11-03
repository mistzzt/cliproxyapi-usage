import { keepPreviousData, queryOptions, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { getOptions, getUsage } from '@/services/api';
import type { OptionsResponse } from '@/types/api';
import {
  computeWindow,
  OPTIONS_KEY,
  windowKey,
  windowPrefix,
  type ResolvedWindow,
  type UsageEndpoint,
  type UsageQueryKey,
  type UsageResponses,
} from '@/utils/usage/queryKeys';
import type { RangeSel } from '@/utils/usage/range';

function windowQuery(range: RangeSel) {
  const key = windowKey(range);
  const compute = () => computeWindow(key, Date.now());
  return queryOptions({
    queryKey: key,
    queryFn: compute,
    // Computed when the key is first used, so the render that picks a range already has its window.
    initialData: compute,
    // Backstop for entries built by a render that never commits; committed ones are removed below.
    gcTime: 10_000,
  });
}

/** The picked range's window, and Refresh: a fresh window, with every shown request and the options fetched again. */
export function useUsageWindow(range: RangeSel): { win: ResolvedWindow; refresh: () => Promise<void> } {
  const qc = useQueryClient();
  const query = useMemo(() => windowQuery(range), [range]);
  const { data: win } = useQuery(query);
  // Drops the window once nothing shows it, so picking the range again or a later visit computes a fresh one.
  // Not a short `gcTime`: that timer starts when render builds the entry and can fire before the render commits.
  useEffect(() => {
    const cache = qc.getQueryCache();
    return () =>
      queueMicrotask(() => {
        const entry = cache.find({ queryKey: query.queryKey, exact: true });
        if (entry && entry.getObserversCount() === 0) cache.remove(entry);
      });
  }, [qc, query]);
  const refresh = useCallback(async () => {
    const options = qc.refetchQueries({ queryKey: OPTIONS_KEY });
    await qc.refetchQueries({ queryKey: query.queryKey, exact: true });
    const next = qc.getQueryData(query.queryKey);
    // A new window means new keys, which fetch on render. In an unchanged window, shown requests refetch now
    // and hidden ones (another tab, a closed drawer) when next shown.
    if (next) await qc.invalidateQueries({ queryKey: windowPrefix(next), refetchType: 'active' }, { cancelRefetch: false });
    await options;
  }, [qc, query]);
  return { win, refresh };
}

/** One usage request; while a new key loads, the previous key's data stays as a placeholder. */
export function useUsage<E extends UsageEndpoint>(key: UsageQueryKey<E>): UseQueryResult<UsageResponses[E]> {
  return useQuery({ queryKey: key, queryFn: ({ signal }) => getUsage(key, signal), placeholderData: keepPreviousData });
}

export function useOptions(): UseQueryResult<OptionsResponse> {
  return useQuery({ queryKey: OPTIONS_KEY, queryFn: ({ signal }) => getOptions(signal) });
}
