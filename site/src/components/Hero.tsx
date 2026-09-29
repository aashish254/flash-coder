import { motion, useReducedMotion } from 'framer-motion'
import { ArrowRight, GitBranch } from 'lucide-react'
import { Suspense, lazy, useState } from 'react'
import { benchmarks, captures, int, ms } from '../lib/data'
import { nodeById, radiusOf, sceneStats } from '../lib/graphModel'
import { COMMANDS } from '../lib/site'
import { wantsCanvas } from '../lib/webgl'
import { GraphFlat } from './GraphFlat'
import { Boundary, CopyLine } from './ui'

// WebGL is the last thing a reader needs, so three.js is a dynamic import that
// only the machines able to draw it well ever pay to download.
const GraphScene = lazy(() => import('./GraphScene'))

const EASE = [0.16, 1, 0.3, 1] as const

/** A terminal cursor is the only ornament a landing page for a CLI needs. */
function TypedLine({ text, delay }: { text: string; delay: number }) {
  const still = useReducedMotion()
  return (
    <motion.span
      initial={{ opacity: still ? 1 : 0 }}
      animate={{ opacity: 1 }}
      transition={{ delay: still ? 0 : delay, duration: 0.35 }}
    >
      {text}
    </motion.span>
  )
}

export function Hero() {
  const [selected, setSelected] = useState<string | null>(null)
  const node = nodeById(selected)
  const radius = radiusOf(selected)
  const totals = benchmarks.totals
  const doctor = captures[0]
  const canvas = wantsCanvas()

  return (
    <section className="relative overflow-hidden border-b border-line">
      <div className="pointer-events-none absolute inset-0 grid-field opacity-70" />
      <div className="absolute inset-y-0 right-0 w-full opacity-60 lg:w-[58%] lg:opacity-100">
        {canvas ? (
          <Boundary fallback={<GraphFlat selected={selected} onSelect={setSelected} />}>
            <Suspense fallback={null}>
              <GraphScene selected={selected} onSelect={setSelected} />
            </Suspense>
          </Boundary>
        ) : (
          <GraphFlat selected={selected} onSelect={setSelected} />
        )}
      </div>

      <div className="relative mx-auto flex min-h-[100svh] w-full max-w-6xl flex-col justify-center gap-8 px-5 pb-10 pt-24 md:px-8 lg:min-h-[92vh]">
        <div className="max-w-[620px]">
          <p className="num flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] uppercase tracking-[0.2em] text-fog-2">
            <span className="text-pass">●</span> offline
            <span className="text-line">/</span>
            <span>apple silicon</span>
            <span className="text-line">/</span>
            <span>no api key</span>
          </p>

          <h1 className="mt-4 text-[34px] font-medium leading-[1.06] tracking-[-0.025em] text-balance text-fog-0 md:text-[52px]">
            <TypedLine text="It doesn’t claim it fixed your bug." delay={0.05} />
            <br />
            <span className="text-fog-1">
              <TypedLine text="It shows you the assert that used to fail." delay={0.2} />
            </span>
          </h1>

          <p className="mt-6 max-w-[58ch] text-[15px] leading-relaxed text-fog-1 md:text-base">
            Flash Coder is a coding agent that runs entirely on one Mac. It writes
            the change, executes the test, reads <em>which assert failed and what
            the value actually was</em>, and tries again — no API key, no
            container, nothing leaving the machine. And the verification battery it
            holds itself to is a command you can run before you trust a word of
            this page.
          </p>

          <div className="mt-8 flex max-w-[640px] flex-col gap-2">
            <CopyLine cmd={COMMANDS.battery} label="verify" />
            <div className="flex flex-wrap items-center gap-3 pt-1">
              <a
                href="#install"
                className="group inline-flex items-center gap-2 border border-pass/50 px-3.5 py-2 text-[13px] text-pass transition-colors hover:bg-pass hover:text-ink-0"
              >
                Install in five commands
                <ArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
              </a>
              <a
                href="#numbers"
                className="inline-flex items-center gap-2 text-[13px] text-fog-1 underline decoration-line decoration-1 underline-offset-4 transition-colors hover:text-fog-0"
              >
                The numbers, with their provenance
              </a>
            </div>
          </div>
        </div>

        {/* The readout. Left empty by design until a node is picked, so the
            first thing a reader sees is a real `flash doctor` line, not copy. */}
        <div className="max-w-[640px] lg:max-w-[560px]">
          <div className="term min-h-[132px] p-3.5">
            {node ? (
              <div className="flex flex-col gap-1.5">
                <p className="num text-[13px] text-pass">{node.symbol}</p>
                <p className="num text-[12px] text-fog-2">
                  {node.file} · {node.kind} · {node.degree} edge(s)
                </p>
                <p className="mt-1 text-[13px] leading-relaxed text-fog-1">
                  <span className="num text-signal">{radius}</span> symbol
                  {radius === 1 ? '' : 's'} in this scene reach it within two hops.
                  On your machine that is{' '}
                  <span className="num text-fog-0">flash graph {node.label}</span>,
                  answering in {ms(benchmarks.graph.fixture_ms)} of a{' '}
                  {int(benchmarks.graph.budget_ms)} ms budget.
                </p>
              </div>
            ) : (
              <div className="flex flex-col gap-1.5">
                <p className="num text-fog-2">
                  <span className="text-pass">$ </span>
                  {doctor?.command.replace('python -m ', '')}
                </p>
                {doctor?.lines.slice(1, 5).map((l) => (
                  <p key={l} className="num whitespace-pre text-[12px] text-fog-1">
                    {l}
                  </p>
                ))}
                <p className="mt-1.5 text-[12px] leading-relaxed text-fog-2">
                  Nine answers about the install, exit code following its own page.
                  This scene is that install’s own call graph —{' '}
                  <span className="num text-fog-1">
                    {int(sceneStats.sampled.shown_nodes)}
                  </span>{' '}
                  of{' '}
                  <span className="num text-fog-1">
                    {int(sceneStats.sampled.repo_nodes)}
                  </span>{' '}
                  indexed symbols, extracted by{' '}
                  <span className="num">flash/graph.py</span>.
                </p>
              </div>
            )}
          </div>
          <p className="num mt-2 flex items-center gap-2 text-[11px] text-fog-2">
            <GitBranch size={12} className="text-signal" />
            {node
              ? 'click the node again, or anywhere empty, to clear it'
              : 'click any node — it is the question the graph answers'}
          </p>
        </div>

        <dl className="grid max-w-[760px] grid-cols-2 gap-x-8 gap-y-4 border-t border-line pt-5 sm:grid-cols-4">
          {[
            { k: int(totals.total), l: 'offline claims, printed' },
            { k: int(totals.mutants), l: 'mutation gates' },
            { k: String(totals.vectors), l: 'vectors in §6' },
            { k: '0', l: 'api keys, anywhere' },
          ].map((f) => (
            <div key={f.l}>
              <dt className="num text-2xl text-fog-0 md:text-[28px]">{f.k}</dt>
              <dd className="mt-1 text-[11px] uppercase tracking-[0.12em] text-fog-2">
                {f.l}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <motion.div
        className="pointer-events-none absolute inset-x-0 bottom-0 hidden h-24 bg-gradient-to-t from-ink-0 to-transparent lg:block"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 1.2, delay: 0.4, ease: EASE }}
      />
    </section>
  )
}
