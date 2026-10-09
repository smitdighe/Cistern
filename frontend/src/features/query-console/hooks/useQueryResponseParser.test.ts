import { describe, expect, it } from 'vitest'

import type { QueryResponse } from '../../../api/types/query.types'
import { classifyQueryResponse } from './useQueryResponseParser'

/** A well-formed failed response; each test overrides only what it is about. */
function response(overrides: Partial<QueryResponse> = {}): QueryResponse {
  return {
    status: 'failed',
    sql: null,
    result: null,
    explanation: null,
    safety_flags: [],
    attempts: 0,
    clarifying_question: null,
    success: false,
    error: null,
    ...overrides,
  }
}

describe('classifyQueryResponse', () => {
  it('classifies a populated success payload', () => {
    const classified = classifyQueryResponse(
      response({
        status: 'success',
        success: true,
        sql: 'SELECT COUNT(id) FROM customers LIMIT 500',
        result: [{ count: 7 }],
        explanation: 'Counts the rows in the customers table.',
        safety_flags: ['statement:select', 'limit_injected:500'],
        attempts: 1,
      }),
    )

    expect(classified).toEqual({
      type: 'success',
      sql: 'SELECT COUNT(id) FROM customers LIMIT 500',
      result: [{ count: 7 }],
      explanation: 'Counts the rows in the customers table.',
      attempts: 1,
      safetyFlags: ['statement:select', 'limit_injected:500'],
    })
  })

  it('classifies a clarifying question', () => {
    const classified = classifyQueryResponse(
      response({
        status: 'ambiguous',
        clarifying_question: 'Did you mean orders placed or orders shipped?',
      }),
    )

    expect(classified).toEqual({
      type: 'clarifying',
      question: 'Did you mean orders placed or orders shipped?',
    })
  })

  it('prefers clarifying over success when both are present', () => {
    // Rule (a) runs before rule (b). The backend sends success: false with a
    // clarifying question, but pinning the precedence keeps a future backend
    // change from silently reclassifying ambiguity as a result.
    const classified = classifyQueryResponse(
      response({
        success: true,
        sql: 'SELECT 1',
        clarifying_question: 'Which customer did you mean?',
      }),
    )

    expect(classified.type).toBe('clarifying')
  })

  it('classifies a failure carrying safety flags as blocked', () => {
    const classified = classifyQueryResponse(
      response({
        safety_flags: ['statement:delete'],
        error: 'DELETE is not permitted.',
        attempts: 1,
      }),
    )

    expect(classified).toEqual({
      type: 'safety_blocked',
      error: 'DELETE is not permitted.',
      safetyFlags: ['statement:delete'],
      attempts: 1,
    })
  })

  it('classifies a failure with no safety flags as correction exhausted', () => {
    const classified = classifyQueryResponse(
      response({
        error: 'Correction limit reached.',
        attempts: 3,
      }),
    )

    expect(classified).toEqual({
      type: 'correction_exhausted',
      error: 'Correction limit reached.',
      attempts: 3,
    })
  })

  it('still classifies as correction exhausted when attempts is 1, not the max', () => {
    // The documented ambiguity: a single-attempt failure is almost certainly a
    // generation or provider error, not an exhausted correction loop, but the
    // payload carries nothing that separates the two. Asserted so the behaviour
    // is deliberate and the test fails loudly if the backend adds a reason code
    // and this stops being the right answer.
    const classified = classifyQueryResponse(
      response({
        error: 'Provider returned no SQL.',
        attempts: 1,
      }),
    )

    expect(classified).toEqual({
      type: 'correction_exhausted',
      error: 'Provider returned no SQL.',
      attempts: 1,
    })
  })

  it('classifies a stalled correction as exhausted, not blocked', () => {
    // backend/orchestration/pipeline.py:419 emits exactly this on the
    // correction-exhausted path. Reading "safety_flags non-empty" literally
    // would call it a safety block and show the wrong card.
    const classified = classifyQueryResponse(
      response({
        safety_flags: ['correction_stalled'],
        error: 'corrector produced no improvement',
        attempts: 3,
      }),
    )

    expect(classified).toEqual({
      type: 'correction_exhausted',
      error: 'corrector produced no improvement',
      attempts: 3,
    })
  })

  it('still blocks when a real flag accompanies a non-blocking marker', () => {
    const classified = classifyQueryResponse(
      response({
        safety_flags: ['correction_stalled', 'correction_aborted_forbidden'],
        error: 'correction aborted: DROP is permanently forbidden',
        attempts: 2,
      }),
    )

    expect(classified.type).toBe('safety_blocked')
    // The count survives classification: this block came out of the correction
    // loop, and a trace that renders it as attempt 1 is telling a lie.
    expect(classified).toMatchObject({ attempts: 2 })
  })

  it('treats an unrecognised flag as a block', () => {
    // Unknown must not mean safe — a flag the frontend has never seen defaults
    // to blocking so a new backend rejection reason cannot slip through as a
    // generic failure.
    expect(classifyQueryResponse(response({ safety_flags: ['some_future_flag'] })).type).toBe(
      'safety_blocked',
    )
  })

  it('substitutes fallback text when a failure carries no error string', () => {
    expect(classifyQueryResponse(response({ safety_flags: ['x'] })).type).toBe('safety_blocked')
    expect(classifyQueryResponse(response())).toMatchObject({
      type: 'correction_exhausted',
      error: expect.stringContaining('correction limit'),
    })
  })

  it('tolerates a success payload with null sql, result and explanation', () => {
    const classified = classifyQueryResponse(response({ success: true, attempts: 1 }))

    expect(classified).toEqual({
      type: 'success',
      sql: '',
      result: [],
      explanation: '',
      attempts: 1,
      safetyFlags: [],
    })
  })

  it('treats an empty-string clarifying question as ambiguity, not a failure', () => {
    // Non-null is the rule, not truthiness — "" is still the backend saying it
    // asked rather than failed.
    expect(classifyQueryResponse(response({ clarifying_question: '' })).type).toBe('clarifying')
  })
})
