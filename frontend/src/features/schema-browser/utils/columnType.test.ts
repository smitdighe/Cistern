import { describe, expect, it } from 'vitest'

import { shortColumnType } from './columnType'

describe('shortColumnType', () => {
  it('uses the Postgres alias for long standard spellings', () => {
    expect(shortColumnType('timestamp with time zone')).toBe('timestamptz')
    expect(shortColumnType('timestamp without time zone')).toBe('timestamp')
    expect(shortColumnType('time with time zone')).toBe('timetz')
    expect(shortColumnType('time without time zone')).toBe('time')
    expect(shortColumnType('character varying')).toBe('varchar')
    expect(shortColumnType('character')).toBe('char')
    expect(shortColumnType('double precision')).toBe('float8')
    expect(shortColumnType('bit varying')).toBe('varbit')
  })

  it('leaves types that are already short alone', () => {
    expect(shortColumnType('integer')).toBe('integer')
    expect(shortColumnType('text')).toBe('text')
    expect(shortColumnType('numeric')).toBe('numeric')
  })

  it('passes unknown types through rather than guessing', () => {
    // information_schema reports arrays and enums by category, not by name.
    expect(shortColumnType('ARRAY')).toBe('ARRAY')
    expect(shortColumnType('USER-DEFINED')).toBe('USER-DEFINED')
  })
})
