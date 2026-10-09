import { useMemo } from 'react'

import type { QueryResponse } from '../../../api/types/query.types'

/**
 * Flags that appear on a failed response without meaning "something blocked
 * this". Kept as an explicit set rather than a prefix or substring match so a
 * new backend flag defaults to being treated as a block — the safe direction.
 */
const NON_BLOCKING_FLAGS: ReadonlySet<string> = new Set(['correction_stalled'])

export type ClassifiedQueryResponse =
  | {
      type: 'success'
      sql: string
      result: Record<string, unknown>[]
      explanation: string
      attempts: number
      safetyFlags: string[]
    }
  | { type: 'clarifying'; question: string }
  | { type: 'safety_blocked'; error: string; safetyFlags: string[]; attempts: number }
  | { type: 'correction_exhausted'; error: string; attempts: number }

/**
 * Collapse a raw /query payload into the four states the UI actually renders.
 *
 * Pure and order-dependent — the rules below are evaluated top to bottom, and
 * the order is the specification, not an implementation detail.
 */
export function classifyQueryResponse(response: QueryResponse): ClassifiedQueryResponse {
  // (a) Ambiguity wins over everything. The backend sends `success: false`
  // alongside a clarifying question, so checking `success` first would
  // misreport "please clarify" as a failure.
  if (response.clarifying_question !== null) {
    return { type: 'clarifying', question: response.clarifying_question }
  }

  // (b) On success the contract guarantees sql/result/explanation are
  // populated, but they are nullable on the wire. Falling back keeps a
  // malformed-but-successful payload renderable instead of throwing.
  if (response.success) {
    return {
      type: 'success',
      sql: response.sql ?? '',
      result: response.result ?? [],
      explanation: response.explanation ?? '',
      attempts: response.attempts,
      safetyFlags: response.safety_flags,
    }
  }

  // (c) A failure carrying validator annotations was stopped by the safety
  // layer, not by a failed correction loop.
  //
  // DEVIATION from the literal spec rule ("safety_flags non-empty →
  // safety_blocked"), because the field is not what its name suggests.
  // backend/schemas/query.py calls it "Validator and pipeline annotations" —
  // it carries non-safety markers too, and successful responses routinely
  // arrive with `["statement:select", "limit_injected:500"]`.
  //
  // The concrete break: backend/orchestration/pipeline.py:419 sets
  // `safety_flags = ["correction_stalled"]` on the correction-exhausted path.
  // Taking "non-empty" literally would render a stalled correction as
  // "blocked by the safety layer" with a `correction_stalled` chip — wrong
  // state, wrong copy. Excluding that one marker is the whole fix; every other
  // flag on a failed response does come from a real block (validator
  // rejection, requires_confirmation, or correction_aborted_forbidden).
  if (response.safety_flags.some((flag) => !NON_BLOCKING_FLAGS.has(flag))) {
    return {
      type: 'safety_blocked',
      error: response.error ?? 'Query was blocked by the safety layer.',
      safetyFlags: response.safety_flags,
      // Carried because a block is not always the *first* thing that happened.
      // `correction_aborted_forbidden` is emitted after the correction loop has
      // run, with `attempts = 1 + correction.attempts`. Dropping the count made
      // every block render as a one-shot rejection.
      attempts: response.attempts,
    }
  }

  // (d) AMBIGUITY IN THE CURRENT BACKEND CONTRACT — worth raising with backend
  // if it shows up in practice. This branch is reached whenever a request
  // failed with no safety flags, regardless of `attempts`. When attempts equals
  // MAX_CORRECTION_ATTEMPTS the name is accurate. When attempts is 1 it is not:
  // the pipeline gave up after a single try, which is more likely a generation
  // or provider error than an exhausted correction loop. The response carries
  // nothing that distinguishes the two — no error code, no terminal-reason
  // field — so both collapse here. The fix belongs on the backend (a typed
  // failure reason), not in a heuristic on `attempts` here, which would be
  // guessing.
  return {
    type: 'correction_exhausted',
    error: response.error ?? 'Query failed after the correction limit was reached.',
    attempts: response.attempts,
  }
}

/** Memoized wrapper for component use. All logic lives in the pure function above. */
export function useQueryResponseParser(
  response: QueryResponse | undefined,
): ClassifiedQueryResponse | undefined {
  return useMemo(() => (response ? classifyQueryResponse(response) : undefined), [response])
}
