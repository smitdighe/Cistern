import type { HTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /**
   * Raise this card to the top of the elevation scale. Reserved for content
   * that is the point of the page rather than a panel on it — in practice, the
   * pipeline trace.
   */
  elevated?: boolean
}

/**
 * The app's glass surface.
 *
 * `.glass` (see index.css) owns the whole treatment — translucency, backdrop
 * blur, the gradient hairline, the shadow stack — so every panel in the app
 * shares one definition and changing the material means editing one place.
 */
export function Card({ elevated, className, ...props }: CardProps) {
  return (
    <div
      className={cn('glass rounded-lg p-5', elevated && 'glass-raised', className)}
      {...props}
    />
  )
}
