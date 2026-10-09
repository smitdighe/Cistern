import { describe, expect, it } from 'vitest'

import type { SchemaResponse } from '../../../api/types/schema.types'
import {
  buildEdgeSpecs,
  clampOffset,
  MIN_VISIBLE_PX,
  resolveEdge,
  resolveEdges,
  specsTouching,
  type LayoutSnapshot,
  type Rect,
} from './diagramGeometry'

const rect = (left: number, top: number, width = 200, height = 120): Rect => ({
  left,
  top,
  width,
  height,
})

const schema: SchemaResponse = {
  table_count: 3,
  tables: [
    {
      name: 'orders',
      columns: [{ name: 'customer_id', type: 'integer', nullable: false }],
      primary_key: ['id'],
      foreign_keys: [{ column: 'customer_id', ref_table: 'customers', ref_column: 'id' }],
    },
    {
      name: 'customers',
      columns: [{ name: 'id', type: 'integer', nullable: false }],
      primary_key: ['id'],
      foreign_keys: [],
    },
    {
      name: 'categories',
      columns: [{ name: 'parent_id', type: 'integer', nullable: true }],
      primary_key: ['id'],
      foreign_keys: [{ column: 'parent_id', ref_table: 'categories', ref_column: 'id' }],
    },
  ],
}

function snapshot(overrides: Partial<LayoutSnapshot> = {}): LayoutSnapshot {
  return {
    container: { width: 1000, height: 600 },
    tables: new Map([
      ['orders', rect(0, 0)],
      ['customers', rect(400, 0)],
      ['categories', rect(0, 200)],
    ]),
    columns: new Map([
      ['orders.customer_id', rect(10, 40, 180, 20)],
      ['customers.id', rect(410, 40, 180, 20)],
      ['categories.parent_id', rect(10, 240, 180, 20)],
    ]),
    ...overrides,
  }
}

describe('buildEdgeSpecs', () => {
  it('emits one spec per foreign key and marks self-references', () => {
    const specs = buildEdgeSpecs(schema)

    expect(specs).toHaveLength(2)
    expect(specs.find((s) => s.sourceTable === 'orders')?.selfReference).toBe(false)
    expect(specs.find((s) => s.sourceTable === 'categories')?.selfReference).toBe(true)
  })
})

describe('specsTouching', () => {
  it('returns edges on either end of the named table', () => {
    const specs = buildEdgeSpecs(schema)

    // `customers` owns no foreign key but is the target of one.
    expect(specsTouching(specs, 'customers').map((s) => s.id)).toEqual([
      'orders.customer_id->customers.id',
    ])
    expect(specsTouching(specs, 'orders')).toHaveLength(1)
  })

  it('returns nothing for a table with no relationships', () => {
    expect(specsTouching(buildEdgeSpecs(schema), 'unrelated')).toEqual([])
  })
})

