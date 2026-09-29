# Architecture

What runs when you type `flash run "…"`, which module owns which decision, and
the two rules that make the rest of the design necessary.

## The loop

```
                 ┌──────────────────────────────────────────────┐
                 │                                              │
 task ──▶ PERCEIVE ─▶ ROUTE ─▶ ACT (fast tier) ─▶ VERIFY ─┬─ ok ─▶ report, ledger
                 ▲                                        │
                 │            retry prompt: bare error    │ fail
                 │              + source block (R-1.1)    ▼
                 │              + dependents block (R-1.3b) ACT again (≤ attempts)
                 │                                        │
                 └────────────────────────────────────────┴─ exhausted ─▶ ESCALATE?
                                                              (governor + router)
```

- **PERCEIVE** `flash/context.py` builds the token-budgeted repo skeleton that
  turns 0 into a first answer that knows the project; `flash/perceive.py` runs one
  file's static diagnostics as an oracle; `flash/lsp.py` ranks the symbols a
  failure is actually about and quotes their real source; `flash/graph.py` answers
  who calls this symbol and what breaks if it changes — of a Python file by
  default, and of TypeScript/TSX too when the optional `ts` grammar is installed,
  because `flash/lang_ts.py` fills the same `Node`/`Edge`/`Unresolved` records so
  there is one query engine and one `blast()` rather than a second graph.
- **ROUTE** `flash/route.py` + `flash/decide.py` pick the cheapest tier that can
  solve this task. The learned version (`flash/learn.py` fit on
  `flash/ledger.py`'s outcomes) is gated off until its held-out AUC earns it —
  today's router is heuristic plus the power governor, and that is stated in the
  trace rather than hidden.
- **ACT** `flash/loop.py::solve` generates with the fast tier. `flash/grammar.py`
  can constrain decoding to the output contract; `flash/tourney.py` samples k
  candidates and lets the oracle rank them; `flash/patches.py` handles the
  symbol-addressed edit form, and its `land()` is what writes a verified patch set to
  disk — only differing bytes, never a delete, never the file named `--test`, and
  only when the oracle passed (`--apply`).
- **VERIFY** `flash/harness.py` runs the task's assertions one at a time in a
  sandboxed subprocess (`flash/sandbox.py`) and reports a failure as `GOT`/`WANT`
  rather than as a stack line. A static pass (`pyflakes` via
  `python-lsp-server`) short-circuits before generation is even diagnosed.
- **ESCALATE** `flash/power.py` decides what the machine may load right now — AC
  or battery, thermal pressure, free memory, load per core — and `flash/loop.py`
  hot-swaps to the brain only when both the policy and the governor agree.
- **RECORD** `flash/trace.py` writes a replayable session; `flash/checkpoint.py`
  keeps the in-flight generation so a `kill -9` resumes inside a task rather than
  restarting it; `flash/ledger.py` appends the outcome, which is the training data
  for every future claim about getting better.

Two surfaces run that loop, and they are the same loop. `flash run` answers one task
per process, which is the right shape for a benchmark. `flash session` keeps one
`(repo, oracle)` pair and reads prompts from stdin, re-reading the workspace from
disk at the start of every turn so turn N+1 edits what turn N landed; it is always
the patch arm, it protects the oracle for the whole session, and it exits with the
last turn's code (`SPEC.md` R-7.15). Neither one holds the weights between turns:
`solve_routed` frees the small tier before the brain loads, five times over, and
that is a memory invariant rather than an oversight.

## The rule that shaped most of this: record ≠ conversation

`Attempt.err` is what the loop stored. It is **not** the message the model
received. Those two were the same object only by coincidence, and when R-1.1 put
the resolved source into `err` the tool looked alive for fourteen green checks and
four live runs while the model saw nothing but the bare error.

Every requirement that talks about what the model sees is therefore certified one
of two ways:

1. **offline**, by driving `loop.solve` with a stubbed generator and reading the
   prompt the stub was handed (`benchmarks/hint_ab_check.py`,
   `benchmarks/graph_perceive_check.py`); or
2. **live**, by counting stored prompts under `--trace-full` that carry the
   injection's own header (`benchmarks/hint_live_audit.py`).

A check that inspects the record to prove something about the prompt is the bug
pattern, not the test.

## The rule about numbers: a count is not proof

Three habits, all of them learned the expensive way here:

- **Mutation-check the gate.** Break the thing on purpose and show which check
  notices. `benchmarks/hint_ab_check.py` ships eight tampered copies; a gate that
  no mutant defeats is documentation.
- **Size the instrument so the bug can cross the line.** A 1200-character budget
  cannot be defeated by a 900-character symbol.
- **State the precision.** On a 10-task suite one task is 10 points, so a
  one-task delta is a nil result, and R-1.1b's is reported that way.

## Invariants, and where each is pinned

| | Invariant | Pinned by |
| --- | --- | --- |
| I-1 | Every mutation of code, weights, skills or memory is a commit | git history; `flash ambient` refuses to merge or push |
| I-2 | No self-modification ships without beating the frozen harness | `SPEC.md` R-6.4's booked negative: trained arm 16/20 vs base 18/20 |
| I-3 | The agent sheds load before the user notices | `flash power --selftest` (22), `tier="shed"` in every trace |
| I-4 | Every feature degrades gracefully with no network | the whole offline battery runs radio-off; `python -m flash.graph --selftest` |
| I-5 | Decisions, tool calls and file sets are schema-valid by construction | `flash.grammar --selftest` (47), the `# file:`/`# edit:` contract validators — **PARTIAL**, see R-4.2 |
| I-6 | Any behavior is replayable and explainable | `flash trace <sid> --task <id>`; `--trace-full` stores exact prompts |

## Where the cost goes

`flash run-suite` prints a cost report per run: solved, small-tier count,
escalations, total seconds, and — for the blocks above — how many retry prompts
carried an injection. A suite's per-task seconds are the number to watch when you
change anything about generation: today's hint-eligible band costs 45–58 s per task
at three attempts on the 7B, and the difference between an arm that injects and
one that doesn't is inside that noise.

## Reading order for a new contributor

`flash/loop.py` (the policy), then `flash/harness.py` (what "verified" means),
then `flash/lsp.py` + `flash/graph.py` (what the model is told about the repo),
then one vector end to end — `benchmarks/hint_ab_check.py` is the most complete
example of house style: fourteen checks, eight mutants, and a docstring that says
which layer each check is certified at.
