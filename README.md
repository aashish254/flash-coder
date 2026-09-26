# Flash Coder

The fast, fully-local, self-improving coding agent for Apple Silicon.
Master plan: [`../PLAN.md`](../PLAN.md) (34 sections, v3.8).

## M0 — Model Bake-Off (first gate)

Validates the §22 model matrix on real hardware before anything else is built.

```bash
# 1. environment (one time)
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

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

# All 23 offline vectors above in one command, summed from the fraction each run
# PRINTS (never an exit code, never a phrase grep — see SPEC §6 for the two
# capture traps that rule is there to prevent). Fails if the tree's total moves
# off SPEC §6's number. `--quick ambient lora` re-runs only named lines.
.venv/bin/python benchmarks/battery_reread.py       # ~2 min, no models

# §33.1 symbol perception: AST discovery + a live language server (jedi/pylsp).
# The loop uses this automatically: a failure that names a repo symbol gets that
# symbol's REAL source appended to the retry feedback.
.venv/bin/python -m flash.cli find total_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli refs total_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli symbols --path benchmarks/fixtures          # whole tree (AST)
.venv/bin/python -m flash.cli lsp-selftest                                # 14 offline checks

# §33.1 ACT leg — symbol-precise edits: a change request is answered with patches that
# name a SYMBOL, and the AST's own lines are what gets replaced. Everything outside the
# addressed range is copied, never re-typed, so an edit cannot drift into a neighbouring
# definition. Out-of-range, ambiguous or overlapping addresses are REFUSED (the retry is
# told why and the project keeps its previous content). Off by default; --edit turns it on
# for a task that ships a project.
.venv/bin/python -m flash.cli run "..." --test t.py --context src/ --edit
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/edit_tasks.jsonl --edit
.venv/bin/python -m flash.patches --selftest     # 37 offline checks, incl. the loop arm
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
# where only an accident counts, never a `raise` the answer wrote itself. The report
# line prints the offer beside the held-out verdict, and the two gate clauses keep
# their own denominators (recall over answers the hidden tests sink, false offers
# over the ones they float, routine rows split out because the <1-per-20 clause is
# about routine tasks). Seconds of subprocess, no model; off by default on a suite,
# on by default for a single `run` (before that answer ships to you).
.venv/bin/python -m flash.cli run-suite --tasks benchmarks/tasks/p6_tasks.jsonl --confidence
.venv/bin/python -m flash.confidence --selftest   # 21 offline checks, no model
# measured 2026-09-26, 7B over 23 keyed tasks: every answer survived its held-out
# key, so the >=90% recall clause had an EMPTY denominator (not a pass), and the
# false-escalation clause was missed by one offer in 15 routine answers. Offline,
# on the seeded subtle-bug suite where would-fail answers exist by construction,
# recall is 6/6 — and the two masked-value bugs stay unoffered, which is the
# documented blind spot, not a surprise.

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
.venv/bin/python benchmarks/lora_path_check.py         # 31 checks + 14 mutants: gate, resume, leakage, identity, job name, arm denominator
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

**Pass criteria** (PLAN §M0): a 30B-class MoE must sustain ≥45 tok/s, peak
memory ≤ 18 GB (24 GB Blender budget leaves headroom), and pass ≥17/20 tasks.
