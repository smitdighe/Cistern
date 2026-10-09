import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, PanelLeftOpen, RotateCw } from 'lucide-react'

import { Button, Skeleton } from '../../components/ui'
import { usePrefersReducedMotion } from '../../hooks/usePrefersReducedMotion'
import { ERDiagram } from './components/ERDiagram'
import { SchemaSidebar } from './components/SchemaSidebar'
import { useSchema } from './hooks/useSchema'

/** How long a clicked table stays ringed before settling back. */
const HIGHLIGHT_MS = 2000

function LoadingState() {
  return (
    <div className="grid gap-6 lg:grid-cols-[14rem_1fr]" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading database schema…</span>
      <Skeleton className="hidden h-48 lg:block" />
      <div className="grid gap-6 sm:grid-cols-2">
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </div>
    </div>
  )
}

export function SchemaBrowserPage() {
  const { data: schema, isPending, isError, error, refetch, isFetching } = useSchema()
  const [highlighted, setHighlighted] = useState<string | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const reducedMotion = usePrefersReducedMotion()
  const timeoutRef = useRef<number | null>(null)

  useEffect(() => () => window.clearTimeout(timeoutRef.current ?? undefined), [])

  const selectTable = useCallback(
    (tableName: string) => {
      setHighlighted(tableName)
      // Below lg the list is a drawer overlaying the diagram, so it has to get
      // out of the way before the scroll target means anything.
      setDrawerOpen(false)

      document.getElementById(`table-${tableName}`)?.scrollIntoView({
        // CSS scroll-behavior does not govern programmatic smooth scrolling,
        // so the preference has to be applied explicitly here.
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'center',
      })

      window.clearTimeout(timeoutRef.current ?? undefined)
      timeoutRef.current = window.setTimeout(() => setHighlighted(null), HIGHLIGHT_MS)
    },
    [reducedMotion],
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-foreground">Schema browser</h1>
        <p className="mt-1 text-sm text-foreground/70">
          The tables, columns and relationships the generator sees when it writes a query.
        </p>
      </div>

      {isPending && <LoadingState />}

      {isError && (
        <div className="glass glass-error rounded-lg bg-error/[0.07] p-5" role="alert">
          <div className="flex items-start gap-3">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-error" aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <h2 className="text-xs font-medium uppercase tracking-wide text-error">
                Could not load the schema
              </h2>
              <p className="mt-2 text-sm text-foreground/70">
                {/* /schema returns 503 when the admin connection is down, which
                    is a transient condition worth retrying rather than a dead end. */}
                The backend could not read the database schema. This is usually the admin
                connection being unavailable.
              </p>
              <p className="mt-2 break-words font-mono text-xs text-foreground/70">
                {error.message}
              </p>
              <Button
                variant="ghost"
                className="mt-4"
                onClick={() => void refetch()}
                disabled={isFetching}
              >
                <RotateCw className="size-3.5" aria-hidden="true" />
                {isFetching ? 'Retrying…' : 'Retry'}
              </Button>
            </div>
          </div>
        </div>
      )}

      {schema && schema.tables.length === 0 && (
        <div className="glass rounded-lg px-4 py-6 text-center text-sm text-foreground/70">
          The schema loaded successfully but contains no tables.
        </div>
      )}

      {schema && schema.tables.length > 0 && (
        <>
          <Button
            variant="ghost"
            className="lg:hidden"
            onClick={() => setDrawerOpen((open) => !open)}
            aria-expanded={drawerOpen}
            aria-controls="schema-table-list"
          >
            <PanelLeftOpen className="size-3.5" aria-hidden="true" />
            {drawerOpen ? 'Hide tables' : `Tables (${schema.table_count})`}
          </Button>

          <div className="grid gap-6 lg:grid-cols-[14rem_1fr] lg:items-start">
            {/* One list, two presentations: a collapsible block below lg and a
                sticky rail above it. Rendering it twice would duplicate the ids
                the diagram anchors to. */}
            <SchemaSidebar
              id="schema-table-list"
              schema={schema}
              selected={highlighted}
              onSelect={selectTable}
              // top-20, not top-6: the header is sticky now and 65px tall, so
              // anything pinned nearer the top slides underneath it. Matches
              // the `scroll-padding-top` in index.css, which solves the same
              // problem for the scroll-into-view this list triggers.
              className={drawerOpen ? '' : 'hidden lg:sticky lg:top-20 lg:block'}
            />
            <ERDiagram schema={schema} highlightedTable={highlighted} />
          </div>
        </>
      )}
    </div>
  )
}
