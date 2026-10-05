# Flash Coder v0.1.0

**Date**: 2026-10-05
**Package version**: `0.1.0` — `flash --version` prints `flash 0.1.0`, and that string
comes from `flash/__init__.py`, which is the only place `pyproject.toml` reads it from.
**Tag**: `v0.1.0` is **not created**. Cutting the tag, opening the GitHub release and
uploading anything to PyPI are the maintainer's explicit calls and have not been made.
Nothing on this page claims a published artifact exists.
**Commit**: the commit that lands this file. A commit cannot quote its own SHA; it is the
first line of `git log -1` and it is reported beside the green CI run.

Every number below was printed by a command, and each is labelled with the tree and the
platform that printed it. Nothing is carried over from a vendor page or a previous
release's notes.

---

## What Flash Coder is

A local, verify-first coding agent for Apple Silicon. It proposes an edit, runs the
project's real tests, reads the actual failure values, and retries — on your machine,
with no API key, no container, and nothing leaving the Mac. Its verification surface is
offline and deterministic: 37 vectors that run with **no model weights** (one of them
reads the fast tier's tokenizer files — 11M, no weights — which is why CI caches them
before asking for the battery), re-read as a battery against printed totals.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

flash doctor              # what this install can and cannot do
flash selftest --all      # the 37-vector offline battery, no model needed
```

`flash run <task>`, `flash session --context . --test t.py --apply` and `flash power`
(governor readout) are the working commands; `flash doctor` tells you which half of the
project your install actually has.

---

## Measured on Linux (the shape a non-macOS install gets)

A Linux box installs the package but cannot install MLX, so it gets the `--backend-free`
lane: the same 37 vectors with `mlx`, `mlx_lm` and `mlx_vlm` made unimportable in every
child process. Both ubuntu jobs of run `37227171133` printed, identically on Python 3.11
and 3.12:

| Vector | Printed on the runner |
|---|---|
| `benchmarks/backend_free_check.py` | `49/49 (+ 15 mutants)` |
| `flash power --selftest` | `24/24` |
| `flash.sandbox --selftest` | `34/34` |
| `benchmarks/battery_reread.py --backend-free` | 35 of 37 rows `OK`, 2 `REFUSED` by name (`flash.grammar --selftest`, `benchmarks/session_check.py`) — `checks 1301  oracle 20  §6 total 1321  mutants 146` |

That run prints `NOT a §6 re-read` and says why: the two refused rows reach the
generative backend, so their 128 checks and 26 mutants are subtracted from the whole-tree
claim rather than reported as failures. A Linux install cannot run them at all, and no
number of local re-reads changes that.

`flash power` and `flash.sandbox` are the two rows this release fixed at the reading:
the memory size now comes from `sysctl hw.memsize`, then `/proc/meminfo`, then POSIX
`sysconf` (and the reading names which source answered), and the sandbox vector asks its
nine Seatbelt claims in two arms so 34 prints on a box that ships no Seatbelt. Before
that fix the same two rows printed `21/22` and nothing at all on ubuntu.

## Measured on macOS (the real macos-14 runner)

The `battery` job of the same run, 27m35s, was that job's first execution ever. Its
`flash selftest --all` step printed **36 `OK`, no `BAD`, 1 row refused by name**:

```
REFUSED benchmarks/checkpoint_resume_check.py    machine — the §34.1 governor will not offer tournament width >= 2 on this machine
     └─ AssertionError: the tournament arm needs the governor's width >= 2 and this machine offers 1 (free memory 6.0GB < 6.9GB needed): put it on AC, let it
