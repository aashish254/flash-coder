# Flash Coder — Build Spec v1 (normative)

**Status:** authoritative for *what counts as done*. `../PLAN.md` (v3.8) remains
authoritative for *why* a feature exists and for the measured history
(Appendix A). Where the two disagree, this spec wins on scope and PLAN wins on
rationale.
**Use:** `SPEC.md` → `TODO.md` (atomic tasks) → autonomous build loop
(implement → verify in terminal → check the box) → no box is checked without the
conformance vector having been *run* in this session or recorded in PLAN Appendix A.

Language: MUST / MUST NOT / SHOULD / MAY (RFC 2119). Every MUST carries a
**conformance vector**: the exact command, and the observable that makes it pass.
A requirement with no vector is a wish, and wishes are not in this spec.

---

## 1. Goal

Make Flash Coder a coding agent that a working developer keeps on their own
machine: it reads a real repo, writes code that has **passed an executable
oracle before it is ever shown to the user**, costs nothing per session, gets
 measurably better from its own outcomes, never makes the laptop worse to use,
and can explain any decision it ever made.

## 2. End gain — the outcomes we are buying, with numbers

Baselines are from `benchmarks/results/ledger.jsonl` as of 2026-09-25
(274 runs, 91 distinct tasks, M5 32GB, both tiers available).

| # | Outcome | Today (measured) | Target (v1 close) |
|---|---|---|---|
| G1 | Trusted correctness: accepted output verified by an oracle | 100% — every accept is a passing assert suite | keep 100%, 0 unverified writes |
| G2 | Fast-tier dominance: tasks the 7B solves without the brain | 57/91 = **63%** | **≥ 78%** via better perception + feedback + speed |
| G3 | Escalation economy: brain loads as a share of runs | 33% of tasks need big | **≤ 20%**, with held-out router AUC ≥ 0.75 before any gate turns on |
| G4 | Retry economy: attempts when the fast tier wins | mean **1.11**, one-shot **89%** | keep ≤ 1.3 at equal or better pass rate |
| G5 | Speed: decision latency / generation rate | 282ms decisions, ~31 tok/s brain (≥46 measured once as a best-case spike) | decisions ≤ 300ms; brain **≥ 46 tok/s** — the speculative-decode route to it is now closed (R-8.1, measured negative 2026-09-26), so the target stays unmet and needs another mechanism |
| G6 | Machine courtesy | governor sheds on battery/heat, 0 measured thermal warnings | **0 thermal warnings, 0 swap growth** during a 30-min AC run |
| G7 | Recovery: work lost to an interruption | 0 tasks (suite granularity, verified) | 0 at **task** granularity; `flash resume` after a real SIGKILL |
| G8 | Explainability: why did it do that | 1 command (`flash trace <sid> --task <id>`) | keep; ≥ 95% of failures diagnosable without a rerun |
| G9 | Self-improvement without a human | router refits from the ledger, gate OFF on AUC 0.569 | a **named** component beats frozen harness (§27.4) at least once |
| G10 | The feel test (M17) | not attempted | ≥ 7 of 10 developers keep it after a week |

**The single sentence version of the end gain:** *a developer on a laptop gets
verified code locally, faster than thinking about it, and the only thing they
ever approve is the diff.*

## 3. Scope

**In:** the agent core (`flash/`), its harness and suites (`benchmarks/`), the CLI,
the outcome flywheel, hardware governance, observability, recovery.

**Out of v1 (deliberate, each with a reason):**
- Cloud fallback of any kind (§34.2 wants a *local-first* trust story; a cloud
  button is a product decision, not an engineering gap).
- IDE plugins (VS Code/JetBrains) — the CLI is the dogfood surface until M17.
- Computer-use / GUI agent (§20), voice productization (§12.2 spike is done).
- Non-Python languages (perceive/context/lsp are Python-only by construction).
- Multi-user or server deployment; anything needing a network at run time.
- RL / phase-2 weight training beyond a LoRA experiment (see §9: hardware time).

## 4. Non-negotiable invariants (mapped from PLAN §33.9, each testable)

