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

Two kinds of line cannot answer with a fraction, and the run now says which one it
met rather than calling both a failure: `checkpoint_resume_check.py` refuses unless
the §34.1 governor offers tournament width >= 2 (a hosted runner cannot be told to
cool down), and `--backend-free` has two rows that reach the generative backend —
`flash.grammar`'s mask section, which loads the tokenizer through
`mlx_lm.tokenizer_utils`, and `benchmarks/session_check.py`, whose chat arm walks
past `flash.loop.solve_routed`'s preamble import. Each becomes a REFUSED line
carrying the sentence the row itself died with, and the totals printed under it are
that lane's own subtotal — CLAIM minus the named rows' counts — behind a
`NOT a §6 re-read` banner. A death that names no cause is still BAD, and a named row
that answers with its fraction anyway is counted like any other — the list only
decides what a missing fraction means, so an over-wide name cannot switch a check
this machine made off. The whole tree's §6 total still needs a quiet machine that has
the backend.
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
    ("flash.harness --selftest", "-m flash.harness --selftest", 28, None, "checks"),
    ("flash lsp-selftest", "-m flash.cli lsp-selftest", 22, None, "checks"),
    ("flash power --selftest", "-m flash.cli power --selftest", 22, None, "checks"),
    ("flash jobs --selftest", "-m flash.jobs --selftest", 20, None, "checks"),
    ("flash trace --selftest", "-m flash.cli trace --selftest", 30, None, "checks"),
    ("flash web --selftest", "-m flash.cli web --selftest", 9, None, "checks"),
    ("flash.grammar --selftest", "-m flash.grammar --selftest", 47, None, "checks"),
    ("flash.patches --selftest", "-m flash.patches --selftest", 94, None, "checks"),
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
     "benchmarks/ts_patch_check.py --sweep", 52, 15, "checks"),
    ("benchmarks/patch_landing_check.py",
     "benchmarks/patch_landing_check.py --sweep", 65, 28, "checks"),
    ("benchmarks/session_check.py",
     "benchmarks/session_check.py --sweep", 81, 26, "checks"),
    ("benchmarks/hint_ab_check.py", "benchmarks/hint_ab_check.py",
     14, 8, "checks"),
    ("benchmarks/portable_paths_check.py", "benchmarks/portable_paths_check.py",
     15, 7, "checks"),
    ("benchmarks/backend_free_check.py", "benchmarks/backend_free_check.py",
     48, 14, "checks"),
    ("benchmarks/documented_commands_check.py",
     "benchmarks/documented_commands_check.py", 8, 5, "checks"),
    ("flash.debug --suite", "-m flash.debug --suite", 32, None, "checks"),
    ("flash.patches --suite",
     "-m flash.patches --suite benchmarks/tasks/edit_tasks.jsonl", 60, None, "checks"),
    ("benchmarks/m0_bakeoff.py --dry-run", "benchmarks/m0_bakeoff.py --dry-run",
     20, None, "oracle"),
]

# Predicted BEFORE the run, by hand — that is the field's whole use, and the addition
# is on the page because this pass's first hand-sum came out one short of what 37 OK
# lines add up to: 1345 (the committed total) + 8 `harness` + 7 `patches` + 8
# `patch_landing_check` + 9 `session_check` = 1377 checks, and 157 + 1 + 3 = 161
# mutants. R-7.15c and R-7.15h move three rows on top of that: `patches` 78 → 94
# (+16 for the create verb's 7e section), `patch_landing_check` 61 → 65 (+4 for the
# four clauses a created file has to carry: it lands, it counts against nothing, it
# cannot escape the root, it cannot found the oracle) and `session_check` 58 → 81
# (+23 for the chat arm). 1377 + 16 + 4 + 23 = 1420 checks, and 161 + 6 = 167
# mutants. R-7.16 moves one row: `backend_free_check` 42 → 44 (+2 for the arm that
# re-runs the package-only sweep with `mlx`, `mlx_lm` and `mlx_vlm` unimportable,
# and for the control that proves the block fired in that interpreter) and its
# mutants 10 → 11 (the planted selftest that reaches for the trainer library).
# Then the same R-7.16 moves it a second time, for the REFUSED machinery below:
# `backend_free_check` 44 → 48 (+1 for the decoder read against deaths it must not
# decode, +1 for the lists being checked as battery arithmetic, +1 for the live
# backend-free child whose refusals must match the parent's list, +1 for the plain
# child with the block injected, which must NOT be forgiven) and its mutants 11 → 14
# (one planted per arm, plus the one that makes the flag stop mattering).
# 1420 + 2 + 4 = 1426 checks, 167 + 1 + 3 = 171 mutants. The rows are the authority;
# a CLAIM that disagrees with them is an arithmetic error, and the fix is this
# number, never a row.
CLAIM = {"checks": 1426, "oracle": 20, "mutants": 171}

