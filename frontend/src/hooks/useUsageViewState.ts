import { useCallback, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router';
import { loadViewState, saveViewState, serializeViewState, type ViewState } from '@/utils/usage/viewState';

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Dashboard view state backed by the URL query string; an empty query restores the last saved state. */
export function useUsageViewState(): { state: ViewState; update: (patch: Partial<ViewState>) => void } {
  const [params, setParams] = useSearchParams();
  const search = params.toString();
  const state = useMemo(() => loadViewState(search, storage()), [search]);

  useEffect(() => {
    saveViewState(state, storage());
    if (search === '') setParams(serializeViewState(state), { replace: true });
  }, [state, search, setParams]);

  const update = useCallback(
    (patch: Partial<ViewState>) => setParams(serializeViewState({ ...state, ...patch }), { replace: true }),
    [state, setParams],
  );
  return { state, update };
}
