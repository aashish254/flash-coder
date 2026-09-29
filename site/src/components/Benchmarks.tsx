import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ReactNode } from 'react'
import { BATTERY_WALL, benchmarks, int, ms, sec } from '../lib/data'
import { Reveal, Section, SectionHead } from './ui'

const AXIS = { stroke: '#6d7a85', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }
const GREEN = '#35d07f'
const LINE = '#263039'

function Panel({
  n,
  title,
  body,
  source,
  children,
  height = 300,
  wide,
}: {
  n: string
  title: string
  body: string
  source: string
  children: ReactNode
  height?: number
  wide?: boolean
}) {
  return (
    <Reveal className={wide ? 'lg:col-span-2' : undefined}>
      <figure className="card h-full p-5">
        <figcaption className="mb-1 flex items-baseline gap-3">
          <span className="num text-[11px] tracking-[0.16em] text-fog-2">{n}</span>
          <span className="text-[15px] font-medium text-fog-0">{title}</span>
        </figcaption>
        <p className="mb-4 max-w-[70ch] text-[13px] leading-relaxed text-fog-1">{body}</p>
        <div style={{ height }}>{children}</div>
        <p className="num mt-3 border-t border-line pt-2.5 text-[11px] leading-relaxed text-fog-2">
          {source}
        </p>
      </figure>
    </Reveal>
  )
}

type Row = Record<string, string | number>

const tip = ({
  active,
  payload,
  fmt,
}: {
  active?: boolean
  payload?: readonly { payload?: unknown }[]
  fmt: (p: Row) => string
}) => {
  const row = payload?.[0]?.payload as Row | undefined
  return active && row ? (
    <div className="num border border-line bg-ink-0 px-2.5 py-1.5 text-[11.5px] text-fog-0 shadow-none">
      {fmt(row)}
    </div>
  ) : null
}

