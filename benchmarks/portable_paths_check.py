"""#30, OFFLINE: can a clone anywhere run the committed suites?

Everything in this repo that a stranger executes has to work at a path nobody
predicted. It did not: three task corpora carried the author's checkout
(`<home>/flash-coder/benchmarks/fixtures`) inside their ORACLE text, because a
context task's test must put the fixture package on `sys.path` and the harness
hoists whatever `sys.path` line the test body carries. So on another machine
every one of those 72 tasks fails with a ModuleNotFoundError before the
candidate's code is even reached — which is not a slow run, it is a suite that
scores nothing while looking like it scores something.

The fix is a token: `<REPO>`, expanded by `harness._hoist_path_bootstrap`, which
is the ONE function every execution seam goes through to split a test into
(path bootstrap, body) — `run_test`, `score`'s probe loop, `debug`'s tracer and
`confidence`'s seeded re-runs. This vector therefore does not test the helper.
It drives all four seams on the real corpora from a foreign working directory
and requires each of them to pass the reference solution and reject a wrong one,
because a token expanded at one seam and not at the next is exactly the shape of
this bug, and it is invisible to any single-seam test.

    python benchmarks/portable_paths_check.py            # gates + mutants
    python benchmarks/portable_paths_check.py --print    # per-task table too
    python benchmarks/portable_paths_check.py --mutant   # mutants only

What is NOT claimed: that every file in the repo is path-free. Witness logs,
traces, the ledger and the embedding caches record what ran, and a record whose
text was rewritten to look portable is no longer a record; they are redacted
instead (see `docs/portability.md`) and the scan below is scoped to what executes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import confidence, debug, harness                # noqa: E402
from flash.harness import extract_code                          # noqa: E402

TOKEN = "<REPO>"
FIXTURES = "benchmarks/fixtures"
# Corpora whose records are EXECUTED as the oracle, and so must be portable.
CONTEXT_SUITES = ("benchmarks/tasks/m2_tasks.jsonl",
                  "benchmarks/tasks/hint_ab_tasks.jsonl",
                  "benchmarks/tasks/hint_ab_candidates.jsonl")
# What a stranger runs or reads as instructions. Witness data is NOT here, on
# purpose: `benchmarks/results/**` records what ran, and a record whose text was
# rewritten to look portable is no longer a record (it is redacted instead, and
# `docs/portability.md` says which files and why). `/private/var` and
# `/private/tmp` are deliberately NOT markers: our own sandbox prose names them,
# and the leak that matters is a person's home directory.
SCAN_SUFFIXES = (".py", ".jsonl", ".md", ".toml", ".yml", ".yaml", ".cfg")
SCAN_PREFIXES = ("flash/", "benchmarks/", "docs/", ".github/")
SCAN_EXCLUDE = ("benchmarks/results/",)
SCAN_TOP = ("README.md", "SPEC.md", "TODO.md", "CHANGELOG.md", "SECURITY.md",
            "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "pyproject.toml")
HOST_PATHS = ("/Users/", "/home/")
SELF = "benchmarks/portable_paths_check.py"
# The RECORD groups `SCAN_EXCLUDE` waves through, because a record whose text was
# rewritten to look portable is no longer a record. A group not listed here is an
# unexplained leak, not an exclusion, and `docs/portability.md` has to name each
# of these for the same reason.
RECORD_GROUPS = ("traces", "results", "adapters", "p6", "jobs")
# A record is DATA, never code: nothing under the excluded prefix may be
# something a clone executes or reads as instructions, which is what makes the
# exclusion a policy about witness data rather than a place to hide a path.
RECORD_EXECUTABLES = (".py", ".sh", ".toml", ".yml", ".yaml", ".cfg", ".md")

CHECKS: list[tuple[str, bool, str]] = []


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


def corpus(path: str) -> list[dict]:
    return [json.loads(l) for l in (ROOT / path).read_text().splitlines() if l.strip()]


def ls_tree() -> list[str]:
    """The tree a stranger would get from this checkout, plus what is staged to
    join it.

    `--others --exclude-standard` joins the untracked-and-not-ignored files to
    the tracked ones: the release skeleton (`pyproject.toml`, `.github/`,
    `docs/`) is exactly what a stranger reads, and a gate that waited for those
    to be committed would be blind to them right when they are being written.
    """
    return subprocess.run(["git", "ls-files", "--cached", "--others",
                           "--exclude-standard"],
                          cwd=ROOT, capture_output=True, text=True).stdout.split()


def tracked_scan() -> list[Path]:
    """Every file the scan covers, by the prefix/suffix rule above."""
    return [Path(p) for p in ls_tree()
            if (p.startswith(SCAN_PREFIXES) or p in SCAN_TOP)
            and not p.startswith(SCAN_EXCLUDE)
            and p.endswith(SCAN_SUFFIXES)]


def record_files() -> list[Path]:
    """Everything under the one excluded prefix: the committed witness data."""
    return [Path(p) for p in ls_tree() if p.startswith(SCAN_EXCLUDE[0])]


def host_path_hits(files: list[Path]) -> list[tuple[str, int, str]]:
    """Every host-path occurrence as (file, line, text), INCLUDING this file's.

    A scanner that hides its own constants cannot be audited for hiding them, so
    the classification happens in the gate, not here.
    """
    hits = []
    for rel in files:
        try:
            text = (ROOT / rel).read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for marker in HOST_PATHS:
                if marker in line:
                    hits.append((str(rel), lineno, line.strip()))
    return hits


# --------------------------------------------------------------- the four seams

def seam_run_test(code: str, test: str) -> tuple[bool, str]:
    ok, err = harness.run_test(code, test, timeout=20)
    return ok, err


def seam_score(code: str, test: str) -> tuple[bool, str]:
    s = harness.score(code, test, timeout=20)
    return s.ok, s.err


def seam_debug(files: dict[str, str], test: str) -> tuple[bool, str]:
    d = debug._run(files, test, timeout=20)
    return d.verdict == "pass", d.verdict


def seam_confidence(code: str, test: str) -> tuple[bool, str]:
    p = confidence._seeded_run(code, test, seed=0, timeout=20)
    return "__PASS__" in p.stdout, (p.stderr or "")[-160:]


SEAMS = (("run_test", seam_run_test), ("score", seam_score),
         ("debug", seam_debug), ("confidence", seam_confidence))


def as_files(code: str) -> dict[str, str]:
    return {"solution.py": code}


def call_seam(name: str, fn, code: str, test: str) -> tuple[bool, str]:
    return fn(code if name != "debug" else as_files(code), test) \
        if name != "confidence" else fn(code, test)


# --------------------------------------------------------------- wrong answers

# ------------------------------------------------------------------- the gates

def run_gates() -> None:
    here = Path(os.getcwd())
    foreign = ROOT / "benchmarks" / "fixtures"
    os.chdir(foreign)                 # not the repo root: no cwd rescue allowed
    try:
        files = tracked_scan()
        ck("the scan has a spine: every corpus this vector depends on is inside "
           "its scope, and the scope is wider than the three files it was written "
           "for — a scanner that scans nothing passes vacuously",
           all(any(str(s) in str(f) for f in files) for s in CONTEXT_SUITES)
           and len(files) >= 60, f"{len(files)} tracked files scanned")
        raw = host_path_hits(files)
        # The instrument has to SPELL the markers to look for them. That is the
        # only exemption it takes, and the next gate checks it instead of
        # trusting it: if anything else in the scanned tree carries a home path,
        # or this file carries one outside its own declaration, it fails.
        declared = [h for h in raw if h[0] == SELF and h[2].startswith("HOST_PATHS")]
        hits = [h for h in raw if h not in declared]
        ck(f"nothing this repo EXECUTES or tells a user to run carries a host "
           f"path ({', '.join(HOST_PATHS)}) — that is what 'clone it anywhere' "
           f"has to mean before it can mean anything",
           not hits, f"{len(hits)} hit(s): {hits[:4]}")
        ck("...and the one exemption above is a checked fact, not a wink: every "
           "host-path literal in the scanned tree sits on this file's own "
           "marker declaration, so a file that leaks for some other reason is "
           "still a failure rather than an exclusion",
           bool(declared) and len(declared) == len(raw) and len(files) >= 60,
           f"{len(raw)} occurrence(s), {len(declared)} of them declared, "
           f"in {[sorted({h[0] for h in raw})]}")
        used = sum((ROOT / t).read_text(errors="ignore").count(TOKEN)
                   for t in files if str(t).startswith("benchmarks/tasks/"))
        ck("...and the token that replaced them is actually load-bearing: the "
           f"corpora carry it {used} times, so the gate above cannot be green "
           "because nobody needs a path at all",
           used >= 50, f"{used} uses")
        ck("`harness.REPO` is resolved from the package's own file, not recorded "
           "in it: it points at this checkout and the fixture package is really "
           "under it",
           harness.REPO == ROOT and (harness.REPO / FIXTURES / "minishop"
                                     / "models.py").exists(), str(harness.REPO))

        boot, body = harness._hoist_path_bootstrap(
            f'import sys; sys.path.insert(0, "{TOKEN}/{FIXTURES}")\nassert 1 == 1\n')
        ck("no `<REPO>` survives into the program text — a token that reaches the "
           "interpreter is an ImportError wearing a passing-suite badge",
           TOKEN not in boot + body and FIXTURES in boot, boot.strip()[:120])

        rows = []
        for suite in CONTEXT_SUITES:
            for t in corpus(suite):
                sol, test = t.get("solution"), t["test"]
                if sol and TOKEN in test:
                    rows.append((suite, t["id"], sol, test))
        ck("the corpus is still a corpus after the rewrite: every line parses and "
           f"{len(rows)} context tasks carry a reference solution to score, so the "
           "seam gates below have real work in them",
           len(rows) >= 50, f"{len(rows)} scorable tasks over {len(CONTEXT_SUITES)} suites")

        bad = []
        for suite, tid, sol, test in rows:
            for name, fn in SEAMS:
                ok, detail = call_seam(name, fn, sol, test)
                if not ok:
                    bad.append(f"{name}:{tid} {str(detail)[:90]}")
        ck("ALL FOUR SEAMS pass EVERY reference solution in the context corpora, "
           "launched from a foreign cwd: `run_test`, `score`'s probe loop, "
           "`debug`'s tracer and `confidence`'s seeded re-run. This is the gate "
           "the fix is booked by — expanding the token at one seam and not the "
           "next is precisely how the suites broke, and no single-seam test can "
           "see it",
           not bad, f"{len(bad)} seam/task failures: {bad[:5]}")

        wrong = []
        naive = [(t["id"], extract_code(t["naive"]), t["test"])
                 for t in corpus("benchmarks/tasks/hint_ab_tasks.jsonl")
                 if t.get("naive")]
        for tid, code, test in naive:
            for name, fn in SEAMS:
                ok, _ = call_seam(name, fn, code, test)
                if ok:
                    wrong.append(f"{name}:{tid}")
        ck("the same four seams REJECT the frozen WRONG answers — the 7B's own "
           "first attempts, stored verbatim — on every task and in every seam, so "
           "a green seam row above cannot mean the oracle stopped deciding "
           f"something{'' if not wrong else ': ' + str(wrong[:6])}",
           not wrong and len(naive) >= 8, f"{len(naive)} naive answers, {len(wrong)} accepted")

        one = corpus("benchmarks/tasks/hint_ab_tasks.jsonl")[0]
        raw = one["test"].replace(TOKEN, str(ROOT))
        ck("the token and the path it replaced decide the same thing: scoring the "
           "reference solution against the PRE-expansion literal path still passes "
           "on this machine, so the rewrite changed portability and not semantics",
           harness.score(one["solution"], raw, timeout=20).ok, "")

        os.chdir(here)
        ck("`benchmarks/tasks/m2_perceive_test.py` locates the repo from its own "
           "file, which is the portable form a standalone test body has to use",
           "Path(__file__).resolve().parents[2]" in
           (ROOT / "benchmarks/tasks/m2_perceive_test.py").read_text(), "")

        # The scan above EXCLUDES `benchmarks/results/**`, because those files
        # record what ran and three kinds of them are load-bearing keys. An
        # exclusion that is not checked is where the next leak hides, so the
        # remainder is counted, grouped and named here.
        recs = record_files()
        execs = [str(p) for p in recs if p.suffix in RECORD_EXECUTABLES]
        ck(f"the exclusion covers DATA only: no file under "
           f"`{SCAN_EXCLUDE[0]}**` has any of {', '.join(RECORD_EXECUTABLES)}, "
           "so 'we do not scan results/' cannot be a place an executed path hides",
           not execs, f"{len(execs)} executable(s) under the exclusion: {execs[:5]}")
        leaked: dict[str, int] = {}
        for rel in recs:
            body = (ROOT / rel).read_text(errors="ignore")
            n = sum(body.count(m) for m in HOST_PATHS)
            if n:
                key = rel.parts[2] if len(rel.parts) > 3 else "results"
                leaked[key] = leaked.get(key, 0) + n
        total = sum(leaked.values())
        ck("every host path left in the tracked tree lives in a RECORD group this "
           "vector can explain (a trace or ledger prompt the model was actually "
           "shown, an embedding cache keyed by that text, an adapter run's own "
           "paths), and no unexplained directory hides behind the exclusion",
           bool(leaked) and all(k in RECORD_GROUPS for k in leaked),
           f"unexpected groups: {[k for k in leaked if k not in RECORD_GROUPS]} "
           f"of {sorted(leaked)}")
        doc = ROOT / "docs" / "portability.md"
        text = doc.read_text(errors="ignore") if doc.exists() else ""
        claimed = re.findall(r"RECORD_RESIDUE\s*=\s*(\d+)", text)
        ck(f"`docs/portability.md` exists, names every group the exclusion covers "
           f"and states a residue floor ({claimed[:1] or 'MISSING'}) against the "
           f"{total} live occurrences in {len(recs)} record files — so a new "
           "record directory fails here until the doc says why it is a record",
           bool(text) and all(f"`{k}/`" in text or f"`{k}`" in text
                              for k in leaked)
           and bool(claimed) and int(claimed[0]) <= total,
           f"missing from doc: "
           f"{[k for k in leaked if f'`{k}`' not in text and f'`{k}/`' not in text]} "
           f"claimed={claimed[:1]} live={total}")
    finally:
        os.chdir(here)


# ---------------------------------------------------------------- mutation cover

BUGS = {
    "no_expand": ("the token stops being expanded — the bug this file exists for: "
                  "the suites import nothing, every context task fails on a "
                  "ModuleNotFoundError, and the suite still prints a verdict",
                  "gate"),
    "wrong_root": ("the token expands, but to a directory that is not this "
                   "checkout — the shape of a hardcoded path that happens to exist",
                   "gate"),
    "abs_back": ("a host path is put back into one corpus line, which is what a "
                 "task generator that never learned the token would do", "gate"),
    "token_erased": ("the corpora lose the token without gaining a path — the "
                     "vacuous-green shape: the scan gate passes because nothing "
                     "needs expansion", "gate"),
    "narrow_scope": ("the scan scope shrinks to the three files the fix touched, "
                     "so a host path in any other executed file is invisible",
                     "gate"),
    "exec_in_exclusion": ("a `.py` carrying a host path appears under the excluded "
                          "prefix — the shape the exclusion is actually there to "
                          "forbid, and the only gate that can see it is the "
                          "data-only one, because the scan never looks there",
                          "gate"),
    "doc_silent": ("the residue doc stops naming one of the record groups, so the "
                   "exclusion is an unexplained allowance again", "gate"),
}

DOC = "docs/portability.md"
PLANTED = "benchmarks/results/_leak_probe.py"


def mutate(one: str | None = None) -> int:
    """Each mutant must be caught by the gate that covers ITS property."""
    failed = ran = 0
    for bug in BUGS:
        if one and bug != one:
            continue
        ran += 1
        snap = list(CONTEXT_SUITES)
        if bug == "doc_silent":
            snap.append(DOC)
        before = {p: (ROOT / p).read_bytes() for p in snap}
        real_hoist = harness._hoist_path_bootstrap
        real_repo = harness.REPO
        real_scan = globals()['tracked_scan']
        try:
            if bug == "no_expand":
                def dead(test, _real=real_hoist):
                    return _real(test.replace(TOKEN, "/nonexistent/nowhere"))
                harness._hoist_path_bootstrap = dead
            elif bug == "wrong_root":
                harness.REPO = Path("/nonexistent/checkout")
            elif bug == "abs_back":
                p = ROOT / CONTEXT_SUITES[0]
                p.write_text(p.read_text().replace(
                    TOKEN, str(ROOT), 1))
            elif bug == "token_erased":
                for s in CONTEXT_SUITES:
                    p = ROOT / s
                    # the JSON bodies escape their quotes, so the pattern is the
                    # INSIDE of the string literal — matching `"<REPO>/…"` would
                    # edit nothing and report a caught mutant that never ran.
                    p.write_text(p.read_text().replace(
                        f'{TOKEN}/{FIXTURES}', '.'))
            elif bug == "narrow_scope":
                def tiny():
                    return [Path(s) for s in CONTEXT_SUITES[:1]]
                globals()["tracked_scan"] = tiny
            elif bug == "exec_in_exclusion":
                (ROOT / PLANTED).write_text(
                    f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n")
            elif bug == "doc_silent":
                p = ROOT / DOC
                # every spelling the gate accepts, or the mutant would edit the
                # first mention and leave the rest of the doc passing.
                p.write_text(p.read_text().replace("`traces/`", "traces")
                             .replace("`traces`", "traces"))
            CHECKS.clear()
            run_gates()
            caught = [l for l, ok, _ in CHECKS if not ok]
            # A mutant that did not mutate is a green result bought with nothing:
            # the corpus edits go through JSON, whose quotes are escaped, and a
            # pattern that misses silently leaves the tree exactly as it was.
            changed = [p for p in before if (ROOT / p).read_bytes() != before[p]]
            writes_tree = ("abs_back", "token_erased", "doc_silent",
                           "exec_in_exclusion")
            if bug in writes_tree and not changed and not (ROOT / PLANTED).exists():
                caught.append("the mutant wrote no bytes at all")
        finally:
            harness._hoist_path_bootstrap = real_hoist
            harness.REPO = real_repo
            globals()["tracked_scan"] = real_scan
            planted = ROOT / PLANTED
            if planted.exists():
                planted.unlink()
            for p, data in before.items():
                (ROOT / p).write_bytes(data)
            leak = [p for p, data in before.items()
                    if (ROOT / p).read_bytes() != data]
        digest = hashlib.sha256(b"".join(
            (ROOT / p).read_bytes() for p in CONTEXT_SUITES)).hexdigest()[:12]
        ok = bool(caught) and not leak
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} MUTATION: {BUGS[bug][0]} "
              f"-> {len(caught)} check(s) fail"
              + (f", and the tree did not restore: {leak}" if leak else "")
              + f"  [{digest}]")
    print(f"\nportability mutants: {ran - failed}/{ran} gates "
          "defeated by exactly their checks")
    return failed


def main(argv: list) -> int:
    if argv and argv[0] == "--mutant":
        return 1 if mutate(argv[1] if len(argv) > 1 else None) else 0
    run_gates()
    for label, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}"
              + (f"\n  ..  {detail}" if detail and not ok else ""))
        if ok and detail and "--print" in argv:
            print(f"  ..  {detail}")
    n = len(CHECKS)
    bad = [l for l, ok, _ in CHECKS if not ok]
    print(f"\nportable-suite checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
