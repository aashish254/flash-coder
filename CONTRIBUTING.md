# Contributing to Flash Coder

Short version: this project's product is **trustworthy numbers**, and the code is
how it produces them. A contribution that makes a claim less checkable is a
regression even if it makes the tool faster.

## The five rules that make this repo different

1. **A claim names the run that printed it.** Every number in a README, SPEC,
   TODO or doc comment should be reproducible with a command that's in the tree.
   "Latency improved" is not a sentence that survives review here; "
   `flash bench`, 47.2 s/task over 10 tasks, in
   `benchmarks/results/<log>`" is.
2. **A passing count is not proof.** A test that passes because it can't fail
   looks exactly like a test that passes because the code is right. So each
   requirement gets a **mutation check**: break the thing on purpose, show the
   gate fails, and say which check caught it. `benchmarks/*_check.py --print`
   prints both halves.
3. **Test at the seam the clause names.** A requirement about what the *model
   sees* is certified by reading the prompt the model saw, not by reading the
   record the loop wrote next to it. Those were different places here once, and
   fourteen green checks never noticed.
4. **Size the instrument so the bug can actually cross the line.** A budget of
   1200 characters can't be defeated by a 900-character symbol. If a check
   cannot fail, it is documentation, not a gate.
5. **Docs move together.** A shipped requirement updates `SPEC.md` (the clause
   and its vector), `TODO.md` (the box, with the result paragraph), `README.md`,
   the module map in `flash/__init__.py`, and `benchmarks/battery_reread.py` if
   it added a check. One commit, not a cleanup later.

Related standing rules: no `# TODO` placeholders and no mocked standard
responses — if a capability isn't there, the code says so out loud (see
`flash/sandbox.py`'s `UNAVAILABLE` memory rlimit for the house style). Never
reword a missed gate to make it pass; book it as a negative result and say what
would close it.

## Getting set up

```bash
git clone https://github.com/aashish254/flash-coder && cd flash-coder
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .            # `flash` on PATH; plain `python -m flash.cli` also works
flash power                 # what your machine can and cannot run, before the downloads
python benchmarks/battery_reread.py   # the offline battery: no models, no network
```

Those last two are what the tree runs today. `flash doctor` — the same pre-flight
in one purpose-built command — and `flash selftest --all` are SPEC **R-7.6**, an
open box, and CI already calls both, so the published pipeline is dangling until
they land. `git clone` above is likewise where this repo is *going*, not where it
is: no remote is configured yet, and `pip install .` from a clean clone into a
throwaway venv has never been run (SPEC **R-7.5**).

`flash power` is deliberately the step before the model download: the fast tier
needs about 4 GB of RAM-resident weights and Apple Silicon, and a machine that
can't hold it should hear that from a 20 ms check rather than from a crash
8 minutes into a download.

On Linux or Windows everything except generation installs and runs: PERCEIVE,
the AST graph, the harness, trace/replay and the whole offline battery are pure
Python. The model layer is `mlx`, and `flash run` says so rather than pretending.
Measured with `mlx` blocked on 2026-09-27: **24 of the 26 modules import**, the
two that cannot being `flash.decide` (top-level `import mlx.core`) and
`flash.route` through it — `flash.cli` is not one of the failures, because it
imports both lazily.

## Making a change

- Branch off `main`, one requirement or one bug per branch.
- Add or extend a vector in `benchmarks/` for anything behavioral, and include
  its mutation check. `benchmarks/hint_ab_check.py`,
  `benchmarks/graph_perceive_check.py` and `benchmarks/portable_paths_check.py`
  are the three most complete examples; the last of the three is the one to read
  if you are deciding *where* to put a check — it drives the execution seams
  rather than the helper they call, because a check that certifies a helper while
  the clause names a consumer is how a dead seam survived 14 green checks.
- Run the checks the CI will run, locally, and paste the printed lines into the
  PR:

  ```bash
  python -m pyflakes flash/*.py benchmarks/*.py   # must print nothing
  python benchmarks/battery_reread.py             # `flash selftest --all` is R-7.6
  ```

- If you changed a number that appears in prose, re-derive it from the tree — a
  battery total comes from the counts the runs print, never from an exit code.

## A PR template exists and it is not bureaucracy

It asks for the command, the printed output, and which check fails when the
change is reverted. Three lines of that is a good PR. If a requirement changed
status, `SPEC.md` and `TODO.md` move in the same commit.

## What I will happily merge, and what I won't

- **Merge:** a bug fix with a failing-first test, a new perception source with a
  deterministic check, a benchmark that raises n on an existing claim, a doc fix
  that removes an ambiguity, a platform profile (Linux sandbox, CUDA backend)
  with its own measured caveats.
- **Won't:** "support every model" via a config sprawl, a dashboard, a rewrite of
  the loop into an async framework, a change that makes a documented negative
  result disappear by deleting the sentence, or a dependency added for one call.

Questions about a direction are cheap — open an issue first if you'd rather not
spend an evening on something that won't land.
