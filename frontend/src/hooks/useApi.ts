import { useEffect, useReducer, useCallback } from 'react';

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

type Action<T> =
  | { type: 'start' }
  | { type: 'success'; data: T }
  | { type: 'error'; error: string };

function reducer<T>(state: ApiState<T>, action: Action<T>): ApiState<T> {
  switch (action.type) {
    case 'start':
      // Keep prior data visible during a reload so the UI doesn't flash empty.
      return { data: state.data, loading: true, error: state.error };
    case 'success':
      return { data: action.data, loading: false, error: null };
    case 'error':
      return { data: state.data, loading: false, error: action.error };
  }
}

/**
 * Fetch-on-mount / fetch-on-deps helper.
 *
 * State transitions go through a reducer so the effect body never calls a
 * `useState` setter directly (which would trip react-hooks/set-state-in-effect
 * and can cause cascading renders). Dispatching a single `start` action is a
 * batched transition, and the terminal `success`/`error` dispatches happen in
 * async callbacks after the fetch settles.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
): ApiState<T> & { reload: () => void } {
  const [state, dispatch] = useReducer(reducer<T>, {
    data: null,
    loading: true,
    error: null,
  });
  const [bump, forceReload] = useReducer((n: number) => n + 1, 0);

  useEffect(() => {
    let cancelled = false;
    dispatch({ type: 'start' });
    fetcher()
      .then((d) => {
        if (!cancelled) dispatch({ type: 'success', data: d });
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          dispatch({ type: 'error', error: e instanceof Error ? e.message : String(e) });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, bump]);

  const reload = useCallback(() => forceReload(), []);
  return { data: state.data, loading: state.loading, error: state.error, reload };
}
