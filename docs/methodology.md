# Methodology

This page exists because the numbers in this repository are the product. If you
cannot tell which ones were measured, how, and what would have made them fail,
they are marketing — and a coding agent's marketing is worse than useless,
because the whole claim is that a machine checked the work.

Five rules, each of which has caught a bug in this project's own instruments.

## 1. A claim is a printed line, not an exit code

Every vector prints its own fraction:

```
lora path: 33/33 checks passed
mutations: 15/15 gates defeated by exactly their checks
```

`python benchmarks/battery_reread.py` runs all 37 of them and adds the columns,
and it reads the *printed* number rather than the return code. That is not
pedantry — the two capture traps that made this a rule are recorded in `SPEC.md`
§6: a `grep "checks passed"` silently dropped two vectors that print a bare
fraction, and a last-line grep read one vector's `14/14 mutants` summary as if
it were its check count, under-counting the published total by 17 while looking
clean.

So the totals in the README (`1,427 checks + 20 oracle verifications + 172
mutation gates`) come from the line `battery_reread` prints, and the file holds
one entry per §6 item with the exact fraction that item must print. When the
numbers disagree, the run reports the disagreement out loud. It has: one CLAIM
was set by arithmetic before the run and the tree printed a different mutant
count, which is the only reason anyone noticed the BATTERY entry had carried `3`
for a vector that printed `4/4`; and on the R-7.15f/g pass a hand-summed CLAIM
came out one short of what its own 37 OK lines add up to, so the run exited 1
having printed every vector green, saying `SPEC §6's checks claim is 1376, the
tree prints 1377`. That disagreement was with my arithmetic, not with a vector,
and the fix was the prediction — a row is only ever moved when the row is what
changed. And once the disagreement was with the page
writing it: a re-read printed `1099 … mutants 75` against a claim of 1107/80
because the new documented-command gate was failing on **SPEC's own paragraph
about two commands that do not exist** — a backticked `flash …` span means "run
this" to a reader and to the scanner alike, so a document that quotes a dead
command cites it. The prose was rewritten and the gate was left alone, and
`SPEC.md` R-7.8 now says why the quotation is written as bare words.

**A refusal is a printed line too.** `python benchmarks/battery_reread.py
--backend-free` re-reads the same 37 vectors with `mlx`, `mlx_lm` and `mlx_vlm`
unimportable in every child process, because that is the install every Linux and
Windows user actually gets and the only shape a Mac cannot see by definition. Two
rows genuinely need the backend — `flash.grammar`'s mask checks load the tokenizer
through `mlx_lm.tokenizer_utils`, and `session_check`'s chat arm walks past
`flash.loop.solve_routed`'s preamble import — so each prints a `REFUSED` line
carrying the sentence the row died with, and the totals below it are that lane's own
subtotal: `CLAIM` minus those rows' counts, behind a `NOT a §6 re-read` banner. A
third arm covers `checkpoint_resume_check.py`, whose precondition is that the §34.1
governor will offer tournament width ≥ 2 on a machine that is cool, plugged in and
not busy — a fact about the box, available to refuse in every lane. The whole design
holds to the rule above: nothing is *skipped*. A death that names no cause is a BAD
line, a row that prints its fraction is counted whether or not a list names it, and
`backend_free_check.py` gates the decoder against deaths it must not read and against
a plain-lane child that must still fail with the block injected but the flag withheld.

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
  file it wrote was the one that lives in the tracked tree.
- R-7.15e's clause was about a *sentence*: a patch aimed at the test file had to
  be refused as the oracle, not as a bad address. The refusal was already
  happening, which is why the bug survived green vectors — `L11-L11 is past the
  end of a 9-line file` is a true statement about a file the run must not edit,
  and it was the only sentence the arm could produce because the address was
  resolved before the file was recognized. So the checks assert the ordering
  (`patch_landing_check.py` asks for the message text, and one mutant replaces
  it with the coordinate complaint so the two cannot be confused), and the
  second mutant returns `""` from `cli._oracle_key`, which is the one return
  value that disarms the patch layer *and* the write-back gate at once. Testing
  `suite_from_dataset()` directly would have passed while the bug stayed.
