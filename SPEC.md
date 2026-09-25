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
| G5 | Speed: decision latency / generation rate | 282ms decisions, ~31 tok/s brain | decisions ≤ 300ms; brain **≥ 46 tok/s** (1.5× via speculative decode) |
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
| I-1 | Reversibility | Every mutation of code, weights, skills or memory is a commit. | `git status` clean after a run; one commit per accepted change. **BLOCKED: this workspace is not a git repo** (see §9, P0). |
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
- **R-3.2 (OPEN)** Edits SHOULD be symbol-precise patches, not text guessing
  (PLAN §33.1 ACT bullet — the one piece of §33.1 not shipped).
  Vector: on a 10-change suite, symbol-precise edits need ≤ 1 attempt for
  ≥ 8 and never rewrite a line outside the target symbol's range.
- **R-3.3 (OPEN)** Tournament mode (§33.4): k candidates under the power
  governor's width cap, scored by the oracle, best-of-k adopted.
  Vector: on the hard family (h-tasks), best-of-3 beats single-attempt pass rate
  by ≥ 8 points at equal or lower total token spend, on AC power.

### D. VERIFY — the oracle is the product

- **R-4.1 (SHIPPED)** Candidate code runs only in an isolated subprocess with a
  hard timeout, and failures report GOT vs WANT for the first failing assert.
  Vector: `m0_bakeoff.py --dry-run` 20/20; `benchmarks/tasks/*_test.py` probes.
- **R-4.2 (OPEN)** Malformed model output MUST be structurally impossible:
  constrained decoding on the sampler path for tool calls, file headers and JSON
  (PLAN §33.3).
  Vector: an FSM-constrained sampler that cannot emit a fence without a
  `# file:` header, verified by 100 sampled generations with 0 contract
  violations, at ≤ 5% latency cost.
- **R-4.3 (OPEN)** The agent MUST be able to *watch* execution, not only rerun
  tests (PLAN §33.2, debug-gym pattern).
  Vector: a seeded-bug suite where step-and-inspect feedback solves ≥ 2 more
  tasks than traceback feedback with the same fast tier and attempt budget.
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

- **R-8.1 (OPEN)** Speculative decoding MUST lift brain-tier throughput to
  ≥ 46 tok/s (G5) without changing pass rates.
  Vector: `benchmarks/m0_bakeoff.py --run` on the 30B with a draft model;
  tok/s reported per task, pass rate within 1 of the frozen baseline.
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
   claim): `flash lsp-selftest` 14 · `flash power --selftest` 22 ·
   `flash learn --selftest` 14 · `flash trace --selftest` 30 ·
   `flash web --selftest` 9 · `python benchmarks/trace_resume_check.py` 11
   · `python benchmarks/m0_bakeoff.py --dry-run` 20 reference solutions.
   **Total: 100 selftest checks + 20 oracle verifications = 120 green, offline.**
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
| I-1 git reversibility | no repository here | user decision (P0) |
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
