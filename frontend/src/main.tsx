import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { MotionConfig } from 'framer-motion'

import { queryClient } from './lib/queryClient'
import { routes } from './router/routes'
import './index.css'

/**
 * Every v7 opt-in, taken now.
 *
 * React Router 6.30 logs a deprecation warning per un-set flag on each page
 * load — six lines of dev-console noise that hide real warnings. They are
 * stripped from production builds, so this is about the development console,
 * not the shipped bundle. All six are inert here: the app has no splat routes,
 * no fetchers, and no router loaders or actions, so the only flag that changes
 * observable behaviour is `v7_startTransition`, which marks navigation state
 * updates as non-urgent — which is what they are.
 *
 * The split is not arbitrary: `v7_startTransition` is the one flag read from
 * the *provider's* future object; the rest belong to the router's, and only
 * that object is typed to accept them.
 */
const router = createBrowserRouter(routes, {
  future: {
    v7_fetcherPersist: true,
    v7_normalizeFormMethod: true,
    v7_partialHydration: true,
    v7_relativeSplatPath: true,
    v7_skipActionErrorRevalidation: true,
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* Framer Motion's default is `reducedMotion: "never"` — without this,
          every motion component ignores the OS setting. "user" makes transform
          and layout animations respect it globally. */}
      <MotionConfig reducedMotion="user">
        <RouterProvider router={router} future={{ v7_startTransition: true }} />
      </MotionConfig>
    </QueryClientProvider>
  </StrictMode>,
)
