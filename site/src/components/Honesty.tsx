import { CircleCheck, TriangleAlert } from 'lucide-react'
import { Chip, Reveal, Section, SectionHead } from './ui'

/** Every row here is a clause in SPEC.md with an OPEN box beside it. The panel
 *  exists because a page that lists only wins is a page you cannot check. */
const NOT_CLAIMED = [
  {
    t: 'No comparison with Cursor or Copilot',
    d: 'One competitor has been run on this machine — aider, against the same 4-bit weights, on the held-out suite, and that table is on the benchmarks page with its witness named. Cursor and Copilot have not been, and cannot be here: there is no headless driver for either on this box, and both generate in their own cloud, so a row for them would change the model, the machine and the token accounting all at once. This page carries no “× faster” multiplier and no dollar-per-month table for anyone. The one time such a dashboard was generated here, it was invented, caught, and reverted in public.',
    tag: 'R-7.12',
  },
  {
    t: 'Speculative decoding is measured negative',
    d: 'R-8.1 asks for ≥ 46 tok/s on the brain tier from a draft-verify path. On this hardware that path faults the GPU — kIOGPUCommandBufferCallbackErrorTimeout — every run, with a vocabulary-matched draft and a mismatched one, at 4 drafted tokens and at 2. The same target without a draft runs normally before and after.',
    tag: 'R-8.1',
  },
  {
    t: 'The trained adapter has never been played',
    d: 'R-6.4’s live arm — LoRA trained on verified outcomes versus the frozen harness, on a frozen suite — has not run. The training path exists, resumes, and is gated offline; the contest that would justify a “it learned” claim is deferred to a machine that is not already busy.',
    tag: 'R-6.4',
  },
  {
    t: 'Voice has never heard a microphone',
    d: 'R-7.3 needs a real-microphone arm with VAD barge-in and ≥ 90 % command recognition over 50 utterances. None of that has run. The only number attached to the voice spike is its command-to-ack of roughly 4.8 seconds, and a latency from a spike is not a recognition rate.',
    tag: 'R-7.3',
  },
  {
    t: 'The live hint A/B came back nil',
    d: 'R-1.1b put the per-block source hint into a live A/B and the result was a nil — no measured win for the feature. It shipped behind switches with that sentence attached, not with the framing removed.',
    tag: 'R-1.1b',
  },
  {
    t: 'Linux and Windows have not installed this',
    d: 'Four shapes have been run on Apple Silicon. On other platforms the non-generating half is expected, not verified, and the generating half is not expected at all: the model layer is MLX.',
    tag: 'R-7.5',
  },
  {
    t: 'Three arms need humans or hardware',
    d: '16 GB co-residency (what survives beside a browser and an editor), a 24-hour chaos run, and M17’s “≥ 7 of 10 developers keep it after a week”. None can be closed by a script, and none is claimed.',
    tag: '§9',
  },
  {
    t: '“No network” means the battery, not the product',
    d: 'The offline verification surface makes no network calls and needs no API key. `flash web` is a documentation fetcher and does fetch, through a cache, when you ask it to.',
    tag: 'R-7.7',
  },
]

export function Honesty() {
  return (
    <Section id="honesty" className="border-t border-line">
      <SectionHead
        index="06"
        title="What this project does not claim"
        lede={
          <>
            A verification culture that only publishes passes is indistinguishable
            from marketing. These eight rows are open boxes in{' '}
            <span className="num text-fog-0">SPEC.md</span>, written the same way the
            passes are: what was asked for, what happened, and what would close it.
          </>
        }
      />

      <ol className="grid gap-px overflow-hidden border border-line bg-line md:grid-cols-2">
        {NOT_CLAIMED.map((r, i) => (
          <Reveal as="li" key={r.t} delay={i * 0.03} className="bg-ink-1 p-5">
            <div className="flex items-start gap-3">
              <TriangleAlert size={15} className="mt-1 shrink-0 text-refuse" />
              <div>
                <h3 className="text-[14.5px] font-medium leading-snug text-fog-0">
                  {r.t}
                </h3>
                <p className="mt-2 max-w-[62ch] text-[13px] leading-relaxed text-fog-1">
                  {r.d}
                </p>
                <Chip className="mt-3">{r.tag}</Chip>
              </div>
            </div>
          </Reveal>
        ))}
      </ol>

      <Reveal delay={0.1}>
        <div className="mt-8 flex items-start gap-3 border border-pass/30 bg-pass/[0.04] p-5">
          <CircleCheck size={16} className="mt-0.5 shrink-0 text-pass" />
          <p className="max-w-[80ch] text-[14px] leading-relaxed text-fog-1">
            What <span className="text-fog-0">is</span> claimed is narrower and
            checkable in about fifteen minutes: the offline verification surface
            exists, runs on a plain user account with no key, prints its own
            fractions, refuses honestly on an install that lacks its data, and is
            mutation-gated often enough that a broken check is caught rather than
            banked. Every number on this page came out of a run whose command is
            printed next to it.
          </p>
        </div>
      </Reveal>
    </Section>
  )
}
