import { benchmarks, BATTERY_WALL, int } from '../lib/data'
import { useCountUp } from '../lib/motion'
import { Reveal, Section, SectionHead, Stat } from './ui'

function Counted({ value, suffix }: { value: number; suffix?: string }) {
  const { ref, value: v } = useCountUp(value)
  return (
    <>
      <span ref={ref}>{int(Math.round(v))}</span>
      {suffix}
    </>
  )
}

const t = benchmarks.totals

export function Numbers() {
  return (
    <Section id="numbers" className="border-t border-line">
      <SectionHead
        index="02"
        title="What is actually true on a machine that has this repo"
        lede={
          <>
            Every figure below is either read out of the committed §6 battery print{' '}
            <span className="num text-fog-1">({benchmarks.witness})</span> or timed
            by{' '}
            <span className="num text-fog-1">benchmarks/dashboard_data.py</span> on
            a 32 GB Apple Silicon box. Nothing here is a comparison with another
            product, because no competitor was measured here — and a chart with a
            made-up bar next to a real one is how a project stops being verifiable.
          </>
        }
      />

      <div className="grid grid-cols-2 gap-x-8 gap-y-8 md:grid-cols-4">
        {[
          {
            value: t.checks,
            unit: 'checks',
            caption: 'pass-and-fail assertions that run with MLX blocked in a child process, so no model and no network is involved.',
          },
          {
            value: t.oracle,
            unit: 'oracle',
            caption: 'verifications against a known GOT/WANT answer rather than a self-reported one — the harness’s own §5 clause.',
          },
          {
            value: t.mutants,
            unit: 'gates',
            caption: `planted bugs across ${t.mutant_vectors} vectors. Each must be caught by exactly the checks that claim to catch it; a gate that any failure trips is not a gate.`,
          },
          {
            value: t.vectors,
            unit: 'vectors',
            caption: 'commands in SPEC §6. Each prints its own fraction; the totals above are summed from the printed lines, never from exit codes.',
          },
        ].map((s, i) => (
          <Reveal key={s.unit} delay={i * 0.05}>
            <Stat
              value={<Counted value={s.value} />}
              unit={s.unit}
              caption={s.caption}
              emphasis={i === 0}
            />
          </Reveal>
        ))}
      </div>

      <Reveal delay={0.1}>
        <div className="mt-12 grid gap-6 md:grid-cols-[1.4fr_1fr]">
          <div className="card p-5">
            <h3 className="text-[15px] font-medium text-fog-0">
              The cost of believing any of it
            </h3>
            <p className="mt-2 text-[14px] leading-relaxed text-fog-1">
              <span className="num text-fog-0">
                {BATTERY_WALL.minutes} min {BATTERY_WALL.seconds} s
              </span>{' '}
              wall clock for all {t.vectors} vectors, measured{' '}
              {BATTERY_WALL.date}. That is the whole ask: a quarter of an hour,
              offline, on your own machine, before you decide whether this project’s
              claims survive contact with your hardware.{' '}
              <span className="num text-fog-2">flash selftest --all</span> runs it
              and prints the same totals.
            </p>
            <p className="mt-3 text-[13px] leading-relaxed text-fog-2">
              Two of the traps that made this a rule are recorded in{' '}
              <span className="num">SPEC.md</span> §6: a phrase grep that silently
              dropped two vectors, and a last-line grep that read one vector’s
              mutant summary as its check count and under-reported the total by 17
              while looking clean.
            </p>
          </div>
          <div className="card p-5">
            <h3 className="text-[15px] font-medium text-fog-0">
              What the battery is not
            </h3>
            <ul className="mt-2 space-y-2 text-[13px] leading-relaxed text-fog-1">
              <li>— Not a model benchmark: it needs no weights and no GPU.</li>
              <li>
                — Not a claim that generation works on your hardware. That is{' '}
                <span className="num">flash power</span> and{' '}
                <span className="num">flash doctor</span>, answered per machine.
              </li>
              <li>
                — Not a leaderboard. There is no rival column because nobody ran a
                rival here.
              </li>
            </ul>
          </div>
        </div>
      </Reveal>
    </Section>
  )
}
