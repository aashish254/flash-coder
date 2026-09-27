import { motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { cn } from '../lib/utils'
import { Reveal, Section, SectionHead } from './ui'

const STAGES = [
  {
    key: 'PERCEIVE',
    modules: 'context.py · perceive.py · graph.py',
    text: 'A token-budgeted skeleton of the repo, the language server’s static errors for the file in hand, and the blast-radius subgraph from the AST index — so the model reads the callers a change would break, not a file it guessed at.',
  },
  {
    key: 'ROUTE',
    modules: 'route.py · decide.py · power.py',
    text: 'One forward pass makes the tier decision, and the power governor decides what the machine is actually allowed to load right now. A ~4.4 GB fast tier or a 15–17 GB brain tier is picked by free RAM, not by ambition.',
  },
  {
    key: 'ACT',
    modules: 'loop.py · patches.py · grammar.py',
    text: 'The answer is a patch set with anchored hunks, not a rewritten file. A patch that will not apply is rejected before anything is executed, which is cheaper than a wrong edit.',
  },
  {
    key: 'VERIFY',
    modules: 'harness.py · debug.py · sandbox.py',
    text: 'The test runs. The harness compares against a known GOT/WANT oracle and reads which assert failed and what the value actually was, then unwinds it to the frame that matters. Executed in an explicit sandbox the caller opts into.',
  },
  {
    key: 'RETRY',
    modules: 'tourney.py · jobs.py · confidence.py',
    text: 'The real failure text goes back into the next attempt. Candidates can be run as a tournament and escalated small → brain, and the confidence signal decides when “I am not sure” is the correct answer to print.',
  },
  {
    key: 'RECORD',
    modules: 'trace.py · ledger.py · checkpoint.py',
    text: 'Every session is replayable from a trace snapshot, every outcome lands in the ledger that later scores the router, and an interrupted suite resumes mid-task rather than from the top.',
  },
]

export function Loop() {
  const [active, setActive] = useState(0)

  useEffect(() => {
    const id = setInterval(() => setActive((a) => (a + 1) % STAGES.length), 3200)
    return () => clearInterval(id)
  }, [])

  return (
    <Section id="loop" className="border-t border-line">
      <SectionHead
        index="04"
        title="The loop, and which file owns each leg"
        lede="Six stages, each one a module you can open. The point of naming them is that a claim about the loop is a claim about code, not about a product feeling."
      />

      <Reveal>
        <ol className="grid gap-px overflow-hidden border border-line bg-line md:grid-cols-2 xl:grid-cols-3">
          {STAGES.map((s, i) => {
            const on = i === active
            return (
              <li
                key={s.key}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  'relative bg-ink-1 p-5 transition-colors duration-300',
                  on && 'bg-ink-2',
                )}
              >
                <div className="flex items-baseline gap-3">
                  <span
                    className={cn(
                      'num text-[11px] tracking-[0.16em]',
                      on ? 'text-pass' : 'text-fog-2',
                    )}
                  >
                    0{i + 1}
                  </span>
                  <h3
                    className={cn(
                      'num text-[15px] tracking-[0.06em] transition-colors',
                      on ? 'text-fog-0' : 'text-fog-1',
                    )}
                  >
                    {s.key}
                  </h3>
                </div>
                <p className="num mt-1 text-[11px] text-fog-2">{s.modules}</p>
                <p className="mt-3 text-[13.5px] leading-relaxed text-fog-1">{s.text}</p>
                <motion.span
                  className="absolute inset-x-0 bottom-0 h-[2px] origin-left bg-pass"
                  initial={false}
                  animate={{ scaleX: on ? 1 : 0 }}
                  transition={{ duration: on ? 3.2 : 0.25, ease: 'linear' }}
                />
              </li>
            )
          })}
        </ol>
      </Reveal>

      <Reveal delay={0.05}>
        <p className="mt-5 max-w-[74ch] text-[13px] leading-relaxed text-fog-2">
          Where the loop has measured negative, SPEC.md says so in the same file:
          speculative decoding faults the GPU on this hardware (R-8.1), the live
          hint A/B came back a nil (R-1.1b), and the trained adapter has never been
          played against the frozen harness (R-6.4). A project that publishes only
          its wins cannot be verified, only believed.
        </p>
      </Reveal>
    </Section>
  )
}
