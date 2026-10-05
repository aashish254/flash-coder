# Changelog

Notable changes per release, written so a claim here can be traced to the run
that printed it. `git log --oneline` is the complete history — each commit naming
the measurement that decided it — and this file is the readable summary of it.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-05 — the version moved; the tag has not been created

**What the bump is.** `0.0.1` → `0.1.0` is one fact kept in four places, so they moved
in this one commit: `flash/__init__.py`'s `__version__` (the only source `pyproject.toml`
reads — it declares `dynamic = ["version"]`), the landing page's `VERSION` constant in
`site/src/lib/site.ts`, the install line the page renders in
`site/src/data/transcripts.json`, and these notes. The transcript was regenerated from a
live `flash doctor` rather than edited, which is why it now carries `flash 0.1.0`
because that is what the command printed. The `flash 0.0.1` occurrences in SPEC's R-7.5
measurements, in the `[0.0.1]` section below, in TODO's closed boxes and in every witness
under `benchmarks/results/` are **not** touched: each is a dated print of the tree that
made it, and relabelling one would make a measurement false. `v0.0.1` still points at
`6a0868b` and still will not be moved.

**What the release carries.** Everything in this section — the R-7.16 CI rounds, the two
platform-dialect rows fixed at the reading, the refused-row lane, the R-7.15h/g/f/e/c/b
session arc, the TypeScript second language, the sdist/install-shape measurements. Its
§6 totals are `checks 1429  oracle 20  §6 total 1449  mutants 172` over 37 vectors, read
from the print and not from this file, and the `--backend-free` lane a Linux install
gets prints `checks 1301  oracle 20  §6 total 1321  mutants 146` with its two backend
rows refused by name.

**What is proved, per platform.** CI is green on `37227171133`: `static (3.11)` 31m49s
and `static (3.12)` 31m57s on ubuntu both run the lane (`backend_free_check` **49/49 +
15/15**, then `battery_reread --backend-free`), and the macos-14 `battery` job — which
had never executed before that run — came back in 27m35s with **36** `OK`, no `BAD`, and
**1** row refused by name (`benchmarks/checkpoint_resume_check.py`, the §34.1 governor
offering tournament width 1 where the arm needs ≥ 2), printing
`checks 1394  oracle 20  §6 total 1414  mutants 172` under `NOT a §6 re-read`. That is
1429 − 35, the refused row's own 35 checks subtracted by the rule that gates it, and its
lines are kept in-tree as
`benchmarks/results/ci_battery_macos_37227171133_20261005.log`. Two things are still
open by nature, not by oversight: no CI run has printed the whole 37-line §6 re-read,
because a hosted runner is not allowed to be cool enough for that row's precondition, and
neither the tag nor any publication has happened — tagging `v0.1.0` is the user's call
and has not been made.

### Added
- **R-7.16: `--backend-free` now refuses the rows it genuinely cannot run, and says
  which cause killed them.** With `mlx`, `mlx_lm` and `mlx_vlm` unimportable in every
  child — the install shape every Linux and Windows user gets, from the platform
  marker in `pyproject.toml` — the battery measured 35 rows green and 2 that reach the
  backend for reasons that are real rather than planted: `flash.grammar`'s mask checks
  load the tokenizer through `mlx_lm.tokenizer_utils`, and `session_check`'s chat arm
  walks past `flash.loop.solve_routed`'s preamble `import mlx.core`. Calling those two
  failures is a red badge nobody can fix in code, and calling them skipped is a lane
  that totals nothing, so each now prints a **REFUSED** line carrying the sentence the
  row died with, `lane_claim` subtracts that row's own counts from `CLAIM`, and the run
  exits 0 under a `NOT a §6 re-read` banner naming the subtotal it printed. A third arm
  covers `checkpoint_resume_check.py`, whose precondition is that §34.1's governor
  offers tournament width ≥ 2 — a fact about the box, so it is refusable in *every*
  lane, because a hosted runner cannot be told to cool down and a warm laptop is not a
  failing test. The lists are decoders, not exemptions, and that is the measured part:
  `python benchmarks/backend_free_check.py` → **49/49 checks, 15/15 mutants** — the
  decoder read against ten texts of which seven must NOT decode (an unrelated
  `AssertionError: expected 5 got 4`, a healthy line that merely names `mlx_lm`, empty
  output, each cause in the other arm), the named rows checked as battery arithmetic, a
  live `--backend-free --quick grammar session` whose refused set must equal the
  parent's list (the child reads the lists off disk, so the agreement is a measurement),
  and a plain-lane child run **with the block injected and the flag withheld**, which
  must come back non-zero with a BAD line and no REFUSED. The three new mutants are
  killed by exactly those checks, one apiece. `ci.yml`'s macos `battery` job
  additionally caches and pre-warms the fast tier's tokenizer — 7 files, 11M, no
  weights — and asserts `battery_reread --quick grammar` before asking for §6, because
  four consecutive scheduled nightlies died with `flash.grammar --selftest want 47/47
  got ['24/25']` on a cold cache (latest run `37193175901`); verified here against a
  throwaway HOME, where the same row then printed **47/47** rc 0. The two identical
  steps for `nightly.yml` are written and held back: that workflow is untouched in this
  release, so the nightly's own failures remain open.
- **R-7.15h: `flash session` answers in prose when no oracle is named — it became a chat,
  not only a scorer.** Every mode used to demand `--test` and print telemetry, so a
  greeting got a `PATCH MISSING` refusal; the author's complaint was that it "can't even
  reply hi" and can't say *what is this* the way Claude Code does. Omit `--test` and the
  session is a MODE: `cmd_session` sets `use_oracle = args.test is not None`, threads
  `chat=not use_oracle` into `solve_routed`, which short-circuits after `enrich_task` (the
  project source stays in the prompt — R-7.15f), loads only the small model, and returns
  `routed="chat"` with a `chat` ledger row; the printed turn is the model's prose, not the
  `[turn N] routed=…` line. The banner says `no verification` — a turn is graded by nothing,
  and it can still carry a patch, so `hi`, `what does money.py do`, and `create a logger
  module that prefixes [app]` all work, the last writing a brand-new file with the R-7.15c
  create address. `run --test` stays required, so the scored path is unchanged, and
  `_land_edits` is chat-aware (a chat turn reports `this turn produced a workspace`, never
  `the oracle passed`). Offline: `python benchmarks/session_check.py --sweep` → **81/81
  checks, 26/26 mutants** in both lanes, the 31st line of §6 — the vector runs the real
  `solve_routed(chat=True)` with only `load_model`/`_generate` stubbed and asserts prose
  lands green, one model loads, the project header is present, a `chat` ledger row is
  written, a patch inside a prose answer founds a new file, and a refused patch prints
  `PATCH REFUSED` with the repair fed back; 6 new mutants, 3 of them below the command
  (`_oracle_key` unguarded, `_land_edits` scored-landing, `solve_chat` prose-refused).
- **R-7.15c: the patch arm can create a whole new FILE, not only revise a symbol.** Three
  of eight attempts on the demo turn invented a module (`format_dollar_amount.py`, then
  `cents_to_str.py`) and were refused because the workspace had no such key. `# edit:
  <newfile> :: *` (whole file) and `# edit: <newfile> :: +Name` (one definition) now
  create it — the grammar-checks the body, one patch per new file, an empty body is
  refused, and the oracle guard runs BEFORE the new-file branch. `land` accepts a key that
  was never in the workspace only when this run's patch arm founded it and the oracle
  scored it, so an unverified new file still cannot be written (the escape guard and the
  protected-oracle guard are unchanged). Offline: `python -m flash.patches --selftest` →
  **94/94**, `python benchmarks/patch_landing_check.py --sweep` → **65/65 checks, 28/28
  mutants** — the created file lands as a `[("money.py",+1,-1),("new.py",+1,-0)]` change,
  a 3-line create reports `+3 -0`, `../new.py` create is refused by the escape guard, and
  the oracle cannot be founded.
- **R-7.15: the loop got the surface the author was actually asking for — `flash
  session`, many turns against your repo and your own asserts, with the tree
  re-read from disk between turns.** The reports that made it a requirement were "if
  the project is done can i run it so i can test" and "what is this how will i code on
  this?": one task per invocation is a batch tool, and the thing being pointed at was
  a chat-shaped editor. Turns arrive one per line on stdin (a blank line is not a
  turn; `quit` / `exit` / `q` / Ctrl-D ends the session, `--turns N` caps it), the
  oracle is read once and is never a file the session may edit, and **each turn
  re-reads `--context` from disk**, which is what makes turn 2 edit what turn 1 landed
  rather than what the process remembered. The session is always the patch arm:
  `loop.EDIT` is set on entry and there is deliberately no `--edit` flag — the parser
  rejects it, because a whole-file rewrite on turn 7 of a session silently discards
  whatever turns 1–6 wrote. Each turn prints its own verdict line, the oracle's own
  words under `oracle|` when it fails, and one of the same three landing sentences
  `run` prints; the session closes with one parseable line
  (`[session] turns=3 solved=2 written=2 seconds=25.6 last_rc=0`) and exits with the
  **last** turn's code. Offline: `python benchmarks/session_check.py --sweep` →
  **48/48 checks, 16/16 mutants caught** in both lanes, the 30th line of §6 and the
  mutant roll-up's fourteenth vector — and the vector drives the real
  `cmd_session` with `_read_turns`/`solve` stubbed only at the two seams that need it,
  so `agree()` holds its 16 mutant copies indistinguishable from the shipped command
  over 7 scenarios. Live, on a tree that is not this repo and real weights
  (`Qwen2.5-Coder-7B-Instruct-4bit`): three turns printed
  `solved=True … [R-3.2] wrote temp.py (+1 -1 lines)`, then
  `PATCH REFUSED: temp.py:k_to_c — no symbol 'k_to_c' in temp.py (it defines: boiling_point, c_to_f, f_to_c, freezing_point)`
  with `--apply` writing nothing, then `solved=True … (+2 -1 lines)` —
  `benchmarks/results/session_live_20260929.log`, replayable under trace
  `20260929-131202-session-e176`.
