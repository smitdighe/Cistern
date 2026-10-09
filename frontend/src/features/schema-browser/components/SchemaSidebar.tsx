import { Table2 } from 'lucide-react'

import type { SchemaResponse } from '../../../api/types/schema.types'
import { cn } from '../../../lib/cn'

export interface SchemaSidebarProps {
  schema: SchemaResponse
  selected: string | null
  onSelect: (tableName: string) => void
  id?: string
  className?: string
}

export function SchemaSidebar({
  schema,
  selected,
  onSelect,
  id,
  className,
}: SchemaSidebarProps) {
  return (
    <nav
      id={id}
      aria-label="Database tables"
      className={cn('glass rounded-lg p-2', className)}
    >
      <h2 className="px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-foreground/60">
        Tables ({schema.table_count})
      </h2>
      <ul>
        {schema.tables.map((table) => {
          const isSelected = selected === table.name
          const foreignKeyCount = table.foreign_keys.length

          return (
            <li key={table.name}>
              <button
                type="button"
                onClick={() => onSelect(table.name)}
                aria-current={isSelected ? 'true' : undefined}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm',
                  // Same interaction language as Button and the nav tabs.
                  'transition-[transform,background-color,color,box-shadow] duration-150',
                  'active:scale-[0.98]',
                  'outline-none focus-visible:ring-2 focus-visible:ring-accent/50',
                  'focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                  isSelected
                    ? 'bg-accent/15 text-accent shadow-sm shadow-accent/10'
                    : 'text-foreground/70 hover:bg-foreground/[0.07] hover:text-foreground',
                )}
              >
                <Table2 className="size-3.5 shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate font-mono text-xs">{table.name}</span>
                {/* Only shown when there is a relationship to report — a "0"
                    on every unrelated table is noise. */}
                {foreignKeyCount > 0 && (
                  <span className="shrink-0 text-[10px] text-foreground/60">
                    {foreignKeyCount} fk
                  </span>
                )}
              </button>
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
