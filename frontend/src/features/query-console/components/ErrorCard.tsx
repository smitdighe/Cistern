import { RotateCcw, ShieldAlert } from 'lucide-react'

import { FadeIn } from '../../../components/motion'
import type { ClassifiedQueryResponse } from '../hooks/useQueryResponseParser'

type ErrorVariant = Extract<
  ClassifiedQueryResponse,
  { type: 'safety_blocked' } | { type: 'correction_exhausted' }
>

export interface ErrorCardProps {
  variant: ErrorVariant
}

export function ErrorCard({ variant }: ErrorCardProps) {
  const blocked = variant.type === 'safety_blocked'
  const Icon = blocked ? ShieldAlert : RotateCcw

  return (
    <FadeIn>
      {/* `glass-error` swaps only the hairline gradient for the status hue, so
          the panel is identifiable as a failure before any text is read while
          still being the same material as everything else. */}
      <div className="glass glass-error rounded-lg bg-error/[0.07] p-5">
        <div className="flex items-start gap-3">
          <Icon className="mt-0.5 size-4 shrink-0 text-error" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <h2 className="text-xs font-medium uppercase tracking-wide text-error">
              {blocked ? 'Blocked by the safety layer' : 'No working query produced'}
            </h2>

            <p className="mt-2 text-sm leading-relaxed text-foreground/90">
              {blocked ? (
                'The generated statement was not allowed to run.'
              ) : (
                <>
                  Couldn&apos;t produce a working query after {variant.attempts}{' '}
                  {variant.attempts === 1 ? 'attempt' : 'attempts'}.
                </>
              )}
            </p>

            {/* No flag chips here. Every branch that renders this card renders
                PipelineTrace beside it, and the trace already lists the flags
                once. Two identical chip rows on one screen read as two separate
                findings. */}
            <p className="mt-2 font-mono text-xs leading-relaxed text-foreground/60">
              {variant.error}
            </p>
          </div>
        </div>
      </div>
    </FadeIn>
  )
}
