import { motion } from 'framer-motion'
import { Cpu, Database, RefreshCw, Shield, SlashSquare } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

import { cn } from '../../../lib/cn'
import type { TraceSource, TraceStatus, TraceStep as TraceStepData } from '../utils/buildTraceSteps'

/**
 * One icon per source, so the two model tiers are distinguishable at a glance
 * without relying on colour — colour already encodes status, and reusing it for
 * identity would make both unreadable.
 */
const SOURCE_ICONS: Record<TraceSource, LucideIcon> = {
  groq: Cpu,
  cerebras: RefreshCw,
  safety: Shield,
  database: Database,
  system: SlashSquare,
}

const SOURCE_LABELS: Record<TraceSource, string> = {
  groq: 'Groq',
  cerebras: 'Cerebras',
  safety: 'Safety layer',
  database: 'Database',
  system: 'Pipeline',
}

/**
 * Three status colours plus neutral. No palette additions.
 *
 * Each ring carries a faint glow in its own status colour — the same depth
 * treatment the panels get, at the scale of a single step, so the trace reads
 * as a stack of lit tokens rather than as flat icons on glass.
 */
const STATUS_STYLES: Record<TraceStatus, { icon: string; ring: string; text: string }> = {
  success: {
    icon: 'text-success',
    ring: 'border-success/40 bg-success/10 shadow-[0_0_12px_-4px_hsl(var(--success)/0.5)]',
    text: 'text-foreground',
  },
  retry: {
    icon: 'text-warning',
    ring: 'border-warning/40 bg-warning/10 shadow-[0_0_12px_-4px_hsl(var(--warning)/0.5)]',
    text: 'text-foreground',
  },
  failed: {
    icon: 'text-foreground/60',
    ring: 'border-border bg-foreground/5',
    text: 'text-foreground/70',
  },
  blocked: {
    icon: 'text-error',
    ring: 'border-error/40 bg-error/10 shadow-[0_0_12px_-4px_hsl(var(--error)/0.5)]',
    text: 'text-foreground',
  },
}

export interface TraceStepProps {
  step: TraceStepData
  delay: number
  reducedMotion: boolean
}

export function TraceStep({ step, delay, reducedMotion }: TraceStepProps) {
  const Icon = SOURCE_ICONS[step.source]
  const styles = STATUS_STYLES[step.status]

  const content = (
    <div className="flex items-center gap-3">
      <span
        className={cn(
          'flex size-7 shrink-0 items-center justify-center rounded-md border',
          styles.ring,
        )}
      >
        <Icon className={cn('size-3.5', styles.icon)} aria-hidden="true" />
      </span>
      <div className="min-w-0">
        <p className={cn('text-sm leading-tight', styles.text)}>{step.label}</p>
        {step.detail && (
          <p className="mt-0.5 truncate text-xs text-foreground/70">{step.detail}</p>
        )}
      </div>
      <span className="sr-only">
        {SOURCE_LABELS[step.source]}, {step.status}
      </span>
    </div>
  )

  if (reducedMotion) {
    return content
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: -6 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay, duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
    >
      {content}
    </motion.div>
  )
}
