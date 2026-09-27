import { benchmarks, BATTERY_WALL } from '../lib/data'
import { COMMANDS, REPO_URL, VERSION } from '../lib/site'
import { CopyLine, Reveal } from './ui'

const LINKS = [
  { href: `${REPO_URL}/blob/main/SPEC.md`, label: 'SPEC.md', note: 'the clauses' },
  { href: `${REPO_URL}/blob/main/README.md`, label: 'README.md', note: 'the first ten minutes' },
  { href: `${REPO_URL}/blob/main/docs/methodology.md`, label: 'docs/methodology.md', note: 'why a claim is a printed line' },
  { href: `${REPO_URL}/blob/main/docs/portability.md`, label: 'docs/portability.md', note: 'four install shapes' },
  { href: `${REPO_URL}/issues`, label: 'issues', note: 'where a failure belongs' },
]

export function Footer() {
  return (
    <footer className="border-t border-line">
      <div className="mx-auto w-full max-w-6xl px-5 py-16 md:px-8">
        <Reveal>
          <div className="grid gap-10 md:grid-cols-[1.2fr_1fr]">
            <div>
              <h2 className="text-[22px] font-medium tracking-tight text-fog-0 md:text-2xl">
                Check it, then decide.
              </h2>
              <p className="mt-3 max-w-[58ch] text-[14px] leading-relaxed text-fog-1">
                This page has no number that does not come out of a run, and every
                run is in the repo. Clone it, spend{' '}
                <span className="num text-fog-0">
                  {BATTERY_WALL.minutes}:{String(BATTERY_WALL.seconds).padStart(2, '0')}
                </span>{' '}
                of a laptop’s time on{' '}
                <span className="num text-fog-0">benchmarks/battery_reread.py</span>,
                and read the fractions yourself.
              </p>
              <div className="mt-6 max-w-[560px]">
                <CopyLine cmd={COMMANDS.measure} label="rebuild these charts" />
                <p className="mt-2 text-[12px] leading-relaxed text-fog-2">
                  The site’s data is generated, not maintained: the exporter reads the
                  battery witness and the timed runs, redacts this machine’s paths, and
                  writes the three JSON files this page imports.
                </p>
              </div>
            </div>

            <nav aria-label="Documents">
              <ul className="space-y-3">
                {LINKS.map((l) => (
                  <li key={l.label} className="flex items-baseline gap-3">
                    <a
                      href={l.href}
                      target="_blank"
                      rel="noreferrer"
                      className="num text-[13px] text-fog-0 underline decoration-line decoration-1 underline-offset-4 transition-colors hover:decoration-signal"
                    >
                      {l.label}
                    </a>
                    <span className="text-[12px] text-fog-2">{l.note}</span>
                  </li>
                ))}
              </ul>
            </nav>
          </div>
        </Reveal>

        <div className="mt-14 flex flex-wrap items-baseline justify-between gap-x-8 gap-y-2 border-t border-line pt-5 text-[11.5px] text-fog-2">
          <span className="num">
            flash-coder v{VERSION} · figures from{' '}
            <span className="text-fog-1">{benchmarks.witness}</span>
          </span>
          <span>Runs locally on Apple Silicon · no telemetry, no account</span>
        </div>
      </div>
    </footer>
  )
}
