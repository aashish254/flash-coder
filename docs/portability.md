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

RECORD_RESIDUE = 411

That is what the widened scan counts on the committed tree at the time of writing
(`traces` 241, top-level records 154, `adapters` 9, `p6` 6, `jobs` 1) across 36
of the 174 record files. Every step between 395 and 411 is named below, because a
residue figure that moves without an explanation is the same failure as one that
was never published:

- **395 over 31 files** was the two-marker count. The §6 battery's re-read witness
  then added a 160th record file carrying **zero** occurrences, which is why the
  floor held at exactly 395 rather than drifting.
- Adding the third marker (the default Homebrew prefix on Apple Silicon) took it
  to **403 over 33 files**, and the +8 is eight occurrences in two run traces —
  the `err` text of two failed `verify` events, which quoted an interpreter path
  under that prefix and had matched neither old marker. Both files were new to the
  count, which is why the file total moved in the same step.
- Storing this vector's own concurrent-run reproduction
  (`portable_concurrent_repro_20260927.log`) added **4** (its gate labels name the
  two markers that run was made under) and one file: **407 over 34**.
- Storing the table of the run that verified the lock
  (`portable_paths_r74b_20260927.log`, with its behaviour log
  `portable_lock_witness_20260927.log` alongside, which carries none) added **3**
  more and one file: **410 over 35**. That one is new behaviour rather than new
  data — the widened gate label prints all three markers, so a witness of this
  vector now carries three occurrences where pass 1's carried two.
- Storing the R-7.6/7.7/7.8 §6 witness (`battery_reread_r76_20260927.log`) added a
  165th record file carrying **zero** occurrences — a battery print is a list of
  check counts and relative paths, and it stays that way only because the vectors
  print relative paths — so the floor held at exactly 410 while the file count moved.
- Storing the R-7.5 four-install-shape witness (`r75_install_shapes_20260927.log`)
  added a 166th record file carrying **one**: the `flash doctor` page that witness
  captured prints the model cache as a resolved path, which is the whole point of
  that line — a report that elided it could not show a stranger where their weights
  actually are. The occurrence is therefore in the record, not in the code, and the
  floor moved to **411 over 36**.
- Storing the R-7.9/R-7.10/R-7.10b §6 witness
  (`battery_reread_r710_20260927.log`) added a 167th record file carrying **zero**,
  for the same reason as the R-7.6 one: every line of a battery print is a vector
  name, a relative path and a count, so the floor held at **411 over 36** while the
  file count moved.
- Storing the R-7.10c §6 witness (`battery_reread_r710c_20260927.log`) added a
  168th record file carrying **zero** again — the sweep's own print is a list of
  temp-directory *relative* paths and counts, because `pkg_only_copy()` hands the
  child a `PYTHONPATH` it built with `Path` arithmetic and the vector never echoes
  it. So the floor stays **411 over 36** and only the file count moved.
- Storing `battery_reread_r75_20260928.log` added a 169th record file carrying
  **zero** — the same shape as the other battery prints: vector names, relative
  paths, counts. It was committed as "R-7.5 fresh clone witness", and it is not one:
  it is the checkout's R-7.10c re-read with a trailing `battery rc=0` added, and the
  driver it was attributed to runs three lines, not thirty-three. Nothing about its
  *contents* is a leak — a print with no host path is still a print with no host
  path — but the file is now read as what it is, a second checkout re-read, and the
  real download run lives beside it under its own name.
- The landing page's measured data file (`dashboard_data.json`) added a 170th
  carrying **zero**, because `benchmarks/dashboard_data.py` records each vector as
  the command it ran and the line it printed, never as an absolute checkout path.
  Both steps moved the file count and neither moved the floor.
- Re-running the §6 battery after the site landed (`battery_reread_r711_20260928.log`)
  added a 171st record file carrying **zero** again, and the run is the reason the
  floor is quoted rather than assumed: the same **411 over 36**, the same
  `checks 1119  oracle 20  §6 total 1139  mutants 85`, because a battery print is a
  list of vector names, relative paths and counts.
- R-7.5's real download witness (`r75_sdist_battery_20260928.log`) added a 172nd
  carrying **zero**, which is the one place in this project where the absence is
  enforced rather than observed: `benchmarks/r75_sdist_battery_check.py` refuses to
  write the file if `str(ROOT)` or the home directory appears in the text it is
  about to save, and prints the count it checked as a provenance line. The paths it
  does carry are the temp directory the download was unpacked into and a
  `site-packages` path under it — neither is a marker, and a witness that elided
  them could not show which package a child actually imported. So the floor is still
  **411 over 36** and only the file count moved.
- Re-reading §6 from this checkout after the correction landed
  (`battery_reread_r75b_20260928.log`) added a 173rd record file carrying **zero** for
  the ordinary reason — a battery print is vector names, relative paths and counts —
  and it is the same print the download made one row above. Its wall clock is quoted
  from the redirect file's timestamps, not from anything inside it, which is exactly
  the gap the mislabelled 169th file exposed; the SPEC entry that cites it says so
  rather than letting the file speak for a run it does not name.
- R-7.5's clause-1 re-measurement (`r75_clause1_20260928.log`) added a 174th carrying
  **zero**, and that absence is the point of the file: the driver it comes from used to
  print the checkout's path into its own report while claiming to interrogate an install.
  It now labels each resolved module with a phrase — `site-packages`, `the unpacked
  sdist`, `the clone` — and refuses the witness outright if `str(ROOT)` survives in the
  text, so a regression in provenance shows up as a refused file rather than as a clean
  log with a host path in it. Floor unchanged at **411 over 36**.

Eight of the 411 are this vector's pass-1 and reproduction logs and three are its
pass-2 table — a count that includes the counter is the kind of detail worth
stating rather than smoothing over. New traces only ever add, so the gate treats
the number as a floor: a documented count above the live one means the doc was
rewritten to fit a shrinking tree, and a new record directory fails until it is
named above.

## What is not claimed

That the repo is path-free. It is not, and it will not be: the point is that
nothing a stranger **executes** or reads as instructions depends on the author's
disk, and that the residue is a counted, grouped, published number rather than
an unbounded one.
