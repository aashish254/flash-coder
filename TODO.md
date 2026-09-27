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
      budget → B solves **≥ 2 more**. **RUN on four substrates, NOT MET:**
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
      * 30-task **wide-band** substrate (`dbg_band_tasks.jsonl`, built by the
        box below, frozen ledger cut): A **27/30**, B **25/30**, 52 attempts in
        each arm, wall 414.5s → 463.5s (+11.8%). **15 of the 30 tasks took a
        second or third attempt** (8 at two, 7 at three), so this band *can* see
        a ≥2 difference — and the two-task gap is not a digest *solve*: on the
        25 tasks that ran the small tier in BOTH arms it is **25/25 vs 25/25**,
        and the entire delta is `h12_min_remove_parens` and `mw1_ringbuf`
        reaching the escalation boundary under B and being denied by §34.1 at
        `--allow-big never` — R-6.4's shed-tier confound again. The digest's
        footprint is two tries and one outcome: **28 of 30 attempt counts
        identical**, `fix01_alias_sort` 3 → 2 (the one effect, now reproduced
        from the tests-shown substrate, solved either way), `mw1_ringbuf` 2 → 3
        with that third try crossing the denied escalation, and on `h12` both
        arms used 3 tries with only the traceback arm's third one passing — the
        same task the 21-task substrate also lost to the digest. Net **0 gained,
        2 lost**. Logs
        `benchmarks/results/dbg_band_arm{A,B}.log`, sessions
        `20260926-222341-run-suite-e6fe` and `20260926-223150-run-suite-4cc6`.
      Why, measured rather than asserted: attempt 0 is greedy and identical in
      both arms, so the digest can only matter on a task that survives to a
      second retry. Pairing the 12 tasks both arms solved on the first
      substrate: **11 have identical attempt counts**. On the three original
      substrates the discriminating band was one task wide, so a ≥2 gate could
      not be observed there at any effect size — the instrument was the limit,
      not the mechanism (which is verified offline: `flash.debug --suite`,
      32/32 causing lines invisible to the traceback and present in the digest).
      The band has since been widened on purpose, to 15 retried tasks, and the
      gate still misses: the mechanism is verified offline, changes attempt
      counts on 2 of 30, and does not raise the pass rate. `--debug` therefore
      stays off by default, now on a measured negative rather than an
      unmeasurable one.
- [x] [B] P2-follow-up: build an instrument with a band wide enough to measure.
      **BUILT 2026-09-26 and used: `benchmarks/gen_dbg_band.py` →
      `benchmarks/tasks/dbg_band_tasks.jsonl`, 30 tasks, vector
      `benchmarks/dbg_band_check.py` 172/172 + 5 mutants, and the A/B re-run on
      it (the box above, fourth substrate).** The recipe was followed with one
      disclosed reading rule: the ledger has no `escalated` boolean, so "whose
      tier was small, never escalated" is `tier == "small"` AND `solved` AND
      `attempts >= 2` AND not `shed` — a row that carries `tier: small` and also
      a shed verdict contradicts itself and is dropped rather than kept because
      it is convenient (`gen_dbg_band.band_from_ledger`). An earlier revision of
      this box claimed the ledger has no tier field; it has one in all 839 rows,
      the generator has always filtered on it, and the claim was wrong.
      The requirement is a task set where the tier needs **2-3 retries under
      traceback feedback**; it exists in the ledger already. Select from
      `benchmarks/results/ledger.jsonl` the tasks whose recorded attempts ≥ 2
      and whose tier was `small` (never escalated), and grow the set to ≥ 30 by
      generating more of that difficulty shape (`gen_vis_assets`-style
      generator, seeded-bug repairs with the tests withheld and *two* bugs
      rather than one). Then re-run this A/B. Do not tune the digest's wording
      against the current suites: with one discriminating task there is nothing
      to tune on, and any apparent gain would be noise.
      *How it was met:* 18 rows are the ledger's own band, carried with the run
      that put them there (`band_ts`, a **frozen cut** so re-generating is
      byte-identical while the ledger grows — proven the hard way, two band
      tasks arrived between the two runs of this vector and the cut held); 12
      are generated into that shape from 31 bug families over 14 correct
      references, and a mutant survives only if the oracle sees it without
      raising (an exception names its own line, so it cannot be misleading),
      both causing lines are invisible in the failure text and named by the
      digest, and each bug still fails the test alone. The A/B was NOT tuned:
      `--debug`'s wording is untouched from the three earlier substrates, and
      the gate missed on the wide band exactly as it missed on the narrow one.
- [x] [B] Docs move together (SPEC R-4.3, README, §33.2 status, Appendix A).
      Verified: README:143-175 (the debugger block plus the band instrument —
      the earlier README:108-113 citation went stale when this session's
      battery and ambient blocks were inserted above it, so the numbers moved
      with the text), SPEC.md R-4.3 → PARTIAL with all four substrates and the
      wide-band verdict, PLAN §33.2's close note and its Appendix A rows.
      `--debug` is documented as off by default on a measured negative, and the
      instrument box below is the closed follow-up.

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
      `python -m flash.patches --selftest` → **46/46**, and the refusals are
      enumerated rather than sampled: unknown symbol (names what the file does
      define), ambiguous address (names both candidates), range past EOF, range
      running backwards, range straddling two symbols, overlapping patch, file
      not in the project, replacement that does not parse, replacement that
      loses the symbol it addressed. A voided set changes nothing, so a retry
      never repairs the tool's output instead of the model's change.
      7 of the 46 drive the **loop's** patch arm with a scripted generator
      (`--wire`): a refusal costs an attempt and reaches the retry as text, an
      accepted set reproduces the reference project, a patch on one module
      leaves its sibling byte-identical.
