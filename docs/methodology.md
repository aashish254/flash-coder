# Methodology

This page exists because the numbers in this repository are the product. If you
cannot tell which ones were measured, how, and what would have made them fail,
they are marketing — and a coding agent's marketing is worse than useless,
because the whole claim is that a machine checked the work.

Four rules, each of which has caught a bug in this project's own instruments.

## 1. A claim is a printed line, not an exit code

Every vector prints its own fraction:

```
lora path: 33/33 checks passed
mutations: 15/15 gates defeated by exactly their checks
```

`python benchmarks/battery_reread.py` runs all 33 of them and adds the columns,
and it reads the *printed* number rather than the return code. That is not
pedantry — the two capture traps that made this a rule are recorded in `SPEC.md`
§6: a `grep "checks passed"` silently dropped two vectors that print a bare
fraction, and a last-line grep read one vector's `14/14 mutants` summary as if
it were its check count, under-counting the published total by 17 while looking
clean.

So the totals in the README (`1,119 checks + 20 oracle verifications + 85
mutation gates`) come from the line `battery_reread` prints, and the file holds
one entry per §6 item with the exact fraction that item must print. When the
numbers disagree, the run reports the disagreement out loud. It has: one CLAIM
was set by arithmetic before the run and the tree printed a different mutant
count, which is the only reason anyone noticed the BATTERY entry had carried `3`
for a vector that printed `4/4`. And once the disagreement was with the page
writing it: a re-read printed `1099 … mutants 75` against a claim of 1107/80
because the new documented-command gate was failing on **SPEC's own paragraph
about two commands that do not exist** — a backticked `flash …` span means "run
this" to a reader and to the scanner alike, so a document that quotes a dead
command cites it. The prose was rewritten and the gate was left alone, and
`SPEC.md` R-7.8 now says why the quotation is written as bare words.

## 2. Test at the seam the clause names

A clause that says "`flash doctor` reports which half of the machine you are
holding" is not satisfied by testing the function that computes it. Two of this
repo's vectors exist because that distinction was learned the hard way:

- `benchmarks/portable_paths_check.py` drives the *execution seams* (what a
  child process actually imports and runs), because a check that certified a
  helper while the clause named a consumer let a dead seam survive 14 green
  checks.
- `benchmarks/lora_path_check.py`'s new `--dry-run` checks call
  `flash.train.main()` with `TASK_DIR` pointed at a temp directory and **no
  `--suite-out`**, because the defect was in the command's plumbing and the
  file it wrote was the one that lives in the tracked tree. Testing
  `suite_from_dataset()` directly would have passed while the bug stayed.

## 3. Every gate pays for a mutation

A check that nothing can break is a check that is not running. Each vector names
the inversion it defends against — delete the parser entry, invert the dedupe
guard, return `True` whatever the parser says, stop collecting CI `run:` lines —
and requires the *specific* checks that mutation defeats to fail, so a cascade
has to be stated rather than waved through. `flash selftest --all` runs the
whole set including those mutants.

The mutants catch documentation drift too. `SPEC.md` once named a throwaway file
that only exists while a mutation is applied; the documented-command vector's
existence gate failed the run over it, and the sentence moved rather than the
gate.

And a mutant can reveal that the *gate* is the broken part. R-7.10c's sweep plants
a module that reads a `benchmarks/` file with no refusal, and its first version
printed **0 checks failing** — because the planted module had no
`if __name__ == "__main__"` tail, so `python -m flash._zz_data_probe --selftest`
imported it, ignored the flag and exited 0, which the sweep had been recording as
a pass. The probe gained the tail and the sweep gained a spine gate that requires
every swept module to have been dispatched, because "it did not crash" and "it ran"
are different claims.

## 4. Label measured, projected and gated — and publish the misses

`SPEC.md` carries the status of every requirement, including the ones that
measured *no*: speculative decoding (R-8.1) is open with its faulting
throughput numbers attached, R-6.4's I-2 arm is booked as a negative result, and
the voice control spike (R-7.3) states its measured command-to-ack latency
instead of a target dressed as an achievement. `SPEC.md` §9 is the register of
arms this environment cannot close — 16 GB co-residency (this box is 32 GB),
watts/task (`powermetrics` needs sudo), the 24-hour chaos window, the
10-developer feel test, and R-6.4's weights arm, whose blocker is the flywheel's
own data law rather than a shortage of compute — and a requirement is not moved
to CLOSED by re-wording its gate.

The same rule applies to install claims. The four shapes a download can produce
(clone, wheel, unpacked sdist, editable clone) were each run: `flash doctor` on
a wheel says the verification surface is absent rather than failing checks for
a missing directory, `flash selftest --all` refuses with rc 2 and names the path
it wanted, and the two real trees ran the entire §6 battery to a printed match.
What is *not* claimed, in `README.md` and `pyproject.toml` both, is a working
Linux or Windows install — that is R-7.5's open half, and saying so is cheaper
than a stranger finding out.

## Running it yourself

```bash
python -m pyflakes flash/*.py benchmarks/*.py   # must print nothing
python benchmarks/battery_reread.py             # the whole §6 battery
python benchmarks/battery_reread.py --backend-free   # the same, with the MLX import blocked
flash selftest --all                            # the same thing from an installed package
```

`--backend-free` is the one to read if you want to know how far this goes: it
installs a `sitecustomize.py` import blocker, requires a child process to prove
the block fires before it will print any total, requires the blocker to leave
`numpy` alone (a shim that broke everything would "prove" the claim by making
the battery unrunnable), and then sweeps every submodule of the package under
it — 26 of 26 — because "the two that used to fail import fine" is an
enumeration, not a claim about the package.
