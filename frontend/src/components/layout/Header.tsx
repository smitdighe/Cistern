import { NavLink } from 'react-router-dom'

import { HealthStatusStrip } from '../../features/system-health/components'
import { cn } from '../../lib/cn'

const NAV = [
  { to: '/', label: 'Console' },
  { to: '/schema', label: 'Schema' },
]

export function Header() {
  return (
    /*
     * Frosted and sticky. Content passing beneath stays faintly readable rather
     * than sliding under an opaque strip — the same "nothing is hidden" claim
     * the glass panels make, applied to the chrome.
     *
     * The bottom hairline is a gradient that fades at both ends, so the bar
     * reads as an edge catching light instead of a ruled line across the page.
     */
    <header
      className={cn(
        'sticky top-0 z-30 border-b border-transparent',
        'bg-background/70 backdrop-blur-xl backdrop-saturate-150',
        'after:absolute after:inset-x-0 after:-bottom-px after:h-px',
        'after:bg-gradient-to-r after:from-transparent after:via-border after:to-transparent',
      )}
    >
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 sm:px-6 sm:py-4">
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-semibold tracking-tight text-foreground">Cistern</span>
          <span className="hidden text-sm text-foreground/70 lg:inline">
            Natural language questions, answered as SQL
          </span>
        </div>

        <div className="flex items-center gap-3 sm:gap-5">
          <HealthStatusStrip />

          <nav className="flex items-center gap-1">
            {NAV.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  cn(
                    'rounded-md px-2.5 py-1.5 text-sm sm:px-3',
                    // Same interaction language as Button: brightness on hover,
                    // scale on press, accent ring on focus.
                    'transition-[transform,background-color,color,box-shadow] duration-150',
                    'active:scale-[0.97]',
                    'outline-none focus-visible:ring-2 focus-visible:ring-accent/50',
                    'focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                    isActive
                      ? 'bg-accent/15 text-accent shadow-sm shadow-accent/10'
                      : 'text-foreground/60 hover:bg-foreground/[0.07] hover:text-foreground',
                  )
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
      </div>
    </header>
  )
}
