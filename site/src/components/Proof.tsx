import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { captures, type Capture } from '../lib/data'
import { cn } from '../lib/utils'
import { Reveal, Section, SectionHead } from './ui'

const TABS = [
  { id: 'doctor', title: 'What can this machine do?' },
  { id: 'battery', title: 'All of §6, re-read from the tree' },
  { id: 'session', title: 'Three turns at one oracle' },
  { id: 'graph', title: 'The clause’s own budget, printed' },
]

/** Hue only on state: green is a run that said yes, amber is an install that
 *  refused, red is a gate that failed. Structure is ink on ink everywhere else. */
function tone(line: string) {
  if (/^\s*\[yes\]/.test(line)) return 'text-pass'
  if (/^\s*\[\s\]/.test(line)) return 'text-refuse'
  if (/^\s*\[no\]/.test(line)) return 'text-fail'
  if (line.startsWith('OK')) return 'text-pass'
  if (line.startsWith('[R-3.2] wrote') || line.includes('solved=True')) return 'text-pass'
  if (line.includes('REFUSED') || line.includes('NOT APPLIED') || line.includes('solved=False'))
    return 'text-refuse'
  if (line.startsWith('checks ') || line.includes('checks passed') || line.startsWith('[session]'))
    return 'text-fog-0'
  if (line.startsWith('  ..')) return 'text-refuse'
  return 'text-fog-1'
}

function Screen({ capture }: { capture: Capture }) {
  return (
    <div className="term p-4 md:p-5">
      <p className="num mb-2 text-[13px]">
        <span className="text-pass">$ </span>
        <span className="text-fog-0">{capture.command}</span>
        <span
          className={cn(
            'ml-2 text-[11px]',
            capture.exit === 0 ? 'text-pass' : 'text-refuse',
          )}
        >
          exit {capture.exit}
        </span>
      </p>
      <AnimatePresence mode="wait">
        <motion.div
          key={capture.command}
          initial="hidden"
          animate="show"
          exit={{ opacity: 0 }}
          variants={{ show: { transition: { staggerChildren: 0.022 } } }}
        >
          {capture.lines.map((line, i) => (
            <motion.p
              key={`${i}-${line}`}
              variants={{
                hidden: { opacity: 0, x: -4 },
                show: { opacity: 1, x: 0 },
              }}
              transition={{ duration: 0.24, ease: [0.16, 1, 0.3, 1] }}
              className={cn('num whitespace-pre text-[12.5px] leading-[1.65]', tone(line))}
            >
              {line}
            </motion.p>
          ))}
        </motion.div>
      </AnimatePresence>
      <p className="mt-3 border-t border-line pt-2.5 text-[11px] leading-relaxed text-fog-2">
        This is captured stdout, written to{' '}
        <span className="num">site/src/data/transcripts.json</span> by{' '}
        <span className="num">benchmarks/export_site_data.py</span>
        {capture.source ? `, from the committed witness ${capture.source}` : ''}.{' '}
        {capture.redacted.length > 0
          ? `Redacted before it was stored: ${capture.redacted.join('; ')}.`
          : 'Nothing on this line was edited.'}
      </p>
    </div>
  )
}

export function Proof() {
  const [tab, setTab] = useState(0)
  const ordered = [captures[0], captures[2], captures[3], captures[1]].filter(Boolean)

  return (
    <Section id="proof">
      <SectionHead
        index="01"
        id="proof-head"
        title="Four commands, run on this machine, output pasted literally"
        lede={
          <>
            The project asks you to believe a handful of numbers. Here is the raw
            text of the runs that produce them — including the one{' '}
            <span className="num text-fog-0">flash doctor</span> page that answers
            <span className="num text-refuse"> no</span> on a machine missing half
            of what it needs, which is the behaviour the last six release boxes
            exist to protect, and the <span className="num text-fog-0">session</span>{' '}
            transcript that keeps its own refusal in the middle of it: one of three
            turns asked for a function that did not exist yet, and the patch arm
            said so instead of inventing one.
          </>
        }
      />

      <Reveal>
        <div className="mb-4 flex flex-wrap gap-x-6 gap-y-2 border-b border-line pb-2">
          {TABS.map((t, i) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(i)}
              className={cn(
                'relative pb-2 text-[13px] transition-colors',
                tab === i ? 'text-fog-0' : 'text-fog-2 hover:text-fog-1',
              )}
            >
              <span className="num mr-2 text-[11px] text-fog-2">0{i + 1}</span>
              {t.title}
              {tab === i && (
                <motion.span
                  layoutId="proof-underline"
                  className="absolute inset-x-0 -bottom-[9px] h-px bg-pass"
                />
              )}
            </button>
          ))}
        </div>
      </Reveal>

      <Reveal delay={0.05}>
        <Screen capture={ordered[tab] ?? ordered[0]} />
      </Reveal>
    </Section>
  )
}
