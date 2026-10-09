import { describe, expect, it } from 'vitest'

import type { ClassifiedQueryResponse } from '../../query-console/hooks/useQueryResponseParser'
import { buildTraceSteps } from './buildTraceSteps'

function success(attempts: number): ClassifiedQueryResponse {
  return {
    type: 'success',
    sql: 'SELECT COUNT(*) FROM orders LIMIT 500',
    result: [{ count: 7 }],
    explanation: 'Counts orders.',
    attempts,
    safetyFlags: ['statement:select', 'limit_injected:500'],
  }
}

describe('buildTraceSteps', () => {
  it('renders no retry loop for a first-try success', () => {
    const steps = buildTraceSteps(success(1))

    expect(steps.map((step) => step.id)).toEqual([
      'generate',
      'safety',
      'execute-final',
      'explain',
    ])
    // Silence is the signal: no skipped or empty retry placeholder.
    expect(steps.some((step) => step.loopBack)).toBe(false)
    expect(steps.some((step) => step.status === 'failed')).toBe(false)
  })

  it('inserts one correction round per extra attempt on a corrected success', () => {
    const steps = buildTraceSteps(success(3))

    expect(steps.map((step) => step.id)).toEqual([
      'generate',
      'safety',
      'execute-1',
      'correction-1',
      'correction-2',
      'execute-final',
      'explain',
    ])
    expect(steps.filter((step) => step.loopBack)).toHaveLength(2)
    expect(steps.find((step) => step.id === 'execute-1')?.status).toBe('failed')
  })

  it('labels correction rounds by position and total, without inventing SQL', () => {
    const steps = buildTraceSteps(success(3))
    const corrections = steps.filter((step) => step.source === 'cerebras' && step.loopBack)

    expect(corrections.map((step) => step.label)).toEqual([
      'Correction attempt 2 of 3',
      'Correction attempt 3 of 3',
    ])
    // The critical guarantee of this phase.
    expect(corrections.every((step) => step.sql === null)).toBe(true)
  })

  it('attaches the final SQL only to the step that actually ran it', () => {
    const steps = buildTraceSteps(success(2))
    const withSql = steps.filter((step) => step.sql !== null)

    expect(withSql).toHaveLength(1)
    expect(withSql[0].id).toBe('execute-final')
    expect(withSql[0].sql).toBe('SELECT COUNT(*) FROM orders LIMIT 500')
  })

  it('short-circuits a first-attempt safety block after the check', () => {
    const steps = buildTraceSteps({
      type: 'safety_blocked',
      error: 'statement requires explicit confirmation and was not executed',
      safetyFlags: ['statement:insert', 'requires_confirmation'],
      attempts: 1,
    })

    expect(steps.map((step) => step.id)).toEqual(['generate', 'safety-blocked'])
    expect(steps[1].status).toBe('blocked')
    expect(steps.some((step) => step.source === 'database')).toBe(false)
    expect(steps.some((step) => step.id === 'explain')).toBe(false)
  })

  it('shows the correction rounds that ran before a late safety block', () => {
    // `correction_aborted_forbidden` is emitted after the loop has run — the
    // corrector, not the generator, wrote the forbidden statement. Rendering
    // this as a two-step trace claimed the pipeline stopped immediately.
    const steps = buildTraceSteps({
      type: 'safety_blocked',
      error: 'correction aborted: DROP is permanently forbidden',
      safetyFlags: ['correction_aborted_forbidden'],
      attempts: 3,
    })

    expect(steps.map((step) => step.id)).toEqual([
      'generate',
      'safety',
      'execute-1',
      'correction-1',
      'correction-2',
      'safety-blocked',
    ])
    expect(steps.at(-1)?.status).toBe('blocked')
    expect(steps.at(-1)?.loopBack).toBe(true)
    expect(steps.some((step) => step.id === 'explain')).toBe(false)
  })

  it('never repeats the flag list in a blocked step detail', () => {
    // The trace lists flags once, in its own Flags section. Echoing them in the
    // step's secondary line put the same chips on screen twice.
    const steps = buildTraceSteps({
      type: 'safety_blocked',
      error: 'statement type DELETE is not permitted',
      safetyFlags: ['statement:delete'],
      attempts: 1,
    })

    expect(steps.at(-1)?.detail).not.toContain('statement:delete')
  })

  it('ends an exhausted correction at Exhausted, with no explain step', () => {
    const steps = buildTraceSteps({
      type: 'correction_exhausted',
      error: 'column "revenue" does not exist',
      attempts: 3,
    })

    expect(steps.map((step) => step.id)).toEqual([
      'generate',
      'safety',
      'execute-1',
      'correction-1',
      'correction-2',
      'exhausted',
    ])
    expect(steps.some((step) => step.id === 'explain')).toBe(false)
    expect(steps.at(-1)?.status).toBe('failed')
    expect(steps.at(-1)?.error).toBe('column "revenue" does not exist')
  })

  it('handles an exhausted run that never reached the correction loop', () => {
    const steps = buildTraceSteps({
      type: 'correction_exhausted',
      error: 'provider returned no SQL',
      attempts: 1,
    })

    expect(steps.map((step) => step.id)).toEqual([
      'generate',
      'safety',
      'execute-1',
      'exhausted',
    ])
    expect(steps.at(-1)?.detail).toBe('No working query after 1 attempt')
  })

  it('never produces a negative-length loop from a malformed attempts count', () => {
    const steps = buildTraceSteps(success(0))

    expect(steps.map((step) => step.id)).toEqual([
      'generate',
      'safety',
      'execute-final',
      'explain',
    ])
  })

  it('returns no trace for a clarifying question', () => {
    // Nothing ran, so there is no pipeline history to replay.
    expect(buildTraceSteps({ type: 'clarifying', question: 'Which customer?' })).toEqual([])
  })

  it('assigns every step a unique id', () => {
    const steps = buildTraceSteps(success(4))
    expect(new Set(steps.map((step) => step.id)).size).toBe(steps.length)
  })
})
