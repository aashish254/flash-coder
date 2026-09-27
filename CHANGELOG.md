# Changelog

Notable changes per release, written so a claim here can be traced to the run
that printed it. `git log --oneline` is the complete history — each commit naming
the measurement that decided it — and this file is the readable summary of it.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **R-7.6, R-7.7 and R-7.8 closed together, because the third one is what proved
  the first two.** `flash --version`, `flash doctor` and `flash selftest --all`
  exist; `flash/decide.py`'s `import mlx.core` moved inside `decide()`; and every
  `flash …` line printed in a tracked document now has to parse against
  `flash.cli.build_parser()`. Two new §6 vectors carry the measurements:
  `python benchmarks/backend_free_check.py` → **30/30 + 5/5 mutants** and
  `python benchmarks/documented_commands_check.py` → **7/7 + 4/4 mutants**, and the
  re-read that followed them is the printed line
  `checks 1104  oracle 20  §6 total 1124  mutants 78` with **33** OK lines
  (`benchmarks/results/battery_reread_r76_20260927.log`). Both files have grown since
  that print, in the bullets below and in R-7.9/R-7.10, and the `[0.0.1]` notes'
  headline carries the later totals. What each command
  promises is gated, not narrated: `doctor`'s exit code follows its own page (a
  synthetic broken install in a temp tree must print `no` on exactly 5 of 9 lines
  and rc 1; a complete one zero `no` and rc 0), `selftest --all` refuses with rc 2
  naming `benchmarks/battery_reread.py` rather than totalling checks that never
  ran, `--version` reads the version off the package object, and the whole package
  is swept submodule-by-submodule under an import blocker — **`SWEEP 26/26`** —
  because the two names that used to fail are not the same claim as "every module".
  `flash doctor` on this machine prints nine lines across six sections and says
  `Both halves this project claims to have are present here.`
- `--backend-free` is now a measured property instead of a flag. The battery writes
  a `sitecustomize.py` blocker, puts it FIRST on the `PYTHONPATH` its children
  inherit, and refuses to print a total until a child's `import mlx.core` has
  raised the shim's own sentence; a sibling gate requires it to leave `numpy`
  alone, because a blocker that broke everything would "prove" the claim by making
  the battery unrunnable.
- `benchmarks/documented_commands_check.py` reads fenced blocks, inline backticks,
  the workflows' `run:` lines and the packaging files' comments: **25 commands across
  150 citations in 15 documents, plus 72 source paths a reader is told to open**, all
  of which resolve. (The four figures move with the prose; the gate asserts a floor and
  prints what it counted.)
  Commands are resolved by `parse_args`, never by dispatch — a documented
  `flash run` would create a worktree — and the collector is not trusted: planting a
  command nobody wrote must be rejected, and deleting `selftest`/`doctor` from the
  live parser must fail a gate whose text names the citing `file.md:line`.
- **R-7.4 path portability**, gated by `benchmarks/portable_paths_check.py`
  (14 checks + 7 mutants, launched from a foreign working directory). A task
  corpus's oracle bootstrap now writes `<REPO>` instead of the author's checkout,
  expanded in the one function every execution seam already passes through.
  Before the fix, 72 context tasks in the committed suites failed with
  `ModuleNotFoundError` on any other machine — before the candidate's first line,
  so the suite printed verdicts while scoring nothing. §6 re-read from the tree
  afterwards on a quiet box: `checks 1066  oracle 20  §6 total 1086  mutants 69`,
  all **31** lines OK (`benchmarks/results/battery_reread_r74_20260927.log`), and
  the vector itself re-run **after** that witness landed — 14/14 + 7/7 with the new
  160th record file on disk.
- The same scan, widened. `HOST_PATHS` now carries a third marker — the default
  Homebrew install prefix on Apple Silicon — which is not a leaked home directory
  but is still a path only some Macs have. It found exactly one line: the
  README's second install command, which spelled an interpreter path under that
  prefix and so told every reader whose prefix is the other one to run a command
  that cannot exist on their machine. Re-run on the widened scan: **14/14 + 7/7**,
  residue **395 → 403** across **31 → 33** record files (the delta is eight
  occurrences in two failed run traces that had matched nothing before), and
  **407 / 34** once this pass's own reproduction witness was stored — which is the
  published floor's number, with every step between the two accounted for in
  `docs/portability.md`.
