"""Flash Coder — fast, fully-local, self-improving coding agent for Apple Silicon.

Plan: ../PLAN.md (v3.8). This package grows milestone by milestone:
  M0: benchmarks/m0_bakeoff.py      (model selection — measured, not guessed)
  M1: flash.loop + flash.harness    (ACT -> VERIFY -> error-feedback retry)
      flash.decide + flash.cli      (System One decision fabric, PLAN §32)
  M2: flash.context + flash.lsp     (repo perception: skeleton, symbols, refs)
      flash.harness                 (multi-WRITER: '# file:' -> file sets)
  M3: flash.ledger + flash.learn    (outcome flywheel + learned router)
      flash.jobs                    (gated, resumable background refit, §34.3)
  §34.1: flash.power                (system profile: what this machine may load)
  §33.6/7: flash.trace              (replayable sessions + `flash resume`)
  §33.3: flash.grammar              (constrained decoding: the output contract
                                     as a per-step token mask, `--constrain`)
  §33.1 ACT: flash.patches          (symbol-precise edits: '# edit: file ::
                                     Symbol' -> the AST's own lines, `--edit`)
  §33.4: flash.tourney              (tournament mode: k independent candidates,
                                     oracle-scored, width-clamped by the
                                     governor, `--tournament`)
  Phase-4: flash.web                (knowledge as a tool: fetch+sha1-cache+
                                     bge-ranked excerpts, §25a3, §33.9)
  M0b: flash.vision                 (screenshot -> HTML through the VLM)

Every subsystem ships an offline deterministic selftest:
  python -m flash.harness | flash.lsp | flash.power | flash.jobs | flash.trace
             | flash.grammar | flash.patches | flash.debug | flash.tourney
  (flash.web's runs through `flash web --selftest`; it has no __main__.)
"""

__version__ = "0.0.1"
