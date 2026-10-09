import { usePrefersReducedMotion } from '../../../hooks/usePrefersReducedMotion'

/** Ideal gap between step reveals. Compressed when there are many steps. */
const STEP_DELAY_MS = 240
/** Hard ceiling on the whole sequence, so a six-retry trace is no slower than a clean one. */
const MAX_SEQUENCE_MS = 1800

/**
 * Gap between consecutive step reveals, in ms.
 *
 * Long traces tighten their stagger rather than running longer — a seven-step
 * correction trace should not take three seconds to draw itself. Exported so
 * the ceiling is testable without rendering.
 */
export function traceStagger(stepCount: number): number {
  if (stepCount <= 1) return STEP_DELAY_MS
  return Math.min(STEP_DELAY_MS, MAX_SEQUENCE_MS / (stepCount - 1))
}

export interface TraceReplay {
  /** Seconds to delay step `index`. Zero throughout under reduced motion. */
  delayFor: (index: number) => number
  reducedMotion: boolean
}

/**
 * Sequence the reveal of an already-complete trace.
 *
 * This is a replay, not a progress indicator. Every step it animates describes
 * something the backend already finished before the response arrived — /query
 * is a single round trip with no streaming, so there is no live state to
 * follow. Timings are chosen for legibility, and carry no information.
 */
export function useTraceReplay(stepCount: number): TraceReplay {
  const reducedMotion = usePrefersReducedMotion()

  const gapMs = traceStagger(stepCount)

  return {
    reducedMotion,
    delayFor: (index: number) => (reducedMotion ? 0 : (index * gapMs) / 1000),
  }
}
