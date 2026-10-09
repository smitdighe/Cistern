import { motion } from 'framer-motion'

import { usePrefersReducedMotion } from '../../../hooks/usePrefersReducedMotion'
import { cn } from '../../../lib/cn'
import { flagSeverity, type FlagSeverity } from '../utils/flagSeverity'

const SEVERITY_STYLES: Record<FlagSeverity, string> = {
  blocked: 'bg-error/15 text-error',
  adjusted: 'bg-warning/15 text-warning',
  neutral: 'bg-foreground/10 text-foreground/60',
}

/**
 * Entrance motion carries the severity too.
 *
 * Amber slides in — something was quietly changed on your behalf, worth
 * noticing, not alarming. Red shakes once, ~3px, a single cycle: enough to pull
 * the eye to a refusal, short enough not to read as decoration.
 */
const SEVERITY_MOTION: Record<FlagSeverity, { x: number | number[]; opacity: number[] }> = {
  adjusted: { x: [-8, 0], opacity: [0, 1] },
  blocked: { x: [0, -3, 3, -2, 0], opacity: [0, 1, 1, 1, 1] },
  neutral: { x: [0, 0], opacity: [0, 1] },
}

/** Spoken severity, so the meaning does not depend on colour or on the shake. */
const SEVERITY_LABELS: Record<FlagSeverity, string> = {
  blocked: 'blocking flag',
  adjusted: 'auto-adjusted flag',
  neutral: 'informational flag',
}

export interface SafetyFlagChipProps {
  flag: string
  delay?: number
  className?: string
}

export function SafetyFlagChip({ flag, delay = 0, className }: SafetyFlagChipProps) {
  // Read directly rather than taking a prop — every caller wanted the same
  // answer, and one that forgot to pass it (ErrorCard) silently shook its
  // chips at users who had asked for no motion.
  const reducedMotion = usePrefersReducedMotion()
  const severity = flagSeverity(flag)

  const chipClass = cn(
    'inline-flex items-center rounded-full px-2 py-0.5 font-mono text-xs leading-5',
    SEVERITY_STYLES[severity],
    className,
  )

  const srLabel = <span className="sr-only">, {SEVERITY_LABELS[severity]}</span>

  if (reducedMotion) {
    return (
      <span className={chipClass}>
        {flag}
        {srLabel}
      </span>
    )
  }

  const { x, opacity } = SEVERITY_MOTION[severity]

  return (
    <motion.span
      className={chipClass}
      initial={{ opacity: 0 }}
      animate={{ x, opacity }}
      transition={{
        delay,
        duration: severity === 'blocked' ? 0.34 : 0.24,
        ease: 'easeOut',
      }}
    >
      {flag}
      {srLabel}
    </motion.span>
  )
}
