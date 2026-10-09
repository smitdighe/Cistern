import { memo, useDeferredValue, useMemo } from 'react'

import { cn } from '../../../lib/cn'

export interface ResultTableProps {
  rows: Record<string, unknown>[]
  className?: string
}

/**
 * How many rows are painted in the same commit as the rest of the answer.
 *
 * Enough to fill any viewport, so the table never *looks* partial. A full
 * LIMIT-capped 500-row response is ~3000 cells; committing them all at once was
 * a measured 568ms task on the main thread, which both stutters the page and
 * eats the first half-second of the pipeline trace's replay — the one animation
 * on the page that is supposed to be smooth.
 */
const FIRST_PAINT_ROWS = 50

/** Sentinel for the deferred read: never equal to a real `rows` array. */
const EMPTY_ROWS: Record<string, unknown>[] = []

/** Postgres sends JSON null for NULL; render it as a marker, not an empty cell. */
function renderCell(value: unknown) {
  if (value === null || value === undefined) {
    return <span className="text-foreground/60">null</span>
  }
  if (typeof value === 'object') {
    return <span className="font-mono text-xs">{JSON.stringify(value)}</span>
  }
  return String(value)
}

export const ResultTable = memo(function ResultTable({ rows, className }: ResultTableProps) {
  // Columns come from the data, never from an assumed schema — the console has
  // to render whatever shape the generated SQL happened to produce. Later rows
  // are unioned in so a sparse first row cannot hide a column.
  const columns = useMemo(() => {
    const seen = new Set<string>()
    for (const row of rows) {
      for (const key of Object.keys(row)) seen.add(key)
    }
    return [...seen]
  }, [rows])

  // The tail arrives in a second, interruptible pass. `useDeferredValue` keeps
  // the urgent commit small: React paints the head, hands the frame back to the
  // browser, then renders the rest at low priority, where it can yield between
  // chunks. Nothing is hidden or paginated — a moment later every row is in the
  // DOM, and scroll, select-all and Ctrl+F all see the full set.
  //
  // The comparison is against `rows`, not against the deferred value itself, so
  // the head slice always comes from the *current* prop. Reading the deferred
  // value directly would paint the previous query's rows for one frame if this
  // component ever updates in place instead of remounting.
  const settled = useDeferredValue(rows, EMPTY_ROWS) === rows
  const visibleRows = settled ? rows : rows.slice(0, FIRST_PAINT_ROWS)

  if (rows.length === 0) {
    return (
      <div
        className={cn(
          'glass rounded-lg px-4 py-6 text-center text-sm text-foreground/70',
          className,
        )}
      >
        No rows returned. The query ran successfully and matched nothing.
      </div>
    )
  }

  return (
    <div className={cn('min-w-0', className)}>
      {/* Focusable and labelled so the horizontal scroll is reachable by
          keyboard — a wide result set is otherwise unreadable without a mouse. */}
      <div
        role="region"
        aria-label={`Query results, ${rows.length} ${rows.length === 1 ? 'row' : 'rows'}`}
        tabIndex={0}
        className={cn(
          'glass overflow-x-auto rounded-lg',
          'outline-none focus-visible:ring-2 focus-visible:ring-accent/50',
          'focus-visible:ring-offset-2 focus-visible:ring-offset-background',
        )}
      >
        {/* Row and cell styling is declared once here as descendant rules
            rather than repeated on every element. At the LIMIT cap that is one
            class attribute instead of 3500, which is measurable work removed
            from the commit that paints the tail. */}
        <table
          className={cn(
            'w-full border-collapse text-left text-sm',
            '[&_td]:whitespace-nowrap [&_td]:px-4 [&_td]:py-2 [&_td]:text-foreground/85',
            '[&_tbody_tr]:border-b [&_tbody_tr]:border-border/50 [&_tbody_tr:last-child]:border-0',
          )}
        >
          <thead>
            {/* Its own tint, not just a rule: on a translucent panel a plain
                hairline is the only thing separating headers from data, and
                the background showing through weakens it. */}
            <tr className="border-b border-border/70 bg-foreground/[0.04]">
              {columns.map((column) => (
                <th
                  key={column}
                  scope="col"
                  className="whitespace-nowrap px-4 py-2 text-xs font-medium uppercase tracking-wide text-foreground/70"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {columns.map((column) => (
                  <td key={column}>{renderCell(row[column])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Always the true total, never the number currently committed — the
          count is a fact about the answer, not about the render. */}
      <p className="mt-2 text-xs text-foreground/60">
        {rows.length} {rows.length === 1 ? 'row' : 'rows'}
      </p>
    </div>
  )
})
