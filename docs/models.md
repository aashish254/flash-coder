# Models

Everything measured here was measured on one machine — an Apple-silicon Mac with
32 GB of memory, macOS 27.0, Python 3.11.15 — on 2026-09-27. Numbers that came
from a command are labelled with it; numbers that came from a web page are
labelled as fetched, with the date.

## The two tiers

Flash Coder runs a cheap model first and a large one only when a verified failure
says the cheap one is out of ideas. Both are Hugging Face repos pulled by
`mlx-lm` on first use; nothing is bundled in this repository.

| Tier | Repo | What it is for | Download, `du -shL` on the HF cache | `flash power`'s own estimate |
| --- | --- | --- | --- | --- |
| fast | [`mlx-community/Qwen2.5-Coder-7B-Instruct-4bit`](https://huggingface.co/mlx-community/Qwen2.5-Coder-7B-Instruct-4bit) | every first attempt and every retry | **4.0 GB** across 11 files | `~4.4GB` |
| brain | [`mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit`](https://huggingface.co/mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit) | escalation, and the tournament's wide arm | **16 GB** across 16 files | `~17.3GB` |

Select them per invocation, not per config file — there is no config file yet:

```bash
flash solve "…" --model mlx-community/Qwen2.5-Coder-7B-Instruct-4bit
flash run-suite --tasks … --small <fast repo> --big <brain repo> --allow-big auto
```

`--allow-big` is the honest knob for a small or hot machine: `never` keeps the
brain off no matter what the governor says, `auto` (the default) lets §34.1's
power profile refuse it, `always` overrides that refusal for a benchmark run and
prints what it overrode.

## Licenses

Fetched from the two Hugging Face pages on 2026-09-27: **both `mlx-community`
repositories state `apache-2.0`** on the model card, and both cards say the
weights were converted from the upstream `Qwen/…` release. The two upstream
cards — [Qwen2.5-Coder-7B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct)
and [Qwen3-30B-A3B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)
— are the authority on the weights you actually run, so read them before
redistributing anything derived from a fine-tune. **Check them again at
release time**: Qwen's larger releases have previously used a research license
with a monthly-active-user threshold, and the MLX mirror's tag is not the
upstream's.

This repository's own code is [MIT](../LICENSE). Model weights are not part of
it and are not licensed by that file.

## Platform

`mlx` is Apple-silicon-only, and that is the one real portability limit here. It
is handled with a marker rather than a crash: `pip install flash-coder` on Linux
installs everything except `mlx-lm`, and every module of the package still imports.
The count is a measurement, not an inference from the marker: with `mlx` blocked in
a child process, all **27 of the 27** submodules of `flash` import
(re-measured 2026-09-28; `benchmarks/backend_free_check.py`, which is a §6 line) and
the single thing that
raises is the call that needs a forward pass, with a message naming MLX. The
denominator is the sweep reading the package, not a checked-in total, so it moved
from 26 to 27 when `flash/lang_ts.py` was added and it will move again; the gate is
numerator == denominator with a floor. Two used
to fail at import — `flash.decide` and `flash.route` through it — until SPEC
**R-7.7** moved the backend import inside `decide()`; before that fix the sweep read
**24 of 26**, which is the number two documents cited differently from a third.

Importing is the weaker claim, and since **R-7.16** the battery stops making the
stronger one falsely: with `--backend-free` the whole §6 tree measures **35 of 37**
rows green and prints a `REFUSED` line naming the other two — `flash.grammar
--selftest`, whose mask checks load the tokenizer through `mlx_lm.tokenizer_utils`,
and `benchmarks/session_check.py`, whose chat arm walks past
`flash.loop.solve_routed`'s preamble `import mlx.core` — then prints its own subtotal
(`checks 1301  oracle 20  §6 total 1321  mutants 146`, this tree's own lane print) under a
`NOT a §6 re-read` banner. A Linux runner is therefore never asked to fail a test it
has no code to fix — and the sentence it reads before that subtotal is its own, not a
Mac's: `backend_free_check`'s gate asks the installer to prove the blocker with the
branch this box earns, `proof --backend-free` where `import mlx.core` survives without
the shim and `note --backend-free` where it does not, with a mutant for printing
`proof` regardless.

Backend-free is one half of that promise; the other half is what the first real ubuntu
run found, and it is not about the model at all. `flash power` read its machine's
memory size from `sysctl hw.memsize` and nowhere else, so a Linux box with a readable
`/proc/meminfo` printed **21/22**; `flash.sandbox`'s vector ended its setup with a bare
`assert seatbelt() is True`, so on a box with no Seatbelt it raised `AssertionError`,
printed no fraction, and looked like a failing test nobody there can repair. Both are
fixed at the reading rather than re-labelled: `power` now tries `sysctl`, then
`/proc/meminfo`, then POSIX `sysconf`, and the line prints which one answered, and the
sandbox vector asks its nine Seatbelt claims in two arms so **34** prints on every
platform — enforcement where the kernel can refuse, the honesty of an absent jail where
it cannot. A row that has no arm to take stays REFUSED; a row that only ever spoke one
platform's dialect gets taught the other one.

What that means in practice on a non-Mac:

- works: `flash context`, `perceive`, `find`, `refs`, `symbols`, `graph`,
  `lsp-selftest`, `trace`, `ledger`, `power`, `router-fit`, `doctor`, `selftest`,
  and the entire offline battery;
- does not work: anything that generates text (`solve`, `run`, `run-suite`,
  `decide`, `ambient`, `vision`).

The cache directory is the Hugging Face default, `~/.cache/huggingface`, or
`HF_HOME` if a user set it. `flash doctor`'s `cache` section prints the resolved
path and how many of the four §22 tier candidates are already in it — the number
this file could not give you when the box was open — and its `machine` section
carries `flash power`'s verdict about what this box may load. Like every `flash`
command in this repo, both are gated by
`benchmarks/documented_commands_check.py` against the real parser.

## Versions every published number came from

| Package | Version |
| --- | --- |
| Python | 3.11.15 |
| mlx | 0.32.2 |
| mlx-lm | 0.31.3 |
| mlx-vlm | 0.7.2 |
| numpy | 2.4.6 |
| python-lsp-server | 1.15.0 |
| transformers | 5.17.0 |
| torch | 2.14.0 |
| requests | 2.34.2 |

A dependency bump is not cosmetic in this project: `mlx-lm` decides what a
greedy attempt emits, `python-lsp-server` decides what PERCEIVE resolves, and
both are cited by claims in `SPEC.md`. That is why Dependabot PRs here are
labelled `needs-battery-run`, and why the battery is a CI gate:
`flash selftest --all` is a thin wrapper on
`python benchmarks/battery_reread.py` — the same lines, the same printed totals,
and the same rc-2 refusal when the battery file is not beside the package — so the
gate a bot has to pass is the gate a human can run. **Gated here, not measured
there:** neither workflow has executed, because no remote is configured, so "the CI
runs the battery" describes a file in this repo rather than a green check mark.
