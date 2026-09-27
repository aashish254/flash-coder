# Flash Coder

A coding agent that runs entirely on one Mac. It writes the change, executes the
test, reads **which assert failed and what the value actually was**, and tries
again — no API key, no container, nothing leaving the machine.

Two things about this repo are load-bearing, and both are visible in the files:

- **Every claim is a printed number.** The offline verification battery is
  **1,119 checks + 20 oracle verifications + 85 mutation gates**, and `SPEC.md` §6
  records why the totals come from the fraction each run *prints* — never an exit
  code, never a phrase grep — after both of those shortcuts produced a wrong total
  that looked clean on this project's own output.
- **The failures sit in the same file as the wins.** Speculative decoding faults the
  GPU on this hardware (R-8.1, measured negative); the live hint A/B is a nil
  (R-1.1b); the trained adapter has never been played against the frozen harness
  (R-6.4, deferred by the author's other project). A project that publishes only its
  wins cannot be verified, only believed.

## Requirements

- **Anything that generates text needs Apple Silicon.** The model layer is MLX
  (`mlx-lm`, `mlx-vlm`), and `pyproject.toml` marks it by platform rather than
  letting a Linux install fail to resolve.
- **Everything else is pure Python 3.11+**: symbol perception, the AST knowledge
  graph, the harness and its GOT/WANT oracle, trace/replay, the outcome ledger, the
  power governor, the sandbox and the whole offline battery. With MLX blocked in a
  child process, **26 of the 26** submodules of `flash` still import, and the one
  thing that raises is the call that needs a forward pass — with a message naming
  MLX instead of a stack trace (SPEC R-7.7, gated by
  `benchmarks/backend_free_check.py`).
- **What is NOT claimed:** that a Linux or Windows machine has installed this. The
  install shapes that HAVE been run are a fresh clone's editable install, a wheel in
  a throwaway venv, and an unpacked sdist with no `.git` — all on Apple Silicon, all
  logged under `benchmarks/results/` (SPEC R-7.5). On those other platforms the
  non-generating half is *expected*, not verified.
- RAM decides what you can run, not the OS. `power` prints the profile the machine
  offers right now and the largest model it may load; the fast tier is ~4.4 GB of
  resident weights and the brain tier is 15–17 GB.

## Install

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .[dev]                    # `flash` lands in .venv/bin
.venv/bin/python -m flash.cli doctor               # nine answers about THIS install
.venv/bin/python -m flash.cli selftest --all       # the offline battery, no models
```

`doctor` is the command to run first because it answers the question a README
usually dodges: which half of this project is present on your machine. It reports
the install shape (a wheel has no `benchmarks/` beside the package, so it says
"no verification surface here" rather than failing checks that never existed), the
backend, the model cache against the four §22 tiers, the tools the vectors import,
and what the box may load — and its exit code follows its own page.

`selftest --all` is a thin wrapper on `benchmarks/battery_reread.py`: the same 33
lines, the same printed totals, and the same refusal (rc 2, naming the path) when
there is no battery to run.

That refusal is the rule for every vector that reads the data tree beside `flash/`.
A wheel install carries no `benchmarks/`, so the six selftests that do — `graph`,
`grammar`, `debug`, `patches`, `tourney` and `lsp` — print one sentence naming the
path they need and exit 2 rather than raising `FileNotFoundError` for a file inside
`site-packages` (SPEC R-7.10). That list is not kept by hand: the same vector copies
`flash/` to a temp directory that carries no data tree, runs every module in the copy
that exposes a selftest, and classifies each death by re-running it with the data
symlinked back in — which is how `flash.tourney`'s cwd-relative task path and
`flash.lsp`'s `ValueError: substring not found` joined the refusals instead of
escaping the gate (SPEC R-7.10c). Run them from a clone or an unpacked sdist.

## First run

Every `flash …` command below works with no model at all; the ones that generate
need weights first.

```bash
# 1. no model needed: read a repo the way the agent does
.venv/bin/python -m flash.cli symbols --path benchmarks/fixtures   # whole-tree AST outline
.venv/bin/python -m flash.cli graph bulk_discount_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli context --path flash                 # token-budgeted skeleton

# 2. get the fast tier (~4.4 GB, the default small model)
.venv/bin/python benchmarks/m0_bakeoff.py --download --models qwen25-coder-7b

# 3. one task, the full policy. --test is a file of plain asserts — the same
# oracle the loop retries against, and the thing that makes "it worked" a measurement
.venv/bin/python -m flash.cli run "Write cents_to_str(n) -> str" --test t.py

# 4. a suite, with the loop's own cost report
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/m2_tasks.jsonl --with-context
```

`.venv/bin/python -m flash.cli --help` lists all 30 subcommands, and `bench/`,
`trace`, `resume`, `ledger` and `learn` are how a long run is inspected rather than
re-run.

## What is where

| | |
|---|---|
| `SPEC.md` | the contract: 9 requirement groups, each with its vector and its status, **including the gates that measured NO** |
| `TODO.md` | the same requirements as boxes, each carrying the measurement that closed it |
| `CHANGELOG.md` | what shipped, and the claims this project struck from its own docs |
| `benchmarks/` | the vectors. `benchmarks/results/` holds the runs that published numbers cite |
| `docs/` | `architecture.md` (the loop and its seams), `models.md` (tiers, versions, platform), `portability.md` (what a stranger's machine must not need), `config.md` (every knob is a flag; there is no config file), `privacy.md` (what leaves the machine, what gets written where), `methodology.md` (how the numbers above were produced, and the four rules that catch a green lie) |
| `flash/` | the package. `cli.py` is the whole surface, in one `argparse` object, so a check can parse commands without running them |

Every command printed in every tracked document is parsed against that one parser on
each battery run (`benchmarks/documented_commands_check.py`), so this page and the
CI pipelines cannot quietly drift from the CLI. The 34-section master plan that
produced `SPEC.md` is the author's working document and is **not** shipped here;
`SPEC.md` is the artifact that stands on its own.

---

## M0 — Model Bake-Off (first gate)

Validates the §22 model matrix on real hardware before anything else is built.

```bash
# 1. environment (one time). Python 3.11+; use whichever python3.11 you have
#    (brew, pyenv, uv, system) by name, not by spelling where it lives.
python3.11 -m venv .venv
.venv/bin/pip install -e .

# 2. validate the harness (no downloads needed)
.venv/bin/python benchmarks/m0_bakeoff.py --dry-run

# 3. smoke test: 7B coder (~4 GB)
.venv/bin/python benchmarks/m0_bakeoff.py --download --models qwen25-coder-7b
.venv/bin/python benchmarks/m0_bakeoff.py --run      --models qwen25-coder-7b

# 4. the real bake-off: MoE contenders (~15–17 GB each)
.venv/bin/python benchmarks/m0_bakeoff.py --download --models qwen3-coder-30b qwen3-30b-instruct gpt-oss-20b
.venv/bin/python benchmarks/m0_bakeoff.py --run      --models qwen3-coder-30b qwen3-30b-instruct gpt-oss-20b

# 5. results
.venv/bin/python benchmarks/m0_bakeoff.py --report
```

**What it measures** (per model, on 20 executable coding tasks):
pass rate · avg TTFT · avg tokens/sec · peak Metal memory · load time.
Results are saved to `benchmarks/results/m0_<timestamp>.json`.

**Candidates** (all MLX 4-bit, per PLAN §22):
| key | model | role |
|---|---|---|
| `qwen25-coder-7b` | Qwen2.5-Coder-7B-Instruct-4bit | smoke test / fast tier |
| `qwen3-coder-30b` | Qwen3-Coder-30B-A3B-Instruct-4bit | contender: coding specialist (3.3B active) |
| `qwen3-30b-instruct` | Qwen3-30B-A3B-Instruct-2507-4bit | contender: general agent (3.3B active) |
| `gpt-oss-20b` | gpt-oss-20b-MXFP4-Q8 | contender: OpenAI open MoE (3.6B active) |

**Not here (by design):** Qwen3-VL-30B needs `mlx-vlm` (vision path) → M0b.
Vision is benchmarked once the text brain is picked (PLAN §M1–M2).

## M1 — agent loop & decision fabric

```bash
# System One decisions: one prefill, restricted softmax, zero generation
.venv/bin/python -m flash.cli decide-batch          # routing eval (12/12)

# The agent loop: ACT -> PERCEIVE (LSP) -> VERIFY (GOT/WANT) -> sampled retry
.venv/bin/python -m flash.cli solve-all             # 20-task pass@1 vs pass@3
.venv/bin/python -m flash.cli solve-all --tasks benchmarks/tasks/m2_tasks.jsonl \
    --with-context                                   # M2 repo suite + skeleton injection
.venv/bin/python -m flash.cli route-test             # small-vs-big routing vs known labels
.venv/bin/python -m flash.cli run "task" --test t.py --context benchmarks/fixtures
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/m2_tasks.jsonl --with-context
                                                     # ^ full policy: PERCEIVE->ROUTE->small->ESC
.venv/bin/python -m flash.cli ledger --tail 10       # M3: outcome ledger (router training data)
.venv/bin/python -m flash.cli router-fit             # M3: learned router from outcomes (LOO report)
# learned gate is OFF by default since held-out m7 (2026-09-24): trained with its own
# big-direct rows it big-directed 8/8 easy tasks; router-fit/autofit now train on
# honest labels only (big-routed rows excluded). Enable explicitly with --threshold 0.5;
# run-suite then auto-refits the router every 10 new ledger rows (self-improvement):
.venv/bin/python -m flash.cli run-suite                          # reactive-only (default, proven)
.venv/bin/python -m flash.cli run-suite --threshold 0.5          # enable learned gate
# A router bundle belongs to ONE model: the PCA basis and the logistic fit are a
# function of that model's hidden states. Since 2026-09-26 the embedding cache is
# keyed by the weights that filled it, every bundle records the repo and the
# pooling it was fit on, the live probe is embedded with THAT pool, and a
# dimension mismatch returns no score with a reason instead of raising inside the
# loop. The reason it is stated loudly: the fit pooled the last token and the
# serve path pooled the mean, so median |ΔP| across 106 cached prompts is 0.297
# (0.180 -> 0.511) and 47/106 would have been big-directed at cutoff 0.5 that the
# fit's own pool would not have. 316 of the 621 ledger rows whose prompt is
# cached reproduce exactly from the mean pool and 0 from the pool the fit used —
# the recorded route_p column is the wrong pool's output, measured not assumed.
# `python benchmarks/router_pool_audit.py` (offline once both pools are cached)
# prints it; `python benchmarks/router_portable_check.py` is the 20+5 vector.
.venv/bin/python benchmarks/router_pool_audit.py     # pooling skew, no generation

# M2 multi-WRITER: tasks with "multi": true write coordinated file sets
# (contract: fenced blocks headed '# file: name.py'; fenceless '# file:' splits and
#  heading-before-fence tolerated; per-file persistent merge across attempts)
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/mw_tasks.jsonl

# M0b vision: screenshot -> HTML via Qwen3-VL (mlx-vlm); PIL-drawn assets,
# tests are python asserts over the generated html string
.venv/bin/python benchmarks/gen_vis_assets.py   # regenerate screenshots
.venv/bin/python -m flash.cli run-vis-suite
.venv/bin/python -m flash.cli run-vis-suite --tasks benchmarks/tasks/visp_tasks.jsonl --attempts 3   # strict pixel-diff oracle, 7 tasks: VLM sees its own render + per-quadrant layout diff on retry
.venv/bin/python -m flash.cli run-vis-suite --tasks benchmarks/tasks/visr_tasks.jsonl --attempts 3 --max-tokens 2048   # real websites (example.com, HN front page), frozen screenshots




# Escalation policy: small model tries, hot-swaps to the big brain
.venv/bin/python -m flash.cli escalate-test

# M2 repo-scale perception
.venv/bin/python -m flash.cli context --path flash  # token-budgeted skeleton
.venv/bin/python -m flash.cli perceive flash/loop.py # LSP lint of one file

# The oracle itself is testable: VERIFY reports GOT vs WANT for the first failing
# assert, and those two values come from the SAME evaluation that decided the verdict.
# An assert whose side mutates is therefore reported honestly (`q.pop() == "high"` on a
# failing assert says GOT: 'low' | WANT: 'high', not GOT == WANT for a difference that
# was really there).
.venv/bin/python -m flash.harness --selftest        # 20 offline checks on the oracle
.venv/bin/python benchmarks/m0_bakeoff.py --dry-run # 20/20 reference solutions pass

# All 33 offline vectors in this file (32 check-summing + m0_bakeoff's oracle) in
# one command, summed from the fraction each run
# PRINTS (never an exit code, never a phrase grep — see SPEC §6 for the two
# capture traps that rule is there to prevent). Fails if the tree's total moves
# off SPEC §6's number. `--quick ambient lora` re-runs only named lines.
.venv/bin/python benchmarks/battery_reread.py       # 15 min measured 2026-09-27
.venv/bin/python -m flash.cli selftest --all        # the same thing, by name
.venv/bin/python -m flash.cli doctor                # nine answers about THIS install
.venv/bin/python -m flash.cli --version             # flash <__version__>, no stale copy
# `--backend-free` re-runs the battery with `mlx`, `mlx_lm` and `mlx_vlm` blocked in
# every child, and refuses to print a total until it has PROVED the block bites.

# §33.1 symbol perception: AST discovery + a live language server (jedi/pylsp).
# The loop uses this automatically: a failure that names a repo symbol gets that
# symbol's REAL source appended to the retry feedback. (Corrected 2026-09-27 —
# that sentence described a seam that had never been live. The helper worked and
# the loop appended its output to the RECORDED attempt, which is what `flash trace`
# prints, while the retry prompt was built from the plain error. Two lines moved
# it into `err` before both are built; checks 11-13 of `lsp-selftest` now drive
# `loop.solve` and read the retry prompt back instead of calling the helper.)
# And since R-1.1c the hint's 1200-char budget OVERFLOWS rather than aborting: a
# ranked symbol too long to fit is clipped at a line boundary and the symbols left
# behind are COUNTED in the block. Before it the budget test was a `break`, so ONE
# oversized symbol deleted the whole hint — an error naming `symbol_source` (1631
# chars of source on this repo) showed the model nothing at all, while two shorter
# ranked symbols beside it were never reached. Checks 14-18; the last reads the
# clipped block back out of the retry prompt, the way R-1.1 insists.
.venv/bin/python -m flash.cli find total_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli refs total_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli symbols --path benchmarks/fixtures          # whole tree (AST)
.venv/bin/python -m flash.cli lsp-selftest                                # 22 offline checks
#   the prompt claim, measured against the live corpus rather than asserted:
.venv/bin/python benchmarks/hint_live_audit.py --print       # 248 pre-fix prompts, 0 hits
.venv/bin/python benchmarks/hint_live_audit.py --expect present   # the live arm, 2 hits

# §28/R-1.3 knowledge graph — the AST-only answer to "what breaks if I change this?".
# No embedding and no vector store: nodes are functions/classes/modules/constants, edges
# are calls/reads/imports/inheritance, and every edge carries the line and source text
# that proves it plus the rule that bound it. The honest part is the floor: a name that
# matches two symbols becomes NO edge but is still COUNTED as a blind spot, so an empty
# answer cannot read as a clean one, and a symbol the graph does not have is reported
# ABSENT with the nearest addresses offered. `--live` asks the language server about this
# pass's own blind spots (outside the <200 ms clause, on purpose). Re-scan by hash
# re-extracts only changed files, and will not drop nodes for files the scan never
# covered. Wired into the loop as of R-1.3b: a retry now carries this graph's
# blast radius for the symbols at issue, not only the LSP's source (see below).
.venv/bin/python -m flash.cli graph bulk_discount_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli graph Cart.subtotal_cents --path benchmarks/fixtures --depth 2 --json
#   ...and the seam to the live server, which is slow on purpose (a server starts up) and
#   therefore counted outside the budget: it settles the blind spots THIS pass reported.
.venv/bin/python -m flash.cli graph bulk_discount_cents --path benchmarks/fixtures --live
#   the two ways an answer can be thin, both said out loud: a symbol nobody reaches
#   prints `0 symbol(s) reach it (nobody)` rather than nothing, and a symbol the graph
#   does not have prints `symbol not in the graph` and exits 1 — with
#   `no node named that; nearest addresses: …` when one shares text, e.g.
#   `.venv/bin/python -m flash.cli graph Cart.subtot --path benchmarks/fixtures`.
#   The trailing `?` line counts uses this pass could not place, so the number above it
#   reads as a floor and not as a census.
.venv/bin/python -m flash.graph --selftest --mutants   # 44 offline checks + 12 mutants

# §28.2 step 3 / R-1.3b — and the loop READS that graph on a retry. `graph.scope_hint`
# takes the ≤3 symbols `lsp.symbols_involved` ranks as at issue and emits their
# blast radius at depth 2, ≤6 dependents each, ≤900 characters, every line carrying
# its file:line and the `via` rule that bound it; a name the graph cannot place
# contributes NOTHING rather than a guess (the file the AST read is the tie-breaker).
# It rides the same assignment as the source hint, into `err` before both the
# `Attempt` record and the feedback template exist, so the model and the trace see one
# string — that seam is exactly where R-1.1's hint failed to reach the model for a
# month. One AST parse per retry serves both hints (32-33 ms shared against ~170 ms
# parsed twice on this repo's 26 files), and the graph is cached per repo root in an
# LRU of 8 so a long run refreshes by hash instead of holding one graph per task.
.venv/bin/python benchmarks/graph_perceive_check.py --sweep   # 33 checks + 12 mutants
#   `--sweep`, not the bare run, is the quoted form: several mutants live in the graph
#   CACHE, so the NUMBER a mutant fails depends on whether that process already built a
#   graph (2/2/7/2/14/2/7/2/2 together, 2/1/6/1/13/2/6/1/1 apart). What is claimed is
#   the identity — each mutant caught by its own named check, in both orders — and the
#   run fails if the two sweeps ever disagree.
#   live, on the real 7B: r03_bulk_rule's two retry prompts both carry
#   `Dependents of the symbols at issue` (`CartLine [class] minishop/models.py:20`,
#   `BULK_MIN_QTY [constant] minishop/pricing.py:5`), and the same failure's first
#   attempt carries neither hint. No accuracy delta is claimed there — that run predates
#   the measurement below.
.venv/bin/python benchmarks/hint_live_audit.py --header "Dependents of the symbols at issue" --expect present

# R-1.1b — and then the A/B that asked whether either block actually HELPS. Each block
# is now switchable on its own (`loop.HINTS`, and `--no-source-hint` / `--no-graph-hint`,
# both recorded in the suite's params so a trace states which arm wrote it), and the same
# frozen 10-task band was run four ways at --attempts 3 on the 7B.
# The answer, stated at its precision: a NIL. pass@N is both 3/10, graph-only 2/10,
# source-only 2/10, off 2/10 — +1 task over the control at n=10, i.e. one task — and the
# per-task flips are not ordered by arm (source-only LOSES a task the OFF arm solves).
# pass@1 is 0/10 in all four arms by construction: the arms share one greedy attempt-0
# answer per task, so it is the control rather than a measurement. What the run does
# establish is that the switches are not dead — the OFF arm injects a block on 0 of 18
# retries, source-only on 18 of 18, and never the other way round — and that the band
# cannot answer the question: 8 of 10 tasks are unsolved in every arm, so there are ~2
# tasks of headroom for a delta to live in. Neither block is shown to help and neither is
# shown to hurt; the switches stay on because that is the honest reading of a nil, not
# because a win was measured.
.venv/bin/python benchmarks/hint_ab_check.py                 # 14 offline checks + 8 mutants
.venv/bin/python benchmarks/hint_ab_report.py <4 arm session ids>   # the live table above

# R-7.4 — clone-anywhere portability, gated at the seams rather than asserted. Three
# task corpora used to store the author's checkout inside their ORACLE `sys.path`
# bootstrap, so on another machine 72 context tasks died with `ModuleNotFoundError`
# before the candidate's first line: verdicts printed, nothing scored. A corpus now
# writes `<REPO>`, expanded in one place (`harness._hoist_path_bootstrap`) because that
# is already the single function every execution seam passes through — `run_test`,
# `score`'s probe loop, `debug`'s tracer, `confidence`'s seeded re-runs. (`python -I`
# implies `-E`, so `PYTHONPATH` cannot carry the fixtures instead.) This vector launches
# from `benchmarks/fixtures`, not the repo root, and requires ALL FOUR seams to pass every
# reference solution and reject the frozen wrong answers — expanding the token at one seam
# and not the next is exactly the shape of the bug, and no single-seam test can see it.
# Reaching the debug seam found a pre-existing defect there: the tracer exec'd the
# bootstrap AFTER the candidate and INSIDE the traced region, so a candidate that
# imports the repo at top level died on its own import and `--debug` fed the model the
# harness's crash instead of a trail. Sized by probe, not story: of the 72 context
# reference solutions, exactly the 42 whose candidate imports the repo changed verdict
# (ModuleNotFoundError -> pass) against the pre-fix build, and the 30 others were never
# in reach. Fixed. Both `flash.debug --selftest` (55/55) and `--suite` (32/32) passed
# before and after — the corpus they run against carries 0 `sys.path` lines, so neither
# ever built the bootstrap the bug lived in.
# Witness data is NOT rewritten: a trace or ledger prompt whose text was edited to look
# portable is no longer evidence, `adapter_config.json` is read by `flash/train.py`, and
# the embedding caches are keyed by the prompt text a cited fit consumed. The exclusion is
# checked instead of granted (0 executables under `benchmarks/results/**`, five named
# record groups, a published residue floor). See `docs/portability.md`.
.venv/bin/python benchmarks/portable_paths_check.py          # 15 checks + 7 mutants

# "Is what I just installed the thing the README describes?" One command answers it, and
# the answer is gated rather than narrated: `flash doctor` walks nine lines across six
# sections (install shape, backend, machine, cache, tools, what to verify) and its exit
# code follows its own page — a synthetic broken install in a temp tree must print `no`
# on exactly 5 of 9 and rc 1. R-7.7's half: with `mlx`, `mlx_lm` and `mlx_vlm` blocked in
# a CHILD process, all 26 submodules of `flash` still import and the only thing that
# raises is the call that needs a forward pass. The sweep is enumerated, not named, and
# its mutant plants a real backend-importing submodule on disk to prove it. R-7.10's half:
# the six selftests that read `benchmarks/` beside the package are run against a synthetic
# wheel-shaped root and must come back rc 2 with one sentence and no traceback — and a
# sibling gate points the same guards at a tree that HAS the data and requires silence.
# R-7.10c's half: that six is measured, not remembered — the package is copied to a temp
# directory with no data tree, every module in it with a selftest is run there, and a death
# is called data-dependence only if the selftest passes once `benchmarks/` is symlinked back
# in. Two real bugs came out of that sweep on its first run; its mutant plants a seventh.
.venv/bin/python benchmarks/backend_free_check.py            # 42 checks + 10 mutants
# Every `flash …` line printed anywhere in this repo — README, CONTRIBUTING, SPEC, docs
# and the two published workflows' `run:` blocks — is parsed against the real argparse
# parser, without dispatching, so this file and the pipeline on the front page cannot
# both drift from the CLI. 25 commands, 150 citations, 15 documents today. Deleting a
# subcommand from the parser is not enough to fail it either: the failure must NAME the
# citing file and line.
.venv/bin/python benchmarks/documented_commands_check.py     # 8 checks + 5 mutants

# §33.1 ACT leg — symbol-precise edits: a change request is answered with patches that
# name a SYMBOL, and the AST's own lines are what gets replaced. Everything outside the
# addressed range is copied, never re-typed — and an address wider than the change (a
# class named where the edit lives in one method) is narrowed to the runs whose bytes
# actually differ, so a sibling the model left alone is not re-emitted from its text. The
# run report says which: `[1 of 13 lines rewritten]`. Out-of-range, ambiguous or
# overlapping addresses are REFUSED (the retry is
# told why and the project keeps its previous content). Off by default; --edit turns it on
# for a task that ships a project.
.venv/bin/python -m flash.cli run "..." --test t.py --context src/ --edit
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/edit_tasks.jsonl --edit
.venv/bin/python -m flash.patches --selftest     # 46 offline checks, incl. the loop arm
.venv/bin/python -m flash.patches --suite benchmarks/tasks/edit_tasks.jsonl  # the suite's premise

#   the patch protocol (one header + one fenced block per symbol):
#     # edit: cart.py :: Cart.total_cents
#     ```python
#     @property
#     def total_cents(self):
#         return sum(l.line_cost() for l in self.lines)
#     ```
#   `Container.name` for a method, `L12-L18` for one statement, `*` for a whole file
#   (which the run report counts, because needing it is the thing patches avoid).

# §33.3 constrained decoding: the output contract becomes a per-step token mask, so a
# fence without its '# file:' header, prose before the first header, a path outside the
# declared file set, or a block that could never be finished cannot be emitted at all.
# Off by default; --constrain turns it on for a run or a whole suite.
.venv/bin/python -m flash.cli run "..." --test t.py --constrain
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/mw_tasks.jsonl --constrain
.venv/bin/python -m flash.grammar --selftest     # 47 offline checks: DFA, masks, liveness, no dead ends
.venv/bin/python -m flash.grammar --census --n 100 --tasks benchmarks/tasks/mw_tasks.jsonl   # live: violations + throughput
.venv/bin/python -m flash.grammar --overhead     # live: ms the mask adds to one decode step

# §33.2 debugger skill: a failed test is re-run under a line tracer in the same isolated
# subprocess, and the retry is told where the value was MADE (executed trail, per-variable
# value history, last-mutated line) instead of only where the assert noticed it.
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/dbg_tasks.jsonl --debug
.venv/bin/python -m flash.debug --selftest       # 55 offline checks, incl. the suite's premise
.venv/bin/python -m flash.debug --suite          # every seeded bug: invisible to the traceback, named by the digest
.venv/bin/python -m flash.debug --suite benchmarks/tasks/dbg_band_tasks.jsonl   # the same proof for a two-bug task

# The A/B instrument (R-4.3's open gate, TODO's P2 follow-up). Every earlier suite was
# degenerate for the gate "debug feedback beats traceback feedback by >= 2":
# dbg_tasks and dbg_blind_tasks are 8/8 ceilings in BOTH arms (attempt 0 solves,
# so no retry and no feedback is ever sent), and dbg_hard_tasks' band is one task
# wide. What the gate needs is tasks this tier RETRIES, so the suite is half
# selected by evidence — every task the ledger records as solved on the small
# tier at attempts >= 2, never shed — and half grown to that shape: blind
# repairs with the tests withheld and TWO independent bugs, from a library of 31
# bug families over 14 correct references. A mutant survives only if the oracle
# sees it (a raise disqualifies it: the exception names its own line), both
# causing lines are invisible in the failure text and named by the digest, and
# each bug still fails the test alone. 30 tasks: 18 measured + 12 generated.
.venv/bin/python benchmarks/gen_dbg_band.py --report          # ~2s, deterministic, no model
.venv/bin/python benchmarks/dbg_band_check.py  # 172 checks + 5 mutants: recomputes the band from the ledger
# arm A (traceback) / arm B (--debug) on it — MEASURED 2026-09-26, AC, small tier,
# --attempts 3 --allow-big never, one model load per arm:
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/dbg_band_tasks.jsonl --attempts 3 --allow-big never --max-tasks 30 [--debug]
# The band is wide (15 of 30 tasks needed a second or third attempt, against one
# task on the old substrate), and the gate still misses: A 27/30, B 25/30, both
# arms 52 attempts, 414.5s -> 463.5s (+11.8%). On the 25 tasks that ran the small
# tier in BOTH arms it is 25/25 vs 25/25 — the whole two-task gap is two
# escalations §34.1 denied, the same shed-tier confound as R-6.4's weights arm.
# The digest's footprint is two tries and one outcome: 28 of 30 attempt counts are
# identical, fix01_alias_sort went 3 -> 2 (reproduced from the earlier A/B, solved
# either way), mw1_ringbuf 2 -> 3 and that third try is what crossed the denied
# escalation, and on h12_min_remove_parens both arms used 3 tries and only the
# traceback arm's third one passed. Net: 0 tasks gained, 2 lost. Logs
# benchmarks/results/dbg_band_arm{A,B}.log. --debug stays off by default.

# §33.4 tournament mode (R-3.3): a hard task gets k INDEPENDENT candidates instead of
# a feedback chain — candidate 0 greedy (so pass@k contains pass@1), the rest sampled
# from per-candidate seeds, each scored by the oracle, first pass adopted and the arm
# exits. The power governor's width (4/2/1 by profile) is the AC-only clamp: on
# battery or in a low-power blip the tournament degenerates to the plain single
# attempt, per task, with the refusal reason in the route record. Off by default
# (--tournament 1); single-file tasks only — multi-file/edit tasks keep the chain.
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/m3_hard_tasks.jsonl --tournament 3
.venv/bin/python -m flash.tourney --selftest     # 16 offline checks: schedule, clamp, adoption, trace record
# measured 2026-09-26 on the 8 h-tasks, small tier: pass@1 5/7 -> best-of-3 6/7 (+14
# pts, in-arm on matched input), suite 7/8 vs chain 6/8, at 3330 tokens vs 5559.

# §34.2 prospective confidence (R-2.3): the escalation offer comes from EXECUTION
# evidence, not the model's probability (which m7 measured dead at AUC 0.569). Four
# streams run on the answer the visible tests already accepted: static errors, the
# share of the answer's own statement lines those tests EXECUTED, its verdict re-run
# under three PYTHONHASHSEEDs, and adversarial calls shaped by the task's own test —
# where only an accident counts, never a `raise` the answer wrote itself, and never at
# a size the probe cannot afford: the shipped 10000-int battery entry cost 1445 MB and
# 2.29 s and came back as `HANG` about a CORRECT reference — our budget failing, filed
# as its bug (`_affordable` refuses it now). An answer whose own module body raises on
# import reads `edges: answer does not import (AssertionError)` — before, the probe
# child died with it and the parent filed the raw traceback, three lines of caret art
# inside a log that is one line per task. The report line prints the offer beside
# the held-out verdict, and the two gate clauses keep
# their own denominators (recall over answers the hidden tests sink, false offers
# over the ones they float, routine rows split out because the <1-per-20 clause is
# about routine tasks). Seconds of subprocess, no model; off by default on a suite,
# on by default for a single `run` (before that answer ships to you).
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/p6b_tasks.jsonl --confidence
# p6b is the WIDE instrument (59 rows: 8 seeded-subtle + 15 keyed routine + 36 keyed
# hard), built by `python benchmarks/gen_p6_key.py --wide`; p6_tasks.jsonl is the
# 23-row original. Both are keyed from each task's own shipped reference, so the
# expectation is held out of the prompt and the model never sees it.
.venv/bin/python -m flash.confidence --selftest   # 29 offline checks, no model
# measured 2026-09-26, 7B over the 23-row original: every answer survived its
# held-out key, so the >=90% recall clause had an EMPTY denominator (not a pass),
# and the false-escalation clause was missed by one offer in 15 routine answers.
# measured 2026-09-27, 7B over the 59-row wide instrument, both clauses now have a
# population: recall 4/11 (gate >= 90%) and 5 false offers over 48 hidden-accepted
# answers — 1 per 9.6 tasks against < 1 per 20 — so BOTH clauses are MISSED with
# real denominators rather than unmeasured. Offline, on the seeded subtle-bug suite
# where would-fail answers exist by construction, recall is 6/6 — and the two
# masked-value bugs stay unoffered, which is the documented blind spot, not a
# surprise. A clause with no denominator prints NOT MEASURABLE, never 0/0.
# measured 2026-09-27, 1.5B pilot tier over the same 59 rows: 52 answers pass the
# visible oracle and fail the held-out key, so recall finally has a population — and
# measures 44/52 = 84.6% (13/15 on routine rows). Still MISSED, and 5 of the 8 misses
# sit above the coverage threshold while 3 are the masked-value blind spot.
.venv/bin/python benchmarks/confidence_tau_check.py # 7 checks, no model: does a
# stricter coverage threshold close it? No. Re-derived from the arms' own recorded
# numbers (that re-derivation must reproduce the recorded offer on 59/59 answers at
# both tiers first), tau 0.85 clears the 1.5B's recall clause at 92.3% while the 7B
# reaches 63.6% and its routine false offers go 1 per 15 tasks -> 1 per 5. The two
# clauses pull opposite ways across tiers, so this gate is not a threshold knob.

# §34.1 power governor — every run asks the machine before loading a model
.venv/bin/python -m flash.cli power                    # profile, state, allowed vs shed
.venv/bin/python -m flash.cli power --json             # machine-readable
.venv/bin/python -m flash.cli power --selftest         # 22 offline decision-table checks
.venv/bin/python -m flash.cli run-suite --allow-big never   # single-track, never loads the brain
#   auto (default) obeys the profile: on battery/heat/memory pressure the big tier is
#   SHED — the task stays unsolved and the ledger records why. --allow-big always overrides.

# §34.3 background learning: idle + AC gated, wall-clock budgeted, resumable
.venv/bin/python -m flash.cli learn --check            # may it run right now? (exit 1 = refused)
.venv/bin/python -m flash.cli learn                    # refit the router inside the 15-min budget
.venv/bin/python -m flash.cli learn --status           # checkpoint: how far it got, what is next
.venv/bin/python -m flash.cli learn --selftest         # 20 offline gate + resume checks

# R-6.4 §27.3 layer 3 — learn from the agent's own verified outcomes (LoRA on mlx-lm)
# The data law is the product: only oracle-verified, closing attempts become rows, in
# ChatML with a dangling generation header, and every id in a frozen scoring suite is
# excluded from the training pool — counted, and proven by reading the written artifact
# back rather than trusting a counter. Training runs inside the same idle+AC gate as the
# router refit, in slices, so a `kill -9` between two slices resumes at the banked step.
# Every checkpoint is measured on the valid split and the BEST one is promoted to the
# adapter root, so `iters_done` (how far it trained) never equals `promoted_step` (which
# weights shipped).
.venv/bin/python -m flash.train --dataset --held-out benchmarks/tasks/m0_tasks.jsonl  # mine + split + write
.venv/bin/python -m flash.train --dry-run --held-out benchmarks/tasks/m0_tasks.jsonl  # the counts, write nothing
.venv/bin/python -m flash.train --suite-from-dataset --split train   # the in-distribution arm's suite
.venv/bin/python -m flash.train --selftest             # 36 checks: the data law, the slices, the promote
.venv/bin/python -m flash.cli learn --lora --adapter v1 --force      # fit under the gate (--force = AC only)
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/m0_tasks.jsonl \
  --adapter benchmarks/results/adapters/v1             # score an arm that carries the adapter
# The adapter directory a run names must hold weights: a name with no
# adapters.safetensors raises instead of quietly running the base model, because a
# before/after where both sides are the before is the most convincing wrong number
# this project can print. Every ledger row says which model decided it
# (`"small": "…-4bit+lora:v1"`, `"big"` never carries one).
.venv/bin/python benchmarks/lora_shuffle_control.py    # build the control: same shapes, values permuted
.venv/bin/python benchmarks/lora_path_check.py         # 33 checks + 15 mutants: gate, resume, leakage, identity, job name, arm denominator, --dry-run
# MEASURED 2026-09-26, m0 (20 tasks, AC): base 18/20 · +lora:v1 16/20 (twice) · the
# shuffled control 19/20. I-2's gate MISSED and is recorded as a negative in SPEC §5
# R-6.4 and §9's register: the trained arm's extra losses are all shed-tier escalations
# the governor denied, and its weights repeat '!!!!' past the end of the answer, so an
# attempt decodes its full 1024-token budget for a 147-token answer (~10x seconds).
# The in-distribution arm has no headroom by construction — the base model already
# solves 12/12 of the tasks the rows were mined from.

# §33.6/§33.7 observability, replay, resume — the trace session IS the snapshot
.venv/bin/python -m flash.cli run-suite --trace-full   # also store exact prompts/outputs
.venv/bin/python -m flash.cli trace                    # list sessions: outcome, tokens, wall time
.venv/bin/python -m flash.cli trace <sid> --task r03_bulk_rule   # replay one task's decisions
.venv/bin/python -m flash.cli resume                   # continue the newest interrupted suite
.venv/bin/python -m flash.cli trace --selftest         # 30 offline store/replay checks
.venv/bin/python benchmarks/trace_resume_check.py      # 11 checks: interrupt -> resume, no re-billing

# R-5.3 task-granular recovery — a kill inside a task resumes INSIDE the task
# §33.7 made an interrupted suite resumable, which left a gap exactly one task
# wide: the generation in flight was thrown away and the task restarted from
# attempt 0. flash/checkpoint.py closes it. While a suite is armed, every
# decoded token of the in-flight generation is folded into a frame keyed by
# (task, arm, stage, attempt) and flushed every 16 tokens through a temp+fsync+
# rename, together with the retry conversation and the multi-file `merged` union
# — so a resume prefills the text a dead process already paid for, restores the
# attempts it already settled, and never re-runs an arm the dead run exhausted.
# A `flash run` (no session armed) stays on mlx_lm.generate, byte for byte.
.venv/bin/python -m flash.cli resume                   # names the mid-flight task it continues
.venv/bin/python -m flash.checkpoint --selftest        # 31 checks: identity, flush, atomicity under real kill -9
.venv/bin/python benchmarks/checkpoint_resume_check.py # 35 checks: kill -9 a live run-suite, resume it, only the span continues
.venv/bin/python benchmarks/live_checkpoint_arm.py --tasks 3   # LIVE: kill -9 a real 7B mid-decode, resume, diff vs a control run
# measured 2026-09-26, offline with the model scripted and everything else real:
# the resume's prompt began at the dead run's last durable 64 characters and
# decoded only the remaining 125 of 189; the killed task settled at attempts=1;
# a span killed TWICE settled at attempts=1 carrying 128 characters from two dead
# processes; the multi-file task passed only because the dead run's file union
# came back; a big-tier kill skipped the small tier the dead run had exhausted.
# Live, same day, 7B on AC: the kill landed 3.5s in with 16 tokens durable, the
# resumed attempt produced a byte-identical answer (prompt_tokens 94 vs the
# control's 78 — grown by exactly those 16) in 455ms where the cold run spent
# 960ms decoding the same 22 tokens, the ledger agreeing at task granularity
# (1.3s resumed vs 1.8s cold, one row per task across the kill and its resume),
# and the untouched tasks changed nothing. The arm prints that comparison itself:
# it re-runs the same argv with no kill and sha1-compares every answer.

# §33.5 / R-7.2 ambient mode — idle windows that DRAFT and ship nothing
# `flash ambient` watches three deterministic checks (pyflakes over the shipped
# scope, every seeded task's premise, and the package map vs `flash/`), and for
# each red one it cuts a git worktree from HEAD, asks the small tier to fix it,
# and re-runs THE SAME CHECK as the oracle. A draft is offered only if its own
# finding is gone, nothing new appeared, and — for an ADD-only finding — not one
# existing line was removed. It commits to a local branch, writes a .diff, and
# never merges, never applies, never pushes.
.venv/bin/python -m flash.cli ambient --dry-run        # print what is red, touch nothing
.venv/bin/python -m flash.cli ambient --check          # may a window run right now? (exit 0/1)
.venv/bin/python -m flash.cli ambient --small mlx-community/Qwen2.5-Coder-7B-Instruct-4bit \
  --limit 1 --attempts 3                               # a real window; the gate needs idle + AC (--force to override)
.venv/bin/python -m flash.cli ambient --status         # list every prepared draft, verified or not
.venv/bin/python -m flash.ambient --selftest           # 61 checks + 6 mutations: the boundary, enforced
.venv/bin/python benchmarks/ambient_echo_probe.py --dry   # rebuild the 2x2 fixtures, no model
# MEASURED 2026-09-26, Qwen2.5-Coder-7B on THIS repo, one red drift finding: seven
# windows. The first five bought two oracle bugs and one wrong story — a verified
# draft that re-wrapped 20 lines it was only asked to ADD to (now refused, pinned by
# a check and a mutation), a work list read from the dirty checkout instead of HEAD,
# and a diff that silently dropped every file's trailing newline (the `# file:`
# fence cannot carry that byte; `carry_newline` puts it back at the write layer).
# The story that "this tier cannot echo a 45-line file" was then REFUTED by the 2x2
# above: all four arms (3x1, 52x1, 3x3, 52x3 entries) produced verified drafts, and
# the failing windows had echoed FEWER tokens than the passing arms. `--inspect`
# named the real mechanism: greedy decode writes `ambient — ambient context
# processing`, a bare name the map parser cannot read, because the prompt asked for
# "the module name" without saying in which form. Stating the accepted form fixed
# the live half on the first try — window 6 drafted in 33.6s on its greedy attempt,
# window 7 in 43.9s — and window 6's filler prose ("Ambient context processing for
# the coding environment") is why the prompt now quotes each module's OWN docstring
# line, so window 7's draft says what flash.perceive and flash.route really do.
# Boundary, across all seven: one worktree at a time and zero leaked, HEAD unmoved,
# zero pushes, no remote configured, no file touched outside the worktree.
# Not measured: an unattended overnight window — these were daytime and --forced,
# with the idle+AC gate opened by a synthetic profile state.

# §21 / R-9.2 — every candidate runs inside an explicit sandbox
# §21's substrate is a Firecracker microVM per rollout, which does not exist on the
# ship target (macOS/arm64 — and the offline battery must run with no Docker daemon),
# so flash/sandbox.py wraps the platform's OWN kernel mechanism instead: a Seatbelt
# profile handed to /usr/bin/sandbox-exec plus POSIX rlimits the child inherits
# across the exec. Candidate code cannot opt out of either: both are applied by the
# process that spawns it. Writes go only to the root the candidate was given (and
# /dev/null — a benign child that redirects a subprocess's stderr there would
# otherwise fail for our reason, not its own); network is denied outbound, so a
# connect is a PermissionError before a packet leaves and a hostname never resolves;
# RLIMIT_CPU is timeout*box-width+2s because a waited-for child's cpu accrues into
# its parent (a narrow limit would kill a CORRECT parallel answer for our
# arithmetic), and RLIMIT_FSIZE 256MB turns a disk-filling write into an ordinary
# verify failure. What this box cannot do is printed, not hidden: `memory` says
# UNAVAILABLE because setrlimit refuses RLIMIT_AS/DATA/RSS at any finite value here
# at any privilege; reads are NOT confined; and headless Chrome cannot be given a
# writable root (--user-data-dir=<root> exits rc=21, a ProcessSingleton it must
# keep after the run), so the §34 renderer is the ONE exempt path and its egress is
# killed with three flags instead (byte-identical PNGs, +10s wall only on a page
# that fetches). Cost, because a sandbox nobody can afford gets bypassed: +12.2 ms
# per child spawn (26.8 → 39.0 ms), 60 score() calls 6.04s → 8.31s.
.venv/bin/python -m flash.sandbox                    # what is enforced on THIS box right now
.venv/bin/python -m flash.sandbox --selftest         # 34 checks: the hostile-candidate vector, and no collateral
# The vector IS the clause: a candidate that writes to ~/.ssh and one that opens a
# socket both fail as ordinary verify errors, the sentinel file still does not exist
# afterwards, and the suite still RANKS the hostile one like a partial answer (1/2)
# and still hands the retry loop an ERROR line. A tilde spelled literally into
# open() is not the hazard (open() never expands it — the file lands inside the
# root), so the vector uses os.path.expanduser and says so. `seatbelt()` is an
# enforcement probe, not a Path.exists(): a wrapper that ignored its profile would
# degrade to no prefix and a loud status line, never to green checks.
# The five candidate-execution seams (harness run_test/_probes, debug _run,
# confidence _seeded_run/edge_probe) are verified at RUNTIME to spawn through
# sandbox.run — a selftest spy records the caller's frame name for each, so "every
# execution path" is one call site and not a grep.

# clean build: no unused imports, no shadowed definitions, no dead assignments
.venv/bin/python -m pyflakes flash/*.py benchmarks/*.py   # 0 findings (tasks/*_test.py are
                                                          # program fragments by design - the
                                                          # candidate supplies their names)
```

**Hardware-validated findings** (PLAN Appendix A): retry without rich feedback
adds zero · greedy retries repeat identical wrong code — sample them · some
failures are capability limits → escalation (30B solves both 7B-impossible
tasks on attempt 1) · single-run scores swing ±2 from Metal nondeterminism.

**New in this session (2026-09-25, all measured on the M5):** `r03_bulk_rule`
was run twice on the same machine to prove the power governor is the only thing
between the agent and its brain — with `--allow-big auto` on battery it is
recorded `tier=shed` (4.4GB fast tier allowed, 17.3GB brain refused), with
`--allow-big always` the same task escalates and solves in 46.6s. That 4/5 is
not a regression: the ledger shows r03 has never been a small-tier solve. And
the §33.1 symbol hint fired on the live run — the retry that had just hit
`undefined name 'BULK_MIN_QTY'` got `# pricing.py:5 BULK_MIN_QTY [constant]`
with its real source, turning two API-blindness failures into one semantic one.
**CORRECTION (2026-09-27):** the last sentence is false as written, and so is the
clause it was supporting. That run's retry prompt did NOT contain the resolved
source — `loop.solve` had appended `symbol_hint`'s output to the recorded
attempt, which is what the trace renders, and built the retry from the plain
error. What the note observed was real; it was observed in the wrong object. Two
lines fixed it, and the same task re-run on the 7B with `--trace-full` now shows
the header in both of its retries (`benchmarks/hint_live_audit.py`). No accuracy
delta is claimed for the corrected seam: that run still failed all three
small-tier attempts, as r03 always has, and an A/B arm would have to say more.

**Pass criteria** (PLAN §M0): a 30B-class MoE must sustain ≥45 tok/s, peak
memory ≤ 18 GB (24 GB Blender budget leaves headroom), and pass ≥17/20 tasks.
