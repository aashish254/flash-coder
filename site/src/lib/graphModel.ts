import { graphScene, type GraphNode } from './data'

/** The hero scene is the repo's own AST call graph, exported by
 *  `benchmarks/export_site_data.py`. This module is deliberately free of three
 *  and React so the graph maths can load eagerly while the WebGL canvas behind
 *  it stays a dynamic import. */
export type { GraphNode }
export const NODES = graphScene.nodes
export const EDGES = graphScene.edges

export const byId = new Map(NODES.map((n) => [n.id, n]))

const CALLERS = new Map<string, string[]>()
for (const e of EDGES) {
  const list = CALLERS.get(e.d)
  if (list) list.push(e.s)
  else CALLERS.set(e.d, [e.s])
}

/** A blast radius is a direction: `flash graph SYM` answers "who reaches this",
 *  so this walks callers, transitively, to `depth`. */
export function callersWithin(start: string, depth = 2) {
  const found = new Map<string, number>()
  let frontier = [start]
  for (let d = 1; d <= depth; d++) {
    const next: string[] = []
    for (const id of frontier) {
      for (const src of CALLERS.get(id) ?? []) {
        if (!found.has(src) && src !== start) {
          found.set(src, d)
          next.push(src)
        }
      }
    }
    frontier = next
  }
  return found
}

export function radiusOf(selected: string | null) {
  return selected ? callersWithin(selected).size : 0
}

export function nodeById(id: string | null): GraphNode | null {
  return id ? (byId.get(id) ?? null) : null
}

export const sceneStats = {
  nodes: NODES.length,
  edges: EDGES.length,
  kinds: graphScene.kinds,
  sampled: graphScene.sampled,
}
