"""Re-read SPEC §6's offline battery from the tree and check its printed total.

The rule this exists to enforce: a battery total must come from the counts the
runs *print*, never from an exit code and never from a grep for a fixed phrase.
Two capture traps happened while hand-summing this list — `grep "checks passed"`
silently drops `grammar` 47 and `debug` 55 because they print a bare fraction,
and a last-line grep reports lora_path_check's 14 mutants as if they were its
checks (31), which under-counted the sum by 17 while looking clean.

So every item here carries the exact fraction it must print. A run that reports
60/61 where 61 is expected fails the presence test even though it exits 0, and
a check added on top of a battery moves its line's number and trips CLAIM.

    python benchmarks/battery_reread.py            # 15 min 9 s measured 2026-09-27
    python benchmarks/battery_reread.py --quick    # only the lines that moved
    python benchmarks/battery_reread.py --backend-free   # the same, with `mlx`
                                                           # blocked in every child

`--quick` takes the names it filters on as further args, e.g.
`--quick ambient lora`. Mutation counts are summed and reported apart from the
checks, because SPEC §6's total counts checks and m0_bakeoff's 20 oracle
verifications, and the mutants are extra to both. `--backend-free` is R-7.6's: it
installs an import blocker for the MLX packages into every child process, proves
the blocker actually bites before it prints a single green line, and so turns
"the offline battery loads no model" from a sentence into a run.

One line is environmental rather than logical: `checkpoint_resume_check.py`
refuses to run unless the §34.1 governor offers tournament width >= 2, so on a
busy box it exits with its own remedy ("let it cool, re-run") and the §6 total
cannot be read here. That is the battery failing closed, not passing quietly —
but it arrived as `printed []`, which named nothing, so a BAD line now echoes the
run's last line. Read the whole tree's total on a quiet machine.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# (label, argv, checks it must print, mutants it must print, kind)
# kind: "checks" feeds the selftest/end-to-end/premise sum, "oracle" the second.
BATTERY = [
    ("flash.harness --selftest", "-m flash.harness --selftest", 20, None, "checks"),
    ("flash lsp-selftest", "-m flash.cli lsp-selftest", 22, None, "checks"),
    ("flash power --selftest", "-m flash.cli power --selftest", 22, None, "checks"),
    ("flash jobs --selftest", "-m flash.jobs --selftest", 20, None, "checks"),
    ("flash trace --selftest", "-m flash.cli trace --selftest", 30, None, "checks"),
    ("flash web --selftest", "-m flash.cli web --selftest", 9, None, "checks"),
    ("flash.grammar --selftest", "-m flash.grammar --selftest", 47, None, "checks"),
    ("flash.patches --selftest", "-m flash.patches --selftest", 46, None, "checks"),
    ("flash.debug --selftest", "-m flash.debug --selftest", 55, None, "checks"),
    ("flash.tourney --selftest", "-m flash.tourney --selftest", 16, None, "checks"),
    ("flash.confidence --selftest", "-m flash.confidence --selftest", 29, None, "checks"),
    ("flash.sandbox --selftest", "-m flash.sandbox --selftest", 34, None, "checks"),
    ("flash.checkpoint --selftest", "-m flash.checkpoint --selftest", 31, None, "checks"),
    ("flash.train --selftest", "-m flash.train --selftest", 36, None, "checks"),
    ("flash.graph --selftest", "-m flash.graph --selftest --mutants", 44, 12, "checks"),
    ("flash.ambient --selftest", "-m flash.ambient --selftest", 61, 6, "checks"),
    ("benchmarks/trace_resume_check.py", "benchmarks/trace_resume_check.py", 11, None, "checks"),
    ("benchmarks/confidence_wiring_check.py", "benchmarks/confidence_wiring_check.py", 35, None, "checks"),
    ("benchmarks/subtle_premise_check.py", "benchmarks/subtle_premise_check.py", 52, None, "checks"),
    ("benchmarks/p6_key_check.py", "benchmarks/p6_key_check.py", 28, None, "checks"),
    ("benchmarks/confidence_tau_check.py", "benchmarks/confidence_tau_check.py",
     7, None, "checks"),
    ("benchmarks/checkpoint_resume_check.py", "benchmarks/checkpoint_resume_check.py", 35, None, "checks"),
    ("benchmarks/lora_path_check.py", "benchmarks/lora_path_check.py", 33, 15, "checks"),
    ("benchmarks/dbg_band_check.py", "benchmarks/dbg_band_check.py", 172, 5, "checks"),
    ("benchmarks/router_portable_check.py", "benchmarks/router_portable_check.py",
     20, 5, "checks"),
    ("benchmarks/graph_perceive_check.py",
     "benchmarks/graph_perceive_check.py --sweep", 33, 12, "checks"),
    ("benchmarks/ts_perception_check.py",
     "benchmarks/ts_perception_check.py --sweep", 47, 13, "checks"),
    ("benchmarks/ts_patch_check.py",
     "benchmarks/ts_patch_check.py --sweep", 44, 13, "checks"),
    ("benchmarks/patch_landing_check.py",
     "benchmarks/patch_landing_check.py --sweep", 40, 22, "checks"),
    ("benchmarks/session_check.py",
     "benchmarks/session_check.py --sweep", 48, 16, "checks"),
    ("benchmarks/hint_ab_check.py", "benchmarks/hint_ab_check.py",
     14, 8, "checks"),
    ("benchmarks/portable_paths_check.py", "benchmarks/portable_paths_check.py",
     15, 7, "checks"),
    ("benchmarks/backend_free_check.py", "benchmarks/backend_free_check.py",
     42, 10, "checks"),
    ("benchmarks/documented_commands_check.py",
     "benchmarks/documented_commands_check.py", 8, 5, "checks"),
    ("flash.debug --suite", "-m flash.debug --suite", 32, None, "checks"),
    ("flash.patches --suite",
     "-m flash.patches --suite benchmarks/tasks/edit_tasks.jsonl", 60, None, "checks"),
    ("benchmarks/m0_bakeoff.py --dry-run", "benchmarks/m0_bakeoff.py --dry-run",
     20, None, "oracle"),
]

CLAIM = {"checks": 1298, "oracle": 20, "mutants": 149}


def run(argv: str) -> str:
    proc = subprocess.run([PY, *argv.split()], cwd=ROOT, capture_output=True,
                          text=True)
    return proc.stdout + proc.stderr


def mutant_count(text: str) -> int:
    """How many mutants a run says it defeated. Three house styles print this:
    lora_path_check's `mutations: 14/14` summary line, and ambient's six
    `OK MUTATION:` lines, which carry no fraction of their own. graph's sweep is
    the second shape with lowercase `ok` markers *plus* a `12/12 caught`
    summary, so the markers deliberately do not match its pattern — matching
    both would count graph's mutants twice. `graph_perceive_check.py --sweep` is
    that same shape a third time (twelve `ok   MUTATION:` verdicts plus two
    `12/12 caught` summaries, one per sweep order); its markers are lowercase for
    exactly this reason, and the two summaries agreeing on 12 is the run's own
    claim, not this parser's. `hint_ab_check.py` prints the FIRST shape — one
    `hint-ab mutants: 8/8 …` summary and `ok MUTATION:` verdicts that carry a
    check count but no fraction of their own. `ts_perception_check.py --sweep` is
    graph's shape a fourth time: thirteen lowercase `ok   MUTATION:` verdicts, then
    three `13/13`-style summaries (this process, the fresh-process lane, and the
    sweep's own verdict), which agree because the run refuses to print a
    fresh-process total unless the child listed all thirteen.
    `session_check.py --sweep` is that shape a fifth time: sixteen
    `fresh>ok  MUTATION:` verdicts — prefixed, so the `^OK MUTATION` counter never
    sees them — and then three `16/16 caught` summaries (this process, the fresh
    process each, and the sweep's own verdict), which agree because the sweep
    returns 1 unless the child lanes and the in-process lane both caught all
    sixteen.
    """
    best = 0
    for line in text.replace("\r", "\n").split("\n"):
        if not re.search(r"mutation|mutant|defeated", line, re.I):
            continue
        if re.match(r"^\s*OK\s+MUTATION", line):
            best += 1
            continue
        same = [int(n) for n, d in re.findall(r"\b(\d+)/(\d+)\b", line) if n == d]
        if same:
            best = max([best] + same)
    return best


def printed(text: str) -> list:
    """Every n/n the run prints, in order — the evidence line for a mismatch."""
    return [f"{n}/{d}" for n, d in
            re.findall(r"\b(\d+)/(\d+)\b", text.replace("\r", "\n"))]


# What `--backend-free` installs into a child interpreter. A meta_path finder
# rather than an env var, because the claim being tested is about `import mlx`,
# and the only way to test that claim is to make that import fail.
_SHIM = '''"""Installed by benchmarks/battery_reread.py --backend-free."""
import sys


class _NoBackend:
    BLOCKED = ("mlx", "mlx_lm", "mlx_vlm")

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ImportError(
                f"{name} blocked by --backend-free: this run is proving which of "
                f"the battery's vectors need a generative backend")
        return None


sys.meta_path.insert(0, _NoBackend())
'''


def install_backend_block() -> str:
    """Block `mlx` in every child, and PROVE the block is live before trusting a
    single green line that follows.

    The proof is a `python -c "import mlx.core"` under the same environment: if
    it succeeds, the flag is decorative and the run must not print a total — a
    `--backend-free` that blocks nothing is precisely the lie this option exists
    to prevent. If mlx is not importable even WITHOUT the shim, the proof says so
    out loud rather than counting a block that was already in place.
    """
    import os
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="flash-backend-free-"))
    shim = d / "sitecustomize.py"
    shim.write_text(_SHIM)
    prior = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(d) + (os.pathsep + prior if prior else "")
    os.environ["FLASH_BACKEND_SHIM"] = str(shim)
    with_shim = subprocess.run([PY, "-c", "import mlx.core"], cwd=ROOT,
                               capture_output=True, text=True, timeout=120)
    plain_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    without = subprocess.run([PY, "-c", "import mlx.core"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120,
                             env=plain_env)
    if with_shim.returncode == 0:
        print("BAD  --backend-free is decorative: a child imported `mlx.core` "
              "with the shim on PYTHONPATH. No total below is a claim about a "
              "backend-free run.")
        raise SystemExit(1)
    if without.returncode != 0:
        print("note --backend-free: `import mlx.core` also fails WITHOUT the shim "
              "on this box, so this run proves the flag installs a blocker, not "
              "that the vectors survive one (this machine has no backend to lose).")
    else:
        print("proof --backend-free: `import mlx.core` succeeds with a plain env "
              "and raises under the shim, so every line below ran with the "
              "backend genuinely gone.")
    return str(shim)


def has_check(text: str, want: int) -> bool:
    return f"{want}/{want}" in printed(text)


def main(argv: list) -> int:
    backend_free = False
    if "--backend-free" in argv:
        argv = [a for a in argv if a != "--backend-free"]
        backend_free = True
    quick = []
    if argv and argv[0] == "--quick":
        quick = argv[1:]
        if not quick:
            print("--quick needs at least one label to filter on")
            return 2
    if backend_free:
        install_backend_block()
    items = [b for b in BATTERY if not quick
             or any(q in b[0] for q in quick)]
    totals = {"checks": 0, "oracle": 0}
    mutants = 0
    bad = []
    for label, cmd, want, mut, kind in items:
        out = run(cmd)
        if not has_check(out, want):
            # A line that prints no fraction at all usually died on an assertion
            # or a traceback, and "printed []" tells nobody which. Show its last
            # line — this battery has to be runnable by someone who is not
            # watching the terminal while it goes.
            cause = next((l for l in reversed(out.strip().splitlines())
                          if l.strip()), "no output whatsoever")
            bad.append(f"{label}: expected {want}/{want}, printed "
                       f"{printed(out)[:4]} — last line: {cause.strip()[:150]}")
            print(f"BAD  {label:44s} want {want}/{want} got {printed(out)[:4]}")
            print(f"     └─ {cause.strip()[:150]}")
            continue
        extra = ""
        if mut is not None:
            got_mut = mutant_count(out)
            if got_mut != mut:
                bad.append(f"{label}: {want}/{want} came but it defeated "
                           f"{got_mut} mutants, not {mut}")
                print(f"BAD  {label:44s} want {want}/{want} + {mut} mutants, "
                      f"got {got_mut}")
                continue
            mutants += mut
            extra = f" (+ {mut} mutants)"
        totals[kind] += want
        print(f"OK   {label:44s} {want}/{want}{extra}")
    if len(items) != len(BATTERY):
        print(f"\n--quick run of {len(items)}/{len(BATTERY)} lines: totals are "
              f"partial, not a §6 re-read")
        return 1 if bad else 0
    print(f"\nchecks {totals['checks']}  oracle {totals['oracle']}  "
          f"§6 total {totals['checks'] + totals['oracle']}  mutants {mutants}")
    for kind, val in (("checks", totals["checks"]), ("oracle", totals["oracle"]),
                      ("mutants", mutants)):
        if val != CLAIM[kind]:
            bad.append(f"SPEC §6's {kind} claim is {CLAIM[kind]}, the tree prints {val}")
    if bad:
        print("\n".join("  " + b for b in bad))
        return 1
    print("matches SPEC §6 as written: " +
          f"{CLAIM['checks']} + {CLAIM['oracle']} = "
          f"{CLAIM['checks'] + CLAIM['oracle']} green, offline (+ {CLAIM['mutants']} mutants)")
    if backend_free:
        print(f"backend-free: {len(items)}/{len(BATTERY)} lines green with "
              "`mlx`, `mlx_lm` and `mlx_vlm` unimportable in every child process. "
              "SPEC §6's totals above are therefore a claim about a run with no "
              "generative backend, measured rather than asserted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
