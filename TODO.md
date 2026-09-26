# Flash Coder — TODO (atomic, derived from `SPEC.md`)

Rules for this file:
- A box is checked **only** after its vector has been *run* in a terminal this
  session (or is a recorded live arm in PLAN Appendix A with a date).
- `[V]` = verification-only task. `[B]` = build task. `[L]` = needs a live model
  run (cost printed in the commit note).
- Order is SPEC §7's priority order. Do not reorder without a reason in the log.

---

## P0 — Reversibility (I-1)

- [x] [B] `git init` + `.gitignore` + baseline commit of the measured tree
      → vector: `git status` clean, 72 files, no `.venv` in the index.
      *Done 2026-09-25: commit `0ea2798`.* Unblocks I-1, R-7.2, M15.

## H — Clean build and battery honesty (cross-cutting, runs after every change)

- [x] [V] `python -m pyflakes flash/*.py benchmarks/*.py` → **0 findings**
      (SPEC §6.1b). Nine pre-existing warnings removed 2026-09-26: dead imports
      in `grammar`/`learn`/`power` and three benchmark scripts, one placeholder-less
      f-string block in `vision`, and — the only one that was a real defect —
      **`ConstrainedSampler.finish` was defined twice** in `flash/grammar.py`
      (47/47 stayed green because the two bodies were identical, so the shadowing
      was invisible; a future edit to one of them would have been silently dead).
      `benchmarks/tasks/*_test.py` is out of scope on purpose: a test file there
      is a *fragment* whose names come from the candidate code the harness
      prepends, so pyflakes' "undefined name" is its correct state.
      Verified with the whole battery after the edits, not just the lint:
      harness 12 · lsp 14 · power 22 · jobs 14 · trace 30 · web 9 · grammar 47 ·
      patches 37 · debug 55 · resume 11 · debug premise 32 · edit premise 60 ·
      m0 dry-run 20 → 363 green.
- [x] [B] SPEC §6's offline total recomputed from the tree, not remembered:
      it said **222** while `flash.patches` (37), `flash.harness` (12) and both
      suite premises (32 + 60) had already shipped — a stale count is a claim
      about coverage that no longer matches the code. Now **363**, each line
      named with its command. README's battery gained the harness line and the
      lint gate; `flash/__init__.py`'s selftest list names `flash.harness` and
      says where `flash.web`'s battery actually lives (no `__main__`; it runs
      through `flash web --selftest`).
- [x] [V] `flash trace --selftest` is 30/30, but README said 29 — the count was
      written before a check was added. Doc numbers are re-read from a run.

## P1 — R-4.2 Constrained decoding (malformed output structurally impossible)

- [x] [B] `flash/grammar.py`: character-level DFA (`Contract`) over the output
      contract (`# file: <name>` header → fenced block → close fence), compiled
      to per-position vocabulary masks (`Masks.allowed`) and applied at every
      decode step by `ConstrainedSampler`.
      *Done 2026-09-26. The mask is keyed on the live position — pending
      backtick count, header-literal progress and the unwritten names — so it
      is exact, not a superset: 10 154 sampled pieces, 0 refusals, 0 dead ends.*
- [x] [B] Wire the mask into the sampler path in `flash/loop.py::_generate`
      (mlx `logits_processors`), with a `--constrain` flag and a fallback path
      when no contract can be named.
      *Done 2026-09-26: `_generate(..., contract=)` + `_contract_for()`,
      `loop.CONSTRAIN` set by `flash run/run-suite/resume --constrain`;
      a task with no nameable file set generates freely (I-7). Telemetry:
      `constrained`, `mask_steps`, `mask_breaches` per generate record.*
- [x] [V] [offline] `python -m flash.grammar --selftest`: **42/42**, 2026-09-26.
      Accepts the 6 mw reference file sets, rejects each historical mutation
      class (fence without header, header without body, prose before the first
      header, unterminated fence, path escape, file outside the set), proves
      tokenization independence (40 random splits × 6 references), masks every
      historical shape, and adds three the spec did not ask for: liveness (no
      reference is blocked by its own mask), no dead ends, and the
      end-of-turn policy at exactly the positions where stopping parses.
- [x] [V] [L] Contract-violation census: **100 constrained generations, 0
      malformed, 0 mask breaches, 99/100 recoverable by the parser**
      (2026-09-26, `benchmarks/results/census/final_con100.log`, temp 0.7,
      max_tokens 1500, 6 mw prompts). The one violation is an answer that
      opened its block and wrote nothing inside — a content failure, not a
      shape one, and the mask must not pretend otherwise.
      The unconstrained arm on the same prompts and seeds: **20/20 violations,
      0/20 recoverable** (`final_free20.log`) — it narrates around the protocol
      and then runs out of budget.
      Reasons are broken out so the gate is auditable: `malformed` (the DFA is
      dead: the structural claim), `unclosed`/`file set` (completeness — a stop
      inside a body or an exhausted budget, which no logit mask can or should
      forbid).
