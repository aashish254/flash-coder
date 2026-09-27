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
installs everything except `mlx-lm`, and 24 of the 26 modules still import — the
count measured by hand with `mlx` blocked on 2026-09-27, not asserted from the
marker — the
two that don't are `flash.decide` and `flash.route`, and SPEC **R-7.7** is the open
box that makes the whole package import everywhere so only a generation attempt
reports the missing backend.

What that means in practice on a non-Mac:

- works: `flash context`, `perceive`, `find`, `refs`, `symbols`, `graph`,
  `lsp-selftest`, `trace`, `ledger`, `power`, `router-fit`, and the entire
  offline battery;
- does not work: anything that generates text (`solve`, `run`, `run-suite`,
  `decide`, `ambient`, `vision`).

The cache directory is the Hugging Face default, `~/.cache/huggingface`. Nothing
in the tree reports how much of each tier is already in it yet: that is SPEC
**R-7.6**'s `flash doctor`, an open box, and today the honest pre-flight is
`flash power`, which answers what the machine may load and says nothing about the
cache.

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
labelled `needs-battery-run`, and why the battery is a CI gate — today
`python benchmarks/battery_reread.py` is that gate, and the committed workflow
also calls `flash selftest --all`, which is SPEC **R-7.6**'s open box and does not
exist yet.
