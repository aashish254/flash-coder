# Changelog

Notable changes per release, written so a claim here can be traced to the run
that printed it. `git log --oneline` is the complete history — each commit naming
the measurement that decided it — and this file is the readable summary of it.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
- `docs/portability.md`: what the token contract is, what was scrubbed, which
  witness files are deliberately NOT rewritten and why, and the published residue
  count for the exclusion (`RECORD_RESIDUE`, asserted `≤` what the tree carries).
- Two open boxes this pass created out of measurements, not intentions. **R-7.7**:
  with `mlx` blocked, **24 of the 26** modules import and `flash.decide` /
  `flash.route` are the two that do not — unnoticed because `flash.cli` imports both
  lazily. **R-7.8**: gate every `flash …` command printed in a doc against
  `flash.cli.build_parser()`, which is how the setup block below was found.

### Corrected — claims this file made that the tree does not support
- This file's own `### Added` section listed `flash doctor`, `flash selftest --all`
  and `flash --version` as shipped. **None of the three exists** — `python -m
  flash.cli` rejects `doctor` and `selftest` as invalid subcommands, measured by
  parsing 23 documented subcommands against the real parser (21 resolve). They are
  SPEC R-7.6's open box, and they have moved to "not implemented" language in
  `CONTRIBUTING.md`, `docs/models.md` and this file.
- `## [0.1.0] - 2026-09-27` dated a release that has not happened. No tag exists
  and no remote is configured, so the header now says so outright.
- Two documents cited a different module count for the same fact: `pyproject.toml`
  and `docs/models.md` said 24 of the 26 import without MLX, the CI comment said 25.
  Measured 24, so `ci.yml` is the one that moved.
- "`pip install flash-coder` works on Linux and Windows" became a gated claim: no
  clean-clone install has been run (SPEC R-7.5), and `pyproject.toml`'s comment that
  cited `benchmarks/backend_free_check.py` as its verifier now says that file is
  unwritten.

### Fixed
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
  install skips the model layer instead of failing to resolve it. **Gated, not
  measured:** that file has never been installed from a clean clone into a
  throwaway venv, which is SPEC R-7.5's open box. What IS measured is the import
  half of the same claim — with `mlx` blocked, 2 of the 26 modules fail
  (`flash.decide` at its `import mlx.core`, and `flash.route` through it) and the
  other 24 import, `flash.cli` among them, because it imports both lazily.
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue
  and PR templates, Dependabot, and a CI workflow with a model-free job on ubuntu
  and the §6 battery on macOS. **The workflow has never executed** (no remote is
  configured), and it currently calls four things that do not exist yet:
  `flash --version`, `flash doctor`, `benchmarks/backend_free_check.py`, and
  `benchmarks/battery_reread.py --backend-free`. Those are SPEC R-7.6 / TODO #31,
  and until they land the published pipeline is dangling, not green.

## [0.1.0] - not yet published

**This release does not exist yet.** No tag has been cut and no remote is
configured, so nothing here is downloadable; the section below is the draft notes
for the release that TODO #32 tags once R-7.5 and R-7.6 close. Its counts are the
tree's printed ones as of the last §6 re-read, and `flash/__init__.py` still says
`__version__ = "0.0.1"`, so a wheel built today would be labelled 0.0.1.

A local, verify-first coding agent for Apple Silicon, with 1,066 offline checks +
20 oracle verifications + 69 mutation gates, and a `SPEC.md` that records which of
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