- [ ] [V] [L] Latency cost ≤ 5% — **measured, not met, mechanism identified.**
      * The mask's own arithmetic (`--overhead`, no weights loaded):
        0.145-0.183 ms to apply a 152064-wide mask = **0.4% of a 42.6 ms
        decode step**; 7-27 ms to compile a position once.
      * End-to-end, same prompts and seeds, both arms, two pairs: free
        25.8/25.5 vs constrained 24.1/23.3 tok/s = **-6.3% to -8.6%**.
        Matched on generation length (both arms under 500 tokens) it is still
        **-6.3%** (25.5 vs 23.9), so the length difference is not the story.
      * Where the residual is, from `hook_ms` accumulated inside the sampler:
        the hook stands in **44.7% of wall time** while its arithmetic is 0.4%
        of it. It is not computing, it is *waiting* — reading the sampled id
        back is a device→host sync, and mlx's loop launches step n+1 before
        syncing step n precisely so the two overlap; asking for the id from
        inside step n+1's processor collapses that overlap.
        Tested and rejected as the cause: a zero-allocation single-id fast path
        (no `join`, no `set`, no list, penalty materialised once per dtype)
        moved nothing — 23.3 tok/s and 44.7% of wall both before and after.
        Python is not the cost, so no amount of hook micro-optimisation will
        reach 5%; only giving up the exactness would.
      * Why the box stays open instead of being reworded: knowing each sampled
        token is what makes the guarantee exact, so the sync cannot be removed
        without weakening it (a lazy read-back with a one-token lag would let
        up to one token slip past the header mask right after a fence closes).
        SPEC §10.5 proposes gating on seconds per usable answer, where this
        build measures *better* (mw suite 95s → 72s at a higher pass rate);
        that is a change to an acceptance criterion, so it is the user's call.
        `--constrain` stays opt-in until it is made.
