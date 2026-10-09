import type { ClassifiedQueryResponse } from '../../query-console/hooks/useQueryResponseParser'

/**
 * Which system performed a step. Drives the icon, so the reader can tell the
 * two model tiers apart without reading labels.
 */
export type TraceSource = 'groq' | 'cerebras' | 'safety' | 'database' | 'system'

export type TraceStatus = 'success' | 'blocked' | 'retry' | 'failed'

export interface TraceStep {
  /** Stable within one response; used as the React key and replay index. */
  id: string
  label: string
  /** Secondary line. Kept generic for retries — see the note on fabrication below. */
  detail: string | null
  source: TraceSource
  status: TraceStatus
  /** 1-based pipeline attempt this step belongs to. */
  attempt: number
  /** Draw the incoming connector as a loop-back curve rather than a straight line. */
  loopBack: boolean

  /*
   * ── Why these two are almost always null ──────────────────────────────────
   *
   * /query returns only the FINAL sql, a numeric `attempts`, and a flat
   * `safety_flags` array. There is no per-attempt SQL and no step log, so
   * every intermediate step genuinely has nothing to show here. They are
   * populated only where the response actually carries the value: `sql` on the
   * executing step of a successful run, `error` on a terminal failure.
   *
   * Filling these with plausible-looking SQL to make the trace richer would be
   * inventing pipeline history that never came back from the server — the one
   * thing this component must not do.
   *
   * A real SQLDiffView (attempt N-1 vs attempt N) becomes possible the moment
   * the backend surfaces its attempt trail. That trail already exists and is
   * already populated per attempt with SQL and error text — see
   * backend/correction/loop.py:15 ("The trail records every attempt") — it is
   * simply withheld at the wire boundary on purpose:
   * backend/routes/query.py:20 documents `attempt_trail` as "deliberately not
   * exposed" because it carries raw provider errors and rejected SQL.
   *
   * So the backend change is a scoping decision, not new plumbing: expose a
   * redacted `attempt_history: { sql, error }[]`. When it lands, populate
   * `sql`/`error` per step here and a diff view can read them straight off
   * TraceStep — no restructuring of this shape required.
   */
  sql: string | null
  error: string | null
}

const GENERATE_LABEL = 'Generate SQL'
const SAFETY_LABEL = 'Safety check'
const EXECUTE_LABEL = 'Execute'
const EXPLAIN_LABEL = 'Explain'

/**
 * Correction rounds, given the total attempt count.
 *
 * Attempt 1 is generation, so the loop accounts for the remainder. Clamped at
 * zero so a malformed `attempts` of 0 cannot produce a negative-length loop.
 */
function correctionRounds(attempts: number): number {
  return Math.max(0, attempts - 1)
}

function correctionStep(round: number, attempts: number, status: TraceStatus): TraceStep {
  return {
    id: `correction-${round}`,
    label: `Correction attempt ${round + 1} of ${attempts}`,
    // Deliberately generic. The corrector's SQL for this round was not
    // returned, and naming it would imply we know what it produced.
    detail: 'Repaired SQL not returned by the backend',
    source: 'cerebras',
    status,
    attempt: round + 1,
    loopBack: true,
    sql: null,
    error: null,
  }
}

/**
 * Map a classified response onto an ordered trace.
 *
 * Pure. Returns an empty array for states with no pipeline history to show —
 * a clarifying question is the system asking before it ran anything.
 */
