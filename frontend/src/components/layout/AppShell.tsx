import { Outlet } from 'react-router-dom'

import { useSchema } from '../../features/schema-browser/hooks/useSchema'
import { BackgroundGrid } from './BackgroundGrid'
import { Header } from './Header'

/** Layout route: chrome that persists across every page. */
export function AppShell() {
  // Fetched once per session (staleTime Infinity) and shared with
  // /schema through the query cache, so mounting it here costs one request.
  // A failure is not surfaced — the grid falls back to its decorative form and
  // the schema page owns reporting the error properly.
  const { data: schema } = useSchema()

  return (
    <div className="relative min-h-svh bg-background">
      <div className="pointer-events-none absolute inset-x-0 top-0 h-[32rem] overflow-hidden">
        <BackgroundGrid density="medium" schema={schema ?? null} />
        {/* Fades the grid out before it reaches the content, so nothing sits on texture. */}
        <div className="absolute inset-0 bg-gradient-to-b from-transparent to-background" />
      </div>

      <div className="relative flex min-h-svh flex-col">
        <Header />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 sm:px-6 sm:py-10">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
