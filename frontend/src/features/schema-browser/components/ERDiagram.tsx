import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { SchemaResponse } from '../../../api/types/schema.types'
import { useMediaQuery } from '../../../hooks/useMediaQuery'
import {
  buildEdgeSpecs,
  clampOffset,
  resolveEdges,
  specsTouching,
  ZERO,
  type Edge,
  type LayoutSnapshot,
  type Point,
  type Rect,
} from '../utils/diagramGeometry'
import { TableNode } from './TableNode'

export interface ERDiagramProps {
  schema: SchemaResponse
  highlightedTable: string | null
  className?: string
}

interface DragState {
  table: string
  pointerId: number
  /** Pointer position at grab time, in client coordinates. */
  originX: number
  originY: number
  /** The node's offset at grab time — the delta is applied on top of this. */
  baseOffset: Point
}

/** Below this the diagram is one column and connectors are hidden — see below. */
const DRAGGABLE_QUERY = '(min-width: 640px)'

/**
 * Tables in a responsive grid, draggable, with FK curves drawn over the top.
 *
 * The grid is still what positions nodes initially: dragging applies a
 * `translate` *offset* on top of the grid slot rather than switching the node
 * to absolute coordinates. Three things fall out of that choice — the initial
 * state needs no seeding pass (offset zero is the grid slot, so nothing can
 * jump on first render), the layout stays responsive at every breakpoint, and
 * an untouched diagram is byte-identical to the one that shipped before.
 */
