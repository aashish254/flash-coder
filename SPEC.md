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
- Non-Python languages (perceive/context/lsp remain Python-only by construction;
  R-1.4 moved `flash.graph`'s perception and `flash.patches`' addresses onto
  TypeScript, and its retry path, live server, `lsp` and the whole-file control
  arm are still Python-only — see R-1.4's PARTIAL).
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

- **R-1.1 (SHIPPED — re-verified 2026-09-27, the earlier vector was false)** The
  agent MUST resolve a symbol named in an error to its real source and inject it
  into retry feedback.
  *What was wrong: the clause says **inject into retry feedback**, and
  `loop.solve` appended `lsp.symbol_hint`'s output to the recorded `Attempt` — a
  different object from the `messages` list the next prompt is built from. The
  helper was correct, every one of the old 14 checks called it directly, and the
  trace render that "proved" the live half prints `Attempt.err`. So the resolved
  source reached the record and never reached the model, on every live run since
  the seam was written (`0ea2798`, the baseline commit — the only one that has
  ever touched that call site).*
  The fix is two lines: `err = _perceive(task, err, code)` on a failed
  attempt, before both the `Attempt` and the feedback template are built, so one
  string is what the model reads and what the trace stores. *(R-1.1 wrote that call
  as `_symbol_hint`; R-1.3b renamed it to `_perceive`, which appends the LSP source
  block AND the graph's dependents block at the same assignment — same position,
  same single-consumer property, and the rename is the only change to this line.)*
  Vector: `flash lsp-selftest` **17/17** as shipped (the run has since become
  **22/22** when R-1.1c added section 6c to the same file; the three checks below
  are unchanged and still pass). Three new checks (section 6b, printed as
  checks 11-13) drive `loop.solve` itself with a stubbed generator and read the
  retry message back — that the prompt carries the source, that it carries
  `symbol_hint`'s own output verbatim and exactly once, and (control) that a task
  with no repo context gains nothing. Mutation, hand-run and kept in
  `benchmarks/results/lsp_wiring_mutation_20260927.log`: with the old line put
  back the two prompt checks FAIL and the control still passes — **15/17**.
  Live, both directions (`python benchmarks/hint_live_audit.py`, raw witness
  `benchmarks/results/hint_live_audit_20260927.log`): of **248** stored prompts
  from runs captured before the fix — **56** of them retry prompts — **0**
  carried the header, and after it one real 7B run of `r03_bulk_rule`
  (`--with-context --allow-big never --attempts 3 --trace-full`) put it in both
  of its retries (`models.py:21 CartLine [class]`, `pricing.py:5 BULK_MIN_QTY
  [constant]`). The audit exits 1 if either half is contradicted, and was checked
  that way on synthetic corpora in both directions.
  **What this voids:** any live accuracy delta previously credited to symbol
  injection, including the 2026-09-25 README note on r03 — that run's retry saw
  the bare error. And no delta is claimed for the corrected seam either: on the
  live run above r03 still failed all three small-tier attempts, as it always
  has, so this clause is SHIPPED on *injection verified*, not on outcomes. The M2
  context-skeleton A/B is unaffected — the skeleton is prepended to the prompt
  itself, and `Project context (real API` appears in 10 of those same 248
  captured prompts, which is the whole difference: one path wrote into `prompt`,
  the other only into the record.
  Out of scope, by design: `--edit` tasks, whose prompt already ships the real
  source inline, and successful attempts.
- **R-1.1c (SHIPPED)** `lsp.symbol_hint`'s context budget MUST overflow rather
  than abort: a ranked symbol whose source does not fit is contributed CLIPPED at
  a line boundary, and the symbols that then had no room are COUNTED in the block.
  It MUST NOT return an empty block while a ranked symbol sits unused — silence
  belongs to "nothing repo-defined is at issue" and to nothing else.
  *Found while writing R-1.3b's cost check, which needed a symbol that fits inside
  the budget in order to compare anything: the budget test was a `break`, so the
  first oversized symbol deleted the hint. Measured on `flash/` — an error naming
  `symbol_source` ranks it first with 1631 chars of source (3276 for its block once
  the location and signature line is counted) against a 1200-char budget, and the
  function returned ZERO characters while two ranked symbols of 183 and 102 chars
  beside it were never reached. That is R-1.1's dead seam by a different route: a
  retry about a long function — the failures where the real signature matters most —
  saw the bare error.*
  Vector: `flash lsp-selftest` **22/22**, five new checks (section 6c, printed as
  checks 14-18). Four call the helper: the oversized top hit arrives clipped with
  `… source truncated here (context budget)` and its `def` line intact (1156 of a
  1200 chars); the left-behind symbols are counted in the tail (`… 2 symbol(s) at
  issue not shown`); the budget is a ceiling with the notes inside it at every size
  that can hold a signature; and a budget too small for a signature plus a `def`
  returns silence rather than a header with nothing under it. The fifth is the seam
  R-1.1 demanded — it drives `loop.solve` with a stubbed generator and reads the
  retry back, asserting the CLIPPED block is what the model is shown (1592 chars).
  Mutation, hand-run and kept in `benchmarks/results/lsp_r11c_mutation_20260927.log`:
  with the `break` restored the shipped selftest runs unmodified and reports
  **18/22** — three of the four helper checks FAIL (the clipped one prints
  `0 chars against a 1200 budget`, the counted-tail one prints `no block at all`, and
  the too-small-budget one FAILs while still printing its static message, since what
  changed for it was the 1100-char arm coming back empty) and the seam check FAILs at
  394 chars of retry — the graph's block arrived, the source block did not. Stated at
  precision: the fourth helper check, "the budget is a ceiling", survives the mutant
  vacuously (`0 and 0` — an empty block is under any ceiling), so it is a
  no-overshoot guard and not part of the defeat.
  `graph.scope_hint`'s `… N more not shown (context budget)` is the in-repo
  precedent this follows, and the clipping is why R-1.1b's hint A/B can now treat
  an empty arm as the measured thing it is.
- **R-1.1b (MEASURED — NIL)** Whether a perception block HELPS, not merely whether
  it arrives, MUST be answerable by running the same frozen suite with each block
  switched off. That requires the blocks to be separable: `loop.HINTS` is the
  selection (`("source", "graph")` ships), `_perceive(…, hints=…)` picks from
  `(("source", _symbol_hint), ("graph", _graph_hint))` and returns the bare error
  before it builds a repo index when the selection is empty, and
  `--no-source-hint` / `--no-graph-hint` on `run` and `run-suite` write it. Both
  flag names are in `cli.SUITE_PARAMS`, so a session's own trace states which arm
  produced it — an A/B whose rows cannot be told apart afterwards is not an A/B.
  *Vector 1, offline — `benchmarks/hint_ab_check.py`,
  **14/14 checks + 8/8 mutants defeated by exactly their checks**
  (`benchmarks/results/hint_ab_offline_20260927.log`). It certifies the frozen
  10-record corpus at the layer the live arms will read (`naive` is the code
  `solve` extracted, and every `naive` COMPILES, so a retry is about a symbol and
  not about a token cap), that both blocks are non-empty over each record's REAL
  verdict, that each block's symbols are a PREFIX of one shared
  `symbols_involved` ranking, and — the gate R-1.1 exists to keep — eligibility is
  proved AT THE SEAM: `loop.solve` with a stubbed generator shows the retry
  message carrying both headers, and shows the four arms as four DIFFERENT prompts
  each carrying exactly its own blocks. Its mutation set is the failure modes of
  this kind of instrument: a task already passing on try 1, a reference solution
  that fails its own test, a crash that names nothing the repo defines, a prompt
  that quotes the answer, a duplicated task, a task with no `context`, a `naive`
  frozen at the wrong layer, a truncated `naive`.*
  *Vector 2, live — four `run-suite` sessions over
  `benchmarks/tasks/hint_ab_tasks.jsonl`, `--attempts 3 --allow-big never
  --trace-full`, scored by `benchmarks/hint_ab_report.py`
  (`benchmarks/results/hint_ab_live_20260927.log`):*

  | arm | pass@1 | pass@N | retries | source blocks | graph blocks |
  |---|---|---|---|---|---|
  | both | 0/10 | **3/10** | 17 | 17 | 17 |
  | graph only | 0/10 | 2/10 | 18 | 0 | 18 |
  | source only | 0/10 | 2/10 | 18 | 18 | 0 |
  | off | 0/10 | 2/10 | 18 | 0 | 0 |

  pass@1 is 0/10 everywhere by construction and is the control, not a result: every
  arm shares one greedy attempt-0 answer per task, so the entire delta belongs to
  the blocks. The injection columns say the switches are not dead: the OFF arm
  speaks on 0 of 18 retries, `source only` on 18 of 18, and never the other way
  round.
  **The result is a nil.** `both` is +1 task over `off` at n=10 — ten points, which
  is one task — and at the same sample size the flips are not ordered by arm:
  `hc16_paid_line_cents` is solved by `both`, `graph only` and `off` and LOST by
  `source only`, while `hc36_add_twice_qty` is solved by `both` and `source only`
  and not by `graph only` or `off`. Only 3 of 10 tasks discriminate at all, and the
  retry delta (1 fewer over 18) is the same single task. So: the hints are not
  shown to help, and they are not shown to hurt; nothing here licenses turning
  either switch off, and nothing licenses claiming a win.
  *What the run did buy is an instrument finding, and it is the reason this box is
  closed rather than iterated: **8 of 10 tasks are unsolved in all four arms at
  attempts=3.** The band has almost no headroom, so an A/B on it can only ever move
  on the ≤2 tasks sitting at the solvability boundary — a design that cannot report
  a real effect is why the answer is nil and not "no". The offline check's own tail
  note adds a second fragility: 3 of the 10 are hint-ELIGIBLE only because
  `diagnose`'s GOT/WANT upgrade names the at-issue symbol in the verdict; on the
  bare traceback they rank to nothing, so that denominator rides on `diagnose` and
  shrinks silently if it regresses.
  Gated follow-up (not booked as a requirement, and no number projected from it):
  re-pick the band at the 3-attempt solvability boundary, or raise `--attempts` to
  6, before spending another 4-arm run on this question.*
- **R-1.2 (SHIPPED)** Cross-file go-to-def and project-wide references MUST be
  answerable, with the AST owning kinds and the server owning resolution.
  Vector: `flash find total_cents --path benchmarks/fixtures`,
  `flash refs …`, and the two `lsp-selftest` checks
  *"lsp: cross-file go-to-def lands in pricing.py"* and *"lsp: project-wide
  references from a use site"* (the run prints labels, not numbers, so they are
  named here rather than indexed).
- **R-1.3 (SHIPPED)** The knowledge graph (PLAN §28) SHOULD supply architecture
  context (call/import graph, blast radius) alongside the LSP's live truth.
  Vector: a `flash graph <symbol>` answer that names the N callers a change
  would break, computed under 200ms for the fixtures repo.
  *`flash/graph.py` builds it from AST alone — no embedding, no vector store.
  Nodes are functions, classes, modules and module-level constants; edges are
  calls, reads, imports and inheritance, and **every edge carries the file, line
  and source text that proves it** plus the `via` label of the rule that bound it
  (`own-scope`, `import-alias`, `enclosing-class`, `inherited`, `qualified-name`,
  `unique-attribute`). A name that resolves to two symbols becomes no edge, but it
  is **counted**: `blind_spots()` is the graph's honest floor, and
  `external()` separates a real hole from a boundary like `json.dumps`.
  `merge()` re-extracts only changed files and refuses to drop nodes for files a
  scan never covered — the shrink guard is checked against the worst case, an
  empty directory.
  Vector, measured (`python -m flash.graph --selftest`, 2026-09-27): the fixtures
  repo answers `flash graph bulk_discount_cents` in **0.1 ms** against the
  clause's 200 ms; because five files are a thin instrument, the same check runs
  against a generated **400-service-module repo** (401 files, 5377 nodes, 10058
  edges) where a depth-3 blast radius costs **29–106 ms cold** across the runs
  taken, after a 0.6–0.9 s build. **44/44 checks + 12/12 mutants.** Two of the
  checks exist because this page made a claim the code had no witness for: an
  ABSENT answer must name the addresses it DOES have once the needle shares text
  (`Cart.subtot` → `cart.py::Cart.subtotal_cents`), and it must invent nothing for
  a needle with no textual neighbour — which is how the `Cart.` defect was found,
  since the empty name after the dot is a substring of every symbol and the answer
  offered five arbitrary nodes as "nearest". Both directions are mutation-covered:
  offering the wrong list, and the sweep's last mutant, which replaces the cached
  reverse index with a scan of every edge per hop and requires the budget check to
  notice; it does, 443–943 ms.
  The clause's other half, "alongside the LSP's live truth", is wired inside the
  graph rather than left as prose: `--live` hands `live_upgrade` this pass's OWN
  blind spots and lets jedi settle them (measured 1 asked, 1 settled, `total_cents`
  → 3 sites), timed outside the 200 ms clause on purpose, because a server has to
  start up. I-4 holds by construction here: the answer path imports stdlib and
  `flash.patches` only, `flash.lsp` is imported lazily inside `--live`, and a dead
  server comes back as `no language server answered` instead of raising.
  *One deviation from PLAN §28.3's route, stated rather than buried: Phase 1 said
  "depend on Graphify directly (don't rebuild)". Graphify is real
  (github.com/Graphify-Labs/graphify) but is not in this venv — `requirements.txt`
  carries mlx-lm and python-lsp-server only — and the deterministic AST pass §28.1
  describes was built in-repo (1614 lines, `ast` alone). The reason: R-1.3's vector
  is an answer under 200 ms, and the four properties that make the plan's graph
  worth having (provenance per edge, no vector store, incremental merge, shrink
  guard) are each pinned by a check here, so the vector is met without adding an
  extractor that runs code of its own. The place where comparing against Graphify
  would actually decide something is §28.2 step 3's subgraph injection, and that
  step has now shipped in-repo (R-1.3b, below) — so the dependency question is
  closed by what the injection turned out to need (the graph's own `blast()`, one
  shared AST parse, 900 chars) rather than by silence either way.*
