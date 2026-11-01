import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { loadViewState, saveViewState, serializeViewState, type ViewState } from '@/utils/usage/viewState';

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Dashboard view state backed by the URL query string; an empty query restores the last saved state.
 * `now` anchors rolling ranges and only advances on a range change or `refresh`, so renders don't refetch.
 */
export function useUsageViewState(): {
  state: ViewState;
  update: (patch: Partial<ViewState>) => void;
  now: number;
  /** Bumped by `refresh`; add to fetch deps so fixed ranges refetch too. */
  refreshToken: number;
  refresh: () => void;
} {
  const [params, setParams] = useSearchParams();
  const search = params.toString();
  const state = useMemo(() => loadViewState(search, storage()), [search]);
  const [clock, setClock] = useState(() => ({ now: Date.now(), refreshToken: 0 }));

  useEffect(() => {
    saveViewState(state, storage());
    if (search === '') setParams(serializeViewState(state), { replace: true });
  }, [state, search, setParams]);

  const update = useCallback(
    (patch: Partial<ViewState>) => {
      if (patch.range) setClock((c) => ({ ...c, now: Date.now() }));
      setParams(serializeViewState({ ...state, ...patch }), { replace: true });
    },
    [state, setParams],
  );
  const refresh = useCallback(() => setClock((c) => ({ now: Date.now(), refreshToken: c.refreshToken + 1 })), []);
  return { state, update, now: clock.now, refreshToken: clock.refreshToken, refresh };
}
