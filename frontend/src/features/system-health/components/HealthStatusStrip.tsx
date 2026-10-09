import { useEffect, useState } from 'react'

import type { DependencyName, DependencyStatus } from '../../../api/types/health.types'
import { cn } from '../../../lib/cn'
import { useHealthPoll } from '../hooks/useHealthPoll'

/**
 * What a dot can show. Narrower than `DependencyStatus` on purpose: `up` and
 * `down` are reported states, everything else — `unconfigured`, a failed poll,
 * a reading too old to trust — collapses to "we do not know".
 */
type DotState = 'up' | 'down' | 'unknown'

const DEPENDENCIES: { key: DependencyName; label: string }[] = [
  { key: 'groq', label: 'Groq' },
  { key: 'cerebras', label: 'Cerebras' },
  { key: 'neon_execution', label: 'Neon (read-only)' },
  { key: 'neon_admin', label: 'Neon (admin)' },
]

/**
 * `unconfigured` means a provider key is not set — the dependency is absent,
 * not broken. It maps to the neutral dot rather than the red one, because
 * flagging a deliberate configuration choice as an outage is a false alarm.
 */
function toDotState(status: DependencyStatus | undefined): DotState {
  if (status === 'up') return 'up'
  if (status === 'down') return 'down'
  return 'unknown'
}

const DOT_COLORS: Record<DotState, string> = {
  up: 'bg-success',
  down: 'bg-error',
  unknown: 'bg-foreground/30',
}

const DOT_STATES: DotState[] = ['up', 'down', 'unknown']

/**
 * Crossfades between states by holding all three layers mounted permanently and
 * toggling their opacity by class.
 *
 * Two earlier shapes were wrong, both for the same underlying reason — they
 * made the *displayed status* depend on an animation finishing:
 *
 *   1. AnimatePresence keyed on state: the outgoing dot stays mounted until its
 *      exit animation ends. Exit animations run on rAF, which a backgrounded
 *      tab parks, so stale dots accumulated on every poll.
 *   2. Fixed layers with a Framer `animate` prop: no accumulation, but Framer
 *      only writes the new opacity as frames render. With rAF parked the dot
 *      kept its previous colour indefinitely while the label already said
 *      otherwise — a dot showing "unknown" grey next to an accessible name
 *      reading "up".
 *
 * CSS classes have neither problem: the class React renders *is* the state, and
 * the browser resolves it whether or not a single frame is painted. The
 * transition is decoration on top of an already-correct value, and the
 * reduced-motion rule in index.css collapses it for free.
 */
function CrossfadeDot({ state, label }: { state: DotState; label: string }) {
  return (
    <span
      role="img"
      aria-label={`${label}: ${state}`}
      className="relative inline-flex size-2 shrink-0"
    >
      {DOT_STATES.map((candidate) => (
        <span
          key={candidate}
          aria-hidden="true"
          className={cn(
            'absolute inset-0 rounded-full transition-opacity duration-500 ease-in-out',
            DOT_COLORS[candidate],
            candidate === state ? 'opacity-100' : 'opacity-0',
          )}
        />
      ))}
    </span>
  )
}

export interface HealthStatusStripProps {
  className?: string
}

/**
 * Age past which the last reading stops being trustworthy — three poll
 * intervals, so a single slow or dropped request does not blank the strip.
 * Tracks POLL_INTERVAL_MS in useHealthPoll; changing one without the other
 * either blanks the strip on every hiccup or hides a dead backend.
 */
const STALE_AFTER_MS = 90_000

export function HealthStatusStrip({ className }: HealthStatusStripProps) {
  const { data, isError, dataUpdatedAt } = useHealthPoll()

  // Staleness is a function of wall-clock time, not of any query event, so it
  // needs its own tick to be noticed — otherwise a strip whose polling has
  // stopped keeps rendering its last result indefinitely with nothing to
  // re-evaluate it.
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000)
    return () => clearInterval(id)
  }, [])

  // A reading this old is not evidence of anything. Reporting "up" from a
  // measurement that stopped arriving is the one failure mode a health strip
  // must not have — better to admit we do not know.
  const stale = dataUpdatedAt > 0 && now - dataUpdatedAt > STALE_AFTER_MS
  const trustworthy = !isError && !stale

  return (
    <div
      className={cn('flex items-center gap-2 sm:gap-3', className)}
      role="status"
      aria-label="Backend dependency health"
    >
      {DEPENDENCIES.map(({ key, label }) => {
        // A failed poll is genuinely unknown state, not a reported outage —
        // the backend might be down, or just unreachable from here.
        const state = trustworthy ? toDotState(data?.checks?.[key]?.status) : 'unknown'
        const latency = data?.checks?.[key]?.latency_ms

        return (
          <span
            key={key}
            className="flex items-center gap-1.5"
            title={
              stale
                ? `${label}: no reading in the last ${Math.round(STALE_AFTER_MS / 1000)}s`
                : latency !== undefined && !isError
                  ? `${label}: ${state} (${latency}ms)`
                  : label
            }
          >
            <CrossfadeDot state={state} label={label} />
            <span className="hidden text-xs text-foreground/70 xl:inline">{label}</span>
          </span>
        )
      })}
    </div>
  )
}
