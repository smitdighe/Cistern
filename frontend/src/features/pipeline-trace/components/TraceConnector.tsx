import { cn } from '../../../lib/cn'

export interface TraceConnectorProps {
  /** Draw a loop-back curve instead of a straight run. */
  loop?: boolean
  className?: string
}

/**
 * The segment between two steps.
 *
 * The loop variant is a curve that leaves the spine, arcs out to the left and
 * returns — the shape reads as "went back and tried again" before any label is
 * read, which is the whole point of distinguishing it.
 */
export function TraceConnector({ loop, className }: TraceConnectorProps) {
  // Shorter runs below sm — the trace is the tallest thing on a phone screen
  // and the connectors are the cheapest height to give back.
  if (!loop) {
    return (
      <div
        className={cn('flex h-3.5 w-7 shrink-0 justify-center sm:h-5', className)}
        aria-hidden="true"
      >
        <span className="w-px bg-border" />
      </div>
    )
  }

  return (
    <div className={cn('h-3.5 w-7 shrink-0 sm:h-5', className)} aria-hidden="true">
      <svg viewBox="0 0 28 20" className="size-full overflow-visible" fill="none">
        <path
          d="M14 0 C 14 6, 3 5, 3 10 C 3 15, 14 14, 14 20"
          className="stroke-warning/70"
          strokeWidth="1.5"
          strokeLinecap="round"
        />
        <path
          d="M11 17 L14 20.5 L17 17"
          className="stroke-warning/70"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  )
}