export function Benchmarks() {
  const timings = benchmarks.timings.map((t) => ({
    name: t.command.replace('python -m flash.', '').replace(' --selftest', ''),
    median_s: t.median_s,
    lo: t.min_s,
    hi: t.max_s,
    command: t.command,
  }))

  let running = 0
  const cumulative = benchmarks.vectors.map((v, i) => {
    running += v.checks
    return {
      i: i + 1,
      cum: running,
      checks: v.checks,
      mutants: v.mutants,
      command: v.command,
    }
  })

  const g = benchmarks.graph

  /** Panel 3.2's prose quoted 33 vectors while the battery carried 36, so the
   *  two figures it states as counts are read off the same arrays the chart
   *  draws: how many vectors the battery printed, and how many of them are
   *  needed to reach half of the checks. */
  const N_VECTORS = benchmarks.vectors.length
  const totalChecks = cumulative[cumulative.length - 1]?.cum ?? 0
  const halfAt = cumulative.findIndex((c) => c.cum >= totalChecks / 2) + 1
  const axisTicks = [1, 6, 12, 18, 24, 30, N_VECTORS].filter(
    (t, i, a) => t <= N_VECTORS && a.indexOf(t) === i,
  )

  return (
    <Section id="benchmarks" className="border-t border-line">
      <SectionHead
        index="03"
        title="Five panels, each one traceable to a command"
        lede={
          <>
            These are the measurements that exist. Wall clock of the offline
            battery’s parts, the mass of the battery itself, R-1.3’s latency
            clause split into what it answers and what it costs to build, and one
            cross-tool run against another agent on the same weights. The
            generator is{' '}
            <span className="num text-fog-0">benchmarks/dashboard_data.py</span>; the
            page imports its JSON and adds nothing.
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Panel
          n="3.1"
          title="Every module selftest, wall clock"
          body="Median of three runs of `python -m flash.<module> --selftest` on a 32 GB M-series box, with the min–max spread in the tooltip. Grammar, tourney and ambient dominate, and that is the honest shape of a battery that parses real Python and replays real traces."
          source={`python benchmarks/dashboard_data.py --repeats 3  ·  n=${timings[0] ? benchmarks.timings[0].repeats : 0} per bar`}
          height={400}
          wide
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={timings} layout="vertical" margin={{ left: 4, right: 16 }}>
              <CartesianGrid horizontal={false} stroke={LINE} strokeDasharray="2 4" />
              <XAxis
                type="number"
                tick={AXIS}
                tickLine={false}
                axisLine={{ stroke: LINE }}
                unit="s"
              />
              <YAxis
                type="category"
                dataKey="name"
                width={78}
                tick={AXIS}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                cursor={{ fill: '#141a21' }}
                content={({ active, payload }) =>
                  tip({
                    active,
                    payload,
                    fmt: (p) =>
                      `${p.command} — ${sec(Number(p.median_s))} (spread ${Number(p.lo).toFixed(2)}–${Number(p.hi).toFixed(2)}s)`,
                  })
                }
              />
              {/* Every bar here is a run that said yes, so every bar is green;
                  the length is the only thing allowed to vary. */}
              <Bar dataKey="median_s" radius={[0, 1, 1, 0]} fill={GREEN} fillOpacity={0.8} isAnimationActive />
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        <Panel
          n="3.2"
          title="Where the battery’s mass actually is"
          body={`Cumulative checks across the ${N_VECTORS} §6 vectors, largest first. ${halfAt} of them carry half the total; the tail is where the small, sharp gates live — the documented-command checker is eight checks, and it has caught two shipped defects.`}
          source="python benchmarks/battery_reread.py  ·  read from the committed print"
          height={300}
          wide
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={cumulative} margin={{ left: 4, right: 16, top: 8 }}>
              <defs>
                <linearGradient id="mass" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={GREEN} stopOpacity={0.32} />
                  <stop offset="100%" stopColor={GREEN} stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke={LINE} strokeDasharray="2 4" />
              <XAxis
                dataKey="i"
                tick={AXIS}
                tickLine={false}
                axisLine={{ stroke: LINE }}
                ticks={axisTicks}
              />
              <YAxis tick={AXIS} tickLine={false} axisLine={false} width={44} />
              <Tooltip
                cursor={{ stroke: LINE }}
                content={({ active, payload }) =>
                  tip({
                    active,
                    payload,
                    fmt: (p) =>
                      `vector #${p.i}: ${p.command} — ${p.checks} checks, ${p.mutants} mutants · ${p.cum} cumulative`,
                  })
                }
              />
              <Area
                type="stepAfter"
                dataKey="cum"
                stroke={GREEN}
                strokeWidth={1.5}
                fill="url(#mass)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel
          n="3.3"
          title="R-1.3’s blast-radius query against its own budget"
          body="The spec clause says a symbol query answers in under 200 ms. Measured: the clause's own five-file fixture repo, and a generated 401-file / 5,377-node repo that exists precisely because five files cannot bound a budget. Both are inside it, and the page prints the instrument size beside the number rather than the number alone."
          source="python -m flash.graph --selftest  ·  the timing line it prints"
          height={280}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={[
                { name: '5-file fixture', ms: g.fixture_ms },
                { name: '401-file, 5,377 nodes', ms: g.wide_ms },
              ]}
              layout="vertical"
              margin={{ left: 4, right: 16 }}
            >
              <CartesianGrid horizontal={false} stroke={LINE} strokeDasharray="2 4" />
              <XAxis type="number" tick={AXIS} tickLine={false} axisLine={{ stroke: LINE }} unit="ms" />
              <YAxis
                type="category"
                dataKey="name"
                width={132}
                tick={AXIS}
                tickLine={false}
                axisLine={false}
              />
              <ReferenceLine x={g.budget_ms} stroke="#f0b429" label={{ value: '200 ms budget', position: 'right', ...AXIS, stroke: '#f0b429' }} />
              <Tooltip
                cursor={{ fill: '#141a21' }}
                content={({ active, payload }) =>
                  tip({
                    active,
                    payload,
                    fmt: (p) => `${p.name}: ${ms(Number(p.ms))} of ${int(Number(g.budget_ms))} ms`,
                  })
                }
              />
              <Bar dataKey="ms" radius={[0, 1, 1, 0]} fill={GREEN} fillOpacity={0.85} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>

        <Panel
          n="3.4"
          title="Index cost, separated from answer cost"
          body="A cold full pass builds the index for the wide instrument; a warm pass merges and re-extracts nothing. Quoting the query time without the build time would be the same trick as a latency number with no denominator, so both are on the same axis."
          source="python -m flash.graph --selftest  ·  printed apart, deliberately"
          height={280}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={[
                { name: 'warm merge', ms: g.warm_ms },
                { name: 'wide query', ms: g.wide_ms },
                { name: 'cold build', ms: g.build_ms },
              ]}
              layout="vertical"
              margin={{ left: 4, right: 16 }}
            >
              <CartesianGrid horizontal={false} stroke={LINE} strokeDasharray="2 4" />
              <XAxis
                type="number"
                tick={AXIS}
                tickLine={false}
                axisLine={{ stroke: LINE }}
                unit="ms"
                scale="log"
                domain={[1, 'dataMax']}
              />
              <YAxis
                type="category"
                dataKey="name"
                width={94}
                tick={AXIS}
                tickLine={false}
                axisLine={false}
              />
              <Tooltip
                cursor={{ fill: '#141a21' }}
                content={({ active, payload }) =>
                  tip({ active, payload, fmt: (p) => `${p.name}: ${ms(Number(p.ms))}` })
                }
              />
              <Bar dataKey="ms" radius={[0, 1, 1, 0]} fill={GREEN} fillOpacity={0.8} />
            </BarChart>
          </ResponsiveContainer>
        </Panel>
      </div>

      <Reveal delay={0.05}>
        <div className="mt-6 card p-5">
          <h3 className="text-[15px] font-medium text-fog-0">
            All {int(benchmarks.totals.vectors)} vectors, as printed
          </h3>
          <p className="mt-1 text-[13px] text-fog-1">
            Bar length is checks. A mutant column of zero means the vector asserts
            behaviour without a planted-bug gate — stated, not hidden.
          </p>
          <div className="mt-4 max-h-[420px] overflow-y-auto">
            <table className="w-full border-collapse text-left">
              <thead className="sticky top-0 bg-ink-1 text-[11px] uppercase tracking-[0.12em] text-fog-2">
                <tr>
                  <th className="py-2 pr-3 font-normal">command</th>
                  <th className="py-2 pr-3 font-normal">checks</th>
                  <th className="w-[38%] py-2 pr-3 font-normal" />
                  <th className="py-2 text-right font-normal">mutants</th>
                </tr>
              </thead>
              <tbody>
                {benchmarks.vectors.map((v) => (
                  <tr key={v.command} className="border-t border-line/60">
                    <td className="num py-1.5 pr-3 text-[12px] text-fog-1">{v.command}</td>
                    <td className="num py-1.5 pr-3 text-[12px] text-fog-0">
                      {v.checks}/{v.expected}
                    </td>
                    <td className="py-1.5 pr-3">
                      <span
                        className="block h-[3px] bg-pass/70"
                        style={{
                          width: `${Math.max(2, (v.checks / benchmarks.vectors[0].checks) * 100)}%`,
                        }}
                      />
                    </td>
                    <td
                      className={`num py-1.5 text-right text-[12px] ${v.mutants ? 'text-signal' : 'text-fog-2'}`}
                    >
                      {v.mutants}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </Reveal>

      <Reveal delay={0.1}>
        <div className="mt-6 card p-5">
          <h3 className="text-[15px] font-medium text-fog-0">
            <span className="num mr-3 text-[11px] tracking-[0.16em] text-fog-2">
              PANEL 3.5
            </span>{' '}
            The one cross-tool run this machine has made
          </h3>
          <p className="mt-1 max-w-[76ch] text-[13px] leading-relaxed text-fog-1">
            {benchmarks.market.tasks} held-out tasks, one model behind every row and
            one oracle grading every answer — the same{' '}
            <span className="num text-fog-0">flash.harness.run_test</span> that
            produced the {int(benchmarks.totals.oracle)} published oracle figures, and
            it self-checks first:{' '}
            <span className="num text-fog-0">{benchmarks.market.grader_check}</span>{' '}
            stored reference solutions pass it before an arm is allowed to score. Bar
            length is tokens, because tokens are what a loop costs; the pass column is
            what it wins.
          </p>
          <table className="mt-4 w-full border-collapse text-left">
            <thead className="text-[11px] uppercase tracking-[0.12em] text-fog-2">
              <tr>
                <th className="py-2 pr-3 font-normal">arm</th>
                <th className="py-2 pr-3 font-normal">tasks passed</th>
                <th className="py-2 pr-3 font-normal">s / task</th>
                <th className="py-2 pr-3 font-normal">requests</th>
                <th className="w-[30%] py-2 pr-3 font-normal">tokens</th>
              </tr>
            </thead>
            <tbody>
              {benchmarks.market.arms.map((a) => (
                <tr key={a.arm} className="border-t border-line/60">
                  <td className="py-1.5 pr-3 text-[12px] text-fog-0">{a.arm}</td>
                  <td className="num py-1.5 pr-3 text-[12px] text-pass">
                    {a.passed}/{a.tasks}
                  </td>
                  <td className="num py-1.5 pr-3 text-[12px] text-fog-1">
                    {sec(a.seconds_per_task)}
                  </td>
                  <td className="num py-1.5 pr-3 text-[12px] text-fog-1">
                    {a.requests}
                  </td>
                  <td className="py-1.5 pr-3">
                    <span className="flex items-baseline gap-2">
                      <span
                        className="block h-[3px] bg-signal/70"
                        style={{
                          width: `${Math.max(
                            2,
                            (a.tokens /
                              Math.max(
                                ...benchmarks.market.arms.map((x) => x.tokens),
                              )) *
                              100,
                          )}%`,
                        }}
                      />
                      <span className="num text-[12px] text-fog-0">
                        {int(a.tokens)}
                      </span>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="num mt-3 border-t border-line pt-2.5 text-[11px] leading-relaxed text-fog-2">
            {`python benchmarks/market_compare.py --arms oneshot,aider,flash`} — the
            print is {benchmarks.market.witness}. {benchmarks.market.note}
          </p>
        </div>
      </Reveal>

      <Reveal delay={0.1}>
        <div className="mt-6 border border-refuse/40 bg-refuse/[0.04] p-5">
          <h3 className="num text-[12px] uppercase tracking-[0.16em] text-refuse">
            What this page still refuses to print
          </h3>
          <p className="mt-2 max-w-[76ch] text-[14px] leading-relaxed text-fog-1">
            There is still no Cursor or Copilot bar and no dollar-per-month table. The
            row above exists because one tool could be run here against these weights;
            those two cannot be, and neither a borrowed latency nor a screenshot of one
            would be the same measurement. An earlier commit published a dashboard with
            invented rival latencies and a fabricated cost column; it is reverted and the
            revert is in the history.{' '}
            <span className="text-fog-0">
              The claim this page makes instead is narrower and checkable:{' '}
              {int(benchmarks.totals.total)} offline assertions,{' '}
              {int(benchmarks.totals.mutants)} of them mutation-gated, run in about{' '}
              {BATTERY_WALL.minutes} minutes on a laptop, with no API key.
            </span>
          </p>
        </div>
      </Reveal>
    </Section>
  )
}