describe('resolveEdge', () => {
  const specs = buildEdgeSpecs(schema)
  const fk = specs.find((s) => !s.selfReference)!
  const self = specs.find((s) => s.selfReference)!

  it('anchors a rightward edge to the source right edge and the target left edge', () => {
    const edge = resolveEdge(fk, snapshot(), {})

    // Source column spans x 10..190, so it leaves at 190; target starts at 400.
    expect(edge?.path).toMatch(/^M 190 50 C /)
    expect(edge?.path).toMatch(/400 16$/)
  })

  it('flips the attachment sides once the target is dragged to the left', () => {
    // Same graph, target moved well left of the source.
    const edge = resolveEdge(fk, snapshot(), { customers: { x: -800, y: 0 } })

    // Now leaves the source's LEFT edge (10) and enters the target's RIGHT
    // edge (400 - 800 + 200 = -200). A fixed side would have produced a curve
    // doubling back across the whole node.
    expect(edge?.path).toMatch(/^M 10 50 C /)
    expect(edge?.path).toMatch(/-200 16$/)
  })

  it('translates both ends by their own table offset', () => {
    const edge = resolveEdge(fk, snapshot(), {
      orders: { x: 5, y: 7 },
      customers: { x: 20, y: 30 },
    })

    expect(edge?.path).toMatch(/^M 195 57 C /)
    expect(edge?.path).toMatch(/420 46$/)
  })

  it('keeps the distinct loop curve for a self-reference', () => {
    const edge = resolveEdge(self, snapshot(), {})

    expect(edge?.selfReference).toBe(true)
    // The loop shape, not a straight run — a line from a row back to its own
    // header would be a few pixels long and invisible.
    expect(edge?.path).toBe('M 190 250 c 26 -6, 26 18, 0 12')
  })

  it('moves a self-reference loop with its own node', () => {
    const edge = resolveEdge(self, snapshot(), { categories: { x: 100, y: -50 } })

    expect(edge?.path).toBe('M 290 200 c 26 -6, 26 18, 0 12')
  })

  it('drops an edge whose target is not in the introspected set', () => {
    const dangling = snapshot()
    dangling.tables.delete('customers')

    expect(resolveEdge(fk, dangling, {})).toBeNull()
    // And the batch resolver simply omits it rather than emitting a hole.
    expect(resolveEdges(specs, dangling, {})).toHaveLength(1)
  })
})

describe('clampOffset', () => {
  const container = { width: 1000, height: 600 }

  it('leaves an in-bounds offset untouched', () => {
    expect(clampOffset(rect(100, 100), container, { x: 20, y: 30 })).toEqual({ x: 20, y: 30 })
  })

  it('keeps a sliver on screen when dragged off the right edge', () => {
    const node = rect(100, 100)
    const { x } = clampOffset(node, container, { x: 99999, y: 0 })

    // The node's left edge may reach at most `container - MIN_VISIBLE`.
    expect(node.left + x).toBe(container.width - MIN_VISIBLE_PX)
  })

  it('keeps a sliver on screen when dragged off the left edge', () => {
    const node = rect(100, 100)
    const { x } = clampOffset(node, container, { x: -99999, y: 0 })

    // The node's right edge may reach at most `MIN_VISIBLE` from the left.
    expect(node.left + x + node.width).toBe(MIN_VISIBLE_PX)
  })

  it('clamps the vertical axis on the same rule', () => {
    const node = rect(100, 100)

    expect(node.top + clampOffset(node, container, { x: 0, y: -99999 }).y + node.height).toBe(
      MIN_VISIBLE_PX,
    )
    expect(node.top + clampOffset(node, container, { x: 0, y: 99999 }).y).toBe(
      container.height - MIN_VISIBLE_PX,
    )
  })

  it('does not restrict a node wider than the canvas', () => {
    // Both constraints are still satisfiable here — an oversized node always
    // leaves far more than the minimum on screen — so it stays freely
    // draggable. Asserted because the obvious reading of "clamp to the canvas"
    // would wrongly pin it.
    const huge = rect(0, 0, 2000, 1200)

    expect(clampOffset(huge, container, { x: 500, y: 500 })).toEqual({ x: 500, y: 500 })
  })

  it('keeps the node on screen when the canvas is narrower than the minimum', () => {
    // The one case where the range inverts: a container smaller than twice
    // MIN_VISIBLE cannot satisfy both edges at once. Falling back to the near
    // edge keeps the node inside rather than letting Math.min/max pick an
    // arbitrary side.
    const tiny = { width: 50, height: 50 }
    const node = rect(0, 0, 20, 20)
    const clamped = clampOffset(node, tiny, { x: 999, y: 999 })

    expect(clamped).toEqual({ x: MIN_VISIBLE_PX - 20, y: MIN_VISIBLE_PX - 20 })
    // Still fully within the canvas.
    expect(node.left + clamped.x + node.width).toBeLessThanOrEqual(tiny.width)
  })
})
