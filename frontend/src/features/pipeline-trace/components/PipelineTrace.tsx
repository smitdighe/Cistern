import { Fragment, useMemo } from 'react'

import { cn } from '../../../lib/cn'
import type { ClassifiedQueryResponse } from '../../query-console/hooks/useQueryResponseParser'
import { useTraceReplay } from '../hooks/useTraceReplay'
import { buildTraceSteps } from '../utils/buildTraceSteps'
import { AttemptBadge } from './AttemptBadge'
import { SafetyFlagChip } from './SafetyFlagChip'
import { TraceConnector } from './TraceConnector'
import { TraceStep } from './TraceStep'

export interface PipelineTraceProps {
  classified: ClassifiedQueryResponse
  className?: string
}

/**
 * Flags the response actually carried.
 *
 * Clarifying short-circuits before generation, so it has none. Exhausted
 * correction is the one case that drops them: the only flag the backend sets on
 * that path is `correction_stalled`, which the classifier has already consumed
 * to pick this state — showing it again would annotate the failure with the
 * reason it was categorised, not with anything new.
 */
function flagsFor(classified: ClassifiedQueryResponse): string[] {
  if (classified.type === 'success') return classified.safetyFlags
  if (classified.type === 'safety_blocked') return classified.safetyFlags
  return []
}

function attemptsFor(classified: ClassifiedQueryResponse): number {
  return classified.type === 'clarifying' ? 1 : classified.attempts
}

export function PipelineTrace({ classified, className }: PipelineTraceProps) {
  const steps = useMemo(() => buildTraceSteps(classified), [classified])
  const { delayFor, reducedMotion } = useTraceReplay(steps.length)

  if (steps.length === 0) return null

  const flags = flagsFor(classified)
  const attempts = attemptsFor(classified)
  // Flags come in after the last step, so the trace finishes drawing first.
  const flagBaseDelay = delayFor(steps.length)

  return (
    <section
      // Top of the elevation scale. This is the page's hero — the thing the
      // product is actually selling — so it gets the deepest blur and the
      // longest shadow, and reads as nearest without any z-index games.
      className={cn('glass glass-raised rounded-lg p-4 sm:p-5', className)}
      aria-label="Pipeline trace"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-xs font-medium uppercase tracking-wide text-foreground/70">
          Pipeline
        </h2>
        <AttemptBadge attempts={attempts} reducedMotion={reducedMotion} />
      </div>

      <ol className="mt-4">
        {steps.map((step, index) => (
          <Fragment key={step.id}>
            {index > 0 && (
              <li aria-hidden="true">
                <TraceConnector loop={step.loopBack} />
              </li>
            )}
            <li>
              <TraceStep step={step} delay={delayFor(index)} reducedMotion={reducedMotion} />
            </li>
          </Fragment>
        ))}
      </ol>

      {flags.length > 0 && (
        <div className="mt-5 border-t border-border pt-4">
          <h3 className="text-xs font-medium uppercase tracking-wide text-foreground/60">
            Flags
          </h3>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {flags.map((flag, index) => (
              <SafetyFlagChip key={flag} flag={flag} delay={flagBaseDelay + index * 0.05} />
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
