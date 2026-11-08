/**
 * How this app talks to the server when it is not the one asking.
 *
 * Split from `providers.tsx` so that file exports only components - a module mixing
 * the two breaks Fast Refresh, and the warning is right: a shared function changing
 * should not remount the tree.
 */

import { QueryClient } from '@tanstack/react-query'

import { ApiError } from './api/client'

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        /**
         * Salary data changes when someone changes it, not on a timer. Refetching
         * because a window regained focus is a request per tab switch for data that
         * is almost always identical.
         */
        refetchOnWindowFocus: false,
        staleTime: 30_000,
        retry: (failureCount, error) => {
          // Never retry a refusal. A 403 retried three times is three audit entries
          // and the same answer; a 401 retried is a redirect loop.
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
          return failureCount < 2
        },
      },
      mutations: { retry: false },
    },
  })
}