- **R-7.15 clause 7: the shipped session was not a chat, and the author's third report
  is what proved it.** "where is the claude like chat option window … the whole point of
  the project is that we can code in chat like claude code" was not a missing feature
  request — `flash session` existed, and it still behaved like a batch file with a
  prompt painted on it, because `_read_turns` collected stdin into a `list[str]` and
  `cmd_session` looped over that list: **every** line had to be typed, and EOF reached,
  before the first turn ran. On a keyboard that is a cursor and silence until Ctrl-D.
  Turns are a generator now, yielding one request per line as it arrives, and a terminal
  gets `you> ` printed before each read plus a one-line banner before the first (a pipe
  gets no marker, because a session's stdout is also a report). Four new checks, two new
  mutants: the read/solve log must interleave (`read1, solve1, read2, solve2`), and
  `--turns 1` must stop **reading** stdin rather than only stop counting — that second
  one is what separates a cap from a session that keeps asking a terminal nobody is
  answering. Proved through a **pseudo-terminal**, not a pipe, by a driver committed with
  it (`python benchmarks/session_pty_demo.py`, ~40 s, real weights, seeds its own scratch
  tree): `pty.openpty()` + `select`, writing line N+1 only once line N's verdict had
  arrived, every chunk timestamped — banner t+0.06 s, first `you> ` t+2.15 s, turn 1's
  verdict **t+19.22 s**, turn 2 typed **t+19.43 s** —
  `benchmarks/results/session_pty_20260929.log`, trace `20260929-144507-session-54e4`.
  **Read that witness for what it also says: both turns lost, twice** — the arm was
  re-run to check it was not a one-off, and turn 1 lost identically each time.
  `tier=failed` twice with `denied=false, tier="big"` in the
  trace means the 30B was offered each turn and failed it too, and turn 1 lost to
  `PATCH REFUSED: money.py:format_dollar — no symbol 'format_dollar' in money.py (it
  defines: cents_to_str)` — R-7.15b, now with a measured victim on the demo task rather
  than on a fixture. The session closed `turns=2 solved=0 written=0 seconds=23.1 last_rc=1`
  and wrote nothing. So this pass bought the chat shape and did **not** buy the "accurate"
  half of the promise; the ledger moved to `48/16` on the session line, which makes the §6
  claim **checks 1298, oracle 20, §6 total 1318, mutants 149**, held as a `CLAIM` in
  `benchmarks/battery_reread.py` — and the whole-tree re-read on this tree then **printed
  exactly that**, 37 OK lines and no BAD line in **17 min 54 s** on AC at 80%
  (`benchmarks/results/battery_reread_r715c7_20260929.log`), so 1294/1314/147 is kept on the
  page as the measurement of the tree one clause earlier rather than being overwritten. Docs moved with it:
  README's fifth step is the keyboard shape, `docs/config.md` gained the marker rule and
  its pipe exception, `docs/architecture.md` says the reads interleave with the solves,
  `CONTRIBUTING.md` tells a contributor the session answers before it asks again, and
  `flash/__init__.py`'s §33.1 line names clause 7. The command census moved to **26
  distinct commands in 199 citations across 15 documents** (86 source paths), with
  `documented_commands_check.py` **8/8 + 5/5** and `portable_paths_check.py` **15/15 +
  7/7** on the edited tree, and `python -m pyflakes flash/*.py benchmarks/*.py` at
  **0 findings**.
- **R-7.15b is what that middle turn taught, and it is shipped: the patch arm can now
  create a symbol.** `# edit: file :: Symbol` resolved against the AST, so "add a function
  `k_to_c(kelvin)`" named a symbol that did not exist, was refused, and cost the turn — a
  correct refusal that served nobody. The address has an add form now: `CREATE_PREFIX = "+"`
  makes `Patch.kind` read `create` (`whole` → `create` → `range` → `symbol`), `resolve`
  hands it to `_resolve_create`, and the position is the extractor's rather than the
  model's — a top-level create lands after the last top-level definition, a
  `+Container.member` after that container's last member at the members' own indentation,
  and into an empty class right after its header. The span is the pure-insertion form
  `splice` already read (`start == end + 1`), so `spans = ()`, `regenerated == 0` and
  `outside_lines == 0` — a create owns no line of what was there — while `changed_lines`
  still charges it the one insertion point, which is the pair the checks keep apart because
  the audit that must read zero is the `spans`-based one. `check_result` refuses a create
  whose body defines a *different* name, an address on a name the file already has refuses
  with the sentence telling the model to drop the `+`, the applier owns the blank lines
  above what it inserts (two at Python top level, one inside a container and one in a
  `.tsx` — the second grammar's convention is what measuring a `.tsx` create found), and
  `parse_patches` needed **no** change: `EDIT_MARKER`'s address group was already `\S+?`,
  pinned rather than assumed, as is the other half — the `PROTOCOL` text the model is shown
  actually names the form.
  Gated the way R-3.2 clause 3 gates replacement, at three seams, all run 2026-09-29:
  `python -m flash.patches --selftest` **46 → 63/63** (a new `# 7b. CREATE` section of 17 —
  12 on what a create writes, 5 on what it must refuse, including create-then-revise in one
  set and one bad create voiding the whole set); `python benchmarks/ts_patch_check.py
  --sweep` **44 → 52 checks, 13 → 15 mutants** (the verb in the second grammar: `+Tag` at
  top level, `+Grid.spin` inside a class, and `loop._solve_edits` accepting a create);
  `python benchmarks/patch_landing_check.py --sweep` **40 → 48 checks, 22 → 24 mutants** in
  both lanes — at the arm, on disk and through the real command, which prints
  `wrote money.py (+4 -0 lines)`, because a create reported as `+0 -0` is the same sentence
  as a run that wrote nothing. The confusion this box demanded is mutanted twice over: a
  create whose body defines another name passing, and a landed create printing `+0 -0`,
  each caught by exactly one named check. The §6 whole-tree re-read on this tree then
  **printed the ledger it had been predicted into**: `checks 1331  oracle 20  §6 total
  1351  mutants 153`, **37** OK lines and no BAD line, with its own
  `matches SPEC §6 as written: 1331 + 20 = 1351 green, offline (+ 153 mutants)`, in **18
  min 42 s** on AC at 80% (`benchmarks/results/battery_reread_r715b_20260929.log`) against a
  `CLAIM` summed from `BATTERY` before the run and not edited after it; `1318 / 149` and
  `1314 / 147` stay on the page as the two earlier trees' prints. `pyflakes` **0 findings**.
  **The live arm was re-run on the very task that motivated it, and it still loses.** Same
  driver, same seeded tree, same two asks
  (`benchmarks/results/session_pty_r715b2_20260929.log`, trace
  `20260929-155858-session-f09b`): turn 1 `attempts=4 (15.6s) patches=1 refused=1`, turn 2
  `attempts=4 (12.6s) patches=1 refused=0` → `FAILING_ASSERT: assert cents_to_str(150) ==
  "$1.50" | GOT: '$1.5'`, `turns=2 solved=0 written=0 seconds=28.2 last_rc=1`. The eight
  attempts say why: the 7B's two on turn 1 were range addresses past the end of a 9-line
  file, the 30B's first invented a module (`format_dollar_amount.py is not one of the
  project files`), and only its **final** attempt wrote a create-shaped address — which is
  the first time the remedy printed, with no attempt left to use it. So the verb is no
  longer what blocks this task, and the gap is not one box but three, booked with their
  prices rather than reworded into a pass: **R-7.15c** create-a-FILE, **R-7.15d** a remedy
  offered on the tier's last attempt (~2 s small / ~5 s big, from the trace's own
  milliseconds), **R-7.15e** an oracle-addressed patch refused for a complaint about line
  numbers.
- **R-7.15e is shipped for the mechanism it asked for, and the demo turn still loses.**
  Two of the eight attempts on the last live run addressed the oracle — `t.py:zero_pad_cents`
  and `t.py:format_money` — and the arm had answered them with a complaint about
  coordinates: `L11-L11 is past the end of a 9-line file`, in a file this run scores
  against and must not change. The patch layer refuses by NAME now, before `resolve` ever
  sees the address, for all four `Patch.kind`s: `apply_patches(workspace, patches,
  oracle="")` voids the set with `t.py is the oracle this run scores against, so it is not
  a patch target — an assertion is never edited to fit the code. Change the module the
  test imports.`, and a PROTOCOL bullet puts the same rule in front of the model before it
  chooses an address. One function supplies the key to both gates that protect the oracle:
  `cli._oracle_key(args, task)` answers `_rel_to(args.test, args.context)` only when that
  name is a workspace key, `cmd_run --edit` and every `cmd_session` turn put it on the task
  as `test_path`, `loop._solve_edits` passes it to `apply_patches`, and `_land_edits` reads
  the same field — so the refusal and the write-back cannot disagree about which file is
  protected. Vectors, run 2026-09-29 on the edited tree: `python -m flash.patches --selftest`
  **63 → 71/71** (`patches selftest: 71/71 checks passed`; the 8 new ones are a `# 7c.
  ORACLE` section — refusal before resolution, a resolvable range refused by this rule
  alone, the sentence naming the oracle and the module to change, all four kinds, an
  unrelated symbol patch still applying while the oracle is named, a mixed set voided, the
  identical patch applying when no oracle is named, and the PROTOCOL pin);
  `python benchmarks/patch_landing_check.py --sweep` **48 → 53 checks, 24 → 27 mutants**
  (`R-3.2 clause 3, patch landing: 53/53 checks passed`, `patch-landing mutants: 27/27
  caught` in both lanes) — the 5 new checks are the command's own key, the key handed to
  the arm on the task, the refusal naming the oracle rather than the line count, and the
  control where no key is handed and the same patch applies; `python
  benchmarks/session_check.py --sweep` **48 → 49 checks, 16 → 17 mutants** (`R-7.15
  interactive session: 49/49 checks passed`, `session mutants: 17/17 caught` in both
  lanes). Witness logs: `benchmarks/results/patches_selftest_r715e_20260929.log`,
  `patch_landing_sweep_r715e_20260929.log`, `session_sweep_r715e_20260929.log`. The three
  mutants are the confusion this box demanded: `apply_patches` told no oracle, a refusal
  message about the lines instead of the oracle, and `_oracle_key` returning `""`. The
  whole-tree re-read on this tree then **printed the ledger it had been predicted into**:
  `checks 1345  oracle 20  §6 total 1365  mutants 157` with **37** OK lines and no BAD line,
  plus its own `matches SPEC §6 as written: 1345 + 20 = 1365 green, offline (+ 157 mutants)`,
  in **18 min 35 s** (`benchmarks/results/battery_reread_r715e_20260929.log`); it began on
  battery at 53% and was plugged into AC partway, which the §6 page says rather than
  smooths over, and `checkpoint_resume_check` printed **35/35** on that run legitimately
  because the governor had not yet clamped tournament width. Rows 8, 29 and 30 of that run
  are the moved vectors: `71/71`, `53/53 (+ 27 mutants)`, `49/49 (+ 17 mutants)`.
  **The live arm, re-run on the same two asks, is the honest part.** Turn 1 printed
  `oracle| PATCH REFUSED: t.py:format_money — t.py is the oracle this run scores against, …`
  and the session closed `turns=2 solved=0 written=0 seconds=23.3 last_rc=1`, writing
  nothing (`benchmarks/results/session_pty_r715e_20260929.log`, trace
  `20260929-203852-session-3973`). Both oracle-addressed attempts got the right sentence —
  that is the box closed — and the turn still lost, because four of the eight attempts
  invented a module instead (`main.py`, `money.format`, `cents_to_str.py`, `cents_to_str`),
  each refused with `… is not one of the project files (money.py, t.py)`. That list the
  model plainly did not have is **R-7.15f**: measured offline, the CLI's edit arm sends
  **2129 characters for a 96-character ask** and none of them is a line of the project's
  source — `enrich_task` returns edit tasks unchanged and only the task corpus generator
  ships the file text — while `task["files"]` already holds `money.py` and `t.py`, and the
  trace records `prompt_tokens` 503–676 per attempt. Docs moved with the mechanism:
  README's headline total is that print, its patch block quotes the oracle sentence and
  says it fires before the address, `docs/architecture.md` names the one function behind
  both gates, `docs/config.md` says the oracle is protected by name on every turn, and
  `docs/methodology.md` keeps R-7.15e as its seam case — the refusal was already
  happening, and only its sentence was wrong. The site's numbers are re-generated from
  this tree's witness rather than retyped: `dashboard_data.py`'s `WITNESS` moved to
  `battery_reread_r715e_20260929.log`, so `site/src/data/benchmarks.json` carries `checks
  1345 / oracle 20 / total 1365 / mutants 157` over 37 vectors with the three moved rows
  at 71, 53+27 and 49+17, and its keyboard transcript panel moved to
  `session_pty_r715e_20260929.log`, whose `exit 1` is read from the command's own
  `[session]` line — **0** host paths in the published JSON, and `npm run build` in
  `site/` green. Gates re-run on the edited tree: `portable_paths_check.py` **15/15 +
  7/7**, `documented_commands_check.py` **8/8 + 5/5** with the census printed as **26
  distinct commands in 200 citations across 15 documents** and **87 source paths**, and
  `python -m pyflakes flash/*.py
  benchmarks/*.py` **0 findings**.
- **R-7.15f: the chat is now shown the project it is asked to patch — and the demo turn it
  was bought for still lost, which is how R-7.15g was found.** Measured offline, with no
  model: `cmd_session`'s first message for a 96-character ask was **2129 characters** of
  request plus protocol and **not one line of source**, while `task["files"]` already held
  `money.py` and `t.py`; `flash run --edit --context <dir>` was the same shape, and
  `enrich_task` returned edit tasks unchanged on the argument that an edit task ships its
  own source — true only of the *stored* corpus tasks, which the generator composes with
  `describe(workspace)` in the prompt. That is why six of the eight attempts in the last
  live run were refusals for not knowing what the project is: four invented a module
  (`main.py`, `money.format`, `cents_to_str.py`, `cents_to_str`) and two reached for the
  oracle. One composer fixes both surfaces at once: `patches.project_prompt(ask, files,
  test)` (`flash/patches.py:917`) emits `The project is below, with the real text of every
  file.` + the listing + `Requested change: <ask>` + the oracle's text, `edit_prompt` is
  now only that plus `PROTOCOL`, and `loop.enrich_task`'s edit branch is the single attach
  point, guarded on that header so a task that already ships the block comes back
  byte-identical. Because `enrich_task` is reached only from `solve_routed`, `run --edit`
  and every `session` turn take the same line. Measured, same shape: **2129 → 2893
  characters** (project block 860, both files named, header once), `describe`'s cap holds —
  a 19 200-character tree composes **12 325 chars** — and live `prompt_tokens` moved from
  the blind run's **503–676** to **728–904**. R-3.2 clause 1's comparability is re-proved
  rather than re-claimed: `gen_edit_tasks.build()` calls the same function and rebuilding
  the corpus reproduces the committed `benchmarks/tasks/edit_tasks.jsonl` **byte for byte**
  (10 records equal; `main()`'s serializer emits the file's SHA-1 prefix `fc5b27b383f5`),
  so no stored prompt moved and the published `m7_heldout` rows still measure the prompt
  they measured. Vectors, run 2026-09-29 on the edited tree: `python -m flash.patches
  --selftest` **71 → 78/78** (`patches selftest: 78/78 checks passed`; 7 new, a
  `# 7d. PROJECT PROMPT` section — every key named as an address, the real body rather than
  an outline, the ask quoted verbatim under its own heading, the oracle fenced and labelled,
  no test heading invented when no test was named, the header being the marker
  `enrich_task` reads, and the over-cap path) and `python
  benchmarks/patch_landing_check.py --sweep` **53 → 61 checks, 27 → 28 mutants** (`R-3.2
  clause 3, patch landing: 61/61 checks passed`, `patch-landing mutants: 28/28 caught` in
  both lanes) — the new checks include that **both arms** get the same `# file:` block, that
  their two messages are identical up to the sentence naming the answer format, that
  composing is idempotent, and that the composed message exceeds the ask by more than the
  protocol's own tail; the new mutant returns an edit task with only its typed ask and
  defeats 6 checks. Witnesses: `benchmarks/results/patches_selftest_r715f_20260929.log`,
  `patch_landing_sweep_r715f_20260929.log`, `session_sweep_r715f_20260929.log`. **Live, same
  driver, same seed, same two asks** (`session_pty_r715f_20260929.log`, trace
  `20260929-224842-session-0e6b`): **all eight attempts addressed `money.py:cents_to_str`**,
  every turn line reads `refused=0 whole=0 outside=0` — six refusals became zero — and the
  turn still lost, `turns=2 solved=0 written=0 seconds=32.9 last_rc=1`, because all eight
  patch rows reported `GOT: '$1.5'` for a module the turn had already rewritten. Docs moved
  with it: README's chat section now states the 2129 → 2893 measurement and the patch block
  says what `--edit` puts in the message, `docs/architecture.md` names the composer and the
  single attach point, and `docs/methodology.md` keeps the seam rule this box tested.
- **R-7.15g: VERIFY graded the bytes from before the edit, so a correct answer lost — and
  the first green live chat turn on the author's own demo task.** The prompt fix worked and
  the turn kept losing, which moved the finding off the model and onto the oracle seam.
  `harness.score_files` writes the candidate workspace into a temp root and substitutes
  `<TMPDIR>` into the test, but the demo's `t.py` carries a **literal**
  `sys.path.insert(0, "/tmp/flash-chat-demo")` — the bootstrap anyone writes so `python t.py`
  runs from anywhere. That path resolves to the live tree, so it stayed ahead of the temp
  root on `sys.path`, `from money import cents_to_str` bound the **unpatched** module, and
  the verdict was computed from files the turn had replaced: eight `applied=1` patches,
  eight identical `GOT: '$1.5'`. No count in the report could have caught it — the patch
  layer, the verdict line and the trace all said the edit landed; only the scored copy
  disagreed. `harness._unshadow` now inserts the temp root **as its own line, carrying the
  bootstrap's indentation**, after any `sys.path.insert(0, <literal>)` whose path resolves to
  a directory holding one of the scored files — and it leaves an oracle alone when its
  bootstrap is `<TMPDIR>` or names a directory holding none of them, so no byte of the exam
  changes. **The first version of this fix was a silent no-op on exactly the shape it was
  bought for**, and it was found by attacking the implementation rather than by the checks
  that shipped with it: appending `; sys.path.insert(...)` to the bootstrap line works until
  that line ends in a comment, and a `#` swallows whatever follows. Measured both shapes —
  `(True, '')` uncommented against `(False, '… GOT: \'live\' …')` with one. So the appended
  form is itself a mutant now. Vectors, run 2026-09-29: `python -m flash.harness --selftest`
  **20 → 28/28** (`harness selftest: 28/28 checks passed`; the 8 new ones are the patched
  copy winning, the pre-edit bytes still failing with `GOT: 'live'` so this is precedence
  and not a softened verdict, `score_files` ranking the shadowed shape off the patched copy,
  a `<TMPDIR>` bootstrap and an unrelated-directory bootstrap both left byte-identical, the
  rewrite adding ONE precedence line and changing no other byte, the trailing-comment case,
  and the indented-block case) and `python benchmarks/session_check.py --sweep` **49 → 58
  checks, 17 → 20 mutants** (`R-7.15 interactive session: 58/58 checks passed`, `session
  mutants: 20/20 caught` in both lanes) — 4 new checks drive the real `cmd_session` over a
  tree whose oracle names its own directory, including that the refusal the model reads
  quotes the oracle's assert **verbatim**, so a precedence fix that rewrote the exam cannot
  pass while turns start going green; 2 new mutants, `VERIFY writes the patch and then grades
  the tree that was there before it` and `the precedence statement lands behind the oracle's
  own comment`. Witnesses: `benchmarks/results/harness_selftest_r715g_20260929.log`,
  `session_sweep_r715g_20260929.log`. **Live, and green for the first time**
  (`session_pty_r715g_20260929.log`, trace `20260929-231604-session-b07a`): turn 1
  `routed=small tier=big solved=True attempts=3 (13.5s) patches=1 refused=0` → `[R-3.2] wrote
  money.py (+4 -1 lines)`; turn 2 `tier=small solved=True attempts=1 (5.7s)` → `wrote
  money.py (+2 -0 lines)`; `[session] turns=2 solved=2 written=2 seconds=19.2 last_rc=0`; the
  child exited **rc 0** in 29.4 s; and the driver re-ran the oracle from outside the session
  against the bytes on disk and printed `rc=0 ORACLE GREEN`. The file it left is what the
  asserts describe — an `isinstance` guard raising `ValueError`, zero-padded cents, a minus
  sign in front of the dollar. The model's addresses were already right before this box
  closed; what changed is that the verdict finally scores the copy it claims to.
  **§6, re-read on this tree and printed:** `python benchmarks/battery_reread.py` →
  `checks 1377  oracle 20  §6 total 1397  mutants 161`, 37 OK rows, no BAD row, **exit 0**, in
  **20 min 43 s** on AC at 80% (`benchmarks/results/battery_reread_r715fg_20260929.log`), with
  its own `matches SPEC §6 as written: 1377 + 20 = 1397 green, offline (+ 161 mutants)`. The
  run before it exited 1 having printed every vector green, because a hand-summed `CLAIM` of
  1376 met 37 rows that add to 1377 — `SPEC §6's checks claim is 1376, the tree prints 1377` —
  and it is kept beside the confirming run as
  `benchmarks/results/battery_reread_r715fg_claim1376_20260929.log`. The prediction moved; no
  row did. Docs, the site and PLAN moved after that run, so the ordering is stated rather than
  blurred, and the two gates that read those pages re-ran on the final surfaces at the numbers
  they printed inside it — `portable_paths_check` **15/15 + 7/7 mutants**,
  `documented_commands_check` **8/8 + 5/5 mutants**, census included
  (`benchmarks/results/doc_gates_r715fg_20260930.log`). `dashboard_data.WITNESS` and
  `export_site_data.SESSION_PTY` now point at this pass's prints, the three
  `site/src/data/*.json` are regenerated from them, `npm run build` is green, and the built
  page was read back through the browser rather than assumed: it renders 1,397 claims / 161
  mutation gates / 37 vectors, the proof panel's keyboard tab is the green witness ending
  `rc=0 ORACLE GREEN`, and install rows C and D name 1,377 + 20 = 1,397 as the current total.
- **Model residency, re-measured rather than remembered, because the number quoted
  before this pass did not reproduce:** three fresh processes, 51% on battery,
  first load **2.01 / 2.10 / 2.05 s** and second load after the free
  **0.65 / 0.74 / 0.66 s** (`benchmarks/results/session_model_residency_20260929.log`).
  `load_model` is uncached by design and the free happens at **five**
  `del model, tok` + `mx.clear_cache()` sites inside `solve_routed`, which is the
  number the SPEC paragraph now states after the first draft said four.
- **The §6 ledger moved to 37 lines and the whole surface moved with it, from the
  print rather than from arithmetic.** `python benchmarks/battery_reread.py` on this
  tree printed `checks 1294  oracle 20  §6 total 1314  mutants 147` with **37** OK
  lines and no BAD line, and its own agreement line `matches SPEC §6 as written:
  1294 + 20 = 1314 green, offline (+ 147 mutants)`, in **16 min 44 s** on AC with the
  charge climbing from 63% to 80%
  (`benchmarks/results/battery_reread_r715_20260929.log`) — against a `CLAIM` of
  `checks 1294  oracle 20  mutants 147` derived from `BATTERY` before the run started.
  The session line is the **30th** of 37, the ledger follows §6's own table order, and
  the new vector is the **14th** of the 147 gates. Docs moved together: README gained
  the fifth first-run step and a verbatim copy of the three-turn transcript, `docs/config.md`
  the two flags that are only the session's, `docs/architecture.md` the sentence that
  `run` and `session` are the same loop, `CONTRIBUTING.md` the note that the vector is a
  §6 line, and `flash/__init__.py`/`pyproject.toml`/`MANIFEST.in` the counts. The
  command census the docs gate prints moved to **26 distinct commands in 197 citations
  across 15 documents** (86 source paths), with `documented_commands_check.py` **8/8 +
  5/5** and `portable_paths_check.py` **15/15 + 7/7** on the edited tree; the landing
  page's proof panel is now four tabs, its session tab reading the live witness
  through `benchmarks/export_site_data.py`, and panel 3.2's vector count is computed
  from the print instead of spelled in prose. `python -m pyflakes flash/*.py
  benchmarks/*.py` **0 findings**.
- **R-3.2 clause 3: a verified patch now reaches the disk, and the run always says
  which state the tree is in.** `--edit` scored every attempt in memory and printed
  `solved=True` while leaving the project byte-identical — found live on
  2026-09-28 on a scratch tree (three attempts, a green oracle, a trace session, and
  the one module still holding its original four lines). `flash.patches.land` writes
  the verified workspace back under `run --edit --context <dir> --apply`: only files
  whose bytes differ, never a deletion, an address escaping `--context` or naming a
  file the oracle never scored is refused, the whole set is validated before the
  first byte so a refusal cannot leave a half-patched tree, and the counts it returns
  are lines rather than diff hunks. Writing stays opt-in; the sentence does not —
  without `--apply` the run prints `NOT APPLIED`, with it each landed file prints
  `wrote <file> (+A -B lines)`. The oracle is protected by name, since
  `workspace_from_dir` lists every Python file in `--context` including the test, so
  a patch set that fixed a failure by weakening an assertion is REFUSED rather than
  written. Offline: `python benchmarks/patch_landing_check.py --sweep` → **40/40
  checks, 22/22 mutants caught** in both lanes
  (`benchmarks/results/patch_landing_sweep_20260929.log`), added to the §6 ledger as a
  new battery line — the ledger now has **36 lines**, and the vector is the 29th. The
  full re-read on that tree printed `checks 1250  oracle 20  §6 total 1270
  mutants 133` at **36/36** with no BAD line, in **16 min 27 s** on battery at 74%
  (`benchmarks/results/battery_reread_r32c3_20260929.log`), against a `CLAIM` set by
  arithmetic before the run. Then the flag was used for real, on a tree that is not
  this repo: `flash run "…zero-pad the cents…" --test t.py --context . --edit
  --allow-big always --apply` printed `solved=True attempts=4 (14.8s)` and
  `[R-3.2] wrote money.py (+5 -4 lines)`, and re-running the same oracle against the
  bytes now on that disk answers green.
- **R-7.5's clause 2 re-measured on both install shapes one tarball supports, and the
  `dev` extra fixed in the same pass so CI cannot go red on main.**
  `python benchmarks/r75_sdist_battery_check.py` now builds the sdist once and runs the
  whole §6 battery inside the download **twice**: `pip install <sdist>` → **33 of 35**
  lines, rc 1, `checks 1119  oracle 20  §6 total 1139  mutants 85`, 13 min 22 s, with
  exactly two refusals named; `pip install '<sdist>[ts]'` → **35/35**, rc 0,
  `checks 1210  oracle 20  §6 total 1230  mutants 111`, 13 min 14 s, plus the battery's
  own `matches SPEC §6 as written`
  (`benchmarks/results/r75_sdist_battery_shapes_20260928.log`). The assertions are
  deliberately asymmetric — shape A fails if a TypeScript vector *passes* there, or if
  the plain install agrees with §6 at all, because `flash.lang_ts.available()` is a
  designed refusal and a grammar-less venv printing 1,210 would mean the check stopped
  checking. `pyproject.toml`'s `dev` extra now self-references `flash-coder[ts]`
  (proven with `pip install --dry-run -e .[dev]`, which lists
  `tree-sitter-0.26.0` and `tree-sitter-typescript-0.23.2`): two of the battery's lines
  are TS vectors, both CI battery jobs run the whole ledger (the driver reads its line
  count from `battery_reread`, so a new vector cannot leave CI asking for the old
  number), and an extra that cannot reach them
  turns the pipeline red on the merge commit rather than at the change.
- **A machine-state limit that no tree can pass round, written down instead of
  resized.** The first shape-B pass printed **34/35** and failed only
  `benchmarks/checkpoint_resume_check.py`, because `flash/power.py` forces
  `tournament_width = 1` below 25% charge *even on AC* while that vector's tournament
  arm requires width ≥ 2. The failing witness is kept
  (`…_shapes_battgate_20260928.log`), the gate was not touched, the machine was put on
  AC above 30% and the driver re-run. `README.md`, `CONTRIBUTING.md` and
  `docs/methodology.md` now say to charge before a full battery.
- **R-7.6, R-7.7 and R-7.8 closed together, because the third one is what proved
  the first two.** `flash --version`, `flash doctor` and `flash selftest --all`
  exist; `flash/decide.py`'s `import mlx.core` moved inside `decide()`; and every
  `flash …` line printed in a tracked document now has to parse against
  `flash.cli.build_parser()`. Two new §6 vectors carry the measurements:
  `python benchmarks/backend_free_check.py` → **30/30 + 5/5 mutants** and
  `python benchmarks/documented_commands_check.py` → **7/7 + 4/4 mutants**, and the
  re-read that followed them is the printed line
  `checks 1104  oracle 20  §6 total 1124  mutants 78` with **33** OK lines
  (`benchmarks/results/battery_reread_r76_20260927.log`). Both files have grown since
  that print, in the bullets below and in R-7.9/R-7.10, and the `[0.0.1]` notes'
  headline carries the later totals. What each command
  promises is gated, not narrated: `doctor`'s exit code follows its own page (a
  synthetic broken install in a temp tree must print `no` on exactly 5 of 9 lines
  and rc 1; a complete one zero `no` and rc 0), `selftest --all` refuses with rc 2
  naming `benchmarks/battery_reread.py` rather than totalling checks that never
  ran, `--version` reads the version off the package object, and the whole package
  is swept submodule-by-submodule under an import blocker — **`SWEEP 26/26`** —
  because the two names that used to fail are not the same claim as "every module".
  `flash doctor` on this machine prints nine lines across six sections and says
  `Both halves this project claims to have are present here.`
- `--backend-free` is now a measured property instead of a flag. The battery writes
  a `sitecustomize.py` blocker, puts it FIRST on the `PYTHONPATH` its children
  inherit, and refuses to print a total until a child's `import mlx.core` has
  raised the shim's own sentence; a sibling gate requires it to leave `numpy`
  alone, because a blocker that broke everything would "prove" the claim by making
  the battery unrunnable.
- `benchmarks/documented_commands_check.py` reads fenced blocks, inline backticks,
  the workflows' `run:` lines and the packaging files' comments: **25 commands across
  150 citations in 15 documents, plus 72 source paths a reader is told to open**, all
  of which resolve. (The four figures move with the prose; the gate asserts a floor and
  prints what it counted.)
  Commands are resolved by `parse_args`, never by dispatch — a documented
  `flash run` would create a worktree — and the collector is not trusted: planting a
  command nobody wrote must be rejected, and deleting `selftest`/`doctor` from the
  live parser must fail a gate whose text names the citing `file.md:line`.
- **R-7.4 path portability**, gated by `benchmarks/portable_paths_check.py`
  (14 checks + 7 mutants, launched from a foreign working directory). A task
  corpus's oracle bootstrap now writes `<REPO>` instead of the author's checkout,
  expanded in the one function every execution seam already passes through.
  Before the fix, 72 context tasks in the committed suites failed with
  `ModuleNotFoundError` on any other machine — before the candidate's first line,
  so the suite printed verdicts while scoring nothing. §6 re-read from the tree
  afterwards on a quiet box: `checks 1066  oracle 20  §6 total 1086  mutants 69`,
  all **31** lines OK (`benchmarks/results/battery_reread_r74_20260927.log`), and
  the vector itself re-run **after** that witness landed — 14/14 + 7/7 with the new
  160th record file on disk.
- The same scan, widened. `HOST_PATHS` now carries a third marker — the default
  Homebrew install prefix on Apple Silicon — which is not a leaked home directory
  but is still a path only some Macs have. It found exactly one line: the
  README's second install command, which spelled an interpreter path under that
  prefix and so told every reader whose prefix is the other one to run a command
  that cannot exist on their machine. Re-run on the widened scan: **14/14 + 7/7**,
  residue **395 → 403** across **31 → 33** record files (the delta is eight
  occurrences in two failed run traces that had matched nothing before), and
  **407 / 34** once this pass's own reproduction witness was stored — which is the
  published floor's number, with every step between the two accounted for in
  `docs/portability.md`.
- `docs/portability.md`: what the token contract is, what was scrubbed, which
  witness files are deliberately NOT rewritten and why, and the published residue
  count for the exclusion (`RECORD_RESIDUE`, asserted `≤` what the tree carries).
- Two open boxes this pass created out of measurements, not intentions. **R-7.7**:
  with `mlx` blocked, **24 of the 26** modules import and `flash.decide` /
  `flash.route` are the two that do not — unnoticed because `flash.cli` imports both
  lazily. **R-7.8**: gate every `flash …` command printed in a doc against
  `flash.cli.build_parser()`, which is how the setup block below was found.
- **A landing page under `site/`, and the pipeline that keeps it honest.** Vite +
  React + Tailwind v4, with the hero scene being this repo's own AST call graph:
  `benchmarks/export_site_data.py` runs `flash.graph.build(ROOT)`, keeps the 150
  most-connected symbols under `flash/`, and joins them on `calls` and `imports`
  edges only — `reads` edges from module-level constants dominate the graph and a
  slice that keeps them is a 2,552-edge hairball rather than a picture. Clicking a
  node computes a caller-direction blast radius to depth 2 in the browser, which is
  the same question `flash graph SYM` answers. Every figure on the page comes from
  `site/src/data/benchmarks.json`, and every panel names the command that prints it;
  the 33 commands were cross-checked against `battery_reread.BATTERY` itself, which
  is how a `build_ms` field that had parsed the edge count (10,058 instead of 603 ms)
  was caught before it shipped. `python -m flash.<mod> --selftest` timings are the
  median of **n=3** fresh runs with min and max published beside them.
  The page's own first defect was a real one and is worth the stating: three.js
  throws when no WebGL context can be made, an uncaught error in a child unmounts
  the whole React tree, and the page rendered **blank** — 33 KB of DOM with an empty
  `#root`. Headless Chrome's own console line was the witness
  (`Uncaught Error: THREE.WebGLRenderer: Error creating WebGL context.`). The hero
  now probes for a context, a software renderer and `prefers-reduced-motion`, and
  falls back to an interactive SVG of the same 150 symbols — so a reader on a remote
  desktop gets the graph, the click-to-blast-radius readout and the copy, and never
  downloads the 914 KB three.js chunk. With the probe in place the rendered DOM is
  173 KB with all eight sections present.
- **R-7.5's second clause is now a run instead of a sentence, and it is the
  download that answers it.** `benchmarks/r75_sdist_battery_check.py` builds the
  sdist, unpacks it under a temp directory, installs the tarball into a throwaway
  venv, and prints its own provenance before it is allowed to report a total —
  `site-packages`, `import flash` from inside it resolves to the unpacked sdist,
  the installed `flash --version` prints `flash 0.0.1`, and the witness is refused
  if the checkout's path appears anywhere in it. Then it runs the entire §6 battery **inside the download**:
  **33/33** lines, `checks 1119  oracle 20  §6 total 1139  mutants 85`, **13 min 2 s**
  against the checkout's own 15 min 9 s for the same tree state
  (`benchmarks/results/r75_sdist_battery_20260928.log`, 0 host paths, `RECORD_RESIDUE`
  floor still **411**, record files **171 → 172**). The same driver records the other
  shape on the way out: `flash selftest --all` with no tree present exits **2** naming
  the `site-packages/benchmarks/battery_reread.py` it wanted.
  The corrected docs were then re-read from this checkout, to the identical print —
  **33/33**, `checks 1119  oracle 20  §6 total 1139  mutants 85`, in **15 min 12 s**
  (`benchmarks/results/battery_reread_r75b_20260928.log`, again 0 host paths, so the
  floor holds at **411 over 36** and record files move to **173**). That one is a shell
  redirect rather than a driver, so it credits itself nothing about which tree it read
  beyond what the SPEC entry says out loud: this checkout, launched from it.
  `docs/methodology.md` rule 5 and `SPEC.md` R-7.5 carry the split this forced — the
  literal reading of "against the installed package, no source tree" cannot produce a
  green battery, because 11 of the battery's vectors index the tree they stand in.
- **R-7.5's first clause was measured the same way, and it had the same disease.**
  `benchmarks/r75_fresh_install_check.py` is credited with clause 1 — a sdist installed
  into a throwaway venv answering as a stranger's terminal would. Rewritten: it now
  builds, installs, and then asks **the venv's own `flash` console script** from a
  working directory that contains no Python at all, and it prints which `flash` each
  child resolved before it is allowed to assert anything about the answer. **9/9 shapes
  green** (`benchmarks/results/r75_clause1_20260928.log`, 0 host paths, 0 substitutions
  needed). The two shapes a downloader can actually make:
  - *installed from the tarball* — `flash --version` → `flash 0.0.1` rc 0; `flash doctor`
    → rc **1**, with `(installed copy)` on its first line, `verification surface beside
    the package: benchmarks/ ABSENT`, and `the offline battery CANNOT run from this
    install` naming its remedy; `flash selftest --all` → rc **2** naming the
    `site-packages/benchmarks/battery_reread.py` it wanted.
  - *cloned, installed editable* — `import flash` resolves to the clone and not to this
    checkout, `flash doctor` → rc 0 with the battery line on `yes`, and
    `flash selftest --all --quick harness lsp power` runs **3/3** vectors from the clone
    and prints its own `run of 3/33 lines: totals are partial` warning.
  The gate that makes this file worth having is the sixth one: run `python -m flash.cli`
  **from inside the unpacked sdist** with the same interpreter, and `flash.__file__` is
  that tree and `doctor` says `(editable checkout)` — `python -m` puts the cwd on
  `sys.path[0]`. The old driver did exactly that, with no `cwd`, so its children answered
  from this checkout while the print claimed the install. That is now a gate rather than a
  footnote, because the only way to keep a provenance bug from coming back is to make the
  wrong shape fail something.
  The battery was then re-read from the tree holding that rewrite, to the same print
  again — **33/33**, `checks 1119  oracle 20  §6 total 1139  mutants 85`, in **15 min 7 s**
  (`benchmarks/results/battery_reread_r75c_20260928.log`, 0 host paths, floor **411 over
  36**, record files **175**). Three identical totals now exist: the checkout before the
  correction, the download, and the checkout after it. `docs/portability.md` says why a
  fourth copy of the same numbers is worth a record file.
- **R-7.12: the first cross-tool run this machine has made.** `python
  benchmarks/market_compare.py --arms oneshot,aider,flash --tasks
  benchmarks/tasks/m7_heldout_tasks.jsonl` answers the same 8 held-out tasks from the
  same `Qwen2.5-Coder-7B-Instruct-4bit` weights and grades every answer with
  `flash.harness.run_test` — the oracle that printed the published 20 — behind three
  gates: the grader self-checks **8/8** against its own stored references, the server
  must name the model the driver thinks it is talking to, and tokens are counted by a
  proxy reading the server's own `usage` rather than re-tokenised by the thing being
  measured. What printed
  (`benchmarks/results/market_compare_20260928.log`, 0 host paths): single-shot with no
  agent loop **5/8**, 6.9 s/task, 8 requests, **2,021** tokens; aider 0.86.2 **6/8**,
  14.9 s/task, 16 requests, **13,084**; `flash run-suite` **8/8**, 10.6 s/task, 11
  requests, **3,470** — from trace session `20260928-081056-run-suite-b660`, with
  `--allow-big never` so the loop never reaches its second, larger model and the table
  stays a comparison of agents rather than of weights. Two instrument bugs died in this
  pass: `--tasks` was accepted and ignored (the HTTP arms called `m0_bakeoff.load_tasks()`,
  which always reads the frozen m0 suite — caught because the "held-out" run reproduced
  m0's exact totals), and the flash arm parsed PASS/FAIL out of `run-suite`'s stdout and
  printed **0/8** while that same run's trace recorded **8/8**. Nothing in §6 moved: the
  driver needs weights, so it is not an offline vector — but the first re-read after this
  pass came back **31/33**, and both failures were this pass's fault rather than the
  machine's. `benchmarks/checkpoint_resume_check.py` printed nothing because the power
  governor offered width 1 with 4.8 GB free against the 6.9 GB its tournament arm wants
  (another project's 14.8 GB model was resident for the whole run), and
  `benchmarks/portable_paths_check.py` fell to **13/15** because `market_compare.py` — the
  file written to keep host paths out of a published witness — carried two host prefixes as
  literals in its own marker list. The exemption was not widened and nothing was reworded:
  the driver imports `portable_paths_check.HOST_PATHS` now and spells no prefix itself. The
  re-read of that corrected tree printed
  `checks 1119  oracle 20  §6 total 1139  mutants 85` with **33** OK lines in **18 min 41 s**
  (`benchmarks/results/battery_reread_r712_20260928.log`, 0 host paths, floor **411 over
  36**, record files **179**) — the same totals, on a busier box, quoted with the condition
  that made it slower.
- **R-7.13 (OPEN): Cursor and Copilot are not in that table, and cannot be here.**
  `cursor-agent` and the standalone Copilot CLI are not installed, `gh extension list`
  is empty, `Cursor.app` ships only the IDE launchers, and the `github.copilot-chat`
  VS Code extension has no headless entry point; both products generate in their own
  cloud under a paid plan, so a row for either would swap the model, the machine and the
  token accounting all at once and measure which vendor has the bigger model. No
  multiplier, no rival latency and no dollar-per-month figure for them is published
  anywhere in this repo.
- **R-1.4 (PARTIAL): the graph learned a second language, and the choice came out of
  a file count because the ledger had no opinion.** The box said "choose from ledger
  evidence, not taste", so both records were read: the ledger's 1148 classified
  outcome rows contain **0** asks for SQL and **0** for TypeScript — the ledger
  cannot pick between the two languages §28.1 named, and that null is booked as the
  finding rather than quietly promoted — while the tree this tool indexes holds **21
  TypeScript-family files** (14 `.tsx`, 7 `.ts`, the number
  `flash.lang_ts.ts_files('.')` prints) against **0** `.sql` and **0** `.db`. An
  earlier draft of this bullet said 23 by sweeping in the two `.css` files, one of
  which is `site/dist`'s generated bundle; a stylesheet is not a parse target here
  and build output is not an edit target, so the count quoted is the tool's own.
  TypeScript is real front-end work the graph was blind to;
  SQL is not present at all. What shipped is `flash/lang_ts.py`: a tree-sitter
  TypeScript/TSX index that emits **`flash.graph`'s own** `Node`/`Edge`/`Unresolved`,
  so `blast()`, `Radius.summary()` and `--json` answer about `.tsx` with no second
  query engine and no second latency. `--lang py,ts` is opt-in and the default stays
  `python`, so every figure published above still describes the index that built it.
  The grammar is a fourth optional extra (`pip install .[ts]`) and its absence is
  **one printed refusal, not a guessed parse** — a hand-rolled scanner would be a
  regex pretending to be a parser, which is the failure `flash/patches.py` exists to
  prevent. Vector: `python benchmarks/ts_perception_check.py --sweep` → **47 checks +
  13 mutants**, green in this process and with one fresh process per mutant, and
  `python -m flash.graph --selftest` still **44/44** on the Python default. On the
  real tree the TS pass costs **44–55 ms for 21 files** inside a mixed cold build of
  1.03–1.09 s and adds **103 nodes / 240 edges** over the Python 4446/24574, with
  **151 unplaceable
  uses each carrying its own sentence** (npm package, non-exported name, `export *`,
  a named re-export one hop too far, unknown name, unparseable file, missing grammar)
  so the blind-spot count stays a floor and never a census.
  **The box is not checked**, because its vector is "R-1.1..1.2 equivalents pass on a
  fixture tree in that language" and three equivalents are Python-only still: the
  loop's PERCEIVE hint ranks symbols with `flash.lsp.symbols_involved` and
  `graph.scope_graph()`, so a failing `.tsx` test gets no ranked hint; `live_upgrade`
  asks jedi and there is no `tsserver` bound; `flash/patches.py` validates a
  replacement with `ast.parse` and takes spans from Python `definitions()`, so
  `# edit: App.tsx :: Widget` is refused as an unknown symbol while
  `# edit: App.tsx :: L11-L15` applies. Three defects the vector caught on the way,
  each now a mutant: `export *` was never detected (tree-sitter makes the `*` an
  *anonymous* child, so a "no named children" test cannot fire), `import * as X`
  bound no name (`X` is a plain identifier child, not a `name` field) so every dotted
  call through a namespace vanished, and tree-sitter node wrappers are made fresh on
  every access, which made `a is b` the wrong identity test and turned a
  declaration's own name into an edge from a symbol to itself.
  The §6 re-read that followed printed `checks 1166  oracle 20  §6 total 1186
  mutants 98` with **34** OK lines, no BAD line, in **16 min 49 s** (witness
  `benchmarks/results/battery_reread_r14_20260928.log`) — the first pass in five that
  moves the line count, because a second language is a new thing to verify rather
  than another gate inside an existing vector. Two claims written earlier the same
  day were corrected by re-reading the literals instead of trusting the note: the
  file census was 23 and is 21, and the vector's absence coverage was described as 13
  checks and is 3 (`docs/config.md`), while `benchmarks/r75_sdist_battery_check.py`
  turned out to require exactly 33 OK lines, which would have made the 34th line a
  failure for the wrong reason — it now reads its expectation from
  `battery_reread.BATTERY`.
- **R-1.4's third gap closed the same day: the patch arm can address a TypeScript
  symbol.** The defect was printed, not inferred — `# edit: site/src/components/Hero.tsx
  :: Hero` came back `no symbol 'Hero' … (it defines: nothing)` while
  `# edit: … :: L16-L18` applied, because `flash/patches.py` validated every
  replacement with `ast.parse` and took its spans from Python `definitions()`. It now
  dispatches on the file the address names: `.ts`/`.tsx` go to `flash.lang_ts`, every
  other name — including the empty path `flash.graph` has always passed — stays on
  `ast`, which is what keeps the published Python figures the same numbers from the
  same code path (`--selftest` **46/46**, `--suite` **60/60** re-run on this tree).
  A TypeScript span is read off the same `_declarations` walk that produces the
  graph's `Node`, so "what this patch replaces" and "what breaks if this changes"
  cannot drift into two ranges, and a replacement has to keep the symbol's name *and*
  the `export` keyword its owned span begins with — the TypeScript shape of Python's
  decorator rule, and the failure mode is silent: drop it and the edited file still
  looks fine while every importer breaks. A replacement that does not parse is refused
  with the grammar's own line and marker instead of Python's `invalid syntax`.
  Measured on the front end in this repo: `Hero` resolves to **L31-L180** of a
  181-line file, re-emitting those bytes applies with **0** lines changed outside the
  span, dropping the keyword refuses as `no longer exports Hero (as Hero L31-L180)`,
  and a broken body refuses at `line 32: error 'return <div>'`. Vector:
  `python benchmarks/ts_patch_check.py --sweep` → **44 checks + 13 mutants**, green in
  this process and with one fresh process per mutant; two of the checks drive
  `loop._solve_edits` with `_generate` and `diagnose_files` stubbed, because that
  arm's per-file `ast` pass is where a clean TypeScript edit used to come back a
  confident false `STATIC` — costing the retry a refusal exists to save. What it does
  **not** buy: nothing here verifies a TypeScript edit (`diagnose_files` still has no
  `node`/`vitest` runner behind it, so a `.tsx` patch that parses is accepted on the
  strength of a parse), and the whole-file control arm is still Python — both booked
  as open in SPEC R-1.4 rather than described as shipped. The §6 re-read that follows
  is the printed line `checks 1210  oracle 20  §6 total 1230  mutants 111` with **35**
  OK lines, no BAD line, in **15 min 18 s** (witness
  `benchmarks/results/battery_reread_r14b_20260928.log`, wall clock from the file's own
  timestamps).
- **R-7.14: the secrets question got a re-runnable answer instead of a memory.**
  The ask was "check every line, because I don't want to get breached later", and a
  grep today answers it in the wrong two directions: a credential scrubbed from the
  tree still ships inside `git log -p`, and a file written since the last commit is
  what a push actually sends. `benchmarks/publish_secret_scan.py` now sweeps **359
  files in the working tree (4 of them not yet tracked) and 713 blobs ever
  committed** against **7 credential shapes plus key-file filenames** and printed
  **2 matches, each with the reason it is not a credential, 0 unlisted** — the
  `proxy-no-auth` string aider is handed for the local token-counting server, and a
  base64 stretch inside an `sha512-…==` integrity hash in `site/package-lock.json`.
  The 21 NUL-padded blobs (the committed `.npz` arrays) were **swept with their
  padding removed, not skipped**, because padding is where a planted secret would
  sit; identity is reported rather than gated — noreply email **7**, personal email
  **0**, host paths **1117**. A clean sweep by patterns that cannot match is the
  worthless kind of green, so the file also plants a synthetic secret through every
  family and fails if any passes it (**8/8 bite**), which was proved end-to-end by
  planting a fake `ghp_` token and a `deploy.pem` filename and watching the sweep
  name both and exit 1. It caught its own author twice on the day it was written:
  the scanner's PEM test sample is now split rather than allowlisted, because a file
  that exempts itself is the one exemption that empties a secret gate; and its own
  **witness** tripped it, since the printed reason quoted the `KEY="value"` shape it
  was excusing — the reasons are worded without that shape, and a second run over the
  log it had just written is clean. An existing gate then caught the new one:
  `benchmarks/portable_paths_check.py` fell to **13/15** because this file spelled a
  host prefix in its own source, the exact rule R-7.5 booked, and the pattern is now
  assembled from `HOST_PATHS` imported from that gate, which is back to 15/15 with
  7/7 mutants defeated. Nothing was deleted from the repo: the author's four
  decisions on this page's contents (SPEC/TODO at top level, all 180 records, the `.npz`
  caches in git, host paths as documented policy) stand, so the deliverable is proof
  rather than cleanup. Not a §6 line — its history arm needs `.git`, so an unpacked
  sdist cannot print it; it is cited, not counted, and the totals above are unchanged
  by it. Witness `benchmarks/results/publish_secret_scan_20260928.log`.

### Corrected — claims this file made that the tree does not support
- **`docs/methodology.md` and `README.md` said "the download has not been re-run
  since" the battery grew its two TypeScript lines.** It has now been re-run, twice, on
  both install shapes, and the sentence is replaced by the two prints it was waiting
  for. Same page, same pass, one further correction of the same class: it sold the
  single-shape print of `checks 1119 … 33/33` as *the* download result without saying
  that the checkout printed `1210 … 35/35` at that moment, which left a reader unable
  to tell a packaging gap from a regression. The shape is now named on every page that
  quotes a download total.
- **`26 of the 26 submodules import without a backend` was the current answer in four
  documents, and the live sweep prints `SWEEP 27/27`.** Nothing had regressed: the
  denominator of that fraction is however many modules `flash/` has, and
  `flash/lang_ts.py` joined the package with R-1.4. `README.md`, `CONTRIBUTING.md`,
  `docs/models.md` and `SPEC.md` R-7.7 now print 27 of 27 with the reason, and the
  `backend_free_check.py` gate label no longer carries a hard-coded count in its own
  description — it asserts numerator == denominator with a floor of 26 and prints the
  fraction, which is the claim. (Re-ran green on the edited tree: **42/42 + 10/10
  mutants**; `portable_paths_check.py` **15/15 + 7/7**; `documented_commands_check.py`
  **8/8 + 5/5**, whose collector now counts **25 commands across 184 citations in 15
  documents** — 150 was that run's number, and this pass's prose moved it.)
- **`docs/portability.md` and `docs/privacy.md` both said "the 182 record files".** The
  gate's live scan counts **188**, and nine files have joined since the page was
  written: two §6 re-reads of the second language, the secrets sweep's table, the two
  install-shape witnesses and four `flash run` traces. Every one of the nine carries
  **zero** host-path occurrences, so `RECORD_RESIDUE`'s floor holds at exactly **411
  over 36** — which is the shape a floor should have: the file count is the tree
  working, the occurrence count is the leak.
- **Four documents said no competitor had ever been run on this machine.** `SPEC.md`'s
  R-7.11 clause 2, `docs/methodology.md`, `site/README.md` and the site's own Numbers and
  Honesty panels each rested that refusal on "no competitor has been run here", and one
  of them was the heading **"Panel 3.5 does not exist"**. R-7.12 ran one, so the sentence
  is false in its reason even while the rule it protected stands: the rule is *no bar for
  a tool that was not run*, and aider's row exists precisely because it was. Panel 3.5 is
  now a measured table parsed from the witness by `dashboard_data.py`, the amber box is
  headed **What this page still refuses to print**, and the two products it still refuses
  are named with the reason they cannot be measured here (R-7.13) instead of a reason that
  no longer holds. The rule did not move; the sentence about the tree did.
- **`flash doctor` does not exit 0 against an installed copy, and this file, `SPEC.md`,
  `TODO.md` and `CONTRIBUTING.md` all said it did.** What is true, measured by the
  rewritten driver: against a tarball install it exits **1** and says why — the
  verification surface is not beside the package — and against a cloned, editable
  install it exits **0**. The rc 0 belongs to the shape a contributor gets and the rc 1
  to the shape a downloader gets; the sentence dropped the difference, and the sentence
  was believed because the driver that should have caught it was interrogating the
  checkout rather than the install. `doctor`'s behaviour is correct and unchanged; four
  documents moved.
- This file's own `### Added` section listed `flash doctor`, `flash selftest --all`
  and `flash --version` as shipped. **None of the three existed** when that was
  written — `python -m flash.cli` rejected `doctor` and `selftest` as invalid
  subcommands, measured by parsing 23 documented subcommands against the real parser
  (21 resolve). They are shipped now, and the bullet above is the measurement that
  says so; what this correction leaves standing is the *rule*, which is that a claim
  in this file moves only when a run prints it. The "21 of the 23" count is itself
  superseded by R-7.8's collector, which reads the documents instead of a hand-kept
  list: **25 commands, all resolving.**
- `## [0.1.0] - 2026-09-27` dated a release that has not happened. No tag exists
  and no remote is configured, so the header now says so outright. **(What moved
  since:** the section is now `## [0.0.1] - tagged 2026-09-28, still not published` —
  an annotated tag exists on this tree, and the remote that would make it downloadable
  does not. The date that made the original claim false was the fabrication, not the
  version.)
- Two documents cited a different module count for the same fact: `pyproject.toml`
  and `docs/models.md` said 24 of the 26 import without MLX, the CI comment said 25.
  Measured 24, so `ci.yml` is the one that moved.
- **That fix went stale and nothing noticed it.** R-7.7 made `flash.decide` and
  `flash.route` import with the backend blocked, so the measured figure became
  **26 of the 26**. Every document that quotes it moved — `README.md`, `CONTRIBUTING.md`,
  `docs/models.md`, `docs/config.md`, SPEC R-7.7 — except the CI header, which still
  described those two modules as the ones that cannot import, while the very step it
  annotates runs the file that measures otherwise. The fix is structural rather than a
  retype: the header no longer carries a count at all. It names
  `benchmarks/backend_free_check.py`, which prints its own (`SWEEP 26/26`), so there is
  nothing left in a file no vector reads to go stale — the same move that made the
  landing page's numbers generated instead of typed. The bullet above stays as dated
  history rather than being rewritten. What is still true is the shape of the hole: a
  workflow comment is prose, and no gate reads it, so a hand-kept number there was only
  ever as current as whoever last looked. The one artifact aimed squarely at strangers —
  the file describing what CI proves — held the least-checked claim in the repo.
- The same rot had reached this file's own `### Packaging` bullet, in the section a
  reader reaches first: it still said the clean-clone install was an open box and still
  offered **2 of the 26** modules failing without MLX as its measured half. Both moved
  after it was written — R-7.7 closed on 2026-09-27 and made the import answer
  **26 of 26**, R-7.5 closed on 2026-09-28 and made the install answer a run — and the
  bullet stayed as if nothing had happened since. Rewritten in place, with both old
  figures kept and labelled as the pre-fix measurements they are. A file that admits
  staleness only in its `### Corrected` section will always be behind its own
  `### Added` list.
- "`pip install flash-coder` works on Linux and Windows" became a gated claim: no
  clean-clone install has been run (SPEC R-7.5), and `pyproject.toml`'s comment that
  cited `benchmarks/backend_free_check.py` as its verifier now says that file is
  unwritten. **That last clause moved again this pass:** the file exists and is a §6
  line, so the comment cites it as the gate it is; the clean-clone run is still
  R-7.5's open box and the install claim is still gated, not measured.
- **This project published invented numbers, and they are gone.** Commit `54a2117`
  added four dashboard PNGs to `benchmarks/results/benchmarks_20261028/` — a
  competitor latency table, a cost table and a "viral summary" — under a commit
  message calling them benchmarks. They were not benchmarks. Every one of those
  figures was typed by hand: no run produced a competitor's millisecond, and the
  cost table multiplied a price this repo has never measured. Reverted as `69a4d2d`,
  which deletes all four files. What makes this a correction rather than a revert is
  the mechanism, because a rule that only lives in a sentence will be broken again:
  the site's numbers are now generated, so the failure is structurally unavailable.
  `benchmarks/dashboard_data.py` measures, `benchmarks/export_site_data.py` converts,
  and `site/src/data/*.json` is the only source a component may read. There is no
  code path from a hand-typed figure to the screen.
- **This file's own bullet above is the stale claim it was warning about.** It
  closed with "the clean-clone run is still R-7.5's open box and the install claim
  is still gated, not measured"; the run has since happened twice, and the sentence
  that was written as a caveat is now the part a reader would mis-trust. `CONTRIBUTING.md`
  carried the same expiry in plainer form ("`pip install .` from a clean clone into a
  throwaway venv has never been run") and now says what was measured instead.
- **A committed artifact was labelled as evidence of a run it was not.**
  `benchmarks/results/battery_reread_r75_20260928.log` went in under the message
  "R-7.5 fresh clone witness: … all 33 lines green", and `docs/portability.md` called
  it "the R-7.5 fresh-clone §6 witness". It is the checkout's R-7.10c re-read: it is
  `battery_reread_r710c_20260927.log` plus one trailing `battery rc=0`, and the driver
  it was credited to runs three lines, not thirty-three. The numbers in it are true —
  they are just the checkout's, which is the kind of true that a filename can turn
  into a claim about somebody else's machine. The file stays (deleting a committed
  artifact is not a correction), `docs/portability.md` says what it is, and
  `benchmarks/results/r75_sdist_battery_20260928.log` is the download's own print.
  What let this happen is worth naming: that log was produced by a shell redirect, not
  by a driver, so nothing in it says which tree it was read from. The replacement
  asserts its provenance before it reports a total and refuses to write the witness
  if the checkout's path appears in it.
- **The secret sweep's blob count was written as though it were a property of the
  repository.** `SPEC.md` and `docs/privacy.md` both cited **713 blobs ever committed**,
  which is true of the run that produced the witness and false of the next one: the
  history arm counts every blob that exists at the moment of the sweep, so the same
  command on the commit that carried that witness printed **735 blobs and 0 untracked
  files**. Both pages now say which number they are quoting and that a larger count on
  re-run is the history growing — a *smaller* one would mean somebody rewrote it.

### Fixed
- **R-7.16** `python -m flash.train --selftest` no longer needs a backend it cannot
  have. The first CI run on a real ubuntu runner (`static (3.11)`, run `37102668462`,
  step *Backend-free import claim*) printed `FAIL ...new unexplained failures:
  [('train', 1, "    from mlx_lm.lora import CONFIG_DEFAULTS\nModuleNotFoundError: No
  module named 'mlx_lm'")]`, and the same code had been re-read green on this laptop
  days earlier — because `mlx-lm` sits behind
  `sys_platform=='darwin' and platform_machine=='arm64'`, so a Mac is the one place
  this class of bug is invisible. The slice-args checks were argued against the
  *installed* library; the 29 defaults are now recorded in the module and
  `build_slice_args` is pure over a dict, with `mlx_lora_defaults()` naming which
  source it read so the check's detail says whether it was measured off the box or off
  the record (proven `== mlx_lm.lora.CONFIG_DEFAULTS` on this machine, not merely
  similar). The check asserts `set(vars(a)) == set(defaults)`, which turns a key the
  library would silently ignore into a failure, and `default_slice` raises
  `ValueError("no training rows in …")` before any model import, so an empty dataset is
  a named error on a machine with no MLX. **36/36 in both lanes**, count unchanged;
  `python -m flash.train --dry-run` and the training call itself still name MLX in the
  error a stranger gets.
- **R-7.16, second CI round: the gate that demanded a Mac-only sentence.** The first
  push's `static (3.11)` and `static (3.12)` jobs printed `backend-free checks: 47/48
  passed` and `battery` was skipped by `needs: static`. The failing assertion was the
  checker's own: it required the blocker line to say `proof --backend-free`, a sentence
  only a machine that has MLX installed may honestly print. The fix is not a looser
  assertion. The installer's branch is now bought from *both* paths — a fabricated-probe
  gate feeds `install_backend_block()` a box where `import mlx.core` survives and a box
  where it does not, and demands each print its own word and never the other's — with a
  mutant (`blocker_is_a_literal`) that hard-wires `proof` and dies on exactly that check.
  The gate then asks for whichever sentence this box earns, so a Linux runner takes the
  `note` path, exits 0 and refuses the same two rows. A third instance of the same bug
  class was caught locally before it reached a runner: the checker decided the
  box-fact from its *inherited* `PYTHONPATH`, which the lane it gates had already
  poisoned, so inside `--backend-free` on this Mac it demanded `note` while the
  installer printed `proof` — 47/49 with the two FAIL labels naming it. It now strips
  `PYTHONPATH`, the same probe the installer answers from. **49/49 checks, 15/15
  mutants**, both on the plain tree and as a child of an injected lane
  (`benchmarks/results/backend_free_check_lane_shape_r716c_20261004.log`).
- **R-7.16, third CI round: two rows that spoke only one platform's dialect.** The
  Mac-only gate is fixed and the runner proved it — `static (3.11)` printed
  `note --backend-free: … this machine has no backend to lose` and
  `OK benchmarks/backend_free_check.py 49/49 (+ 15 mutants)` — then the same job went
  red two rows further down (run `37217140806`): `BAD flash power --selftest want
  22/22 got ['21/22']` and `BAD flash.sandbox --selftest want 34/34 got []`, the
  second one's last line a bare `AssertionError`. Neither is a backend claim and
  neither was closed by a label. `flash power`'s live probe demanded
  `mem_total_gb is not None` while the module read physical size from exactly one
  source, `sysctl hw.memsize`; it now reads `sysctl`, then `/proc/meminfo`
  (`MemTotal`, and `MemAvailable` for the free percentage the governor sheds on), then
  POSIX `sysconf`, and the check's detail prints **which source answered** — the same
  discipline `mlx_lora_defaults()` uses for the trainer's defaults. Two table rows
  carry the non-mac pair, one on a real Linux dump and one on a dump with no
  `MemAvailable`, which must report the total and refuse to invent a percentage:
  **22 → 24**. The two non-mac arms were exercised on this Mac by rewriting the
  platform fact in a child and reading what the module answers *and* who it says
  answered (`benchmarks/results/power_nonmac_arm_r716f_20261004.log`).
  `flash.sandbox`'s vector ended its setup with `assert seatbelt() is
  True`, an assertion no box without macOS' Seatbelt can satisfy, so on Linux it raised
  before printing a single check and the runner could not tell it from a broken jail.
  The invariant that assert was protecting is the restoration of `SENTRY` after the
  vector's two wrapper swaps, and it is now stated as that; nine Seatbelt claims are
  asked in two arms, and on an unconfined box the hostile write is aimed at a throwaway
  HOME and the two network candidates at a closed loopback port instead of the
  documentation IP the confined arm uses — a row may not print a fraction that depends
  on how a runner's egress treats a packet it is not confining — because a vector that litters a real `~/.ssh` and then cites the file as proof
  of a refusal it never got is the worse bug. **34/34 in both arms here**
  (`benchmarks/results/sandbox_arms_r716f_20261004.log` holds both prints), the Linux
  arm reached by pointing `SENTRY` at a path that does not exist, which is the state a
  Linux box is born in, and `RLIMIT_FSIZE` now accepts whichever shape the kernel
  reports (EFBIG to the interpreter on macOS, SIGXFSZ as a signal on Linux) since both
  are the write being stopped. `flash power` 22 → 24 moves the totals: **1,429 checks +
  20 oracle verifications = 1,449**, 172 mutants unchanged, and the lane's subtotal by
  the same two (1299 → 1301 checks, 1319 → 1321). The tree re-read on that fix printed
  **37** `OK` and no `BAD`
  (`benchmarks/results/battery_reread_r716f_20261004.log`) and the lane **35** with the
  same two rows named `REFUSED`
  (`benchmarks/results/battery_backendfree_lane_r716f_20261004.log`), both lanes printing
  `power 24/24` and `sandbox 34/34`. The next push bought the macos `battery` job:
  run `37227171133` is green on all three jobs (`static (3.11)` 31m49s, `static (3.12)`
  31m57s, `battery` on macos-14 in 27m35s), and that job's `flash selftest --all`
  printed **36** `OK`, no `BAD` and **1** row refused by name —
  `benchmarks/checkpoint_resume_check.py`, `machine — the §34.1 governor will not offer
  tournament width >= 2 on this machine`, `this machine offers 1 (free memory 6.0GB <
  6.9GB needed)` — on `checks 1394  oracle 20  §6 total 1414  mutants 172` under `NOT a
  §6 re-read`. 1429 − 35 = 1394, that row's own 35 checks, so the runner's number is a
  machine-shaped refusal, not a shortfall. Both rows fixed here answered on the runner:
  `power 24/24`, `sandbox 34/34`
  (`benchmarks/results/ci_battery_macos_37227171133_20261005.log`). What that run still
  does not buy is a whole 37-line §6 re-read on a hosted runner — the runner is not
  allowed to be cool enough to offer tournament width, which is a fact about the box,
  not about this tree.
- **R-7.9** `python -m flash.train --dry-run` writes nothing, on both of its
  branches. `--suite-from-dataset` was dispatched above the branch that honoured the
  flag, so a command typed to *avoid* touching the tree rewrote the tracked
  `benchmarks/tasks/r64_train_from_dataset.jsonl`; `git status` after a verification
  run is what found it. `suite_from_dataset()` now takes `dry` and skips the `mkdir`
  with the `write_text`, printing `-> would write <path>`. Two checks and one mutant
  in `benchmarks/lora_path_check.py`'s suite group, run with **no `--suite-out`** so
  the default name is the thing under test, and aimed at a temp copy of the suites so
  the mutant cannot dirty the tree it measures: **`lora path: 33/33 checks passed`,
  `mutations: 15/15 gates defeated by exactly their checks`**.
- **R-7.10** three module selftests that cannot run on a wheel install now say so
  instead of failing. Measured on a `pip install .` into a throwaway venv:
  `flash.grammar --selftest` and `flash.debug --selftest` raised
  `FileNotFoundError` for a `benchmarks/tasks/*.jsonl` path inside
  `site-packages`, and `flash.patches --selftest` printed
  `FAIL workspace: a project directory loads under its relative path` inside an
  otherwise-green 46-check report — three ways for a missing directory to be read as
  a broken package. `flash.graph` already refused with exit 2; the refusal is now one
  function, `doctor.vector_refusal()`, driven by a four-entry `doctor.VECTOR_DATA`
  table that all four `run_selftest`s consult before touching the filesystem.
- **R-7.10b** `flash doctor` told a wheel-installed user they were in an **editable
  checkout**, because the answer came from `Path(sys.executable).resolve()` — a macOS
  venv symlink pointing at the Homebrew framework, which is not a prefix of anywhere
  the package lives. It is now read off the package path: under
  `site-packages`/`dist-packages` is an installed copy.
  Vector for both: six checks and three mutants in
  `benchmarks/backend_free_check.py` → **`backend-free checks: 37/37 passed`**,
  **`backend-free mutants: 8/8 gates defeated by exactly their checks`**, plus a live
  repeat in a package-only tree where all four print the sentence and none prints a
  traceback. The gate that requires the same guards to say *nothing* when the four
  paths are present is what keeps this from being a vector that refuses to run.
- **R-7.10c** the refusal table was a list of four, and a list cannot grow a
  data-dependence on its own. Two more selftests were found broken on a
  package-only install by sweeping the table instead of trusting it:
  `flash.tourney --selftest` resolved its task file against the **caller's working
  directory** and died with `FileNotFoundError`, and `flash.lsp --selftest` read the
  minishop fixture and died three frames away from the absent directory with
  `ValueError: substring not found` — an error that names no path, which is exactly
  what a user reads as a broken product. Both now anchor on the package and refuse
  through `doctor.vector_refusal()`, and the table has six keys. The sweep
  (`benchmarks/backend_free_check.py`) copies `flash/` to a temp dir with no
  `benchmarks/`, discovers every module with a module-level `run_selftest` from the
  copy's sources, runs each from a foreign cwd, and classifies any death by
  re-running it in a second copy with the data symlinked back in: passes-with-data
  is data-dependence and must be tabled, fails-either-way is machine state and is on
  a named three-entry allow-list. **16 modules were swept, 6 refused, and the
  refusals equalled the table's keys.** Its first mutant printed **0 checks
  failing** because the planted module had no `if __name__ == "__main__"` tail, so
  `python -m … --selftest` imported and exited 0 — the spine gate now requires every
  swept module to have been dispatched. Five checks and two mutants added:
  **`backend-free checks: 42/42 passed`**, **`backend-free mutants: 10/10 gates
  defeated by exactly their checks`**, §6 **1119 checks + 20 oracle verifications =
  1139** with **85** mutants, re-read in a measured **15 min 9 s**
  (`benchmarks/results/battery_reread_r710c_20260927.log`).
- `benchmarks/portable_paths_check.py` now takes a `flock` on a per-checkout lock
  file and exits 2 rather than running twice at once. Found by a §6 battery run
  that printed **BAD … defeated 0 mutants, not 7** while every gate still said
  14/14. It did not reproduce alone — same interpreter, same tree, **14/14 + 7/7**
  twice, and again through the battery's own `--quick` path. So it was chased by
  construction instead: the mutants write real bytes into three corpora and
  restore them, and launching two runs together in a throwaway clone reproduced
  the shape on purpose — **14/14 gates** with **5/7** and **6/7** mutants.
  Concurrent execution is therefore a *sufficient* cause of what the battery
  caught; that it was this run's cause is inferred from being the only difference
  available, and is not proven. The dangerous half is a mutation being reverted
  by the other run before the gates read it, which disarms the check that exists
  to catch a host path going back while the suite still looks green. And it does
  not stop at a wrong report: while two of this file's own runs were alive at
  once, one snapshotted `docs/portability.md` while the other had its
  `doc_silent` mutation applied, so the restore wrote the mutated bytes back and
  the group name the vector exists to check for was gone from the doc on disk
  afterwards. The next honest run caught it as `13/14` with
  `missing from doc: ['traces']`. That is the blast radius, and it is why this is
  a lock rather than a docstring warning.
  Verified as
  a behaviour, not a code read: with the lock held the run prints the holder's pid
  and exits 2, with `--exclusive` it proceeds and defeats its mutant (1/1), and
  alone it still prints **7/7**.
- `requirements.txt` mirrored `pyproject.toml`'s dependency floors with one of
  them wrong: `mlx-lm>=0.24` against the package's `>=0.31`, on a file whose whole
  purpose is to give the same four things. Diffed all four by parsing
  `pyproject.toml` rather than by eye; `mlx-lm` was the only mismatch, and the
  floor is now 0.31. `docs/models.md` documents 0.31.3, which is what is installed
  here — and no §6 number depends on the line, because the offline battery loads
  no model; the live arms do. Nothing in the tree compares the two files, so the
  note in the header says the diff was done by hand and when.
- `flash/debug.py` exec'd the test's `sys.path` bootstrap **after** the candidate
  and inside the traced region, so a candidate that imports the repository at top
  level died on its own `import` and the `--debug` digest reported the harness's
  crash instead of the candidate's execution trail. Measured against the pre-fix
  build over all 72 context reference solutions: **42 changed verdict**
  (`ModuleNotFoundError` → `pass`) and they are exactly the ones whose candidate
  imports the repo; the other 30 were never affected. `run_test` had always
  hoisted it first; the tracer now does the same, with tracing off during the
  bootstrap (`benchmarks/results/debug_order_probe_20260927.log`). Found by reaching
  the debug seam while porting the suites — `--selftest` (55/55) and `--suite`
  (32/32) passed before and after, because the corpus those two run against has no
  bootstrap line at all for the bug to live in. This does **not** reopen R-4.3's
  measured miss: the band tasks that carry a bootstrap get the same verdict and the
  same trail length under both builds, so neither A/B arm was fed a crash.

### Packaging
- `pyproject.toml` with a platform marker on `mlx-lm`, so a non-Apple-silicon
  install skips the model layer instead of failing to resolve it. **Both halves of
  this bullet were superseded the day it stopped being true.** The clean-clone box it
  called open is R-7.5, which closed on 2026-09-28 with all four install shapes RUN —
  fresh clone, wheel in a throwaway venv, editable clone and unpacked sdist — the last
  of them carrying the whole §6 battery to the same printed totals from inside the
  download. And the measurement it offered as what *was* verified, **2 of the 26
  modules failing** with `mlx` blocked (`flash.decide` at its `import mlx.core`,
  `flash.route` through it), is the pre-fix figure: R-7.7 moved the import inside the
  call, so all 26 import and the sweep prints `SWEEP 26/26`. It stays quoted here
  because it is the reason the box existed, not because it is what a reader would
  measure today.
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue
  and PR templates, Dependabot, and a CI workflow with a model-free job on ubuntu
  and the §6 battery on macOS. **The workflow has never executed** (no remote is
  configured). It used to call four things that did not exist — `flash --version`,
  `flash doctor`, `benchmarks/backend_free_check.py` and
  `benchmarks/battery_reread.py --backend-free` — and all four now do, which is why
  the dangling-pipeline sentence in `SPEC.md`, `CONTRIBUTING.md` and `docs/models.md`
  became history in this pass. **That is what the files say; no run on GitHub's side
  has proved they hold, and the ubuntu job in particular has never had a Linux
  interpreter run this battery**, which is a wider claim than R-7.7's Apple-Silicon
  import sweep supports. Every `flash` command either workflow prints is gated by
  `benchmarks/documented_commands_check.py`, which parses them against the real
  parser on every §6 re-read.

## [0.0.1] - tagged 2026-09-28, still not published

**Tagged, not released.** `v0.0.1` is an annotated tag on `6a0868b`, the commit that
closed R-7.5 — the box that gated it — and work has landed since it was cut, including
the cross-tool run above. It is still not downloadable, because no remote
is configured: a tag on a laptop is a promise, and the promise is kept only when
someone pushes it. The version was deliberately not bumped to `0.1.0` for this; the
number the package prints is repeated in `flash/__init__.py`, in the landing page's
own `VERSION` constant, in the exported transcript the page renders, and in the
`flash 0.0.1` lines of SPEC, CONTRIBUTING and the witnesses under
`benchmarks/results/` — one fact kept true in several places, and relabelling it would
mean re-measuring the generated page for a digit. The notes below are the release's,
and its counts are the tree's printed ones as of the last §6 re-read.

A local, verify-first coding agent for Apple Silicon, with 1,210 offline checks +
20 oracle verifications + 111 mutation gates, and a `SPEC.md` that records which of
its own gates measured NO. (The tree this tag points at printed 1,119 + 20 + 85; the
counts above are the newest printed ones, because the tag is not downloadable and
these notes follow the tree, as the paragraph above says they do.)

### Added — perception
- **R-1.1** `flash/lsp.py`: when a failure names a symbol the repository defines,
  the *real source* of that symbol is put into the retry prompt (not just into
  the record) — budget 1200 chars, ranked so the thing actually at issue comes
  first. Certified at the seam that matters: a stored prompt carries it.
- **R-1.1c** that budget now overflows instead of aborting — an oversized symbol
  is clipped at a line boundary and the symbols left behind are counted in the
  block, rather than the whole hint vanishing.
- **R-1.1b** both perception blocks are individually withholdable
  (`--no-source-hint`, `--no-graph-hint`), which makes "does a hint help?" a
  measurable question. Its first reading is a stated nil (see below).
- **R-1.2** cross-file go-to-definition and project-wide references: the AST owns
  symbol kinds, `pylsp` owns resolution, and every call degrades to the AST answer
  when the server is missing or slow.
- **R-1.3 / R-1.3b** `flash/graph.py`: an AST call graph answering a blast radius
  in about 0.1 ms (measured: 44 checks + 12 mutants), now feeding a retry prompt
  the dependents of the symbols at issue.

### Added — verification and execution
- `flash/harness.py`: assertions run one at a time in a subprocess, and a failing
  assert is reported as `GOT`/`WANT` rather than as a traceback line — which is
  what makes a retry about a *value* instead of about a stack.
- **R-9.2** every candidate-execution seam goes through `flash/sandbox.py`: one
  Seatbelt profile via `/usr/bin/sandbox-exec` (writes confined to a root,
  outbound network denied) plus inherited `RLIMIT_CPU`/`RLIMIT_FSIZE`. Proven with
  a hostile `~/.ssh` write and an open socket, both of which come back as ordinary
  verify failures. On macOS no memory rlimit can be set at any value, and
  `sandbox.status()` prints `UNAVAILABLE` instead of claiming the bound.

### Added — agent policy
- **R-3.3** tournament mode, gate MET: pass@1 5/7 → best-of-3 6/7 (+14 points) at
  40 % lower spend.
- **R-5.3** task-granular checkpoints: a `kill -9` between two tokens resumes
  *inside* the task, measured twice on a real 7B.
- **R-2.3** prospective confidence from four verification streams, wired to the
  ledger.
- **R-3.2** symbol-precise patching (`--edit`): clause 1 MET, clause 2 missed by
  one line, for a stated reason.
- **R-7.2** ambient mode: an idle window that drafts, verifies its own drafts, and
  cannot commit or push.

### Added — learning
- **R-6.1/R-6.2** the outcome ledger and the router fit on it.
- **R-6.3** `trainable()`'s extra exclusion, audited as costing no measurable AUC
  — at stated precision.
- **R-6.4** a gated, resumable LoRA path: verified-outcome dataset, kill/resume
  under `kill -9`, leakage rule, adapter identity (31 checks + 14 mutants).

### Measured, and NOT met
These stayed open because the number said so. Each is a negative result in
`SPEC.md`, not a removed requirement:
- **R-4.2** latency cost ≤ 5 % — measured, not met, mechanism identified
  (constrained decoding's per-step cost is 0.4 % of the decode step; the residual
  is the read-back sync).
- **R-4.3** debug feedback beating traceback feedback by ≥ 2 solves — three
  substrates, none of which held; booked as a miss at 27/30 vs 25/30.
- **R-2.3** the offer-rate gate — one clause is unmeasurable rather than passed.
- **R-6.4** trained arm 16/20 where the frozen base is 18/20, and a shuffled-label
  control beats the trained arm. Booked as a negative.
- **R-8.1** speculative decoding — negative on both targets.
- **R-1.1b** whether the hint blocks help: at n=10 with 3 attempts, `both` solved
  3/10 and each of `source only`, `graph only` and `off` solved 2/10. One task is
  10 points on this instrument, so that is a nil, not a win.

### Corrected
- **R-1.1** the symbol hint was writing into the attempt record and never into the
  prompt. Fourteen green checks and four live runs had been crediting symbol
  injection with things it did not do; every affected claim in this repository was
  retracted in place rather than deleted.

### Not in this release, by design
- Any hosted or server form (no API, no multi-tenancy — it is a local CLI).
- Generation on Linux/Windows (the `mlx` model layer is Apple-silicon-only).
- A 16 GB co-residency arm, a 24-hour soak, a 10-developer week-long feel test, a
  real-microphone arm, and watts/task — each blocked on hardware, sudo, or people,
  and each named as such in `SPEC.md`.