| ID | Invariant | MUST | Conformance vector |
|---|---|---|---|
| I-1 | Reversibility | Every mutation of code, weights, skills or memory is a commit. | `git status` clean after a run; one commit per accepted change. **SHIPPED 2026-09-25: repo initialised at baseline `0ea2798` (72 files, `.venv` excluded); pushing stays a user-gated step.** |
| I-2 | Gated change | No self-modification ships without beating the frozen harness. | A change to model, router or prompt fabric reports pass rates on the frozen suites vs the recorded baseline; a regression blocks the merge. |
| I-3 | Bounded resources | The agent sheds load before the user notices it. | `flash power` reports the profile; a run on battery records `tier="shed"` rather than loading the brain. **SHIPPED, measured 2026-09-25.** |
| I-4 | Offline-first | Every feature degrades gracefully with no network. | `flash web --selftest` (9/9) and the offline battery below run with the radio off. |
| I-5 | Typed everything | Decisions, tool calls and file-set outputs are schema-valid by construction. | Contract validator rejects malformed `# file:` and JSON output *before* the subprocess runs. **PARTIAL — see R-4.2.** |
| I-6 | Observable | Any behavior is replayable and explainable. | `flash trace <sid> --task <id>` reconstructs a failure. **SHIPPED (30/30 + live).** |
| I-7 | Fallbacks | Losing any single component degrades, never kills. | AST-only perception with no language server; keyword ranker with no embedder; reactive routing with no trained gate. **SHIPPED, each pinned by a check.** |

## 5. Requirements

Statuses: **SHIPPED** (built + vector run), **PARTIAL**, **OPEN**.

### A. PERCEIVE — the agent knows the repo, not just the prompt

- **R-1.1 (SHIPPED)** The agent MUST resolve a symbol named in an error to its
  real source and inject it into retry feedback.
  Vector: `flash lsp-selftest` 14/14; live — r03's retry received
  `BULK_MIN_QTY = 5` from `pricing.py:5`.
- **R-1.2 (SHIPPED)** Cross-file go-to-def and project-wide references MUST be
  answerable, with the AST owning kinds and the server owning resolution.
  Vector: `flash find total_cents --path benchmarks/fixtures`,
  `flash refs …`, checks 3 and 9 of `lsp-selftest`.
- **R-1.3 (OPEN)** The knowledge graph (PLAN §28) SHOULD supply architecture
  context (call/import graph, blast radius) alongside the LSP's live truth.
  Vector: a `flash graph <symbol>` answer that names the N callers a change
  would break, computed under 200ms for the fixtures repo.
- **R-1.4 (OPEN)** Perception MUST extend to the second language of real work
  (TypeScript or SQL — pick by ledger evidence, not taste).
  Vector: R-1.1..1.2 equivalents pass on a fixture tree in that language.

### B. DECIDE & ROUTE — cheap decisions before expensive generation

- **R-2.1 (SHIPPED)** One-forward-pass typed decisions with restricted softmax.
  Vector: `flash decide-batch` 12/12 (live arm; recorded in Appendix A).
- **R-2.2 (PARTIAL)** Routing MUST be trained on outcomes, and MUST NOT be
  trusted until it generalizes. Gate default stays off while held-out
  leave-suite-out AUC is 0.569.
  Vector: `flash router-fit` prints LOO precision and a held-out AUC
  **≥ 0.75** on a fresh suite before `run-suite --threshold 0.5` becomes default.
- **R-2.3 (OPEN)** The prospective confidence signal (§34.2) MUST come from
  verification evidence — static diagnostics, coverage, suite flakiness — not
  the model's own probability.
  Vector: on a seeded subtle-bug suite, the fallback is offered on ≥ 90% of
  outputs that would fail hidden tests, with < 1 false escalation per 20
  routine tasks.

### C. ACT — generate, then edit precisely

- **R-3.1 (SHIPPED)** Multi-file answers MUST round-trip through the
  `# file:` contract with per-file persistence and targeted repair.
  Vector: `run-suite --tasks benchmarks/tasks/mw_tasks.jsonl` at 6/6.
