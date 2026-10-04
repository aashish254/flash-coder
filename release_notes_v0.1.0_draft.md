# Flash Coder v0.1.0 Release Notes

**Release Date**: October 4, 2026  
**Commit**: (this pass's commit — shown beside the tag before anything is pushed)  
**Tag**: v0.1.0

---

## What is Flash Coder

Flash Coder is a **local, verify-first coding agent for Apple Silicon**. It writes code, runs tests, reads actual failure values, and retries—all on your machine with no API keys, no containers, nothing leaving your Mac.

### Quick Start

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Check your installation
flash doctor

# Run the offline battery (no models needed)
flash selftest --all

# Get started coding
.venv/bin/python benchmarks/m0_bakeoff.py --download --models qwen25-coder-7b
.venv/bin/python -m flash.cli session --context . --test t.py --apply
```

---

## What's Verified (Measured Only)

### Installation Shapes
All four install shapes tested against the §6 offline battery:

| Shape | Result | Witnesses |
|-------|--------|-----------|
| Fresh clone editable install | ✅ 35/35 lines, checks 1210 + oracle 20 = 1230 green, mutants 111 | `benchmarks/results/r75_sdist_battery_20260928.log` |
| Wheel in throwaway venv | ✅ 33/35 lines, checks 1119 + oracle 20 = 1139 green, mutants 85 (TS vectors refuse by name) | Same as above |
| Packed sdist install `[ts]` extra | ✅ 35/35 lines, checks 1210 + oracle 20 = 1230 green, mutants 111 | Same as above |
| Current tree (37 lines) | ✅ 37/37 lines, checks 1429 + oracle 20 = 1449 green, mutants 172 | `benchmarks/results/battery_reread_r716f_20261004.log` |
| Same tree, `--backend-free` (the Linux shape) | ✅ 35/37 lines green + 2 refused by name, checks 1301 + oracle 20 = 1321, mutants 146 | `benchmarks/results/battery_backendfree_lane_r716f_20261004.log` |

### Offline Battery Highlights (§6)
- **1,429 premise checks** across 37 vectors
- **20 oracle verifications** (held-out tests for patch application, confidence gates, etc.)
- **172 mutation gates** planted bugs caught by the suite

Key vectors verified:
- `benchmarks/backend_free_check.py`: **49/49 checks, 15/15 mutants** — all 27 `flash` submodules import with MLX blocked, and the `--backend-free` lane refuses two named rows instead of calling them failures ✓
- `benchmarks/documented_commands_check.py`: **8/8 commands resolve**, **5/5 mutants caught** ✓
- `benchmarks/portable_paths_check.py`: **15/15 checks**, **7/7 mutants caught** — no host paths leaked ✓
- `benchmarks/ts_perception_check.py`: **47/47 checks**, **13/13 mutants** — TypeScript graph support ✓
- `benchmarks/ts_patch_check.py`: **52/52 checks**, **15/15 mutants** — TS symbol patches ✓
- `benchmarks/session_check.py`: **81/81 checks**, **26/26 mutants** — prose chat mode ✓
- `benchmarks/patch_landing_check.py`: **65/65 checks**, **28/28 mutants** — verified edit landing ✓

### Live Run (First Green Chat Turn)
Witnessed on the author's demo task (`benchmarks/results/session_pty_r715g_20260929.log`):
```
[turn 1] routed=small tier=big solved=True attempts=3 (13.5s) patches=1 refused=0 whole=0 outside=0
[R-3.2] wrote money.py (+4 -1 lines)
[turn 2] routed=small tier=small solved=True attempts=1 (5.7s) patches=1 refused=0 whole=0 outside=0
[R-3.2] wrote money.py (+2 -0 lines)
[session] turns=2 solved=2 written=2 seconds=19.2 last_rc=0
rc=0 ORACLE GREEN
```

Token residency re-measured: cold load 2.0–2.1s, free after delete 0.65–0.74s.

---

## Honesty Panel (What This Page Refuses to Print)

### Open Gaps
These are not blockers; they're honest bookings of what hasn't been measured here:

| Item | Status | Reason |
|------|--------|--------|
| **Live real-weights prose chat transcript** | OPEN | Offline sweep passes (81/81 + 26/26), but hasn't been witnessed running against actual weights. The README documents this explicitly. |
| **Cursor / Copilot comparison** | OPEN | Neither tool is headless-capable on this machine, both generate in cloud under paid plans. R-7.13 booked as negative. |
| **Linux / Windows installation** | GATED | MLX is Apple Silicon only. `pyproject.toml` marks the platform constraint, and the `--backend-free` lane measures that shape: every module imports and 35 of 37 battery rows answer, with the two that reach the backend refusing by name rather than failing. What has still never happened is a full install run on a non-macOS machine. Documented in SPEC R-7.5 and R-7.16. |
| **macOS CI battery job** | GATED | `ci.yml`'s `battery` job has never executed — the run that exposed the training-seam bug was `needs: static`-skipped by the red static job. Its first green on a real macos-14 runner is the proof, not this page. |
| **Trained adapter vs frozen harness** | MEASURED NEGATIVE | R-6.4 trained arm 16/20 where base is 18/20. Booked as negative result in SPEC §5. Shuffled control 19/20 beat the trained arm. |
| **Debug feedback gain (R-4.3)** | MEASURED NEGATIVE | 27/30 traceback vs 25/30 --debug. No measurable delta; --debug stays off by default. |

### Not Measured, But Intentional
- No benchmark PNGs (fabricated dashboard set reverted in commit `69a4d2d`)
- No "will N hours move the stat" claims (bounded scenarios from committed artifacts only)
- No vendor figures pasted into tables

---

## What Changed Since v0.0.1 (tagged 2026-09-28)

**R-7.16** — the first CI run on a real Linux runner found a bug a Mac cannot see, and
the release now carries the machinery that says so honestly:
- **The training seam went offline for real.** `flash.train`'s slice-args checks were
  argued against the *installed* `mlx-lm`; the library's 29 defaults are now recorded in
  the module (proven `== mlx_lm.lora.CONFIG_DEFAULTS` on mlx-lm 0.31.3) and
  `mlx_lora_defaults()` names which source it read. **36/36 checks in both lanes.**
- **A lane that refuses instead of lying.** `--backend-free` prints a `REFUSED` line per
  backend-bound row carrying the sentence that row died with, subtracts that row's own
  counts, and exits 0 under a `NOT a §6 re-read` banner. Gated five ways: **49/49 checks,
  15/15 mutants** — including the gate the ubuntu runner bought, which feeds the
  installer both probe outcomes and demands each print its own blocker word.
- **CI's tokenizer pre-warm.** Four scheduled nightlies died on a cold cache, not a failing
  test; `ci.yml`'s macos `battery` job now caches and pre-warms the tokenizer's 7 files
  (11M, no weights) and asserts `battery_reread --quick grammar` in isolation. The same
  two steps for `nightly.yml` are written and deliberately held out of this release, so
  the nightly's own failures stay open.

The R-7.15h/g/f/e/c/b arc shipped in the same window:
- **R-7.15h**: `flash session` answers in prose when no `--test` provided — it became a chat, not only a scorer. **81/81 checks, 26/26 mutants** offline.
- **R-7.15c**: Patch arm can create whole new files (`# edit: newfile.py :: *`) or single symbols (`+Name`). **94/94 patches selftest, 65/65 patch landing, 28/28 mutants**.
- **R-7.15g**: VERIFY grades patched copy when oracle names its own tree. Precedence line fixes bootstrap shadowing. **28/28 harness selftest**.
- **R-7.15f**: The chat sees the project it's asked to patch (2129 → 2893 characters, both files included). All eight attempts addressed target module (zero refusals).
- **R-7.15e**: Oracle-addressed patch refused before address resolution. One shared key function guards both patch gate and write-back gate. **71/71 patches, 53/53 landing**.
- **R-7.15b**: Patch arm creates symbols (`# edit: file :: +Symbol`). Position extractor owns insertion point, two blank lines at top level. **63/63 patches, 52/52 ts_patch, 24/24 mutants**.
- **R-7.15 clause 7**: Session reads turns as generator (one per line), prints `you>` marker, accepts answer before next keystroke. Pseudo-terminal driver `benchmarks/session_pty_demo.py` confirms **t+0.06s banner, t+2.15s prompt, t+19.22s verdict, t+19.43s next keystroke**.

Total moved to **1,426 + 20 = 1,446** (§6 checks + oracle) and **171 mutants** on the
37-line tree, then to **1,427 + 20 = 1,447** and **172 mutants** when the first ubuntu
runner moved the gate that checked it, and to **1,429 + 20 = 1,449** on the same 172
mutants when the third CI round moved a row that had nothing to do with a model:
`flash power` read its memory size from one macOS-only source (22 → 24), and
`flash.sandbox`'s vector asserted a jail only macOS ships (34 held at 34 by giving nine
claims a second arm). Both now print the same fraction on the Mac and on the runner.

