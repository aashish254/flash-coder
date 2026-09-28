import { Cpu, Lock, Package } from 'lucide-react'
import { BATTERY_WALL, benchmarks, int } from '../lib/data'
import { COMMANDS } from '../lib/site'
import { Chip, CopyLine, Reveal, Section, SectionHead } from './ui'

const SHAPES = [
  {
    id: 'A',
    name: 'fresh clone, editable install',
    status: 'verified',
    detail: `the whole §6 battery re-read inside a clone built from a bare git clone — ${int(benchmarks.totals.total)} claims, ${int(benchmarks.totals.mutants)} gates, exit 0.`,
    witness: 'benchmarks/results/battery_reread_r75_20260928.log',
  },
  {
    id: 'B',
    name: 'wheel in a throwaway venv',
    status: 'verified',
    detail:
      'a wheel carries the package only, so there is no data tree beside it. The six selftests that need one print a single sentence naming the path and exit 2 — no traceback. `flash doctor` reports “installed copy” and answers no on the verification surface.',
    witness:
      'gated in the battery itself: benchmarks/backend_free_check.py (42 checks + 10 mutants) sweeps every module in a copy that has no benchmarks/',
  },
  {
    id: 'C',
    name: 'editable install of the clone, run from elsewhere',
    status: 'dated',
    detail:
      'all 33 vectors green from a clone installed with -e. Run on 2026-09-27 against the totals of that day (1,104 + 20 = 1,124, 78 gates). The battery is 35 lines and 1,210 + 20 = 1,230 with 111 gates as of 2026-09-28 — this exact shape has not been re-run since; what has been re-run is the download, on both of its install shapes (row D).',
    witness: 'benchmarks/results/r75_install_shapes_20260927.log',
  },
  {
    id: 'D',
    name: 'unpacked sdist / “Download ZIP”, no .git',
    status: 'verified',
    detail:
      'the whole battery ran inside the unpacked sdist on 2026-09-28, on both install shapes the one tarball supports: `pip install <sdist>` printed 33 of 35 lines and 1,119 + 20 = 1,139 with 85 gates and two named grammar refusals, and `pip install \'<sdist>[ts]\'` printed 35 of 35 and 1,210 + 20 = 1,230 with 111 gates, agreeing with SPEC §6 as written. The driver fails if a TypeScript vector passes on the plain install — that shape is supposed to refuse.',
    witness:
      'benchmarks/results/r75_sdist_battery_shapes_20260928.log (the charge-limited first pass is kept beside it as …_battgate_…)',
  },
]

export function Install() {
  return (
    <Section id="install" className="border-t border-line">
      <SectionHead
        index="05"
        title="Install, and which four shapes have actually been run"
        lede={
          <>
            Four commands, then the honest part: this project has been installed
            and verified in four different shapes on Apple Silicon, and each row
            below names the log that says so. A row labelled{' '}
            <Chip tone="refuse">dated</Chip> was verified once, at a total that has
            since moved, and has not been re-run.
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[1.15fr_1fr]">
        <Reveal className="flex flex-col gap-4">
          <div className="num text-[11px] uppercase tracking-[0.16em] text-fog-2">
            5.1 · the four commands
          </div>
          <CopyLine cmd={COMMANDS.install} label="install" />
          <CopyLine cmd={COMMANDS.doctor} label="ask" />
          <CopyLine cmd="python -m flash.cli selftest --all" label="verify" />
          <CopyLine cmd="python -m flash.cli run <task-id>" label="use" />
          <p className="max-w-[62ch] text-[13px] leading-relaxed text-fog-2">
            <span className="num text-fog-1">doctor</span> goes first because it
            answers the question a README usually dodges: which half of this project
            is present on your machine. Its exit code follows its own page — five
            <Chip tone="refuse"> no</Chip> on a wheel install, and the reason beside
            each one.
          </p>
        </Reveal>

        <Reveal delay={0.06}>
          <div className="card h-full p-5">
            <div className="num text-[11px] uppercase tracking-[0.16em] text-fog-2">
              5.2 · requirements, by half
            </div>
            <ul className="mt-4 space-y-4 text-[13.5px] leading-relaxed text-fog-1">
              <li className="flex gap-3">
                <Cpu size={15} className="mt-0.5 shrink-0 text-signal" />
                <span>
                  <span className="text-fog-0">Anything that generates text needs
                  Apple Silicon.</span> The model layer is MLX, and{' '}
                  <span className="num">pyproject.toml</span> marks it by platform
                  instead of letting a Linux install fail to resolve.
                </span>
              </li>
              <li className="flex gap-3">
                <Lock size={15} className="mt-0.5 shrink-0 text-pass" />
                <span>
                  <span className="text-fog-0">Everything else is pure Python 3.11+
                  and needs no key.</span> With MLX blocked in a child process,{' '}
                  <span className="num text-fog-0">26 of 26</span> submodules of{' '}
                  <span className="num">flash</span> still import, and the one thing
                  that raises is the call that needs a forward pass — with a message
                  naming MLX, not a stack trace.
                </span>
              </li>
              <li className="flex gap-3">
                <Package size={15} className="mt-0.5 shrink-0 text-fog-2" />
                <span>
                  <span className="text-fog-0">RAM decides what you can run, not the
                  OS.</span> <span className="num">flash power</span> prints the
                  profile the machine offers right now and the largest model it may
                  load. This box offered{' '}
                  <span className="num text-fog-0">17.3 GB</span> during the runs
                  behind panel 3.1.
                </span>
              </li>
            </ul>
            <p className="mt-5 border-t border-line pt-3 text-[12.5px] leading-relaxed text-fog-2">
              Verifying the offline half costs about{' '}
              <span className="num text-fog-1">
                {BATTERY_WALL.minutes} min {BATTERY_WALL.seconds} s
              </span>{' '}
              and needs no weights, no GPU time and no network.
            </p>
          </div>
        </Reveal>
      </div>

      <div className="mt-12">
        <div className="num mb-4 text-[11px] uppercase tracking-[0.16em] text-fog-2">
          5.3 · install shapes that have been run
        </div>
        <ol className="grid gap-px overflow-hidden border border-line bg-line md:grid-cols-2">
          {SHAPES.map((s, i) => (
            <Reveal as="li" key={s.id} delay={i * 0.04} className="bg-ink-1 p-5">
              <div className="flex items-center gap-3">
                <span className="num text-[11px] text-fog-2">{s.id}</span>
                <h3 className="num text-[13.5px] text-fog-0">{s.name}</h3>
                <Chip tone={s.status === 'verified' ? 'pass' : 'refuse'} className="ml-auto">
                  {s.status}
                </Chip>
              </div>
              <p className="mt-3 text-[13px] leading-relaxed text-fog-1">{s.detail}</p>
              <p className="num mt-3 text-[11px] leading-relaxed text-fog-2">
                {s.witness}
              </p>
            </Reveal>
          ))}
        </ol>
      </div>
    </Section>
  )
}
