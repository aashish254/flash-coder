import { motion, useReducedMotion } from 'framer-motion'
import { useMemo } from 'react'
import { byId, callersWithin, EDGES, NODES, type GraphNode } from '../lib/graphModel'

const NODE_BASE = '#3f4a55'
const SELECTED = '#35d07f'
const AFFECTED = '#5ac8fa'
/** The canvas sees ~56.9 world units across this box's height and this viewBox
 *  sees 68, so node radii are the WebGL ones scaled by the ratio. */
const SCALE = 1.29
const DEPTH = 30

/** The same 150-symbol scene, drawn without a GPU. WebGL is blocked on remote
 *  desktops, locked-down browsers and older drivers, and the hero's graph is
 *  the one part of this page a reader is allowed to lose — the copy, the
 *  commands and the blast-radius readout are not. */
export function GraphFlat({
  selected,
  onSelect,
}: {
  selected: string | null
  onSelect: (id: string | null) => void
}) {
  const still = useReducedMotion()
  const radius = useMemo(() => (selected ? callersWithin(selected, 2) : null), [selected])

  const tone = (n: GraphNode) =>
    n.id === selected ? SELECTED : radius?.has(n.id) ? AFFECTED : NODE_BASE

  return (
    <motion.svg
      viewBox="-34 -34 68 68"
      className="h-full w-full"
      role="img"
      aria-label="Call graph of the flash package"
      animate={still ? undefined : { y: [0, -0.9, 0] }}
      transition={{ duration: 11, repeat: Infinity, ease: 'easeInOut' }}
      onClick={() => onSelect(null)}
    >
      <g>
        {EDGES.map((e, i) => {
          const a = byId.get(e.s)
          const b = byId.get(e.d)
          if (!a || !b) return null
          const lit =
            !!selected &&
            (e.s === selected ||
              e.d === selected ||
              (!!radius && radius.has(e.s) && radius.has(e.d)))
          return (
            <line
              key={`${e.s}->${e.d}-${i}`}
              x1={a.pos[0]}
              y1={a.pos[1]}
              x2={b.pos[0]}
              y2={b.pos[1]}
              stroke={lit ? AFFECTED : '#2b3642'}
              strokeOpacity={lit ? 0.6 : 0.9}
              strokeWidth={0.08}
            />
          )
        })}
      </g>
      <g>
        {NODES.map((n) => (
          <circle
            key={n.id}
            cx={n.pos[0]}
            cy={n.pos[1]}
            r={
              (n.id === selected
                ? 0.8
                : 0.13 + Math.min(n.degree, 18) * 0.026) * SCALE
            }
            fill={tone(n)}
            // Depth is the only cue a flat projection has left, so nearer
            // symbols sit brighter and the far half of the sphere recedes.
            fillOpacity={0.4 + 0.6 * ((n.pos[2] + DEPTH) / (2 * DEPTH))}
            className="cursor-pointer"
            onClick={(ev) => {
              ev.stopPropagation()
              onSelect(n.id === selected ? null : n.id)
            }}
          />
        ))}
      </g>
    </motion.svg>
  )
}