---

## Dependencies

- Python ≥ 3.11 (system python must be 3.11+, Homebrew/pyenv/uv OK)
- MLX backends (Apple Silicon only): `mlx-lm>=0.31`, `mlx-vlm>=0.3` (optional, for vision tasks)
- Tree-sitter TypeScript (optional): `tree-sitter`, `tree-sitter-typescript` — adds 44–55ms build time for 21 `.tsx/.ts` files

Install with:
```bash
pip install -e .                    # core + MLX on macOS
pip install -e .[ts]                # + TypeScript grammar
pip install -e .[dev]               # + dev tools (pyflakes, pytest, build, etc.)
```

---

## Known Issues

None tracked. The three Dependabot CI-action bumps (PRs #3, #2, #1 —
`actions/checkout` 4→7, `actions/cache` 4→6, `actions/upload-artifact` 4→7) were merged
2026-10-03 and touch only the two workflow files; none of them changes a measured figure.

Two things are open by their nature rather than by oversight: the live real-weights prose
chat transcript above, and CI's macOS `battery` job, whose first green run has not happened
yet. Both are measured rather than asserted in SPEC R-7.16.

---

## License

MIT License. See LICENSE file.

---

**Notes on methodology**: Every number here comes from an executable witness file. If you see a figure that looks like it was typed, check `benchmarks/dashboard_data.py`'s WITNESS constant — if it's not sourced from a dated log printed by the driver, it shouldn't be on this page.

This release follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. Changes since v0.0.1 are also documented in CHANGELOG.md with full git-traceable history.
