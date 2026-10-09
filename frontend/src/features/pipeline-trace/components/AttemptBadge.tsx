import { useEffect, useState } from 'react'

import { cn } from '../../../lib/cn'

const COUNT_UP_MS = 400

export interface AttemptBadgeProps {
  attempts: number
  reducedMotion?: boolean
  className?: string
}

/**
 * Attempt counter, shown only when there is something to say.
 *
 * A first-try success renders nothing at all — a "1 attempt" badge would turn
 * the normal case into a status report and dilute the signal when the count is
 * genuinely interesting.
 */
export function AttemptBadge({ attempts, reducedMotion, className }: AttemptBadgeProps) {
  const shouldAnimate = attempts > 1 && !reducedMotion
  const [displayed, setDisplayed] = useState(() => (shouldAnimate ? 1 : attempts))

  useEffect(() => {
    if (!shouldAnimate) {
      setDisplayed(attempts)
      return
    }

    setDisplayed(1)
    const stepMs = COUNT_UP_MS / Math.max(1, attempts - 1)
    const id = setInterval(() => {
      setDisplayed((current) => {
        if (current >= attempts) {
          clearInterval(id)
          return attempts
        }
        return current + 1
      })
    }, stepMs)

    return () => clearInterval(id)
  }, [attempts, shouldAnimate])

  if (attempts <= 1) return null

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full bg-warning/15 px-2 py-0.5 text-xs font-medium text-warning',
        className,
      )}
    >
      {/* The animated digit is decorative; the full sentence is what gets read out. */}
      <span aria-hidden="true">{displayed}</span>
      <span aria-hidden="true">{displayed === 1 ? 'attempt' : 'attempts'}</span>
      <span className="sr-only">{attempts} attempts</span>
    </span>
  )
}
