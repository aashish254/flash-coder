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

# §33.1 symbol perception: AST discovery + a live language server (jedi/pylsp).
# The loop uses this automatically: a failure that names a repo symbol gets that
# symbol's REAL source appended to the retry feedback.
.venv/bin/python -m flash.cli find total_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli refs total_cents --path benchmarks/fixtures
.venv/bin/python -m flash.cli symbols --path benchmarks/fixtures          # whole tree (AST)
.venv/bin/python -m flash.cli lsp-selftest                                # 14 offline checks

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
.venv/bin/python -m flash.cli learn --selftest         # 14 offline gate + resume checks

# §33.6/§33.7 observability, replay, resume — the trace session IS the snapshot
.venv/bin/python -m flash.cli run-suite --trace-full   # also store exact prompts/outputs
.venv/bin/python -m flash.cli trace                    # list sessions: outcome, tokens, wall time
.venv/bin/python -m flash.cli trace <sid> --task r03_bulk_rule   # replay one task's decisions
.venv/bin/python -m flash.cli resume                   # continue the newest interrupted suite
.venv/bin/python -m flash.cli trace --selftest         # 29 offline store/replay checks
.venv/bin/python benchmarks/trace_resume_check.py      # 11 checks: interrupt -> resume, no re-billing
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
