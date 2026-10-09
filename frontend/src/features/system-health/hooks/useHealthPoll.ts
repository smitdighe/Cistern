import { useQuery } from '@tanstack/react-query'

import { getHealth } from '../../../api/endpoints/health'

export const healthQueryKey = ['health'] as const

/**
 * Gap between polls.
 *
 * Thirty seconds, not fifteen. The backend memoises its probe result, so a
 * faster poll no longer buys fresher data — past the server's TTL it only adds
 * round trips that return the same bytes. This is the rate at which the strip
 * learns the backend is *reachable*, which is the part still measured per
 * request; dependency status itself is as fresh as the server's cache.
 */
const POLL_INTERVAL_MS = 30_000

/**
 * Poll dependency health.
 *
 * Background polling is off: nobody is looking at the strip in a hidden tab,
 * and on a serverless database even a cheap query is enough to stop the
 * compute from ever autosuspending.
 */
export function useHealthPoll() {
  return useQuery({
    queryKey: healthQueryKey,
    queryFn: getHealth,
    refetchInterval: POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
  })
}