- R-7.15g's clause named *which copy* the verdict is computed from, and every count in the
  report agreed that the edit had landed: eight attempts applied (`applied=1`, `refused=0`),
  the verdict line printed `solved=False`, and the trace recorded the right symbol each
  time. The bug was that the candidate's oracle carried a literal
  `sys.path.insert(0, "<the live tree>")`, so the module imported at score time was the one
  from before the patch. A check on `score()` alone could not see this — the temp root it
  builds is where the patched bytes are, and nothing about that call is wrong. So the vector
  drives `cmd_session` over a seeded repo whose `t.py` bootstraps with its own directory, and
  asserts four things at that seam: the patched copy wins, the pre-edit body still fails with
  a `GOT:` (so the fix moved precedence rather than softening the verdict), the refusal the
  model reads quotes the oracle's assert verbatim (so a fix that rewrote the exam cannot ride
  along while turns start passing), and the trailing-comment case below.
- R-7.15h's clause was about a *mode switch at the seam the user types*: `--test` became
  optional, and the defect it fixes ("it can't even reply hi") lived in the CLI→loop wiring,
  not in a helper. A check that only asserted `solve_chat` returns a string would pass while
  the command still printed the telemetry line or still demanded an oracle. So the vector
  drives the real `solve_routed(chat=True)` with only `load_model`/`_generate` stubbed, and
  puts three of its six mutants *below the command* — an unguarded `cli._oracle_key`, a
  `_land_edits` that lands a scored verdict on a chat turn, and a `solve_chat` that refuses
  prose — each of which the shipped command must defeat, because the mode is defined by what
  the printed turn is and not by what a function returns.

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
R-7.5's second clause — the battery green "against the installed package rather
than the checkout" — then had to say which of its two readings it meant before
anyone could claim to have measured it: read literally, with every child importing
out of `site-packages` and no source tree present, it is **unsatisfiable by
design**, because eleven of the battery's vectors index the tree they stand in and the
rest refuse (R-7.10) rather than pass quietly. Read as the property the README
sells, it has been run — and on 2026-09-28 it was run again, on **both install
shapes the one tarball supports**, by `python benchmarks/r75_sdist_battery_check.py`:
the driver builds the sdist, makes a throwaway venv, prints which `flash` a child
imports from inside the unpacked tree and from outside it, and runs the whole
battery there twice.
`pip install <sdist>` returns **33 of 35** lines, rc 1, `checks 1119  oracle 20
§6 total 1139  mutants 85` in 13 min 22 s, with exactly two named grammar refusals;
`pip install '<sdist>[ts]'` returns **35/35**, rc 0, `checks 1210  oracle 20
§6 total 1230  mutants 111` in 13 min 14 s and the battery's own
`matches SPEC §6 as written`. Those are the **35-line tree's** prints: R-3.2's clause 3
added a 36th battery line on 2026-09-29, so the driver is being re-run against the
current tree rather than having its old totals carried forward by arithmetic. Two of
the battery's lines are the second language's vectors,
and `flash.lang_ts.available()` is a designed refusal — a `0/1` print, not a crash —
so a plain install *cannot* reach the checkout's total, and the driver now fails if either TS line
passes on shape A, or if shape A agrees with §6 at all. That is the difference between
a number and a claim: the old single-shape run printed 1,119 and 33/33 while the
checkout printed 1,210 and 35/35, and nothing on the page said which was the download's
fault. The
earlier as the fresh-clone §6 print is the checkout's own re-read with one trailing
line added, and the driver it was attributed to is only capable of three lines. The
counts it carried were true; the label was not, and `SPEC.md` R-7.5 says so in both
places.
One machine-state limit travels with the full battery and is worth knowing before
you start a 13-minute run: `checkpoint_resume_check` needs the tournament arm at
width ≥ 2, and `flash/power.py` forces width 1 below 25% charge **even on AC**, so a
laptop at 22% fails that one line on every tree — the first shape-B pass of 2026-09-28
printed 34/35 for that reason alone and is kept as
`benchmarks/results/r75_sdist_battery_shapes_battgate_20260928.log`.
Then the same disease turned up in the driver that measured the *other* clause.
`python benchmarks/r75_fresh_install_check.py` had been reporting that `flash doctor`
exits 0 "against the installed copy"; it had been building the sdist and installing it
faithfully, and then asking `python -m flash.cli doctor` **with no working directory**,
so the child inherited this checkout and `python -m` put that checkout on `sys.path[0]`
ahead of the venv's `site-packages`. Every one of its answers came from the source tree
it had just installed from. Rewritten to ask the venv's own console script from a
directory holding no Python, and to print which `flash` resolved before asserting
anything about the answer, the same run reports **9/9** shapes and a truer picture: an
editable clone exits 0 and runs 3 of the battery's 36 vectors from the clone, while a tarball install
answers `flash --version` at rc 0, `flash doctor` at rc **1** naming the absent
verification surface and its remedy, and `flash selftest --all` at rc **2** naming the
path it wanted. The instrument's own shadowing is now one of its gates, because the fix
that only lives in a sentence gets undone by the next person who forgets the `cwd`.
What is still *not* claimed, in `README.md` and `pyproject.toml` both, is a
working Linux or Windows install, and saying so is cheaper than a stranger finding
out.

