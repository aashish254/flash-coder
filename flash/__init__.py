"""Flash Coder — fast, fully-local, self-improving coding agent for Apple Silicon.

Plan: ../PLAN.md (v3.8). This package grows milestone by milestone:
  M0: benchmarks/m0_bakeoff.py      (model selection — measured, not guessed)
  M1: flash.loop + flash.harness    (ACT -> VERIFY -> error-feedback retry)
      flash.decide + flash.cli      (System One decision fabric, PLAN §32)
  M2: flash.context + flash.lsp     (repo perception: skeleton, symbols, refs.
                                     R-1.1's seam is in flash.loop: a failed
                                     attempt's `err` gains `lsp.symbol_hint`'s
                                     resolved source BEFORE the retry prompt and
                                     the recorded Attempt are built — writing it
                                     the other way round fed only the trace.
                                     `loop._perceive` appends BOTH perception
                                     blocks over the BARE error, and `_repo_index`
                                     parses the repo once per retry so the source
                                     hint and the graph hint rank the same symbols.
                                     R-1.1c: that shared ranking is why an
                                     oversized symbol is CLIPPED and the ones left
                                     behind COUNTED — the budget check used to be a
                                     `break`, so one long function at issue deleted
                                     the whole source block.
                                     R-1.1b: `loop.HINTS` is the SELECTION
                                     (`("source", "graph")` ships) and
                                     `--no-source-hint` / `--no-graph-hint` drive it,
                                     with both names in `cli.SUITE_PARAMS` so a
                                     session states which arm wrote it — the A/B that
                                     question asked is a measured NIL on a frozen
                                     10-task band, which is why both blocks stay
                                     wired and neither is claimed to help)
      flash.perceive                (one file's static diagnostics as an oracle)
      §28/R-1.3: flash.graph         (the knowledge graph, AST-only and no vector
                                     store: nodes are functions/classes/modules/
                                     constants, every edge carries the line and
                                     text that proves it plus the rule that bound
                                     it; `flash graph <symbol>` names the callers a
                                     change would break under the clause's 200 ms,
                                     `merge()` re-extracts only changed files, and
                                     `--live` lets the language server settle the
                                     graph's own blind spots.
                                     §28.2 step 3 / R-1.3b: `scope_hint()` feeds the
                                     same answer into the loop — the at-issue
                                     symbols' depth-2 blast radius, ≤900 chars, as
                                     the second block `_perceive` appends, silent
                                     when a name will not resolve to one node.
                                     R-1.4: `flash.lang_ts` adds the second
                                     language — TypeScript, picked by a file census
                                     of this tree (the ledger asked for neither it
                                     nor SQL, and that null is recorded) — folded
                                     into the SAME records, so one `blast()` answers
                                     across both under
                                     `build(langs=("python", "typescript"))` /
                                     `--lang py,ts`. Python alone remains the
                                     default, because every published graph number
                                     was built that way, and the tree-sitter grammar
                                     is an optional extra whose absence prints one
                                     refusal rather than a graph of guesses)
      flash.route                   (the cheapest tier that can solve this task)
      flash.harness                 (multi-WRITER: '# file:' -> file sets.
                                     R-7.4: `harness.REPO` + the `<REPO>` token in
                                     a task corpus's oracle bootstrap are expanded
                                     in `_hoist_path_bootstrap`, deliberately the
                                     ONE place, because that is already the single
                                     function every execution seam passes through
                                     to split a test into (bootstrap, body) —
                                     `run_test`, `score`'s probes, `debug`'s tracer,
                                     `confidence`'s seeded re-runs. `python -I`
                                     implies `-E`, so `PYTHONPATH` cannot carry
                                     fixtures instead; a corpus that stored a real
                                     path could only ever run on the machine that
                                     wrote it, and it stored 72 of them. Reaching
                                     that seam also found the tracer exec'ing the
                                     bootstrap INSIDE the traced region: measured
                                     old-vs-new over those 72, **42 changed verdict**
                                     — exactly the candidates that import the repo —
                                     and the other 30 were never affected. The scan
                                     that keeps it that way counts the default
                                     Homebrew prefix as a host path too, and takes
                                     a lock, because its mutants write into these
                                     corpora)
  M3: flash.ledger + flash.learn    (outcome flywheel + learned router)
      flash.jobs                    (gated, resumable background refit, §34.3)
  §34.1: flash.power                (system profile: what this machine may load)
  §33.6/7: flash.trace              (replayable sessions + `flash resume`)
  §33.3: flash.grammar              (constrained decoding: the output contract
                                     as a per-step token mask, `--constrain`)
  §33.1 ACT: flash.patches          (symbol-precise edits: '# edit: file ::
                                     Symbol' -> the AST's own lines, `--edit`;
                                     an address wider than the change is
                                     narrowed to the runs that differ, so a
                                     class header cannot re-emit an untouched
                                     method; '# edit: file :: +Name' CREATES a
                                     symbol as a pure insertion, at a position
                                     the extractor owns; the test file named by
                                     `--test` is refused as the oracle before its
                                     address is resolved, so the refusal says
                                     which file it is rather than complaining
                                     about line numbers; `land()` is the only thing that
                                     writes, and `flash run --apply` /
                                     `flash session` hand it the verified
                                     workspace)
  §33.1 UX: flash.cli cmd_session   (R-7.15: `flash session` reads one prompt
                                     per line from stdin against one repo and
                                     one oracle, runs each turn as its line
                                     arrives (a tty gets `you> `, a pipe gets
                                     none), re-reads the workspace from
                                     disk every turn, and prints a verdict per
                                     turn plus one report at EOF)
  §33.4: flash.tourney              (tournament mode: k independent candidates,
                                     oracle-scored, width-clamped by the
                                     governor, `--tournament`)
  §34.2: flash.confidence           (prospective confidence from verification
                                     evidence: static + coverage + hash-seed
                                     reruns + edge probes, `--confidence`)
  §33.7/R-5.3: flash.checkpoint     (task-granular recovery: the in-flight
                                     generation's text, token ids, retry
                                     conversation and sandbox state, flushed per
                                     16 tokens so `flash resume` continues inside
                                     a task instead of restarting it)
  §27.3/R-6.4: flash.train          (the weights leg of the flywheel: mine the
                                     agent's own oracle-verified outcomes into a
                                     train/valid dataset split by task id, fit
                                     LoRA inside §34.3's budget, and regenerate a
                                     suite from a dataset's own task ids)
  §21/R-9.2: flash.sandbox          (the explicit sandbox every candidate
                                     execution runs under — Seatbelt via
                                     /usr/bin/sandbox-exec + inherited rlimits:
                                     writes only to the root it was given, no
                                     outbound network, cpu and file-size ceilings;
                                     `python -m flash.sandbox` prints which layers
                                     this box actually enforces)
  Phase-4: flash.web                (knowledge as a tool: fetch+sha1-cache+
                                     bge-ranked excerpts, §25a3, §33.9)
  M0b: flash.vision                 (screenshot -> HTML through the VLM)
  §33.5/R-7.2: flash.ambient        (`flash ambient`: idle windows that DRAFT —
                                     lint, suite-premise and doc-drift findings
                                     fixed inside their own git worktree, verified
                                     by re-running the check that found them, and
                                     refused if the fix adds a finding or deletes
                                     a line it was only asked to add — never
                                     merged, never pushed)

Every subsystem ships an offline deterministic selftest:
  python -m flash.harness | flash.lsp | flash.power | flash.jobs | flash.trace
             | flash.grammar | flash.patches | flash.debug | flash.tourney
             | flash.confidence | flash.checkpoint | flash.train | flash.ambient
             | flash.sandbox | flash.graph
  (flash.web's runs through `flash web --selftest`; it has no __main__.)
"""

__version__ = "0.1.0"
