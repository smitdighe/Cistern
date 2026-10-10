import { forwardRef, type PointerEvent as ReactPointerEvent } from 'react'
import { GripHorizontal, KeyRound, Link2 } from 'lucide-react'

import type { TableInfo } from '../../../api/types/schema.types'
import { cn } from '../../../lib/cn'
import { shortColumnType } from '../utils/columnType'
import type { Point } from '../utils/diagramGeometry'

export interface TableNodeProps {
  table: TableInfo
  highlighted?: boolean
  /** Whether this node accepts pointer dragging at the current breakpoint. */
  draggable?: boolean
  /** True only for the node under an active pointer. */
  dragging?: boolean
  /** Translation from the node's grid slot. Absent means "as the grid placed it". */
  offset?: Point
  onPointerDown?: (event: ReactPointerEvent<HTMLDivElement>) => void
  onPointerMove?: (event: ReactPointerEvent<HTMLDivElement>) => void
  onPointerUp?: (event: ReactPointerEvent<HTMLDivElement>) => void
  onPointerCancel?: (event: ReactPointerEvent<HTMLDivElement>) => void
  /** Registers the DOM node for a column row so FK lines can be anchored to it. */
  registerColumn?: (key: string, element: HTMLElement | null) => void
}

export const TableNode = forwardRef<HTMLDivElement, TableNodeProps>(function TableNode(
  {
    table,
    highlighted,
    draggable,
    dragging,
    offset,
    onPointerDown,
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    registerColumn,
  },
  ref,
) {
  const primaryKey = new Set(table.primary_key)
  const foreignKeyColumns = new Map(table.foreign_keys.map((fk) => [fk.column, fk]))

  return (
    <div
      ref={ref}
      id={`table-${table.name}`}
      className={cn(
        'glass overflow-hidden rounded-lg',
        // Colour and shadow settle smoothly; position must not. A transition on
        // transform would put the node behind the pointer for the whole drag.
        'transition-[box-shadow,background-color] duration-500',
        highlighted && 'glass-accent',
        // Above the connector overlay (z-10) while held, so the node the
        // pointer is carrying is never drawn underneath its own lines.
        //
        // Lifted via the existing elevation step rather than a `shadow-*`
        // utility: a Tailwind shadow sets `box-shadow` outright and would drop
        // the whole glass stack — the accent glow and the inset lit edge — for
        // a flat grey drop shadow, exactly while the node is most looked at.
        dragging && 'z-20 glass-raised',
      )}
      style={{
        transform: offset ? `translate(${offset.x}px, ${offset.y}px)` : undefined,
        // Hinted only while held. Left on permanently it would keep a
        // compositor layer per table for the life of the page.
        willChange: dragging ? 'transform' : undefined,
      }}
    >
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerCancel}
        className={cn(
          'flex items-baseline justify-between gap-2 border-b px-3 py-2',
          'transition-colors duration-500',
          highlighted ? 'border-accent/40 bg-accent/10' : 'border-border/60 bg-foreground/[0.06]',
          // The header is the handle, not the whole card. A card-wide drag
          // surface would have to suppress text selection across the column
          // list, and those names are the thing most worth copying out of this
          // view. A title bar is also the affordance people already know.
          draggable && (dragging ? 'cursor-grabbing select-none' : 'cursor-grab'),
          // Claims the touch stream so a drag does not also scroll the page.
          // Only set where dragging is offered — below `sm` this would trap
          // vertical scrolling on every table header.
          draggable && 'touch-none',
        )}
      >
        {/* h2, not h3: the page heading is the h1 and there is no intermediate
            level, so h3 would skip one. */}
        <h2 className="min-w-0 truncate font-mono text-sm font-medium text-foreground">
          {table.name}
        </h2>
        <div className="flex shrink-0 items-baseline gap-2">
          <span className="text-xs text-foreground/60">
            {table.columns.length} {table.columns.length === 1 ? 'col' : 'cols'}
          </span>
          {/* Visible affordance for the drag, so it does not depend on
              hovering to be discovered. Decorative: the grip does nothing the
              header itself does not already do. */}
          {draggable && (
            <GripHorizontal
              className={cn(
                'size-3.5 shrink-0 transition-colors',
                dragging ? 'text-accent' : 'text-foreground/30',
              )}
              aria-hidden="true"
            />
          )}
        </div>
      </div>

      <ul className="divide-y divide-border/40">
        {table.columns.map((column) => {
          const isPrimary = primaryKey.has(column.name)
          const foreignKey = foreignKeyColumns.get(column.name)
          const shortType = shortColumnType(column.type)

          return (
            <li
              key={column.name}
              ref={(element) => registerColumn?.(`${table.name}.${column.name}`, element)}
              className={cn(
                'flex items-center gap-2 px-3 py-1.5 text-xs',
                // The key is the structural fact worth seeing first, so it gets
                // the accent tint rather than just a marker glyph.
                isPrimary && 'bg-accent/5',
              )}
            >
              <span className="flex w-3.5 shrink-0 justify-center">
                {isPrimary ? (
                  <KeyRound className="size-3 text-accent" aria-label="Primary key" />
                ) : foreignKey ? (
                  <Link2 className="size-3 text-foreground/60" aria-label="Foreign key" />
                ) : null}
              </span>

              {/* `flex-auto`, not `flex-1`: with a zero basis the name was sized
                  from whatever the type left over, and a long type such as
                  "timestamp with time zone" left nothing. Sized from its
                  content, the name competes for the row — and the type's
                  shrink weight below decides that it wins. */}
              <span
                className={cn(
                  'min-w-0 flex-auto truncate font-mono',
                  isPrimary ? 'font-medium text-foreground' : 'text-foreground/80',
                )}
              >
                {column.name}
                {/* Below sm the tables stack and the connector lines are hidden,
                    so the relationship has to be stated in text or it is lost.
                    Also the only form available to a screen reader. */}
                {foreignKey && (
                  <span className="ml-1 text-foreground/60 sm:sr-only">
                    → {foreignKey.ref_table}.{foreignKey.ref_column}
                  </span>
                )}
              </span>

              {/* The name is what people scan for; the type is detail. Postgres's
                  short alias keeps most types whole, and where the card is
                  still too narrow the type gives way first — a shrink weight of
                  100 against the name's 1 — down to a floor that keeps a few
                  characters showing rather than nothing. The full spelling
                  stays one hover away and is what a screen reader hears. */}
              <span
                title={column.type}
                className="min-w-[4ch] shrink-[100] truncate font-mono text-foreground/60"
              >
                {shortType === column.type ? (
                  column.type
                ) : (
                  <>
                    <span aria-hidden="true">{shortType}</span>
                    <span className="sr-only">{column.type}</span>
                  </>
                )}
              </span>

              {/* Nullability is stated only when it is true — "NULL" on two of
                  fifteen rows reads faster than "NOT NULL" on the other thirteen.
                  Neutral, not amber: `warning` is a status colour, and a nullable
                  column is a fact about the schema, not a problem with it.
                  At /50 this measured 4.42:1 against the composited glass — the
                  translucent surface costs a little contrast, and 10px text has
                  none to spare. */}
              <span className="w-8 shrink-0 text-right text-[10px] uppercase tracking-wide text-foreground/70">
                {column.nullable ? 'null' : ''}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
})
