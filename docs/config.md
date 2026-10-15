# Configuration

**There is no config file.** Nothing in `flash` reads a dotfile, a YAML in
`~`, or an environment variable you have to set before the first run. That is
a deliberate limit, and `flash doctor` states it on the page: the one place a
path can come from the environment is the model cache, which it prints
resolved rather than asking about.

So this page has three sections: the flags that change behaviour per run, the
directories the tool writes to, and the environment variables it honours.

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
| `--backend auto\|mlx\|llama.cpp` | which model library answers (R-7.17d). `auto`, the default, takes the first that imports with MLX preferred; a named one is *enforced*, because a published number and a run quietly served by different backends is a wrong measurement rather than a faster one. On a box with neither it refuses in one sentence naming both routes. |
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
complains about a line number in the one file the session must not edit.

What it prints, in the order a reader sees it: one `[working] turn N …` line
**before** the turn starts work (which tier answers first, how many attempts, what
it is graded against), one `[turn N] routed=… tier=… solved=… attempts=…` verdict
after the oracle answers, one `[session] turns=… solved=… written=… seconds=…
last_rc=…` report at EOF, then the session's bill, and it exits with the last
turn's code. The promise line and the verdict are deliberately different shapes: a
running 7B takes tens of seconds, and the line before the work is what tells a
slow turn apart from a hung one.

The bill (`[cost] …`, plus one indented row per turn) is the same numbers the trace
already holds — the token counts and `ms` of each generation, the oracle runs, and
the escalations — so it says what the session cost, which library answered each
turn, and which tier ended up answering it. It is written into the trace as a
`session_cost` record before the session closes, so `flash trace show <id>`
afterwards agrees with what was on screen. A turn Ctrl-C killed is billed (its
tokens were spent) but not counted, and the line says so; a session that generated
nothing prints no bill at all.

Four lines starting with `/` are commands, not prompts: `/help` lists them, `/quit`
leaves (the ask typed after it never runs), `/save notes.md` writes the asks and
the answers so far to a file, and `/retry` asks the previous question again. None
of them charges a turn except `/retry`, and `/save` refuses to write over the
session's own oracle. Ctrl-C is a normal way to leave: the interrupted turn is
announced, never verdicted, the summary prints, the trace still closes, and the
exit code is 130 — the code a shell gives SIGINT, so a script cannot read an
interrupted session as a completed one.

On a keyboard it prints `you> ` before every read and a one-line banner before the
first, which is the difference between a session that looks like a chat and one that
looks like a hang: a running 7B takes seconds, and a cursor with no marker next to it
gives no way to tell those apart. A pipe gets no marker at all — `printf 'ask\n' |
flash session …` is a driver, and a driver's captured text has to stay parseable.

### `flash models` — the three verbs that own the GGUF cache

`--backend llama.cpp` needs a file, and `flash models` is the only thing in the
package that fetches one (R-7.18). `flash models --help` is the authority:

| verb | what it does |
| --- | --- |
| `flash models pull fast` | downloads the pinned GGUF into `~/.cache/flash/gguf`, resumably, and registers it as a tier only after the SHA256 matched the pin. It prints the byte count and asks first; without a terminal to ask, it refuses unless you pass `--yes`. `--force` discards a partial and starts at byte 0, `--quiet` drops the per-chunk line. The tier is `fast` or `brain`. |
| `flash models list` | what the cache holds, read from file sizes and the manifest — it never opens a weight, which is why it is fast and why it cannot tell you the bytes are right. |
| `flash models verify fast` | the only command that re-hashes, so it reads the whole file. A mismatch drops the tier from the manifest and leaves the bytes for a human, because flash is not owed an opinion about a file it did not put there. With no tier named it re-hashes every pulled one. |

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

Four directories under `benchmarks/results/` in the checkout, plus the caches in
your home:

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

Four the package reads and one that only appears in a test — `grep -rn "environ"
flash/*.py` finds exactly these names and nothing else, which is the measured version
of "there is no config file":

- `HF_HOME` — where the MLX weights live. The one cache path `flash` resolves itself
  (`flash/doctor.py`'s `cache` line prints it resolved, with how many of the four §22
  candidates are there), so a stray `HF_HOME` is visible instead of looking like a
  re-download. **One asymmetry worth knowing:** `huggingface_hub` also honours
  `HUB_HOME`, and `flash` does not — a cache configured through `HUB_HOME` alone
  reads to doctor as empty.
- `FLASH_GGUF_DIR` — where the GGUF weights live, for `--backend llama.cpp`.
  Default `~/.cache/flash/gguf`; `flash models` and `flash doctor`'s GGUF line both
  print the resolved path, and this is the variable to set when the volume that
  holds your home directory cannot fit 4.68 GB.
- `FLASH_HF_ENDPOINT` — the host `flash models pull` downloads from. The default is
  Hugging Face itself; it exists because the download is the one outbound request
  this feature makes, and a mirror or an air-gapped box needs to be able to answer
  it. `benchmarks/models_pull_check.py` runs the whole vector against a local server
  through this variable, which is how the gate is offline.
- `FLASH_GGUF_N_CTX` — the context window llama.cpp is handed, default 8192. It is
  memory, not style: the KV cache is what makes a 4.68 GB file cost more than
  4.68 GB of RAM.
- `TMPDIR` — not read for configuration; `flash/sandbox.py`'s own selftest asserts a
  sandboxed child sees the temp directory it was given, which is why the variable
  appears in that module's tests.

`PYTHONHASHSEED` is *set* (to a fixed value) on children whose verdict must
reproduce, never read as user configuration.

## Optional installs

`pyproject.toml` carries six extras, and each one's absence is a message
rather than a crash:

```bash
pip install -e .[dev]    # pyflakes + build + the ts extra: what CI runs and what you run to check your own work
pip install .[mac]       # mlx-lm, Apple Silicon only — the name flash's own remedy line prints
pip install .[vlm]       # mlx-vlm: `flash run-vis-suite`, screenshots through a VLM
pip install .[web]       # transformers + torch: bge embeddings for `flash web` ranking
pip install .[ts]        # tree-sitter + its TypeScript grammar: `flash graph --lang ts`
pip install .[local]     # llama-cpp-python: `--backend llama.cpp`, the non-MLX route
```

`[local]` is the one that costs something to install: measured on PyPI (2026-10-15),
`llama-cpp-python` 0.3.36 is one sdist and no wheel for any platform, so the install
compiles llama.cpp with whatever toolchain the box has. That is a thing a person
chooses, which is why it is an extra and why it is NOT in `dev` — CI would pay that
compile on every job. It carries no platform marker for the same measured reason: the
package itself restricts nothing but `requires-python >=3.8`.

`[mac]` is a name, not a new install. `mlx-lm` stays a marked *dependency* (it is what
makes `pip install .` on Apple Silicon the shape every published §6 number came from,
and moving it would silently turn a Mac install into one that cannot generate), so
`[mac]` resolves to the same thing there and to nothing elsewhere. It exists because
`flash/backend.py`'s Apple-Silicon remedy line tells you to type it — and until
`benchmarks/backend_message_check.py` started sweeping every `flash-coder[…]` name the
package prints against the declared extras, that extra did not exist, which pip answers
with "Does not provide the extra".

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
(`sys_platform == 'darwin' and platform_machine == 'arm64'`; `[mac]` above is only the
name the remedy line prints), and with it
blocked every one of the package's 29 modules still imports, which
`benchmarks/backend_free_check.py` measures rather than asserts (re-read 2026-10-15 as
`SWEEP 29/29`, up from 27 when `flash/backend.py` and `flash/models.py` joined).