# Rows that can die for a reason this run names rather than diagnoses, each with the
# sentence it dies with. The lists are decoders, not exemptions: a row that answers
# with its fraction is counted whether or not it appears here, and a death that names
# no cause is a BAD line however many lists carry its label. What a name buys is only
# that the lane can subtract that row's own counts from CLAIM and say so out loud —
# so the lists cannot hide a check, and cannot excuse a bug either.
#
# `backend` is what `--backend-free` measures. Both of these were green for days on
# the author's Mac and red on the first ubuntu runner: `flash.train` reached for the
# installed mlx-lm in its own offline selftest (R-7.16 fixed that one), and these two
# reach it for reasons that are real — one loads a tokenizer through `mlx_lm`, the
# other walks through a loop preamble that imports `mlx.core`.
#
# `machine` is the §34.1 governor: a row whose precondition is live state rather than
# code. It is available in every lane, because a hosted runner is not allowed to be
# cool, and a busy laptop is not allowed to be quiet.
REFUSAL = {
    "backend": {
        "flash.grammar --selftest":
            "the mask section loads the fast tier's tokenizer through "
            "`mlx_lm.tokenizer_utils` and the overhead section builds `mx` arrays",
        "benchmarks/session_check.py":
            "`flash.loop.solve_routed` imports `mlx.core` and `mlx_lm.load` in its "
            "preamble, which the chat arm's stubbed model still walks past",
    },
    "machine": {
        "benchmarks/checkpoint_resume_check.py":
            "the §34.1 governor will not offer tournament width >= 2 on this "
            "machine",
    },
}

# Both spellings of a missing backend, because they are the same fact about it: the
# shim's own sentence when a machine has MLX to lose, and the interpreter's when it
# never had any. Anything else that dies is a bug, not a refusal.
BACKEND_PROBES = ("blocked by --backend-free", "no module named 'mlx")
GOVERNOR_PROBE = "the governor's width"


def refuses(kind: str, out: str) -> str:
    """The line of this run's output that says why the row could not run, or "".

    Matched line by line, not against the whole buffer, so the REFUSED print can show
    the sentence rather than a byte count — the row's own words are the evidence.
    """
    for line in out.splitlines():
        low = line.lower()
        hit = (any(p in low for p in BACKEND_PROBES) if kind == "backend"
               else GOVERNOR_PROBE in low)
        if hit:
            return line.strip()
    return ""


def refusal_map(backend_free: bool) -> dict:
    """Which rows this lane may refuse. The plain lane keeps §6 as its denominator:
    only the machine-state arm is open to it, so a backend-bound row stays a failure
    there instead of being forgiven on the one platform that has the backend."""
    allowed = dict(REFUSAL["machine"])
    if backend_free:
        allowed.update(REFUSAL["backend"])
    return allowed