- `docs/portability.md`: what the token contract is, what was scrubbed, which
  witness files are deliberately NOT rewritten and why, and the published residue
  count for the exclusion (`RECORD_RESIDUE`, asserted `≤` what the tree carries).
- Two open boxes this pass created out of measurements, not intentions. **R-7.7**:
  with `mlx` blocked, **24 of the 26** modules import and `flash.decide` /
  `flash.route` are the two that do not — unnoticed because `flash.cli` imports both
  lazily. **R-7.8**: gate every `flash …` command printed in a doc against
  `flash.cli.build_parser()`, which is how the setup block below was found.
- **A landing page under `site/`, and the pipeline that keeps it honest.** Vite +
  React + Tailwind v4, with the hero scene being this repo's own AST call graph:
  `benchmarks/export_site_data.py` runs `flash.graph.build(ROOT)`, keeps the 150
  most-connected symbols under `flash/`, and joins them on `calls` and `imports`
  edges only — `reads` edges from module-level constants dominate the graph and a
  slice that keeps them is a 2,552-edge hairball rather than a picture. Clicking a
  node computes a caller-direction blast radius to depth 2 in the browser, which is
  the same question `flash graph SYM` answers. Every figure on the page comes from
  `site/src/data/benchmarks.json`, and every panel names the command that prints it;
  the 33 commands were cross-checked against `battery_reread.BATTERY` itself, which
  is how a `build_ms` field that had parsed the edge count (10,058 instead of 603 ms)
  was caught before it shipped. `python -m flash.<mod> --selftest` timings are the
  median of **n=3** fresh runs with min and max published beside them.
  The page's own first defect was a real one and is worth the stating: three.js
  throws when no WebGL context can be made, an uncaught error in a child unmounts
  the whole React tree, and the page rendered **blank** — 33 KB of DOM with an empty
  `#root`. Headless Chrome's own console line was the witness
  (`Uncaught Error: THREE.WebGLRenderer: Error creating WebGL context.`). The hero
  now probes for a context, a software renderer and `prefers-reduced-motion`, and
  falls back to an interactive SVG of the same 150 symbols — so a reader on a remote
  desktop gets the graph, the click-to-blast-radius readout and the copy, and never
  downloads the 914 KB three.js chunk. With the probe in place the rendered DOM is
  173 KB with all eight sections present.
- **R-7.5's second clause is now a run instead of a sentence, and it is the
  download that answers it.** `benchmarks/r75_sdist_battery_check.py` builds the
  sdist, unpacks it under a temp directory, installs the tarball into a throwaway
  venv, and prints its own provenance before it is allowed to report a total —
  `site-packages`, `import flash` from inside it resolves to the unpacked sdist,
  the installed `flash --version` prints `flash 0.0.1`, and the witness is refused
  if the checkout's path appears anywhere in it. Then it runs the entire §6 battery **inside the download**:
  **33/33** lines, `checks 1119  oracle 20  §6 total 1139  mutants 85`, **13 min 2 s**
  against the checkout's own 15 min 9 s for the same tree state
  (`benchmarks/results/r75_sdist_battery_20260928.log`, 0 host paths, `RECORD_RESIDUE`
  floor still **411**, record files **171 → 172**). The same driver records the other
  shape on the way out: `flash selftest --all` with no tree present exits **2** naming
  the `site-packages/benchmarks/battery_reread.py` it wanted.
  The corrected docs were then re-read from this checkout, to the identical print —
  **33/33**, `checks 1119  oracle 20  §6 total 1139  mutants 85`, in **15 min 12 s**
  (`benchmarks/results/battery_reread_r75b_20260928.log`, again 0 host paths, so the
  floor holds at **411 over 36** and record files move to **173**). That one is a shell
  redirect rather than a driver, so it credits itself nothing about which tree it read
  beyond what the SPEC entry says out loud: this checkout, launched from it.
  `docs/methodology.md` rule 5 and `SPEC.md` R-7.5 carry the split this forced — the
  literal reading of "against the installed package, no source tree" cannot produce a
  green battery, because 11 of the 33 vectors index the tree they stand in.
