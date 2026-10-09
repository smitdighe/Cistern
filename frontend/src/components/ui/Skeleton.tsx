import type { HTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

export type SkeletonProps = HTMLAttributes<HTMLDivElement>

/**
 * Indeterminate loading placeholder.
 *
 * Deliberately has no percentage, width animation, or ETA: the backend pipeline
 * gives no progress signal, and a bar that fills on a timer would be inventing
 * one. A pulse says "working" without claiming to know how far along it is.
 */
export function Skeleton({ className, ...props }: SkeletonProps) {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'animate-pulse rounded-md bg-foreground/10 motion-reduce:animate-none',
        className,
      )}
      {...props}
    />
  )
}
