import type { ButtonHTMLAttributes } from 'react'

import { cn } from '../../lib/cn'

export type ButtonVariant = 'primary' | 'ghost' | 'danger'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
}

/**
 * Per-variant colour and depth.
 *
 * The lift on hover is carried by the shadow, not by a translate: a button that
 * physically moves under the cursor is a button you can chase. Brightness does
 * the "responding to you" work instead, and the shadow does the "coming
 * forward" work.
 */
const VARIANTS: Record<ButtonVariant, string> = {
  primary: cn(
    'bg-accent text-background shadow-sm shadow-accent/20',
    'hover:brightness-110 hover:shadow-md hover:shadow-accent/25',
    'active:brightness-95',
  ),
  ghost: cn(
    'glass text-foreground/80',
    'hover:bg-surface/80 hover:text-foreground hover:brightness-110',
    'active:brightness-95',
  ),
  danger: cn(
    'bg-error text-background shadow-sm shadow-error/20',
    'hover:brightness-110 hover:shadow-md hover:shadow-error/25',
    'active:brightness-95',
  ),
}

export function Button({ variant = 'primary', className, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-md px-3.5 py-2',
        'text-sm font-medium',
        // Transform is listed so the press reads as a press. Duration is short
        // enough that the press still feels like contact rather than playback.
        'transition-[transform,box-shadow,background-color,filter,color] duration-150',
        // Scale, not translate: a press should read as the surface giving way
        // under the finger, which is uniform, not directional.
        'active:scale-[0.97]',
        // One focus treatment everywhere. The ring is the accent at low opacity
        // plus a solid inner edge, so it stays visible against both the glass
        // panels and the solid accent fill of the primary variant.
        'outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2',
        'focus-visible:ring-offset-background',
        'disabled:pointer-events-none disabled:opacity-50',
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  )
}