- **R-7.5's first clause was measured the same way, and it had the same disease.**
  `benchmarks/r75_fresh_install_check.py` is credited with clause 1 — a sdist installed
  into a throwaway venv answering as a stranger's terminal would. Rewritten: it now
  builds, installs, and then asks **the venv's own `flash` console script** from a
  working directory that contains no Python at all, and it prints which `flash` each
  child resolved before it is allowed to assert anything about the answer. **9/9 shapes
  green** (`benchmarks/results/r75_clause1_20260928.log`, 0 host paths, 0 substitutions
  needed). The two shapes a downloader can actually make:
  - *installed from the tarball* — `flash --version` → `flash 0.0.1` rc 0; `flash doctor`
    → rc **1**, with `(installed copy)` on its first line, `verification surface beside
    the package: benchmarks/ ABSENT`, and `the offline battery CANNOT run from this
    install` naming its remedy; `flash selftest --all` → rc **2** naming the
    `site-packages/benchmarks/battery_reread.py` it wanted.
  - *cloned, installed editable* — `import flash` resolves to the clone and not to this
    checkout, `flash doctor` → rc 0 with the battery line on `yes`, and
    `flash selftest --all --quick harness lsp power` runs **3/3** vectors from the clone
    and prints its own `run of 3/33 lines: totals are partial` warning.
  The gate that makes this file worth having is the sixth one: run `python -m flash.cli`
  **from inside the unpacked sdist** with the same interpreter, and `flash.__file__` is
  that tree and `doctor` says `(editable checkout)` — `python -m` puts the cwd on
  `sys.path[0]`. The old driver did exactly that, with no `cwd`, so its children answered
  from this checkout while the print claimed the install. That is now a gate rather than a
  footnote, because the only way to keep a provenance bug from coming back is to make the
  wrong shape fail something.

### Corrected — claims this file made that the tree does not support
- **`flash doctor` does not exit 0 against an installed copy, and this file, `SPEC.md`,
  `TODO.md` and `CONTRIBUTING.md` all said it did.** What is true, measured by the
  rewritten driver: against a tarball install it exits **1** and says why — the
  verification surface is not beside the package — and against a cloned, editable
  install it exits **0**. The rc 0 belongs to the shape a contributor gets and the rc 1
  to the shape a downloader gets; the sentence dropped the difference, and the sentence
  was believed because the driver that should have caught it was interrogating the
  checkout rather than the install. `doctor`'s behaviour is correct and unchanged; four
  documents moved.
- This file's own `### Added` section listed `flash doctor`, `flash selftest --all`
  and `flash --version` as shipped. **None of the three existed** when that was
  written — `python -m flash.cli` rejected `doctor` and `selftest` as invalid
  subcommands, measured by parsing 23 documented subcommands against the real parser
  (21 resolve). They are shipped now, and the bullet above is the measurement that
  says so; what this correction leaves standing is the *rule*, which is that a claim
  in this file moves only when a run prints it. The "21 of the 23" count is itself
  superseded by R-7.8's collector, which reads the documents instead of a hand-kept
  list: **25 commands, all resolving.**
- `## [0.1.0] - 2026-09-27` dated a release that has not happened. No tag exists
  and no remote is configured, so the header now says so outright. **(What moved
  since:** the section is now `## [0.0.1] - tagged 2026-09-28, still not published` —
  an annotated tag exists on this tree, and the remote that would make it downloadable
  does not. The date that made the original claim false was the fabrication, not the
  version.)
- Two documents cited a different module count for the same fact: `pyproject.toml`
  and `docs/models.md` said 24 of the 26 import without MLX, the CI comment said 25.
  Measured 24, so `ci.yml` is the one that moved.
- **That fix went stale and nothing noticed it.** R-7.7 made `flash.decide` and
  `flash.route` import with the backend blocked, so the measured figure became
  **26 of the 26**. Every document that quotes it moved — `README.md`, `CONTRIBUTING.md`,
  `docs/models.md`, `docs/config.md`, SPEC R-7.7 — except the CI header, which still
  described those two modules as the ones that cannot import, while the very step it
  annotates runs the file that measures otherwise. The fix is structural rather than a
  retype: the header no longer carries a count at all. It names
  `benchmarks/backend_free_check.py`, which prints its own (`SWEEP 26/26`), so there is
  nothing left in a file no vector reads to go stale — the same move that made the
  landing page's numbers generated instead of typed. The bullet above stays as dated
  history rather than being rewritten. What is still true is the shape of the hole: a
  workflow comment is prose, and no gate reads it, so a hand-kept number there was only
  ever as current as whoever last looked. The one artifact aimed squarely at strangers —
  the file describing what CI proves — held the least-checked claim in the repo.
