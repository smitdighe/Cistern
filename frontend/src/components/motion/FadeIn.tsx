import type { ReactNode } from 'react'
import { motion } from 'framer-motion'

import { usePrefersReducedMotion } from '../../hooks/usePrefersReducedMotion'
import { fadeUp } from './variants'

export interface FadeInProps {
  children: ReactNode
  /** Seconds to wait before animating. Ignored under reduced motion. */
  delay?: number
  className?: string
}

export function FadeIn({ children, delay = 0, className }: FadeInProps) {
  const reduced = usePrefersReducedMotion()

  // Under reduced motion the content is rendered plainly rather than animated
  // to opacity 1 instantly — no motion node, so nothing can flash mid-transition.
  if (reduced) {
    return <div className={className}>{children}</div>
  }

  return (
    <motion.div
      className={className}
      variants={fadeUp}
      initial="hidden"
      animate="visible"
      transition={{ delay }}
    >
      {children}
    </motion.div>
  )
}
