"""Executable verification harness (PLAN §5 VERIFY, §33: tests are the oracle).

Shared by the agent loop. Candidate code runs in an isolated subprocess
(`python -I`), never in-process. Timeouts are hard. This is the ground truth
every self-modification must beat (§27.4).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TASKS_FILE = Path(__file__).resolve().parent.parent / "benchmarks" / "tasks" / "m0_tasks.jsonl"
CODE_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def load_tasks(path: str | Path | None = None) -> list[dict]:
    with open(path or TASKS_FILE) as f:
        return [json.loads(l) for l in f if l.strip()]


def extract_code(text: str) -> str:
    m = CODE_FENCE.search(text)
    return (m.group(1) if m else text).strip()

FILE_MARKER = re.compile(r"^#\s*file:\s*(\S+)\s*$", re.MULTILINE)


def extract_files(text: str, expected: list[str] | None = None) -> dict[str, str]:
    """Multi-WRITER (M2): parse a response into {relative_path: source}.

    Contract: '# file: path.py' as the first line inside a fenced block.
    Tolerances (live finds, mw suite):
      - models usually put the filename in a heading BEFORE the fence
        ('### file: model.py', '**model.py**', ...): use the nearest
        filename-like line above each block;
      - retry responses often drop the headings entirely: when `expected`
        filenames are known (from attempt 1), assign unnamed blocks to the
        missing names positionally, in response order.
    No usable blocks at all -> single-file fallback {'solution.py': <first
    block>} so the loop degrades gracefully.
    """
    files: dict[str, str] = {}
    unnamed: list[str] = []
    pos = 0
    for m in CODE_FENCE.finditer(text):
        block = m.group(1).strip()
        fm = FILE_MARKER.match(block)
        if fm:
            files[fm.group(1)] = block[fm.end():].strip()
        else:
            above = [l for l in text[pos:m.start()].splitlines() if l.strip()]
            hm = (re.search(r"file:\s*([\w./-]+)", above[-1]) or
                  re.search(r"([\w./-]+\.py)\s*\**\s*$", above[-1])) if above else None
            if hm:                               # named by heading
                files[hm.group(1)] = block
            else:
                unnamed.append(block)
        pos = m.end()
    if expected:                                 # retry: rescue dropped files
        missing = [n for n in expected if n not in files]
        for name, block in zip(missing, unnamed):
            files[name] = block
    if not files:                                # fenceless: 30B often emits
        parts = re.split(r"(?m)^(#\s*file:\s*\S+)\s*$", text)  # raw code +
        for i in range(1, len(parts) - 1, 2):    # '# file:' comment headers
            fm = FILE_MARKER.match(parts[i])
            if fm:
                body = "\n".join(l for l in parts[i + 1].splitlines()
                                 if not l.strip().startswith("```"))
                files[fm.group(1)] = body.strip()
    return files or {"solution.py": extract_code(text)}


def diagnose_files(files: dict[str, str], test: str, timeout: int = 15) -> tuple[bool, str]:
    """VERIFY for multi-WRITER: materialize the file set into a tmp package,
    substitute <TMPDIR> in the test's sys.path bootstrap, then reuse the exact
    same GOT/WANT assert-probing as single-file (one oracle for both shapes).
    """
    s = score_files(files, test, timeout)
    return s.ok, s.err[:400]


def score_files(files: dict[str, str], test: str, timeout: int = 15) -> Score:
    """The same ranking for a file set as `score()` gives one file."""
    import tempfile
    if not files:
        return Score(False, 0, 0,
                     "no files extracted (expected '# file: path.py' headers)")
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        for rel, src in files.items():
            dest = (root / rel).resolve()
            if root not in dest.parents:          # path-escape guard
                return Score(False, 0, 0, f"unsafe file path: {rel}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src)
        return score("", test.replace("<TMPDIR>", str(root)), timeout)


def _hoist_path_bootstrap(test: str) -> tuple[str, str]:
    """Split test into (sys.path bootstrap lines, the rest).

    Candidate code may import fixture modules at top level, so path setup
    must run BEFORE the code, not after it (live find: r02-r04 sweep).
    """
    pathy = [l for l in test.splitlines()
             if l.strip().startswith(("import sys", "sys.path"))]
    rest = [l for l in test.splitlines()
            if l.strip() and not l.strip().startswith(("import sys", "sys.path"))]
    return "\n".join(pathy), "\n".join(rest)


def run_test(code: str, test: str, timeout: int = 15) -> tuple[bool, str]:
    boot, body = _hoist_path_bootstrap(test)
    prog = boot + "\n" + code + "\n\n" + body + "\nprint('__PASS__')\n"
    try:
        r = subprocess.run([sys.executable, "-I", "-c", prog],
                           capture_output=True, text=True, timeout=timeout)
        ok = "__PASS__" in r.stdout and r.returncode == 0
        return ok, ("" if ok else (r.stderr.strip()[-500:] or "no __PASS__"))
    except subprocess.TimeoutExpired:
        return False, f"timeout>{timeout}s"


_DIAG = '''
import sys
ns = globals()
try:
    __ok = bool(__EXPR__)
except Exception as e:
    print("FAILING_ASSERT:", __ASSERT__)
    print("ERROR:", type(e).__name__, e)
    sys.exit(1)
if not __ok:
    print("FAILING_ASSERT:", __ASSERT__)
    if "==" in __EXPR_STR__:
        __l, __r = __EXPR_STR__.split("==", 1)
        for __name, __side in (("GOT", __l), ("WANT", __r)):
            try:
                print(__name + ":", repr(eval(__side.strip(), ns)))
            except Exception as e:
                print(__name + ": <eval error>", e)
    sys.exit(1)
print("OK")
'''

# The `a == b` shape, probed with each side evaluated EXACTLY ONCE. See
# _eq_sides() for why the re-evaluating version above is wrong for these.
_DIAG_EQ = '''
import sys
try:
__SIDES__
    __ok = bool(__c0 == __c1)
except Exception as e:
    print("FAILING_ASSERT:", __ASSERT__)
    print("ERROR:", type(e).__name__, e)
    sys.exit(1)
if not __ok:
    print("FAILING_ASSERT:", __ASSERT__)
    print("GOT:", repr(__c0))
    print("WANT:", repr(__c1))
    sys.exit(1)
print("OK")
'''


def _eq_sides(expr: str) -> str | None:
    """Indented `__c0 = <left>` / `__c1 = <right>` when `expr` is `a == b`.

    The verdict and the printed values must come from ONE evaluation. The
    re-evaluating probe told both arms of the R-3.2 run `GOT: 'high' |
    WANT: 'high'` for a FAILING `assert q.pop() == "high"` — it had already
    consumed the element while deciding, so the second evaluation of the left
    side reported a value the test never got (and raised, in the sibling case).
    Feedback that names no difference costs the retry its whole attempt.
    """
    try:
        import ast
        node = ast.parse(expr, mode="eval").body
    except Exception:
        return None
    if not isinstance(node, ast.Compare) or len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return None
    left = ast.get_source_segment(expr, node.left)
    right = ast.get_source_segment(expr, node.comparators[0])
    if left is None or right is None:
        return None
    return f"    __c0 = {left}\n    __c1 = {right}"


def diagnose(code: str, test: str, timeout: int = 15) -> tuple[bool, str]:
    """VERIFY upgrade: find WHICH assert fails and show actual vs expected.

    Runs each top-level assert individually; for the first failure the model
    sees 'GOT: X | WANT: Y' instead of a bare 'AssertionError'. This is the
    signal that makes error-feedback retry actually converge (PLAN §33.1:
    better oracle -> fewer blind retries).
    """
    s = score(code, test, timeout)
    return s.ok, s.err[:400]


def _probes(code: str, test: str,
            timeout: int) -> tuple[list[tuple[bool, str]], int, bool] | None:
    """One probe per TOP-LEVEL assert, in order, stopping at the first failure.

    Returns `(records, total, ran_all)`, or None when the test has no top-level
    assert — then there is nothing to probe individually and the caller runs the
    whole test instead. `records` is every passing probe plus, when one failed,
    that failure as its last entry.

    Prefix-replay: each assert is decided by replaying the exact test prefix up
    to it, then swapping the assert for a GOT/WANT probe. This preserves (a)
    state mutation between asserts and (b) asserts nested in try/except blocks —
    both of which naive line-splitting corrupts (found live: t05 stateful LRU,
    t20 try/except 'assert False').
    """
    boot, body = _hoist_path_bootstrap(test)
    raw = [l for l in body.splitlines() if l.strip()]
    idx = [i for i, l in enumerate(raw) if l.startswith("assert")]
    if not idx:
        return None
    recs: list[tuple[bool, str]] = []
    for i in idx:
        a = raw[i]
        # assert-with-message: `assert cond, "why"` — the probe must eval
        # only `cond`. ast extracts it exactly (live find: m2_perceive_test).
        try:
            import ast as _ast
            node = _ast.parse(a).body[0]
            expr = _ast.get_source_segment(a, node.test) or a[len("assert "):]
        except Exception:
            expr = a[len("assert "):]
        head = boot + "\n" + code + "\n" + "\n".join(raw[:i]) + "\n"
        sides = _eq_sides(expr)
        if sides is not None:            # `a == b`: one evaluation, both values
            prog = (head + _DIAG_EQ.replace("__SIDES__", sides)
                                   .replace("__ASSERT__", repr(a)))
        else:
            prog = (head + _DIAG.replace("__EXPR_STR__", repr(expr))
                                  .replace("__ASSERT__", repr(a))
                                  .replace("__EXPR__", expr))
        try:
            r = subprocess.run([sys.executable, "-I", "-c", prog],
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return recs + [(False, f"timeout>{timeout}s on: {a}")], len(idx), False
        if r.returncode != 0:
            info = " | ".join(l for l in r.stdout.splitlines()
                              if l.startswith(("FAILING_ASSERT", "GOT", "WANT", "ERROR")))
            tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
            return recs + [(False, info or tail or f"failed: {a}")], len(idx), False
        recs.append((True, ""))
    return recs, len(idx), True


@dataclass
class Score:
    """The oracle's full verdict: pass/fail, and how far the candidate got.

    `passed`/`total` rank candidates that ALL fail, which is what a tournament
    (§33.4) needs and a boolean cannot express: 'which assert did it die on' is
    the difference between a candidate that half-understands the task and one
    that merely parses.
    """
    ok: bool
    passed: int
    total: int
    err: str = ""


def score(code: str, test: str, timeout: int = 15) -> Score:
    """VERIFY for ranking: every top-level assert decided, first failure named.

    Probing stops at the first failure because the real run stops there too — a
    later assert's prefix would have to replay past a failure that aborts it —
    so `passed` means "how far it got before the test would have stopped".
    """
    probed = _probes(code, test, timeout)
    if probed is None:                   # all checks inside blocks: run whole
        ok, err = run_test(code, test, timeout)
        return Score(ok, 0, 0, "" if ok else err)
    recs, total, ran_all = probed
    if ran_all:
        # probes only cover top-level asserts; the full test stays the gate, so
        # a non-assert failure (a crash after the last probe) cannot look green
        ok, err = run_test(code, test, timeout)
        return Score(ok, total, total, "" if ok else err)
    return Score(False, len(recs) - 1, total, recs[-1][1])


# ------------------------------------------------------------------- selftest

def run_selftest() -> int:
    """Offline, deterministic, no model: the oracle's own contract.

    The load-bearing checks are the two that pin ONE evaluation per side — the
    bug made the probe report a value the test never got, which turns a retry
    into a coin flip on a difference that does not exist.
    """
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, cond, note: str = "") -> None:
        checks.append((name, bool(cond), str(note)))

    # a green test is green, with no diagnostic text
    ok, err = diagnose("def double(x):\n    return x * 2\n",
                       "assert double(3) == 6\nassert double(0) == 0\n")
    ck("passing test -> (True, '')", ok and err == "", f"ok={ok} err={err!r}")

    # the common shape: the first failing assert names both values
    ok, err = diagnose("BULK_MIN_QTY = 4\n",
                       "assert BULK_MIN_QTY == 5\nassert BULK_MIN_QTY > 0\n")
    ck("failing == reports GOT vs WANT",
       not ok and "GOT: 4" in err and "WANT: 5" in err, err)

    # THE FIX: the verdict and the printed GOT come from one evaluation, so a
    # side-effecting side reports the value the comparison actually used (the
    # old probe returned 2 here, from a second call the test never made).
    code = ("calls = {'n': 0}\n"
            "def f():\n"
            "    calls['n'] += 1\n"
            "    return calls['n']\n")
    ok, err = diagnose(code, "assert f() == 5\n")
    ck("side-effecting side evaluated EXACTLY ONCE (GOT is the compared value)",
       not ok and "GOT: 1" in err and "WANT: 5" in err, err)

    # the live artifact from the R-3.2 run: for a FAILING `assert q.pop() ==
    # "high"` the old probe printed GOT == WANT, because popping a second time
    # reached the element the first pop had not. Feedback that names no
    # difference burned both arms' second attempt on e09.
    ok, err = diagnose("q = ['high', 'low']\n", 'assert q.pop() == "high"\n')
    ck("failing pop-assert reports the value the pop actually returned",
       not ok and "GOT: 'low'" in err and "WANT: 'high'" in err, err)

    # state between asserts survives: each probe replays the real prefix, so a
    # mutation the 2nd assert depends on has happened exactly once
    code = ("class Box:\n"
            "    def __init__(self):\n"
            "        self.n = 0\n"
            "    def bump(self):\n"
            "        self.n += 1\n"
            "        return self.n\n")
    test = "b = Box()\nassert b.bump() == 1\nassert b.n == 2\n"
    ok, err = diagnose(code, test)
    ck("prefix replay keeps mutation real (2nd assert sees n=1, not 2)",
       not ok and "GOT: 1" in err and "WANT: 2" in err, err)

    # assert-with-message: the probe must evaluate the CONDITION only.
    # `assert (flag, "why")` is a non-empty tuple and always truthy, so a probe
    # that swallows the message reports a real failure as passing.
    ok, err = diagnose("flag = False\n", 'assert flag, "flag must be set"\n')
    ck("assert-with-message still fails (and keeps its message)",
       not ok and "FAILING_ASSERT" in err and "flag must be set" in err, err)
    ok, err = diagnose("flag = True\n", 'assert flag, "flag must be set"\n')
    ck("assert-with-message that holds passes", ok and err == "", f"ok={ok} err={err!r}")

    # a non-comparison condition gets no invented values
    ok, err = diagnose("flag = True\n", "assert not flag\n")
    ck("non-== condition fails without fabricated GOT/WANT",
       not ok and "FAILING_ASSERT" in err and "GOT:" not in err, err)

    # a condition that raises is still an ERROR, not a value mismatch
    ok, err = diagnose("", "assert missing_name == 1\n")
    ck("raising condition reports ERROR", not ok and "ERROR: NameError" in err, err)

    # no top-level assert (t20 class): the whole test runs, verdict stands
    ok, err = diagnose("", "try:\n    raise ValueError('boom')\n"
                           "except ValueError as e:\n    assert str(e) == 'other'\n")
    ck("block-nested-only test still fails with the real traceback",
       not ok and "AssertionError" in err, err)

    # multi-WRITER shares the one oracle (diagnose_files -> diagnose)
    ok, err = diagnose_files({"m.py": "X = 1\n"},
                             "import sys; sys.path.insert(0, '<TMPDIR>')\n"
                             "from m import X\nassert X == 2\n")
    ck("diagnose_files reports the same GOT/WANT", 
       not ok and "GOT: 1" in err and "WANT: 2" in err, err)

    # a file path that escapes the tmp package is refused, not written
    ok, err = diagnose_files({"../evil.py": "X = 1\n"}, "assert 1 == 1\n")
    ck("path-escape guard refuses an out-of-tree file", not ok and "unsafe" in err, err)

    # --- score(): the ranking signal a tournament (§33.4) needs
    test3 = "assert f(2) == 4\nassert f(3) == 9\nassert f(4) == 16\n"
    s = score("def f(x):\n    return x * x\n", test3)
    ck("score: a fully correct candidate is ok with every assert passed",
       s.ok and s.passed == 3 and s.total == 3 and s.err == "",
       f"{s.passed}/{s.total} err={s.err!r}")
    s = score("def f(x):\n    return x + 2\n", test3)          # right for 2 only
    ck("score: dying on the 2nd of 3 asserts scores 1/3",
       not s.ok and s.passed == 1 and s.total == 3, f"{s.passed}/{s.total} {s.err}")
    s = score("def f(x):\n    return 0\n", test3)
    ck("score: dying on the first assert scores 0/3",
       not s.ok and s.passed == 0 and s.total == 3, f"{s.passed}/{s.total} {s.err}")
    s = score("def f(x):\n    return x*#\n", "assert f(2) == 4\n")
    ck("score: an unparsable candidate scores below every running one",
       not s.ok and s.passed == 0, f"{s.passed}/{s.total} {s.err}")
    # every probe passing but the test failing is NOT green: the full test stays
    # the gate (a crash after the last assert is invisible to the probes)
    s = score("def f(x):\n    return x\n", "assert f(2) == 2\nraise SystemExit('boom')\n")
    ck("score: probes all pass but the full test gate still fails it",
       not s.ok and s.total == 1, f"ok={s.ok} {s.passed}/{s.total} err={s.err[:60]!r}")
    s = score("def f(x):\n    return x * x\n",
              "try:\n    assert f(3) == 9\nexcept AssertionError:\n    pass\n")
    ck("score: a test with no top-level assert still gets a verdict", s.ok,
       f"{s.passed}/{s.total}")
    s = score_files({"m.py": "X = 1\n"},
                    "import sys; sys.path.insert(0, '<TMPDIR>')\nfrom m import X\n"
                    "assert X == 1\nassert X == 2\n")
    ck("score_files ranks a file set the same way",
       not s.ok and s.passed == 1 and s.total == 2, f"{s.passed}/{s.total} {s.err}")
    ck("diagnose stays exactly score's verdict",
       diagnose("def f(x):\n    return x + 2\n", test3) == (False, score("def f(x):\n    return x + 2\n", test3).err[:400]))

    for name, ok, note in checks:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}" + (f"  [{note}]" if not ok and note else ""))
    n_bad = sum(not ok for _, ok, _ in checks)
    print(f"\nharness selftest: {len(checks) - n_bad}/{len(checks)} checks passed")
    return 1 if n_bad else 0


if __name__ == "__main__":                       # pragma: no cover
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())
    print(__doc__)
    raise SystemExit(run_selftest())