- The same rot had reached this file's own `### Packaging` bullet, in the section a
  reader reaches first: it still said the clean-clone install was an open box and still
  offered **2 of the 26** modules failing without MLX as its measured half. Both moved
  after it was written — R-7.7 closed on 2026-09-27 and made the import answer
  **26 of 26**, R-7.5 closed on 2026-09-28 and made the install answer a run — and the
  bullet stayed as if nothing had happened since. Rewritten in place, with both old
  figures kept and labelled as the pre-fix measurements they are. A file that admits
  staleness only in its `### Corrected` section will always be behind its own
  `### Added` list.
- "`pip install flash-coder` works on Linux and Windows" became a gated claim: no
  clean-clone install has been run (SPEC R-7.5), and `pyproject.toml`'s comment that
  cited `benchmarks/backend_free_check.py` as its verifier now says that file is
  unwritten. **That last clause moved again this pass:** the file exists and is a §6
  line, so the comment cites it as the gate it is; the clean-clone run is still
  R-7.5's open box and the install claim is still gated, not measured.
- **This project published invented numbers, and they are gone.** Commit `54a2117`
  added four dashboard PNGs to `benchmarks/results/benchmarks_20261028/` — a
  competitor latency table, a cost table and a "viral summary" — under a commit
  message calling them benchmarks. They were not benchmarks. Every one of those
  figures was typed by hand: no run produced a competitor's millisecond, and the
  cost table multiplied a price this repo has never measured. Reverted as `69a4d2d`,
  which deletes all four files. What makes this a correction rather than a revert is
  the mechanism, because a rule that only lives in a sentence will be broken again:
  the site's numbers are now generated, so the failure is structurally unavailable.
  `benchmarks/dashboard_data.py` measures, `benchmarks/export_site_data.py` converts,
  and `site/src/data/*.json` is the only source a component may read. There is no
  code path from a hand-typed figure to the screen.
- **This file's own bullet above is the stale claim it was warning about.** It
  closed with "the clean-clone run is still R-7.5's open box and the install claim
  is still gated, not measured"; the run has since happened twice, and the sentence
  that was written as a caveat is now the part a reader would mis-trust. `CONTRIBUTING.md`
  carried the same expiry in plainer form ("`pip install .` from a clean clone into a
  throwaway venv has never been run") and now says what was measured instead.
- **A committed artifact was labelled as evidence of a run it was not.**
  `benchmarks/results/battery_reread_r75_20260928.log` went in under the message
  "R-7.5 fresh clone witness: … all 33 lines green", and `docs/portability.md` called
  it "the R-7.5 fresh-clone §6 witness". It is the checkout's R-7.10c re-read: it is
  `battery_reread_r710c_20260927.log` plus one trailing `battery rc=0`, and the driver
  it was credited to runs three lines, not thirty-three. The numbers in it are true —
  they are just the checkout's, which is the kind of true that a filename can turn
  into a claim about somebody else's machine. The file stays (deleting a committed
  artifact is not a correction), `docs/portability.md` says what it is, and
  `benchmarks/results/r75_sdist_battery_20260928.log` is the download's own print.
  What let this happen is worth naming: that log was produced by a shell redirect, not
  by a driver, so nothing in it says which tree it was read from. The replacement
  asserts its provenance before it reports a total and refuses to write the witness
  if the checkout's path appears in it.

### Fixed
- **R-7.9** `python -m flash.train --dry-run` writes nothing, on both of its
  branches. `--suite-from-dataset` was dispatched above the branch that honoured the
  flag, so a command typed to *avoid* touching the tree rewrote the tracked
  `benchmarks/tasks/r64_train_from_dataset.jsonl`; `git status` after a verification
  run is what found it. `suite_from_dataset()` now takes `dry` and skips the `mkdir`
  with the `write_text`, printing `-> would write <path>`. Two checks and one mutant
  in `benchmarks/lora_path_check.py`'s suite group, run with **no `--suite-out`** so
  the default name is the thing under test, and aimed at a temp copy of the suites so
  the mutant cannot dirty the tree it measures: **`lora path: 33/33 checks passed`,
  `mutations: 15/15 gates defeated by exactly their checks`**.
