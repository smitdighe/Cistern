import { describe, expect, it } from 'vitest'

import { flagSeverity } from './flagSeverity'

describe('flagSeverity', () => {
  it('treats a plain SELECT as unremarkable', () => {
    // The overwhelmingly common flag. Colouring it would make every successful
    // query look annotated.
    expect(flagSeverity('statement:select')).toBe('neutral')
  })

  it('treats any other statement type as blocking', () => {
    expect(flagSeverity('statement:insert')).toBe('blocked')
    expect(flagSeverity('statement:delete')).toBe('blocked')
    expect(flagSeverity('statement:drop')).toBe('blocked')
  })

  it('marks refusals as blocking', () => {
    expect(flagSeverity('requires_confirmation')).toBe('blocked')
    expect(flagSeverity('correction_aborted_forbidden')).toBe('blocked')
  })

  it('marks things done on the user’s behalf as adjusted', () => {
    expect(flagSeverity('limit_injected:500')).toBe('adjusted')
    expect(flagSeverity('empty_result_accepted')).toBe('adjusted')
    expect(flagSeverity('suspicious_result_accepted')).toBe('adjusted')
    expect(flagSeverity('correction_stalled')).toBe('adjusted')
    expect(flagSeverity('explanation_failed')).toBe('adjusted')
  })

  it('falls back to neutral for an unrecognised flag', () => {
    // Guessing a severity for a flag we have never seen would be worse than
    // showing it plainly.
    expect(flagSeverity('some_future_flag')).toBe('neutral')
  })
})
