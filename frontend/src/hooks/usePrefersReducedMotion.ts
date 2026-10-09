import { useMediaQuery } from './useMediaQuery'

/** Whether the user has asked the OS to reduce motion. */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery('(prefers-reduced-motion: reduce)')
}
