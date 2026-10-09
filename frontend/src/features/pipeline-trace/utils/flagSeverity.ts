export type FlagSeverity = 'blocked' | 'adjusted' | 'neutral'

/**
 * Classify a backend safety flag by what it means for the user.
 *
 * Derived from the flags the pipeline actually emits — `statement:*` from the
 * validator, `limit_injected:*` and the `*_accepted` markers from the executor,
 * and the correction outcomes. Unrecognised flags fall through to neutral
 * rather than guessing at a severity they might not have.
 *
 * Lives beside the chip rather than inside it so the component file exports
 * only a component — a module that mixes the two breaks fast refresh.
 */
export function flagSeverity(flag: string): FlagSeverity {
  if (flag === 'statement:select') return 'neutral'
  if (flag.startsWith('statement:')) return 'blocked'
  if (flag === 'requires_confirmation' || flag === 'correction_aborted_forbidden') return 'blocked'
  if (flag.startsWith('limit_injected')) return 'adjusted'
  if (flag.endsWith('_accepted')) return 'adjusted'
  if (flag === 'correction_stalled' || flag === 'explanation_failed') return 'adjusted'
  return 'neutral'
}
