import { useMutation } from '@tanstack/react-query'

import { postQuery } from '../../../api/endpoints/query'
import type { QueryRequest, QueryResponse } from '../../../api/types/query.types'

/**
 * Submit a question. Deliberately dumb — it returns the raw payload and does
 * no interpretation, so classification stays in one place
 * (`classifyQueryResponse`) rather than being split across a hook and a caller.
 */
export function useSubmitQuery() {
  return useMutation<QueryResponse, Error, QueryRequest>({
    mutationFn: postQuery,
  })
}