- [x] [V] [L] Reference sweep, run as **pairs** under identical flags (a stored
      pass rate from a different configuration is not a control):
      * `m0`: 18/20 unconstrained, **18/20 with `--constrain`** — unaffected ✓
        (and matches the 7B's recorded 18/20 from 2026-09-23).
      * `mw`: 5/6 unconstrained, **6/6 with `--constrain`** — the constraint
        gained a task, at 72s vs 95s wall clock. The mw suite was the one the
        malformed-output bug class lived in, so this is the expected shape.
      Logs: `benchmarks/results/sweeps/{m0,mw}_{free,constrain}.log`.
      Wall-clock note: the constrained mw run is 24% *faster* despite a lower
      per-token rate, because a masked answer goes straight into the protocol
      instead of narrating to the token budget. That is the number an operator
      feels, and it is reported next to the per-token one, not instead of it.
- [x] [B] Docs move together: README command, `flash/__init__.py` map,
      SPEC R-4.2 → SHIPPED, PLAN §33.3 status + Appendix A row.
      Verified in tree: README:98-106, `flash/__init__.py`:13, SPEC.md's R-4.2
      row, PLAN §33.3 and its Appendix A row. The census/sweep logs that back
      the numbers are tracked under `benchmarks/results/`, not ignored.

## P2 — R-4.3 Debugger skill (watch execution, don't re-guess)

- [x] [B] `flash/debug.py`: a line-tracer session in the same isolated `-I`
      subprocess — executed trail, the value each local took as it changed,
      locals at the failing frame, last-mutated line, serialised digest.
      *Done 2026-09-26. Not pdb: a model cannot drive an interactive loop, and
      the harness needs a digest it can put in a prompt.*
- [x] [B] Feed the digest into retry feedback as a distinct `kind="debug"`.
      *Done: `loop._debug_feedback`, `--debug`, skipped for static failures
      (broken syntax has no execution to watch) and on a pass.*
- [x] [V] [offline] `python -m flash.debug --selftest`: **55/55**, 2026-09-26.
      Five seeded bugs each blamed on the line that made the value wrong, a
      hang that still reports the line it spun on, a raise that names its
      frame's locals, a compile error blamed on the candidate rather than the
      debugger, execution observed in a child pid, a path-escape refused, and
      the trail cap.
- [x] [B] Seeded-bug suite `benchmarks/tasks/dbg_tasks.jsonl`: 8 tasks, built
      by `benchmarks/gen_dbg_tasks.py`, verified offline — every `solution`
      passes, every `seeded` bug fails, and every causing line is ABSENT from
      the traceback and PRESENT in the digest (that last pair is the suite's
      whole premise, and `--selftest` checks it per task).
- [ ] [V] [L] Arm A: traceback feedback. Arm B: debug feedback. Same tier, same
      budget → B solves **≥ 2 more**. **RUN on three substrates, NOT MET:**
      * 21 tasks selected from measured first-attempt failure
        (`benchmarks/gen_dbg_hard.py`, from `results/probe/*_one_attempt.log`):
        A **6/21**, B **5/21** — the digest arm lost `h12_min_remove_parens`;
        59 generations and 59 attempts in each arm; wall 500s → 591s (+18%).
      * 8 repair tasks with the tests shown (`dbg_fix_tasks.jsonl`): A **7/8**,
        B **7/8**; `fix01_alias_sort` went 3 attempts → 2.
      * 8 blind repair tasks, brief + broken module, tests not shown
        (`dbg_blind_tasks.jsonl`): A **8/8**, B **8/8**, every task on the first
        greedy attempt — a ceiling, and proof the 7B is not fooled by these
        traps once the code is in front of it.
      Why, measured rather than asserted: attempt 0 is greedy and identical in
      both arms, so the digest can only matter on a task that survives to a
      second retry. Pairing the 12 tasks both arms solved: **11 have identical
      attempt counts**. The discriminating band in every suite available is one
      task wide, so a ≥2 gate cannot be observed here at any effect size — the
      instrument is the limit, not the mechanism (which is verified offline:
      `flash.debug --suite`, 32/32 causing lines invisible to the traceback and
      present in the digest). `--debug` therefore stays off by default.
- [ ] [B] P2-follow-up: build an instrument with a band wide enough to measure.
      The requirement is a task set where the tier needs **2-3 retries under
      traceback feedback**; it exists in the ledger already. Select from
      `benchmarks/results/ledger.jsonl` the tasks whose recorded attempts ≥ 2
      and whose tier was `small` (never escalated), and grow the set to ≥ 30 by
      generating more of that difficulty shape (`gen_vis_assets`-style
      generator, seeded-bug repairs with the tests withheld and *two* bugs
      rather than one). Then re-run this A/B. Do not tune the digest's wording
      against the current suites: with one discriminating task there is nothing
      to tune on, and any apparent gain would be noise.
- [x] [B] Docs move together (SPEC R-4.3, README, §33.2 status, Appendix A).
      Verified: README:108-113, SPEC.md R-4.3 → PARTIAL with the three substrates,
      PLAN §33.2's close note and the 2026-09-26 Appendix A row. `--debug` is
      documented as off by default and the open gate is the P2-follow-up box.

## P3 — R-8.1 Speculative decoding (G5: brain ≥ 46 tok/s)

- [x] [B] Draft model selection in the bake-off harness: `--draft <repo|key>`
      and `--draft-tokens N`, threaded through `bench_model` into mlx's
      `stream_generate(draft_model=…)`, recorded in the result JSON so a
      speculated row cannot be mistaken for a plain one. Harness still
      `--dry-run` 20/20 after the change.
- [x] [V] [L] The arms, run 2026-09-26 — **both fail, and the reasons are
      separate**:
      * **Brain (Qwen3-30B-A3B-4bit, the G5 target): the speculative path faults
        the GPU.** `kIOGPUCommandBufferCallbackErrorTimeout`, every time, with
        both a vocabulary-matched draft (`Qwen3-0.6B-4bit`, width 151646 = the
        target's own) and a mismatched one (`Qwen2.5-Coder-7B`, 151657), at
        `--draft-tokens` 4 and 2. The same target with no draft runs the suite
        normally (2/2, 17.3 GB peak) before and after, so the fault is the
        draft-verify path on an MoE target under this mlx/Metal build — not my
        wiring, not the tokenizer, not the batch size.
      * **Fast tier (Qwen2.5-Coder-7B + `Qwen2.5-Coder-1.5B-4bit`, the same
        tokenizer family, so this is the mechanism working as intended):
        speculation is 40% SLOWER.** 11.7 tok/s against a 19.4 tok/s baseline on
        the identical task, and TTFT 1219 ms against 337 ms.
        Not claimed as proof of the mechanism: with a vocabulary-incompatible
        draft (`Qwen3-0.6B`, 151646 against the target's 151657) the same target
        made 18.8 tok/s, which is within noise of the 19.4 baseline — but that
        is ambiguous, because mlx may have quietly disabled speculation on the
        shape mismatch rather than paid for a draft that always rejects. What
        is not ambiguous is the matched pair, where the path demonstrably ran
        and moved throughput the wrong way.
- [x] [V] [L] Acceptance test → **RECORDED AS A NEGATIVE**, which is the box's
      own second branch. Throughput did not reach 46 tok/s anywhere: on the
      dense target where speculation actually functions it moves the wrong way,
      and on the MoE target it cannot be measured at all. 4-bit weights on
      Apple Silicon are already bandwidth-bound at one token per step, so
      verifying k drafted tokens costs the full k forwards and the only saving
      is launch overhead — which is not what the GPU is short of here.
- [x] [B] Docs move together: SPEC R-8.1 → measured-negative with the two
      distinct causes, G5's target kept as unmet with the single-model figures
      (46.5 best-case / 31.2 sustained) as the honest standing numbers, §9 gains
      the arm register row, PLAN §8.1 and Appendix A record the runs.

## P4 — R-3.2 Symbol-precise edits (§33.1's ACT leg)

- [x] [B] Emit/apply range-addressed patches: LSP+AST give (file, symbol, range);
      patch replaces a symbol body, never a text guess.
      `flash/patches.py`: `# edit: <file> :: <Symbol>` (or `L12-L18`, or `*`) +
      one fenced block. The AST owns the span — decorators included, so dropping
      `@property` is impossible — a column-0 replacement is re-indented to the
      symbol's real nesting, and the bytes outside the addressed range are
      copied, not regenerated.
- [x] [V] [offline] Selftest: an out-of-range or overlapping patch is refused.
      `python -m flash.patches --selftest` → **37/37**, and the refusals are
      enumerated rather than sampled: unknown symbol (names what the file does
      define), ambiguous address (names both candidates), range past EOF, range
      running backwards, range straddling two symbols, overlapping patch, file
      not in the project, replacement that does not parse, replacement that
      loses the symbol it addressed. A voided set changes nothing, so a retry
      never repairs the tool's output instead of the model's change.
      7 of the 37 drive the **loop's** patch arm with a scripted generator
      (`--wire`): a refusal costs an attempt and reaches the retry as text, an
      accepted set reproduces the reference project, a patch on one module
      leaves its sibling byte-identical.
- [x] [B] 10-change suite (`benchmarks/tasks/edit_tasks.jsonl`).
      `benchmarks/gen_edit_tasks.py`: three projects (prose / shop / shift),
      ten requests, targets spanning a module function, a method, a
      constructor, a decorated property, a class-level constant and a
      statement-level body change. `python -m flash.patches --suite …` →
      **60/60 premise checks**: every task ships its own project text in the
      prompt (so both arms read identical input), fails its test as seeded,
      passes on the reference patch, and that patch fits inside one symbol.
- [ ] [V] [L] ≥ 8 changes solved in ≤ 1 attempt, and 0 edits touch lines outside
      the target symbol's range. **Clause 1 MET, clause 2 NOT MET.**
      Small tier (`Qwen2.5-Coder-7B-4bit`), greedy first attempt,
      `--allow-big never`, 10 tasks, both arms on the same input:
      * **patches (`--edit`): 8/10 solved, all 8 on the first attempt**, 6.0 s/task,
        51 completion tokens per generation, 0 refusals, 0 whole-file rewrites,
        **7 lines touched outside the annotated symbol (gate 0)** —
        `benchmarks/results/edits/arm_edit_small.log`.
      * **whole-file control: 8/10 solved, all 8 on the first attempt**, 16.3 s/task,
        184 completion tokens per generation —
        `benchmarks/results/edits/arm_free_small.log`.
      What the comparison actually shows, stated precisely: the patch arm is
      **2.7× faster** and decodes **3.6× fewer tokens**, at a *slightly higher
      total token count* (8169 vs 7845) because a patch prompt carries the
      protocol text (~160 tokens/generation) and re-sends the project every
      attempt. On this hardware tokens are not the binding cost, decode steps
      are, so the win is wall-clock and the guarantee, not spend.
      Both arms fail the same two tasks (e06 rounds tax with `round()` →
      banker's rounding; e09 keeps the wrong priority order), which puts those
      two on tier capability rather than protocol.
      Clause 2's single cause is one task, and it is worth reading: e04's
      request says "…clamped to 1 **when the Box is built**" and the change
      lives in `Box.__init__`; the model addressed `Box` — the noun in the
      request — and re-typed the whole class correctly (its exact output is in
      the trace `20260926-060927-run-suite-526b`). **The address width follows
      the noun in the request, not the locus of the change.** That is a real
      limit of the mechanism, not a transcription bug, and it makes
      "the target symbol's range" ambiguous in exactly this case: the lines are
      outside the annotated symbol and inside the addressed one. Escalated as
      SPEC §10.6 rather than reworded here.
- [x] [B] Docs move together. SPEC R-3.2 → PARTIAL with both clauses and the
      measured numbers, SPEC §10.6 (annotated vs addressed symbol), PLAN
      §33.1's ACT sentence + Appendix A row, README's patch-protocol block,
      `flash/__init__.py` map. Re-verified against the tree: `--selftest` 37/37,
      `--suite` premise 60/60, README names both counts.
- [x] [B] P4-follow-up — the GOT/WANT probe double-evaluated a stateful assert.
      `diagnose()` evaluated the whole condition and then evaluated each side
      AGAIN to print its values, so an assert whose condition mutates
      (`q.pop() == "high"`) handed the retry feedback that named no difference.
      Fixed: for a single `a == b` condition the probe now assigns each side
      once (`__c0 = a`, `__c1 = b`) and compares the stored values, so the
      printed GOT/WANT *are* the comparison that failed. Non-comparison
      conditions keep the old probe, which prints no values it did not compute.
      Proven falsifiable — the legacy probe run on the same two cases prints
      `GOT: 2` for `assert f() == 5` where `f()` had returned 1, and
      `GOT: 'high' | WANT: 'high'` on the failing pop-assert; the new one prints
      `GOT: 1` and `GOT: 'low' | WANT: 'high'`.
      On real task data (`benchmarks/tasks/edit_tasks.jsonl`, e09 seeded):
      `FAILING_ASSERT: assert q.pop() == "high" | GOT: 'low' | WANT: 'high'`.
      Vector: `python -m flash.harness --selftest` **12/12** (new — the oracle
      had no offline selftest before). No regression: `m0_bakeoff --dry-run`
      20/20, and the whole battery stays green (14/22/14/30/47/37/55/9 + 60/60
      premise + 11/11 resume). The recorded R-3.2 arms are NOT re-run: the fix
      changes what future retries are told, not the measured clause-1/clause-2
      outcomes, and re-running to make a missed gate look better would be the
      wrong kind of progress.

## P5 — R-3.3 Tournament mode (G2, AC-only)

- [x] [B] k-candidate sampling under the governor's width cap, oracle-scored,
      best-of-k adopted.
      *Done 2026-09-26 (offline half): `flash/tourney.py` — candidate 0 greedy
      (so pass@k contains pass@1), the rest sampled per-seed, first-pass
      adoption, early exit, ranking only chooses the surfaced diagnostic;
      `power.tournament_width` (4/2/1 by profile) is the AC-only clamp;
      `loop._tourney_arm` replaces the small-tier chain only when
      `tourney.eligible` says yes (width ≥ 2, single-file, k ≥ 2) and the
      refusal reason rides the route record. `harness.score()` is the new
      ranking oracle (passes-count + first failure), with `diagnose` now a
      projection of it — proven equal by a check. `--tournament K` on
      run/run-suite/resume; the suite summary prints pass@1 vs best-of-k and
      generations spent, and resume replays the candidate table from the trace
      without re-charging a model.
      Vector: `python -m flash.tourney --selftest` **16/16** (schedule, clamp,
      adoption, ties, STATIC refusal, real h-task reference adopted, trace
      event); `python -m flash.harness --selftest` **20/20** (8 new `score`
      checks incl. the "probes pass but full-test gate fails" case); whole
      battery re-run green (14/22/14/30/47/37/55/9/16 + 32 + 60 premise +
      11 resume + 20 dry-run), pyflakes 0 findings.
- [x] [V] [L] Hard family (h-tasks): best-of-3 beats single-attempt by ≥ 8 points
      at ≤ the same total token spend, on AC. **MET, as measured on matched input.**
      *Run 2026-09-26, 8 h-tasks, small tier, `--allow-big never`, AC
      (maximum-performance, width 4), both arms `tee`-logged:*
      * **B (`--tournament 3`): suite 7/8.** 7 of 8 tasks were tournament-eligible;
        in-arm, on identical prompts and one model load: pass@1 (candidate 0)
        **5/7 → best-of-3 6/7 = +14 pts** (gate ≥ 8) ✓. The gained task is
        `h06_log_error_windows` — candidates 0 and 1 died on assert 1/3, candidate
        2 passed (`[0/3,0/3,P] adopted=2`).
        `benchmarks/results/tourney/armB_tourney3.log`, session
        `20260926-105917-run-suite-6386`.
      * **A (chain, `--attempts 3`, same flags): suite 6/8** — the chain arm lost
        `h06` after 3 feedback attempts. Suite-level delta +12.5 pts.
        `armA_chain3.log`, session `20260926-110220-run-suite-3a97`.
      * **Spend ✓:** both arms 12 generations; B **3 330** tokens (1 412 prompt +
        1 918 completion) vs A **5 559** (3 894 + 1 665) — 40% less, because a
        chain retry re-sends the prompt *plus* the growing feedback, while a
        candidate re-sends only the prompt and the arm exits at the first pass.
        Wall 135s vs 128s (+5%).
      * Stated honestly, three ways the number could be over-read. (1) The
        suite-level delta is exactly **one task**, and single-run swings of ±2
        are documented (Appendix A) — the defensible figure is the **in-arm**
        one, where both numbers come from the same load and the same prompts.
        (2) The design input proved ranking is nearly inert (6/7 real failures
        die at assert 1 — `benchmarks/fail_position_check.py`, output above in
        the §33.4 module docstring) and the run confirmed it: `passed` chose
        nothing here, a passing candidate did. The mechanism's value is pass@k.
        (3) A first launch went out **while the machine was on battery** and the
        governor clamped to width 2 (best-of-2); the arm was stopped after 2
        tasks and kept as `armB_tourney3_PARTIAL_battery_width2.log` — not
        evidence, a demonstration that the AC-only clamp is real.
      * Live bonus: at h03 the mid-run probe read `low-power` for one task, and
        `eligible()` declined the tournament per-task (`tour_why` in the route
        record: "governor width 1 cannot run 2+ candidates"), so that task ran
        the feedback chain and solved on attempt 1. The refusal reason made the
        anomaly answerable from the trace alone, no re-run — §33.6 working.
- [x] [B] Docs move together.
      *Done 2026-09-26:* SPEC R-3.3 → SHIPPED with the in-arm numbers and the
      one-task-delta caveat; SPEC §6 battery re-read from the tree (harness
      12→**20**, new `flash.tourney` **16** line, total 363→**387**); README's
      battery + `--tournament` usage; `flash/__init__.py` §33.4 row and
      selftest list; PLAN §33.4 status paragraph + Appendix A row.
      Re-verified against the tree after the doc edits: whole battery green
      and `python -m pyflakes flash/*.py benchmarks/*.py` → 0 findings.

## P6 — R-2.3 confidence from verification + R-6.3 label hygiene

- [x] [B] Prospective confidence signal from static diagnostics / coverage /
      suite flakiness (not model probability). **Shipped** as
      `flash/confidence.py` + `--confidence` on `run`/`run-suite`/`resume`: four
      streams judge the answer the visible oracle already accepted — static
      errors, the share of the answer's statement lines the visible tests
      executed, the visible verdict re-run under `PYTHONHASHSEED` 0/1/7, and
      shape-typed adversarial calls that crash or hang. The offer and every
      number behind it ride the ledger (`conf_static` … `conf_reasons`) and the
      trace. **Run 2026-09-26:** `flash.confidence --selftest` **21/21**,
      `benchmarks/confidence_wiring_check.py` **30/30**,
      `benchmarks/subtle_premise_check.py` **52/52**,
      `benchmarks/p6_key_check.py` **13/13**, pyflakes 0. Two premises the
      battery re-proves every run, because both were learned by being wrong
      first: `-I` silently disables `PYTHONHASHSEED` (the seeded re-runs use
      `-s`), and a probe that ignores the argument's shape reports an answer's
      crash on input it was never asked to take (8/20 correct references offered
      before shape typing; 1/20 after).
- [ ] [V] [L] Seeded subtle-bug suite: offered on ≥ 90% of outputs that would
      fail hidden tests, < 1 false escalation per 20 routine tasks.
      **NOT MET, and one clause is unmeasurable rather than passed.**
      *Live arm 2026-09-26, Qwen2.5-Coder-7B-4bit, `benchmarks/tasks/p6_tasks.jsonl`
      (8 seeded-subtle + 15 differential-keyed routine), AC maximum-performance,
      `--confidence --trace-full`, 23/23 solved in 102s (22 small tier, 1
      escalated), session `20260926-141119-run-suite-d086`, log
      `benchmarks/results/p6/live_arm_7b_keyed.log`:*
      `[R-2.3] 2/23 answer(s) carried evidence; recall on would-fail-hidden 0/0
      (gate: >= 90%), 2 false offer(s) over 23 hidden-accepted answer(s)
      (gate: < 1 per 20)` / `gate's own population (rows tagged routine): recall
      0/0 (>= 90%), 1 false offer(s) over 15 hidden-accepted of 15 task(s)`.*
      *Recall clause: **empty denominator** — every one of the 23 answers
      survives its held-out key (the 7B answered the seeded-subtle tasks
      correctly, and the keyed routine tasks correctly), so ≥ 90% of zero is
      untested, not met. Measured offline instead, where the would-fail answers
      exist by construction: **6/6 = 100%** recall on the seeded answers the
      hidden tests sink (`subtle_premise_check`), with the two masked-value
      cases declared blind and *staying* blind.*
      False-offer clause: **missed by a single offer** — 1 over 15 routine
      (1 per 15 against < 1 per 20), 2 over 23 blended. The extra offer is on a
      seeded-subtle answer whose unexecuted branch is true evidence, so the
      routine-only figure is the clause's own; both are printed.*
      *The one routine false offer is `max_subarray([]) -> IndexError` on an
      answer whose own reference raises the same way, and the differential key
      records that crash as the expectation — the edge stream calling a
      spec-admitted crash a finding. What would close this, and is NOT done here:
      a population that produces real hidden failures (harder tasks, or the 4B
      tier) to give recall a denominator, and a second live arm at n ≥ 40 to
      give the false-offer clause any resolution.*
      *Void data, kept for the record: the two arms run before 13:35 against a
      `p6_tasks.jsonl` whose keys were broken (`gen_p6_key.observe` never
      interpolated the function name, so every probe "raised NameError")
      reported recall 2/17. Those 17 hidden-failures were the generator, not the
      model; the `hidden_ok` in sessions `20260926-130301-*` and
      `20260926-130853-*` is non-evidence (the ledger's own rows are clean —
      `hidden_ok` never left the trace), and their logs are renamed
      `benchmarks/results/p6/live_arm_7b{_full}_VOID_keys.log`. A third arm
      (`20260926-133556-*`) died at the 30B load on `[METAL] Insufficient
      Memory` and was superseded rather than resumed for the same reason.
      `p6_key_check.py`'s mutation clause puts that bug back and proves the
      generator now refuses to write.*
- [x] [V] [offline] `trainable()` exclusion audit: AUC on excluded-only re-fit ==
      AUC on all-minus-override re-fit (no signal lost). **MET within measurement
      precision, and the precision is stated.**
      *Run 2026-09-26, `benchmarks/trainable_audit.py` (log:
      `benchmarks/results/p6/trainable_audit.log`): fit A = 314 trainable-only
      rows; fit B = 370 rows minus the *mandated* policy exclusions only
      (`routed=big(...)`, `tier=shed`) — B therefore keeps exactly the 56 vision
      rows A drops, and the audit measures trainable()'s one exclusion beyond
      the hygiene minimum. Shared honest eval: 66 held-out rows (m4–m7,
      tier∈{small,big,failed}, none big-routed), 10 positive.*
      *Pooled AUC: A **0.618**, B **0.736**; difference −0.118 with a bootstrap
      95% interval **[−0.369, +0.099]** over 4 000 replicates — the gap does not
      separate from zero, so no measurable signal is lost; at 10 positives a
      true gap of up to ~0.37 could also hide here, and that is stated rather
      than smoothed. m7 reports n/a on purpose: its honest-label pool has one
      class only (its big rows are all gate-routed, excluded as policy).*
      *Cost note (box is marked offline but this crossed the line): 69 prompts
      were not in the embedding cache, so one 7B load ran forward passes only —
      no generation, no outcomes; the selftest (16/16: AUC ranks/ties/empty,
      set-definitions, bootstrap band contains zero for identical fits and
      excludes it for a true separation) needs no model.*
- [x] [B] Docs move together (SPEC R-2.3, R-6.3). **Moved 2026-09-26:**
      SPEC R-2.3 → PARTIAL (mechanism shipped, both clauses missed, with the
      live numbers and the empty-denominator reading stated), SPEC §6's battery
      re-read from the tree **387 → 503 green** (confidence 21, wiring 30,
      seeded-suite premise 52, key premise 13), README's §34.2 command block,
      `flash/__init__.py`'s module map, PLAN §34.2 status line and an Appendix A
      row that carries the void-key arms as non-evidence. R-6.3's docs move here
      rather than with `dbdd7bf`, which touched only this file: its SPEC status is
      now MET (within the stated precision) and its Appendix A row records the
      −0.118 / [−0.369, +0.099] figure and the forward-pass-only cost.

## P7 — R-5.3 task-granular recovery → R-5.4 M16 chaos

- [ ] [B] Checkpoint in-flight task: partial generation + sandbox state.
- [ ] [V] [offline] Kill/resume check extended to mid-task (`--resume` does not
      regenerate attempt 1 of the interrupted task).
- [ ] [V] [L] Real `kill -9` between two tokens of a live `run-suite`, then
      `flash resume` → in-flight task completes.
- [ ] [V] [L] M16: 24h window with random kills, memory pressure, network loss,
      thermal load → 0 data loss, every session closed-or-resumable, no torn
      ledger lines. *(Needs a scheduled 24h window — see SPEC §9.)*
- [ ] [B] Docs move together.

## P8 — R-6.4 first learned self-improvement (G9) + LoRA

- [ ] [B] LoRA experiment path inside `jobs.py`'s AC+idle gate.
- [ ] [V] [L] Before/after on a frozen suite for one named component (skills,
      memory or weights — whichever lands first), I-2 gate satisfied: the changed
      component beats the frozen harness.
- [ ] [B] Docs move together.

## P9 — Product shell and adoption

- [ ] [B] R-7.2 ambient mode: idle+AC prepares **draft** diffs only; never
      merges, never auto-applies, nothing pushed.
- [ ] [V] [offline] Check: an overnight run leaves ≥ 1 reviewable draft + trace,
      0 pushes, 0 files touched outside its worktree.
- [ ] [V] [L] R-7.3 voice: real-microphone arm, VAD barge-in, ≥ 90% command
      recognition over 50 utterances.
- [ ] [V] [L] M17 feel test: ≥ 7 of 10 developers keep it after a week.
      *(Needs humans — SPEC §9 register.)*

## Backlog (OPEN, not scheduled; each needs ledger demand to earn a slot)

- [ ] R-1.3 knowledge graph (`flash graph <symbol>` blast radius < 200ms)
- [ ] R-1.4 second language for perception (choose from ledger evidence)
- [ ] R-8.2 latent compute — adopt only on a measured ≥ 20% token saving
- [ ] R-9.2 explicit sandbox: writable root, no network, cpu/mem rlimits; hostile
      candidate test proves `open('~/.ssh/id_rsa','w')` fails as a normal verify error
- [ ] G6 watts/task (blocked: `powermetrics` needs sudo)
- [ ] §34.1 16GB co-residency arm (blocked: this box is 32GB)

---

## Log

- 2026-09-26 — P5 (R-3.3) ran and the gate MET, with two facts I want on record
  because they are easy to over-read. First, **the first launch went out while
  the machine was on battery** (the `power --json` probe was minutes stale); the
  governor clamped the tournament to width 2 and I stopped the arm after 2
  tasks, keeping the log as `armB_tourney3_PARTIAL_battery_width2.log` — not
  evidence, a demonstration that the AC-only rule bites. Second, the **gate's
  honest number is the in-arm one**: candidate 0 vs best-of-k on the same load,
  same prompts, same model — +14 pts (5/7 → 6/7). The suite-level B-vs-A delta
  is also +1 task (7/8 vs 6/8), but that sits inside the documented ±2 single-run
  swing, so it corroborates and does not carry the claim. What the run did
  prove about the mechanism: `h06_log_error_windows` died at assert 1/3 for the
  greedy candidate AND for all three chain attempts, then a sampled candidate
  passed it — variance where feedback got nothing. And the design pre-check
  held exactly: 6/7 real hard-task failures die at the first assert, so
  `passed` ranked nothing anywhere; adoption-by-pass is the whole mechanism.
- 2026-09-26 — P4 (R-3.2) run, and the interesting part is not the score: it is
  that **the control arm was invalid on first principles and I did not see it
  until it had run.** Edit prompts carried only the change request, never the
  source, so the whole-file control was answering about a project it had never
  seen — it "failed nearly everything" and I nearly booked that as the patch
  arm's win. Stopped the background run, moved the project listing into the
  stored prompt so both arms read identical material, added a premise check
  ("the task ships its own project text"), and kept the superseded logs renamed
  `_v1_blind` rather than deleting them. The matched rerun then showed the arms
  tied on capability (8/10, both all-first-attempt) and separated only on cost
  and guarantee — the opposite of what the invalid arm implied.
- 2026-09-26 — The same live run caught a **VERIFY** defect that no offline test
  could have: `diagnose()` decided an assert by evaluating its condition, then
  evaluated each side *again* to print GOT/WANT. For `assert q.pop() == "high"`
  that produced `GOT: 'high' | WANT: 'high'` for a **failing** assert — feedback
  that names no difference, on which both arms burned a retry. Now each side is
  evaluated once and the printed pair *is* the comparison that failed. Two
  notes: the oracle had **no selftest of its own** before this (12/12 now, and
  the offline battery count in SPEC §6 was 202 → 343 checks), and I deliberately
  did **not** re-run the R-3.2 arms after fixing it — the fix changes what a
  future retry is told, not the measured outcome, and re-running a missed gate
  until it passes is not evidence.
- 2026-09-26 — Clause 2 of R-3.2 missed for a reason worth stating to myself:
  the request said "when the Box is built", the change lived in `Box.__init__`,
  and the model addressed `Box` — the noun it was handed — while re-typing the
  class correctly. **The address width follows the noun in the request, not the
  locus of the change.** I could have reworded the gate to measure the addressed
  symbol instead of the annotated one and reported 10/10; that reading also
  makes `*` pass by definition, so I kept the stricter metric and escalated the
  ambiguity as SPEC §10.6 instead.
- 2026-09-26 — P1 offline shipped and its live arm run. The live run found
  three defects the offline battery could not: (1) this checkpoint returns
  152064-wide logits over a 151657-token vocabulary, and a table-sized mask
  does not broadcast — the arm crashed on its first masked step; (2) the mask
  excused the whole special-token family as "template control" while the
  decoder only ever strips the piece that *ends* the turn, so a role marker
  sampled mid-body passed the mask and the walk then called the answer
  malformed — every masked answer was reported as a violation; (3) the
  sampler's baseline was `len(tokens) - 1`, so the prompt's last token was
  walked as generated text and 49 refusals were booked against the mask.
  All three are fixed and each is pinned by a check (47/47 offline).
  A fourth survived those fixes as **99 breaches in 100 generations** — one
  per run. My first diagnosis (the turn guard tested `eos` where the mask
  offers the whole turn family) was *wrong*, and disproved in five minutes by
  the offline probe that the numbers were claimed to need: `eos` is in the
  family, the stop policy is right at both positions, and feeding the stop
  plus trailing tokens produces no breach. The real cause was `consume`
  joining a multi-id array into one string and walking it as a single piece,
  while the mask had been compiled piece by piece — the same mask/walk
  disagreement as defect (2), reached from the other side. With pieces
  absorbed one at a time: 6/6 live runs, 0 breaches; then 100/100, 0 breaches.
  Two process notes worth keeping: the breach count was the *symptom* the
  offline walk could not show, because the walk always fed one piece per step;
  and `illegal_picks` was changed to also record the first offending ids, text
  and position, because a count without a named instance invites exactly the
  confident wrong diagnosis I just made.
  Observed on the pre-fix build, 84 constrained generations over the mw
  prompts: 0 malformed, 0 dead ends, 82/84 recoverable by the parser — the two
  misses were answers that opened their block and wrote nothing inside it,
  which is a content failure the mask cannot and should not prevent. The same
  arm's answers averaged 150-330 tokens where the unconstrained arm consumed
  the whole 1500-token budget to say less, which is why the latency claim is
  measured as `--overhead` (ms of mask per decode step) rather than as
  end-to-end tok/s: the two arms do not generate the same workload.
- 2026-09-25 — P0 complete. Repo initialised; baseline `0ea2798` (72 files,
  `.venv`/`__pycache__`/`benchmarks/cache` ignored, results+traces tracked as
  evidence). Working tree clean.
