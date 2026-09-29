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

### `flash session` — the two flags that are only its own

`flash session --context DIR --test FILE` reads one prompt per line from stdin,
**answers it, and only then reads the next**, and
takes every cost flag above unchanged, plus two of its own and one deliberate
absence (`flash session --help` is the authority):

| flag | what it decides |
| --- | --- |
| `--turns N` | stop after N turns even if stdin keeps coming. `0`, the default, reads to EOF — `quit`, `exit`, `q` or Ctrl-D end it. This is what lets a pipe and a keyboard drive the same session. |
| `--apply` | write each verified patch into `--context`. Off by default, exactly as on `flash run`: opening a session must never be what edits a project. |
| *(no `--edit`)* | a session is always the patch arm, because a whole-file answer is the one thing a multi-turn surface cannot land safely. The parser rejects `--edit` rather than ignoring it. |

Each turn re-reads `--context` from disk, so turn N+1 patches what turn N wrote
rather than what the process remembered, and the oracle named by `--test` is
protected on every turn of the session, not only the first patch set. It is
protected by name: a turn that addresses `t.py` is refused as *the oracle this run
scores against* before the arm looks at the address, so the sentence never
complains about a line number in the one file the session must not edit. The session
prints one verdict line per turn and one `[session] turns=… solved=… written=…
seconds=… last_rc=…` report at EOF, and exits with the last turn's code.

On a keyboard it prints `you> ` before every read and a one-line banner before the
first, which is the difference between a session that looks like a chat and one that
looks like a hang: a running 7B takes seconds, and a cursor with no marker next to it
gives no way to tell those apart. A pipe gets no marker at all — `printf 'ask\n' |
flash session …` is a driver, and a driver's captured text has to stay parseable.

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

`pyproject.toml` carries four extras, and each one's absence is a message
rather than a crash:

```bash
pip install -e .[dev]   # pyflakes + build + the ts extra: what CI runs and what you run to check your own work
pip install .[vlm]      # mlx-vlm: `flash run-vis-suite`, screenshots through a VLM
pip install .[web]      # transformers + torch: bge embeddings for `flash web` ranking
pip install .[ts]       # tree-sitter + its TypeScript grammar: `flash graph --lang ts`
```

`dev` pulls `ts` in deliberately. Two of the §6 battery's 37 lines are the
TypeScript vectors, and one documented install command has to be enough to print
every published line — otherwise a contributor's `.[dev]` install would watch two
lines refuse and CI would call the main branch red for a dependency its own install
line never asked for. A **plain** `pip install .` is still the downloader's shape and
still gets the refusal sentence rather than a string-matching fake: measured on the
download, that install prints every battery line but the two TypeScript vectors, each
refusing with the sentence above (last counted on the 35-line tree, as 33 of 35).

The `ts` extra is the one a person with a front end in their repo will want, and
its absence is measured rather than described: without it the Python pass still
builds the graph, `--lang ts` prints one refusal naming the command above, and the
refusal is counted as a blind spot so a `summary()` cannot read as complete.
`benchmarks/ts_perception_check.py` gates that sentence by faking the grammar's
absence three times (47 checks: 3 require the printed refusal, the still-answering
Python pass and the counted blind spot; 2 more gate what the grammar refuses to
guess, an unparseable file and a broken one's neighbours).

MLX itself is not an extra — it is a marked dependency
(`sys_platform == 'darwin' and platform_machine == 'arm64'`), and with it
blocked every one of the package's 26 modules still imports, which
`benchmarks/backend_free_check.py` measures rather than asserts.
