import rawBenchmarks from '../data/benchmarks.json'
import rawGraph from '../data/graph.json'
import rawTranscripts from '../data/transcripts.json'

/**
 * The page reads three generated files and nothing else. They are written by
 * `python benchmarks/export_site_data.py` from `benchmarks/results/dashboard_data.json`,
 * which is written by a run. A number that is not in this JSON cannot appear on
 * screen, and there is no hand-edited copy to drift.
 */

export type Vector = {
  command: string
  checks: number
  expected: number
  mutants: number
}

export type Timing = {
  command: string
  median_s: number
  min_s: number
  max_s: number
  repeats: number
}

export type Capture = {
  command: string
  exit: number
  lines: string[]
  redacted: string[]
  source?: string
}

export type GraphNode = {
  id: string
  file: string
  symbol: string
  kind: string
  degree: number
  label: string
  pos: [number, number, number]
}

export type GraphEdge = { s: string; d: string; kind: string }

export type MarketArm = {
  arm: string
  passed: number
  tasks: number
  seconds_per_task: number
  requests: number
  tokens: number
}

export const benchmarks = rawBenchmarks as unknown as {
  witness: string
  totals: {
    checks: number
    oracle: number
    total: number
    mutants: number
    vectors: number
    mutant_vectors: number
  }
  distribution: { bucket: string; vectors: number }[]
  vectors: Vector[]
  timings: Timing[]
  graph: {
    measured: boolean
    fixture_ms: number
    budget_ms: number
    wide_ms: number
    wide_nodes: number
    wide_edges: number
    build_ms: number
    warm_ms: number
  }
  market: {
    witness: string
    suite: string
    tasks: number
    grader_check: string
    arms: MarketArm[]
    note: string
  }
  provenance: Record<string, string>
}

export const graphScene = rawGraph as unknown as {
  nodes: GraphNode[]
  edges: GraphEdge[]
  kinds: Record<string, number>
  sampled: {
    shown_nodes: number
    shown_edges: number
    repo_nodes: number
    repo_edges: number
    note: string
  }
}

export const captures = (rawTranscripts as unknown as { captures: Capture[] })
  .captures

/** The full offline re-read. Timed by the witness file's own birth and mtime —
 *  the battery prints its totals but not its clock — and written into the docs
 *  beside it, so the two never disagree about which run this is. */
export const BATTERY_WALL = { minutes: 16, seconds: 44, date: '2026-09-29' }

export const int = (n: number) => n.toLocaleString('en-US')
export const sec = (n: number) => `${n.toFixed(n < 10 ? 2 : 1)}s`
export const ms = (n: number) => `${n.toFixed(n < 10 ? 1 : 0)} ms`