- **R-1.3b (SHIPPED)** PLAN §28.2 step 3: the graph's blast-radius subgraph MUST
  reach the model's retry context, not only the CLI answer.
  *Wired at the seam R-1.1 exposes: `loop._perceive(task, err, code)` appends both
  perception blocks to `err` on a failed attempt, before the `Attempt` record and
  the feedback template are built — so the model and the trace read the same
  string. One call site, one ranking: `_repo_index` parses the repo ONCE per retry
  and hands the same `SymbolIndex` to `lsp.symbol_hint` ("what is this symbol
  really") and `graph.scope_hint` ("who breaks if I change it"), which is
  deliberate — fed the source block as its own `err`, the graph ranked names out
  of that quoted text and one retry showed two different sets at issue, a defect
  this vector found and now pins with a mutant.*
  `scope_hint` takes the ≤3 symbols at issue, walks depth **2** (§28.2's own words
  are "small context", not "everything"), caps at 6 dependents per symbol and
  **900 characters** with a stated `… N more not shown (context budget)` tail
  rather than a silent cut, and refuses to guess: an at-issue name that binds two
  graph nodes is disambiguated by the FILE the AST index read, and if that does not
  settle it the symbol contributes no block. The graph is held per repo root in a
  `SCOPE_CACHE`-bounded LRU (8 roots), cold `build` then `merge` on re-entry, so a
  long run refreshes rather than serving a stale scope.
  Vector: `benchmarks/graph_perceive_check.py` **33/33 + 12/12 mutants** (it was
  27/27 + 9/9 when this clause shipped; the extra 6 checks and 3 mutants are
  R-1.1b's per-block switches, which live in the same vector because the switch
  semantics are `_perceive`'s), and the §6
  line runs it as `--sweep` — one fresh process per mutant — because several of
  these bugs live in the cache and the number of checks a mutant fails is
  order-dependent (2/2/7/2/14/2/7/2/2 in one process, 2/1/6/1/13/2/6/1/1 in nine);
  what is claimed is that each mutant is caught by ITS OWN named check in both
  orders. Several of the 33 drive `loop.solve` with a stubbed generator and read the
  retry message back, per R-1.1's lesson — three in R-1.3b's own section, three more
  in R-1.1b's switch checks, each asserting which header does and does not arrive. Cost, printed by the run: **3.0-3.4 ms**
  cold and **0.84-0.91 ms** cached on the fixtures repo; 32-33 ms for the hint pair on
  this repo's 26 files against ~170 ms if the parse were done twice (167 and 169 in
  the two runs taken here, 32 and 33 in the two for the shared path; so the ratio —
  about 5× — is the stable part of this claim, not the millisecond).
  Live (`run-suite --with-context --allow-big never --attempts 3 --trace-full`,
  session `20260927-044613-run-suite-d366`): `r03_bulk_rule`'s two stored retry
  prompts both carry `Dependents of the symbols at issue`, headed
  `CartLine [class] minishop/models.py:20 — 2 symbol(s) reach it` and
  `BULK_MIN_QTY [constant] minishop/pricing.py:5`, with provenance on every line
  (`d1 minishop/cart.py::Cart.add calls it: self.lines.append(CartLine(product,
  qty)) [minishop/cart.py:18 via import-alias]`), and the run's other task solved
  on its first attempt so it paid nothing. `benchmarks/hint_live_audit.py`, which
  took a `--header` for this, reports **2 of 6** post-fix retries carrying it and
  **0 of 248** pre-fix prompts. **No accuracy delta is claimed**: r03 still failed
  all three small-tier attempts, and whether either hint HELPS is R-1.1b's
  unmeasured question.*
- **R-1.4 (PARTIAL)** Perception MUST extend to the second language of real work
  (TypeScript or SQL — pick by ledger evidence, not taste).
  Vector: R-1.1..1.2 equivalents pass on a fixture tree in that language.
  *The choice was measured on 2026-09-28 and the named evidence source could not
  make it: the ledger's 1148 outcome rows (202 task ids, every prompt template
  classified) contain **0** rows asking for SQL and **0** asking for TypeScript —
  all of its demand is this project's own Python suites plus 38 screenshot→HTML
  rows. That null is the finding, so the tie was broken on the tree the tool
  indexes instead: **21 TypeScript-family files (14 `.tsx`, 7 `.ts`) against 0
  `.sql` and 0 `.db`** — the number `flash.lang_ts.ts_files('.')` prints, which is
  what the tool reads and not a `find` over the tree (an earlier draft of this
  sentence said 23 by counting two `.css` files, one of them `site/dist`'s
  generated bundle; a stylesheet is no one's parse target here and build output is
  no one's edit target). TypeScript was picked by a file count; SQL was
  not picked because nothing here is written in it, and §10.2 keeps the call
  overrulable.
  **What shipped:** `flash/lang_ts.py` — a tree-sitter TypeScript/TSX extractor
  that emits `flash.graph`'s own `Node`/`Edge`/`Unresolved` records, so one
  `blast()`, `summary()` and `to_json()` answer across both languages with no
  second query engine. `graph.build(langs=("python","typescript"))` folds it in
  and `flash graph --lang py,ts` asks it, over a `--lang` that refuses a typo by
  name. **Python alone stays the default**, and that default is load-bearing: the
  committed graph figures describe the index the tool builds when nobody opts in.
  Measured on this repo (2026-09-28, three runs of the same build, `python -m
  flash.graph`): 21 indexed `.ts`/`.tsx` files in **44–55 ms** inside a mixed cold
  build of **1.03–1.09 s**; **103 nodes and 240 edges** added to the Python index's
  4446 nodes / 24574 edges, and **151** uses the pass could not place. Every one of
  those 151 is a *counted* blind spot
  with its own sentence — an npm specifier, a name the target file does not
  export, a name that reaches through `export *`, a name that only passes through
  a re-export, an unparseable file's floor, and a missing grammar.
  `benchmarks/ts_perception_check.py --sweep` is **47/47 checks + 13/13 mutants**,
  in this process and one fresh process per mutant.
  **The patch arm learned it on 2026-09-28** (the gap this row filed as (c)):
  `flash/patches.py` now dispatches an address by the file it names — `.ts`/`.tsx`
  to `flash.lang_ts`, everything else to `ast`, including the empty path
  `flash.graph` has always passed, which is what leaves the published Python
  figures the same numbers from the same code path (`78/78` and `60/60` here). A
  TypeScript span comes out of the same `_declarations` walk the graph's node
  comes out of, so "what this patch replaces" and "what breaks if this changes"
  cannot be two different ranges. A replacement must keep the symbol's name and —
  the TypeScript shape of Python's decorator rule — the `export` keyword the owned
  span begins with; silently dropping it un-exports the symbol while the edited
  file still looks fine, so `check_result` refuses it by name. Measured on this
  repo's own front end, not a fixture (`site/src/components/Hero.tsx`, 181 lines):
  `# edit: … :: Hero` resolves to **L31–L180** where it used to be refused as an
  unknown symbol, re-emitting the file's own bytes there applies with **0** lines
  changed outside the span, dropping the keyword refuses with
  `no longer exports Hero (as Hero L31-L180)`, and a replacement that is not valid
  TypeScript refuses at the line the grammar marked (`line 32: error 'return <div>'`)
  instead of with Python's `invalid syntax`. `workspace_from_dir("site/src")`
  gathers **20** files, all 20 TypeScript, so a symbol address names a file the
  project has. Vector: `benchmarks/ts_patch_check.py --sweep` — **52/52 checks +
  15/15 mutants** (44 checks / 13 mutants when this row was written; the extra
  eight and two are R-7.15b's create verb arriving in the second grammar),
  one of which is `loop.py`'s own edit arm driven with
  `_generate` and `diagnose_files` stubbed, because its `ast` static pass would
  otherwise hand a clean TypeScript edit a confident false syntax error and spend
  the retry the refusal exists to save.
  **What is NOT closed, and is the reason this row is PARTIAL:** the vector names
  R-1.1 and R-1.2's equivalents, and neither runs on TypeScript yet. (a) The
  loop's PERCEIVE hint still ranks symbols with `flash.lsp.symbols_involved` and
  `graph.scope_graph()` builds Python-only, so a failing `.tsx` test gets no graph
  block — 47/47 of it is offline perception, not the retry path. (b) R-1.2's
  live-upgrade equivalent needs a `tsserver` seam; `graph.live_upgrade` still asks
  jedi, which answers about Python and nothing else. (c) is closed; the range form
  and the symbol form are now equally precise, and the symbol form is the one the
  protocol tells the model to prefer. (d) What (c) bought is the ACT leg, not a
  verdict: the whole-file control arm (`harness.CODE_FENCE` and its `# file:`
  heading regex) is still Python, and nothing here *verifies* a TypeScript edit —
  `diagnose_files` has no `vitest`/`node` runner behind it, so a `.tsx` patch that
  parses is accepted on the strength of a parse. Vector for the rest: a TypeScript
  fixture suite run through
  `flash run-suite` with the graph block asserted per retry, plus a `tsserver`
  `--live` equivalent.*

### B. DECIDE & ROUTE — cheap decisions before expensive generation

- **R-2.1 (SHIPPED)** One-forward-pass typed decisions with restricted softmax.
  Vector: `flash decide-batch` 12/12 (live arm; recorded in Appendix A).
- **R-2.2 (PARTIAL)** Routing MUST be trained on outcomes, and MUST NOT be
  trusted until it generalizes. Gate default stays off while held-out
  leave-suite-out AUC is 0.569.
  Vector: `flash router-fit` prints LOO precision and a held-out AUC
  **≥ 0.75** on a fresh suite before `run-suite --threshold 0.5` becomes default.
  *Generalization has two parts, and the second was broken until 2026-09-26: a
  bundle is a function of ONE model's hidden states, so it must refuse a probe
  from another model instead of scoring it. `flash/learn.py` now keys the
  embedding cache by the weights that produced it
  (`prompt_embeddings_last_<repo>.npz`, the unlabeled legacy file adopted once on
  first use), stamps `small_repo`/`pool` into every bundle, embeds the live probe
  with the pool the fit used, and `route_score()` returns `None` plus a reason on
  a width mismatch instead of raising inside the loop. Vector:
  `benchmarks/router_portable_check.py` 20/20 + 5/5 mutants.*
  *What the audit found was worse than a crash, and it was silent: the fit pooled
  the LAST token and `embed_text`'s default pooled MEAN, so every live `route_p`
  came from a differently-pooled vector than the one the logistic fit was trained
  on. Measured on the 106 prompts cached for this model
  (`benchmarks/router_pool_audit.py`, forward passes only): median |ΔP| **0.297**,
  median P 0.180 → 0.511, and **47 of 106** decisions would have been
  big-directed at cutoff 0.5 that the fit's own pool would not have. Corroborated
  from the ledger rather than asserted: of 621 rows whose prompt is cached, **316
  reproduce exactly from the mean pool of the bundle on disk and 0 from the pool
  it was fit on**; the other 305 match neither, so they predate the current fit —
  a silent refit is a second confound this audit names instead of folding in. No
  recorded pass rate moved, because the gate is disarmed at
  `run-suite`'s default `--threshold 1.1`; the damage is a wrong `route_p` column
  and a wrong answer for anyone who ran `--threshold 0.5`.*
- **R-2.3 (PARTIAL — mechanism shipped, both gate clauses measured on live arms and
  missed)** The
  prospective confidence signal (§34.2) MUST come from verification evidence —
  static diagnostics, coverage, suite flakiness — not the model's own probability.
  Shipped: `flash/confidence.py` (`--confidence`) judges the answer the visible
  oracle already accepted, from four streams — static errors, the share of its
  statement lines the visible tests executed, the visible verdict re-run under
  three `PYTHONHASHSEED`s, and shape-typed adversarial calls that crash or hang.
  Vector: on a seeded subtle-bug suite, the fallback is offered on ≥ 90% of
  outputs that would fail hidden tests, with < 1 false escalation per 20
  routine tasks.
  *Offline 2026-09-26: `flash.confidence --selftest` 21/21, wiring
  `benchmarks/confidence_wiring_check.py` 30/30, seeded-suite premise
  `benchmarks/subtle_premise_check.py` 52/52 (recall 6/6 on the would-fail
  answers, each class fired by its own stream), differential-key premise
  `benchmarks/p6_key_check.py` 13/13. Stated limit: the two masked-value cases
  are NOT offered and cannot be — no stream reaches a wrong value on a line that
  ran, in a field the visible asserts never read.*
  *Live 2026-09-26, 7B small tier, 23 tasks, AC maximum-performance, 102s,
  23/23 solved: **recall clause not measurable — its denominator is empty**
  (0 of 23 answers fails its held-out key, so ≥ 90% of nothing is untested, not
  passed); **false-offer clause missed by one offer**: 1 false offer over 15
  hidden-accepted routine answers (1 per 15, gate < 1 per 20), 2 over 23 blended.
  The one routine false offer is `max_subarray([]) -> IndexError` on an answer
  whose own reference raises the same way — the edge stream calling a crash the
  spec itself admits a finding. Closing this needs a population that actually
  produces hidden failures (harder tasks or a weaker tier), not a softer key.*
  *That population was built 2026-09-27, and building it exposed two unbounded
  hazards in the shipped probe design. `benchmarks/tasks/p6b_tasks.jsonl` is the
  wide instrument — **59 rows, 8 seeded-subtle + 15 keyed routine + 36 keyed hard**,
  each key written from the task's own shipped reference and verified against it at
  all three hash orders before the file is written; tags stay separate so widening
  the recall denominator cannot move the per-20-ROUTINE clause.*
  *Live on it, 2026-09-27, two tiers. 7B (`arm_7b_p6b.log`, 47/59 solved): recall on
  would-fail-hidden **4/11** and **5 false offers over 48 hidden-accepted answers**,
  routine population 0/1 and 1 over 15 tasks — both clauses MISSED, now with real
  denominators. 1.5B (`arm_1b5_p6b.log`, 7/59 solved, 52 escalations denied by the
  §34.1 governor): recall **44/52 = 84.6%**, routine 13/15 = 86.7%, 1 false offer
  over the 7 hidden-accepted answers it produced, and the routine false-offer clause
  printing `NOT MEASURABLE` because none of its 15 rows was accepted by a hidden
  test. A weaker tier does answer in a way that passes the visible oracle and fails
  the held-out key, which is the population §34.2's clause needed. Five of the eight
  misses are coverage-shaped but above `COVERAGE_TAU` 0.55 (0.667 to 0.875); three
  have total visible coverage and a wrong value anyway — the masked-value blind spot
  already stated, not a new one.*
  *`benchmarks/confidence_tau_check.py` **7/7** then tests the obvious fix, and the
  obvious fix does not work. Re-deriving the shipped predicate from each arm's own
  recorded per-answer numbers — it must reproduce the recorded `conf_offer` on 59/59
  answers at both tiers before any row prints, and a model that drops the static
  short-circuit is the mutant that proves that clause bites — tau 0.85 lifts the
  1.5B's recall to 92.3%, over the gate, while the 7B's reaches only 63.6% and its
  routine false offers go from 1 per 15 tasks to **1 per 5**. The two clauses pull
  opposite ways across tiers: R-2.3's gate is not reachable by tuning a threshold on
  this feature set, and needs a stream that reaches a wrong value on a line that ran
  (§32.4's untried levers). The sweep is a re-derivation from recorded fields, not
  the four streams re-run over the answers, and 7 hidden-accepted answers cannot
  resolve a per-20-tasks clause — both clauses stay NOT MET.*
  *Hazard 1, an unbounded probe: `PROBE_BATTERIES["int"]` carried `10 ** 4`, so
  `edge_probe` planned `spiral(10000)` for a **correct** reference and measured
  **2.29 s and 1445 MB** in the probe child before the 2-second alarm fired, which
  the module then reported as `spiral(10000) -> HANG` — the instrument's own budget
  failing, filed as the answer's bug. `confidence._affordable` now bounds what
  `_probe_calls` will plan (int magnitude 1000, 32 elements, 64 characters) and the
  shipped battery's largest sentinel is ±10³. A memory ceiling in the child is not
  an alternative on this box: `setrlimit` for `RLIMIT_AS`, `RLIMIT_DATA` and
  `RLIMIT_RSS` all raise `ValueError` here (measured), while `RLIMIT_CPU` does
  enforce (a busy loop died at exactly 1.00 s) — which is also a stated limit for
  R-9.2's "cpu and memory rlimits" clause.*
  *Hazard 2, an unbounded key: the committed 23-row instrument held **87 568 bytes
  of expectations, one line 82 183 characters long**; the bounded generator refuses
  an expectation over 2000 characters (the wide run dropped `spiral(1000)` at
  **7 890 896** of repr), rewrites the same 92 key lines into **3 576 bytes**, and
  refuses to write a key that only repeats the task's own visible test — three hard
  keys were more than half repetitions, because a battery of small integers collides
  with what a test typically asserts. `flash.confidence --selftest` 21 → **25**,
  `benchmarks/p6_key_check.py` 13 → **28** (the eight clauses now audited over both
  instruments, each bound proven by removing it),
  `benchmarks/confidence_wiring_check.py` 30 → **35**: a clause whose denominator is
  0 now prints `NOT MEASURABLE` with its own label instead of a ratio that reads
  like a result — the shipped printer used to emit `0 false offer(s) over 0
  hidden-accepted`, a clean pass that measured nothing.*
  *Hazard 3, an instrument death filed in an evidence slot: the 7B's answer for
  `h15_shell_split` put a self-check at module level — `assert rt.shell_split(…)`
  comparing the return value against the `ValueError` class — so importing it
  raised, the probe child died at `import answer` before it could print its JSON
  line, and `edge_probe` filed the last 120 characters of the child's raw stderr as
  an edge finding. Verbatim from `arm_7b_p6b.log`, that report was
  `edges: <probe crashed>() -> rt shell_split("unmatched 'quote") == ValueError`
  followed by the traceback's caret line and `AssertionError` — three physical
  lines inside a log whose contract is one readable line per task. The driver now
  guards its own import and the module renders
  `edges: answer does not import (AssertionError)`; a child that dies uncatchably
  still reaches the fallback, and both paths flatten whitespace, so no reason
  string can carry a newline or a temp path. `flash.confidence --selftest`
  25 → **29**, proven by mutations that each fail exactly their own checks: taking
  the import guard away fails 2, taking `_filtered`'s flattening away fails 1,
  taking the fallback's away fails 1. Taking BOTH the guard and the fallback's
  flattening away — the shipped-before state — fails 3 and reproduces the recorded
  artifact: the reason string becomes **3 physical lines**,
  `shell_split("unmatched 'quote") == ValueError` / the caret row /
  `AssertionError`, the same shape `arm_7b_p6b.log` carries.*

### C. ACT — generate, then edit precisely

- **R-3.1 (SHIPPED)** Multi-file answers MUST round-trip through the
  `# file:` contract with per-file persistence and targeted repair.
  Vector: `run-suite --tasks benchmarks/tasks/mw_tasks.jsonl` at 6/6.
- **R-3.2 (PARTIAL — clause 1 MET, clause 2 NOT MET, clause 3 MET)** Edits SHOULD be
  symbol-precise patches, not text guessing (PLAN §33.1 ACT bullet).
  Shipped: `flash/patches.py` (`--edit`) — `# edit: <file> :: <Symbol>` or
  `L<a>-<b>` or `*`, plus one fenced block. The AST owns the replaced span
  (decorators included, so dropping `@property` is impossible), a column-0
  replacement is re-indented to the symbol's real nesting, and bytes outside
  the addressed range are copied rather than regenerated. Every failure mode
  is a refusal with a reason and an unchanged project: unknown symbol, ambiguous
  address, backwards/EOF/straddling range, overlapping set, file not in the
  project, replacement that does not parse, replacement that loses the symbol it
  addressed. An address WIDER than the change is narrowed before it is written
  (`narrow`): the applier diffs the owned block against the replacement and
  splices only the runs whose bytes differ, bottom-up, copying every other line
  of the block out of the file — so `# edit: box.py :: Box` cannot re-emit a
  sibling method that happens to be unchanged. A sibling that really did change
  is still spliced and still counted. `outside_lines` therefore measures lines
  whose BYTES were regenerated, not lines inside the addressed span, and the run
  report says so (`[1 of 13 lines rewritten]`). Offline:
  `python -m flash.patches --selftest` **78/78** (7 of them drive the loop's
  patch arm against a scripted generator, so the wiring is proven without
  charging a model; 9 more pin the narrowing — byte-identity with the wide
  splice, a count-changing run before another, a real sibling drift that must
  still score, a verbatim re-type that must write nothing; 17 more are a new
  `# 7b. CREATE` section pinning R-7.15b's shape — 12 on what a create writes
  (`# edit: f :: +Name` as a pure insertion, the applier owning the
  blank lines above it, the new symbol addressable afterwards, and the PROTOCOL
  the model is shown actually naming the form) and 5 on what it must refuse; 8
  more are a `# 7c. ORACLE` section pinning R-7.15e — the refusal fires before
  the address is resolved, all four kinds are refused, an unrelated symbol patch
  still applies while the oracle is named, the identical patch applies when no
  oracle is named, and the PROTOCOL the model is shown actually says so;
  `--suite` **60/60**
  premise checks over `benchmarks/tasks/edit_tasks.jsonl` (each task ships its
  own project text, so both arms read identical input, fails as seeded, passes
  on the reference patch, and that patch fits inside one symbol).
  * **clause 3 — the write-back, filed 2026-09-28 after the defect was found
    live, MET.** The two clauses above are about what a patch set *is*; neither
    one says what it does to the tree. `--edit` scored every attempt in memory,
    printed `solved=True`, and left the project on disk byte-identical — a
    sentence nobody printed. This ran on a scratch tree outside the repository the
    same day (three attempts, a green oracle, a trace session in
    `benchmarks/results/traces/`, and the one module still holding its original
    four lines), which is the shape of the bug: a person reading
    the last line of a terminal takes the verdict for an edit that happened.
    `flash.patches.land` now writes the verified workspace back. It touches only
    files whose bytes differ, never deletes a module the listing stopped
    covering, refuses an address that resolves outside `--context` or names a file
    the oracle never scored, validates the whole set before the first byte so a
    refusal cannot leave a half-patched tree, and returns per-file line counts
    that are lines rather than diff hunks. Writing stays opt-in
    (`run --edit --context <dir> --apply`) because a small model's guess at
    someone's source is not a reason to change it; what is no longer optional is
    the sentence naming which state the tree is in — without `--apply` the command
    prints `NOT APPLIED` and exits on its verdict, with it each landed file prints
    `wrote <file> (+A -B lines)`; R-7.15b's create prints `+A -0`, because a
    create reported as `+0 -0` is the same sentence as a run that wrote
    nothing). The oracle is protected by name, because
    `workspace_from_dir` lists every Python file in `--context`, the test file
    included, so a patch set that repaired a failure by weakening an assertion is
    REFUSED rather than written. Offline:
    `python benchmarks/patch_landing_check.py --sweep` **61/61 checks, 28/28
    mutants caught** (40 checks / 22 mutants when this clause was written; 8
    checks and 2 mutants more are the create shape, at the arm, on disk and
    through the real command; 5 checks and 3 more are R-7.15e's oracle key — the
    command's own `_oracle_key`, the key handed to the arm on the task, the
    refusal naming the oracle rather than the line count, and the control where
    no key is handed and the same patch applies; 8 checks and 1 more are
    R-7.15f's project block reaching both arms, the two arm messages being
    identical up to the sentence that names the answer format, composing being
    idempotent, and the mutant that hands the arm only its typed ask) — the sweep runs each mutant in
    its own process and the
    in-process lane agrees, so no count below is a leftover from the previous bug.
  Vector, measured 2026-09-26 and re-measured 2026-09-27 after the narrowing —
  small tier, greedy first attempt, `--allow-big never`, 10 tasks, two arms on
  the same input:
  * **clause 1 MET**: **8/10 solved, all 8 within 1 attempt** (gate: ≥ 8 in ≤ 1);
    0 refusals, 0 whole-file rewrites. `benchmarks/results/edits/arm_edit_small.log`.
  * **clause 2 NOT MET**: **7 lines touched outside the annotated symbol**
    (gate: 0), all from one task → **1 line** after the narrowing, from a
    different task. `arm_free_small.log` is the matched control at the same
    8/10; the narrowed arm is `arm_edit_small_narrow.log` and, with the model's
    raw text on record, `arm_edit_small_narrow_full.log` (traces
    `20260927-023150-run-suite-7ba6`, `20260927-023330-run-suite-cde5` — the two
    agree per task to 0.1 s, so the residue reproduces).
  e04's cause, from trace `20260926-060927-run-suite-526b`: the request named
  the class (`Box`), the change lives in `Box.__init__`; the model addressed the
  noun it was given and re-typed the class correctly. **The address width
  follows the noun in the request, not the locus of the change.** All 7 of those
  lines were this and nothing else — the class came back byte-identical apart
  from `__init__`, so the tool had been regenerating text it needed no part of.
  Replaying that day's exact greedy text (`prompt_tokens 550`,
  `completion_tokens 74`, address `box.py:Box`, captured with `--trace-full`)
  through both modules side by side measures **7 → 0** at identical bytes: the
  new summary reads `box.py:Box L4-L13 [1 of 10 lines rewritten]`, one line
  because only the clamp line differs. The
  narrowing is the fix for that, and it is a mechanism change, not a re-roll:
  the shipped tool now writes a different set of bytes than the 2026-09-26 arm
  did, which is why the arm was run again (contrast P4-follow-up in TODO, whose
  fix changed only future retries and was deliberately NOT re-measured).
  The **1 line that is left is a different fact**, and `--trace-full` names it:
  e09 (unsolved) sent two patches. Its `TaskQueue.pop` block was verbatim the
  text already in the file — narrowed to zero runs, nothing written, 0 lines
  charged. Its `TaskQueue.push` block added a real `self._items.sort()`, a line
  the request never asked for and the annotated symbol does not own. Running
  that same text through both modules side by side measures the change precisely:
  HEAD's `outside_lines` reports **2** for it (the whole addressed `push` span),
  the tree reports **1** (the line the insertion lands after), the run's own
  summary says `taskq.py:TaskQueue.push L8-L9 [1 of 2 lines rewritten]` and
  `taskq.py:TaskQueue.pop L11-L14 [0 of 4 lines rewritten]`, and both write byte-
  identical text. So the remaining line is the model
  solving the requested behaviour in a member the request never named — content,
  not bytes the tool re-emitted. It is the same §10.6 ambiguity, now with the
  mechanism's contribution at zero and only the reading of "target symbol"
  standing between 1 and 0.
  Cost, stated precisely: the patch arm is **2.7× faster** (6.0 vs 16.3 s/task)
  and decodes **3.6× fewer tokens** per generation (51 vs 184) at a *slightly
  higher* total token count (8169 vs 7845) — a patch prompt carries ~160 tokens
  of protocol and re-sends the project every attempt. On 4-bit Apple-Silicon
  decode the binding cost is steps, not tokens, so the win is wall clock and the
  structural guarantee, not spend. Both arms fail the same two tasks
  (`round()` → banker's rounding; wrong priority order), putting those on tier
  capability rather than protocol.
- **R-3.3 (SHIPPED)** Tournament mode (§33.4): k candidates under the power
  governor's width cap, scored by the oracle, best-of-k adopted.
  Vector: on the hard family (h-tasks), best-of-3 beats single-attempt pass rate
  by ≥ 8 points at equal or lower total token spend, on AC power.
  *Run 2026-09-26, 8 h-tasks, small tier, `--allow-big never`, AC width 4:*
  in-arm on matched input (candidate 0 = the single attempt), pass@1 **5/7 →
  best-of-3 6/7 = +14 pts** ✓; spend clause ✓ — both arms 12 generations,
  tournament **3 330** tokens vs chain **5 559** (−40%), wall +5%. Suite level
  7/8 vs chain 6/8; that delta is one task and single-run swings are ±2, so
  the in-arm figure is the one the gate rests on. Candidate 0 is always greedy
  (pass@k contains pass@1); adoption is first-pass; ranking only chooses the
  surfaced diagnostic (6/7 real hard-task failures die at assert 1, so the
  value is pass@k, not ranking). The width clamp is the AC-only enforcement:
  a battery launch clamped to 2 was stopped and kept as non-evidence, and one
  mid-run `low-power` probe declined the tournament per-task with the reason
  in the route record. `python -m flash.tourney --selftest` 16/16; logs under
  `benchmarks/results/tourney/`.*

### D. VERIFY — the oracle is the product

- **R-4.1 (SHIPPED)** Candidate code runs only in an isolated subprocess with a
  hard timeout, and failures report GOT vs WANT for the first failing assert.
  The reported values MUST come from the same evaluation that decided the
  verdict — an assert whose side mutates (`q.pop() == "high"`) used to be
  evaluated twice, so a *failing* assert could report `GOT: 'high' |
  WANT: 'high'` and the retry got feedback that named no difference.
  Vector: `m0_bakeoff.py --dry-run` 20/20; `python -m flash.harness --selftest`
  **28/28** (the original 12, the 8 `score()` ranking checks added for
  R-3.3, which keep `diagnose == score's verdict` proven, and the 8 `score_files()`
  precedence checks added for R-7.15g), including a counting call whose printed GOT must be the value the
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
  in `TODO.md` states the instrument that could measure it.
  *The instrument was built and the A/B re-run on it, 2026-09-26: the gate is
  **MISSED with a denominator that can now see it.***
  `benchmarks/tasks/dbg_band_tasks.jsonl` is 30 tasks — 18 selected because
  `ledger.jsonl` records them solved on the small tier at attempts ≥ 2, never
  shed, and 12 generated into that shape (blind repairs, tests withheld, TWO
  independent bugs; 172 offline checks + 5 mutants prove the shape, and the
  suite is a frozen ledger cut so the 30 stay the 30). Its band is wide where
  the others were not: **15 of the 30 tasks took at least a second attempt**
  (8 at two, 7 at three) — against one task in the earlier substrate.
  Arm A 27/30, arm B 25/30, both arms spending 52 attempts, wall 414.5s →
  463.5s (+11.8%). On the 25 tasks that ran the small tier in **both** arms
  the score is **25/25 vs 25/25**, and the whole gap is
  `h12_min_remove_parens` and `mw1_ringbuf`, which reached the
  escalation boundary under B and were denied by §34.1 at `--allow-big never`
  — the same shed-tier confound R-6.4's weights arm hit. That decomposition is
  not an exoneration, and the digest's own footprint is stated at its measured
  size: it **moved attempts** on two tasks and **moved one outcome**. 28 of 30
  tasks have identical attempt counts in both arms; `fix01_alias_sort` went
  3 → 2 (the one effect the earlier substrate also showed, now reproduced, and
  still no outcome change — it passed either way); `mw1_ringbuf` went 2 → 3,
  and that third attempt is what put it over the boundary it was denied at; on
  `h12_min_remove_parens` both arms used 3 attempts and only the traceback
  arm's third one passed. Net over the run: **zero tasks gained, two lost**,
  on identical attempt budgets per arm — and at n=1 per task none of
  those single-task movements can be separated from decode noise, which is
  exactly why the gate is stated as ≥2 rather than ≥1. A mechanism that changes
  how many tries a task takes on a band 15 tasks wide and loses more tasks than
  it wins is a real result, not an instrument failure — so this is booked as a
  measured miss rather than retried into a shape that flatters it. `--debug`
  therefore stays **off by default**.
  **2026-09-27, found by the path-portability vector (R-7.4): the tracer was
  exec'ing the oracle's `sys.path` bootstrap AFTER the candidate, inside the
  traced region.** `run_test` has always hoisted it first; `_run` did not, so a
  candidate that imports the repository at top level died on its own `import`
  before the traced execution began, and the digest reported the harness's crash
  instead of the candidate's trail.
  **Scope, measured rather than asserted** — every one of the **72** context
  reference solutions was run through `debug._run` twice, once against the shipped
  build and once against `git show HEAD:flash/debug.py` loaded as a separate
  module, each with the path the corpus used to carry already correct (so this
  measures the ORDERING, not the token): **42 diverge** — old
  `ModuleNotFoundError`, new `pass` — and the split is exactly "does the
  candidate import the repo in its first lines", 42 do and 30 do not. The 30 were
  never affected, because it is the test that imports their fixtures and the test
  runs after the bootstrap either way.
  **And R-4.3's own measured miss is NOT undone by this**, which is checked
  rather than assumed, because the temptation to reopen a negative result on a
  newly-found bug is exactly what the correction rule exists to stop. The band
  corpus is self-contained: its two bootstrap-carrying tasks (`mw1_ringbuf`,
  `mw3_registry`) insert `<TMPDIR>`, which the driver already has on its path, and
  the same old-vs-new probe gives them **the same verdict and the same 104-line
  trail** (the trails differ only in `<object at 0x…>` reprs). Both arms of the A/B
  therefore received the mechanism on every one of the 30 tasks, and the two tasks
  arm B lost are not losses this bug can explain.
  Why no vector saw it: `dbg_tasks.jsonl`, the corpus `--suite` reads, carries
  **0** `sys.path` lines in its 8 tests, so `_hoist_path_bootstrap` returned an
  empty bootstrap and the ordering could not matter — while `m2_tasks.jsonl`
  carries 5 and `hint_ab_tasks.jsonl` 10. `--selftest` (55/55) and `--suite`
  (32/32) pass identically before and after, and that IS the finding: a seam
  certified only on tasks that do not need its dependency cannot see the
  dependency break. Fixed in `flash/debug.py` (bootstrap to its own file, exec'd
  before `sys.settrace`) and gated at the seam by
  `benchmarks/portable_paths_check.py`, which drives all four seams over all 72
  reference solutions from a foreign cwd.
- **R-4.4 (SHIPPED)** Vision outputs MUST be scored by a pixel oracle with a
  coverage guard so an empty page can never win.
  Vector: `calibrate_visr.py` + `visp` 5/5, `visr` 4/5 with real4 recorded as a
  documented capability ceiling.

### E. ENDURE — power, recovery, observability

- **R-5.1 (SHIPPED)** The loop MUST consult the system profile before loading a
  model and MUST record a refusal instead of silently degrading.
  Vector: `flash power --selftest` 24/24 (22 until R-7.16's CI round made the memory
  reading portable — see §6); live battery A/B on r03 (shed vs
  allowed, Appendix A 2026-09-25).
- **R-5.2 (SHIPPED)** A session MUST survive interruption at suite
  granularity, resume without re-billing settled work, and store its own
  parameters.
  Vector: `benchmarks/trace_resume_check.py` 11/11.
- **R-5.3 (SHIPPED)** Recovery MUST reach task granularity: a partial generation
  and the sandbox state of the in-flight task are checkpointed, so SIGKILL
  mid-task resumes inside the task.
  Vector: `kill -9` a live `run-suite` between two tokens, `flash resume`, and
  the in-flight task completes without regenerating its first attempt.
  *Offline 2026-09-26: `python -m flash.checkpoint --selftest` 31/31 (frame
  identity, flush cadence, atomic durability under two real `kill -9` races —
  0 torn reads in 35k parent reads) and
  `python benchmarks/checkpoint_resume_check.py` **35/35**, seven scenarios, each
  a real SIGKILL on a real `flash run-suite` child resumed by a fresh process
  with only the model scripted: mid-generation, repeat-kill (two crashes in one
  span), mid-chain, multi-file sandbox union, tournament candidate, big tier,
  inertness. Measured, not asserted: the resume's prompt carried the dead run's
  last durable 64 characters and decoded only the remaining 125 of 189; the
  killed task settled with `attempts=1`, and the repeat-kill span settled with
  one attempt after three processes. Stable over five consecutive clean runs
  (the fifth is this requirement's line in the §6 battery re-read below);
  deleting the carried text from the resume fails 15 of the 35, so the gates
  have teeth. Two defects were found BY the vector: the reactive escalation
  labelled its big-tier frame `small` (which made the "skip the exhausted small
  tier" branch unreachable), and a second kill inside one generation carried
  only its own newest fragment, losing the first — `flash.checkpoint`'s
  composition check now pins the cumulative contract.*
  Not covered, stated so: the patch arm (R-3.2) shares the begin/owns/restore
  shape but is not killed separately; R-4.2's mask replay is rebuilt from
  checkpointed ids and checked at the DFA level only (`grammar --selftest`);
  R-5.4's 24h gate stays open (§9).
  *Live 2026-09-26 (`benchmarks/live_checkpoint_arm.py`, 7B small tier, m0's
  first 3 tasks, AC maximum-performance, `--allow-big never`, log
  `benchmarks/results/p7/live_arm_7b_kill_resume.log`): `kill -9` landed 3.5s
  into the run, inside t01's attempt 0, with 16 tokens / 62 characters durable;
  `flash resume` settled it `solved=True attempts=1 tier=small` and the whole
  suite 3/3 in 15s. The arm then runs the same argv with no kill and prints the
  two sessions side by side from their own records: the resumed answer is
  **byte-identical** to the control's (sha1 over `--trace-full` outputs, all
  three tasks), `completion_tokens 22/22`, `prompt_tokens 94 vs 78` — the prompt
  grew by exactly the 16 tokens the checkpoint carried — and that attempt decoded
  in **455ms against the control's 960ms**. At task granularity the ledger
  agrees: the resumed t01 records 1.3s where the control's cold t01 records 1.8s,
  and the killed-plus-resumed session leaves exactly one row per task, so nothing
  was double-billed. t02/t03 are untouched (`resumed=None`, their spans cost the
  same in both runs), so the recovery costs the rest of the suite nothing.
  Run twice that day; the earlier one printed 456ms against a 968ms control.*
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
- **R-6.3 (MET, within stated precision)** Label hygiene MUST be preserved as
  the ledger grows: rows produced under a shed, forced or benchmark-override
  policy are labelled honestly and excluded where they would self-amplify.
  Vector: a re-fit trained only on `trainable()` rows, evaluated on a held-out
  family, reports the same AUC as a re-fit trained on all rows *minus* the
  override rows — i.e. the exclusion is not costing signal.
  *Run 2026-09-26, `benchmarks/trainable_audit.py` (16/16 offline, log
  `benchmarks/results/p6/trainable_audit.log`): fit A = 314 `trainable()` rows,
  fit B = 370 rows minus only the *mandated* policy exclusions, so B keeps
  exactly the 56 vision rows A drops and the audit measures trainable()'s one
  exclusion beyond the hygiene minimum. Shared honest eval pool: 66 held-out
  rows (m4–m7, none big-routed), 10 positives. Pooled AUC **0.618 vs 0.736**;
  difference −0.118 with a bootstrap 95% interval **[−0.369, +0.099]** over
  4 000 replicates — the gap does not separate from zero, so no measurable
  signal is lost. The precision is part of the claim: at 10 positives a true
  gap of up to ~0.37 could hide here.*
- **R-6.4 (OPEN — first arm measured, gate MISSED)** One component MUST be shown
  to improve by learning, not by editing (gate for §27 autopoiesis, G9).
  Vector: a documented before/after on a frozen suite for skills, memory or
  weights — whichever lands first — with I-2's harness gate satisfied.
  *Weights arm measured 2026-09-26 on m0 (20 tasks, AC, `--attempts 2`,
  `--allow-big never`), the adapter fit on 34 verified rows over 12 tasks and
  promoted at step 16 of 128 (best valid loss 0.2237):*

  | arm | m0 solved | tier split | s/task |
  |---|---|---|---|
  | base `Qwen2.5-Coder-7B-Instruct-4bit` | 18/20 | 18 small, 2 shed | 7.0 |
  | `+lora:v1` | **16/20, twice** | 16 small, 4 shed | 72.6 / 87.8 |
  | `+lora:v1-shuffled` (control) | 19/20 | 19 small, 1 shed | 7.7 |

  The gate misses on both halves of I-2: the trained arm does not beat the frozen
  harness (−2 tasks) and it costs ~10x the seconds. The control — same shapes,
  same rank, same config, values permuted inside each tensor — scores *higher*
  than the trained adapter, so nothing in this measurement attributes to what was
  learned rather than to an adapter being present. Two mechanisms, both measured
  rather than assumed:

  1. every extra failure in the trained arm is a **shed-tier** loss: it escalates
     4 tasks where the base escalates 2, the §34.1 governor denies them on this
     box, and shed-tier solves none. On the 16 tasks that did run the small tier
     the trained arm is 16/16, exactly the base model's rate on its own 18;
  2. after the answer ends the trained weights **degenerate into `!!!!`
     repetition**, and mlx's `generate` is called with no stop list, so an
     attempt burns its whole 1024-token budget at 15.6 tok/s (66s) to ship a 147
     -token answer. That is a defect in the weights, not a cost of the adapter
     path: the control decodes at the same rate and stops normally, and a
     one-process probe of load time, time-to-first-token and steady rate found no
     adapter-path cost at all (0.7–1.1s load, 202–286ms TTFT, 14–18 tok/s across
     base, `v1` and both controls).

  The in-distribution arm — "did it learn at all?",
  `flash train --suite-from-dataset` on the 12 tasks the weights were fit on —
  has **no headroom by construction**: the base model already solves 12/12 of
  them, because the mining law (§27.3) admits only oracle-verified successes, and
  those are tasks this router already passes. So no pass rate on this dataset can
  rise. Closing R-6.4 needs rows the current tier *fails* — the store does hold
  failed attempts followed by the oracle's complaint (12 of the 48 mined rows are
  such a repair turn), but every one of them ends in a task this router
  eventually passed — or a component with measured headroom: skills or memory
  rather than weights.*

### G. PRODUCT SHELL

- **R-7.1 (SHIPPED)** A single-command agent surface with typed flags, honest
  exit codes and a printed cost report. Vector: `flash --help`, all commands
  above.
- **R-7.2 (SHIPPED — offline vector MET; live vector MET on this repo, 2 verified
  drafts, and the earlier "0 drafts" reading is withdrawn)** Ambient mode (PLAN
  §33.5): on idle + AC, the agent MAY prepare **draft PRs** for morning review —
  never merging, never auto-applying.
  Vector: an overnight run leaves ≥ 1 reviewable draft diff plus a trace, and
  0 commits pushed, 0 files modified outside its own worktree.
  Shipped as `flash/ambient.py`, reached as `flash ambient`: three deterministic
  repo checks (lint drift, suite premise, package-map drift) whose own green/red
  verdict is the oracle for the draft that claims to fix them. The offline half
  is measured: 61 checks against a temp git repo — 3 verified drafts, each
  `git apply --check` clean, plus 0 pushes (`git branch -r` empty and no push in
  the window's own command log), a byte-identical working tree outside the
  worktrees, an unmoved HEAD, and a trace of every decision — and then each
  guarantee is broken on purpose six times (allowlist guard out → a push is
  issued; containment guard out → a file lands beside the worktree; oracle out →
  a harmful draft ships as "verified"; additive-line clause out → a re-wrapping
  draft ships; newline carrier out → the diff ships de-newlined; name matcher
  widened → a draft the map cannot resolve goes green) so the boundary is known
  to be enforced, not intended.
  **What the live windows bought, in order: two oracle bugs, then a wrong causal
  story, then the fix.** Qwen2.5-Coder-7B-Instruct-4bit on this repo's one red
  finding, 2026-09-26. Window 1 refused (62.1s) because the work list came from
  the *checkout* while the fix was verified in a *HEAD* worktree — an untracked
  module produced a finding no worktree could clear; fixed by reading HEAD
  (`head_tree`), which makes `--dry-run` and a live window agree by construction.
  Window 2 produced the first verified live draft (77.8s, 1 attempt) — and
  replaying its stored diff shows all three clauses passing on a change that
  **re-wrapped 20 lines of the map** and dropped the file's trailing newline:
  `verified` meant "the check is green", not "a reviewer would merge this". Fixed
  by the additive clause (an ADD-only finding may delete no line) and by carrying
  the newline back at the write layer, each pinned by a check and a mutation.
  Windows 3, 4 and 5 (87.9s, 88.8s, 108.0s, 3 attempts each) then produced no
  accepted draft, and the obvious story — "this tier cannot echo a 45-line file"
  — was WRONG, and is retracted. `benchmarks/ambient_echo_probe.py` runs the
  2x2 over the two variables that story conflated (echo length x number of
  entries to place) and **all four arms produced a verified draft**: 3x1 in 2.3s,
  52x1 in 64.1s (2 attempts), 3x3 in 3.8s, 52x3 in 74.7s (2 attempts). The
  failing windows had echoed LESS than the passing arms — prompt 831–1070 tokens
  and completion 677–714 against the 52-line arms' 984–1088 and 802–830 — so
  neither echo length nor insertion count was the ceiling.
  `--inspect` then named the mechanism by looking at the raw text rather than the
  verdict: on greedy decode the model writes the entry as
  `ambient — ambient context and environment handling.`, a BARE name, and the map
  parser reads `flash.<name>` or a backticked `<name>` only. The finding survives
  however good the clause is. The prompt had asked for "the module name" without
  saying in what form, so it was answered in a form the check cannot see.
  Stating the accepted form in the prompt closed the live half on the real repo:
  window 6 produced a verified additive draft on its FIRST (greedy) attempt in
  33.6s, and window 7 repeated it in 43.9s. Both diffs are pure additions
  (5 lines and 4), no existing line moved, no trailing-newline damage, the names
  in the readable form — the boundary across all seven windows is 1 worktree at a
  time (0 leaked), HEAD unmoved at 4ea9c49, 0 pushes and no remote configured.
  **The remaining gap is prose, not mechanics.** Window 6's draft was structurally
  perfect and useless to a reviewer: it invented filler
  ("Ambient context processing for the coding environment") because the prompt
  asks what each module does while showing the model only the map. Fixed the same
  day by quoting each module's own first docstring line into the finding —
  window 7's draft therefore says what `flash.perceive` and `flash.route` actually
  are — pinned by a check that fails if the prompt stops grounding itself. House
  style (a section tag, column alignment) is still not matched, and no check
  claims it is. Not run: an unattended overnight window on this box — every
  window here was daytime and `--force`d, with the gate itself opened by a
  synthetic idle+AC profile state, so "on idle + AC" is enforced and tested, not
  observed in the wild.
- **R-7.4 (SHIPPED — offline vector MET)** A clone executes and reads at a path
  nobody predicted. **Nothing this repo runs, or tells a user to run, may carry
  the author's absolute directory.**
  It did not hold: three context corpora stored the author's checkout inside
  their ORACLE text, because a context task's test must put the fixture package on
  `sys.path` and the harness hoists whatever bootstrap the test carries. On another
  machine every one of those **72** tasks died with `ModuleNotFoundError` before
  the candidate's first line — a suite scoring nothing while looking like it
  scores something.
  Contract: the corpus writes `<REPO>`; `harness.REPO` is
  `Path(__file__).resolve().parent.parent` (resolved outward from the package, never
  recorded), and `harness._hoist_path_bootstrap` expands the token as the **one**
  place it is expanded — because that function is already the single choke point
  every execution seam uses to split a test into (bootstrap, body): `run_test`,
  `score`'s probe loop, `debug`'s tracer, `confidence`'s seeded re-runs.
  `python -I` implies `-E`, so `PYTHONPATH` cannot carry the fixtures instead.
  Vector: `benchmarks/portable_paths_check.py` **14/14 + 7/7 mutants**, launched
  from a foreign working directory (`benchmarks/fixtures`), and it drives the four
  seams rather than the helper: every reference solution in the three corpora
  passes in **all four**, the same four reject the 10 frozen wrong answers the 7B
  actually produced, and scoring the reference against the pre-expansion literal
  path still passes here, so the rewrite bought portability and not semantics. The
  mutant set is the point of the shape: expansion dead, expansion to a directory
  that is not this checkout, one host path back in a corpus, the token erased
  without a path replacing it (the vacuous-green form), the scan scope narrowed to
  the three touched files, a `.py` planted under the excluded prefix, and the
  residue doc stripped of a group name.
  Scope, honestly bounded: `benchmarks/results/**` is **not** rewritten. A record
  whose text was edited to look portable is no longer a record, and three kinds of
  them are load-bearing keys (`adapter_config.json` is read by `flash/train.py`;
  the embedding caches are keyed by the prompt text the fit consumed, so rewriting
  an entry silently changes what the cited pool audit was computed from). The
  exclusion is therefore checked, not assumed: no file under it may be code or
  instructions (0 of 164 are), and the residual paths must fall in one of five
  named groups with a documented floor — **RECORD_RESIDUE = 410** across 35 files
  (`traces` 241, top-level records 153, `adapters` 9, `p6` 6, `jobs` 1), printed by
  the run and asserted `≤` the live count so a new record directory fails until
  `docs/portability.md` explains it. The instrument's own exemption is a gate too:
  the only host-path literals in the scanned tree are the three markers
  `portable_paths_check.py` declares, which is what keeps "we do not scan our own
  constants" from becoming "we do not scan". The third marker is the default
  Homebrew prefix on Apple Silicon, and it was added **after** the first pass of
  this requirement shipped because the README's install block spelled an
  interpreter path with it — a line that was not a leaked home directory and was
  still a command a stranger could not run.
  **What is NOT claimed: that the repo is path-free.** It is not, and the residue
  above is the published count, not a rounding. Claiming the clone-and-run property
  itself — `pip install .` in a throwaway venv on a fresh clone — belongs to R-7.5 and
  not to this clause; R-7.5 closed it on 2026-09-28, which is why the sentence here no
  longer points at an open box.
- **R-7.5 (CLOSED 2026-09-28, both clauses met, second one met in the reading that
  is physically available and labelled as such)**
  `pip install .` on a FRESH CLONE, in a throwaway venv, MUST
  produce a working `flash` entry point, and the offline battery MUST be green
  against the installed package rather than the checkout.
  Vector: the run logged under `benchmarks/results/` with its printed counts, and
  `flash doctor` (R-7.6) as the one command a stranger types to prove it. This is
  the sentence a README may print as "works on your machine" only after it has been
  true on a machine that is not the author's.
  **Clause 1, measured 2026-09-28 by `python benchmarks/r75_fresh_install_check.py`,
  and re-measured on the day it closed because the first version of that driver did not
  test the install.** It builds the sdist, installs it into a throwaway venv, and then
  interrogates **the venv's `flash` console script from a directory holding no Python**,
  printing which `flash` each child resolved before asserting anything about its answer:
  **9/9 shapes** (`benchmarks/results/r75_clause1_20260928.log`, 0 host paths).
  A tarball install gives `flash --version` → `flash 0.0.1` at rc 0, `flash doctor` → rc
  **1** with `(installed copy)`, `verification surface beside the package: benchmarks/
  ABSENT` and `the offline battery CANNOT run from this install` naming its remedy, and
  `flash selftest --all` → rc **2** naming the `site-packages` battery path it wanted.
  A cloned, editable install gives an `import flash` that resolves to the clone,
  `flash doctor` → rc **0** with the battery line on `yes`, and
  `flash selftest --all --quick harness lsp power` → **3/3** vectors run from the clone,
  printing its own `run of 3/33 lines: totals are partial`.
  So the clause's "working entry point" is working in both shapes and the rc differs
  because the two shapes genuinely have different surfaces: a wheel carries `flash`
  alone, which is R-7.10's refusal-by-design, not a regression. The superseded sentence
  here — "`flash doctor` exits 0 against the installed copy" — was true of neither: it was
  the old driver's `-m` child answering from this checkout, because `python -m` puts the
  cwd on `sys.path[0]`. That shadowing is now the run's sixth gate, measured rather than
  explained: same interpreter, same install, cwd inside the unpacked sdist, and
  `doctor` reports `(editable checkout)`.
  **Clause 2 needed a reading before it could be measured, and the two readings
  are not the same claim.** Read literally — every child imports `flash` out of
  `site-packages`, with no source tree present — it is **unsatisfiable by design**:
  eleven of the battery's vectors index the tree they stand in (the graph selftest builds
  its index over its own source, `benchmarks/portable_paths_check.py` scans it,
  `benchmarks/documented_commands_check.py` reads its documents), and R-7.10 and
  R-7.10c made the rest refuse rather than pass quietly with no `benchmarks/`
  beside the package. A battery run that way would print a total from checks that
  could not have run, which is the failure this whole section exists to prevent.
  Read as the property the README sells — the battery is green in the tree a person
  gets when they download this project, with that tree's own package and a venv
  this checkout is not on — it **has been run**, and it is green:
  **Measured 2026-09-28 by `python benchmarks/r75_sdist_battery_check.py`.** That
  driver builds the sdist, unpacks it under a temp directory, installs the tarball
  into a throwaway venv, and then prints its own provenance before it is allowed to
  report a total: `import flash` from outside the tree resolves inside
  `site-packages`, `import flash` from inside the tree resolves to the unpacked
  sdist, the installed `flash --version` prints `flash 0.0.1`, and the checkout's
  path appears nowhere in the witness (the run asserts its own residue count: **0**
  host paths, which is also why `RECORD_RESIDUE` stays 411). Then it runs the
  **whole** battery there — **twice, on both install shapes the one tarball
  supports**, because the second language made the shape matter:
  `pip install <sdist>` printed **33 of 35** lines green, rc 1, `checks 1119
  oracle 20  §6 total 1139  mutants 85` in **13 min 22 s**, with exactly two
  refusal lines named (`benchmarks/ts_perception_check.py`,
  `benchmarks/ts_patch_check.py`, each printing its own `0/1` because
  `flash.lang_ts.available()` reports the grammar absent);
  `pip install '<sdist>[ts]'` printed **35/35**, rc 0, `checks 1210  oracle 20
  §6 total 1230  mutants 111` in **13 min 14 s** and the battery's own
  `matches SPEC §6 as written`. Raw witness:
  `benchmarks/results/r75_sdist_battery_shapes_20260928.log` (the single-shape run
  it supersedes, `r75_sdist_battery_20260928.log`, is left in the tree). **These are
  the 35-line tree's prints, dated 2026-09-28**: R-3.2's clause 3 added a 36th
  battery line and +40/+22 to the checkout side on 2026-09-29, and R-7.15's session
  vector added a 37th the same day (the checkout re-read printed `checks 1294  oracle
  20  §6 total 1314  mutants 147` on that tree; clause 7 then moved the session line to
  48/16, which is what §6 now prints), so the download's own
  two shapes are being re-run against the 37-line tree rather than carried forward by
  arithmetic — the prediction is 35 of 37 for a plain install and 37/37 for the `[ts]`
  one at whatever §6 total the driver prints, and that stays a prediction until the
  run prints it.
  **The driver's assertion is asymmetric on purpose**: shape A FAILs if either TS
  line *passes* there, or if shape A agrees with §6 at all, because a plain install
  reaching the page's headline total would mean the grammar check had stopped
  checking; shape B FAILs unless every line the battery declares is green and the §6
  line prints (the count is read from `battery_reread`, not typed in, which is why
  adding a line cannot leave the driver asking for the old number). One tarball, two commands,
  two different honest totals — and the earlier single-shape print of `1119 … 33/33`
  was true of the tree it ran in and misleading on the page, which is the same defect
  this clause corrected once already.
  **A machine-state limit found on the way**: the first shape-B pass printed **34/35**,
  failing only `benchmarks/checkpoint_resume_check.py`, because the power governor
  forces `tournament_width = 1` below 25% charge *even on AC*
  (`flash/power.py`) while that vector needs the tournament arm at width ≥ 2. That run
  is kept as `benchmarks/results/r75_sdist_battery_shapes_battgate_20260928.log`; the
  gate was not resized and the assertion was not reworded, the machine was put on AC
  above 30% and the driver re-run.
  **Both shapes then execute** the literal reading's answer and put it on the record:
  `flash selftest --all`
  with no tree present exits **2** naming the `site-packages/benchmarks/` path it
  wanted, which is R-7.10's refusal doing its job on the installed shape.
  **What this box corrected on the way.** A file named
  `battery_reread_r75_20260928.log` had been committed an hour earlier under the
  message "R-7.5 fresh clone witness: … all 33 lines green". It is not one: it is
  byte-identical to `battery_reread_r710c_20260927.log`, the checkout's own re-read,
  except for a trailing `battery rc=0`, and the driver it was attributed to cannot
  produce 33 lines — it runs `--quick harness lsp power`, three of them. So the
  counts it carries are true and its label was not, which is the same defect as a
  fabricated number wearing a real one's clothes. It is left in the tree (deleting
  a committed artifact is not a correction), relabelled in
  `docs/portability.md`, and the run above replaces it.
- **R-7.6 (CLOSED 2026-09-27)** One command must answer "is this install sane, and
  how do I verify the claims I just read?" `flash --version`, `flash doctor`
  (python, platform, GPU/backend presence, model cache, config, whether the offline
  vectors can run here) and `flash selftest --all`, which runs the §6 battery from
  the installed package and prints its totals the same way `battery_reread.py` does.
  Vector: `benchmarks/backend_free_check.py`, **30 checks + 5 mutants** when it
  closed, and a §6 line of its own; the file now carries **37 checks + 8 mutants**,
  the six extra checks and two extra mutants being R-7.10's and R-7.10b's guards
  living in the same place as the report they test. What each command commits to, gate by gate:
  `--version` prints `flash <flash.__version__>` read from the package object, so a
  bump cannot leave a stale number in a parser; `doctor` answers nine questions and
  its exit code follows its own page — the vector builds a synthetic broken install
  in a temp directory and requires `no` on exactly 5 of 9 lines plus rc 1, and a
  synthetic complete one zero `no` marks plus rc 0; `selftest --all` is a thin
  wrapper that refuses with rc 2 when `benchmarks/battery_reread.py` is not beside
  the package, which is the wheel case R-7.5 found in `flash.graph --selftest` and
  the difference between "nothing to verify here" and a total printed from checks
  that never ran. `--backend-free` is not a filter either: the battery installs an
  import blocker in every child and proves it bites — a child's `import mlx.core`
  must raise the shim's own sentence — before it prints a fraction, and a sibling
  gate requires the same shim to leave `numpy` alone.
  The box's word "config" is the one thing it did not get: `flash` reads no config
  file, so the report prints the single path a user's environment actually changes
  (`HF_HOME`/`HUB_HOME`, resolved, with the §22 tier counts beside it) rather than a
  section about a file nobody opens.
  The "21 of the 23 `flash` subcommands the docs name exist" measurement this box
  carried is superseded by R-7.8's collector, which reads the documents rather than
  a list: **25 distinct commands across 150 citations in 15 documents, all of which
  resolve**. That citation count is the spine gate's own printed line, so it moves
  whenever a document gains or loses a `flash …` line — 111 at this box's first green
  print, 112 after the README front-page rewrite, 130 once the packaging files and
  `docs/config.md` joined the scan — which is why it is re-read from the run rather
  than
  copied forward.
- **R-7.7 (CLOSED 2026-09-27)** A non-Apple-Silicon user MUST be able to `import
  flash` and every submodule of it, and hear about the missing backend at the call
  that needs generation — not at import time. `flash/decide.py`'s `import mlx.core`
  now sits inside `decide()`, one statement after the 2..26-options check, and its
  `except ImportError` re-raises a `RuntimeError` that names MLX, names the remedy,
  and lists the things in the package that still work.
  Measured under `benchmarks/backend_free_check.py`'s import blocker, in child
  processes so the shim is doing the work rather than this file's own `sys.modules`:
  **27 of the 27 submodules import** as re-measured 2026-09-28, up from the 26 this
  clause was written against because `flash/lang_ts.py` joined the package
  (the gate prints the sweep's own `SWEEP 27/27`
  fraction, requires numerator == denominator and the denominator ≥ 26, so a module
  quietly dropping out of the package cannot read as a smaller victory),
  `import flash.decide` and
  `import flash.route` each succeed, and `decide(None, None, 'ctx', ['a','b'])`
  prints `RUNTIMEERROR:…MLX…` instead of a `ModuleNotFoundError`, a tokenizer crash
  or an empty verdict. Both halves are gated because "swallow the ImportError" would
  satisfy the import clause and destroy the second one: a generator that quietly
  returns nothing lets a suite print verdicts scored from nothing.
  The sweep is mutation-checked rather than trusted: the mutant writes a real
  throwaway submodule — `_zz_backend_probe.py`, planted in the `flash` package
  directory for the length of the run and deleted after it, so no document here can
  tell a reader to open a file that only exists mid-mutation — carrying
  `import mlx.core` at top level while the
  gates run. Onto disk rather than into a stubbed global, because the claim is about
  what a child process can import, not about what this process has already cached.
  Exactly the sweep gate fails, then the
  file is deleted and the tree verified clean.
  The pre-fix measurement stays on the record because it is why the box existed: with
  `mlx` blocked, **24 of the 26** imported and `decide`/`route` did not, unnoticed
  for as long as the package shipped because `flash.cli` loads both lazily.
  The same number then went stale a second time, in the one place no gate reads:
  `.github/workflows/ci.yml`'s header annotated the static tier with a count — 25, then
  corrected to 24 — and kept describing `flash.decide` and `flash.route` as the two that
  cannot import for the whole of this clause's life, in the file whose very step runs the
  vector that prints `SWEEP 26/26`. Nothing caught it because a workflow comment is prose
  and `documented_commands_check.py` reads its `run:` lines, not its English. The header
  now cites the vector and carries no count, which is the same reason the landing page
  reads generated JSON: a figure a program neither measures nor can check is not a claim
  but a rumour, and R-7.8 exists precisely because typed command lines went wrong here.
- **R-7.8 (CLOSED 2026-09-27)** Every `flash …` command line printed in a tracked
  document MUST parse against `flash.cli.build_parser()`.
  Vector: `benchmarks/documented_commands_check.py`, **8 checks + 5 mutants**, green
  in this session and a §6 line of its own. It reads fenced blocks, inline spans, the
  two published workflows' `run:` lines **and the comments of the two packaging
  files**, and today the collector's own spine is one of the gates: **26 distinct
  commands in 200 citations across 15 documents**, plus **87 source paths** a reader
  is told to open. (Those four figures move with the prose — the citations printed 111
  when this box first closed, 112 after the README front-page rewrite, 130 once the
  packaging files were scanned, 150 when `docs/config.md`, `docs/privacy.md` and
  `docs/methodology.md` joined the scanned set, 185 after R-3.2's clause 3 put
  `--apply` in the pages a stranger copies, 196 when `flash session` arrived, 197
  when this release's own CHANGELOG entry was written, 199 when clause 7 put a
  `printf … | flash session …` pipe in `docs/config.md`, and 200 with 87 paths on
  R-7.15e's pass — a changelog is a scanned
  document, which is exactly why the spine gate asserts a
  floor and prints what it found rather than pinning a literal.) Commands are
  resolved by
  `parse_args([cmd])` against the real parser under a swallowed stderr — never by
  dispatching, because a documented `flash run` would create a worktree and a
  documented `flash selftest --all` would take a measured quarter of an hour (the §6
  re-read of the same vectors took **15 min 9 s** on 2026-09-27) — and the gate that
  the
  collector is not blind is paid for by planting a command nobody wrote and requiring
  the existence check to reject it.
  **The packaging files were added the same day, and they were not idle for a
  minute.** `pyproject.toml`'s `vlm` extra carried the comment "flash vision --run
  reads screenshots through a VLM", and there has never been a **vision**
  subcommand — the suite is `flash run-vis-suite`. That file is the one a stranger
  opens to decide what to install, its prose is invisible to every markdown-only
  scan, and the command appears without backticks, which is the shape the collector
  had no rule for. So `code_lines()` grew a third source (a line whose comment begins
  with `flash …`, in `.toml`/`.in`), the 8th gate requires both packaging files to be
  in the scanned set *and* to yield commands, and the 5th mutant removes them from
  that set to prove the gate is watching. The instrument earned its keep twice more
  inside the hour: `docs/config.md`, written minutes later against the same
  `pyproject.toml`, inherited the same non-existent vision `--run` form and added a
  **sandbox** command that is also not a command — two gates failed on a page that did
  not exist an hour before, which is the only kind of evidence that a doc scan is
  worth its runtime.
  **This paragraph is written the way it is because the gate cannot tell a citation
  from a report about a citation.** A backticked `flash …` span means "run this" to a
  reader and to the collector alike, so the two dead subcommands are named here as
  bare words; the strings as they shipped are in the quoted comment above, which is a
  surface the collector deliberately does not read (it reads fenced blocks, inline
  backticks, workflow `run:` lines and packaging comments — the four shapes a reader
  copies), and in the commit that removed them from `pyproject.toml`.
  Two of the seven are the reason the box existed. `CONTRIBUTING.md` shipped three
  citations of commands that did not exist, one inside the copy-pasteable setup
  block; the fix is not only that they now exist, it is that the **published
  workflows** are on the same gate — 5 `flash` commands across `ci.yml` and
  `nightly.yml`, all of which resolve — so the pipeline a stranger sees on the front
  page cannot go back to being dangling. The ellipsis rule is honoured: `--tasks …`
  reads as an argument, not a subcommand, so a documented example with a placeholder
  cannot fail the gate for the wrong reason.
  SPEC's mutation-check is the last mutant and it is literal: `selftest` and `doctor`
  are deleted from `build_parser()`'s subchoices while the gates run, and the failure
  must not merely happen — it must **name the citing file and line**, so the gate
  demands a matching `\.(md|yml):\d+` in the failure text before it counts as caught.
  It earned its keep the day it landed: the write-up of R-7.7's own mutant named the
  planted throwaway submodule by its path in a code span, and this gate failed the run
  — one collected source path missing, and it was that one — a document telling a
  reader to open a file that exists only while a mutation is applied being exactly the
  defect the gate was written for. The sentence moved, not the gate; that is why this
  box describes the planted file without naming its path.
- **R-7.9 (CLOSED 2026-09-27)** A flag that says "write nothing" MUST write nothing,
  whichever sub-command it is combined with.
  `python -m flash.train --dry-run` has advertised "mine and print the counts, write
  nothing" since the day it was added, and `main()` honoured it for `--dataset` while
  dispatching `--suite-from-dataset` **above** that branch with no `dry` argument at
  all. So the command a user runs to avoid touching the tree wrote
  `benchmarks/tasks/r64_train_from_dataset.jsonl` — a tracked file, in the shipped
  suite directory, on the machine that generated the dataset R-6.4 is scored against.
  It was caught here by nothing more clever than `git status` after a verification run:
  the file was modified, and the command that modified it had been typed as a dry run.
  `suite_from_dataset()` now takes `dry`, skips the `mkdir` with the `write_text`
  (creating the directory is also a write), and prints `-> would write <path>` so the
  refusal is visible rather than silent.
  Vector: two checks and one mutant inside `benchmarks/lora_path_check.py`'s suite
  group — **`lora path: 33/33 checks passed`, `mutations: 15/15 gates defeated by
  exactly their checks`**. The seam is `flash.train.main()`, not the helper, because
  the defect was in `main()`'s plumbing; `TASK_DIR` is aimed at a temp directory
  holding copies of the real suites (the function reads its rows from the folder it
  writes into), so a broken flag cannot cost a tracked file even while the mutant is
  live, and the gate is exercised with **no `--suite-out`**, which is the only way the
  default name — the one that lands in `benchmarks/tasks/` — can be proven untouched.
  The second check runs the identical call **without** the flag and requires the file
  to appear, because "nothing was written" is also what a command that never ran looks
  like. That coupling is why deleting the dispatch now fails three checks where it used
  to fail one, and the expectation says so rather than hiding it.
- **R-7.10 (CLOSED 2026-09-27)** A vector that cannot run on this install MUST say
  so and stop, on every install shape, rather than raise or print a count of failures
  that are really a missing directory.
  This is R-7.5's wheel finding, finished. `flash.graph --selftest` already refused
  with exit 2 and a sentence; the other three that read beside the package did not.
  Measured on a wheel installed in a clean venv (witness
  `benchmarks/results/r75_install_shapes_20260927.log`, phase B): `flash.grammar` and
  `flash.debug` raised `FileNotFoundError` for
  `<site-packages>/benchmarks/tasks/*.jsonl`, and `flash.patches` printed
  `FAIL workspace: a project directory loads under its relative path` inside an
  otherwise-green 46-check report — a stranger reads the first two as a broken
  package and the third as a broken patch resolver.
  The refusal is now one function, `doctor.vector_refusal(mod, root)`, driven by a
  four-entry `doctor.VECTOR_DATA` table (grammar/debug → their task corpora,
  patches/graph → the fixtures package), and all four `run_selftest`s call it before
  touching the filesystem. Exit 2, not 1: a total printed from checks that could not
  run is the failure mode this whole group is about.
  Vector: six checks and two mutants inside `benchmarks/backend_free_check.py`
  (**`backend-free checks: 37/37 passed`, `backend-free mutants: 8/8`**), plus a live
  repeat in a package-only tree (`flash/` copied next to no `benchmarks/`, run from a
  directory that holds neither): four for the refusals — each module's `_DATA_ROOT`
  aimed at an empty directory, `run_selftest` called at the real seam, rc 2, the
  sentence naming the exact missing path, and **no `Traceback` in the text** — one
  that points the same four guards at a tree that DOES carry the four paths and
  requires them to say nothing (otherwise the refusal is a constant, and a vector
  that can never run is not "honest", it is deleted), and one on the table's shape:
  every entry must sit under `benchmarks/`, because a package-side path would make
  the guard answer "present" forever. Mutants: a guard that reports "missing" even
  when the data is there (fails the second), and a table entry moved inside `flash/`
  (fails the third). The same run found a second wheel-only lie, booked as R-7.10b.
- **R-7.10b (CLOSED 2026-09-27)** `flash doctor` told a `pip install .` user they were
  in an **editable checkout**. The line came from
  `Path(sys.executable).resolve().parent.parent / "lib"` being compared to the
  package's own path, and a macOS venv's `bin/python` is a symlink into the Homebrew
  framework — so the prefix it built was that framework's own `lib`, which is not a
  prefix of the throwaway venv's `site-packages/flash` directory, and the report said
  the opposite of the truth about the one thing this release is about. `_is_editable`
  now reads the property off the path it is a property of: a package under
  `site-packages`/`dist-packages` is an installed copy, anything else is a working
  tree. Vector: the 37th check of the same file, against a synthetic
  `lib/python3.11/site-packages/flash` directory, plus the 8th mutant putting the
  interpreter back and requiring the gate to notice; both green in this session, and
  the wheel venv re-printing `installed copy` is in R-7.5's rerun witness.
- **R-7.10c (CLOSED 2026-09-27)** The refusal table was a list of four, and a list
  is only as good as the measurement that says it is complete. Re-running R-7.5's
  wheel shape found two more modules that read the data tree and were not on it:
  `python -m flash.tourney --selftest` exited **1** with `FileNotFoundError` because
  its one real h-task was named by a path resolved against the **caller's cwd** (so it
  was broken in a clone too, for anyone who ran it from another directory), and
  `python -m flash.lsp --selftest` exited **1** with `ValueError: substring not
  found` — three layers of AST work away from the absent fixture, naming no path at
  all, which is the error text a "look for a missing file" classifier would miss.
  Both now go through `doctor.vector_refusal` like the other four (the table is six,
  and tourney's task path is anchored at `_DATA_ROOT`), and the clause's verification
  stopped being a list: `benchmarks/backend_free_check.py` copies `flash/` into a temp
  directory that carries **no** `benchmarks/`, discovers every module in the copy with
  a `run_selftest` **from the copy's own sources**, runs each one as a stranger would,
  and classifies every death by re-running it in a second copy with the data
  symlinked back in. Passes only when the data is there ⇒ data-dependent ⇒ must be
  tabled. Fails either way ⇒ this box's state, and it is on a three-entry named list
  (`ambient`'s wall-clock budget, `power`'s governor read, `sandbox`'s memory rlimit)
  rather than skipped — the checked-exemption shape R-7.4's residue floor uses. A
  non-tabled failure is re-run in the bare copy first, because a data-dependence is
  deterministic and a timing budget is not: on the run that closed this box
  **16 modules were swept, 6 refused, and the refusals equalled the table's keys**.
  The spine gate also requires every swept module to carry the `__main__` tail that
  makes `python -m` reach it — a hole the first version of this gate had, and found by
  its own mutant: the planted probe initially had no `__main__`, so `python -m` on it
  returned 0, the sweep called that a pass, and the mutation printed **0 checks
  failing**. Vector: **42 checks + 10 mutants** (`backend-free checks: 42/42 passed`,
  `10/10 gates defeated by exactly their checks`), the new mutant writing a real
  data-reading, unguarded, selftest-exposing module into `flash/` and requiring the
  sweep to name it. Cost of the sweep is stated rather than hidden: this vector went
  from seconds to ~2 min and the §6 battery from ~8 min to a measured **15 min 9 s**.
- **R-7.11 (CLOSED 2026-09-28)** A public page may print only a number a run printed.
  `site/` is the project's landing page, and its entire numeric surface comes from
  `site/src/data/{benchmarks,graph,transcripts}.json`, written by
  `python benchmarks/export_site_data.py` from `benchmarks/results/dashboard_data.json`,
  written by `python benchmarks/dashboard_data.py` — which parses the committed §6
  witness for the 33 printed fractions and times `python -m flash.<mod> --selftest` at
  **n=3** per module, publishing the median with its min and max beside it. A figure
  that is not in that JSON cannot render, and no figure is typed into the page by hand.
  Two clauses the vector enforces rather than describes. **(1) Provenance is on the
  page:** every panel names the command that prints its number, the 33 commands in the
  table are generated from `battery_reread.BATTERY` itself (so a panel cannot claim a
  vector the battery does not run), and every captured transcript is redacted before it
  is stored — `<checkout>` for the repo root, `~` for the home directory — with the
  capture printing the substitutions it carries. **(2) The page may not claim a
  comparison it did not run:** no cost table, and no bar for a tool that was not run
  against these weights on this machine. That clause exists because the first version
  of this surface violated it: commit `54a2117` published four dashboard PNGs with
  invented rival latencies and a fabricated dollar column under a message calling them
  benchmarks, reverted as `69a4d2d`. The page states the breach in its own amber panel
  rather than only in the history, on the grounds that a reader who cannot see the
  failure cannot see the rule. That panel's heading — **Panel 3.5 does not exist**,
  naming the bar that had been fabricated — was retired by **R-7.12**, which ran one
  competitor on this machine: panel 3.5 is now a measured table, and the amber box
  names only what is still refused.
  Vector: `python benchmarks/dashboard_data.py --repeats 3 && python
  benchmarks/export_site_data.py`, then the render check below. The exporter's own
  cross-check against `BATTERY` is what caught `build_ms` reading the cold-index line's
  **edge count** (10,058) and publishing it as an index build time; the fix was a
  re-measure to **603 ms**, not a retype.
- **R-7.11b (CLOSED 2026-09-28)** The page must render on a machine that cannot give it
  a GPU context. three.js throws rather than degrading when no WebGL context can be
  made, an uncaught error in any child unmounts the whole React tree, and the result was
  a **blank page**: a 33 KB DOM with an empty `#root`, witnessed by the browser's own
  console line `Uncaught Error: THREE.WebGLRenderer: Error creating WebGL context.` The
  hero now asks three questions before mounting the canvas — is there a context at all,
  is it a *software* one (SwiftShader renders this scene at a few frames a minute, which
  is worse than the alternative), and has the reader asked for reduced motion — and on
  any "no" it draws the same 150 symbols as an interactive SVG instead, so the graph,
  the click-to-blast-radius readout and every word survive, and the 914 KB three.js
  chunk is never downloaded. An error boundary sits behind the probe for the case the
  probe cannot see: a context lost after mount, to a driver reset or a sleep.
  Vector: the render is checked by driving a **real scroll** and reading the DOM at
  1440×900 and 390×844 — `chrome --screenshot` is not an instrument here, because it
  captures one viewport inside a virtual-time budget and an `IntersectionObserver`
  entrance paints blank, which is indistinguishable from broken. With the probe in place
  the rendered DOM is **173 KB** with all eight sections present; `npx tsc -b` silent and
  `npm run build` clean with the WebGL and recharts chunks deferred.
- **R-7.12 (CLOSED 2026-09-28)** A rival's number may enter a chart only by being run
  against these weights on this machine. `python benchmarks/market_compare.py --arms
  oneshot,aider,flash --tasks benchmarks/tasks/m7_heldout_tasks.jsonl` produces the
  project's first cross-tool table. **One variable at a time is the whole design:** all
  three arms answer the same 8 held-out tasks, the same
  `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` weights are behind every row, and every
  answer is graded by `flash.harness.run_test` — the same oracle that printed the
  published **20** — so the grader is not a fourth thing being compared. Three gates run
  before an arm is allowed to score: the grader self-checks against its own suite (**8/8
  stored reference solutions pass**), the server is asked `/v1/models` and must name the
  model the driver thinks it is talking to, and a **proxy that counts tokens by reading
  the server's own `usage` field** sits between them, so the token column is counted at
  the seam instead of re-tokenised by the thing being measured; a row with 0 proxied
  requests is refused, not printed.
  Measured, same machine, no API key (wall clock 4 min 38 s, quoted from the redirect
  file's own birth and last-write timestamps because the witness is a shell redirect and
  says nothing about itself): **single-shot, no agent loop — 5/8 (62%), 6.9 s/task, 8
  requests, 2,021 tokens**; **aider 0.86.2 — 6/8 (75%), 14.9 s/task, 16 requests, 13,084
  tokens**; **flash run-suite — 8/8 (100%), 10.6 s/task, 11 requests, 3,470 tokens**,
  from trace session `20260928-081056-run-suite-b660` (72.97 s generating + 11.8 s
  verifying of 84.8 s task time). Both aider's failures (`text_wrap`, `fraction_add`) are
  two of the three the bare one-shot also failed, which is what says the loop is doing
  work rather than the prompt having been written differently. `--allow-big never` is
  load-bearing: the loop's escalation tier is a second, larger model, and letting it
  answer would have made the table a comparison of weights rather than of agents. The
  arms are not the same shape of tool and the page does not pretend otherwise — aider is
  a repository-editing agent given one file to write (`--edit-format whole`, a fresh
  `git init` per task so it cannot see its neighbour), and a large part of its token
  column is its repo map and chat scaffolding, which is the honest reading of why it
  costs more per task rather than a gotcha. That row is labelled `aider aider` in the
  witness because the arm's display name sat beside the binary's basename; the driver
  prints the single word now, and the committed print is left exactly as the run made it.
  Two instrument bugs this driver found in itself, both the class the release has been
  correcting: **`--tasks` was accepted and ignored**, because the HTTP arms called
  `m0_bakeoff.load_tasks()`, which always reads the frozen m0 suite — so a run would
  have printed held-out totals under a suite the reader never asked for; the fix is a
  `read_tasks(path)` the arms must use, plus a printed `suite: … ids h41…h48` line, and
  it was caught because the "held-out" run reproduced m0's exact numbers. Second, **the
  flash arm parsed PASS/FAIL out of `run-suite`'s stdout** and printed **0/8** while that
  same run's trace recorded **8/8 solved**; the arm now reads `trace.summarize` and the
  stdout parsing is gone. A third, not a measurement bug but a live one: one aider
  process per task calls `webbrowser.open` for release notes it has not shown before, so
  the arm opened a browser tab every 6–19 s on the user's desktop until `BROWSER=/usr/bin/true`
  went into the arm's env. The witness write path substitutes this machine's markers
  before it saves — the checkout, the home directory and the Homebrew prefix become
  `<checkout>`, `<home>` and `<homebrew>`, longest marker first because the checkout path
  *starts with* the home directory — and prints the count it made, because the aider arm's
  failure detail is another process's log and it will quote whatever it was launched with.
  That path went in after this run, so the committed print carries no provenance line; it
  holds **0** host markers, which is what `benchmarks/portable_paths_check.py` measures
  rather than what this sentence asserts.
  Vector: `python benchmarks/dashboard_data.py`, which refuses
  to write the site JSON when the witness has no parsable arm rows, and
  `python benchmarks/export_site_data.py` after it — panel 3.5 renders from those rows
  and nothing else.
- **R-7.13 (OPEN)** No bar on this page for Cursor or Copilot, and none is obtainable
  here. `cursor-agent` and the standalone Copilot CLI are not installed on this machine,
  `gh extension list` is empty, and the two products that *are* present — `Cursor.app`,
  which ships only the IDE launchers `cursor` and `cursor-tunnel`, and the
  `github.copilot-chat` VS Code extension — have no headless entry point at all. Both
  generate in their own cloud under a paid plan, so running either would replace the one
  controlled variable of R-7.12's table (same weights, one machine, one oracle) with a
  different model, different hardware and vendor-side token accounting, and the result
  would measure which vendor has the larger model rather than which loop is cheaper.
  Vector: a cloud arm behind the same proxy and the same harness oracle, on the same
  8-task held-out suite, labelled in the table as a different-weights comparison.
  *(Needs a paid account and a machine that is not this one — SPEC §9 register. Offered
  and declined by the author on 2026-09-28: a cloud row bills his own subscription and
  changes the model, the hardware and the token accounting in one move, so it would not
  be the comparison the table above is. Adding it later does not make it like-for-like;
  the label is the requirement.)*
- **R-7.14 (CLOSED 2026-09-28)** Nothing that could be used against the author may
  leave this repository, and the claim has to be re-runnable rather than remembered.
  A download-and-run project publishes more than source: it publishes the traces of its
  own development and the whole git history, so "grep the tree for a key today" answers
  the wrong question in two ways — a secret scrubbed from HEAD still ships in
  `git log -p`, and a file written since the last commit is what a push actually sends.
  Vector: `python benchmarks/publish_secret_scan.py`, which swept **359 files in the
  working tree (4 of them not yet tracked) and 713 blobs ever committed** against
  **7 credential families plus filename shapes**, printed **2 matches, both accepted by
  name with their reason beside them and 0 unlisted**, and exited with
  `OK  no unlisted credential shape in the 359 files a push sends or in any of the 713
  blobs ever committed, and 8/8 families proved they can bite.` The 21 blobs carrying NUL
  padding (the committed `.npz` arrays) were **not skipped**: their padding was removed
  and the bytes swept anyway, because archive padding is where a planted secret would
  sit. Identity is reported rather than gated: `github-noreply-email` **7**,
  `personal-email` **0**, `host-path` **1117** occurrences across the tree and history.
  Witness `benchmarks/results/publish_secret_scan_20260928.log`. The file and blob
  counts belong to that run, not to the repository forever: the history arm counts every
  blob that exists at the moment of the sweep, so re-running the same command after this
  entry was committed printed **735 blobs and 0 files not yet tracked**. A larger blob
  count on re-read is the history growing, which is what it measures; a *smaller* one
  would mean someone rewrote it.
  Three things this pass produced that are worth more than the green line, each kept as
  a mutant-shaped fact. **(1) A scan that cannot match anything is green forever**, so
  `plant()` puts a synthetic secret through every family and fails the run if any family
  passes it — 8/8, and separately proved end-to-end by planting a fake `ghp_` token and a
  `deploy.pem` filename in the working tree, which the sweep named as `UNLISTED` and
  exited 1 on before both were deleted. **(2) The scanner caught itself twice.** Its own
  `private_key` test sample was a whole PEM header, so the new file tripped its own rule
  on the day it was written; the sample is now written in two pieces rather than adding
  an allowlist entry that lets this file exempt itself, which is the one exemption that
  makes a secret gate meaningless. Then its own **witness** tripped it, because the
  printed reason quoted the `KEY="value"` shape it was excusing — the reasons are now
  worded without that shape and a second run over the log it had just written is clean.
  **(3) An existing gate caught the new one**: `benchmarks/portable_paths_check.py` fell
  to 13/15 when this file spelled a host prefix literally in its identity pattern, which
  is the exact rule R-7.5's marker-list finding established — only the file declaring
  the markers may carry them. The pattern is now assembled from `HOST_PATHS` imported
  from that gate, and
  the portability line is back to 15/15 with 7/7 mutants defeated.
  What is NOT claimed: this is a pattern sweep, not a proof of absence — it finds the
  shapes credentials have and every match it excuses is listed with a reason a reader can
  refute. It sweeps what a push sends, so a gitignored file is out of scope unless it is
  force-added. It is **not** a §6 battery line, for the same reason as
  `benchmarks/market_compare.py`: its history arm needs `.git`, so an unpacked sdist or a
  ZIP download cannot run it, and a count that only one tree shape can print would move
  the totals without meaning them. It is cited here and in `docs/privacy.md`, and the §6
  totals are unchanged by it.
- **R-7.15 (CLOSED 2026-09-29)** A developer can hold a conversation with the agent
  about one code base, and **every turn ends in an oracle verdict rather than a
  paragraph.** The gap was reported by the author's own first use of the shipped tool:
  after R-3.2's clause 3 put a verified edit on disk, `flash run` still answered exactly
  one task per process, so coding with it meant re-typing the same `--test … --context …
  --edit --apply` for every change and re-reading the project each time. The requirement
  is therefore a session, and a session MUST:
  **(1)** keep one session per `(context, oracle)` pair and read turns from **stdin**, one
  prompt per line, so a pipe or a here-doc drives it exactly as a keyboard does (I-4, and
  it is what lets the vector need no terminal); `quit`, `exit` and `q` end it, and
  `--turns N` bounds it;
  **(2)** re-read the workspace from **disk** at the start of every turn and never reuse
  the bytes an earlier turn held — a session that edits against a stale in-memory copy
  hands `land` a `before` that is missing its own previous turn, and `land` writes the
  whole file it was given, so the revert would print `solved=True`;
  **(3)** end every turn with a printed verdict naming the turn, the tier, `solved=`, the
  attempt count and the patch arm's own audit counts, plus the oracle's words under
  `oracle|` when the turn failed, and reuse `cli._land_edits` verbatim so `wrote <file>
  (+a -b lines)` / `NOT APPLIED` / `REFUSED: …` cannot mean one thing on `run` and another
  here;
  **(4)** keep the oracle protected on every turn, not only on the first patch set;
  **(5)** print one report at EOF — `turns`, `solved`, `written`, `seconds`, `last_rc` —
  and exit with the **LAST** turn's code, counting `written` by re-reading the tree rather
  than by believing the write-back;
  **(6)** refuse with rc 2, before generating, when the oracle cannot be read, is blank,
  or when stdin holds no turn: a session with no verify step is a chat, and a chat that
  prints confident answers is what this project is not;
  **(7)** **run turn N as soon as line N arrives.** The first shipped session collected
  stdin into a `list[str]` and then looped over it, so it drained to EOF before generating
  anything: on a keyboard that is a blinking cursor and no answer until Ctrl-D, which is a
  batch file with a prompt painted on it. Turns are therefore a generator that yields one
  request per line, and on a terminal the session prints `you> ` immediately before each
  read and a one-line banner before the first one — a person who cannot see that the
  program is waiting reads it as a hung one. On a pipe no marker is printed at all, because
  a session's stdout is also a report and captured text must stay parseable.
  There is deliberately **no `--edit` flag** on `flash session`: the patch arm is the only
  answer shape a session can land, so a flag that could turn it off would only let a turn
  be scored and then silently not applied. The parser rejects `--edit`, and a check names
  that rejection — an argument that does nothing is worse than one that does not exist.
  The session does **not** hold the weights: `loop.load_model` is uncached, and the five
  `del model, tok` + `mx.clear_cache()` sites counted inside `solve_routed`'s own source
  exist to free the small tier before the 30B brain loads, so a module-level cache would
  keep 7B resident under a 30B on exactly the machine that has the least room (I-3). The
  claim was measured rather than assumed: three sequential processes loading
  `Qwen2.5-Coder-7B-Instruct-4bit` on this box (raw witness
  `benchmarks/results/session_model_residency_20260929.log`) print a first `load_model` of
  **2.01 / 2.10 / 2.05 s** and a second one after `del model, tok` + `mx.clear_cache()` of
  **0.65 / 0.74 / 0.66 s** — the hot swap is the cheaper of the two, and what a session
  saves is the workspace, the oracle path and the user's intent already being there, not
  the weights.
  Vector (offline): `python benchmarks/session_check.py --sweep` — **58/58 checks, 20/20
  mutants defeated in both lanes** (this process, and one fresh process per mutant, which
  is the lane that matters because a session owns process state: `loop.EDIT`, the trace
  store, stdin). Witness: `benchmarks/results/session_sweep_r715g_20260929.log`; the row was
  `48/48 (+ 16)` when this box shipped and `49/49 (+ 17)` after R-7.15e added the check that
  **every turn is handed the oracle's workspace key** and the mutant that withholds it,
  `54/54 (+ 18)` at R-7.15f, and `58/58 (+ 20)` when R-7.15g added the
  checks that **VERIFY grades the patched copy**, not the copy on disk before the turn. The router
  is stubbed and one scripted patch is supplied per turn, so what is under test is the loop
  around the write-back: the disk state after turn N, the `before` handed to turn N+1, the
  per-turn verdict, the mid-session refusal of a patch that rewrites `--test`, the report,
  the exit code, and that `run` did not change when the session arrived. Four of the checks
  belong to clause 7 alone: the read/solve log must interleave (`read1, solve1, read2,
  solve2`) rather than drain; `--turns 1` must stop **reading** stdin, not merely stop
  counting; a terminal gets its marker before its first request; a pipe gets none. The
  mutants are copies with one clause switched, and `agree()` first proves the clean copy is
  indistinguishable from the shipped command on seven scenarios (two of them claiming a
  tty, because a prompt that only exists on a terminal is otherwise invisible to the
  harness) — otherwise a check would fail for drift, not for the bug it names.
  Live arm (a real 7B, three turns, `--apply` on a scratch tree whose `f_to_c` multiplied
  by `9/5` instead of `5/9`): `solved=True attempts=2 (6.2s) patches=1` then
  `[R-3.2] wrote temp.py (+1 -1 lines)`; turn 2 asked for a **new** function and printed
  `PATCH REFUSED: temp.py:k_to_c — no symbol 'k_to_c' in temp.py (it defines:
  boiling_point, c_to_f, f_to_c, freezing_point)` with `attempts=4 (11.5s)` and
  `NOT APPLIED`; turn 3 landed a docstring `(+2 -1 lines)`;
  `[session] turns=3 solved=2 written=2 seconds=25.6 last_rc=0`, replayable as
  `flash trace show 20260929-131202-session-e176`. The oracle was byte-for-byte the file
  the session started with (`1251ecda…`, re-hashed after the run against the text written
  before turn 1) and no turn printed a landing line naming it. Raw transcript:
  `benchmarks/results/session_live_20260929.log`, with the trace file beside the other
  committed traces.
  **Clause 7's own arm is a pseudo-terminal, not a pipe** — `pty.openpty()` + `select`,
  writing line N+1 only after line N's verdict reached the terminal, every chunk
  timestamped, the scratch tree seeded by the driver so the arm is re-runnable
  (`python benchmarks/session_pty_demo.py`, witness
  `benchmarks/results/session_pty_20260929.log`). The banner arrives at
  t+0.06 s and the first `you> ` at t+2.15 s — before any model work — turn 1's verdict
  prints at **t+19.22 s**, and the second request is typed at **t+19.43 s**, **after**
  the first answer, which is the whole claim. That run is also the honest one: it is on
  the author's own demo task and **both turns lost**. Turn 1 printed `patches=1 refused=1` with
  `PATCH REFUSED: money.py:format_dollar — no symbol 'format_dollar' in money.py (it
  defines: cents_to_str)` (R-7.15b, below, now with a measured victim); turn 2 printed
  `FAILING_ASSERT: assert cents_to_str(150) == "$1.50" | GOT: '$1.5' | WANT: '$1.50'`.
  `tier=failed` on both turns is not the small model giving up: `loop.solve_routed` prints
  that string only on the path where the big tier ran and also failed, and the trace
  confirms it — two `escalation` events, `denied=false`, `tier="big"`, `reason="small
  failed after 2 attempt(s)"` (replay: `flash trace show 20260929-144507-session-54e4`).
  The session closed `turns=2 solved=0 written=0 seconds=23.1 last_rc=1` and left the file
  untouched, because `[R-3.2] NOT APPLIED` is what an unsolved turn means. The arm was
  run twice in this pass and turn 1's refusal reproduced — same symbol, same count, the
  two runs differing only in which assert turn 2 trips first. So this requirement bought
  the chat shape, and the accuracy half of the promise is still open on this task — which
  is why R-7.15b is not reworded into a pass.
  **The ledger moved with it, from the print.** `python benchmarks/battery_reread.py` on
  this tree printed `checks 1294  oracle 20  §6 total 1314  mutants 147` with **37** OK
  lines and no BAD line, and its own `matches SPEC §6 as written: 1294 + 20 = 1314 green,
  offline (+ 147 mutants)`, in **16 min 44 s** on AC with the charge climbing from 63% to
  80% (`benchmarks/results/battery_reread_r715_20260929.log`) — against a `CLAIM` of
  `checks 1294  oracle 20  mutants 147` summed from `BATTERY` before the run started. The
  session line is the **30th** of 37, because the ledger follows §6's own table order
  rather than arrival order, and its 14 mutants are one of the **14** vectors contributing
  to the 147. The charge condition is named because it is the one variable that has moved
  a §6 total before: this run was on AC, so the tournament arm was not clamped to width 1
  and `checkpoint_resume_check` passed rather than being resized.
  **That 1294 was the tree before clause 7, and 1298 has now been printed.** Adding the
  interactivity checks moved the session line from 44 checks / 14 mutants to **48 / 16**,
  which is `+4 checks, +2 mutants` on the same 37 lines, and the re-read on this tree
  printed `checks 1298  oracle 20  §6 total 1318  mutants 149` with **37** OK lines and no
  BAD line, plus its own `matches SPEC §6 as written: 1298 + 20 = 1318 green, offline
  (+ 149 mutants)`, in **17 min 54 s** on AC at 80% (`battery_reread_r715c7_20260929.log`)
  against a `CLAIM` of `checks 1298  oracle 20  mutants 149` summed from `BATTERY` before
  the run started and not edited after it. Its 30th OK line is the session's own
  `48/48 (+ 16 mutants)`.
- **R-7.15b (CLOSED 2026-09-29 for the mechanism it asked for; its follow-ups are booked
  below as R-7.15c/d/e/f/g, of which e, f and g have since closed and c and d are open)**
  That same live turn named the most
  ordinary request a developer makes, and
  the patch arm could not answer it: **it can revise a symbol it can see, and could not
  create one.** A `# edit: file :: Symbol` address is resolved against the AST, so a
  request to add a function names a symbol that does not exist yet, the patch is refused,
  and the correct refusal costs the turn.
  *(Not closed by rewording turn 2 as expected behaviour: the offline vector proves the
  refusal is correct, and the live transcript proves it is also useless for the request.)*
  **It now has a second, worse victim, on the author's own demo task** (the pty run above):
  asked to fix dollar formatting, the model wrote a patch addressed to `money.py::
  format_dollar` — a symbol it had decided to create — and the AST refused it
  (`patches=1 refused=1`, `solved=False`, `attempts=4`). What makes that expensive is not
  the refusal, it is what follows: `loop.solve_routed` then escalated to the 30B — the
  trace logs `denied=false, tier="big"` on **both** turns — and the big tier failed too,
  so the create-shape gap is not a small-model artifact. One ask, one refusal, two tiers,
  zero lines written, rc 1.
  **Shipped: a create address the AST owns, in both grammars.** `CREATE_PREFIX = "+"`
  makes `Patch.kind` read `create` (`whole` → `create` → `range` → `symbol`), `target`
  strips the `+`, and `resolve` hands the whole question to `_resolve_create`, which
  refuses a `+` that names nothing and refuses a `+` on a name the file already has — with
  the sentence telling the model to drop the `+` to revise it. The position is the
  extractor's, never the model's: a top-level create lands after the last top-level
  definition, a `+Container.member` after that container's last member at the members' own
  indentation, and into an empty class right after its header. The span it returns is the
  pure-insertion form `splice` already read (`start == end + 1`), so `spans = ()`,
  `regenerated == 0` and `outside_lines == 0`: a create owns no line of what was there.
  `changed_lines` still charges it one line — the insertion point, at the end of the before
  file — and that is the pair the checks keep apart, because the audit that must read zero
  is the `spans`-based one. `check_result` refuses a create whose body defines a *different*
  name, so the symbol the oracle is about to be asked about is the symbol that landed;
  `PROTOCOL` names the form (a capability the prompt does not mention is a capability the
  model cannot ask for, and a check pins that the shipped text contains it); and the
  applier owns the whitespace — two blank lines above a top-level Python definition, one
  inside a container and in a `.tsx`, because that is each file's own convention.
  `parse_patches` needed **no** change: `EDIT_MARKER`'s address group was already `\S+?`,
  which is pinned rather than assumed.
  **Gated the way R-3.2's clause 3 gates replacement.** `python -m flash.patches
  --selftest` **46 → 63** (`patches selftest: 63/63 checks passed`), with a `# 7b. CREATE`
  section of 17 checks including create-then-revise in one set and one bad create voiding
  the whole set. `python benchmarks/ts_patch_check.py --sweep` **44 → 52 checks, 13 → 15
  mutants** (`R-1.4 TypeScript patch arm: 52/52 checks passed`, `ts-patch mutants, fresh
  process each: 15/15 caught`, `one process: 15/15 caught`); the two new bugs are a create
  that ignores the container and one that skips the result check, each caught by exactly
  one named check. `python benchmarks/patch_landing_check.py --sweep` **40 → 48 checks, 22
  → 24 mutants** (`R-3.2 clause 3, patch landing: 48/48 checks passed`, `patch-landing
  mutants: 24/24 caught` in both lanes), and those four are the pair this box was written
  for: a landed create (`+k_to_c` through the real arm, an oracle only the new definition
  can satisfy, `+4 -0` on disk, every sibling's bytes and mtime intact) and a refused one
  (`+cents` on a name that exists: no workspace, tree byte-identical, and the attempt's own
  `err` carrying the remedy) cannot be confused by anything printed after either. The §6
  whole-tree re-read on this tree then printed exactly the `CLAIM` derived from `BATTERY`
  before it ran — `checks 1331  oracle 20  §6 total 1351  mutants 153`, **37** OK lines, no
  BAD line, **18 min 42 s** on AC at 80%
  (`benchmarks/results/battery_reread_r715b_20260929.log`).
  **The live arm was re-run on the same request, on this tree, and it still loses.** Same
  driver, same seeded tree, same two asks: `benchmarks/results/session_pty_r715b2_20260929
  .log`, replayable as `flash trace show 20260929-155858-session-f09b`. Turn 1 printed
  `routed=small tier=failed solved=False attempts=4 (15.6s) patches=1 refused=1`; turn 2
  `attempts=4 (12.6s) patches=1 refused=0` with `FAILING_ASSERT: assert cents_to_str(150)
  == "$1.50" | GOT: '$1.5'`; the session closed `turns=2 solved=0 written=0 seconds=28.2
  last_rc=1` and the recheck confirmed the file on disk still red. So the verb is no longer
  what blocks this task, and the trace names what is: four attempts on turn 1, and **the
  create form was offered to the model on the last one of them**
  (`— to ADD a symbol that does not exist yet, address it as '+format_dollar_amount'`), so
  it was told how to fix the patch with no attempt left to fix it with. The create-shaped
  address was the 30B's — the 7B's two refusals on that turn were `t.py` range addresses
  past the end of a 9-line file, which is R-7.15e's victim rather than this box's — and
  R-7.15e has since closed that sentence, on the same tree, with the same ask. Five
  gaps follow, and they are booked rather than reworded: **R-7.15c**, **R-7.15d**,
  **R-7.15e**, **R-7.15f** and **R-7.15g** below.
- **R-7.15c (CLOSED 2026-09-30 for the mechanism it asked for; its own vector line is
  what proves the guard moved rather than vanished)** **Create-a-FILE.** Three of the
  eight attempts in that re-run invented a module: `format_dollar_amount.py is not one of
  the project files (money.py, t.py)` and, on the next turn, `cents_to_str.py is not one
  of the project files`. The `+` verb existed for symbols only; `# edit: <newfile> :: *`
  was refused at the patch layer because the workspace has no such key, and `land`
  separately refused any key the oracle never scored.

  **Done, in the order the request arrives.** A file key that is not in the listing is now
  a workspace of its own: `# edit: <new_file> :: *` writes the block as the entire file and
  `# edit: <new_file> :: +<Name>` creates it holding that one definition, the body is
  syntax-checked in the grammar its filename claims (`ast.parse` for Python, the optional
  tree-sitter grammar for `.ts`/`.tsx`), an empty body is refused, more than one patch on
  that key is refused, and a bare symbol or range address on it is still refused — with the
  remedy naming the verb that would have worked. `land` writes the created file
  (`+N -0`, counted against nothing) and the oracle guard runs **before** the new-file
  branch, so `# edit: t.py :: *` cannot found an oracle that was not in the listing. The
  audit columns tell the truth about it too: a founded file carries `founded=True` and is
  kept out of `whole_rewrites`, because `*` on a file with no previous bytes is not a model
  re-typing something it could have addressed.

  *(The guard the box said must stay does stay, one layer up: `land` will write a key the
  listing never had, and nothing reaches `land` with such a key except a workspace
  `apply_patches` accepted — which is where the syntax, one-patch and oracle checks live.
  In the scored arm the workspace is only handed over when the oracle passed; in chat mode
  (R-7.15h) it is not scored at all, and the session says so on the line that opens it.
  `patch_landing_check`'s new-file clause is now a mutant rather than a check: put the
  refusal back and the created module never reaches the tree.)*
- **R-7.15h (CLOSED 2026-09-30)** **A session with no oracle could not answer `hi`.**
  The product report this project is named for — "it should also answer question no like
  hi how does this work what is this what does this do", then "it cant even reply hi" — was
  not a missing feature in the CLI, it is a clause inside the patch arm. `_solve_edits`
  fails any attempt whose response holds no `# edit:` header, because on a task with an
  oracle a prose answer is a non-answer: there is nothing for the test to have verified.
  With no oracle that check stops being a guard and becomes the reason a question is
  refused. So the session's `--test` is optional, and omitting it is a MODE:
  `flash session --context DIR` opens a chat, `loop.solve_chat` accepts prose and patches
  in the same reply, and the printed turn is the answer rather than the telemetry line.
  The three shapes are decided in the order the user means them: no patch → the text IS the
  answer and it is green; patches that apply → they land (so "create me a logger module" is
  one turn, through R-7.15c's verb) and the text is still the answer; patches that are
  REFUSED → not green, because the model said it changed something and changed nothing, and
  the refusal is fed back as the repair prompt exactly as the scored arm does it. What the
  mode gives up is stated where it is chosen: the banner says `no verification`, the landing
  sentence says `this turn produced a workspace` and never that an oracle passed, the ledger
  row and the `routed=` field both say `chat`, one model loads (the small tier — the router,
  the governor and the big tier are never reached), and `run --test` stays required so the
  scored surface cannot quietly become a chat that prints confident answers.
  Vector: `python benchmarks/session_check.py --sweep` — **58 → 81/81 checks, 20 → 26/26
  mutants**, the 31st line of §6. The command's side is stubbed at `solve_routed` (so the
  copy-vs-shipped `agree()` holds over 11 scenarios now, 4 of them chat); the arm's side
  runs the REAL `solve_routed` with only `load_model` and `_generate` replaced, so the
  claims that a chat loads one model, never escalates, is asked with the project in front
  of it (R-7.15f carried into this arm) and gets a `chat` ledger row are measured rather
  than asserted. Three of the six new mutants live below the command — `_oracle_key`
  without its no-oracle line (the session dies on `Path(None)` at the first `hi`),
  `_land_edits` printing the scored sentence (an unscored turn claims an oracle passed),
  and `solve_chat` reusing PATCH MISSING (the prose is generated, printed, then marked red —
  the original bug one layer down, which no count of turns can tell from a chat).
- **R-7.15d (OPEN)** **A remedy offered on the tier's last attempt is not a remedy.** The
  budget is two attempts per tier, so the first refusal that names the fix — which is the
  entire point of putting the fix in the refusal — arrives when there is nothing left to
  spend it on. Vector: one remedy-triggered retry at the tier that refused, measured on this
  same demo task with the printed before/after. Cost, from the re-run's own timestamps: the
  7B's four attempts ran 1960–2904 ms and the 30B's 1590–6862 ms (the one that carried
  the create remedy took 4641 ms and had nothing left to spend it on), so the retry is
  ~2 s on the small
  tier and ~5 s on the big one, and it is paid only on turns that are already losing.
  *(Priced, not decided: it changes the attempt accounting every §34 gate reads.)*
- **R-7.15e (CLOSED 2026-09-29 for the mechanism it asked for; the demo turn it was bought
  for went green two boxes later, at R-7.15g)** **A patch aimed at the oracle is refused for the wrong
  reason.** Turn 1
  spent two of its four attempts on `t.py:L10-L12` and `t.py:L11`, and the arm answered
  `past the end of a 9-line file` — a complaint about coordinates, in a file the run is
  scored against and must not change. The sentence that would have cost nothing is `t.py is
  the oracle this run scores against; patches to it are refused`. Vector: the patch arm has
  to be told which key is the oracle (`--test` already knows; `apply_patches` does not, and
  the protected-file list is a `land`-side concept today), with a check that the refusal
  names the oracle rather than the line count.
  **Done.** `apply_patches(workspace, patches, oracle="")` refuses any patch whose file key
  is the oracle, before the address is resolved and in all four address kinds, with
  `ORACLE_MSG`: `… is the oracle this run scores against, so it is not a patch target — an
  assertion is never edited to fit the code. Change the module the test imports.` Resolving
  first would have kept the accident: on this machine's own oracle the coordinate complaint
  and the oracle refusal are different sentences, and only one of them is actionable. The
  key comes from one function, `cli._oracle_key(args, task)`, which is `_rel_to(--test,
  --context)` when that name is a workspace key and `""` otherwise; `cmd_run --edit` and
  every turn of `cmd_session` put it on the task as `test_path`, `loop._solve_edits` passes
  it down, and `_land_edits` reads the same field — so the gate that refuses the patch and
  the gate that refuses the write-back cannot disagree about which file the run is scored
  against, which they previously could not do anything about because only one of them knew.
  A test kept outside `--context` names no key, which is what leaves the published suite's
  60 premise checks and their whole-file control untouched. The PROTOCOL now carries the
  rule as a bullet, so the first refusal is the prompt's and the patch layer's is the
  second. Vector: `flash.patches --selftest` **71/71** (8 new, on the refusal running
  before resolution, on all four kinds, on the set being voided, on a non-oracle patch
  still applying while the oracle is named, and on the same patch applying when no oracle is
  named — the last one is what makes this a gate rather than a second grammar);
  `patch_landing_check --sweep` **53/53 checks, 27/27 mutants** (three new bugs: the layer
  never told, the two sentences confused, and the command naming no key);
  `session_check --sweep` **49/49, 17/17** (every turn carries the key, including the turn
  after a write) — those three rows have since moved to **78/78**, **61/61 (+ 28)** and
  **58/58 (+ 20)** at R-7.15f and R-7.15g, so the numbers above are this box's own prints,
  not the tree's current totals.
  Witnesses for those three prints, each on the edited tree:
  `benchmarks/results/patches_selftest_r715e_20260929.log`,
  `patch_landing_sweep_r715e_20260929.log` and `session_sweep_r715e_20260929.log`. Live, on
  the same driver and the same seeded tree as the run above, with
  the ask that starts `…exactly as t.py asserts`
  (`benchmarks/results/session_pty_r715e_20260929.log`, trace
  `20260929-203852-session-3973`): **two of the eight attempts addressed `t.py`, and both
  were answered about the file rather than about its line numbers** — the 7B's first attempt
  `t.py:zero_pad_cents` and the 30B's second `t.py:format_money` each printed
  `PATCH REFUSED: t.py:… — t.py is the oracle this run scores against, … Change the module
  the test imports.` The turn still lost: `turns=2 solved=0 written=0 seconds=23.3
  last_rc=1`, which is R-7.15f's finding rather than this box's.
- **R-7.15f (CLOSED 2026-09-29; the demo turn it was bought for went green only after
  R-7.15g below)** **The chat never sees the project it is asked to patch.** Measured on
  this machine, offline, with no model: `cmd_session` builds
  `task["prompt"] = <the typed ask>` and `edit_prompt` appends `PROTOCOL`, so the message
  the 7B was given for the ask above is **2129 characters — 96 of them the ask, the rest
  the protocol — and contains neither `# file: money.py` nor a single line of
  `cents_to_str`'s body**, while `task["files"]` holds both files and is handed to the arm.
  `flash run --edit --context <dir>` is the same shape. `enrich_task` returns an edit task
  unchanged (`An edit task ships its own real source in the prompt`), and the suite's tasks
  do ship it: `gen_edit_tasks.build()` stores `The project is below, with the real text of
  every file.` + `describe(workspace)` + `Requested change: …` + the test, which is what
  makes its patch-vs-whole-file A/B a protocol comparison. So the 60/60 premise checks, the
  published `m7_heldout` figures and every committed pass rate are measured on a prompt no
  CLI command can produce, and the CLI shape — the one the release is for — has never been
  measured. The live run above is the consequence: of eight attempts, **four invented a
  module** (`main.py`, `cents_to_str.py`, `money.format`, `cents_to_str`) and **two reached
  for the oracle**, so six were refused for not knowing what the project is; the only reason
  any of them ever recovered is that the refusal itself prints the file list. Vector: the
  CLI's edit arm composes the same material the generator stores — `describe(files)` +
  `Requested change: <ask>` + the oracle's text — through one shared function so the suite
  and the session cannot drift, with checks that a session turn's first message names every
  workspace key and that a `+NewSymbol` ask arrives with the target module's real text; the
  A/B's comparability claim (R-3.2 clause 1) then has to be re-read against the CLI, not
  only against the stored tasks. Cost: prompt tokens go from the ~478 the trace printed for
  turn 1 to the size of the tree, capped at `describe`'s 6000 characters, and the router
  and the ledger both read prompt length — the numbers of every live row in §34 measured on
  a 96-character ask are re-measured or marked as measured on the blind prompt.
  **Done.** One composer does the attaching: `patches.project_prompt(ask, files, test)`
  (`flash/patches.py:917`) emits `PROJECT_HEADER` + `describe(files)` + `Requested change:
  <ask>` + the oracle's text, and `edit_prompt` is that plus `PROTOCOL` and nothing else.
  `loop.enrich_task`'s edit branch (`flash/loop.py:206`) is the only place the block is
  added and it is guarded on the header, so a task that already ships the material comes
  back byte-identical instead of nesting a project inside a project; because `enrich_task`
  is reached only from `solve_routed`, `run --edit` and every `session` turn take that same
  line, so there is no second prompt shape left to drift. Measured on this box's own shape,
  offline, no model: the ask above goes from **2129 characters of message to 2893** — the
  project block is 860 of them, both files are named, the header appears once — and a
  three-file tree 19 200 characters wide composes **12 325 chars**, which is `describe`'s
  cap doing its job rather than the prompt growing with the repo. Live, the same eight
  attempts cost `prompt_tokens` **728–904** where the blind run recorded **503–676**.
  Comparability is re-proved rather than asserted: `gen_edit_tasks.build()` calls the same
  function, and rebuilding the corpus reproduces the committed
  `benchmarks/tasks/edit_tasks.jsonl` byte for byte — 10 records, 10 equal, and `main()`'s
  own serializer emits the file's SHA-1 prefix `fc5b27b383f5` — so R-3.2 clause 1's
  patch-vs-whole-file A/B still differs only in the answer format it asks for, and the
  60/60 premise checks and every published pass rate stay measurements of the prompt they
  were measured on. Vector: `flash.patches --selftest` **71 → 78/78**
  (`patches selftest: 78/78 checks passed`; the 7 new checks are a `# 7d. PROJECT PROMPT`
  section — every key named as an address, the real body rather than an outline, the ask
  quoted verbatim, the oracle fenced and labelled, no test heading invented when no test
  was named, the header being the one exact sentence `enrich_task` reads as its marker, and
  the over-cap path) and `patch_landing_check.py --sweep` **53 → 61 checks, 27 → 28
  mutants** (`R-3.2 clause 3, patch landing: 61/61 checks passed`, `patch-landing mutants:
  28/28 caught` in both lanes) — among the 8 new checks, that composing is idempotent, that
  **both arms** are handed the same `# file:` block and the same oracle text, that the two
  arm messages are identical up to the sentence naming the answer format, and that the
  composed message is larger than the ask by more than the protocol's own tail; the new
  mutant `returns an edit task with only its typed ask, which is the state R-7.15f was
  filed for` defeats 6 checks. Witnesses, each on the edited tree:
  `benchmarks/results/patches_selftest_r715f_20260929.log`,
  `patch_landing_sweep_r715f_20260929.log`, `session_sweep_r715f_20260929.log`.
  Live, same driver, same seeded tree, same two asks
  (`benchmarks/results/session_pty_r715f_20260929.log`, trace
  `20260929-224842-session-0e6b`): **all eight attempts addressed `money.py:cents_to_str`**,
  every turn line reads `refused=0 whole=0 outside=0`, and nothing invented `main.py`,
  `money.format`, `cents_to_str.py` or `cents_to_str` or reached for `t.py` — six refusals
  became zero. **The turn still lost**: `turns=2 solved=0 written=0 seconds=32.9
  last_rc=1`, because all eight patch rows report the identical `GOT: '$1.5'` for a module
  the turn had already rewritten. That is R-7.15g, and it had been costing the two
  well-addressed attempts in R-7.15e's run as well. The blind-prompt rows in §34 keep their
  label rather than being quietly re-used as current numbers: they measure a message this
  repo no longer sends.
- **R-7.15g (CLOSED 2026-09-29)** **VERIFY graded the bytes from before the edit.** The
  prompt fix worked and the demo turn kept losing, so the finding was not in what the model
  was shown. `harness.score_files` writes the candidate workspace into a temp root and
  substitutes `<TMPDIR>` into the oracle, but the demo's `t.py` carries a **literal**
  bootstrap — `sys.path.insert(0, "/tmp/flash-chat-demo")`, the shape a person writes so
  `python t.py` works from anywhere. That path resolves to the live tree, so it went onto
  `sys.path` ahead of the temp root, `from money import cents_to_str` bound the **unpatched**
  module, and the scored verdict was computed from files the turn had already replaced: the
  same eight attempts that applied cleanly (`applied=1`, `refused=0`) were each graded
  against the pre-edit body and each printed `GOT: '$1.5'`. A count in the report was never
  going to catch this — the patch layer, the verdict line and the trace all said the edit
  landed, and only the oracle's copy disagreed. Vector: `score_files` must hand the patched
  copy precedence when the oracle's own bootstrap names a directory that holds one of the
  scored files, while an oracle that names nothing relevant keeps every byte it wrote —
  `harness._unshadow` inserts the temp root **as its own line, carrying the bootstrap's
  indentation**, and the fix's first version is a mutant.
  **Done.** `_unshadow` (`flash/harness.py`) scans for `sys.path.insert(0, <literal>)` lines
  and, when that path resolves to a directory containing one of the scored files and is not
  already the temp root, appends `{indent}sys.path.insert(0, <root>)` after it. A new line
  rather than `; sys.path.insert(...)` on the same one, measured both ways on 2026-09-29: a
  bootstrap ending in a comment — which is what a stranger's `t.py` actually looks like —
  swallows anything appended after the `#`, and the appended form came back `(True, '')` with
  no comment and `(False, '… GOT: \'live\' …')` with one. So the fix as first written was a
  silent no-op on the shape it was bought for, and it was found by attacking it rather than
  by the checks that shipped with it; both the check `a bootstrap that ends in a comment
  still grades the patched copy` and the mutant `the precedence statement lands behind the
  oracle's own comment: the fix is present, correct, and` now pin it.
  Vector, on the edited tree: `python -m flash.harness --selftest` **20 → 28/28**
  (`harness selftest: 28/28 checks passed`) — the patched copy wins, the pre-edit bytes
  still fail under the same oracle with `GOT: 'live'` (so this is precedence, not a softened
  verdict), `score_files` ranks the shadowed shape off the patched copy, a `<TMPDIR>`
  bootstrap and a bootstrap naming an unrelated directory are both left byte-identical, the
  rewrite adds **one** line and changes no other byte of the oracle, and the indented-block
  case keeps the block's indentation; `python benchmarks/session_check.py --sweep` **49 → 58
  checks, 17 → 20 mutants** (`R-7.15 interactive session: 58/58 checks passed`, `session
  mutants: 20/20 caught` in both lanes) — the four new checks drive the real `cmd_session`
  over a tree whose oracle names its own directory, and they assert that the patched
  `money.py` wins, that the pre-edit body still fails with a `GOT:`, that the refusal the
  model sees quotes the oracle's assert **verbatim** (a precedence fix that rewrote the exam
  would fail here even while turns started passing), and the comment case. New mutants:
  `VERIFY writes the patch and then grades the tree that was there before it` and
  `the precedence statement lands behind the oracle's own comment`. Witnesses:
  `benchmarks/results/harness_selftest_r715g_20260929.log`,
  `session_sweep_r715g_20260929.log`.
  **Live, and green — the first coding turn this release was asked for.** Same driver, same
  seed, same two asks, real weights (`benchmarks/results/session_pty_r715g_20260929.log`,
  trace `20260929-231604-session-b07a`): turn 1 `[turn 1] routed=small tier=big
  solved=True attempts=3 (13.5s) patches=1 refused=0` and `[R-3.2] wrote money.py (+4 -1
  lines)`; turn 2 `solved=True attempts=1 (5.7s)` and `wrote money.py (+2 -0 lines)`; the
  session closed `turns=2 solved=2 written=2 seconds=19.2 last_rc=0`, the child exited
  **rc 0** in 29.4 s, and the driver then re-ran the oracle against the bytes now on disk
  outside the session and printed `rc=0 ORACLE GREEN`. The file it left behind is the one the
  asserts describe: an `isinstance` guard raising `ValueError`, zero-padded cents, and a
  minus sign in front of the dollar. Nothing here is the model getting smarter — the same
  eight addresses were already correct before this box closed; what changed is that the
  verdict finally scores the copy it claims to.
- **R-7.16 (CLOSED 2026-10-04)** **A Mac cannot see the bug every other install
  has.** The first CI run on a real runner said it in one line
  (`static (3.11)`, run `37102668462`, step *Backend-free import claim*):
  `FAIL ...and a failure that survives the data coming back is one of the three
  named machine-state vectors … [('train', 1, "    from mlx_lm.lora import
  CONFIG_DEFAULTS\nModuleNotFoundError: No module named 'mlx_lm'")]`. Several days
  earlier the same code had been re-read green on this laptop, because a Mac has
  `mlx-lm` installed and `pyproject.toml` puts it behind
  `sys_platform=='darwin' and platform_machine=='arm64'`, so every Linux and Windows
  install has the shape that line describes. Two halves came out of that, plus a
  third the nightly runner handed over for free.

  **Half one: the training seam, offline for real.** `flash.train --selftest`'s
  slice-args checks had been argued against the *installed* library, so the
  library's defaults are now recorded in the module
  (`MLX_LORA_ARG_DEFAULTS`, the 29 keys of mlx-lm 0.31.3) and `build_slice_args` is
  a pure function over a dict; `mlx_lora_defaults()` returns the recorded map when
  `mlx_lm` cannot be imported and names its source either way, so the check's own
  detail says which of the two the argument was made against — and on this box the
  recorded map was proven `== mlx_lm.lora.CONFIG_DEFAULTS`, not merely similar. The
  check asserts `set(vars(a)) == set(defaults)`, which makes a key the library would
  silently ignore a failure rather than a no-op, and `default_slice` now raises
  `ValueError("no training rows in …")` *before* any model import, so an empty
  dataset is a named error on a machine with no backend. **36/36 in both lanes**,
  unchanged in count, and the vector that catches this class of bug got the arm
  that measures it: the package-only sweep re-runs all 16 selftests in the same
  copy with `mlx`, `mlx_lm` and `mlx_vlm` unimportable, plus a control that imports
  `mlx_lm.lora` in that interpreter and requires the shim's own sentence — so the
  arm cannot go green because `BLOCKED` lost a name.

  **Half two: a lane that refuses instead of lying.** With the block live, the
  whole battery measured 35 rows green and 2 that reach the backend for reasons
  that are real, not planted: `flash.grammar`'s mask checks load the tokenizer
  through `mlx_lm.tokenizer_utils`, and `benchmarks/session_check.py`'s chat arm
  walks past `flash.loop.solve_routed`'s preamble `import mlx.core`. Calling those
  two failures on Linux is a red badge nobody can fix in code, so `--backend-free`
  now prints a **REFUSED** line per row carrying the sentence the row died with,
  subtracts that row's own counts from `CLAIM`, and exits 0 behind a `NOT a §6
  re-read` banner that says which lane's subtotal it printed. The lists are
  decoders, not exemptions, and that is gated five ways: the decoder read against
  ten texts of which seven must *not* decode (an unrelated `AssertionError`, a
  healthy line that merely names `mlx_lm`, empty output, and each cause in the
  other arm), the named rows checked as battery arithmetic (`lane_claim([])` is §6
  untouched; an 81-check 26-mutant row drops the claim by exactly 81 and 26), a
  live `--backend-free --quick grammar session` whose REFUSED set must equal the
  parent's list (the child reads the lists off disk, so the two agreeing is a
  measurement), and a plain-lane child run *with the block injected but the flag
  withheld*, which must come back non-zero with a BAD line and no REFUSED — the
  flag decides forgiveness, not the environment. `python
  benchmarks/backend_free_check.py`: **49/49 checks, 15/15 mutants**, each of the
  four new ones killed by exactly its own check. A third arm exists for
  `checkpoint_resume_check.py`, whose precondition is live state rather than code:
  the §34.1 governor will not offer tournament width ≥ 2 on a busy or uncooled
  machine, and that row is refusable in *every* lane, because a hosted runner is
  not allowed to be cool and a laptop is not allowed to be quiet.

  **Half three, from the nightly runner's own words.** Four consecutive scheduled
  nightly failures (latest `37193175901`) died in *Offline battery first* with
  exactly two BAD lines: `flash.grammar --selftest want 47/47 got ['24/25']` and
  `checkpoint_resume_check.py want 35/35 got []` on `AssertionError: the
  tournament arm needs the governor's width >= 2 and this machine offers 1 (free
  memory 6.2GB < 6.9GB needed, load 4.6/core)`. The second is now a REFUSED line.
  The first is a cold cache, not a failing test: the weights cache in that job only
  fills *after* the live run, so `ci.yml`'s macos `battery` job now pre-warms the
  tokenizer's 7 files (11M, `snapshot_download` with an `allow_patterns` list that
  touches no weights) and asserts the row in isolation with `battery_reread --quick
  grammar` before asking for §6. Verified here against a throwaway HOME, which is
  where `flash.grammar --selftest` then printed **47/47** rc 0. The same two steps are
  written for `nightly.yml` and are deliberately **not in this commit** — the nightly
  workflow stays untouched until its own pass, so those four scheduled failures are
  still open and named here rather than claimed fixed.

  **The confirming re-read.** Both lanes ran on the final tree, sequentially, on AC
  at 80%. The plain lane printed 37 `OK` lines, no `BAD` line and rc 0 with
  `checks 1426  oracle 20  §6 total 1446  mutants 171` followed by `matches SPEC §6
  as written: 1426 + 20 = 1446 green, offline (+ 171 mutants)`; the `--backend-free`
  lane printed 35 `OK`, the two named `REFUSED` rows each carrying the sentence it
  died with, and rc 0 with `checks 1298  oracle 20  §6 total 1318  mutants 145` above
  the `NOT a §6 re-read` banner. The lane's **22 min 47 s** wall comes from the two
  prints' completion times and the fact that the driver ran them one after the other;
  the plain run's start is not in its own print, so only its totals are claimed.
  1426 − 128 = 1298 and 171 − 26 = 145, which is the subtraction the banner states.

  **The CI round that moved the same row again.** Both prints above are dated to the
  tree before this paragraph, and the reason they moved is the bug this requirement was
  built to catch, aimed at its own gate. The first push ran `static (3.11)` and
  `static (3.12)` on a real ubuntu runner and both printed `backend-free checks:
  47/48 passed`, which cancelled the matrix and skipped `battery`. The failing check
  demanded the installer's blocker line read `proof --backend-free` — a sentence only a
  machine that owns MLX may print, so on a runner that has none the honest line is
  `note --backend-free: … this machine has no backend to lose`. Fixed at the cause, not
  loosened: the branch is bought from both paths by feeding `install_backend_block()`
  fabricated probe results (a box where `import mlx.core` survives, a box where it does
  not) and requiring each to print its own word and never the other's, with a 15th
  mutant, `blocker_is_a_literal`, that hard-wires `proof` and dies on exactly that
  check; the gates then ask for whichever sentence the box earns, so the Linux runner
  takes the `note` path, exits 0 and refuses the same two rows. That is
  `backend_free_check` **48 → 49 checks, 14 → 15 mutants**, and the re-read on that
  tree printed `checks 1427  oracle 20  §6 total 1447  mutants 172` with **37** OK lines
  and no BAD line (`battery_reread_r716c_20261004.log`).
  **The same bug class, caught locally before it reached a runner.** The checker decided
  its box-fact from `import mlx.core` in its *inherited* environment — and inside
  `--backend-free` that PYTHONPATH already carries the shim, so on this Mac the lane
  child reported "no backend to lose" while the installer's own probe, which strips
  PYTHONPATH, reported `proof`. Measured, not argued: run as a lane child it printed
  **47/49** with the two FAIL labels naming the two gates that keyed off the inherited
  env, and after reading the same probe the installer answers from it printed **49/49 +
  15/15** at rc 0
  (`benchmarks/results/backend_free_check_lane_shape_r716c_20261004.log` holds both
  halves of that in one file). A vector that gates a lane has to survive being *in* the
  lane it gates, and the lane itself is now the gate that proves it.
  **Both lanes on the fixed tree, sequentially.** The plain run printed **37** `OK`,
  no `BAD`, rc 0 on `checks 1427  oracle 20  §6 total 1447  mutants 172` + `matches
  SPEC §6 as written` (`battery_reread_r716d_20261004.log`, 21:15:13 → 21:39:37); the
  `--backend-free` run then printed **35** `OK`, the same two named `REFUSED`, no `BAD`
  and rc 0 on `checks 1299  oracle 20  §6 total 1319  mutants 146` under `NOT a §6
  re-read` (`battery_backendfree_lane_r716d_20261004.log`, 21:39:37 → 22:01:20) — 1427
  − 128 and 172 − 26, which is what its own banner states. That second print is the one
  the fix bought: the same command on the pre-fix tree refused to total at all, because
  it could not pass its own gate from inside the lane.

  **What this does not buy.** The `battery` job in `ci.yml` has still never
  executed — `needs: static` skipped it on the run above, so its first green on a
  real macos-14 runner remains the proof, not this entry. And the two refused rows
  have never been measured *passing* on a Linux install, because a Linux install
  cannot run them; the lane now says that out loud instead of reporting it as a
  defect. Witnesses: `benchmarks/results/train_selftest_r716_20261004.log`,
  `benchmarks/results/backend_free_check_r716b_20261004.log`,
  `benchmarks/results/battery_reread_r716b_20261004.log` and
  `benchmarks/results/battery_backendfree_lane_r716b_20261004.log`.
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
- **R-9.2 (SHIPPED — PARTIAL: the memory rlimit this box cannot set; the §34
  renderer is the one exempt path, named below)** Every execution path MUST run
  under an explicit sandbox (§21): writable root, no network by default, cpu and
  memory rlimits.
  Vector: a test that proves a hostile candidate (`open('~/.ssh/id_rsa','w')`,
  `socket.connect`) fails under the sandbox and the suite still reports it as a
  normal verify failure.
  *Shipped as `flash/sandbox.py`. §21's substrate is named rather than quietly
  swapped: the plan is a Firecracker microVM per rollout, which does not exist on
  the ship target (macOS/arm64 — and the offline battery must run with no Docker
  daemon), so the module wraps the platform's own kernel-enforced mechanism: a
  Seatbelt profile handed to `/usr/bin/sandbox-exec`, plus POSIX rlimits the child
  inherits across the `exec`. Candidate code cannot opt out of either, because
  both are applied by the process that spawns it. `seatbelt()` is an *enforcement
  probe*, not a `Path.exists()` — it asks a deny-only profile to refuse a real
  write and requires the file to stay absent — so a wrapper that accepted a
  profile it then ignored degrades to `prefix() == []` loudly instead of leaving
  every check below reading green; the selftest proves that with a fake wrapper
  which shifts its own `-p` argument away.*
  *The vector's four refusal shapes, each run through `harness.score` so the
  reporting path is the shipped one: a write to `~/.ssh` → `PermissionError` from
  the kernel, with the sentinel file still absent afterwards; `socket.connect` to a
  TEST-NET-3 address → `PermissionError: [Errno 1] Operation not permitted`, before
  a packet leaves; `urllib.urlopen` → `URLError` re-wrapping the same refusal, so a
  candidate cannot hide behind its own `try/except` (the ERROR line is what the retry
  sees); `socket.getaddrinfo('example.com')` → `gaierror`, because name service is
  itself outbound, so the refusal arrives early rather than as a long timeout. The
  clause's second half is the part that is easy to fake, so it is checked as a
  ranking: a hostile candidate whose refusal sits *after* one passing assert scores
  `1/2` like any partial answer, and `diagnose` returns it in the same
  `GOT/WANT/ERROR` shape (`ERROR: PermissionError`) the retry loop consumes. And
  `open('~/.ssh/id_rsa','w')` spelled literally is *not* the confinement case —
  `open()` never expands a tilde, so that candidate writes `<root>/~/.ssh/id_rsa`
  inside its own writable root; the vector is run through `os.path.expanduser` and
  the distinction is a stated check, not a glossed one.*
  *Two traps found by running the thing, both pinned: a `subpath` must be
  DOUBLE-quoted (a single-quoted path parses as a SYMBOL and `sandbox-exec` dies
  with "unexpected symbol argument"), and the root must be a REALPATH —
  `/var/folders/...` is a symlink to `/private/var/...`, a pattern naming the
  symlink form is compared literally against the kernel's resolved write path, and
  the exception then silently misses so a candidate cannot write in the directory
  it was given (all four pattern×target combinations measured; only a symlink at
  the ROOT of the path, `/tmp`, is resolved by a different rule). One claim
  corrected in writing while doing this: the first draft of `profile` said
  Seatbelt's last match wins, so the root allow had to follow the blanket deny.
  Measured both orders, that is false — this pair resolves by filtered
  specificity and the root stays writable either way. The order is now documented
  as convention, and "the exception binds" is carried by the write vector rather
  than by the shape of the string.*
  *The rlimits ride a `preexec_fn` on the wrapper, which then `exec`s into the
  interpreter, so they are the candidate's own and are inherited by anything it
  forks. `RLIMIT_CPU` is proven to bind *through* the wrapper: a busy loop under
  `(2, 3)` died at 2.01 s on signal 24 (SIGXCPU) instead of at its 30 s wall
  timeout, and the child itself reports the limit it was given. `RLIMIT_FSIZE`
  caps a file at 256 MB — a 600 MB write came back as `OSError: [Errno 27] File
  too large`, i.e. an ordinary verify failure rather than a filled disk. The cpu
  budget is deliberately `timeout * CPU_WIDTH + 2 s` (CPU_WIDTH = the box's cpu
  count) instead of equal to the wall clock, because `RLIMIT_CPU` accrues a
  waited-for child's cpu into its parent: a limit tuned to one thread would fail a
  CORRECT parallel answer for our arithmetic. Nothing in the 22 shipped suites
  parallelises (measured: 0 rows mention multiprocessing/threading/subprocess/Popen)
  and Accelerate BLAS runs at 0.66 cpu-seconds per wall-second here, so in practice
  the wall clock is the bound that fires and the rlimit is the backstop for a child
  that outlives the caller waiting on it. `RLIMIT_NPROC` was tried and rejected as a
  design: this uid already owns ~436 processes, so any cap that binds also breaks
  the user's own shell, and the clause does not name it.*
  *The memory half is NOT met and stays booked. Measured 2026-09-27 while bounding
  the R-2.3 edge probe and re-measured here: `resource.setrlimit` accepts and
  enforces `RLIMIT_CPU` and `RLIMIT_FSIZE` but raises `ValueError: current limit
  exceeds maximum limit` for `RLIMIT_AS`, `RLIMIT_DATA` and `RLIMIT_RSS` at ANY
  finite value on this macOS, at any privilege. `memory_ceiling()` therefore asks a
  *fresh child* to try each one and report, so the claim tracks the platform
  instead of a table written in this file, and `status()['memory']` prints the
  refusal (`UNAVAILABLE: no memory rlimit can be set (...)`) wherever the report
  does. For OUR OWN probes the fix is to bound what is asked for (`_affordable`,
  shipped); for a hostile candidate that allocates on its own initiative neither
  rlimit is a ceiling — the remaining options are the §34.1 free-memory signal as a
  pre-flight refusal, and running the sandbox on a kernel that enforces `RLIMIT_AS`.
  Reads are not confined anywhere either, as a stated limit rather than an accident:
  a `(deny default)` read policy breaks the interpreter's own dyld and framework
  lookups, and the clause names a writable root, no network and rlimits. So this is
  a write-and-network jail with a cpu and file-size ceiling, described as that.*
  *Wiring: the five candidate-execution seams — `harness.run_test`,
  `harness._probes` (through `score`/`score_files`), `debug._run`,
  `confidence._seeded_run`, `confidence.edge_probe` — now spawn through
  `sandbox.run`, which is `subprocess.run` plus the prefix, the rlimits, and `TMPDIR`
  retargeted into the root (without the retarget every candidate that calls
  `tempfile` fails for our reason; the check asserts both
  `os.environ['TMPDIR'] == root` and `tempfile.gettempdir() == root`, because
  tempfile's cwd fallback can mask a missing retarget). "Every execution path" is
  verified at runtime, not by grep: a selftest check swaps `flash.sandbox.run` for a
  spy that records the caller frame's name and requires all five seams to appear —
  and it must patch the *imported module*, since `python -m flash.sandbox` creates a
  second module object and patching this file's own globals spies on a copy nobody
  calls. `benchmarks/m0_bakeoff.py` carried a duplicate copy of the oracle that
  scored real MODEL output through a bare `subprocess.run`; it is now an import of
  `flash.harness.run_test`. Multi-file sets were re-verified inside the jail (the
  `<TMPDIR>` bootstrap and `cwd=root` agree, so a sibling module still imports:
  `1/2`, not an error-out), and `debug`'s trace driver runs with `cwd` in the root.*
  *One path is exempt, named here instead of quietly skipped: the §34 HTML→PNG
  renderer (`vision.render_html_png`, headless Chrome) cannot be given a writable
  root — `--user-data-dir=<sandbox root>` makes Chrome exit rc=21 "Failed to create
  a ProcessSingleton" (measured), because it must lock and cache outside a one-shot
  directory. Its egress is killed instead with
  `--disable-background-networking --host-resolver-rules="MAP * ~NOTFOUND"
  --proxy-server=http://127.0.0.1:9`, which produced byte-identical PNGs and cost
  +10 s of wall only on a page that actually fetches; its wall bound is now the
  named `RENDER_TIMEOUT_S = 60` rather than an inherited default.*
  *Cost, because "fast AND accurate" is the standing gate and a sandbox nobody can
  afford gets bypassed: per child spawn the bare run measured 26.8 ms, with the
  wrapper alone 29.0 ms, with the whole sandbox 39.0 ms (+12.2 ms); 60 `score()`
  calls went 6.04 s → 8.31 s. Vector: `python -m flash.sandbox --selftest` (34
  checks) plus a 12-mutant campaign — drop the blanket write-deny, drop the root
  allow, single-quote the subpath, feed the `/var` symlink form, reduce `seatbelt()`
  to `Path.exists`, remove the cpu limit, remove the FSIZE limit, retarget `TMPDIR`
  back out, drop the null-device exception, unwire each of the five seams — all 12
  caught, tree restored and checksum-verified after each. One mutant leaked the
  sentinel into `~/.ssh`, which is why `reap_own_artifact()` exists: a clean run
  deletes a file there only when its content is exactly the bytes the vector writes
  (three checks pin that an unrelated file — a real key — survives).*

---

## 6. Verification protocol — how a box gets checked

1. **Offline battery first** (seconds, no models, must be green before any live
   claim): `python -m flash.harness --selftest` 28 · `flash lsp-selftest` 22 ·
   `flash power --selftest` 24 · `flash jobs --selftest` 20 ·
   `flash trace --selftest` 30 · `flash web --selftest` 9 ·
   `python -m flash.grammar --selftest` 47 · `python -m flash.patches --selftest`
   94 · `python -m flash.debug --selftest` 55 ·
   `python -m flash.tourney --selftest` 16 ·
   `python -m flash.confidence --selftest` 29 ·
   `python -m flash.sandbox --selftest` 34 ·
   `python -m flash.checkpoint --selftest` 31 ·
   `python -m flash.train --selftest` 36 ·
   `python -m flash.graph --selftest --mutants` 44 (+ 12 mutants) ·
   `python -m flash.ambient --selftest` 61 (+ 6 mutations) ·
   `python benchmarks/trace_resume_check.py` 11 ·
   `python benchmarks/confidence_wiring_check.py` 35 ·
   `python benchmarks/subtle_premise_check.py` 52 ·
   `python benchmarks/p6_key_check.py` 28 ·
   `python benchmarks/confidence_tau_check.py` 7 ·
   `python benchmarks/checkpoint_resume_check.py` 35 ·
   `python benchmarks/lora_path_check.py` 33 (+ 15 mutants) ·
   `python benchmarks/dbg_band_check.py` 172 (+ 5 mutants) ·
   `python benchmarks/router_portable_check.py` 20 (+ 5 mutants) ·
   `python benchmarks/graph_perceive_check.py --sweep` 33 (+ 12 mutants) ·
   `python benchmarks/ts_perception_check.py --sweep` 47 (+ 13 mutants) ·
   `python benchmarks/ts_patch_check.py --sweep` 52 (+ 15 mutants) ·
   `python benchmarks/patch_landing_check.py --sweep` 65 (+ 28 mutants) ·
   `python benchmarks/session_check.py --sweep` 81 (+ 26 mutants) ·
   `python benchmarks/hint_ab_check.py` 14 (+ 8 mutants) ·
   `python benchmarks/portable_paths_check.py` 15 (+ 7 mutants) ·
   `python benchmarks/backend_free_check.py` 49 (+ 15 mutants) ·
   `python benchmarks/documented_commands_check.py` 8 (+ 5 mutants) ·
   `python -m flash.debug --suite` 32 ·
   `python -m flash.patches --suite benchmarks/tasks/edit_tasks.jsonl` 60 ·
   `python benchmarks/m0_bakeoff.py --dry-run` 20 reference solutions.
   **Total: 1429 selftest / end-to-end / premise checks + 20 oracle
   verifications (m0_bakeoff's 20 reference solutions, which are the only
   numbers in that 20 — the 6 ambient, 15 lora, 5 band, 5 router-portability,
   12 graph, 12 graph-perceive, 13 ts-perception, 15 ts-patch, 28 patch-landing,
   26 session, 8 hint-ab, 7 path-portability, 15 backend-free and 5 documented-command
   mutants are extra to both totals, 172 in all) = 1449 green, offline.**
   Two of this page's own numbers were wrong when R-7.16 moved them, and the way
   they were wrong is worth keeping: `backend_free_check` was still listed at 42
   checks and 10 mutants while the tree had already printed 44 and 11 (the
   package-only sweep re-run with `mlx`, `mlx_lm` and `mlx_vlm` unimportable, plus
   the control that proves the block fired), and the lane that refuses a row it can
   genuinely not run then put 4 more checks and 3 more mutants on the same vector —
   **48 / 14**, 1420 + 6 = 1426 checks and 167 + 4 = 171 mutants. The vector's own
   `--mutant` run printed the 14/14 and this page copied it. The first CI run on a real
   ubuntu runner moved that same row a third time, for a reason worth keeping in the
   sentence rather than in a commit message: the check that failed was demanding a
   blocker sentence only a machine that owns MLX may print. That bought one more check —
   both branches fed to the installer as fabricated probes, so neither word can be a
   tautology on the box that happens to print it — and one more mutant that hard-wires
   `proof`, which dies on exactly that check: **49 / 15**, 1420 + 7 = 1427 checks and
   167 + 5 = 172 mutants, and the re-read on that tree printed `checks 1427  oracle 20
   §6 total 1447  mutants 172`.
   That same ubuntu runner then moved a row that had nothing to do with a model, and it
   is the same bug wearing a different hat: a check written in one platform's grammar.
   `flash power`'s live probe asserted a memory size it only knew how to read from
   `sysctl hw.memsize`, so a box with a perfectly readable `/proc/meminfo` printed
   **21/22**; the fix is a source order (`sysctl`, then `/proc/meminfo`, then POSIX
   `sysconf`) and a line that names which one answered, and it bought two table rows —
   a real Linux dump and a dump with no `MemAvailable`, which must answer the total and
   refuse to guess the percentage: **22 → 24**. `flash.sandbox` was worse, because it
   did not print at all: its setup ended in `assert seatbelt() is True`, a sentence no
   machine without Seatbelt can satisfy, so the vector raised before its first `ck()`,
   printed no fraction, and the runner reported `want 34/34 got []`. Nine of its claims
   belong to a macOS mechanism, and dropping them would make the denominator a rumour,
   so each is asked in two arms — the enforcement claim where the kernel can refuse, and
   on an unconfined box the degradation claim: the jail named absent, the hostile write
   landing in a throwaway HOME instead of a real `~/.ssh`, the candidate passing 2/2
   because nothing stopped it, and no line reading green because a mechanism was missing
   rather than working. **34** on both arms, measured here in each (the Linux arm reached
   by pointing `SENTRY` at a path that does not exist, which is the state a Linux box is
   born in). 1427 + 2 = 1429 checks, 172 mutants unchanged, and the lane's own subtotal
   moves by the same two: 1299 → 1301 checks, 1319 → 1321 total. The degradation arm's
   two network candidates aim at a closed port on loopback instead of the documentation
   IP the confined arm uses, because on a box with no jail the fate of an outbound
   packet is the runner's own business — it can time out, refuse, or be answered by a
   proxy — and a row may not print a fraction that depends on which. The tree re-read
   whole on that fix: **37** `OK` and no `BAD` on `checks 1429  oracle 20  §6 total
   1449  mutants 172` + `matches SPEC §6 as written`
   (`benchmarks/results/battery_reread_r716f_20261004.log`, 23:56:10 → 00:21:08), then
   the lane's **35** `OK` with the same two rows named `REFUSED`, no `BAD`, on `checks
   1301  oracle 20  §6 total 1321  mutants 146` under `NOT a §6 re-read`
   (`benchmarks/results/battery_backendfree_lane_r716f_20261004.log`, 00:21:08 →
   00:44:03) — 1301 = 1429 − 128 and 146 = 172 − 26, its own banner's arithmetic. Both
   lanes print `flash power --selftest 24/24` and `flash.sandbox --selftest 34/34`, so
   the two rows that moved are green on a machine with the backend and on a machine
   without it alike.
   Four of the rows above were moved twice to get here, and one of those moves is a
   correction rather than an addition: this numbered list still carried the
   R-7.15b tree's `63` and two `48`s while the page's own totals paragraph said that
   run had printed `71/71`, `53/53 (+ 27)` and `49/49 (+ 17)`. The list added to 1331
   and the total said 1345 — two numbers on one page, neither wrong, both about a
   different tree. R-7.15e's pass updated the paragraph and forgot the list, which is
   exactly the drift `battery_reread.py` exists to catch except that the script reads
   its own table, not this page, so nothing was comparing them. Both now say what the
   tree prints.
   Every line below this page's sum is a number a run printed, and the sum itself is
   printed too: R-7.15f put 7 checks on `flash.patches --selftest` (**71 → 78/78**) and
   8 checks and 1 mutant on `patch_landing_check.py --sweep` (**53 → 61/61**, 28 mutants),
   and R-7.15g put 8 checks on `flash.harness --selftest` (**20 → 28/28**) plus 9 checks
   and 3 mutants on `session_check.py --sweep` (**49 → 58/58**, 20 mutants) — each
   measured on the edited tree, and `benchmarks/battery_reread.py`'s `CLAIM` was set to
   their sum **before** the whole-tree run started. That prediction was **one short**: it
   said 1376, and 37 OK lines add to 1377. The rows were right and the hand-sum was not
   (`1345 + 8 + 7 + 8 + 9`), so `CLAIM` moved to the tree's number rather than a row
   moving to the prediction, and the addition is now written next to `CLAIM` in the
   script. **The confirming re-run on the unchanged code printed it,** in **20 min 43 s**:
   `checks 1377  oracle 20  §6 total 1397  mutants 161` with **37** OK lines, no BAD
   line and its own `matches SPEC §6 as written: 1377 + 20 = 1397 green, offline (+ 161
   mutants)` (`benchmarks/results/battery_reread_r715fg_20260929.log`), started on AC at
   80%, rows 1, 8, 29 and 30 reading `28/28`, `78/78`, `61/61 (+ 28
   mutants)` and `58/58 (+ 20 mutants)`. The run measured the code tree; the prose and
   the published `site/` JSON moved after it, and the two gates that read those pages
   re-ran on the final surfaces at the same numbers they printed inside the run — the
   first file at that path is kept beside it as
   `battery_reread_r715fg_claim1376_20260929.log`, which is what the disagreement looks
   like when a run exits 1 on every vector green.
   The runs it moved on top of stay on the page. R-7.15e put 8 checks on
   `flash.patches --selftest` (63 → **71/71**,
   `patches selftest: 71/71 checks passed`), 5 checks and 3 mutants on
   `patch_landing_check.py --sweep` (**53/53**, `patch-landing mutants: 27/27 caught`
   in both lanes: `fresh process each` and `one process`), and 1 check and 1 mutant on
   `session_check.py --sweep` (**49/49**, `session mutants: 17/17 caught` in both
   lanes) — each of those three lines measured on the edited tree, and
   `benchmarks/battery_reread.py`'s `CLAIM` was set to their sum **before** the
   whole-tree run started. **That run has since printed it:**
   `checks 1345  oracle 20  §6 total 1365  mutants 157` with **37** OK lines and no
   BAD line, plus its own `matches SPEC §6 as written: 1345 + 20 = 1365 green, offline
   (+ 157 mutants)`, in **18 min 35 s**, on the tree this box was written for
   (`benchmarks/results/battery_reread_r715e_20260929.log`), started on battery at 53% and
   plugged into AC partway through —
   which is worth saying because `checkpoint_resume_check` is the line the charge gate
   can fail, and at 53% the governor was still at width 2, so it printed **35/35**
   legitimately rather than by luck. Those three vectors are its 8th, 29th and 30th OK
   rows reading `71/71`, `53/53 (+ 27 mutants)` and `49/49 (+ 17 mutants)`, with
   `ts_patch_check` holding line 28 at `52/52 (+ 15)`, unchanged. The whole-tree re-read
   before this one printed `checks 1331  oracle 20  §6 total 1351  mutants 153` in
   **18 min 42 s** on AC at 80%
   (`benchmarks/results/battery_reread_r715b_20260929.log`), on the tree one box
   earlier, with the same three lines at `63/63`, `52/52 (+ 15 mutants)` and
   `48/48 (+ 24 mutants)` and `session_check` holding line 30 at `48/48 (+ 16)`. The
   two re-reads before that printed
   `checks 1298  oracle 20  §6 total 1318  mutants 149`
   (`benchmarks/results/battery_reread_r715c7_20260929.log`) and `checks 1294  oracle
   20  §6 total 1314 mutants 147`. All five stay on the page rather than merging into one.
   Read those two
   numbers with care: CHECKS and TOTAL are different columns, and this page has
   been quoted wrongly by its own notes before — R-1.1b's checks count (1052) was
   exactly the total the page had claimed one commit earlier, and the number the
   notes then quoted (1072) is that same page's TOTAL, not its checks. Re-read by
   `python benchmarks/battery_reread.py`, which holds one line per item above,
   requires the exact fraction each one prints, sums checks/oracle/mutants
   separately, and fails if the tree's sum moves off this page's number. It
   exists because hand-summing this list produced a wrong total from output that
   looked clean twice: a `grep "checks passed"` once collected 214 of 316
   because `grammar` 47 and `debug` 55 print a bare fraction, and a grep for the
   last `n/n` on 2026-09-26 read lora_path_check's `14/14 mutants` as its
   checks and under-counted by 17. Both traps are why the counts below are the
   runs' own printed numbers.
   **One line is environmental, not logical**: `checkpoint_resume_check.py`
   refuses to run unless §34.1's governor offers tournament width ≥ 2, so on a
   busy box it exits with its own remedy ("put it on AC, let it cool, re-run") and
   the total cannot be read here at all. That is the battery failing closed rather
   than passing quietly — measured 2026-09-27, when it tripped at load 2.4/core
   while an unrelated process group held the machine — but it arrived as
   `printed []`, which named nothing, so a BAD line now echoes the run's last
   line. Read §6's total on a quiet machine.
   (Updated 2026-09-27, after R-1.1c: **+5** on `flash lsp-selftest` (**17 → 22**) —
   four checks on `symbol_hint`'s overflow behaviour and one that drives `loop.solve`
   to confirm the CLIPPED block is what the retry actually sees. The defect was
   R-1.3b's own cost probe walking into it: the budget test was a `break`, so the
   first ranked symbol too long for 1200 chars deleted the entire hint (1631 chars of
   `symbol_source` on this repo → a ZERO-char block, two shorter ranked symbols never
   reached). Hand-run mutation, `benchmarks/results/lsp_r11c_mutation_20260927.log`:
   the `break` restored, the shipped selftest run unmodified gives **18/22** — and
   stated at precision, the one overflow check that survives is the ceiling check,
   which passes vacuously on an empty block (`0 and 0`); it is a no-overshoot guard,
   not part of the defeat. **1027 → 1032** checks, mutants unchanged at 51, §6 total
   **1047 → 1052**. Re-read from the tree on a quiet box the same day: `battery_reread`
   prints `checks 1032  oracle 20  §6 total 1052  mutants 51` with all 29 lines on the
   OK list and `flash lsp-selftest 22/22` the only line that moved (raw witness
   `benchmarks/results/battery_reread_r11c_20260927.log`), pyflakes 0 findings. This is
   also what unblocks R-1.1b: until now a hint-OFF arm could
   come back empty for a reason nobody measured.)
   (Updated 2026-09-27, after R-1.1b: **+14 checks and +8 mutants** for
   `python benchmarks/hint_ab_check.py` (the frozen band's premise, certified at the
   seam `loop.solve` with a stubbed generator), **+6 checks and +3 mutants** on
   `python benchmarks/graph_perceive_check.py --sweep` (**27 → 33**, `+9 → +12`) for
   the per-block switches — three checks that each arm hides exactly its own block and
   nothing else, one that an arm with both hints off pays no AST parse at all, one that
   the flag reaches the seam it names, and one that the arm is written into the session
   so a trace states which arm produced it rather than relying on a command line
   someone remembers. **1032 → 1052** checks, mutants **51 → 62**, §6 total
   **1052 → 1072**. Re-read from the tree on a quiet box the same day: `battery_reread`
   prints `checks 1052  oracle 20  §6 total 1072  mutants 62` with all **30** lines on
   the OK list (raw witness `benchmarks/results/battery_reread_r11b_20260927.log`),
   `hint_ab_check 14/14` and `graph_perceive 33/33` the two lines that moved, pyflakes
   0 findings. The live A/B those switches exist for is a **nil**, and it is written up
   as one under R-1.1b rather than dropped here: the §6 count is the instrument's
   greenness, which is the only thing this battery can certify about a measurement
   that found nothing.)
   (Updated 2026-09-27, after R-7.5's first clean-clone run: **+1 check** on the
   `portable_paths_check.py` line, **14 → 15**, and the page total
   **1066 → 1067** / **1086 → 1087**. Re-read from the tree the same day:
   `battery_reread` prints `checks 1067  oracle 20  §6 total 1087  mutants 69` with
   all **31** lines on the OK list, pyflakes 0 findings. The check is not
   decoration; the run that added it caught the hole. Inside this project's own
   unpacked sdist — a tree with no `.git` sitting under a temp directory, which is
   what a download produces — the vector first printed **9/14**, five gates saying
   `0 tracked files scanned` because the file list came from `git ls-files` (now
   `_fs_tree`, which lists the same tree a slower way and prints which of the two
   it used), and after that fix **15/15 + 7/7**. Between those two prints one
   mutant escaped outright: `abs_back` put that checkout's real fixtures path back
   into a corpus line and **all 14 gates stayed green**, because `HOST_PATHS` is a
   *prefix* list and `/tmp/…` is not one of its prefixes. Where a checkout happens
   to live has never been the property R-7.4 is about, so the 15th gate is on the
   shape instead: every corpus `sys.path.insert` must name a token the harness
   expands — `<REPO>` or `<TMPDIR>` — measured over **99 bootstraps across 24 task
   files**, none of which carries a literal directory.)
   (Updated 2026-09-27, when R-7.6, R-7.7 and R-7.8 closed: **+30 checks and +5
   mutants** for `python benchmarks/backend_free_check.py` and **+7 checks and +4
   mutants** for `python benchmarks/documented_commands_check.py`, which is
   **1067 → 1104** checks, mutants **69 → 78**, §6 total **1087 → 1124**. Re-read
   from the tree the same day: `battery_reread` prints
   `checks 1104  oracle 20  §6 total 1124  mutants 78` with all **33** lines on the
   OK list, pyflakes 0 findings
   (`benchmarks/results/battery_reread_r76_20260927.log`).
   Two things this re-read caught about itself, both worth keeping. The first CLAIM
   was set by arithmetic before the run (`checks 1103 … mutants 76`) and the re-read
   printed **1103 / 77** — the mutants number was wrong by the four documented-command
   gates' own mutant line, which the BATTERY entry had carried as `3` while the
   vector printed `4/4`. The gate said so out loud instead of the run being re-worded,
   the entry moved to `4`, and the number published here is the tree's second print,
   not the first prediction. The second: R-7.7's clause is "26 of the 26 modules
   import", and the vector as first written asserted only the two that used to fail —
   a sweep, not an enumeration, is what stops a *new* module growing a top-level
   backend import and still printing green. That gate was added, planted-file mutant
   and all, which is why the line is 30 and not 29.)
   (Updated 2026-09-27, when R-7.10c closed: **+5 checks and +2 mutants** on
   `python benchmarks/backend_free_check.py` (**37 → 42**, **8 → 10**) — the sweep
   that measures the refusal table instead of trusting it, its spine gate, its
   machine-state gate, and the two mutants that plant an unguarded data-reading
   selftest and drop an entry from the table. **1114 → 1119** checks, mutants
   **83 → 85**, §6 total **1134 → 1139**, with the line count unchanged at **33**
   for the same reason as last time: the new gates went into a vector that already
   had a line. The sweep is what found the two product bugs the table of four had
   missed — one selftest resolved its task file against the caller's working
   directory and so raised `FileNotFoundError` on a wheel install, and another died
   three frames away from the absent directory with `ValueError: substring not
   found`, an error that names no path at all. Both now anchor on the package and
   refuse with rc 2 through the table, which is why the table has six keys.
   The sweep's own first mutant printed **0 checks failing**, and the reason is in
   R-7.10c: a module with no executable tail answers `python -m … --selftest` by
   importing and exiting 0, which the gate had been counting as a pass. The spine
   gate now requires every swept module to have been dispatched.
   This run's re-read printed `checks 1119  oracle 20  §6 total 1139  mutants 85`
   with all **33** lines on the OK list, on a tree that took **15 min 9 s** to
   re-read — the sweep is not free, and README carries the measured figure rather
   than the earlier estimate (raw witness
   `benchmarks/results/battery_reread_r710c_20260927.log`, which carries 0 host
   paths and so leaves `RECORD_RESIDUE` at 411.)
   (Updated 2026-09-28, when R-7.11 and R-7.11b closed and R-7.5's fresh-clone vector
   was re-run: **+0 checks, +0 mutants, +0 lines.** The landing page added a collector
   and an exporter under `benchmarks/` and neither one is a §6 vector, so the correct
   result of re-reading the battery after that work is an identical print, and that is
   what it was: `checks 1119  oracle 20  §6 total 1139  mutants 85` with all **33**
   lines on the OK list and no FAIL line (raw witness
   `benchmarks/results/battery_reread_r711_20260928.log`, which also carries 0 host
   paths, so `RECORD_RESIDUE` stays **411** and only the record-file count moved to
   171). A changed total is not the signature this project looks for when it wants to
   know a pass did something; an unchanged one, on a pass that touched only a page, is
   what lets the page's numbers keep citing the battery.)
   (Updated again the same day, when R-7.5's second clause was measured for real and
   its mislabelled witness was corrected: **+0 checks, +0 mutants, +0 lines**, and
   for once the reason is the finding rather than an excuse. The new driver is not a
   §6 vector — it builds an sdist, installs it into a throwaway venv and runs the
   **existing** 33 lines inside the download — so the battery it exercises cannot move
   a count, and it printed the identical one: `checks 1119  oracle 20  §6 total 1139
   mutants 85`, **33/33**, in **13 min 2 s** against the checkout's own **15 min 9 s**
   for the same tree state (witness
   `benchmarks/results/r75_sdist_battery_20260928.log`, 0 host paths, so the floor
   holds at **411** while the record-file count moves to **172**). What did change is
   the provenance of a published artifact: `battery_reread_r75_20260928.log` had been
   committed as the fresh-clone §6 print and is the checkout's R-7.10c print with one
   extra line, so R-7.5 now cites the run above and `docs/portability.md` says what
   that file actually is. Nothing in §6's totals moved, which is the correct outcome
   for a pass whose whole content is "the download re-runs what the checkout
   printed".)
   (Updated again the same day, after the seven tracked docs carrying that correction
   were re-read by the battery: **+0 checks, +0 mutants, +0 lines** —
   `checks 1119  oracle 20  §6 total 1139  mutants 85`, **33/33**, no FAIL line, in
   **15 min 12 s** (witness `benchmarks/results/battery_reread_r75b_20260928.log`, 0
   host paths, so the floor holds at **411 over 36** while the record-file count moves
   to **173**). That figure is the redirect file's own birth and last-write timestamps
   (04:18:12 → 04:33:24), and the file itself says nothing about either: it is a shell
   redirect, the same self-erasing shape named two entries above. So it is credited to
   no driver and labelled for what it is — a re-read of **this checkout**, launched from
   it, after the doc edits above had landed. What it does establish is the shape
   R-7.5's clause-2 run was supposed to have: the docs changed, the totals did not, and
   the two gates that read prose —
   `documented_commands_check.py` **8/8 (+ 5 mutants)** and `portable_paths_check.py`
   **15/15 (+ 7 mutants)** — stayed green on the edited text rather than being retyped
   to match it.)
   (Updated again the same day for R-7.5's **first** clause: **+0 checks, +0 mutants,
   +0 lines**, and again the reason is the finding. `benchmarks/r75_fresh_install_check.py`
   was rewritten to stop interrogating this checkout through a `python -m` child that
   imported the package from its own cwd, and the rewrite is not a §6 vector either —
   it builds an sdist and a `git clone`, makes two throwaway venvs and asks their console
   scripts. It printed **9/9 shapes** (`benchmarks/results/r75_clause1_20260928.log`,
   **0** host paths, so the floor holds at **411 over 36** while the record-file count
   moves to **174**). What it changes is not a total but a sentence: the run now backs
   `flash doctor`'s two different exit codes for two different install shapes, which is
   the claim R-7.5's clause 1 makes in this file and in `README.md`.)
   (And then the whole battery was re-read from this checkout with that rewrite committed:
   **+0 checks, +0 mutants, +0 lines**, `checks 1119  oracle 20  §6 total 1139  mutants
   85`, **33** OK lines and no FAIL line, in **15 min 7 s** (witness
   `benchmarks/results/battery_reread_r75c_20260928.log`, 0 host paths, floor **411 over
   36**, record files **175**). Again a shell redirect rather than a driver, so its wall
   clock is the file's own timestamps and it credits itself no provenance. Three
   identical totals now exist — this checkout, the unpacked sdist, this checkout again
   after the install driver stopped answering about the wrong copy — and that agreement
   is the point: a total that only one tree has ever printed is a moment, not a property.)
   (Updated again the same day, for **R-7.12** and the four documents its run made false:
   **+0 checks, +0 mutants, +0 lines**, and the first re-read did not print the totals at
   all — which is the interesting part of this entry. `benchmarks/market_compare.py` is not
   a §6 vector, because it needs weights, so the battery it exercises cannot move a count;
   but the tree it left behind failed two lines. `benchmarks/checkpoint_resume_check.py`
   printed nothing, naming its own cause: *the tournament arm needs the governor's width
   >= 2 and this machine offers 1 (free memory 4.8GB < 6.9GB needed)* — another project's
   14.8 GB model was resident for the whole run. And
   `benchmarks/portable_paths_check.py` fell to **13/15** because `market_compare.py` had
   written its own marker list, so the file whose job is keeping host paths out of a
   published witness was itself carrying two of them as literals. The gate was not widened
   and the sentence was not reworded: the driver now imports the marker list from
   `portable_paths_check.py` and spells no prefix in its own source. That is exactly the
   asymmetry the exemption exists to enforce — the only forgiven line is the one that
   declares the list, in the file that declares it. The re-read after that fix printed
   `checks 1119  oracle 20  §6 total 1139  mutants 85`, **33** OK lines, no FAIL line, in
   **18 min 41 s** (witness `benchmarks/results/battery_reread_r712_20260928.log`, 0 host
   paths, floor **411 over 36**, record files **179**) — the slowest of the four identical
   prints, on the same resident-model box, and the number is quoted with that condition
   rather than smoothed to the 15 minutes the unloaded machine takes. Four trees now print
   the same totals; the cross-tool table that this pass added is in
   `benchmarks/results/market_compare_20260928.log`.)
   (Updated 2026-09-28, when R-1.4's TypeScript pass arrived: **+47 checks and +13
   mutants** on a brand-new vector, `python benchmarks/ts_perception_check.py --sweep`
   — **1119 → 1166** checks, mutants **85 → 98**, §6 total **1139 → 1186**, and the
   line count moves for the first time in five passes: **33 → 34**, because a second
   language is a new thing to verify rather than another gate inside a vector that
   already had a line. The re-read printed `checks 1166  oracle 20  §6 total 1186
   mutants 98` with **34** OK lines, no BAD line, in **16 min 49 s** (witness
   `benchmarks/results/battery_reread_r14_20260928.log`). The prediction in
   `CLAIM` was set by arithmetic before the run (`checks 1166 … mutants 98`) and the
   tree matched it, which is the only direction this project accepts: the number is
   guessed first and then printed. Three of the 47 checks fake the grammar's absence
   and gate what the tool says when it is missing — one sentence naming
   `pip install 'flash-coder[ts]'`, the Python pass still answering, and the refusal
   counted as a blind spot so a `summary()` cannot read as complete — and two more
   gate what the grammar refuses to guess (a file it cannot parse still gets a node
   and says so, and cannot poison its neighbours' edges). One of the 13 mutants is
   not about TypeScript at all: it
   makes `graph.build()` index TypeScript by default, which would silently redefine
   every Python figure this page has published. The Python default still prints 44/44
   and the graph's published 4446 nodes / 24574 edges are unchanged — the mixed build
   adds 103 nodes and 240 edges only when `--lang py,ts` is asked for.)
   (Updated again the same day, when R-1.4's **patch arm** learned the second
   language — the gap that entry filed as (c): **+44 checks and +13 mutants** on a
   second brand-new vector, `python benchmarks/ts_patch_check.py --sweep` — **1166 →
   1210** checks, mutants **98 → 111**, §6 total **1186 → 1230**, lines **34 → 35**,
   again because a second thing the tool can now *do* is a new thing to verify rather
   than another gate inside a vector that already had a line. The re-read printed
   `checks 1210  oracle 20  §6 total 1230  mutants 111` with **35** OK lines, no BAD
   line, in **15 min 18 s** (witness `benchmarks/results/battery_reread_r14b_20260928.log`,
   its wall clock taken from the file's own timestamps, since a shell redirect credits the
   run no provenance),
   against a `CLAIM` again written by arithmetic before the run started. What the 44
   hold: an address dispatches on the file it names, and the empty path
   `flash.graph` passes stays on `ast`, which is why `flash.patches --selftest` is
   still **46/46** and `--suite` still **60/60** on this tree; a TypeScript span is
   read off the same `_declarations` walk the graph's node comes from, checked symbol
   by symbol over a fixture file and again after a real `build()`; a replacement must
   keep the symbol's name *and* its `export` keyword; a replacement that does not
   parse is refused with the grammar's own line and marker rather than Python's
   `invalid syntax`; a `tsx` fence is read as a patch and a fenced log line is not;
   and two of the checks drive `loop._solve_edits` with `_generate` and
   `diagnose_files` stubbed, because that arm's `ast` pass is where a false syntax
   error would have cost a retry. One of the 13 mutants is not about the new
   language at all: it routes every file to the TypeScript grammar, which would
   redefine every published Python figure — the same trap its perception counterpart
   tests on the other side of the seam. What this
   does NOT buy is R-1.4's (d): nothing here *verifies* a TypeScript edit — a `.tsx`
   patch that parses is accepted on the strength of a parse, because
   `diagnose_files` still has no `node`/`vitest` runner behind it, and the whole-file
   control arm is still Python.)
   (Updated 2026-09-29, when R-3.2's clause 3 arrived — the write-back: **+40 checks
   and +22 mutants** on a brand-new vector, `python benchmarks/patch_landing_check.py
   --sweep` — **1210 → 1250** checks, mutants **111 → 133**, §6 total **1230 → 1270**,
   lines **35 → 36**, because making the tool *write to your tree* is a new thing to
   verify rather than another gate inside a vector that already had a line. The
   re-read printed `checks 1250  oracle 20  §6 total 1270 mutants 133` with **36** OK
   lines and no BAD line, in **16 min 27 s** on battery at 74% (witness
   `benchmarks/results/battery_reread_r32c3_20260929.log`, 0 host paths), against a
   `CLAIM` of `checks 1250 oracle 20 total 1270 mutants 133` written by arithmetic
   before the run started and matched. The 22 are 12 on a copy of `land` with one
   clause switched each, 6 on a copy of the command's write-back path
   (`cli._land_edits`), and 4 on the real seams — `cli._rel_to`, `cli._apply_guard`,
   `loop._solve_edits` and `cli.build_parser`;
   an `agree()` check compares the copies against the shipped functions over the same
   scenarios first, so a green mutant count cannot be a green count on a stub. The
   vector also caught its own instrument twice: the counts `land` returns were diff
   *operations*, so a 4-line module rewrite printed `+2 -1 lines`, and the oracle
   protection mutant was written into the wrong copy, where it was never read and so
   escaped. Neither is visible from the shipped code's exit status.)
   (Updated 2026-09-27, when R-7.9, R-7.10 and R-7.10b closed: **+2 checks and +1
   mutant** on `python benchmarks/lora_path_check.py` (**31 → 33**, **14 → 15**) for
   `--dry-run`, **+1 check and +1 mutant** on
   `python benchmarks/documented_commands_check.py` (**7 → 8**, **4 → 5**) for the two
   packaging files, and **+7 checks and +3 mutants** on
   `python benchmarks/backend_free_check.py` (**30 → 37**, **5 → 8**) for R-7.10's four
   refusals, its table-shape guard and R-7.10b's `site-packages` answer —
   **1104 → 1114** checks, mutants **78 → 83**, §6 total **1124 → 1134**, with the
   line count unchanged at **33**, because every one of these gates went into a vector
   that already had a line of its own.
   The first re-read printed `checks 1099  oracle 20  §6 total 1119  mutants 75` and
   named its own cause on the line above it:
   `BAD benchmarks/documented_commands_check.py want 8/8 got ['7/8']`. The new
   packaging-era gate was failing on **SPEC's own paragraph about the non-existent
   commands** — the passage that records the `vision` and `sandbox` lies cites them in
   the exact shape the collector reads as a citation, so the page documenting a
   dangling command was itself dangling two. The prose was rewritten and the gate was
   left alone: the two dead subcommands are now named as bare words, and the reason is
   written into R-7.8 (a backticked `flash …` span means "run this" to a reader and to
   the scanner alike, and this project will not hold a gate that cannot tell a
   recommendation from a quotation, so quotations are written where they cannot be
   copied). The same re-read then failed a **second** time, on a different vector,
   for the **same** shape of reason: `BAD benchmarks/portable_paths_check.py want
   15/15 got ['13/15']`, and the failing gate was the one that says nothing this
   repo tells a user to run may carry a host path — the paragraph recording
   R-7.10b had just spelled a Homebrew prefix to explain what the old code built.
   `SPEC.md` is inside that scan, and its only exemption is the marker declaration
   lines of the vector itself, so the fix was again to re-word the page and leave
   the gate alone: the prefix is now named rather than written, exactly as the
   vector's own comment does it. Third print, on a quiet tree with nothing else
   editing it: **`checks 1114  oracle 20  §6 total 1134  mutants 83`**, all **33**
   lines OK, pyflakes 0 findings, the two collectors still reading 25 commands in
   150 citations across 15 documents (raw witness
   `benchmarks/results/battery_reread_r710_20260927.log`, which carries 0 host
   paths and so leaves `RECORD_RESIDUE` at 411.)
   (Updated 2026-09-27, after R-7.4: **+14 checks and +7 mutants** for a new vector,
   `python benchmarks/portable_paths_check.py`. No existing line moved, and that is
   the finding: `flash debug --selftest` stayed **55/55** and `flash debug --suite`
   stayed **32/32** across the tracer-ordering fix, because the corpus those two run
   against carries 0 `sys.path` lines — so what was broken was a seam neither of them
   exercises. **1052 → 1066** checks, mutants **62 → 69**, §6 total
   **1072 → 1086**. Re-read from the tree on a quiet box the same day:
   `battery_reread` prints `checks 1066  oracle 20  §6 total 1086  mutants 69` with
   all **31** lines on the OK list (raw witness
   `benchmarks/results/battery_reread_r74_20260927.log`; the vector's own table at
   `benchmarks/results/portable_paths_r74_20260927.log`), pyflakes 0 findings. The
   re-read also had to catch its own instrument: `docs/portability.md`'s
   `RECORD_RESIDUE` is asserted `≤` the live count, and committing this vector's
   witness log raised that count by 4 — a residue number that excluded the file
   measuring it would have been the same mistake one layer up. The re-read's own
   witness then added a 160th record file carrying **0** occurrences, which is why
   the published floor stayed exactly 395 and why `portable_paths_check.py` was
   re-run **after** it: **14/14 + 7/7** with the new file on disk.)
   (Updated 2026-09-27, when `HOST_PATHS` gained a third marker and the vector
   gained a lock. The floor moved **395 → 403** and the file count **31 → 33** on
   one explained delta: two run traces whose failed-`verify` `err` text quoted a
   Homebrew-prefixed interpreter, carrying **0** occurrences under the old two
   markers and 3 + 5 under the new one. Storing this pass's two own witnesses moved
   it again, **403 → 407 → 410** across **33 → 34 → 35** files of 164 — four
   occurrences from the reproduction log's gate labels, three from the table of the
   run that verified the lock, which prints all three markers where pass 1 printed
   two.
   `python benchmarks/portable_paths_check.py` re-run on the widened scan:
   **14/14 + 7/7**. The second change was forced by a run that printed
   `defeated 0 mutants, not 7` with all 14 gates green: the mutants write real
   bytes into three corpora and restore them, so two processes in one checkout
   hand each other half-finished files — measured in a throwaway clone at **5/7**
   and **6/7** with **14/14** both times, and it is not only a wrong report,
   because an interleaved snapshot/restore of `docs/portability.md` left the group
   name its own `doc_silent` mutant had erased sitting on disk afterwards. The
   vector now takes a `flock` per checkout and exits 2 rather than running twice:
   with the lock held a second run refuses and names the holder, with
   `--exclusive` it proceeds, alone it defeats **7/7**.)
   (Updated 2026-09-27, after R-1.3b's injection: **+27 checks and +9 mutants** for
   `python benchmarks/graph_perceive_check.py --sweep` (`--sweep`, not the bare run,
   because several of its mutants live in the graph CACHE and how many checks one
   fails depends on whether the process already built a graph — 2/2/7/2/14/2/7/2/2
   in a single process against 2/1/6/1/13/2/6/1/1 in nine; the claim is that each
   mutant is caught by its OWN named check in both orders, and `--sweep` fails if
   the two ever disagree). **1020 → 1027**, mutants **42 → 51**. Two things this
   vector caught that its clause did not expect: assembling the hints by nesting fed
   the graph the text the source hint had already quoted, so one retry showed two
   different symbol sets for one failure (`loop._perceive` now ranks the bare error
   once, and mutant `nested` keeps that from regressing), and the same probe found
   `lsp.symbol_hint` returning NOTHING when the top-ranked symbol's source exceeds
   `max_chars` — measured on this repo, `symbol_source` at 1631 chars makes the
   whole block vanish although a 72-char symbol ranked beside it would have fit.
   That second one was booked as R-1.1c rather than folded into this commit, and the
   paragraph above is its fix. Re-read from the tree the same day, quiet box, before
   R-1.1c landed: `battery_reread` printed `checks 1027  oracle 20  §6 total 1047
   mutants 51` with all 29 lines on the OK list (raw witness
   `benchmarks/results/battery_reread_r13b_20260927.log`), pyflakes 0 findings.)
   (Updated 2026-09-27, after R-1.1's correction: **+3** on
   `flash lsp-selftest` (**14 → 17**) — the three checks that drive `loop.solve`
   and read the retry prompt back, instead of calling `lsp.symbol_hint` directly
   the way the other 14 do. That is the whole point of the number: the 14 were
   green and true, and the seam that fed their helper to the model was dead, so a
   vector can print exactly its claimed fraction and still not test its clause.
   **1017 → 1020**, mutants unchanged at 42, because this fix was mutation-checked
   by hand rather than by a sweep: 15/17 with the old line put back, in
   `benchmarks/results/lsp_wiring_mutation_20260927.log`.
   `benchmarks/hint_live_audit.py` is deliberately **not** a line here — its
   denominator is the trace corpus, which grows with every live run, so it can
   never print a fixed fraction; it is a two-sided gate that exits 1 when a
   pre-fix prompt carries the hint and when a post-fix one does not.)
   (Updated 2026-09-27, after R-1.3's knowledge graph: **+44** for
   `python -m flash.graph --selftest --mutants` — the clause's named answer (the
   callers a change would break, with the file/line/text that proves each one) plus
   the two halves that are easy to fake and are therefore checked as refusals: a
   name matching two symbols must become NO edge, and yet still be counted in
   `blind_spots()`; and a symbol the graph does not have must come back ABSENT with
   nearest addresses, never as an empty answer that reads clean. Its 12 mutants join
   the extra-to-both-totals sum (**30 → 42**), so **973 → 1017**, moving this line
   alone. Re-read from the tree the same day: `battery_reread` prints
   `checks 997  oracle 20  §6 total 1017  mutants 42` with all 28 lines on the OK
   list (raw witness: `benchmarks/results/battery_reread_graph_20260927.log`, the
   first §6 re-read whose full output is kept), pyflakes 0 findings. One battery
   detail worth writing down, because it nearly double-counted: graph prints its
   mutant sweep with lowercase `ok MUTATION:` markers *and* a `12/12 caught`
   summary, and `mutant_count` counts only the summary — had it matched ambient's
   uppercase marker shape as well, graph would have entered the sum twice.)
   (Updated 2026-09-27, after R-3.2's narrowing: **+9** in
   `flash.patches --selftest` (37→46) — an over-wide address must land
   byte-identical to the wide splice it replaces, a count-changing run before
   another must still splice in the right place (this is the check that made the
   top-down-splice mutant visible), a verbatim re-type must write nothing, and a
   sibling the model DID change must still be spliced and must still score.
   `--suite` stays 60/60 and the whole battery's total moves with this line alone
   (964→**973**). Re-read from the tree the same day, on AC at 0.33 load/core:
   `battery_reread` prints `checks 953  oracle 20  §6 total 973  mutants 30` with
   all 27 lines on the OK list, and pyflakes reports 0 findings.)
   (Updated 2026-09-27, after R-9.2's sandbox: **+34** for
   `flash.sandbox --selftest` — the hostile-candidate vector in four refusal
   shapes, the ranking half of the clause (`1/2`, and a `GOT/WANT/ERROR` line for
   the retry), the two Seatbelt profile traps, the rlimit witnesses from inside the
   sandbox, the runtime spy that requires all five execution seams to spawn
   through `sandbox.run`, and three clauses pinning that the reaper would leave a
   real key alone — 12 mutants of its own, extra to both totals, so
   **930→964**. Re-read from the tree once the box quieted (2.8/core): `battery_reread`
   prints `checks 944  oracle 20  §6 total 964  mutants 30` and matches this page,
   with `flash.sandbox --selftest 34/34` and `checkpoint_resume_check 35/35` both on
   the OK list. It is worth recording that this run was NOT available on the first
   try: at 5.2/core load the checkpoint arm refuses its tournament clause, the tree
   printed 909 + 20 = 929, and `battery_reread` reported the gap as a mismatch
   against this page instead of accepting it — which is the whole reason the total is
   a re-read and not a sum. Before the sandbox, after the two live arms on the wide
   instrument and the probe-child fix: +4 in `flash.confidence` (25→29 — an answer
   that crashes on import is reported as `answer does not import`, and no reason
   string can carry a newline or a temp path any more) and +7 for
   `benchmarks/confidence_tau_check.py` (the coverage-threshold sweep over both
   arms' recorded numbers, gated on reproducing the shipped predicate's recorded
   offer on 59/59 answers at each tier), so 919→930; before that, earlier the same
   day, after R-2.3's instrument was bounded and
   widened: +4 in `flash.confidence` (the probe-size budget and the planner that
   enforces it), +5 in `confidence_wiring_check` (a recall or false-offer clause
   whose denominator is 0 now prints NOT MEASURABLE instead of a ratio that reads
   as a result, and the mutation that mutes the refusal is caught by exactly 3
   checks), +15 in `p6_key_check` (the same eight clauses now audited over BOTH
   instruments, plus the two size bounds each proven by removing them), so
   895→919; before that, the same day after the learned router's portability fix:
   +20 for `router_portable_check` — the embedding cache keyed by the weights
   that produced it, one model's bundle refusing another model's width, and
   serve-time pooling matching fit-time pooling — with 5 mutants of its own (a
   cache that ignores the model, a bundle that keeps no labels, a score that
   trusts any width, a serve path that pools mean, a cache path that is optional
   again), so 875→895; before that, the same day after the R-4.3 band
   instrument: +172 for `dbg_band_check` — the shape of the 30-task suite, the
   two-bug premise of its 13 seeded rows, and that re-generating it is
   byte-identical while the ledger grows — with 5 mutants of its own, so
   703→875; before that, the same day after
   R-7.2's live ambient arm: 53→61, of which 6 are mutations that break each
   guarantee on purpose — the git allowlist, the worktree containment guard,
   the oracle itself, the additive-line clause, the newline carrier, and the map
   name matcher — and must be caught; before
   that 622, re-read after
   R-6.4's offline half: +36 the dataset law and the slice loop, +31 the LoRA
   path vector with its 14 mutants, and `jobs` 14→20 with the LoRA gate's
   decision; before that 549, re-read after
   R-5.3: +31 checkpoint storage, +35 the real-`kill -9` recovery vector; before
   that 503, re-read after
   R-2.3: +25 confidence, +35 wiring, +52 seeded-suite premise, +28 key premise;
   before that 387, after R-3.3: harness 12→20 with the `score()` ranking checks
   and the new `tourney` line; and before that 202, when the oracle and the patch
   protocol had no batteries of their own).
   (The `jobs` line was labelled `learn` until 2026-09-26: `flash learn
   --selftest` dispatches into `flash.jobs`, and `flash/learn.py` has no
   battery of its own. Same 14 checks, wrong owner — the mislabelling made the
   battery look like it covered the router's training code when it covers the
   background scheduler.)
   (`web` was the one battery that ran ONLY through `flash.cli`: `python -m
   flash.web --selftest` had no `__main__` guard, so it exited 0 printing
   nothing — 9 checks silently dropped from a re-read that trusted its own exit
   code. Re-reads here must check the printed count, not `rc`. Guarded
   2026-09-26; every other battery already ran under both forms.)
   (A re-read that greps for "checks passed" silently drops two lines: `grammar`
   and `debug` print a bare `47/47` / `55/55`. The 2026-09-26 re-read first
   collected 214 from twelve commands because those two matched nothing and read
   as blank rather than as failures — the same class of hole as `web`'s, reached
   from the capture side instead of the exit-code side.)
   (Updated 2026-09-28, when R-7.5's clause 2 was re-measured on **both install
   shapes** and the `dev` extra gained `flash-coder[ts]`: **+0 checks, +0 mutants,
   +0 lines**, and the reason is the same as the first clause-2 entry's — the driver
   is not a §6 vector, it builds a tarball and a venv and runs the **existing** 35
   lines inside them. What it printed there is what this page quotes, and it is the
   only battery print of this tree state: shape A `checks 1119  oracle 20  §6 total
   1139  mutants 85` over **33** of 35 with two named grammar refusals, shape B
   `checks 1210  oracle 20  §6 total 1230  mutants 111` at **35/35** with the battery's
   own `matches SPEC §6 as written`
   (`benchmarks/results/r75_sdist_battery_shapes_20260928.log`, 0 host paths, floor
   **411 over 36**, record files **188**). **No new checkout-side battery re-read was
   taken for this pass**, and the entry says so rather than implying one: the edits were
   prose plus one check description, so the three gates that read prose were re-run
   individually on the edited tree — `backend_free_check.py` **42/42 + 10/10**, whose
   sweep now prints `SWEEP 27/27` because `flash/lang_ts.py` is a submodule and the
   denominator is the package, `portable_paths_check.py` **15/15 + 7/7**, and
   `documented_commands_check.py` **8/8 + 5/5**, whose collector counted **184**
   citations against the 150 an earlier pass printed. The totals those three lines
   contribute to §6 are unchanged, and the next full re-read will carry this pass with
   them.)
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
| P0 | `git init` + baseline commit | **DONE 2026-09-25** (`0ea2798`, 39 commits since; `git push` stays user-gated) — unblocked I-1, R-7.2, M15 | 5 min |
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
| M16 24h chaos | needs a 24h window | offline kill/resume checks (11/11 suite-granular + 31/31 frame storage + 35/35 task-granular recovery, each with a real `kill -9`) |
| M17 feel test | needs 10 developers | dogfood transcript discipline |
| R-6.4's weights arm (first run measured) | the flywheel's own data law: the mined rows are tasks the current tier already solves, so a pass rate on them cannot rise (base 12/12 in-distribution). Not a compute or AC-idle shortage — a dataset whose negatives are what the router fails | a training pass whose rows are failures-with-repairs, or the gate re-aimed at skills/memory where headroom is measured |
| Phase-1/2 training, LoRA | hours of compute + AC idle windows | `jobs.py` gate shipped and mutation-checked; one 48-step fit run end to end, paused/resumed under a real `kill -9` |

## 10. Judgment calls in this spec the user may want to overrule

1. **G2 target 78%** (from 63%) is a guess bounded by measurement, not by
   evidence — if it is wrong it should be corrected from the suites, not argued.
2. **R-1.4's second language** was unselected by design; on 2026-09-28 it was
   selected — **TypeScript**, by a file census of the tree the tool indexes
   (the 21 files `flash.lang_ts.ts_files('.')` prints, against 0 `.sql`/`.db`),
   because the ledger the
   requirement named as the evidence source contains 0 rows for either candidate
   and so cannot discriminate. The call stays overrulable, and the override is
   cheap: `flash/lang_ts.py` is the only module that knows the language, and
   `graph.LANGS` is the list that names it. SQL remains unimplemented — not
   deferred for lack of will, but because nothing in this repo is written in it.
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
   *Update 2026-09-27, after `narrow` shipped: the ambiguity is smaller than it
   looked, and the gate is still unmet for a reason that is NOT the ambiguity.
   6 of the 7 lines were the tool regenerating an unchanged sibling, and that
   shape no longer exists — the splice writes only the runs whose bytes differ.
   Re-measured on the same 10 tasks: clause 2 is **1 line**, and it is e09's
   model-written `self._items.sort()` inside `TaskQueue.push`, a member the
   request never named, on a task the arm failed anyway. Reading (b) scores that
   0; reading (a) scores 1. So the choice is now exactly "may a model solve the
   requested behaviour by changing a sibling member?" — nothing about it depends
   on the tool's width any more, and the mechanism's own contribution is 0 on
   both readings. I have still not switched the gate to (b).*