- **R-7.10** three module selftests that cannot run on a wheel install now say so
  instead of failing. Measured on a `pip install .` into a throwaway venv:
  `flash.grammar --selftest` and `flash.debug --selftest` raised
  `FileNotFoundError` for a `benchmarks/tasks/*.jsonl` path inside
  `site-packages`, and `flash.patches --selftest` printed
  `FAIL workspace: a project directory loads under its relative path` inside an
  otherwise-green 46-check report — three ways for a missing directory to be read as
  a broken package. `flash.graph` already refused with exit 2; the refusal is now one
  function, `doctor.vector_refusal()`, driven by a four-entry `doctor.VECTOR_DATA`
  table that all four `run_selftest`s consult before touching the filesystem.
- **R-7.10b** `flash doctor` told a wheel-installed user they were in an **editable
  checkout**, because the answer came from `Path(sys.executable).resolve()` — a macOS
  venv symlink pointing at the Homebrew framework, which is not a prefix of anywhere
  the package lives. It is now read off the package path: under
  `site-packages`/`dist-packages` is an installed copy.
  Vector for both: six checks and three mutants in
  `benchmarks/backend_free_check.py` → **`backend-free checks: 37/37 passed`**,
  **`backend-free mutants: 8/8 gates defeated by exactly their checks`**, plus a live
  repeat in a package-only tree where all four print the sentence and none prints a
  traceback. The gate that requires the same guards to say *nothing* when the four
  paths are present is what keeps this from being a vector that refuses to run.
- **R-7.10c** the refusal table was a list of four, and a list cannot grow a
  data-dependence on its own. Two more selftests were found broken on a
  package-only install by sweeping the table instead of trusting it:
  `flash.tourney --selftest` resolved its task file against the **caller's working
  directory** and died with `FileNotFoundError`, and `flash.lsp --selftest` read the
  minishop fixture and died three frames away from the absent directory with
  `ValueError: substring not found` — an error that names no path, which is exactly
  what a user reads as a broken product. Both now anchor on the package and refuse
  through `doctor.vector_refusal()`, and the table has six keys. The sweep
  (`benchmarks/backend_free_check.py`) copies `flash/` to a temp dir with no
  `benchmarks/`, discovers every module with a module-level `run_selftest` from the
  copy's sources, runs each from a foreign cwd, and classifies any death by
  re-running it in a second copy with the data symlinked back in: passes-with-data
  is data-dependence and must be tabled, fails-either-way is machine state and is on
  a named three-entry allow-list. **16 modules were swept, 6 refused, and the
  refusals equalled the table's keys.** Its first mutant printed **0 checks
  failing** because the planted module had no `if __name__ == "__main__"` tail, so
  `python -m … --selftest` imported and exited 0 — the spine gate now requires every
  swept module to have been dispatched. Five checks and two mutants added:
  **`backend-free checks: 42/42 passed`**, **`backend-free mutants: 10/10 gates
  defeated by exactly their checks`**, §6 **1119 checks + 20 oracle verifications =
  1139** with **85** mutants, re-read in a measured **15 min 9 s**
  (`benchmarks/results/battery_reread_r710c_20260927.log`).
- `benchmarks/portable_paths_check.py` now takes a `flock` on a per-checkout lock
  file and exits 2 rather than running twice at once. Found by a §6 battery run
  that printed **BAD … defeated 0 mutants, not 7** while every gate still said
  14/14. It did not reproduce alone — same interpreter, same tree, **14/14 + 7/7**
  twice, and again through the battery's own `--quick` path. So it was chased by
  construction instead: the mutants write real bytes into three corpora and
  restore them, and launching two runs together in a throwaway clone reproduced
  the shape on purpose — **14/14 gates** with **5/7** and **6/7** mutants.
  Concurrent execution is therefore a *sufficient* cause of what the battery
  caught; that it was this run's cause is inferred from being the only difference
  available, and is not proven. The dangerous half is a mutation being reverted
  by the other run before the gates read it, which disarms the check that exists
  to catch a host path going back while the suite still looks green. And it does
  not stop at a wrong report: while two of this file's own runs were alive at
  once, one snapshotted `docs/portability.md` while the other had its
  `doc_silent` mutation applied, so the restore wrote the mutated bytes back and
  the group name the vector exists to check for was gone from the doc on disk
  afterwards. The next honest run caught it as `13/14` with
  `missing from doc: ['traces']`. That is the blast radius, and it is why this is
  a lock rather than a docstring warning.
  Verified as
  a behaviour, not a code read: with the lock held the run prints the holder's pid
  and exits 2, with `--exclusive` it proceeds and defeats its mutant (1/1), and
  alone it still prints **7/7**.