checks 1394  oracle 20  §6 total 1414  mutants 172
```

1429 − 35 = 1394: the refused row's own 35 checks, subtracted by the rule that gates it.
The runner's power state triggered the refusal, not the code, and the run declared
itself `NOT a §6 re-read` rather than quietly totalling. The step's lines are kept in the
tree at `benchmarks/results/ci_battery_macos_37227171133_20261005.log` (runner paths
removed) because that job's artifact step found nothing to upload.

## Measured on the author's Mac (the whole §6, 37 lines)

Re-read on **this release's own tree** — launched 22:34:40, last line written 22:59:35
(24 min 55 s), in `benchmarks/results/battery_reread_v010_20261005.log`:

```
checks 1429  oracle 20  §6 total 1449  mutants 172
matches SPEC §6 as written: 1429 + 20 = 1449 green, offline (+ 172 mutants)
```

37 `OK`, no `BAD`, no `REFUSED`, rc 0 — including the row CI's runner refused,
`benchmarks/checkpoint_resume_check.py 35/35`, on a Mac that was on AC and offering
tournament width 4. The same totals were read on the commit immediately before the
version bump (`battery_reread_r716f_20261004.log`), so the bump moved no check count by
design: nothing in §6 reads the version string except
`benchmarks/backend_free_check.py`, which compares `flash --version`'s print against the
module and so follows it.

The `--backend-free` lane was measured on the pre-bump tree, paired with that tree's
pre-bump plain re-read rather than with the 0.1.0 pass above; 23 min
(`battery_backendfree_lane_r716f_20261004.log`): `checks 1301  oracle 20  §6 total
1321  mutants 146`, 35 `OK` plus the same two rows refused by name — the identical print
both ubuntu jobs of `37227171133` produced on a real Linux interpreter. Budget that, not
a couple of minutes: **the offline battery costs ~25 min plain and ~23 min in the lane**
on this machine, and CI's three jobs cost 31m49s / 31m57s / 27m35s.

One ordering detail, stated because it is the only thing that would otherwise let the
paragraph above read better than it is: the two §6 rows that look at prose —
`benchmarks/portable_paths_check.py` and `benchmarks/documented_commands_check.py` — ran
inside that 24 min pass, and these notes gained a few sentences after it. Both were
re-run on the final text and printed **15/15 (+ 7 mutants)** and **8/8 (+ 5 mutants)**,
and `python -m pyflakes flash/*.py benchmarks/*.py` printed nothing.

Named vectors, from the same prints:

- `flash.patches --selftest` 94/94, `flash.debug --selftest` 55/55, `flash.graph
  --selftest` 44/44 (+ 12 mutants), `flash.ambient --selftest` 61/61 (+ 6 mutants)
- `benchmarks/ts_perception_check.py` 47/47 (+ 13 mutants),
  `benchmarks/ts_patch_check.py` 52/52 (+ 15 mutants) — the second language
- `benchmarks/patch_landing_check.py` 65/65 (+ 28 mutants) — a verified edit reaching disk
- `benchmarks/session_check.py` 81/81 (+ 26 mutants) — the chat answering in prose
- `benchmarks/portable_paths_check.py` 15/15 (+ 7 mutants),
  `benchmarks/documented_commands_check.py` 8/8 (+ 5 mutants) — no host path in a
  published record, and every `flash` command a document cites exists in the parser

## Live run witnessed here (real weights, not a selftest)

Verbatim from `benchmarks/results/session_pty_r715g_20260929.log` — a real
pseudo-terminal, three typed turns, each typed only after the previous verdict reached
the screen, on a scratch demo tree under `/tmp`:

```
# command: python -m flash.cli session --context /tmp/flash-chat-demo --test /tmp/flash-chat-demo/t.py --apply
[t+  0.06s] flash session on /tmp/flash-chat-demo against the oracle /tmp/flash-chat-demo/t.py — one ask per line, and each answer arrives before you type the next. `quit` or Ctrl-D ends it.
[t+ 20.78s] [turn 1] routed=small tier=big solved=True attempts=3 (13.5s) patches=1 refused=0 whole=0 outside=0
[t+ 20.99s] [R-3.2] wrote money.py (+4 -1 lines)
[t+ 28.95s] [turn 2] routed=small tier=small solved=True attempts=1 (5.7s) patches=1 refused=0 whole=0 outside=0
[t+ 29.16s] [session] turns=2 solved=2 written=2 seconds=19.2 last_rc=0
=== child exited rc=0 after 29.4s ===
```

This is the demo task, not a benchmark suite; the
before/after claim about the loop's own value lives in SPEC's cross-tool panel, which is
measured against the same served weights behind a token-counting proxy.

## Download shapes, measured (dated, on the v0.0.1-era tree)

`benchmarks/r75_sdist_battery_check.py` built one sdist and ran the whole battery against
it twice, 2026-09-28. `git diff` over `pyproject.toml` and `MANIFEST.in` between that run
and this release is **comment-only**, so the two shapes below are still this release's
shapes; the *code* under them has moved a lot, so their totals are the dated prints of the
tree that made them, not of `0.1.0`:

- **shape A** — `pip install flash_coder-0.0.1.tar.gz`: `33/35` lines green in 13 min 22
  s, rc 1, with `checks 1119  oracle 20  §6 total 1139  mutants 85`. The two red lines
  are the TypeScript vectors refusing for want of the `ts` grammar, which is exactly what
  a plain install should do.
- **shape B** — after `pip install '…[ts]'`: `35/35` green in 13 min 14 s, rc 0,
  `checks 1210  oracle 20  §6 total 1230  mutants 111`.
- A wheel is package-only by design, so `flash selftest --all` from an installed wheel
  exits 2 and says the verification surface did not come with it; `flash doctor` exits 1
  and names the absent `benchmarks/`. Clone the repo (or unpack the sdist) to run the
  battery.

## Pre-publish sweep (this pass, printed 2026-10-05)

`python benchmarks/publish_secret_scan.py` — working tree, every blob ever committed, and
filename shapes:

```
publish sweep — 432 files in the working tree (0 of them not yet tracked) and 1007 historical blobs, 7 credential families + filename shapes
  matches accepted by name: 2   unlisted: 0
  planted-secret proof: 8/8 families bite
OK  no unlisted credential shape in the 432 files a push sends or in any of the 1007 blobs ever committed, and 8/8 families proved they can bite.
```

Two matches accepted **by name**, each with the reason the scan prints: a literal
`proxy-no-auth` string handed to a local token-counting server that authenticates nobody,
and a base64 run inside an npm `sha512-…==` integrity digest. Every credential pattern is
planted in the repo's own test fixtures so the scan has to bite — 8/8 families did — which
is what makes the 0 findings a measurement instead of a blind spot. `host-path` identity
appears 2083 times, and stays: those are documented policy lines, gated by
`portable_paths_check.py` (15/15 + 7/7) so no published record under `benchmarks/results/`
carries one.

---

## What this release does NOT claim

Booked open, with the reason it is open. None of these are closed by rewording a gate.

| Item | Status | Why |
|---|---|---|
| A **CI** run printing the whole 37-line §6 re-read | OPEN | A hosted macos runner is not allowed to be cool enough to offer tournament width ≥ 2, and that row's precondition is a fact about the box. The whole-tree §6 print is the local one above, on this release's own tree; CI's prints are per-lane and say so. |
| The two backend-bound rows measured *passing* on Linux | Impossible in that lane | A Linux install has no backend to load; the lane names them as refused. |
| Prose chat transcript against real weights, beyond the demo task | OPEN | 81/81 + 26/26 offline, and one pseudo-terminal demo; no wider live transcript. |
| Linux/Windows **generation** | GATED, not claimed | MLX is Apple Silicon only; everything except generation installs and runs there. A PC port is a model-layer port plus a full re-measure. |
| Trained adapter vs frozen harness (R-6.4) | MEASURED NEGATIVE | 16/20 trained vs 18/20 base on the frozen suite; the shuffled control at 19/20 beat it. Training work is deferred by the maintainer. |
| `--debug` feedback gain (R-4.3) | MEASURED NEGATIVE | 27/30 traceback vs 25/30 `--debug`; no measurable delta, so `--debug` stays off by default. |
| Cursor / Copilot cloud arms (R-7.13) | OPEN | Neither is headless-capable here and both bill a paid account; the comparison would not be like-for-like. |
| 16 GB co-residency, watts/task, 24 h chaos, 10-developer week | OPEN | Needs hardware, sudo, hours and people this box does not have. |

No benchmark images (a fabricated dashboard set was reverted), no vendor figures pasted
into a table, no projected number presented as measured.

## Dependencies

- Python ≥ 3.11
- MLX backends (Apple Silicon only): `mlx-lm>=0.31`, `mlx-vlm>=0.3` (optional, for vision)
- TypeScript grammar (optional): `pip install -e .[ts]` — `tree-sitter`,
  `tree-sitter-typescript`
- `[dev]` pulls `pyflakes`, `build` **and** the `ts` grammar, because the battery's two
  TypeScript rows are in the default run

MIT. See `LICENSE`. `SPEC.md` records which of its own gates measured NO, and
`CHANGELOG.md` traces every claim here to the run that printed it.
