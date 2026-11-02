import { QueryClient } from '@tanstack/react-query';

/** Responses never expire, so the dashboard fetches only for a new key or an explicit refetch; failures are not retried. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,
      gcTime: Infinity,
      retry: false,
      retryOnMount: false,
      // A failed query has no data and counts as stale, so these would otherwise fetch it again.
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
    },
  },
});