- **R-3.2 (PARTIAL — clause 1 MET, clause 2 NOT MET)** Edits SHOULD be
  symbol-precise patches, not text guessing (PLAN §33.1 ACT bullet).
  Shipped: `flash/patches.py` (`--edit`) — `# edit: <file> :: <Symbol>` or
  `L<a>-<b>` or `*`, plus one fenced block. The AST owns the replaced span
  (decorators included, so dropping `@property` is impossible), a column-0
  replacement is re-indented to the symbol's real nesting, and bytes outside
  the addressed range are copied rather than regenerated. Every failure mode
  is a refusal with a reason and an unchanged project: unknown symbol, ambiguous
  address, backwards/EOF/straddling range, overlapping set, file not in the
  project, replacement that does not parse, replacement that loses the symbol it
  addressed. Offline: `python -m flash.patches --selftest` **37/37** (7 of them
  drive the loop's patch arm against a scripted generator, so the wiring is
  proven without charging a model); `--suite` **60/60** premise checks over
  `benchmarks/tasks/edit_tasks.jsonl` (each task ships its own project text, so
  both arms read identical input, fails as seeded, passes on the reference patch,
  and that patch fits inside one symbol).
  Vector, measured 2026-09-26 — small tier, greedy first attempt,
  `--allow-big never`, 10 tasks, two arms on the same input:
  * **clause 1 MET**: **8/10 solved, all 8 within 1 attempt** (gate: ≥ 8 in ≤ 1);
    0 refusals, 0 whole-file rewrites. `benchmarks/results/edits/arm_edit_small.log`.
  * **clause 2 NOT MET**: **7 lines touched outside the annotated symbol**
    (gate: 0), all from one task. `arm_free_small.log` is the matched control at
    the same 8/10.
  Cause, from trace `20260926-060927-run-suite-526b`: the request named the
  class (`Box`), the change lives in `Box.__init__`; the model addressed the
  noun it was given and re-typed the class correctly. **The address width
  follows the noun in the request, not the locus of the change** — so clause 2
  is ambiguous in exactly this case (outside the annotated symbol, inside the
  addressed one), which is escalated as §10.6 rather than redefined here.
  Cost, stated precisely: the patch arm is **2.7× faster** (6.0 vs 16.3 s/task)
  and decodes **3.6× fewer tokens** per generation (51 vs 184) at a *slightly
  higher* total token count (8169 vs 7845) — a patch prompt carries ~160 tokens
  of protocol and re-sends the project every attempt. On 4-bit Apple-Silicon
  decode the binding cost is steps, not tokens, so the win is wall clock and the
  structural guarantee, not spend. Both arms fail the same two tasks
  (`round()` → banker's rounding; wrong priority order), putting those on tier
  capability rather than protocol.
- **R-3.3 (OPEN)** Tournament mode (§33.4): k candidates under the power
  governor's width cap, scored by the oracle, best-of-k adopted.
  Vector: on the hard family (h-tasks), best-of-3 beats single-attempt pass rate
  by ≥ 8 points at equal or lower total token spend, on AC power.

### D. VERIFY — the oracle is the product

- **R-4.1 (SHIPPED)** Candidate code runs only in an isolated subprocess with a
  hard timeout, and failures report GOT vs WANT for the first failing assert.
  The reported values MUST come from the same evaluation that decided the
  verdict — an assert whose side mutates (`q.pop() == "high"`) used to be
  evaluated twice, so a *failing* assert could report `GOT: 'high' |
  WANT: 'high'` and the retry got feedback that named no difference.
  Vector: `m0_bakeoff.py --dry-run` 20/20; `python -m flash.harness --selftest`
  **12/12**, including a counting call whose printed GOT must be the value the
  comparison used, and the seeded e09 task now reporting `GOT: 'low' |
  WANT: 'high'` where the shipped probe reported no difference.
- **R-4.2 (SHIPPED, one budget missed)** Malformed model output MUST be
  structurally impossible: constrained decoding on the sampler path for the
  output protocols that exist — the multi-WRITER file set and the single fenced
  answer (PLAN §33.3; `flash/grammar.py`, `--constrain`).
  Vector, measured 2026-09-26: `python -m flash.grammar --selftest` **47/47**
  offline (including liveness — no shipped reference is blocked by its own
  mask — and no-dead-end: 11 191 mask-offered pieces, 0 refusals, 0 stuck
  positions); **100 sampled generations, 0 malformed, 0 mask breaches**,
  99/100 recovered by the parser against **0/20** unconstrained on the same
  prompts and seeds; reference sweeps as pairs under identical flags — m0
  18/20 → 18/20 (unaffected), mw 5/6 → 6/6.
  The **≤ 5% latency budget is missed**: −6.3% per token on the multi-file
  shape at matched generation length (see §10.5 for why the number is what it
  is, and for the operational metric that moved the other way).
- **R-4.3 (PARTIAL — mechanism shipped, acceptance gate NOT met)** The agent
  MUST be able to *watch* execution, not only rerun tests (PLAN §33.2,
  debug-gym pattern).
  Shipped and verified: `flash/debug.py` traces a candidate line by line in the
  same isolated `-I` subprocess and reports the executed trail, per-variable
  value history and the line that last mutated what the failing assert
  compares; `--debug` puts it in the retry feedback as `kind="debug"`. Offline:
  **55/55** checks, and `python -m flash.debug --suite` proves the premise on
  all 8 seeded bugs — the causing line is **absent from the traceback and
  present in the digest**, 32/32.
  The vector's claim — *digest feedback solves ≥ 2 more tasks than traceback
  feedback* — was run on **three substrates** and **did not hold**: 7/8 vs 7/8
  on repair prompts that showed the model the tests, 8/8 vs 8/8 on blind repair
  prompts that did not (every task solved on the first greedy attempt — a
  ceiling, not a result), and 6/21 vs 5/21 on a 21-task substrate selected from
  measured first-attempt failure, where the digest arm *lost* a task, at +18%
  wall time (500s → 591s) for the same 59 generations. The reason is measured
  rather than assumed: attempt 0 is greedy and shared, so the digest can only
  differ on a task that survives to a second retry — of the 12 tasks both arms
  solved, **11 had identical attempt counts** and one (`fix01_alias_sort`) went
  from 3 attempts to 2. The discriminating band in these suites is one task
  wide, so the ≥2 gate is undetectable here at any effect size; `P2-follow-up`
  in `TODO.md` states the instrument that could measure it. `--debug` therefore
  stays **off by default**.
- **R-4.4 (SHIPPED)** Vision outputs MUST be scored by a pixel oracle with a
  coverage guard so an empty page can never win.
  Vector: `calibrate_visr.py` + `visp` 5/5, `visr` 4/5 with real4 recorded as a
  documented capability ceiling.

### E. ENDURE — power, recovery, observability

- **R-5.1 (SHIPPED)** The loop MUST consult the system profile before loading a
  model and MUST record a refusal instead of silently degrading.
  Vector: `flash power --selftest` 22/22; live battery A/B on r03 (shed vs
  allowed, Appendix A 2026-09-25).
- **R-5.2 (SHIPPED)** A session MUST survive interruption at suite
  granularity, resume without re-billing settled work, and store its own
  parameters.
  Vector: `benchmarks/trace_resume_check.py` 11/11.
- **R-5.3 (OPEN)** Recovery MUST reach task granularity: a partial generation
  and the sandbox state of the in-flight task are checkpointed, so SIGKILL
  mid-task resumes inside the task.
  Vector: `kill -9` a live `run-suite` between two tokens, `flash resume`, and
  the in-flight task completes without regenerating its first attempt.
- **R-5.4 (OPEN)** A 24h chaos run MUST end with zero data loss and every
  session resumable (gate M16).
  Vector: random kills, memory pressure, network loss and thermal load for 24h;
  `flash trace` shows a closed or resumable state for every session, and the
  ledger has no torn lines.
- **R-5.5 (SHIPPED)** Token spend and wall time per task MUST be recorded per
  generation. Vector: `flash trace <sid>` header prints prompt/complete tokens
  and generating vs verifying seconds. **Watts/task stays OPEN** (§9).

### F. LEARN — the flywheel

- **R-6.1 (SHIPPED)** Every routed attempt appends one ledger record, including
  refusals, and telemetry MUST NEVER be able to kill the loop.
  Vector: 274 rows, 91 tasks, `tier="shed"` rows present with reasons.
- **R-6.2 (SHIPPED)** Background learning MUST run only on AC, only when the
  user has been idle ≥ 5 min, inside a wall-clock budget, at lowered priority,
  and MUST resume from its checkpoint.
  Vector: `flash learn --selftest` 14/14; live `flash learn --check` → refused
  on battery with the reason.
- **R-6.3 (OPEN)** Label hygiene MUST be preserved as the ledger grows: rows
  produced under a shed, forced or benchmark-override policy are labelled
  honestly and excluded where they would self-amplify.
  Vector: a re-fit trained only on `trainable()` rows, evaluated on a held-out
  family, reports the same AUC as a re-fit trained on all rows *minus* the
  override rows — i.e. the exclusion is not costing signal.
- **R-6.4 (OPEN)** One component MUST be shown to improve by learning, not by
  editing (gate for §27 autopoiesis, G9).
  Vector: a documented before/after on a frozen suite for skills, memory or
  weights — whichever lands first — with I-2's harness gate satisfied.

### G. PRODUCT SHELL

- **R-7.1 (SHIPPED)** A single-command agent surface with typed flags, honest
  exit codes and a printed cost report. Vector: `flash --help`, all commands
  above.
- **R-7.2 (OPEN)** Ambient mode (PLAN §33.5): on idle + AC, the agent MAY
  prepare **draft PRs** for morning review — never merging, never auto-applying.
  Vector: an overnight run leaves ≥ 1 reviewable draft diff plus a trace, and
  0 commits pushed, 0 files modified outside its own worktree.
- **R-7.3 (OPEN)** Hands-free control (voice) at the measured spike latency:
  command-to-ack ~4.8s. Vector: real-microphone arm of the spike with VAD
  barge-in, ≥ 90% command recognition over 50 utterances.

### H. SPEED

- **R-8.1 (OPEN — measured negative on this hardware)** Speculative decoding
  MUST lift brain-tier throughput to ≥ 46 tok/s (G5) without changing pass
  rates.
  Vector: `benchmarks/m0_bakeoff.py --run` on the 30B with a draft model; tok/s
  reported per task, pass rate within 1 of the frozen baseline.
  Run 2026-09-26 (`--draft`, `--draft-tokens`), and it failed in two different
  ways: on the **brain** (Qwen3-30B-A3B-4bit) the draft-verify path faults the
  GPU — `kIOGPUCommandBufferCallbackErrorTimeout`, every run, with a
  vocabulary-matched draft (`Qwen3-0.6B-4bit`, both 151646 wide) and a
  mismatched one, at 4 and at 2 drafted tokens; the same target without a draft
  runs the suite normally before and after, so the fault is that path on an MoE
  target under this mlx/Metal build. On the **fast tier**, where the mechanism
  does run (`Qwen2.5-Coder-7B` + `Qwen2.5-Coder-1.5B-4bit`, both 151657 wide,
  so acceptance is real) it is **40% slower**: 11.7 tok/s against a 19.4
  baseline on the same task, TTFT 1219 ms against 337 ms. A
  vocabulary-incompatible draft on the same target gave 18.8 tok/s, within noise
  of the baseline, but that control is ambiguous — mlx may have disabled
  speculation on the shape mismatch rather than paid for a draft that always
  rejects. What is not ambiguous is the matched pair: the path ran, and
  throughput moved the wrong way. Reading: 4-bit weights on Apple
  Silicon are already bandwidth-bound at one token per step, so verifying k
  candidates costs the k forwards speculation was supposed to avoid. **G5 must
  come from somewhere else**; the standing brain figures stay 46.5 tok/s
  best-case, 31.2 sustained, with no speculation.
- **R-8.2 (OPEN)** Latent-compute mechanisms (§31.2, cheapest first) MUST be
  adopted only where a measured token or latency saving appears.
  Vector: a before/after token count on the same suite, ≥ 20% saving, no
  pass-rate loss.

### I. SAFETY & SANDBOX

- **R-9.1 (SHIPPED)** Untrusted fetched content is data: doc excerpts are
  budgeted, labelled and never executed; candidate paths are checked for escape
  before writing (§33.9 offline ladder).
- **R-9.2 (OPEN)** Every execution path MUST run under an explicit sandbox
  (§21): writable root, no network by default, cpu and memory rlimits.
  Vector: a test that proves a hostile candidate (`open('~/.ssh/id_rsa','w')`,
  `socket.connect`) fails under the sandbox and the suite still reports it as a
  normal verify failure.

---

## 6. Verification protocol — how a box gets checked

1. **Offline battery first** (seconds, no models, must be green before any live
   claim): `python -m flash.harness --selftest` 12 · `flash lsp-selftest` 14 ·
   `flash power --selftest` 22 · `flash jobs --selftest` 14 ·
   `flash trace --selftest` 30 · `flash web --selftest` 9 ·
   `python -m flash.grammar --selftest` 47 · `python -m flash.patches --selftest`
   37 · `python -m flash.debug --selftest` 55 ·
   `python benchmarks/trace_resume_check.py` 11 ·
   `python -m flash.debug --suite` 32 ·
   `python -m flash.patches --suite benchmarks/tasks/edit_tasks.jsonl` 60 ·
   `python benchmarks/m0_bakeoff.py --dry-run` 20 reference solutions.
   **Total: 343 selftest / end-to-end / premise checks + 20 oracle
   verifications = 363 green, offline** (counted 202 on 2026-09-26, before the
   oracle and the patch protocol had batteries of their own).
   (The `jobs` line was labelled `learn` until 2026-09-26: `flash learn
   --selftest` dispatches into `flash.jobs`, and `flash/learn.py` has no
   battery of its own. Same 14 checks, wrong owner — the mislabelling made the
   battery look like it covered the router's training code when it covers the
   background scheduler.)
1b. **Clean build** (no warnings accepted): `python -m pyflakes flash/*.py
   benchmarks/*.py` → **0 findings**. `benchmarks/tasks/` is out of that scope
   on purpose — a `*_test.py` there is a program *fragment* (`count_tasks`, the
   candidate's own name, is undefined until the harness prepends the candidate),
   so "undefined name" is its correct state, not a defect to silence.
2. **Live arm** for anything that touches a model, costed explicitly in the PR
   note: minutes, peak GB, battery-vs-AC. The frozen suites are m0 (20),
   m2 (5), mw (6), m3h/m4-m7 held-out families, visp (5), visr (5).
3. **Reference sweep after any harness change** — a harness edit invalidates
   every stored pass rate, so re-run the reference solutions before comparing.
4. **No claim without a run.** A requirement moves to SHIPPED only with the
   command, the date, and the number. Failures are logged in Appendix A too —
   the m7 gate negative is the template.
5. **Docs move together:** code, `README.md` commands, `flash/__init__.py`
   module map, PLAN Appendix A row, PLAN status line, this spec's status tag.
   A change that doesn't update the spec is not finished.

## 7. Execution order (priority = §34's own verdict: local context engine and
latency first; the rest earns the right to exist after)

| Step | Item | Why now | Cost |
|---|---|---|---|
| P0 | `git init` + baseline commit | unblocks I-1, R-7.2, M15; **user-gated** | 5 min |
| P1 | R-4.2 constrained decoding | kills a whole bug class the mw suite keeps hitting | offline vector + 1 live arm |
| P2 | R-4.3 debugger skill | highest-value new *capability* on hard tasks | 1 seeded suite + 2 live arms |
| P3 | R-8.1 speculative decoding | G5 latency, and every later measurement gets cheaper | 2 bake-off runs |
| P4 | R-3.2 symbol-precise edits | finishes §33.1's ACT leg, feeds G2 | offline + live |
| P5 | R-3.3 tournament (AC only) | G2 fast-tier dominance without touching weights | live, power-gated |
| P6 | R-2.3 + R-6.3 confidence from verification | the §34.2 trust gap, gated on AUC | ledger growth |
| P7 | R-5.3 task-granular recovery → R-5.4 M16 chaos | the bulletproof gate | 24h window |
| P8 | R-6.4 + §34.3 LoRA experiment | first learned self-improvement | hours of training |
| P9 | R-7.1 shell polish → M17 feel test | adoption | 10 humans |

## 8. Definition of done (project level)

Flash Coder is done when, on one laptop and with no network: **G2 ≥ 78%** of a
real task mix is solved by the fast tier, the rest escalates locally and is
verified by an executable oracle before the user sees it; **G5** throughput makes
it faster to delegate than to type; **I-1..I-7** are each pinned by a check that
runs in under a minute; one component has demonstrably improved itself from its
own outcomes under the §27.4 gate (**G9**); a 24h chaos run lost nothing
(**M16**); and **≥ 7 of 10 developers chose to keep it** (**M17**). Until then
the honest label is *a very good local loop with instrumentation*.

## 9. Register: arms that cannot be closed in this environment

| Item | Blocker | Interim |
|---|---|---|
| Speculative decoding on the brain (R-8.1) | the draft-verify path faults the Metal command buffer on the 30B MoE target, with a vocabulary-matched draft and at two batch sizes; and on the dense target where it does run it is 40% slower | an upstream mlx/Metal fix plus a re-run, or a draft small enough that verification is not the bottleneck — G5 then needs a different mechanism |
| §34.1 16GB co-residency arm | this box is 32GB, single-user | profiles + shed evidence on battery |
| Watts/task (G6 energy) | `powermetrics` needs sudo | tokens + seconds per task in the trace |
| M16 24h chaos | needs a 24h window | offline kill/resume checks (11/11) |
| M17 feel test | needs 10 developers | dogfood transcript discipline |
| Phase-1/2 training, LoRA | hours of compute + AC idle windows | `jobs.py` gate shipped; experiment queued at P8 |

## 10. Judgment calls in this spec the user may want to overrule

1. **G2 target 78%** (from 63%) is a guess bounded by measurement, not by
   evidence — if it is wrong it should be corrected from the suites, not argued.
2. **R-1.4's second language** is deliberately unselected; pick from ledger
   demand or drop it.
3. **No cloud fallback** in v1. If §34.2's trust gap is judged product-fatal,
   that reverses a scope decision, not an implementation detail.
4. **R-4.2's wording named "tool calls … and JSON".** This agent has no
   tool-call grammar and no JSON answer path — §32's decision fabric is a
   restricted softmax over a fixed menu, schema-checked at the far end — so the
   contract was built for the two protocols that actually exist: the
   multi-WRITER file set and the single fenced answer. Adding a JSON DFA is new
   scope, not a closed gap; say so rather than let the requirement read as
   partly done.
5. **R-4.2's ≤ 5% latency budget is the wrong instrument.** A mask that works
   changes what the model writes, so per-token throughput compares two
   different workloads: masked answers finish at 150-390 tokens where
   unconstrained ones run to the 1500-token budget. Measured: the mask's own
   arithmetic is 0.145-0.183 ms/step (0.4% of a 42.6 ms decode step), the
   per-token rate is 6.3% lower at matched length, and the mw suite's wall
   clock per run went from 95s to 72s **with a higher pass rate**. Proposed:
   gate the constraint on *seconds per usable answer* and on the isolated
   per-step share, and keep the 5% per-token line as telemetry. This is a
   change to an acceptance criterion, so it is the user's call, not the
   implementer's. The residual is now identified rather than unexplained: the
   hook stands in 44.7% of wall time while computing 0.4% of it, because
   reading each sampled id back is a device→host sync and mlx deliberately
   launches step n+1 before syncing step n. A zero-allocation hook changed the
   number by nothing, so no Python-side work closes the gap — only accepting a
   read-back lag, which would put the exactness of the guarantee at risk for
   the first token after every fence closes.
6. **R-3.2's clause 2 says "outside the target symbol's range" without saying
   which symbol.** Measured 2026-09-26: the one clause-2 violation (7 lines) is
   a change the request named by its class (`Box`) while the edit lived in
   `Box.__init__`; the model addressed `Box`, re-typed the rest of the class
   byte-correctly, and every line it touched is inside the *addressed* symbol
   and outside the *annotated* one. Two readings, both defensible: (a) annotated
   — the suite's `target.symbol` is the contract, so the run misses the gate;
   (b) addressed — whatever the model put in the header defines the range, so
   the run is 10/10 and the mechanism has no remaining violation. (a) keeps the
   metric falsifiable and charges the model for naming the wrong width; (b)
   measures only the tool's splice discipline, which is already proven by
   construction and so is not news. I kept (a) and left the gate unmet. Related,
   and the reason (b) is not free: under (b) a model could address `*` and pass
   clause 2 by definition — the whole-file count only stays honest because it is
   reported separately, not because the audit forbids it. The alternative that
   sidesteps the ambiguity is a stricter prompt rule ("address the smallest
   symbol that owns the lines you need to change"), already in `PROTOCOL`; it
   held on 9/10 tasks, which is evidence for the rule rather than for reading
   (b). This changes an acceptance criterion, so it is the user's call.
