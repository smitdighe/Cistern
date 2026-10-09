import { apiClient } from '../client'
import type { QueryRequest, QueryResponse } from '../types/query.types'

/**
 * Ask a question. Always resolves with a payload on a reachable backend —
 * ambiguity and pipeline failure are described in the body, not as HTTP errors.
 */
export async function postQuery(request: QueryRequest): Promise<QueryResponse> {
  const { data } = await apiClient.post<QueryResponse>('/query', request)
  return data
}