export function ERDiagram({ schema, highlightedTable, className }: ERDiagramProps) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const tableRefs = useRef(new Map<string, HTMLElement>())
  const columnRefs = useRef(new Map<string, HTMLElement>())

  /**
   * Measured layout with offsets removed. A ref, not state: it is an input to
   * rendering rather than a cause of it, and writing it during a drag must not
   * schedule work of its own.
   */
  const snapshotRef = useRef<LayoutSnapshot>({
    container: { width: 0, height: 0 },
    tables: new Map(),
    columns: new Map(),
  })

  const [positions, setPositions] = useState<Record<string, Point>>({})
  const [edges, setEdges] = useState<Edge[]>([])
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [dragging, setDragging] = useState<string | null>(null)

  // Dragging is pointer-only, so it is offered only where a pointer is the
  // likely input and the connectors it moves are actually drawn. Below `sm`
  // the nodes stack one per row with the SVG hidden, and claiming the touch
  // stream there would trade a meaningless rearrangement for the ability to
  // scroll the page.
  const canDrag = useMediaQuery(DRAGGABLE_QUERY)

  const specs = useMemo(() => buildEdgeSpecs(schema), [schema])

  // Live drag bookkeeping. Held in refs because a pointermove must not re-render
  // by itself — the frame callback is the only thing allowed to.
  const dragRef = useRef<DragState | null>(null)
  const frameRef = useRef<number | null>(null)
  const pendingRef = useRef<Point | null>(null)
  const positionsRef = useRef(positions)
  positionsRef.current = positions

  const registerTable = useCallback((name: string, element: HTMLElement | null) => {
    if (element) tableRefs.current.set(name, element)
    else tableRefs.current.delete(name)
  }, [])

  const registerColumn = useCallback((key: string, element: HTMLElement | null) => {
    if (element) columnRefs.current.set(key, element)
    else columnRefs.current.delete(key)
  }, [])

  /**
   * Re-read the DOM and rebuild every connector.
   *
   * The only place `getBoundingClientRect` is called. Runs on mount and on
   * reflow — never during a drag, where the snapshot it produced is reused and
   * only arithmetic runs.
   */
  const measure = useCallback(() => {
    const container = containerRef.current
    if (!container) return

    const origin = container.getBoundingClientRect()
    const offsets = positionsRef.current

    const toLayoutRect = (element: HTMLElement, table: string): Rect => {
      const rect = element.getBoundingClientRect()
      const offset = offsets[table] ?? ZERO
      // Subtract the drag offset back out: what is stored is where the grid
      // would put this node, so a later offset can be applied to it cleanly.
      return {
        left: rect.left - origin.left - offset.x,
        top: rect.top - origin.top - offset.y,
        width: rect.width,
        height: rect.height,
      }
    }

    const tables = new Map<string, Rect>()
    for (const [name, element] of tableRefs.current) {
      tables.set(name, toLayoutRect(element, name))
    }

    const columns = new Map<string, Rect>()
    for (const [key, element] of columnRefs.current) {
      // Column keys are `${table}.${column}`; the table name is everything
      // before the first dot, and table names cannot contain one here.
      const table = key.slice(0, key.indexOf('.'))
      columns.set(key, toLayoutRect(element, table))
    }

    const snapshot: LayoutSnapshot = {
      container: { width: origin.width, height: origin.height },
      tables,
      columns,
    }
    snapshotRef.current = snapshot

    setSize({ width: origin.width, height: origin.height })
    setEdges(resolveEdges(specs, snapshot, offsets))
  }, [specs])

  useEffect(() => {
    measure()

    const container = containerRef.current
    if (!container || typeof ResizeObserver === 'undefined') return

    // Re-measure on any reflow — column count changes at breakpoints, and the
    // lines are meaningless if they lag the layout. Skipped mid-drag: the only
    // thing moving then is a transform, which does not resize anything, and
    // re-measuring would read rects that already include the live offset.
    const observer = new ResizeObserver(() => {
      if (dragRef.current) return
      measure()
    })
    observer.observe(container)
    for (const element of tableRefs.current.values()) observer.observe(element)

    return () => observer.disconnect()
  }, [measure])

  /** Apply a pending offset and refresh only the connectors that moved. */
  const commitFrame = useCallback(() => {
    frameRef.current = null
    const drag = dragRef.current
    const desired = pendingRef.current
    if (!drag || !desired) return
    pendingRef.current = null

    const snapshot = snapshotRef.current
    const layout = snapshot.tables.get(drag.table)
    if (!layout) return

    const clamped = clampOffset(layout, snapshot.container, desired)
    const previous = positionsRef.current[drag.table] ?? ZERO
    if (clamped.x === previous.x && clamped.y === previous.y) return

    const next = { ...positionsRef.current, [drag.table]: clamped }
    positionsRef.current = next
    setPositions(next)

    // Only this node's own edges can have changed. Everything else keeps the
    // path it already had, so cost scales with the dragged node's degree
    // rather than with the size of the graph.
    const touched = specsTouching(specs, drag.table)
    if (touched.length === 0) return

    const recomputed = new Map(
      resolveEdges(touched, snapshot, next).map((edge) => [edge.id, edge]),
    )
    setEdges((current) => current.map((edge) => recomputed.get(edge.id) ?? edge))
  }, [specs])

  const handlePointerDown = useCallback(
    (table: string, event: React.PointerEvent<HTMLDivElement>) => {
      if (!canDrag) return
      // Primary button only, and never from inside something interactive.
      if (event.button !== 0) return

      const element = event.currentTarget
      element.setPointerCapture(event.pointerId)

      dragRef.current = {
        table,
        pointerId: event.pointerId,
        originX: event.clientX,
        originY: event.clientY,
        baseOffset: positionsRef.current[table] ?? ZERO,
      }
      setDragging(table)
    },
    [canDrag],
  )

  const handlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return

      pendingRef.current = {
        x: drag.baseOffset.x + (event.clientX - drag.originX),
        y: drag.baseOffset.y + (event.clientY - drag.originY),
      }

      // Coalesce to one update per frame. A pointer can emit several moves per
      // frame; recomputing on each would be work whose result is discarded
      // before it is ever painted.
      if (frameRef.current === null) {
        frameRef.current = requestAnimationFrame(commitFrame)
      }
    },
    [commitFrame],
  )

  const endDrag = useCallback(
    (event: React.PointerEvent<HTMLDivElement>) => {
      const drag = dragRef.current
      if (!drag || drag.pointerId !== event.pointerId) return

      // Flush any move that arrived after the last painted frame, so the node
      // lands exactly where the pointer left it. There is no release easing to
      // skip under reduced motion because there is none for anyone: the node
      // is already at its final position the moment the pointer stops, and
      // inertia would put it somewhere the user did not point at.
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current)
        frameRef.current = null
      }
      commitFrame()

      dragRef.current = null
      pendingRef.current = null
      setDragging(null)

      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId)
      }
    },
    [commitFrame],
  )

  /**
   * Re-sync the snapshot after a drag ends.
   *
   * Resizes are ignored while a pointer is down, so a window resized mid-drag
   * leaves the cached layout describing the old one — and nothing else would
   * ever correct it, since the next resize measures from an already-wrong
   * baseline. Runs on the commit *after* `dragging` clears, so the node's final
   * transform is in the DOM and `measure` subtracts the right offset back out.
   */
  const wasDraggingRef = useRef(false)
  useEffect(() => {
    if (dragging !== null) {
      wasDraggingRef.current = true
      return
    }
    if (!wasDraggingRef.current) return
    wasDraggingRef.current = false
    measure()
  }, [dragging, measure])

  useEffect(
    () => () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current)
    },
    [],
  )

  return (
    <div ref={containerRef} className={`relative ${className ?? ''}`}>
      {/* Hidden below sm: with one table per row the connectors become long
          vertical runs across unrelated content, which is noise rather than
          information. TableNode states each FK target in text at that width. */}
      <svg
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-10 hidden overflow-visible sm:block"
        width={size.width}
        height={size.height}
      >
        <defs>
          <marker
            id="er-arrow"
            markerWidth="6"
            markerHeight="6"
            refX="5"
            refY="3"
            orient="auto"
          >
            <path d="M0 0 L6 3 L0 6 z" className="fill-accent/60" />
          </marker>
        </defs>
        {/* Self-references are distinguished by dashing, not by hue. They were
            amber, which reads as a warning about a perfectly ordinary
            relationship and spends a status colour on decoration. */}
        {edges.map((edge) => (
          <path
            key={edge.id}
            d={edge.path}
            fill="none"
            strokeWidth={1.5}
            className="stroke-accent/50"
            strokeDasharray={edge.selfReference ? '3 3' : undefined}
            markerEnd={edge.selfReference ? undefined : 'url(#er-arrow)'}
          />
        ))}
      </svg>

      <div className="grid gap-x-16 gap-y-6 sm:grid-cols-2 xl:grid-cols-3">
        {schema.tables.map((table) => {
          const offset = positions[table.name]
          return (
            <TableNode
              key={table.name}
              table={table}
              highlighted={highlightedTable === table.name}
              draggable={canDrag}
              dragging={dragging === table.name}
              offset={offset}
              onPointerDown={(event) => handlePointerDown(table.name, event)}
              onPointerMove={handlePointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
              registerColumn={registerColumn}
              ref={(element) => registerTable(table.name, element)}
            />
          )
        })}
      </div>
    </div>
  )
}