- `requirements.txt` mirrored `pyproject.toml`'s dependency floors with one of
  them wrong: `mlx-lm>=0.24` against the package's `>=0.31`, on a file whose whole
  purpose is to give the same four things. Diffed all four by parsing
  `pyproject.toml` rather than by eye; `mlx-lm` was the only mismatch, and the
  floor is now 0.31. `docs/models.md` documents 0.31.3, which is what is installed
  here — and no §6 number depends on the line, because the offline battery loads
  no model; the live arms do. Nothing in the tree compares the two files, so the
  note in the header says the diff was done by hand and when.
- `flash/debug.py` exec'd the test's `sys.path` bootstrap **after** the candidate
  and inside the traced region, so a candidate that imports the repository at top
  level died on its own `import` and the `--debug` digest reported the harness's
  crash instead of the candidate's execution trail. Measured against the pre-fix
  build over all 72 context reference solutions: **42 changed verdict**
  (`ModuleNotFoundError` → `pass`) and they are exactly the ones whose candidate
  imports the repo; the other 30 were never affected. `run_test` had always
  hoisted it first; the tracer now does the same, with tracing off during the
  bootstrap (`benchmarks/results/debug_order_probe_20260927.log`). Found by reaching
  the debug seam while porting the suites — `--selftest` (55/55) and `--suite`
  (32/32) passed before and after, because the corpus those two run against has no
  bootstrap line at all for the bug to live in. This does **not** reopen R-4.3's
  measured miss: the band tasks that carry a bootstrap get the same verdict and the
  same trail length under both builds, so neither A/B arm was fed a crash.

### Packaging
- `pyproject.toml` with a platform marker on `mlx-lm`, so a non-Apple-silicon
  install skips the model layer instead of failing to resolve it. **Both halves of
  this bullet were superseded the day it stopped being true.** The clean-clone box it
  called open is R-7.5, which closed on 2026-09-28 with all four install shapes RUN —
  fresh clone, wheel in a throwaway venv, editable clone and unpacked sdist — the last
  of them carrying the whole §6 battery to the same printed totals from inside the
  download. And the measurement it offered as what *was* verified, **2 of the 26
  modules failing** with `mlx` blocked (`flash.decide` at its `import mlx.core`,
  `flash.route` through it), is the pre-fix figure: R-7.7 moved the import inside the
  call, so all 26 import and the sweep prints `SWEEP 26/26`. It stays quoted here
  because it is the reason the box existed, not because it is what a reader would
  measure today.
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue
  and PR templates, Dependabot, and a CI workflow with a model-free job on ubuntu
  and the §6 battery on macOS. **The workflow has never executed** (no remote is
  configured). It used to call four things that did not exist — `flash --version`,
  `flash doctor`, `benchmarks/backend_free_check.py` and
  `benchmarks/battery_reread.py --backend-free` — and all four now do, which is why
  the dangling-pipeline sentence in `SPEC.md`, `CONTRIBUTING.md` and `docs/models.md`
  became history in this pass. **That is what the files say; no run on GitHub's side
  has proved they hold, and the ubuntu job in particular has never had a Linux
  interpreter run this battery**, which is a wider claim than R-7.7's Apple-Silicon
  import sweep supports. Every `flash` command either workflow prints is gated by
  `benchmarks/documented_commands_check.py`, which parses them against the real
  parser on every §6 re-read.

## [0.0.1] - tagged 2026-09-28, still not published

**Tagged, not released.** `v0.0.1` is an annotated tag on this commit, cut the day
R-7.5 closed — the box that gated it. It is still not downloadable, because no remote
is configured: a tag on a laptop is a promise, and the promise is kept only when
someone pushes it. The version was deliberately not bumped to `0.1.0` for this; the
number the package prints is repeated in `flash/__init__.py`, in the landing page's
own `VERSION` constant, in the exported transcript the page renders, and in the
`flash 0.0.1` lines of SPEC, CONTRIBUTING and the witnesses under
`benchmarks/results/` — one fact kept true in several places, and relabelling it would
mean re-measuring the generated page for a digit. The notes below are the release's,
and its counts are the tree's printed ones as of the last §6 re-read.

A local, verify-first coding agent for Apple Silicon, with 1,119 offline checks +
20 oracle verifications + 85 mutation gates, and a `SPEC.md` that records which of
its own gates measured NO.

