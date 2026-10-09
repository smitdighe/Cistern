import { useQuery } from '@tanstack/react-query'

import { getSchema } from '../../../api/endpoints/schema'

export const schemaQueryKey = ['schema'] as const

/**
 * The schema is introspected once at backend startup and cached there, so it
 * cannot change mid-session without a migration and a `?refresh=true`. Never
 * stale, never refetched on its own.
 */
export function useSchema() {
  return useQuery({
    queryKey: schemaQueryKey,
    queryFn: getSchema,
    staleTime: Infinity,
  })
}
