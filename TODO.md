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
- [ ] [B] Docs move together: README command, `flash/__init__.py` map,
      SPEC R-4.2 → SHIPPED, PLAN §33.3 status + Appendix A row.

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
- [ ] [B] Docs move together (SPEC R-4.3, README, §33.2 status, Appendix A).

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

- [ ] [B] Emit/apply range-addressed patches: LSP+AST give (file, symbol, range);
      patch replaces a symbol body, never a text guess.
- [ ] [V] [offline] Selftest: an out-of-range or overlapping patch is refused.
- [ ] [B] 10-change suite (`benchmarks/tasks/edit_tasks.jsonl`).
- [ ] [V] [L] ≥ 8 changes solved in ≤ 1 attempt, and 0 edits touch lines outside
      the target symbol's range.
- [ ] [B] Docs move together.

## P5 — R-3.3 Tournament mode (G2, AC-only)

- [ ] [B] k-candidate sampling under the governor's width cap, oracle-scored,
      best-of-k adopted.
- [ ] [V] [L] Hard family (h-tasks): best-of-3 beats single-attempt by ≥ 8 points
      at ≤ the same total token spend, on AC.
- [ ] [B] Docs move together.

## P6 — R-2.3 confidence from verification + R-6.3 label hygiene

- [ ] [B] Prospective confidence signal from static diagnostics / coverage /
      suite flakiness (not model probability).
- [ ] [V] [L] Seeded subtle-bug suite: offered on ≥ 90% of outputs that would
      fail hidden tests, < 1 false escalation per 20 routine tasks.
- [ ] [V] [offline] `trainable()` exclusion audit: AUC on excluded-only re-fit ==
      AUC on all-minus-override re-fit (no signal lost).
- [ ] [B] Docs move together (SPEC R-2.3, R-6.3).

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
  arm's answers averaged 150-330 tokens where the unconstrained arm consumed
  the whole 1500-token budget to say less, which is why the latency claim is
  measured as `--overhead` (ms of mask per decode step) rather than as
  end-to-end tok/s: the two arms do not generate the same workload.
- 2026-09-25 — P0 complete. Repo initialised; baseline `0ea2798` (72 files,
  `.venv`/`__pycache__`/`benchmarks/cache` ignored, results+traces tracked as
  evidence). Working tree clean.