- [x] [B] An address WIDER than the change is narrowed before it is written
      (`narrow`, for clause 2's e04 shape). The applier diffs the owned block
      against the replacement and splices only the runs whose bytes differ,
      bottom-up, copying every other line of the block out of the file — so
      `# edit: box.py :: Box` cannot re-emit a sibling method the change does
      not live in. `outside_lines` now reports lines whose BYTES were
      regenerated rather than lines inside the addressed span, and
      `Applied.spans` keeps "never narrowed" (`None`) apart from "rewrote
      nothing" (`()`) because those are different facts. Nine checks pin the
      shape, three of them the ones a lazy version would skip: the narrowed
      splice must land **byte-identical** to the wide one (a narrowing that
      changed the file is a different edit than the one asked for), a
      count-changing run must still land in the right place when another run
      follows it (this is the check that made a top-down-splicing mutant
      visible — the other eight were all line-count-preserving and every one of
      them passed), and a sibling the model DID change must still be spliced and
      must still score, because an audit that can only ever report zero is
      worse than no audit. A verbatim re-type writes nothing at all.
      Mutation-checked against this narrowing: **9 mutants, all caught**, tree
      checksum-verified restored (`ac6e6831eeea`) after each.
- [x] [B] 10-change suite (`benchmarks/tasks/edit_tasks.jsonl`).
      `benchmarks/gen_edit_tasks.py`: three projects (prose / shop / shift),
      ten requests, targets spanning a module function, a method, a
      constructor, a decorated property, a class-level constant and a
      statement-level body change. `python -m flash.patches --suite …` →
      **60/60 premise checks**: every task ships its own project text in the
      prompt (so both arms read identical input), fails its test as seeded,
      passes on the reference patch, and that patch fits inside one symbol.
- [ ] [V] [L] ≥ 8 changes solved in ≤ 1 attempt, and 0 edits touch lines outside
      the target symbol's range. **Clause 1 MET, clause 2 NOT MET at 1 line
      (was 7) — the box stays open, and the line left is not the same kind of
      line as the seven.**
      2026-09-26, before `narrow`. Small tier (`Qwen2.5-Coder-7B-4bit`), greedy
      first attempt, `--allow-big never`, 10 tasks, both arms on the same input:
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
      Clause 2's single cause was one task, and it is worth reading: e04's
      request says "…clamped to 1 **when the Box is built**" and the change
      lives in `Box.__init__`; the model addressed `Box` — the noun in the
      request — and re-typed the whole class correctly (its exact output is in
      the trace `20260926-060927-run-suite-526b`). **The address width follows
      the noun in the request, not the locus of the change.** That is a real
      limit of the mechanism, not a transcription bug, and it makes
      "the target symbol's range" ambiguous in exactly this case: the lines are
      outside the annotated symbol and inside the addressed one. Escalated as
      SPEC §10.6 rather than reworded here.
      2026-09-27, re-measured after `narrow` shipped (this is a mechanism change
      — the tool now writes a different set of bytes — so the old 7 no longer
      describes what is installed, which is the opposite case from P4's
      fix-future-retries-and-do-not-re-measure):
      **8/10 solved, all 8 on the first attempt** (clause 1 still MET), 4.0 s/task,
      0 refusals, 0 whole-file rewrites, **1 line outside the target symbol** —
      `arm_edit_small_narrow.log`, and identically
      `arm_edit_small_narrow_full.log` with `--trace-full` (traces
      `…-7ba6` / `…-cde5`; per-task timings agree to 0.1 s, so the residue
      reproduces rather than being one hot retry's luck).
      The remaining line, from the model's own text: e09 (unsolved) sent two
      patches. `TaskQueue.pop` came back **verbatim** — zero differing runs,
      nothing written, zero charged — and `TaskQueue.push` gained a real
      `self._items.sort()`, a line in a member the request never named.
      Both halves of the 7 → 1 were then measured rather than inferred, by
      replaying each day's captured greedy text through HEAD's module and the
      tree's side by side:
      * e04: **7 → 0** at identical bytes, `summary()` reading
        `box.py:Box L4-L13 [1 of 10 lines rewritten]` — one line, because only
        the clamp line differs. Same text on both days (`prompt_tokens 550`,
        `completion_tokens 74`, address `box.py:Box`), so this is a matched
        before/after on one model output, not two samples.
      * e09: **2 → 1** (`taskq.py:TaskQueue.push L8-L9 [1 of 2 lines
        rewritten]`, `taskq.py:TaskQueue.pop L11-L14 [0 of 4 lines rewritten]`).
      HEAD charges the whole addressed span; the tree charges only the lines
      whose bytes it wrote. What is left is therefore the model solving the
      behaviour somewhere other than the annotated symbol — content, not
      regeneration. Under §10.6's reading (b) that is a 0 and the
      gate passes; the reading was not switched, so it does not.
- [x] [B] Docs move together. SPEC R-3.2 → PARTIAL with both clauses and the
      measured numbers, SPEC §10.6 (annotated vs addressed symbol), PLAN
      §33.1's ACT sentence + Appendix A row, README's patch-protocol block,
      `flash/__init__.py` map. Re-verified against the tree: `--selftest` 37/37,
      `--suite` premise 60/60, README names both counts.
- [x] [B] Docs move together for the narrowing. SPEC R-3.2's box (both clauses
      re-measured, the mechanism named, §10.6's residue restated as a model
      choice rather than a tool artifact), SPEC §10.6's update paragraph, SPEC §6
      (`flash.patches --selftest` 37 → 46, total 964 → 973), PLAN §33.1's ACT
      sentence + a new Appendix A row, README's ACT block + its stale "23 offline
      vectors" corrected to the 27 listed, `flash/__init__.py` map.
      Re-verified against the tree: `--selftest` **46/46**, `--suite` **60/60**,
      `battery_reread` re-read the same day on AC at 0.33 load/core printing
      `checks 953  oracle 20  §6 total 973  mutants 30` with all 27 lines OK,
      pyflakes 0.
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
      *Live arm 2026-09-27, the same 7B over the 59-row wide instrument (session
      `20260927-001446-run-suite-bcf3`, log
      `benchmarks/results/p6/arm_7b_p6b.log`, 47/59 solved in 396s, avg 6.7s/task):*
      `[R-2.3] 9/59 answer(s) carried evidence; recall on would-fail-hidden 4/11
      (gate: >= 90%), 5 false offer(s) over 48 hidden-accepted answer(s) (gate: < 1
      per 20)` / `gate's own population (rows tagged routine): recall 0/1 (>= 90%),
      1 false offer(s) over 14 hidden-accepted of 15 task(s) (< 1 per 20)`. *Both
      clauses MISSED with real denominators: the recall clause has 11 answers the
      hidden key sinks, so 4/11 = 36% is a measurement rather than an empty set.*
      *Live arm 2026-09-27, the 1.5B pilot tier over the same 59 rows (session
      `20260927-002448-run-suite-7bb2`, log
      `benchmarks/results/p6/arm_1b5_p6b.log`, 7/59 solved in 1252s, avg 21.2s/task,
      52 tasks denied escalation by the §34.1 governor at load 2.4/core on
      low-power):* `[R-2.3] 45/59 answer(s) carried evidence; recall on
      would-fail-hidden 44/52 (gate: >= 90%), 1 false offer(s) over 7 hidden-accepted
      answer(s) (gate: < 1 per 20)` / `gate's own population (rows tagged routine):
      recall 13/15 (>= 90%), false offers NOT MEASURABLE (none of the 15 routine
      row(s) was accepted by a hidden test, so the per-20 clause has no population)`.*
      *A weaker tier does produce answers that pass the visible oracle and fail the
      held-out key — 52 of 59 — which is the population the recall clause was
      written for and never had. Recall is 44/52 = 84.6% overall and 13/15 = 86.7%
      on the routine rows: **MISSED at both**, and the shape of the 8 misses says
      why. Five are coverage-shaped but ABOVE the shipped `COVERAGE_TAU` 0.55
      (`cov01_invoice_tax` 0.667, `seed02_shared_name` 0.75, `h24_md_table` 0.818,
      `h08_settle_fees` 0.846, `cov02_report_sum` 0.875); three have total visible
      coverage and a wrong value anyway (`t09_max_subarray`, `t14_topk_frequent`,
      `h25_luhn_check`) — the documented masked-value blind spot, not a new one.
      Both routine misses are in that second group.*
      *Whether a stricter threshold could close it is measured by
      `benchmarks/confidence_tau_check.py` **7/7** (offline, no model): the shipped
      predicate is re-derived from each arm's own recorded per-answer numbers, and
      the re-derivation must reproduce the recorded `conf_offer` on **59/59 answers
      at both tiers** before a single sweep row prints (dropping the static
      short-circuit is the mutant that proves the clause bites). At tau 0.85 the
      1.5B's recall becomes 48/52 = **92.3%, over the gate** — while the 7B's rises
      only to 63.6% and its routine false offers go 1 → 3, i.e. 1 per 15 tasks →
      **1 per 5**, deeper into the < 1 per 20 miss. **The two clauses move in
      opposite directions across tiers, so R-2.3's gate is not a knob-tuning
      problem on this feature set**: it needs a stream that reaches a wrong value on
      a line that ran (§32.4's untried levers). Stated limit of that sweep: it is a
      re-derivation from recorded fields, not the four streams re-run over the
      answers (the traces keep the evidence, not the source), and the 1.5B's 7
      hidden-accepted answers cannot resolve a per-20-tasks clause at all.*
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

## P6b — the learned router must survive a model switch (R-2.2)

Found by the 1.5B pilot: switching `--small` made the shipped router crash. Both
failures were correctness bugs in code already marked SHIPPED, so both are booked
here rather than folded into P6's confidence work.

- [x] [V] The prompt-embedding cache is (weights, text)-keyed, not text-keyed.
      **Fixed 2026-09-26:** `learn.emb_cache_path(repo)` puts the repo in the
      filename (`prompt_embeddings_last_<repo-slug>.npz`),
      `ensure_cache_path()` adopts the unlabeled legacy file exactly once and only
      for the repo it was built with, and `load_cache()`/`embed_backfill()` now
      REQUIRE a path — a call that could not say which model's cache it meant
      raises before it reads anything.
- [x] [V] A bundle knows what it was fit on, and refuses what it cannot score.
      `fit_router()` stamps `small_repo` and `pool` into the npz;
      `bundle_labels()`/`probe_pool()` read them with an `unlabeled` fallback;
      `route_score()` returns `(None, reason)` on a width mismatch instead of
      broadcasting — that broadcast was the crash, and inside `solve_routed` it
      would have killed every task in a suite.
- [x] [V] Serve-time pooling equals fit-time pooling. `flash/loop.py` embeds the
      live probe with `probe_pool(bundle)`, and `autofit_if_stale()` refits on a
      tier switch as well as on staleness, so a resumed suite cannot quietly score
      a 7B's weights with a 1.5B's basis.
- [x] [V] Vector: `benchmarks/router_portable_check.py` **20/20 checks + 5/5
      mutants defeated by exactly their checks** (a cache that ignores the model,
      a bundle that keeps no labels, a score that trusts any width, a serve path
      that pools mean, a cache path that is optional again). Wired into
      `battery_reread`; §6 re-read from the tree **855 → 875 checks /
      875 → 895 green / 25 → 30 mutants**, all 25 lines printed OK, pyflakes 0.
- [x] [V] The skew that was hiding behind the crash, MEASURED. The fit pooled the
      last token, `embed_text`'s default pooled the mean, so every live `route_p`
      came from a differently-pooled vector.
      `benchmarks/router_pool_audit.py` (forward passes only, re-runnable with no
      model load once both pools are cached; log
      `benchmarks/results/p6/router_pool_audit.log`): over the 106 prompts cached
      for the 7B, median |ΔP| **0.297**, median P **0.180 → 0.511**, and **47/106**
      decisions would have been big-directed at cutoff 0.5 that the fit's own pool
      would not have — the distributional separation the router exists to provide
      is gone. Corroborated from the system under test rather than asserted: of
      621 ledger rows whose prompt is cached, **316 reproduce exactly from the mean
      pool of the bundle on disk and 0 from the pool it was fit on**; the other 305
      match neither, so a silent background refit owns those rows and pooling
      cannot be blamed for them. Suite attribution comes from the task files (a
      shared id counts in each file that carries it), and the m7 question the
      finding was aimed at is answered honestly: 8 m7 prompts are in the population,
      the bundle on disk scores them at last 0.169 / mean 0.585 with **0/16
      pool-prompt pairs ≥ 0.83**, while the ledger recorded 0.513–0.882 for them —
      so §34's "every m7 prompt ≥ 0.83" note predates this fit and this audit
      CANNOT re-attribute it in either direction. Nothing was harmed: the gate is
      disarmed at `run-suite`'s default `--threshold 1.1`, so no recorded pass rate
      moved — the damage is a wrong `route_p` column and a wrong answer for anyone
      who ran `--threshold 0.5`.
- [x] [L] Re-run the 1.5B pilot now that the crash is fixed: does a weaker tier
      produce answers that pass the visible oracle and fail the held-out key?
      **Answered 2026-09-27: yes — 52 of 59 answers on `p6b_tasks.jsonl` pass the
      visible oracle and sink the held-out key, which is the non-empty denominator
      the recall clause was written for and had never had.** The tier switched
      without the router crash (P6b's three fixes hold under a live switch), the
      arm solved 7/59 in 1252s with 52 escalations denied by the §34.1 governor on
      low-power, and the recall clause measured 44/52 = 84.6% (routine rows
      13/15 = 86.7%) against a gate of >= 90%. Booked as a miss in P6's R-2.3 box
      with the eight misses named; the same box carries the 7B arm at 4/11. Without
      this arm the clause would still be reading 0/0 at the strong tier.
- [x] [B] Empty-denominator refusal in the arm printer: a recall line whose
      denominator is 0 must print `NOT MEASURABLE`, never a percentage.
      **Built 2026-09-27 as `cli._scored`** — each R-2.3 clause renders through one
      helper that keeps its own label and refuses when its own population is empty:
      `recall on would-fail-hidden NOT MEASURABLE (nothing in this suite fails its
      hidden test, so recall was not tested at any threshold)`, and the matching
      offer clause; a suite with no hidden keys at all refuses both. The defect this
      closes is the shipped printer's own output — the 1.5B pilot printed `0 false
      offer(s) over 0 hidden-accepted of 15 task(s)` next to a recall clause that had
      genuinely missed at 16/22, and the first line reads as a clean pass on a
      population where nothing was ever accepted. Vector:
      `benchmarks/confidence_wiring_check.py` **30 → 35/35**, five new checks over
      three constructed populations (all-hidden-accepted, all-hidden-sunk, unkeyed);
      replacing `_scored` with `return text` — the refusal muted — fails **exactly 3**
      of them, which is the proof that the checks are about the refusal. §6 re-read
      **895 → 919 green** (899 checks + 20 oracle, 30 mutants unchanged), pyflakes 0.
- [x] [B] The wide instrument, and the two size bounds it forced on the shipped
      probe design. **Built 2026-09-27:** `python benchmarks/gen_p6_key.py --wide`
      writes `benchmarks/tasks/p6b_tasks.jsonl` — **59 rows: 8 seeded-subtle + 15
      keyed routine + 36 keyed hard** — every key verified against its own reference
      at all three hash orders before the file is written, with the tags kept
      separate so widening the recall denominator cannot move the per-20-ROUTINE
      false-offer clause. Attrition is named, not silent: 20 tasks yield no
      assertable value on the probed inputs, and a per-tag guard refuses a file where
      half a population disappeared. Two hazards had to be fixed in shipped code
      first, both found by this generator refusing to write:
  * **the probe was unbounded in size.** Measured on this box with the battery as
    it shipped: `PROBE_BATTERIES["int"]` carried `10 ** 4`, so the edge stream
    planned `spiral(10000)` for `h39_spiral_matrix`'s **correct** reference —
    **2.29 s and 1445 MB** of probe child, through the 2-second alarm, reported as
    `('spiral', '10000', 'HANG')`: our budget failing, filed as the answer's bug.
    `confidence._affordable` now refuses an argument over `PROBE_MAX_INT` 1000 (or
    32 elements / 64 characters) inside `_probe_calls`, so the bound lives in the
    planner and a hand-edited battery cannot re-open it; the largest shipped
    sentinel is now ±10³. A memory ceiling in the child is NOT available here —
    `setrlimit(RLIMIT_AS|DATA|RSS, anything finite)` raises `ValueError` on this box
    (measured; `RLIMIT_CPU` does work and killed a busy loop at exactly 1.00 s,
    which is the lever R-9.2's memory clause had to do without — the shipped
    sandbox books that gap in `status()['memory']`).
    `flash.confidence --selftest` **21 → 25/25**, and deleting the guard from
    `_probe_calls` fails **exactly 1** of them.
  * **the key was unbounded in what it wrote.** The committed `p6_tasks.jsonl` held
    **87 568 bytes of keys with one 82 183-character line** (`assert climb_stairs(10000)
    == …`); the bounded file holds 3 576 bytes with a widest line of 238 characters
    and the same 92 key lines. `gen_p6_key.observe` now truncates inside the child
    and `key_for` refuses an expectation over `MAX_EXPECTATION_CHARS` 2000, counting
    what it dropped: the wide run dropped `fizzbuzz(1000)` (7 673 chars),
    `pascal_row(1000)` (218 190) and `spiral(1000)` (**7 890 896**). An earlier
    symptom of the same missing bound was `OSError: [Errno 7] Argument list too
    long`: both the key prober and the shipped seeded re-run (`_seeded_run`) now put
    the program on stdin instead of `-c`, because a long answer plus a long test as
    one argv entry is not a verdict, it is a crash.
  * **and a key that repeats the visible oracle is not held out at all.** Three hard
    keys were more than half repetitions (`h26_pascal_row` 2/3, `h33_base32_decode`
    1/1, `h45_phone_letters` 1/1) because the battery's small integers are what a
    test typically writes; `key_for` now skips a probe the task's own visible test
    asserts verbatim (19 skipped across the wide run), which costs two rows and
    keeps the rest differential. `benchmarks/p6_key_check.py` **13 → 28/28**: the
    same eight clauses are now audited over BOTH instruments, plus both size bounds
    and the novelty rule, each proven by removing it — with the key budget taken
    away the same probe writes a **7 890 919**-character line, and with the novelty
    skip taken away the generator writes the 61-row file back with 3 dominated keys.
      Live arms on this instrument: BOTH are booked in P6's R-2.3 box — the 7B at
      recall 4/11 with 5 false offers over 48 hidden-accepted, the 1.5B pilot at
      recall 44/52 with 1 over 7 — both clauses missed with real denominators, and
      `benchmarks/confidence_tau_check.py` 7/7 measures why no coverage threshold
      fixes that pair.
- [x] [B] The probe child's own death was being filed as the answer's edge
      finding. **Fixed 2026-09-27, from the arm's own log.** The 7B's answer for
      `h15_shell_split` carried a self-check at module level — `assert
      rt.shell_split(…)` comparing the return value against the `ValueError`
      *class* — so importing it raised. The edge driver then died at `import
      answer` before printing its JSON line, and `edge_probe` returned the last 120
      characters of the child's raw stderr as an edge event, which the arm printed
      as three physical lines inside a log whose contract is one line per task:
      `edges: <probe crashed>() -> rt shell_split("unmatched 'quote") == ValueError`
      / the caret row / `AssertionError`. The driver guards its import now and
      renders `edges: answer does not import (AssertionError)`; a child that dies
      uncatchably (`os._exit`) still reaches the fallback, and both paths flatten
      whitespace, so no reason can carry a newline or a temp path. Vector:
      `flash.confidence --selftest` **25 → 29/29**, and the mutations are the proof
      of the diagnosis rather than of the code — removing the import guard fails
      **exactly 2** checks, removing `_filtered`'s flattening **exactly 1**,
      removing the fallback's **exactly 1**, and removing both the guard and the
      fallback's flattening (the shipped-before state) fails **3** and makes the
      reason string **3 lines** with the recorded text in it. The fixture is a
      reconstruction from that log line, not the original source (the trace keeps
      the evidence, not the answer), and the reconstruction landing on the same
      three-line shape is what makes it more than a guess. §6 re-read from the tree
      **919 → 930 green** (910 checks + 20 oracle; the other +7 is
      `confidence_tau_check`, below), pyflakes 0.
- [x] [V] [offline] Is R-2.3's gate reachable by tuning the coverage threshold?
      **Measured: no — the two clauses pull opposite ways across tiers.**
      `benchmarks/confidence_tau_check.py` **7/7** re-derives the shipped predicate
      from the two arms' own recorded per-answer fields and sweeps `COVERAGE_TAU`
      over both, refusing to print a row until the re-derivation reproduces the
      recorded `conf_offer` on **59/59 answers at each tier** (the mutant is a model
      that drops the static short-circuit, which the clause catches on 41 answers at
      the 1.5B and 0 at the 7B — the 7B produced no unparsable answer, which is why
      the mutant is run over both arms and not the first one). Result: tau 0.85 lifts
      the 1.5B's recall to 48/52 = 92.3%, clearing the ≥ 90% clause at that tier,
      while the 7B's recall only reaches 63.6% at ANY threshold in the sweep and its
      routine false offers go 1 → 3, i.e. 1 per 15 tasks → 1 per 5 against < 1 per
      20. Stated limits, in the script's own docstring and not just here: this is a
      re-derivation from recorded fields, not the four streams re-run over the
      answers (the traces keep the evidence, not the source), and the 1.5B's 7
      hidden-accepted answers cannot resolve a per-20-tasks clause at all. Wire the
      finding into the gate rather than around it: the gate stays NOT MET.


## P7 — R-5.3 task-granular recovery → R-5.4 M16 chaos

- [x] [B] Checkpoint in-flight task: partial generation + sandbox state.
      **Built 2026-09-26 as `flash/checkpoint.py`** — one frame per armed session
      keyed by (task, arm, stage, attempt), holding the chat-templated prompt,
      the pending and cumulative decoded text, the token ids (for R-4.2's mask
      replay), the sampler settings, the retry conversation and the caller's
      sandbox state (`merged` file union, targeted-repair list); flushed every
      16 tokens through temp+fsync+rename, with `arm()` sweeping the orphaned
      temps a kill between write and rename leaves. `flash.resume` adopts the
      frame only when it names the task being solved. 31/31 offline checks,
      including two real `kill -9` races (0 torn reads in ~35k parent reads).
- [x] [V] [offline] Kill/resume check extended to mid-task (`--resume` does not
      regenerate attempt 1 of the interrupted task). **Ran 2026-09-26:
      `benchmarks/checkpoint_resume_check.py` 35/35**, seven scenarios, each a
      real SIGKILL on a real `flash run-suite` child resumed by a fresh process
      with only the model scripted — mid-generation, repeat kill inside one span,
      mid-chain, multi-file sandbox union, tournament candidate, big tier,
      inertness. Measured: the resume's prompt begins at the dead run's last
      durable 64 characters and decodes only the remaining 125 of 189; the killed
      task settles `attempts=1`. Stable over five consecutive clean runs (the
      fifth is its line in the P7 docs re-read below), and
      deleting the carried text fails 15 of the 35 (mutation-checked).
- [x] [V] [L] Real `kill -9` between two tokens of a live `run-suite`, then
      `flash resume` → in-flight task completes. **Ran 2026-09-26**
      (`benchmarks/live_checkpoint_arm.py`, 7B small tier, m0's first 3 tasks,
      AC maximum-performance, `--allow-big never`; log
      `benchmarks/results/p7/live_arm_7b_kill_resume.log`). The signal landed
      3.5s in, inside t01's attempt 0, 16 tokens / 62 characters durable;
      `flash resume` settled it `solved=True attempts=1 tier=small`, suite 3/3
      in 15s. The arm then runs the same argv with no kill and prints both
      sessions from their own records: all three answers **byte-identical**
      (sha1, `--trace-full`), t01 `completion_tokens 22/22`, `prompt_tokens 94
      vs 78` — grown by exactly the 16 carried tokens — 455ms of decode against
      the control's 960ms, and the ledgers agreeing at task granularity (1.3s
      resumed vs 1.8s cold, one row per task across the kill and its resume);
      t02/t03 show `resumed=None` and cost the same in both runs. Run twice:
      the earlier pass printed 456ms against a 968ms control.
- [ ] [V] [L] M16: 24h window with random kills, memory pressure, network loss,
      thermal load → 0 data loss, every session closed-or-resumable, no torn
      ledger lines. *(Needs a scheduled 24h window — see SPEC §9.)*
- [x] [B] Docs move together. **Moved 2026-09-26:** SPEC R-5.3 → SHIPPED (the
      offline paragraph carries the 31/31 + 35/35, the seven scenarios, the
      64-carried/125-of-189 measurement, the 15-of-35 mutation and the two
      defects the vector found, then a "Not covered, stated so" clause and the
      live arm's numbers), SPEC §6's battery re-read from the tree **503 → 569
      green** (checkpoint storage 31, recovery vector 35) with each command
      named, SPEC §9's M16 interim now reading "11/11 suite-granular + 31/31
      frame storage + 35/35 task-granular recovery", README's R-5.3 command
      block (`resume`, `checkpoint --selftest`, the two check scripts, the live
      arm), `flash/__init__.py`'s module map and selftest list, PLAN §33.7's
      status paragraph and an Appendix A row. §6 also gained the capture trap
      this re-read hit: a grep for "checks passed" drops `grammar` 47 and
      `debug` 55, which print a bare fraction, so the first pass collected 214
      of 316 and looked like a clean run while missing 102 checks.

## P8 — R-6.4 first learned self-improvement (G9) + LoRA

- [x] [B] LoRA experiment path inside `jobs.py`'s AC+idle gate. (`flash train`
      mines the verified-outcome dataset behind a split law — train/valid by task
      id, so no task straddles — and `flash learn --lora` fits and promotes an
      adapter inside §34.3's budget, resumable, and raising rather than promoting
      a fit that produced no weights. Vectors RUN: `flash.train --selftest` 36/36,
      `benchmarks/lora_path_check.py` 31/31 with 14/14 mutants defeated,
      `flash learn --selftest` 14→20 with the LoRA gate's decision, and a real
      48-step fit taken end to end and paused/resumed under `kill -9`.)
- [ ] [V] [L] Before/after on a frozen suite for one named component (skills,
      memory or weights — whichever lands first), I-2 gate satisfied: the changed
      component beats the frozen harness.
      **Weights arm measured 2026-09-26 and the gate MISSES.** m0's 20 tasks, AC,
      `--attempts 2`, `--allow-big never`: base **18/20** at 7.0 s/task,
      `+lora:v1` **16/20 twice** (identical failure set) at 72.6 / 87.8 s/task,
      shuffled-weight control **19/20** at 7.7 s/task — the control beats the
      trained adapter, so the number attributes to nothing learned. Both halves
      of I-2 miss: −2 tasks and ~10× the seconds. Mechanisms, measured not
      assumed: every extra loss is **shed-tier** (4 escalations denied by §34.1
      where base has 2; on the 16 tasks that did run the small tier the adapter
      is 16/16), and after the answer ends the trained weights **degenerate into
      `!!!!` repetition** with no stop list, so one attempt burns its full
      1024-token budget at 15.6 tok/s. The adapter path itself is clean: load
      0.7–1.1s, TTFT 202–286ms, 14–18 tok/s across base, `v1` and both controls.
      **And the next arm cannot rise either**: §27.3's law admits only
      oracle-verified *successes*, so the base model already solves 12/12 of the
      12 tasks the weights were fit on — the in-distribution question has zero
      headroom by construction. Closing R-6.4 needs rows the current tier *fails*
      or a component with headroom (skills, memory).
- [x] [B] Docs move together. **Moved 2026-09-26, and the gate box above stays
      unchecked on purpose** — the arm missed, so the spec row is OPEN, not
      SHIPPED or PARTIAL. Re-read against the tree: SPEC.md:335 → `R-6.4 (OPEN —
      first arm measured, gate MISSED)` with both halves of I-2's numbers, the
      shed-tier decomposition and the zero-headroom finding at SPEC.md:374; SPEC
      §9's register row (SPEC.md:603) naming the data law as the blocker; SPEC
      §6's battery line carrying +36 train and +31 lora_path; README.md:190-215's
      R-6.4 block (the fit command, the shuffle-control script, `lora_path_check`
      with its 31+14, and the measured 18/20 · 16/20 · 19/20 line);
      `flash/__init__.py`:30's `§27.3/R-6.4: flash.train` map entry; PLAN §34.3's
      close note (PLAN.md:1482) pointing at the decomposition, and the full
      Appendix A row at PLAN.md:1572. All of it landed in `6b1fde5`.
      The §6 re-read is now a run rather than an arithmetic: 
      `benchmarks/battery_reread.py` holds one line per vector, requires the
      exact fraction each prints, sums checks (683) / oracle (20) / mutants (20)
      apart, and exits non-zero if the tree's total moves off the page's number —
      `9/9`-vs-`10/10` and `14`-vs-`15` mutants were both seeded and caught before
      the script was trusted. It exists because the hand-sum hit two capture
      traps (SPEC §6 records both).

## P9 — Product shell and adoption

- [x] [B] R-7.2 ambient mode: idle+AC prepares **draft** diffs only; never
      merges, never auto-applies, nothing pushed.
      *Done 2026-09-26 as `flash/ambient.py`, reached as `flash ambient` (flags
      defined once in the module and shared with `flash.cli`, because argparse's
      REMAINDER cannot carry a leading `--flag`). Git verbs run through an
      allowlist with no `push`; writes are containment-checked against the
      worktree root; a draft is verified by re-running, inside that worktree, the
      very check that found it — and refused if the fix adds a finding or removes
      a line an ADD-only finding was asked only to add. Not a daemon: no launchd
      or cron entry was installed, because that part cannot be audited by a
      reviewer reading this tree.*
- [x] [V] [offline] Check: an overnight run leaves ≥ 1 reviewable draft + trace,
      0 pushes, 0 files touched outside its worktree.
      *Run 2026-09-26, `python -m flash.ambient --selftest` → **61/61**, against a
      temp git repo with a seeded red check and a scripted generator: 3 verified
      drafts each `git apply --check` clean, a trace per window recording every
      git call and attempt, 0 pushes (`git branch -r` empty and no `push` in the
      window's own command log), byte-identical tree outside the worktrees, HEAD
      unmoved. Six mutations break each guarantee on purpose and every one is
      caught (allowlist → a push is issued; containment → the file lands at
      `../../escaped.py`; oracle → a harmful draft ships; additive clause → the
      re-wrapping draft ships; newline carrier → "No newline at end of file"; name
      matcher widened → an unreadable draft goes green).*
      *Live half also met, and it took seven windows to find out why the first five
      failed: Qwen2.5-Coder-7B produced **0** verified drafts on this repo's real
      map until the prompt was fixed, and the tempting story ("this tier cannot
      echo a 45-line file") was **refuted** by `benchmarks/ambient_echo_probe.py`,
      whose 2x2 over echo length × insert count produced a verified draft in all
      four arms — 52 lines of echo with 3 inserts included (74.7s, 2 attempts). The
      mechanism was a FORM failure, captured verbatim by `--inspect`: greedy decode
      writes `ambient — ambient context…`, a bare name the map parser does not
      read. Naming the accepted form in the prompt produced this repo's first
      accepted live draft on the **greedy** attempt, 33.6s (window 6), repeated in
      43.9s (window 7); window 6's filler prose is why the finding now quotes each
      module's own docstring line. Boundary across all seven, read back from the
      repo and the trace store: HEAD unmoved, 0 pushes (no remote configured at
      all), 0 leaked worktrees, 0 files touched outside the worktree. Still open
      and stated: no *unattended* overnight window has run — all seven were daytime
      and `--force`d — and §33.5's CI/dependency watch targets are not read here.*
- [x] [V] [offline] R-9.2 explicit sandbox: every execution path runs under one
      — writable root, no network by default, cpu and file-size rlimits — and the
      hostile candidate (`~/.ssh` write, `socket.connect`) fails as a normal
      verify error.
      *Run 2026-09-27, `python -m flash.sandbox --selftest` → **34/34**, and the
      vector is the clause rather than a paraphrase of it: a candidate that writes
      into `~/.ssh` and one that opens a socket are refused BY THE KERNEL
      (`PermissionError`), the sentinel file still does not exist after the run,
      `urllib` re-wraps the same refusal as `URLError` so a candidate cannot hide
      behind its own `try/except`, and a hostname never resolves either (`gaierror`,
      because name service is itself outbound — the refusal arrives early, not as a
      long timeout). The easy half to fake is the second clause, so it is checked as
      arithmetic: a hostile candidate whose refusal sits after one passing assert
      ranks **1/2** like any partial answer, and `diagnose` returns
      `ERROR: PermissionError` in the same `GOT/WANT/ERROR` shape the retry loop
      consumes. No collateral: a benign candidate is unaffected, `TMPDIR` and
      `tempfile.gettempdir()` both name the root, a relative write lands in the root
      and `getcwd()` IS the root, a child that shells out with `stderr=DEVNULL`
      still works, and a multi-file set still imports its sibling module (`1/2`, not
      an error-out). The rlimits are witnessed FROM INSIDE the sandbox — the child
      reports the `(3, …)` it was given, a busy loop under `cpu=2` died on signal 24
      rather than at its 30 s wall timeout — and `RLIMIT_FSIZE` turned a 600 MB write
      into `OSError: [Errno 27] File too large`. "Every execution path" is verified
      at runtime, not by grep: a check swaps `flash.sandbox.run` for a spy that
      records the caller frame's name and requires all five seams
      (`harness.run_test`, `harness._probes`, `debug._run`, `confidence._seeded_run`,
      `confidence.edge_probe`) to appear, each with a root of its own. `seatbelt()`
      is an enforcement probe, not a `Path.exists()` — the selftest hands it a fake
      wrapper that shifts its own `-p` argument away and requires `False` plus an
      empty prefix, so a box whose sandbox does nothing fails loudly instead of
      reading green. 12 mutants caught (drop the blanket write-deny, drop the root
      allow, single-quote the `subpath`, feed the `/var` symlink form, `seatbelt()`
      reduced to `Path.exists`, remove the cpu limit, remove the FSIZE limit,
      retarget `TMPDIR` back out, drop the null-device exception, unwire each seam),
      tree checksum-verified after each. One mutant leaked the sentinel into
      `~/.ssh`, which is why `reap_own_artifact()` exists and why 3 checks pin that
      it deletes only a file carrying the vector's exact bytes. §6 re-read with the
      new line on a quiet box: `battery_reread` prints
      **`checks 944 oracle 20 §6 total 964 mutants 30`** and matches the page — the
      first attempt, at 5.2/core load, printed 929 because `checkpoint_resume_check`
      refuses its tournament arm under load, and the re-read reported the gap instead
      of accepting it. Also closed on the
      way: `benchmarks/m0_bakeoff.py` carried a duplicate oracle that scored real
      MODEL output through a bare `subprocess.run`; it is an import of
      `flash.harness.run_test` now.*
      *PARTIAL, four named gaps rather than a rounded-off claim:* **(a)** the
      **memory rlimit** — `setrlimit` raises `ValueError: current limit exceeds
      maximum limit` for `RLIMIT_AS`, `RLIMIT_DATA` and `RLIMIT_RSS` at ANY finite
      value on this macOS, at any privilege, so `memory_ceiling()` asks a *fresh
      child* to try each one (the claim tracks the platform, not a table in this
      file) and `status()['memory']` prints `UNAVAILABLE: …`. A hostile candidate
      that allocates on its own initiative therefore has no ceiling here until the
      sandbox runs on a kernel that enforces `RLIMIT_AS`, or §34.1's free-memory
      signal is used as a pre-flight refusal. `RLIMIT_NPROC` was tried and rejected
      as a design: this uid already owns ~436 processes, so anything that binds also
      breaks the user's own shell, and the clause does not name it. **(b)** **reads
      are not confined** — a `(deny default)` read policy breaks the interpreter's
      own dyld and framework lookups, and the clause asks for a writable root, no
      network and rlimits, so this is a write-and-network jail with a cpu and
      file-size ceiling, described as that. **(c)** the **§34 HTML→PNG renderer**
      (headless Chrome) is the one exempt execution path: `--user-data-dir=<root>`
      makes Chrome exit `rc=21` "Failed to create a ProcessSingleton" (measured)
      because it must lock and cache outside a one-shot directory, so its egress is
      killed with `--disable-background-networking --host-resolver-rules="MAP *
      ~NOTFOUND" --proxy-server=http://127.0.0.1:9` instead — byte-identical PNGs,
      +10 s of wall only on a page that fetches — and its wall bound is the named
      `RENDER_TIMEOUT_S = 60`. **(d)** §21's **microVM substrate is still the plan**:
      this is the platform's own kernel mechanism standing in for it, and a box with
      no `sandbox-exec` degrades to no prefix and still verifies (pinned by a check),
      so a Linux rollout host can put a real VM underneath these same five seams.
      Overhead is booked too, because a sandbox nobody can afford gets bypassed:
      26.8 → 29.0 → 39.0 ms per child spawn (wrapper +10.0, whole sandbox +12.2) and
      60 `score()` calls 6.04 s → 8.31 s.*
- [x] [B] [V] [offline] R-7.4 path portability: nothing this repo **executes** or
      tells a user to run may carry the author's absolute directory.
      *Run 2026-09-27, `python benchmarks/portable_paths_check.py` → **14/14 +
      7/7 mutants** (witness `benchmarks/results/portable_paths_r74_20260927.log`),
      launched from `benchmarks/fixtures` rather than the repo root. What was wrong:
      72 context tasks in `m2_tasks.jsonl`, `hint_ab_tasks.jsonl` and
      `hint_ab_candidates.jsonl` stored the author's checkout inside their ORACLE
      `sys.path` bootstrap, so on any other machine each one died with
      `ModuleNotFoundError` before the candidate's first executed line — a suite
      printing verdicts while scoring nothing. The fix is a `<REPO>` token expanded
      in `harness._hoist_path_bootstrap`, chosen because that function is already the
      single choke point all four execution seams pass through (`run_test`,
      `score`'s probe loop, `debug`'s tracer, `confidence`'s seeded re-runs);
      `python -I` implies `-E`, so `PYTHONPATH` could not carry the fixtures instead.
      The vector therefore drives the SEAMS, not the helper: all 72 reference
      solutions pass in all four, the same four reject the 10 frozen wrong answers
      from the 7B, and the pre-expansion literal path still scores the same here, so
      the rewrite bought portability and not semantics.
      **The second finding is the one that mattered.** Reaching the `debug` seam
      showed `flash/debug.py` exec'ing the bootstrap *after* the candidate and
      *inside* the traced region — so a candidate that imports the repository at
      top level died on its own `import` before the traced execution began, and the
      digest reported the harness's crash rather than the candidate's trail.
      **Blast radius measured, not asserted:** all 72 context reference solutions
      were run through `debug._run` against the shipped build and against
      `git show HEAD:flash/debug.py` loaded as a separate module, each with the
      correct path already in place so the probe measures the ORDERING and not the
      token. **42 changed verdict** (old `ModuleNotFoundError` → new `pass`) and
      they are exactly the ones whose candidate imports the repo; the other 30 were
      never affected, because it is the *test* that imports their fixtures and the
      test runs after the bootstrap either way. Pre-existing, not caused by this
      change (proved by scoring a context task's reference solution against the OLD
      absolute path under the old code: `harness.score` → pass, `debug._run` →
      `ModuleNotFoundError`). Fixed: bootstrap to its own file, exec'd before
      `sys.settrace`. This does **not** reopen R-4.3's measured miss, and that is
      checked rather than assumed: the band corpus is self-contained — its two
      bootstrap-carrying tasks (`mw1_ringbuf`, `mw3_registry`) insert `<TMPDIR>`,
      which the driver already has on its path — and the same old-vs-new probe
      gives them **the same verdict and the same 104-line trail**, so both arms of
      that A/B received the mechanism on every one of its 30 tasks. `--selftest` 55/55 and `--suite` 32/32 are unchanged across
      the whole sequence, and that is the lesson, not a reassurance — `dbg_tasks.jsonl`
      carries 0 `sys.path` lines, so neither vector ever built the bootstrap the bug
      lived in. A seam tested only on tasks that do not need its dependency cannot
      see the dependency break.
      Witness data was deliberately NOT rewritten: a record whose text was edited to
      look portable is no longer a record, `adapter_config.json` is read by
      `flash/train.py`, and the embedding caches are keyed by the prompt text the fit
      consumed. `benchmarks/results/**` is therefore excluded from the scan and the
      exclusion is checked rather than granted — 0 executables under it, five named
      record groups, and `docs/portability.md`'s `RECORD_RESIDUE = 410` asserted `≤`
      the live count (35 of 164 record files; 8 of the 410 are this vector's own
      two pass-1 witnesses and 3 more are its pass-2 table, which print the markers
      in their labels). §6 re-read on a quiet
      tree after the fix: `checks 1066  oracle 20  §6 total 1086  mutants 69` with
      all **31** lines OK (`benchmarks/results/battery_reread_r74_20260927.log`),
      and the vector re-run **after** that witness landed — 14/14 + 7/7 with the
      160th record file on disk. (That line is at **15 gates** as of R-7.5's
      clean-clone run, which is where the page total moved to 1067; the counts
      above are what printed on that date.) The scan was then widened with a third
      marker, the
      default Homebrew prefix on Apple Silicon, and it found one line this box had
      declared clean: `README.md` told every reader to create the venv with an
      interpreter at a path that only exists if Homebrew installed into the ARM
      default. Re-run on the widened scan **14/14 + 7/7**; the residue moved
      395 → 403 → 410 and 31 → 33 → 35 files, the first step on eight occurrences
      two failed run traces had carried all along and the rest on this pass's own
      two stored witnesses. The vector also takes a `flock` on its checkout now,
      which is a correctness fix rather than hygiene: two runs at once report
      14/14 gates with the mutant suite under-counted (measured 5/7 and 6/7 in a
      throwaway clone), and an interleaved snapshot-and-restore of
      `docs/portability.md` left one run's `doc_silent` mutation on disk after
      both had exited. Not claimed: that the repo is path-free.
      Claimed: that nothing a clone runs, and nothing a clone is *told to run*,
      depends on this disk.*
- [x] [V] [offline] R-7.5 clean-clone install: `pip install .` in a throwaway venv
      on a fresh clone, `flash` on PATH, and the §6 battery green against the
      **installed package** rather than the checkout, logged under
      `benchmarks/results/`. Until this box is closed no document in this repo may
      say "works on your machine" — R-7.4 only proves no executed file depends on the
      author's path, which is necessary and not sufficient.
      **Closed 2026-09-28 by `python benchmarks/r75_sdist_battery_check.py`, with the
      half of this wording that is physically unavailable kept open rather than
      smoothed.** What ran: the sdist is built, unpacked into a temp directory, and
      installed into a throwaway venv; the driver prints, before it reports anything,
      that `import flash` from outside the tree resolves inside `site-packages` and
      that `import flash` from inside the tree resolves to the unpacked sdist, then
      runs the whole battery there and asserts its own witness carries 0 host paths.
      It printed **33/33** and `checks 1119  oracle 20  §6 total 1139  mutants 85` in
      **13 min 2 s** (`benchmarks/results/r75_sdist_battery_20260928.log`). So the
      battery is green in **the download rather than the checkout** — no path on this
      checkout is on that interpreter's way to a module. What is *not* satisfied is
      the strictest reading of the words "the installed package": with no source tree
      present, 11 of the 33 vectors index the tree they stand in and the rest refuse
      (R-7.10), so that reading cannot produce a green battery at all — the same run
      records `flash selftest --all` exiting **2** naming the `site-packages` path it
      wanted. `SPEC.md` R-7.5 carries both readings and says which one closed. This
      box also corrected its own earlier evidence: the file committed before it as
      `battery_reread_r75_20260928.log` is the checkout's R-7.10c re-read with one
      extra line, not a fresh-clone print.
- [x] [B] [V] [offline] R-7.6 one-command verify: `flash --version`,
      `flash doctor` (python, platform, backend presence, model cache, config,
      whether the offline vectors can run here) and `flash selftest --all`, plus
      `benchmarks/backend_free_check.py` for `--backend-free`. **Closed
      2026-09-27: that vector prints `backend-free checks: 30/30` and
      `backend-free mutants: 5/5`, and it is a §6 line of its own.** The three
      commands are real: `--version` reads `flash.__version__` off the package
      object (it printed `flash 0.0.1`), `doctor` walks nine answer lines across six
      sections and its exit code follows its own page — the vector builds a synthetic
      broken install in a temp dir and requires `no` on exactly 5 of 9 plus rc 1, and
      a complete one with zero `no` plus rc 0 — and `selftest --all` refuses with rc 2
      naming `benchmarks/battery_reread.py` on an install that has no battery, which
      is the wheel lesson R-7.5 learned from `flash.graph --selftest` applied before
      the CI could print a total from checks that never ran.
      The published workflows are un-dangled: `ci.yml`'s `flash doctor`,
      `flash selftest --all`, `flash --version` and `python
      benchmarks/backend_free_check.py` and `nightly.yml`'s two all resolve, which is
      gated by R-7.8 rather than asserted here.
      What the parser probe said when this box was still open, kept because it is the
      reason the box was worth opening: **21 of the 23 `flash` subcommands the tracked
      docs reference existed**, and the two that did not were exactly `doctor` and
      `selftest`. R-7.8's collector supersedes the count — it reads the documents, not
      a list, and measures **25 commands across 150 citations in 15 documents** (111
      at its first green print; the figure moves with the prose), all
      resolving. The same probe
      settled a number two documents cited differently — with `mlx` blocked
      **24 of the 26** modules imported (pyproject and `docs/models.md` said 24, the
      CI comment said 25), the two failures being `flash.decide` at its top-level
      `import mlx.core` and `flash.route` through it, while `flash.cli` imports
      because it loads both lazily; `benchmarks/trace_resume_check.py` was likewise
      still **11/11** under the same block, so `--backend-free` is a measured subset
      and not a list of names. `flash/__init__.py` is `__version__ = "0.0.1"`, which
      is what a wheel built today would be labelled — TODO #32 bumps it.
- [x] [B] [V] [offline] R-7.7 whole package imports with no MLX present: move
      `flash/decide.py`'s top-level `import mlx.core` behind the call so
      `import flash.route` works everywhere, keeping the failure at the generation
      call with a named-backend message. **Closed 2026-09-27.** Both halves the box
      demanded are gated, in child processes under the shim: the sweep prints
      `SWEEP 26/26` — numerator equal to denominator, denominator at least 26 — and
      `decide(None, None, 'ctx', ['a','b'])` prints
      `RUNTIMEERROR:…flash.decide.decide() needs MLX…` rather than a
      `ModuleNotFoundError` or an empty verdict. The mutant that pays for the sweep
      writes a real throwaway submodule (`_zz_backend_probe.py`, planted in the
      `flash` package directory and deleted after the run — R-7.8's file-existence
      gate is what made this sentence stop naming a path a reader could not open)
      carrying `import mlx.core` at top
      level onto disk while the gates run and removes it afterwards; exactly the sweep
      gate fails, which is the proof that "26/26" is an enumeration and not the two
      names this vector first happened to check.
      *The pre-fix measurement stands:* **24 of the 26** modules imported with `mlx`
      blocked, `decide` and `route` being the two that did not, unnoticed because
      `flash.cli` imports both lazily.
- [x] [B] [V] [offline] R-7.8 documented commands parse: every `flash …` line in a
      tracked doc goes through `flash.cli.build_parser()` without dispatching, and
      any rejection fails the gate naming the file and line. **Closed 2026-09-27:
      `documented-command checks: 7/7`, `documented-command mutants: 4/4`.** The
      collector covers fences, inline spans and the workflows' `run:` lines and its
      own spine is a gate — **25 commands / 150 citations / 15 documents / 72 source
      paths** (the citation figure is that gate's live print, which has moved 111 →
      112 as the README grew a block; the gate asserts a floor, not this number) —
      with a planted bogus command required to be rejected, so the vector
      cannot go blind and still print a pass. Resolution is `parse_args([cmd])` under
      a swallowed stderr, never dispatch: a documented `flash run` would create a
      worktree and a documented `flash selftest --all` takes minutes. `--tasks …`
      placeholders parse as arguments. §1350's "docs move together" rule had never
      been enforced, which is how `CONTRIBUTING.md` came to ship `flash doctor` in a
      copy-pasteable setup block; that block now runs.
      Mutation-check, verbatim from SPEC: `selftest` and `doctor` are deleted from the
      live parser's subchoices while the gates run, two gates fail, and the vector
      accepts that as caught only if the failure text matches `\.(md|yml):\d+` — a
      rejection that does not name the citing file and line does not count.
- [x] [B] [V] [offline] R-7.9 `--dry-run` writes nothing, on every sub-command that
      accepts it. **Closed 2026-09-27: `lora path: 33/33 checks passed`,
      `mutations: 15/15 gates defeated by exactly their checks`.**
      `python -m flash.train --dry-run` promised "write nothing" and `main()` honoured
      it for `--dataset` only: `--suite-from-dataset` was dispatched above that branch
      with no `dry` argument, so the command rewritten here as a dry run modified the
      tracked `benchmarks/tasks/r64_train_from_dataset.jsonl`. `git status` was the
      witness; nothing in the battery would have said so, which is why the two new
      checks are on `flash.train.main()` and run with **no `--suite-out`** — the
      default name is the one that lands in the tree. `suite_from_dataset()` skips the
      `mkdir` along with the `write_text` and prints `-> would write <path>`; the
      companion check calls the same command without the flag and requires the file to
      appear, so an empty temp directory cannot be "the command silently did nothing".
      `TASK_DIR` is repointed at a copy of the suites for both halves, so the mutant
      cannot dirty the tree it is measuring.
- [x] [B] [V] [offline] R-7.10 a selftest that cannot run on this install says so on
      every install shape, and R-7.10b `flash doctor` names the shape correctly.
      **Closed 2026-09-27: `backend-free checks: 37/37 passed`,
      `backend-free mutants: 8/8 gates defeated by exactly their checks`.**
      R-7.5's wheel run left three loose ends behind this box picked up: on a
      `pip install .` into a throwaway venv, `python -m flash.grammar --selftest` and
      `-m flash.debug --selftest` raised `FileNotFoundError` for a
      `benchmarks/tasks/*.jsonl` path inside `site-packages`, and `-m flash.patches
      --selftest` printed one `FAIL` inside an otherwise-green 46-check report. All
      four now call `doctor.vector_refusal(mod, _DATA_ROOT)` — one function, a
      four-entry `doctor.VECTOR_DATA` table, exit 2 and one sentence — where
      `flash.graph` had been refusing alone. Six new gates: four for the refusals at
      each module's own seam (rc 2, the exact missing path in the text, **no
      `Traceback`**), one requiring the same guards to say *nothing* when the four
      paths are present (a refusal that is a constant is a deleted vector, not an
      honest one), one that every table entry sit under `benchmarks/` (a
      package-side path makes the guard answer "present" forever). Three mutants:
      always-refuses, table entry moved inside `flash/`, and the editable answer
      going back to `sys.executable`. **R-7.10b** is that last one: a wheel install
      printed `(editable checkout)` because `Path(sys.executable).resolve()` on a
      macOS venv lands in the Homebrew framework, which is not a prefix of the
      package's own path; `_is_editable` now asks whether the path is under
      `site-packages`/`dist-packages`, and `site_install()` builds exactly that
      directory to gate it. Live repeat in a package-only tree, from a directory
      holding neither `flash/` nor `benchmarks/`: four sentences, zero tracebacks.
- [x] [B] [V] [offline] R-7.10c the wheel-refusal table is swept, not remembered:
      every module that exposes a `run_selftest` is found in a package-only copy and
      must either be tabled or be on a named machine-state list.
      **Closed 2026-09-27: `backend-free checks: 42/42 passed`,
      `backend-free mutants: 10/10 gates defeated by exactly their checks`, and the
      §6 re-read printed `checks 1119  oracle 20  §6 total 1139  mutants 85` with all
      33 lines OK in a measured 15 min 9 s
      (`benchmarks/results/battery_reread_r710c_20260927.log`).**
      The table of four was correct for four and silent about the other twelve, and
      the R-7.5 rerun proved it mattered: on the same wheel install
      `python -m flash.tourney --selftest` exited **1** with `FileNotFoundError` —
      its task path was resolved against the **caller's cwd**, so it was broken in a
      clone as well — and `python -m flash.lsp --selftest` exited **1** with
      `ValueError: substring not found`, three frames of AST work away from the
      missing fixture and naming no path at all. Both are fixed (`_DATA_ROOT` +
      `vector_refusal`, so the table is six) and both now print 16/16 and 22/22 from
      a foreign cwd in a clone. The vector: `pkg_only_copy()` copies `flash/` to a
      temp dir with no `benchmarks/` next to it, `selftest_mods()` discovers the
      module list by AST off the **copy's sources**, `run_in_copy()` runs each one
      from a cwd that holds neither tree, and any death is re-run in a second copy
      with the data symlinked back in — passes-with-data is a data-dependence that
      must be tabled, fails-either-way is this box's state and is allowed only on a
      three-entry named list (`ambient`, `power`, `sandbox`). Three new gates (spine,
      untabled, machine-state) and two new mutants. The first mutant run printed
      **0 checks failing**, because the planted module had no `if __name__ ==
      "__main__"` tail and `python -m … --selftest` on such a module imports it and
      exits 0; the probe gained the tail and the spine gate gained "every swept
      module was dispatched", which is the difference between *did not crash* and
      *ran*. On the closing run: **16 swept, 6 refused, refusals == the table's
      keys**.
- [x] [V] [offline] R-7.5 `pip install .` on a FRESH CLONE produces a working `flash`
      entry point. **Closed 2026-09-28 by
      `benchmarks/r75_fresh_install_check.py`: the sdist builds, installs into a
      throwaway venv, `flash --version` prints `flash 0.0.1` and `flash doctor` exits 0
      against the installed copy — about 2 min including venv creation and cleanup.**
      This box is clause 1 only; the battery half of R-7.5 is the box above, which is
      where the 33-line download run and the witness that was mislabelled as one are
      recorded. The entry point itself comes from `[project.scripts]` in
      `pyproject.toml`, and the verification surface a stranger needs to re-run the
      numbers ships with the source per `MANIFEST.in`.
- [x] [V] [offline] R-7.11 a landing page whose numbers cannot be invented:
      **Closed 2026-09-28. `site/` is a Vite + React page and its only data source is
      `site/src/data/{benchmarks,graph,transcripts}.json`, written by
      `python benchmarks/export_site_data.py` from `benchmarks/results/dashboard_data.json`,
      which `python benchmarks/dashboard_data.py` produced by measuring: the §6 witness
      parsed for the 33 printed fractions (**1,119 + 20 = 1,139**, **85** mutants) and
      `python -m flash.<mod> --selftest` timed **n=3** per module with min and max
      published. Three things this box exists to record:
      (1) **the fabrication and its revert** — commit `54a2117` published four dashboard
      PNGs with invented competitor latencies and a hand-typed cost table; reverted as
      `69a4d2d`, and the pipeline is now the control, because there is no code path from
      a typed figure to the screen;
      (2) **the exporter caught a bug in itself** — `build_ms` read capture group 4 of the
      cold-index line, which is the edge count, so the panel would have printed
      **10,058 ms** as an index build; cross-checking the 33 generated commands against
      `battery_reread.BATTERY` is what surfaced it, and the fix was a re-measure
      (**603 ms**), not a retype;
      (3) **the page's first shipped defect was a blank page** — three.js throws when no
      WebGL context can be made, an uncaught error in a child unmounts the whole React
      tree, and headless Chrome's console said so
      (`Uncaught Error: THREE.WebGLRenderer: Error creating WebGL context.`) against a
      33 KB DOM with an empty `#root`. The hero now probes for a context, a software
      renderer and `prefers-reduced-motion` and falls back to an interactive SVG of the
      same 150 symbols; rendered DOM **173 KB**, all eight sections present, and the
      914 KB three.js chunk is never downloaded on the fallback path.
      Verified by driving a real scroll over the Chrome DevTools Protocol at 1440×900 and
      390×844 — `chrome --screenshot` cannot be used for this, because it captures one
      viewport inside a virtual-time budget and an `IntersectionObserver` entrance paints
      blank, which is indistinguishable from broken. `npx tsc -b` silent,
      `npm run build` clean, `python -m pyflakes flash/*.py benchmarks/*.py` **0
      findings**, `benchmarks/portable_paths_check.py` **15/15 + 7/7 mutants**.**
- [ ] [V] [L] R-7.3 voice: real-microphone arm, VAD barge-in, ≥ 90% command
      recognition over 50 utterances.
- [ ] [V] [L] M17 feel test: ≥ 7 of 10 developers keep it after a week.
      *(Needs humans — SPEC §9 register.)*

## Backlog (OPEN, not scheduled; each needs ledger demand to earn a slot)

- [x] R-1.3 knowledge graph (`flash graph <symbol>` blast radius < 200ms)
      *Run 2026-09-27, `python -m flash.graph --selftest --mutants` → **44/44 +
      12/12 mutants**, and `python -m pyflakes flash/*.py benchmarks/*.py` → 0
      findings. `flash/graph.py` is AST-only (no embedding, no vector store):
      nodes are functions/classes/modules/module-level constants, edges are
      calls/reads/imports/inheritance, and each edge carries the file, line and
      source text that proves it plus the `via` label of the rule that bound it.
      The clause's own repo answers in **0.1 ms** of the 200 ms budget, and that
      is stated as a weakness rather than banked — five files cannot bound a
      budget — so the same check also runs on a generated 401-file repo (5377
      nodes, 10058 edges) where depth-3 blast costs **29–106 ms cold** across the
      runs taken. Three judgement calls went the honest way and each has a named
      mutant: an attribute name matching two symbols becomes **no edge** (but is
      still counted in `blind_spots()`, so a hole cannot be silently dropped),
      `json.dumps` and `list.append` are boundaries not holes, and parameters are
      locals not callers. The 11th mutant is the one that justifies the instrument
      size: it replaces the cached reverse index with a scan of every edge per hop,
      which the budget check catches at 443–943 ms on 400 files and would NOT catch
      on the fixtures repo, where the scan measured *faster* than the index
      (0.08 ms vs 0.11 ms). That is why the wide repo's size is part of the gate.
      Incremental `merge()` is checked by hash (re-extracts exactly the one changed
      file; a second pass over the wide repo re-extracts nothing) and the shrink
      guard is checked against its worst case — a scan of an empty directory may
      not delete 5377 nodes. The LSP seam is wired, not stubbed: `live_upgrade`
      asks jedi about this pass's own blind spots and reports what it settled
      (measured 1 asked, 1 settled), deliberately outside the 200 ms clause — and
      with no server answering it returns `no language server answered` rather than
      raising, which is a check without a mutant because the sweep passes
      `live=False` on purpose (twelve mutants cannot each start a server).
      Writing the README is what found a real defect: the claim "the nearest
      addresses are offered" had no witness, so pinning it meant trying a needle
      like `Cart.` — whose last dotted component is the EMPTY string, a substring
      of every symbol — and watching it offer five arbitrary nodes as neighbours.
      That is the mirror image of the failure the absence answer exists to avoid,
      now guarded in `nearest()` and covered from both directions (wrong list, and
      no list invented).
      Battery: `flash.graph --selftest` joins SPEC §6's re-read at 44 checks + 12
      mutants, moving the tree's printed totals to 997 + 20 = **1017 green,
      offline**, 42 mutants.*
- [x] [B] **Correction, R-1.1 (2026-09-27): the clause was marked SHIPPED on a
      vector that did not test it.** Picked up while choosing R-1.3b's seam — the
      graph was about to be injected into a path that turned out not to reach the
      model. SPEC R-1.1 says *resolve a symbol named in an error to its real
      source and inject it into retry feedback*. `lsp.symbol_hint` did the
      resolving; `loop.solve` called it inside the `Attempt(...)` constructor, so
      its output went into the **record** and the retry prompt was built from the
      plain `err`. `flash trace` prints that record, which is why this survived a
      month of green vectors and a "live" note in the README.
      Proof it was dead, three ways: the code had one call site and it was in the
      record constructor; a stubbed-`_generate` probe showed the retry prompt
      lacking `BULK_MIN_QTY` before the fix and carrying it after; and
      `benchmarks/hint_live_audit.py` finds the header in **0 of 248** stored
      prompts (56 of them retry prompts) across every `--trace-full` run captured
      before today.
      Fix: `err = _symbol_hint(task, err, code)` on a failed attempt before both
      the record and the feedback template are built — one string, so the model
      and the trace see the same thing. *(Name updated when R-1.3b landed: that
      call is now `_perceive(task, err, code)`, which appends the LSP source block
      and the graph's dependents block at the same assignment. The fix itself is
      unchanged — same position, one string, one consumer that is also the record.)*
      Vector moved: three `wiring:` checks added to `flash lsp-selftest` (**14 →
      17**), driving `loop.solve` itself with a stubbed generator and reading the
      retry message back: the source arrives, it arrives verbatim and exactly
      once, and (control) a task with no repo context gains nothing. Mutation is
      hand-run and logged — put the old line back, the two prompt checks FAIL and
      the control still passes, **15/17**
      (`benchmarks/results/lsp_wiring_mutation_20260927.log`). Live arm re-taken
      on the real 7B (`run-suite --with-context --allow-big never --attempts 3
      --trace-full`, session `20260927-035552-run-suite-b2b4`): both retries carry
      `Symbols in play` with `models.py:21 CartLine` and `pricing.py:5
      BULK_MIN_QTY`, `hint_live_audit.py --expect present` exits 0, and the
      pre-fix half still exits 1 if any older prompt is found carrying it
      (`benchmarks/results/hint_live_audit_20260927.log`). r03 still did not solve
      on the small tier, so **no accuracy delta is claimed** — only that the text
      now reaches the model. Battery: 997 + 20 = 1017 → **1000 + 20 = 1020**,
      mutants still 42. What this voids: any live delta credited to symbol
      injection before today, including the 2026-09-25 README note (corrected in
      place, not deleted). What it does not void: the M2 context-skeleton A/B —
      the skeleton is prepended to the prompt itself, and `Project context (real
      API` appears in 10 of those same 248 captured prompts, which is exactly the
      difference: one path wrote into `prompt`, the other only into the record.*
- [x] R-1.1b does symbol injection actually HELP? A/B on a frozen suite, hint on
      vs off, same model, same seeds — the clause is now live-verified in the
      prompt (`benchmarks/hint_live_audit.py --expect present`) and its outcome
      value has never been measured, because the one live note that claimed it was
      read from the record. Gate: a stated delta or a stated nil.
      *(Switch, updated by R-1.3b: `_perceive` is now the single call site and it
      appends TWO blocks, so an arm that turns "the hint" off must say which —
      `--no-source-hint`, `--no-graph-hint`, or both — or the A/B measures one
      block while believing it measured the pair. Both arms must be scored with
      `--trace-full` and the injection presence proven per-arm by
      `benchmarks/hint_live_audit.py --header …`, which now takes the header as an
      argument precisely so each arm can be certified separately.)*
      *(Prerequisite, cleared 2026-09-27 by R-1.1c below: with the budget aborting on
      the first oversized symbol, an arm could come back with no source block because
      the failure happened to name a long function — which would have made the A/B
      compare an invisible arm against an invisible control. Empty now means "nothing
      repo-defined is at issue", and that is the only silence this arm produces.)*
      **Result (2026-09-27): a stated nil, which is what this box's gate asked for.**
      The two blocks are now separately switchable (`loop.HINTS`,
      `_perceive(…, hints=…)`, `--no-source-hint` / `--no-graph-hint`, both recorded
      in `SUITE_PARAMS` so a trace states its own arm), and the question was run as
      four arms over a frozen 10-task suite at `--attempts 3 --allow-big never
      --trace-full`: pass@N is **both 3/10, graph only 2/10, source only 2/10, off
      2/10**, pass@1 is **0/10 in every arm** and is the control rather than a
      measurement, because all four arms share one greedy attempt-0 answer per task.
      The injection columns prove the switches are live: the OFF arm shows a block on
      0 of 18 retries, `source only` on 18 of 18, `graph only` on 0, and the two are
      never transposed. So `both` is **+1 task over off at n=10 — one task, ten
      points** — and at that sample size the per-task flips are not ordered by arm:
      `hc16_paid_line_cents` is solved by `both`, `graph only` AND `off` and lost by
      `source only`; `hc36_add_twice_qty` is solved by `both` and `source only` and
      not by `graph only` or `off`. Retries move 18→17, which is the same single
      task. Neither block is shown to help and neither is shown to hurt; this box
      does not license turning either switch off, and it does not license a win.
      *The durable finding is about the instrument, and it is why the box closes here
      instead of iterating: 8 of 10 tasks are unsolved in all four arms, so the band
      has ~2 tasks of headroom and cannot report a real effect — see SPEC §5's R-1.1b
      for the re-pick-the-boundary follow-up, which is explicitly NOT booked as a
      requirement and has no number projected from it.*
      Vectors: `benchmarks/hint_ab_check.py` offline (**14/14 + 8/8 mutants**,
      `benchmarks/results/hint_ab_offline_20260927.log`), whose 10th and 11th checks
      certify eligibility at the seam R-1.1 exposed (`loop.solve` over a stubbed
      generator shows both headers, and shows the four arms as four different
      prompts), and `benchmarks/hint_ab_report.py` live
      (`benchmarks/results/hint_ab_live_20260927.log`).
- [x] **R-1.1c (SHIPPED, found by R-1.3b's vector)** A failure whose top-ranked symbol
      is long lost the source hint entirely. `lsp.symbol_hint`'s budget check was a
      `break`, so when the FIRST ranked symbol's own source exceeded `max_chars=1200`
      the loop never reached the symbols that would have fit and the empty block
      returned `""` — the block did not shrink, it vanished. Measured on this repo
      (2026-09-27): `err="lsp.symbol_source is broken: AttributeError"` ranks
      `symbol_source` at **1631 chars** and `broken` at **72**, and
      `symbol_hint("flash", err, …)` returned **0 characters**.
      Gate: a too-long symbol contributes a truncated block with a stated tail —
      `graph.scope_hint`'s `… N more not shown (context budget)` is the in-repo
      precedent — and never a silent zero while a ranked symbol sits unused; plus a
      check that a non-empty ranking yields a non-empty block.
      *Why it is booked rather than fixed here: it changes R-1.1's shipped semantics
      and its 17/17 vector, and R-1.3b's commit is the graph's wiring. It also
      blocks R-1.1b: with this live, a hint-OFF arm can come back empty for a reason
      nobody measured, and the A/B would compare an invisible arm against an
      invisible control. Found because `_shared_index_cost` needed a symbol that
      fits on `flash/` to have a source block to compare at all.*
      **Result (2026-09-27).** The budget now overflows: the top hit is clipped at a
      LINE boundary by the new `_clip` (which refuses to emit anything that cannot
      hold a signature plus a `def`, so a tiny budget yields silence rather than a
      header over an empty block), the note `… source truncated here (context
      budget)` is kept inside the ceiling rather than appended past it, and the
      symbols that then had no room are counted: `… N symbol(s) at issue not shown
      (context budget)`. Re-measured on this repo with the box's own shape — an error
      naming `symbol_source`, which ranks it first with **1631 chars of source (3276
      for its whole block, location and signature line included)** against the 1200
      budget, beside ranked symbols of **183** and **102** chars: **0 → 1118 chars**,
      tail `… 2 symbol(s) at issue not shown (context budget)`. Vector: `flash
      lsp-selftest` **22/22**, five new checks as section 6c — four on the helper
      (clipped-with-`def`-intact at 1156 of 1200; the tail counted; the ceiling held
      at every size that can hold a signature; 150 → `''` while 1100 → a block) and
      the fifth at the seam R-1.1 demands, driving `loop.solve` with a stubbed
      generator and reading the retry back: **1592 chars, clipped block present**.
      Mutation, hand-run like R-1.1's and kept in
      `benchmarks/results/lsp_r11c_mutation_20260927.log`: the pre-fix `break`
      installed over the shipped function, the shipped selftest run UNMODIFIED →
      **18/22**; three of the four helper checks FAIL (`0 chars against a 1200
      budget`, `no block at all`, and the too-small-budget one failing on its
      1100-char arm while printing its static message) and the seam check FAILs at 394
      chars of retry — the graph's block arrived, the source block did not. Stated at
      precision: the fourth (ceiling) check survives the mutant VACUOUSLY, `0 and 0`,
      because an empty block is under any ceiling; it is a no-overshoot guard and is
      not part of the defeat. Battery **+5**, **1027 → 1032** checks, mutants
      unchanged at 51, `§6 total 1047 → 1052`, re-read quiet on the tree with all 29
      lines OK and `flash lsp-selftest 22/22` the only line that moved
      (`benchmarks/results/battery_reread_r11c_20260927.log`); the vector's own run is
      `benchmarks/results/lsp_r11c_selftest_20260927.log`. **R-1.1b is unblocked**: a
      hint-OFF arm can now only be empty because nothing repo-defined was at issue.
      *(Two notes on the fix's shape, because both were bugs met on the way: the clip
      branch initially appended a block without marking `emitted`, so the whole hint
      still returned `""`; and reserving no room for the tail note made it report zero
      skipped symbols — an overflow that said nothing about overflowing.)*
- [x] R-1.3b feed the graph's subgraph into `loop.py`'s PERCEIVE context
      (PLAN §28.2 step 3). The graph and its CLI answer ship; the agent does not
      yet consult it unprompted. *(Seam note, 2026-09-27: inject it where R-1.1
      now injects — into `err` before the `Attempt` and the feedback template are
      both built. Anything added to the record side alone will pass every check
      and reach no model; that is precisely the bug corrected above. The vector
      has to read the retry message back, and
      `benchmarks/hint_live_audit.py`'s `--expect present` is the shape a live arm
      for this should take.)*
      **Shipped 2026-09-27, at the seam the note names.** `graph.scope_hint(root,
      err, code)` ranks the at-issue symbols through `lsp.symbols_involved` — the
      SAME function the source hint ranks with, so the two blocks answer about one
      set — takes each to depth **2** (§28.2's own words are "small context"), caps
      at 6 dependents per symbol and **900 characters** with a stated
      `… N more not shown (context budget)` tail, and returns `""` rather than
      guessing when a name binds two graph nodes and the file the AST read does not
      settle it. `loop._perceive` appends both blocks to `err` at the one call site
      in `solve`, and `_repo_index` parses the repo ONCE per retry for both —
      measured on this repo's own 26 files at **32-33 ms shared against ~170 ms
      parsed twice**, a number the vector prints. The per-root graph cache is an LRU
      of 8 roots, because a suite over ten repos is a suite holding ten graphs.
      Vector: `benchmarks/graph_perceive_check.py` **27/27 + 9/9 mutants** (the file
      prints **33/33 + 12/12** from R-1.1b onward — its switch checks joined it), run for
      §6 as `--sweep` (one fresh process per mutant). Three of the 27 drive
      `loop.solve` with a stubbed generator and read the retry message back; one is
      the control that a first attempt pays nothing; one requires every cited
      `file:line` to be re-read from disk and match the call text it quotes.
      Two of the nine mutants are the reason this box was worth opening:
      `record_only` puts R-1.1's bug back at THIS seam (compute the block, never
      append it — 6 checks fail), and `nested` re-creates the assembly this shipped
      with first, where the graph ranked over the source hint's own quoted text and
      one retry showed two different symbol sets for one failure.
      Live arm, real 7B (`run-suite --with-context --allow-big never --attempts 3
      --trace-full`, session `20260927-044613-run-suite-d366`): both stored retry
      prompts of `r03_bulk_rule` carry `Dependents of the symbols at issue`, headed
      `CartLine [class] minishop/models.py:20 — 2 symbol(s) reach it` with
      `d1 minishop/cart.py::Cart.add calls it: self.lines.append(CartLine(product,
      qty)) [minishop/cart.py:18 via import-alias]` under it, and
      `BULK_MIN_QTY [constant] minishop/pricing.py:5` reaching to depth 2 at
      `Cart.subtotal_cents`. `hint_live_audit.py` grew a `--header` for exactly this
      and now reads **2 of 6** post-fix retries carrying it, **0 of 248** pre-fix
      prompts (`benchmarks/results/hint_live_audit_graph_20260927.log`). **No
      accuracy delta is claimed** — r03 failed all three small-tier attempts, as it
      always has, and whether either hint helps at all is R-1.1b's open question.
      Battery: **+27 checks, +9 mutants**, **1020 → 1027**, mutants **42 → 51**;
      re-read on a quiet box as `checks 1027 oracle 20 §6 total 1047 mutants 51`,
      all 29 lines OK
      (`benchmarks/results/battery_reread_r13b_20260927.log`), pyflakes 0. What it
      found on the way is booked as **R-1.1c** above rather than fixed here — it has
      since shipped, and the shared-parse figure has reprinted 32 ms since, so the
      ratio rather than the millisecond is what holds.*
- [ ] R-1.4 second language for perception (choose from ledger evidence)
- [ ] R-8.2 latent compute — adopt only on a measured ≥ 20% token saving
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
