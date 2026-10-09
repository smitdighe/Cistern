import { useEffect, useState } from 'react'

/**
 * Subscribe to a CSS media query.
 *
 * Read once during the initial state so the first paint is already correct —
 * initialising to `false` and correcting in an effect makes the first frame a
 * lie, which for a motion preference means playing exactly the animation the
 * setting asks us to skip. Subscribed thereafter, because these can change
 * while the app is open (a resized window, a toggled OS setting).
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
    return window.matchMedia(query).matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const list = window.matchMedia(query)
    // Re-read on subscribe: the value can change between the initial render
    // and this effect running.
    setMatches(list.matches)

    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches)
    list.addEventListener('change', onChange)
    return () => list.removeEventListener('change', onChange)
  }, [query])

  return matches
}
