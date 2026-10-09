import { useMemo } from 'react'
import { motion } from 'framer-motion'

import type { SchemaResponse } from '../../api/types/schema.types'
import { cn } from '../../lib/cn'

export type GridDensity = 'low' | 'medium' | 'high'

export interface BackgroundGridProps {
  density?: GridDensity
  /**
   * Real schema, once loaded. When absent — still loading, or /schema failed —
   * the decorative fallback is used instead of an empty background.
   */
  schema?: SchemaResponse | null
  className?: string
}

const NODE_COUNT: Record<GridDensity, number> = {
  low: 18,
  medium: 32,
  high: 52,
}

const VIEW_W = 1000
const VIEW_H = 600
/** Edges are only drawn between decorative nodes closer than this, so the field stays sparse. */
const LINK_RADIUS = 190

/** Deterministic PRNG — the layout must not reshuffle on every render. */
function mulberry32(seed: number) {
  return () => {
    seed |= 0
    seed = (seed + 0x6d2b79f5) | 0
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

interface Node {
  x: number
  y: number
  r: number
}

interface Graph {
  nodes: Node[]
  edges: [Node, Node][]
  /**
   * Points where real foreign keys land.
   *
   * Only ever populated from the introspected schema — the decorative fallback
   * returns none. The glow is meant to say "a relationship is anchored here",
   * so putting one on a random decorative vertex would be inventing a fact
   * about the database.
   */
  junctions: Node[]
}

/** Purely decorative field. Used until the real schema arrives. */
function buildDecorativeGraph(count: number): Graph {
  const rand = mulberry32(0x0c157e42)
  const nodes: Node[] = Array.from({ length: count }, () => ({
    x: rand() * VIEW_W,
    y: rand() * VIEW_H,
    r: 1.5 + rand() * 2,
  }))

  const edges: [Node, Node][] = []
  for (let i = 0; i < nodes.length; i++) {
    for (let j = i + 1; j < nodes.length; j++) {
      const dx = nodes[i].x - nodes[j].x
      const dy = nodes[i].y - nodes[j].y
      if (Math.hypot(dx, dy) < LINK_RADIUS) {
        edges.push([nodes[i], nodes[j]])
      }
    }
  }

  return { nodes, edges, junctions: [] }
}

/**
 * Derive the field from the actual schema: one node per table, sized by column
 * count, one edge per foreign key.
 *
 * Tables are laid out on an ellipse rather than a force simulation — with five
 * tables a solver would be more code and a less stable picture, and this is
 * background texture, not a diagram anyone reads.
 */
function buildSchemaGraph(schema: SchemaResponse): Graph {
  const tables = schema.tables
  if (tables.length === 0) return { nodes: [], edges: [], junctions: [] }

  const cx = VIEW_W / 2
  const cy = VIEW_H / 2
  const rx = VIEW_W * 0.34
  const ry = VIEW_H * 0.32

  const byName = new Map<string, Node>()
  const nodes: Node[] = tables.map((table, index) => {
    // Offset by a quarter turn so a single table does not sit on the edge.
    const angle = (index / tables.length) * Math.PI * 2 - Math.PI / 2
    const node: Node = {
      x: cx + Math.cos(angle) * rx,
      y: cy + Math.sin(angle) * ry,
      r: 2 + Math.min(table.columns.length, 12) * 0.35,
    }
    byName.set(table.name, node)
    return node
  })

  const edges: [Node, Node][] = []
  // A Set, so a table referenced by three foreign keys still glows once.
  const junctions = new Set<Node>()

  for (const table of tables) {
    const from = byName.get(table.name)
    if (!from) continue
    for (const fk of table.foreign_keys) {
      const to = byName.get(fk.ref_table)
      // A FK can point outside the introspected set (another schema, or a
      // table filtered out of the generator's view). Skip rather than crash.
      if (!to || to === from) continue
      edges.push([from, to])
      // Both ends of a real relationship. These are the only points that get a
      // glow, which is what ties the ambient field to the actual schema rather
      // than to a pattern that merely looks like one.
      junctions.add(from)
      junctions.add(to)
    }
  }

  return { nodes, edges, junctions: [...junctions] }
}

/**
 * Ambient node/line field behind the app chrome.
 *
 * Decorative and non-interactive either way — it is kept out of the
 * accessibility tree because the real schema is presented properly on /schema.
 * Drift is deliberately near-imperceptible: this must not compete with content.
 */
export function BackgroundGrid({ density = 'medium', schema, className }: BackgroundGridProps) {
  const { nodes, edges, junctions } = useMemo(() => {
    // An empty schema still means "loaded and there is nothing" — but an empty
    // background reads as broken, so fall back to the decorative field.
    if (schema && schema.tables.length > 0) return buildSchemaGraph(schema)
    return buildDecorativeGraph(NODE_COUNT[density])
  }, [density, schema])

  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="xMidYMid slice"
      className={cn('pointer-events-none absolute inset-0 size-full', className)}
    >
      {/* 60s for a 7px excursion — drift you notice only if you stare. Stops
          entirely under reduced motion via the app-wide MotionConfig in
          main.tsx (Framer Motion's own default is `reducedMotion: "never"`,
          so that wrapper is doing real work here). */}
      <defs>
        {/* Soft falloff, so a junction reads as light rather than as a disc.
            A radial gradient rather than a blur filter: same look, none of the
            per-frame filter cost while the whole group is drifting. */}
        <radialGradient id="grid-junction">
          <stop offset="0%" stopColor="hsl(var(--accent))" stopOpacity={0.5} />
          <stop offset="45%" stopColor="hsl(var(--accent))" stopOpacity={0.14} />
          <stop offset="100%" stopColor="hsl(var(--accent))" stopOpacity={0} />
        </radialGradient>
      </defs>

      <motion.g
        animate={{ x: [0, 7, 0, -7, 0], y: [0, -5, 0, 5, 0] }}
        transition={{ duration: 60, repeat: Infinity, ease: 'easeInOut' }}
      >
        {/* Behind the lines and nodes: a glow is light falling on the graph,
            not another object drawn on top of it. */}
        <g>
          {junctions.map((n, i) => (
            <motion.circle
              key={i}
              cx={n.x}
              cy={n.y}
              r={n.r * 6}
              fill="url(#grid-junction)"
              // Nine seconds a cycle, and never brighter than a third of a
              // token that is already at 20% — near the threshold of notice.
              // Anything faster or stronger starts competing with the pipeline
              // trace, which is the only thing on the page allowed to pull the
              // eye. Staggered so the field breathes rather than blinks.
              animate={{ opacity: [0.25, 0.75, 0.25] }}
              transition={{
                duration: 9,
                repeat: Infinity,
                ease: 'easeInOut',
                delay: i * 1.4,
              }}
            />
          ))}
        </g>
        <g className="stroke-accent/10">
          {edges.map(([a, b], i) => (
            <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={1} />
          ))}
        </g>
        <g className="fill-accent/20">
          {nodes.map((n, i) => (
            <circle key={i} cx={n.x} cy={n.y} r={n.r} />
          ))}
        </g>
      </motion.g>
    </svg>
  )
}