export function buildTraceSteps(classified: ClassifiedQueryResponse): TraceStep[] {
  if (classified.type === 'clarifying') {
    return []
  }

  const steps: TraceStep[] = [
    {
      id: 'generate',
      label: GENERATE_LABEL,
      detail: 'Groq',
      source: 'groq',
      status: 'success',
      attempt: 1,
      loopBack: false,
      sql: null,
      error: null,
    },
  ]

  if (classified.type === 'safety_blocked') {
    // A block is not always the first thing that happened. The validator can
    // reject the generator's very first statement (attempts === 1), but it can
    // also reject something the *corrector* wrote several rounds in —
    // `correction_aborted_forbidden` arrives with attempts > 1. Rendering both
    // as a two-step trace claimed the pipeline stopped immediately when it had
    // in fact retried. The same inference the success path already makes
    // applies here: attempts > 1 is itself the evidence that the first
    // statement passed safety, ran, and failed.
    const rounds = correctionRounds(classified.attempts)

    if (rounds > 0) {
      steps.push({
        id: 'safety',
        label: SAFETY_LABEL,
        detail: 'Statement validator',
        source: 'safety',
        status: 'success',
        attempt: 1,
        loopBack: false,
        sql: null,
        error: null,
      })
      steps.push({
        id: 'execute-1',
        label: `${EXECUTE_LABEL}: failed`,
        detail: 'Postgres, read-only role',
        source: 'database',
        status: 'failed',
        attempt: 1,
        loopBack: false,
        sql: null,
        error: null,
      })
      for (let round = 1; round <= rounds; round++) {
        steps.push(correctionStep(round, classified.attempts, 'failed'))
      }
    }

    // Nothing executed after the block, so no execute or explain step exists to
    // render. Showing them greyed out would imply they were reached. The flags
    // themselves are not repeated in `detail` — the trace lists them once, in
    // its own Flags section directly below.
    steps.push({
      id: 'safety-blocked',
      label: `${SAFETY_LABEL}: blocked`,
      detail: 'Statement validator',
      source: 'safety',
      status: 'blocked',
      attempt: Math.max(1, classified.attempts),
      loopBack: rounds > 0,
      sql: null,
      error: classified.error,
    })
    return steps
  }

  steps.push({
    id: 'safety',
    label: SAFETY_LABEL,
    detail: 'Statement validator',
    source: 'safety',
    status: 'success',
    attempt: 1,
    loopBack: false,
    sql: null,
    error: null,
  })

  if (classified.type === 'correction_exhausted') {
    const rounds = correctionRounds(classified.attempts)

    steps.push({
      id: 'execute-1',
      label: `${EXECUTE_LABEL}: failed`,
      detail: 'Postgres, read-only role',
      source: 'database',
      status: 'failed',
      attempt: 1,
      loopBack: false,
      sql: null,
      error: null,
    })

    for (let round = 1; round <= rounds; round++) {
      steps.push(correctionStep(round, classified.attempts, 'failed'))
    }

    steps.push({
      id: 'exhausted',
      label: 'Exhausted',
      detail: `No working query after ${classified.attempts} ${
        classified.attempts === 1 ? 'attempt' : 'attempts'
      }`,
      source: 'system',
      status: 'failed',
      attempt: classified.attempts,
      loopBack: false,
      sql: null,
      error: classified.error,
    })

    return steps
  }

  // Success.
  const rounds = correctionRounds(classified.attempts)

  if (rounds > 0) {
    // The first execution must have failed, or the corrector would never have
    // run. That inference is sound: `attempts > 1` is itself the evidence.
    steps.push({
      id: 'execute-1',
      label: `${EXECUTE_LABEL}: failed`,
      detail: 'Postgres, read-only role',
      source: 'database',
      status: 'failed',
      attempt: 1,
      loopBack: false,
      sql: null,
      error: null,
    })

    for (let round = 1; round <= rounds; round++) {
      // Every round but the last failed; the last one produced the SQL that ran.
      steps.push(correctionStep(round, classified.attempts, round === rounds ? 'retry' : 'failed'))
    }
  }

  steps.push({
    id: 'execute-final',
    label: EXECUTE_LABEL,
    detail: `${classified.result.length} ${classified.result.length === 1 ? 'row' : 'rows'}`,
    source: 'database',
    status: 'success',
    attempt: Math.max(1, classified.attempts),
    loopBack: false,
    // The one step that genuinely knows its SQL.
    sql: classified.sql,
    error: null,
  })

  steps.push({
    id: 'explain',
    label: EXPLAIN_LABEL,
    detail: 'Cerebras',
    source: 'cerebras',
    status: 'success',
    attempt: Math.max(1, classified.attempts),
    loopBack: false,
    sql: null,
    error: null,
  })

  return steps
}