## 5. A published number is written by a program, or it is not published

Everything above guards against a claim that is true but unverified. This one
guards against the easier failure: a claim that is *typed*.

`site/` — the landing page — reads exactly three files, the JSON under
`site/src/data/`, and those are written by a two-command pipeline:

```bash
python benchmarks/dashboard_data.py     # measures -> benchmarks/results/dashboard_data.json
python benchmarks/export_site_data.py   # converts -> site/src/data/*.json
```

The collector has three sources and every one of them is local: a committed §6
witness, which it *parses* for the 33 printed fractions rather than being told
what the totals were; `python -m flash.<mod> --selftest` run n=3 times for 15
modules, published as a median with the min and max beside it, because one timing
on a laptop is not a property of anything; and the latency line the graph selftest
prints about its own instrument. The exporter converts and adds nothing — it
derives the command string for each figure from the battery's own label, so every
number on the page carries the command that prints it, and it runs `flash doctor`
and the graph selftest as live children to paste their literal output, with this
machine's paths replaced and each capture listing the substitutions it carries.

This rule is not theoretical. Commit `54a2117` put four dashboard PNGs on this
repo carrying invented competitor latencies and a hand-typed monthly-cost table;
the revert is `69a4d2d`, and what replaced them is structure: there is
no code path from a typed figure to the screen, so a chart cannot draw what a run
did not print. The page therefore has five benchmark panels and an amber box. The
fifth is the one cross-tool comparison this machine has produced — `aider` 0.86.2,
a no-agent-loop single shot and `flash run-suite` on the same 4-bit weights, graded
by the same harness oracle — printed by `python benchmarks/market_compare.py` into
`benchmarks/results/market_compare_20260928.log` and read out of that file's own
table by `dashboard_data.py`, which refuses to write the JSON when the print has no
parsable rows. The amber box names what is **still** refused: no "vs. Cursor /
Copilot" bar and no dollar-per-month table. That is not a matter of not having got
round to it. This machine has no headless driver for either product, and both
generate in their own cloud, so a row for them would replace the single controlled
variable of the table above — same weights, one machine, one oracle — with two
unknowns and a vendor's own token accounting. That axis would be invented, not
measured.

Two findings from inside the pipeline, both of which it produced:

- **The exporter read the wrong capture group.** Its regex for the cold-index line
  pulled group 4 for the build seconds, and group 4 is the *edge* count — so the
  panel would have advertised a **10,058 ms** index build against the **603 ms**
  the line actually reports. Nothing checked that: what surfaced it was
  cross-matching the 33 commands the exporter generated against the battery's own
  table, which is a different check for a different claim and happened to be
  looking. The fix was a re-measure, not a retype.
- **`--headless --screenshot` is not an instrument for this page.** The first
  shipped defect was a blank one: three.js throws when no WebGL context can be
  made, an uncaught error in a child unmounts the whole React tree, and the dump
  was 33 KB with an empty `#root`. A screenshot captures one viewport inside a
  virtual-time budget, so every scroll-triggered section paints blank — which is
  pixel-indistinguishable from the bug, meaning the instrument being used to look
  for it could not have seen it. Verification now drives a real scroll over the
  Chrome DevTools Protocol at 1440×900 and 390×844, on the rendered tree rather
  than a picture of it (173 KB of DOM, all eight sections), and runs the same
  scroll on the fallback path: the hero probes for a WebGL context, a *software*
  renderer and `prefers-reduced-motion`, and renders an interactive SVG of the
  same 150 symbols without downloading the 914 KB three.js chunk.

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
enumeration, not a claim about the package. What it prints for two of the 37
rows is `REFUSED`, naming the sentence the row died with, and its totals are
`CLAIM` minus those rows' own counts under a banner saying this lane is not a §6
re-read — see §1 above for why a refusal gets the same treatment as a fraction.
