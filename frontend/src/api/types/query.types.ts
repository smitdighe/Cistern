/** Wire types for POST /query. Mirrors backend/schemas/query.py. */

/**
 * The pipeline's terminal state.
 *
 * The backend sends this on every response and treats it as the real
 * discriminator; `success` is documented as shorthand for
 * `status === "success"`. Typed here so the field is not silently dropped,
 * though classification keys off `clarifying_question`, `success` and
 * `safety_flags` — see `classifyQueryResponse`.
 */
export type QueryStatus = 'success' | 'ambiguous' | 'failed'

export interface QueryRequest {
  question: string
}

export interface QueryResponse {
  status: QueryStatus
  sql: string | null
  result: Record<string, unknown>[] | null
  explanation: string | null
  safety_flags: string[]
  attempts: number
  clarifying_question: string | null
  success: boolean
  error: string | null
}
