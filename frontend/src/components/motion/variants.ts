import type { Variants } from 'framer-motion'

/** Shared easing — one curve everywhere so unrelated animations still feel related. */
export const EASE = [0.22, 1, 0.36, 1] as const

export const fadeUp: Variants = {
  hidden: { opacity: 0, y: 8 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.22, ease: EASE } },
}