### Added — perception
- **R-1.1** `flash/lsp.py`: when a failure names a symbol the repository defines,
  the *real source* of that symbol is put into the retry prompt (not just into
  the record) — budget 1200 chars, ranked so the thing actually at issue comes
  first. Certified at the seam that matters: a stored prompt carries it.
- **R-1.1c** that budget now overflows instead of aborting — an oversized symbol
  is clipped at a line boundary and the symbols left behind are counted in the
  block, rather than the whole hint vanishing.
- **R-1.1b** both perception blocks are individually withholdable
  (`--no-source-hint`, `--no-graph-hint`), which makes "does a hint help?" a
  measurable question. Its first reading is a stated nil (see below).
- **R-1.2** cross-file go-to-definition and project-wide references: the AST owns
  symbol kinds, `pylsp` owns resolution, and every call degrades to the AST answer
  when the server is missing or slow.
- **R-1.3 / R-1.3b** `flash/graph.py`: an AST call graph answering a blast radius
  in about 0.1 ms (measured: 44 checks + 12 mutants), now feeding a retry prompt
  the dependents of the symbols at issue.

### Added — verification and execution
- `flash/harness.py`: assertions run one at a time in a subprocess, and a failing
  assert is reported as `GOT`/`WANT` rather than as a traceback line — which is
  what makes a retry about a *value* instead of about a stack.
- **R-9.2** every candidate-execution seam goes through `flash/sandbox.py`: one
  Seatbelt profile via `/usr/bin/sandbox-exec` (writes confined to a root,
  outbound network denied) plus inherited `RLIMIT_CPU`/`RLIMIT_FSIZE`. Proven with
  a hostile `~/.ssh` write and an open socket, both of which come back as ordinary
  verify failures. On macOS no memory rlimit can be set at any value, and
  `sandbox.status()` prints `UNAVAILABLE` instead of claiming the bound.

### Added — agent policy
- **R-3.3** tournament mode, gate MET: pass@1 5/7 → best-of-3 6/7 (+14 points) at
  40 % lower spend.
- **R-5.3** task-granular checkpoints: a `kill -9` between two tokens resumes
  *inside* the task, measured twice on a real 7B.
- **R-2.3** prospective confidence from four verification streams, wired to the
  ledger.
- **R-3.2** symbol-precise patching (`--edit`): clause 1 MET, clause 2 missed by
  one line, for a stated reason.
- **R-7.2** ambient mode: an idle window that drafts, verifies its own drafts, and
  cannot commit or push.

### Added — learning
- **R-6.1/R-6.2** the outcome ledger and the router fit on it.
- **R-6.3** `trainable()`'s extra exclusion, audited as costing no measurable AUC
  — at stated precision.
- **R-6.4** a gated, resumable LoRA path: verified-outcome dataset, kill/resume
  under `kill -9`, leakage rule, adapter identity (31 checks + 14 mutants).

### Measured, and NOT met
These stayed open because the number said so. Each is a negative result in
`SPEC.md`, not a removed requirement:
- **R-4.2** latency cost ≤ 5 % — measured, not met, mechanism identified
  (constrained decoding's per-step cost is 0.4 % of the decode step; the residual
  is the read-back sync).
- **R-4.3** debug feedback beating traceback feedback by ≥ 2 solves — three
  substrates, none of which held; booked as a miss at 27/30 vs 25/30.
- **R-2.3** the offer-rate gate — one clause is unmeasurable rather than passed.
- **R-6.4** trained arm 16/20 where the frozen base is 18/20, and a shuffled-label
  control beats the trained arm. Booked as a negative.
- **R-8.1** speculative decoding — negative on both targets.
- **R-1.1b** whether the hint blocks help: at n=10 with 3 attempts, `both` solved
  3/10 and each of `source only`, `graph only` and `off` solved 2/10. One task is
  10 points on this instrument, so that is a nil, not a win.

### Corrected
- **R-1.1** the symbol hint was writing into the attempt record and never into the
  prompt. Fourteen green checks and four live runs had been crediting symbol
  injection with things it did not do; every affected claim in this repository was
  retracted in place rather than deleted.

### Not in this release, by design
- Any hosted or server form (no API, no multi-tenancy — it is a local CLI).
- Generation on Linux/Windows (the `mlx` model layer is Apple-silicon-only).
- A 16 GB co-residency arm, a 24-hour soak, a 10-developer week-long feel test, a
  real-microphone arm, and watts/task — each blocked on hardware, sudo, or people,
  and each named as such in `SPEC.md`.