def lane_claim(refused: list) -> dict:
    """What this run predicts it will total: §6's CLAIM minus the refused rows' own
    counts, read off the rows rather than typed twice."""
    out = dict(CLAIM)
    for _, want, mut, kind in refused:
        out[kind] -= want
        out["mutants"] -= mut or 0
    return out


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
    `session_check.py --sweep` is that shape a fifth time: twenty
    `fresh>ok  MUTATION:` verdicts — prefixed, so the `^OK MUTATION` counter never
    sees them — and then three `20/20 caught` summaries (this process, the fresh
    process each, and the sweep's own verdict), which agree because the sweep
    returns 1 unless the child lanes and the in-process lane both caught all
    twenty.
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
    can_refuse = refusal_map(backend_free)
    kinds = {l: k for k, rows in REFUSAL.items() for l in rows}
    items = [b for b in BATTERY if not quick
             or any(q in b[0] for q in quick)]
    totals = {"checks": 0, "oracle": 0}
    mutants = 0
    bad = []
    refused = []
    for label, cmd, want, mut, kind in items:
        out = run(cmd)
        if not has_check(out, want):
            # A line that prints no fraction at all usually died on an assertion
            # or a traceback, and "printed []" tells nobody which. Show its last
            # line — this battery has to be runnable by someone who is not
            # watching the terminal while it goes.
            cause = next((l for l in reversed(out.strip().splitlines())
                          if l.strip()), "no output whatsoever")
            named = kinds.get(label)
            allowed = bool(named) and label in can_refuse
            said = refuses(named, out) if allowed else ""
            if said:
                refused.append((label, want, mut, kind))
                print(f"REFUSED {label:40s} {named} — {can_refuse[label]}")
                print(f"     └─ {said[:150]}")
                continue
            note = "" if not named else (
                f" — this run calls it {named}-bound"
                + (", and its death did not say so" if allowed
                   else ", which this lane will not refuse"))
            bad.append(f"{label}: expected {want}/{want}, printed "
                       f"{printed(out)[:4]} — last line: {cause.strip()[:150]}"
                       f"{note}")
            print(f"BAD  {label:44s} want {want}/{want} got {printed(out)[:4]}")
            print(f"     └─ {cause.strip()[:150]}{note}")
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
    want_totals = lane_claim(refused)
    print(f"\nchecks {totals['checks']}  oracle {totals['oracle']}  "
          f"§6 total {totals['checks'] + totals['oracle']}  mutants {mutants}")
    for kind, val in (("checks", totals["checks"]), ("oracle", totals["oracle"]),
                      ("mutants", mutants)):
        if val != want_totals[kind]:
            bad.append(f"this run's {kind} claim is {want_totals[kind]}, the tree "
                       f"prints {val}"
                       + (f" (SPEC §6 says {CLAIM[kind]}, less "
                          f"{len(refused)} refused row(s))" if refused else ""))
    if bad:
        print("\n".join("  " + b for b in bad))
        return 1
    if backend_free:
        print(f"backend-free: {len(items) - len(refused)}/{len(BATTERY)} lines "
              "green with `mlx`, `mlx_lm` and `mlx_vlm` unimportable in every "
              + (f"child process, {len(refused)} refused by name "
                 f"({', '.join(l for l, *_ in refused)}). The totals above are "
                 "therefore a claim about the rows a machine with no generative "
                 "backend can answer, measured rather than asserted."
                 if refused else
                 "child process. SPEC §6's totals above are therefore a claim "
                 "about a run with no generative backend, measured rather than "
                 "asserted."))
    if refused:
        print(f"NOT a §6 re-read: {len(refused)} of {len(BATTERY)} rows refused by "
              f"name ({', '.join(l for l, *_ in refused)}), so the totals above are "
              f"this lane's own subtotal — SPEC §6's {CLAIM['checks']} checks and "
              f"{CLAIM['mutants']} mutants, less the refused rows' "
              f"{sum(w for _, w, _, _ in refused)} checks and "
              f"{sum(m or 0 for _, _, m, _ in refused)} mutants. Read §6 whole on a "
              f"machine that has the backend and a governor offering width.")
        return 0
    print("matches SPEC §6 as written: " +
          f"{CLAIM['checks']} + {CLAIM['oracle']} = "
          f"{CLAIM['checks'] + CLAIM['oracle']} green, offline (+ {CLAIM['mutants']} mutants)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
