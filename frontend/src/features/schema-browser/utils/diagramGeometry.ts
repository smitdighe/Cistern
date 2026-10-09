import type { SchemaResponse } from '../../../api/types/schema.types'

/**
 * Connector geometry for the ER diagram, as pure functions.
 *
 * Everything here works on a *snapshot* of measured layout rects plus a map of
 * drag offsets — it never touches the DOM. That is the whole point: during a
 * drag the connector paths have to be recomputed every frame, and calling
 * `getBoundingClientRect` per frame on five tables and their columns forces a
 * synchronous layout each time. Measuring once when the layout actually
 * changes, then doing arithmetic on the result, keeps a drag at pointer speed.
 */

export interface Point {
  x: number
  y: number
}

export interface Rect {
  left: number
  top: number
  width: number
  height: number
}

export interface Size {
  width: number
  height: number
}

/**
 * Measured positions with drag offsets removed, in container coordinates.
 *
 * "Layout" means where the CSS grid put things. A node's live position is
 * always `layout + offset`, so an offset of zero is exactly the grid slot —
 * which is what makes the initial state seed itself for free and survive a
 * breakpoint change.
 */
export interface LayoutSnapshot {
  container: Size
  tables: Map<string, Rect>
  /** Keyed `${table}.${column}`. */
  columns: Map<string, Rect>
}

/** A connector, derived from the schema alone. Independent of any position. */
export interface EdgeSpec {
  id: string
  sourceTable: string
  /** `${table}.${column}` — the row the line leaves from. */
  columnKey: string
  targetTable: string
  selfReference: boolean
}

export interface Edge {
  id: string
  path: string
  selfReference: boolean
}

/** Vertical anchor inside the target node: the header band, not its top edge. */
const TARGET_ANCHOR_Y = 16

/** Minimum horizontal control-point reach, so short hops still read as curves. */
const MIN_BEND = 28

/** How much of a node must stay on-canvas, in px, on every edge. */
export const MIN_VISIBLE_PX = 40

export const ZERO: Point = { x: 0, y: 0 }

/**
 * Every foreign key in the schema, as a connector spec.
 *
 * Derived once per schema. A FK can reference a table outside the introspected
 * set — the backend filters `query_log` and `benchmark_results` out of the
 * generator's view — so specs are emitted optimistically here and dropped at
 * resolve time when the target turns out not to be on screen.
 */
export function buildEdgeSpecs(schema: SchemaResponse): EdgeSpec[] {
  const specs: EdgeSpec[] = []

  for (const table of schema.tables) {
    for (const fk of table.foreign_keys) {
      const selfReference = fk.ref_table === table.name
      specs.push({
        id: selfReference
          ? `${table.name}.${fk.column}->self`
          : `${table.name}.${fk.column}->${fk.ref_table}.${fk.ref_column}`,
        sourceTable: table.name,
        columnKey: `${table.name}.${fk.column}`,
        targetTable: fk.ref_table,
        selfReference,
      })
    }
  }

  return specs
}

function live(rect: Rect, offset: Point | undefined): Rect {
  if (!offset) return rect
  return { ...rect, left: rect.left + offset.x, top: rect.top + offset.y }
}

/**
 * Resolve one spec to an SVG path, or null when either end is not on screen.
 *
 * The attachment side is chosen from the *current* relative positions rather
 * than fixed to right-edge-out / left-edge-in. Once nodes can be dragged
 * anywhere, a fixed side means a target sitting to the left of its source gets
 * a curve that leaves rightward, doubles back across the whole node and enters
 * from the left — legible as a bug, not as a relationship.
 */
export function resolveEdge(
  spec: EdgeSpec,
  snapshot: LayoutSnapshot,
  positions: Record<string, Point>,
): Edge | null {
  const columnBase = snapshot.columns.get(spec.columnKey)
  const targetBase = snapshot.tables.get(spec.targetTable)
  if (!columnBase || !targetBase) return null

  const source = live(columnBase, positions[spec.sourceTable])

  if (spec.selfReference) {
    // A loop off the right edge. A straight line from a row back to its own
    // header would be a few pixels long and invisible; the loop is the shape
    // that says "points at itself" before any label is read.
    const x1 = source.left + source.width
    const y1 = source.top + source.height / 2
    return {
      id: spec.id,
      path: `M ${x1} ${y1} c 26 -6, 26 18, 0 12`,
      selfReference: true,
    }
  }

  const target = live(targetBase, positions[spec.targetTable])

  const targetIsLeft = target.left + target.width / 2 < source.left + source.width / 2
  const direction = targetIsLeft ? -1 : 1

  const x1 = targetIsLeft ? source.left : source.left + source.width
  const y1 = source.top + source.height / 2
  const x2 = targetIsLeft ? target.left + target.width : target.left
  const y2 = target.top + TARGET_ANCHOR_Y

  // Horizontal control points keep the curve reading as a run between two
  // sides even when the target sits well above or below the source.
  const bend = Math.max(MIN_BEND, Math.abs(x2 - x1) / 2)

  return {
    id: spec.id,
    path: `M ${x1} ${y1} C ${x1 + direction * bend} ${y1}, ${x2 - direction * bend} ${y2}, ${x2} ${y2}`,
    selfReference: false,
  }
}

/** Resolve every spec. Used on mount and whenever the layout itself changes. */
export function resolveEdges(
  specs: EdgeSpec[],
  snapshot: LayoutSnapshot,
  positions: Record<string, Point>,
): Edge[] {
  const edges: Edge[] = []
  for (const spec of specs) {
    const edge = resolveEdge(spec, snapshot, positions)
    if (edge) edges.push(edge)
  }
  return edges
}

/**
 * Specs with an end on `tableName` — the only ones a drag of that node can
 * move. Everything else keeps the path it already had, so dragging one node in
 * a large schema costs work proportional to that node's own degree rather than
 * to the size of the whole graph.
 */
export function specsTouching(specs: EdgeSpec[], tableName: string): EdgeSpec[] {
  return specs.filter(
    (spec) => spec.sourceTable === tableName || spec.targetTable === tableName,
  )
}

/**
 * Constrain an offset so at least `MIN_VISIBLE_PX` of the node stays inside the
 * canvas on every edge — a node dragged fully off-canvas would be unreachable,
 * with no scrollback and (by design) no persistence to recover it from.
 */
export function clampOffset(layout: Rect, container: Size, desired: Point): Point {
  return {
    x: clampAxis(desired.x, layout.left, layout.width, container.width),
    y: clampAxis(desired.y, layout.top, layout.height, container.height),
  }
}

function clampAxis(desired: number, layoutStart: number, extent: number, bound: number): number {
  // Far edge must not cross the near boundary, and vice versa.
  const min = MIN_VISIBLE_PX - extent - layoutStart
  const max = bound - MIN_VISIBLE_PX - layoutStart

  // A node wider than the canvas inverts the range. Pinning to the near edge
  // is the sane resolution: it keeps the node's start visible.
  if (min > max) return min

  return Math.min(Math.max(desired, min), max)
}
