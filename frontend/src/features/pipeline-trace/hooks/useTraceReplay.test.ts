import { describe, expect, it } from 'vitest'

import { traceStagger } from './useTraceReplay'

const MAX_SEQUENCE_MS = 1800

describe('traceStagger', () => {
  it('uses the comfortable gap for short traces', () => {
    // A first-try success (4 steps) has room for the full stagger.
    expect(traceStagger(4)).toBe(240)
  })

  it('keeps the whole sequence under the ceiling however many steps there are', () => {
    // The invariant that matters: a multi-retry trace must not feel sluggish.
    // Tolerance is for float division only — 1800/24*24 lands on
    // 1800.0000000000002, which is not a real overshoot.
    for (const count of [1, 2, 4, 7, 11, 25, 100]) {
      const total = traceStagger(count) * Math.max(0, count - 1)
      expect(total).toBeLessThanOrEqual(MAX_SEQUENCE_MS + 1e-6)
    }
  })

  it('compresses the gap once the ceiling would be exceeded', () => {
    // 11 steps at the full 240ms would run 2.4s, so the gap has to shrink.
    expect(traceStagger(11)).toBe(MAX_SEQUENCE_MS / 10)
    expect(traceStagger(11)).toBeLessThan(240)
  })

  it('never returns a negative or zero gap for a single step', () => {
    expect(traceStagger(1)).toBeGreaterThan(0)
    expect(traceStagger(0)).toBeGreaterThan(0)
  })
})
