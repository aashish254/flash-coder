# Configuration

**There is no config file.** Nothing in `flash` reads a dotfile, a YAML in
`~`, or an environment variable you have to set before the first run. That is
a deliberate limit, and `flash doctor` states it on the page: the one place a
path can come from the environment is the model cache, which it prints
resolved rather than asking about.

So this page has three sections: the flags that change behaviour per run, the
directories the tool writes to, and the two environment variables it honours.

## Per-run flags

Everything tunable is on the command line, so a benchmark and a Tuesday
edit are the same program with different arguments. `flash run --help` is the
authority; these are the ones that change what the agent *does*, in the order
they appear in its loop:

| flag | what it decides |
| --- | --- |
| `--test FILE` | the verifier. Required. The run's success is `python FILE` exiting 0 — nothing about configuration is trusted beyond this file. |
| `--context PATH` | the repository whose symbol skeleton is injected into PERCEIVE. Omit it and the agent answers from the prompt alone. |
| `--small REPO` / `--big REPO` | the two model tiers. Defaults are the §22 matrix names in `docs/models.md`; passing them is how you A/B a candidate without editing code. |
| `--attempts N` | generations per tier before escalating. |
| `--allow-big auto\|always\|never` | escalation policy against the power governor. `auto` obeys the machine (see below), `always` is the benchmark override, `never` is single-track. |
| `--constrain` | mask every decode step to the task's output contract, so a malformed answer is structurally impossible (R-4.2). |
| `--debug` | re-run a failure under a line tracer and put the execution digest in the retry feedback (R-4.3). |
| `--no-source-hint` / `--no-graph-hint` | withhold one of the two context blocks PERCEIVE assembles. Both exist because each was measured on its own (`docs/architecture.md`). |
| `--confidence` | gate an answer on the token-level confidence signal instead of on the test alone (R-2.3). |
| `--adapter DIR` | load a LoRA adapter on the small tier. A named directory with no `adapters.safetensors` raises rather than quietly running the base model. |
| `--tournament K` | K candidates per task, scored by the verifier, adopted only on a majority (R-5.x). |
| `--trace-full` | keep the entire prompt/response in the trace record instead of the digest. |

The suite-side commands (`flash run-suite`, `flash learn`, `flash jobs`) take
the same names; `flash <cmd> --help` prints each one.

## What the machine decides for you

`flash power` prints the governor's verdict, and it is the only part of the
configuration you cannot set:

```
caps: maximum-performance: big-tier=yes max-model=12.0GB width=4 background=no
```

`max-model` comes from measured free memory at the moment you ask, so this
line moves between runs and is re-derived rather than quoted — on a 32 GB box
on battery, or with a second model loaded, the brain tier gets shed and the
agent stays on the 7 B. `--allow-big always` overrides it for a benchmark;
that is the honest way to compare tiers, and the way to cook a machine.

Python itself is a floor, not a preference: `flash/doctor.py`'s `PY_FLOOR` is
`(3, 11)`, and `flash doctor` says so on its first line.

## Where state is written

Four directories under `benchmarks/results/` in the checkout, plus one in your
home:

| path | holds | written by |
| --- | --- | --- |
| `benchmarks/results/traces/` | one JSONL record per run: stages, tokens, tier, verifier result | every `run` / `run-suite`, unless `--trace-full` is off in which case still the record, without the full text |
| `benchmarks/results/jobs/` | resumable job state — the banked step of a paused training job, the router's fit watermark | `flash learn`, `flash jobs` |
| `benchmarks/results/datasets/` | mined SFT pairs and their manifest, including what was excluded for leakage | `python -m flash.train --dataset` |
| `benchmarks/results/adapters/` | LoRA checkpoints | the training job, when it runs |
| `~/.flash/ambient/` | draft worktrees and the log every ambient-mode safety claim is read from | `flash ambient` |

The last one is outside every working tree on purpose: ambient mode's rule is
that it may write inside its own worktree and nowhere else, and a write that
tries to leave it raises rather than normalising the path.

Nothing else on the machine is touched. No telemetry, no update check, no
account. `requests` is a dependency because `flash web` reads documentation
over HTTPS when you ask it to; if `transformers`/`torch` are absent it falls
back to keyword ranking and says that it fell back.

## Environment variables

Two, both for the model cache, both read through the Hugging Face downloader
rather than by this package:

- `HF_HOME` (or `HUB_HOME`) — where the 4-bit weights live. `flash doctor`'s
  `cache` line prints the resolved path and how many of the four §22
  candidates are present there, so a stray `HF_HOME` is visible instead of
  looking like a re-download.
- `TMPDIR` — not read for configuration; `flash/sandbox.py`'s own selftest asserts a
  sandboxed child sees the temp directory it was given, which is why the variable
  appears in that module's tests.

`PYTHONHASHSEED` is *set* (to a fixed value) on children whose verdict must
reproduce, never read as user configuration.

## Optional installs

`pyproject.toml` carries three extras, and each one's absence is a message
rather than a crash:

```bash
pip install -e .[dev]   # pyflakes + build: what CI runs and what you run to check your own work
pip install .[vlm]      # mlx-vlm: `flash run-vis-suite`, screenshots through a VLM
pip install .[web]      # transformers + torch: bge embeddings for `flash web` ranking
```

MLX itself is not an extra — it is a marked dependency
(`sys_platform == 'darwin' and platform_machine == 'arm64'`), and with it
blocked every one of the package's 26 modules still imports, which
`benchmarks/backend_free_check.py` measures rather than asserts.
