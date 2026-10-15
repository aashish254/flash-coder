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

## The GGUF tiers (`--backend llama.cpp`)

The same two tiers exist as GGUF files, so the loop can generate on a machine that
cannot run MLX at all — Linux, Windows, or an Apple box that never installed
`mlx-lm`. Nothing is bundled, no API key is involved, and the download is the only
outbound request this feature makes.

| Tier | Repo | File | Bytes, exactly as pinned | SHA256, exactly as pinned |
| --- | --- | --- | --- | --- |
| fast | [`bartowski/Qwen2.5-Coder-7B-Instruct-GGUF`](https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF) | `Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf` | 4,683,074,336 (4.68 GB decimal) | `1664fccab734674a50763490a8c6931b70e3f2f8ec10031b54806d30e5f956b6` |
| brain | [`lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF`](https://huggingface.co/lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF) | `Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf` | 18,632,186,176 (18.63 GB decimal) | `79ad15a5ee3caddc3f4ff0db33a14454a5a3eb503d7fa1c1e35feafc579de486` |

**Where those two rows came from.** Each byte count and each hash is the `lfs.oid`
and `size` field of one `GET https://huggingface.co/api/models/<repo>/tree/main`,
read on 2026-10-14 and re-read on 2026-10-15 (both files matched both times). They
are the values `flash/models.py` downloads against, and
`benchmarks/models_pull_check.py` fails if the document and the code ever disagree —
the same number is not allowed to live in two places. GB here is decimal (1000³),
because that is what the upstream reports and what a metered connection is billed in.

```bash
pip install 'flash-coder[local]'      # llama-cpp-python — a source build, see pyproject
flash models pull fast                # asks first: it names the 4.68 GB before byte 0
flash models list                     # what is here, what size, what is half-done
flash models verify fast              # the only command that re-hashes a weight
flash run task.json --backend llama.cpp --small fast
```

A pull is resumable by design: the bytes go to `<file>.gguf.part`, the next run asks
the host for `Range: bytes=<size already on disk>-`, and a host that ignores the
range and answers 200 restarts from byte 0 rather than appending a whole file onto a
prefix. The manifest entry that makes a file a *tier* is written only after the
SHA256 matched the pin, so `--small fast` can never resolve to bytes that were not
verified; a mismatch deletes the download and leaves the manifest alone. `FLASH_GGUF_DIR`
moves the cache, `FLASH_HF_ENDPOINT` moves the host, `FLASH_GGUF_N_CTX` sets the
context llama.cpp is given (default 8192).

**What this page is NOT claiming.** No llama.cpp generation has been run in this
repository. The gate that covers this feature
(`python benchmarks/models_pull_check.py`, **53/53 checks and 9/9 mutants** as
printed on 2026-10-15) drives the real downloader against local
`http.server` fixtures and no network, because the pins above are 4.68 GB and
18.63 GB and a test that fetched them would be a bill rather than a proof. So the
arithmetic inside llama.cpp, the quality of a Q4_K_M quantisation against the 4-bit
MLX tier, and any number comparing the two routes are measured NOWHERE here.

**Licenses of these two mirrors, as fetched 2026-10-15.** The `bartowski` card
states `apache-2.0`; the `lmstudio-community` card states **nothing** — its front
matter carries `quantized_by: bartowski` and
`base_model: Qwen/Qwen3-Coder-30B-A3B-Instruct`, and the word "license" does not
appear in the file at all. The upstream Qwen cards are the authority for both, and
this page's release-time rule below applies to the GGUF route exactly as it does to
the MLX one.

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

`mlx` is Apple-silicon-only, and it is handled with a marker rather than a crash:
`pip install flash-coder` on Linux installs everything except `mlx-lm`, and every
module of the package still imports. The count is a measurement, not an inference
from the marker: with `mlx` blocked in
a child process, all **29 of the 29** submodules of `flash` import
(re-measured 2026-10-15, up from 27 on 2026-09-28 because `flash/backend.py` and
`flash/models.py` joined the package; `benchmarks/backend_free_check.py`, which is a
§6 line) and
the single thing that
raises is the call that needs a forward pass, with a message naming MLX. The
denominator is the sweep reading the package, not a checked-in total, so it moved
from 26 to 27 when `flash/lang_ts.py` was added and it will move again; the gate is
numerator == denominator with a floor. Two used
to fail at import — `flash.decide` and `flash.route` through it — until SPEC
**R-7.7** moved the backend import inside `decide()`; before that fix the sweep read
**24 of 26**, which is the number two documents cited differently from a third.

Since **R-7.17d** MLX is not the only thing that can answer the loop: `flash/backend.py`
is a seam with two implementations, and the oracle loop, the tournament, the ledger,
the governor and the confidence table none of them know which one replied. That is a
statement about the code, gated by `python benchmarks/backend_seam_check.py` (**R-7.17d**) with the
backend blocked at the interpreter, and by `python benchmarks/models_pull_check.py`
(**R-7.18**) for the download half. It is NOT a measured llama.cpp run: no generation has happened
through that route in this repository, so what the GGUF tiers buy in quality and in
tokens per second is open, and the page above says so rather than borrowing a number.

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
  `models list`, `models pull`, `models verify`, and the entire offline battery;
- works if you install the other backend: anything that generates text (`solve`,
  `run`, `run-suite`, `session`, `decide`, `ambient`) — `pip install
  'flash-coder[local]'`, then `flash models pull fast`, then `--backend llama.cpp`.
  `vision` stays MLX-only: it goes through `mlx-vlm`, which the seam has no second
  implementation of, and llama.cpp reports `embed: False` and `constrain: False` —
  the capability table is what refuses, not a string comparison on a name;
- does not work with nothing installed: generation, and it says so in one sentence
  naming both routes instead of a traceback.

The MLX cache directory is the Hugging Face default, `~/.cache/huggingface`, or
`HF_HOME` if a user set it; the GGUF cache is `~/.cache/flash/gguf`, or
`FLASH_GGUF_DIR`. `flash doctor`'s `cache` section prints the resolved MLX path and
how many of the four §22 tier candidates are already in it — the number
this file could not give you when the box was open — then the GGUF line
`flash models list` prints, verbatim from the same function, and its `machine`
section carries `flash power`'s verdict about what this box may load. Its `verify`
section asks the seam rather than `import mlx.core`, so a Linux box with
`llama-cpp-python` and no MLX is told that generation runs there. Like every `flash`
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
gate a bot has to pass is the gate a human can run. **Both workflows now execute:**
the remote is configured, and `CI` on main at `850af44` (run `37349276866`,
2026-10-05) came back with all three jobs green — `static (3.12)`, `static (3.11)`
and the macos-14 `battery` job, which is the run that prints §6 on someone else's
Mac. **What that page could not say until 2026-10-15:** `Nightly live check` had
failed all **15 of its 15** scheduled runs (Oct 1–15), every one of them in
*Offline battery first* on the same two BAD lines, because a fresh macos runner has
an empty HF cache (`Cache not found for input keys: hf-macOS-qwen2.5-coder-7b-4bit-v1, hf-macOS-`)
and `flash.grammar --selftest`'s mask checks read the fast tier's tokenizer from it
— so the gate asked the runner for a file only the step *behind* it downloads. The
CI job already pre-warmed those 7 files; the nightly now carries the same two steps,
with its green witness still pending the next scheduled run.
