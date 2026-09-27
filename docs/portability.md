# Portability: what has to work at a path nobody predicted

`git clone` and `flash run-suite` have to work on a machine whose checkout lives
somewhere the author never saw. That is a property with a gate on it, not an
intention: `benchmarks/portable_paths_check.py`.

## The contract: one token, expanded in one place

A context task's test has to put the fixture package on `sys.path` before the
candidate's code runs, because the candidate imports those fixtures at top
level. Those bootstrap lines are oracle text stored in a task corpus, so they
carry a directory — and a directory written into a corpus is a claim about the
reader's filesystem.

The corpus writes `<REPO>` instead:

```
import sys; sys.path.insert(0, "<REPO>/benchmarks/fixtures")
```

`harness.REPO` is `Path(__file__).resolve().parent.parent` — resolved from
`flash/harness.py` outward, never recorded — and `_hoist_path_bootstrap` expands
the token. It is deliberately the **only** expansion site, because it is already
the one function every execution seam passes through to split a test into
(bootstrap, body):

| seam | caller |
| --- | --- |
| `harness.run_test` | the loop's VERIFY |
| `harness.score`'s probe loop | every suite verdict |
| `debug._run` | R-4.3's trace feedback |
| `confidence._seeded_run` | R-2.3's re-runs |

Expanding it per-caller is how a suite ends up importing fixtures on one seam
and failing to on the next — a `ModuleNotFoundError` raised before the
candidate's first line is not a wrong answer, it is a verdict scored on nothing.
`python -I` (which the harness runs under) implies `-E`, so `PYTHONPATH` cannot
carry the fixtures instead; the bootstrap has to be in the test.

**Ordering is part of the contract.** `debug._run` writes the bootstrap to its
own file and `exec`s it *before* installing the tracer, mirroring `run_test`.
Without that, the fixture import fails inside the traced region, the tracer
records a crash instead of the candidate's execution, and every context task's
debug feedback is dead — which is the bug this scrub surfaced, in code that had
already been certified 32/32 on tasks that do not import the repo.

## What was scrubbed

72 oracle bootstrap paths across the three context corpora
(`benchmarks/tasks/m2_tasks.jsonl`, `hint_ab_tasks.jsonl`,
`hint_ab_candidates.jsonl`) now read `<REPO>`, and
`benchmarks/tasks/m2_perceive_test.py` locates the repo from its own `__file__`.
The vector checks that all four seams pass every reference solution **launched
from a foreign working directory**, that the same four seams reject the 10 frozen
wrong answers in that corpus, and that the token and the path it replaced decide
the same thing on this machine.

## What stays verbatim, and why

`benchmarks/results/**` is excluded from the host-path scan. The exclusion is a
policy about records, and the vector checks it rather than trusting it: nothing
under that prefix may be code or instructions (no `.py`, `.sh`, `.md`,
`.pyproject`-class file at all), and the residual paths must fall in one of five
named groups:

- `traces/` — the prompt text the model was actually shown, with the tool's
  working directory in it. Redacting it turns evidence into a claim.
- `results/` (top level) — `ledger.jsonl` for the same reason, plus two
  embedding caches. Their zip entries are keyed by the prompt text, so rewriting
  a key does not crash anything: it silently changes what the fit consumed, and
  the pool-audit numbers SPEC cites stop being reproducible from the cache in the
  tree.
- `adapters/` — `adapter_config.json` is read by `flash/train.py`, and its paths
  describe the run that produced those weights.
- `p6/`, `jobs/` — one-off run records cited by dated clauses.

RECORD_RESIDUE = 395

That is the count the vector printed on the tree at the time of writing
(`traces` 233, top-level records 146, `adapters` 9, `p6` 6, `jobs` 1) across 31
of the 160 record files. Four of the 395 are this vector's own witness log, which
names the two markers in the labels it prints — a count that includes the counter
is the kind of detail worth stating rather than smoothing over. The §6 battery's
re-read witness added a 160th record file carrying **zero** occurrences, which is
why the floor held at exactly 395 rather than drifting. New traces only
ever add, so the gate treats the number as a floor: a documented count above the
live one means the doc was rewritten to fit a shrinking tree, and a new record
directory fails until it is named above.

## What is not claimed

That the repo is path-free. It is not, and it will not be: the point is that
nothing a stranger **executes** or reads as instructions depends on the author's
disk, and that the residue is a counted, grouped, published number rather than
an unbounded one.
