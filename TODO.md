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
      is exact, not a superset: 12 000 sampled pieces, 0 refusals, 0 dead ends.*
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
- [ ] [V] [L] Contract-violation census: 100 sampled unconstrained generations
      → N violations; same 100 constrained → **0** *structural* violations.
      Running now: 25 free + 25 constrained (latency pair) + 75 constrained.
      `malformed` (a DFA-dead answer) and mask breaches must be 0;
      `unclosed`/`file set` are completeness misses — a stop inside a body or
      an exhausted budget, which no logit mask can or should forbid. The two
      arms are measured on the same yardstick (`conformance`) so N stays
      comparable.
- [ ] [V] [L] Latency cost ≤ 5%: same seeds, constrained vs unconstrained tok/s.
- [ ] [V] [L] Reference sweep: `run-suite --tasks mw_tasks.jsonl` still 6/6 and
      m0 unaffected (a harness change invalidates stored pass rates).
- [ ] [B] Docs move together: README command, `flash/__init__.py` map,
      SPEC R-4.2 → SHIPPED, PLAN §33.3 status + Appendix A row.

## P2 — R-4.3 Debugger skill (watch execution, don't re-guess)

- [ ] [B] `flash/debug.py`: pdb-based session — run to breakpoint, capture locals
      at the failing frame, one-shot stepping, serialised trace digest.
- [ ] [B] Feed the digest into retry feedback as a distinct `kind="debug"`.
- [ ] [V] [offline] `python -m flash.debug --selftest` on seeded bugs:
      locates the mutating line, no model involved.
- [ ] [B] Seeded-bug suite `benchmarks/tasks/dbg_tasks.jsonl` (≥ 6 tasks where
      the traceback alone is misleading).
- [ ] [V] [L] Arm A: traceback feedback. Arm B: debug feedback. Same tier, same
      budget → B solves **≥ 2 more**.
- [ ] [B] Docs move together (SPEC R-4.3, README, §33.2 status, Appendix A).

## P3 — R-8.1 Speculative decoding (G5: brain ≥ 46 tok/s)

- [ ] [B] Draft model selection (small tier drafts, 30B verifies) in the bake-off
      harness.
- [ ] [V] [L] `m0_bakeoff.py --run` with and without speculation on the 30B.
- [ ] [V] [L] Acceptance test: pass rate within 1 of the frozen baseline,
      throughput ≥ 46 tok/s, else the claim is recorded as a negative.
- [ ] [B] Docs move together.

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

- 2026-09-25 — P0 complete. Repo initialised; baseline `0ea2798` (72 files,
  `.venv`/`__pycache__`/`benchmarks/cache` ignored, results+traces tracked as
  evidence). Working tree clean.
