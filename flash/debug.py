"""Debugger skill (PLAN §33.2, SPEC R-4.3): watch execution, don't re-guess.

The harness's oracle is a test run, and the feedback it produces is a
traceback — which names the line where a wrong value was *noticed*, not the
line where it was *made*. This module runs the same candidate under a line
tracer in an isolated subprocess and reports what actually happened: the trail
of executed source lines, the value each local took as it changed, and for the
names the failing assert compares, the line that last mutated them. That
digest goes into the retry prompt as `kind="debug"`, so the model repairs the
cause instead of re-guessing the symptom.

Two rules the design is built around:
  * the candidate never runs in-process (R-4.1): the same `-I` subprocess and
    hard timeout `flash.harness` uses;
  * a killed child still has to explain itself. The trail is written as it is
    produced, so a hang or a crash leaves its last executed lines behind —
    which is exactly what an infinite loop needs to be diagnosable.

    python -m flash.debug --selftest        # offline: seeded bugs, no model
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

MAX_LINES = 240          # executed candidate lines kept per session
MAX_VALUE = 48           # characters of any recorded repr
FEEDBACK_CHARS = 2000    # the digest's budget inside a retry prompt

# Runs in the child. `solution.py` holds the candidate, `test.py` the oracle,
# `_dbg_spec.json` the filenames to trace; the trail goes to a side file so a
# kill mid-run still leaves evidence, and the verdict to `_dbg_result.json`.
_DRIVER = '''
import json, os, sys, traceback

MAX_LINES = 240
MAX_VALUE = 48

HERE = os.path.dirname(os.path.abspath(__file__))
CAND = set(json.load(open(os.path.join(HERE, "_dbg_spec.json")))
             ["files"])
log = {}
count = 0


def _src(fn, line):
    try:
        p = fn if os.path.isabs(fn) else os.path.join(HERE, fn)
        return open(p).readlines()[line - 1].strip()
    except Exception:
        return "?"


def _cut(v):
    try:
        s = repr(v)
    except Exception as e:                      # a hostile __repr__ must not hide
        return "<repr error " + type(e).__name__ + ">"
    return s if len(s) <= MAX_VALUE else s[:MAX_VALUE] + "..."


def _changes(frame, key):
    seen = log.get(key)
    if seen is None:
        seen = log[key] = {}
    out = {}
    for name, v in list(frame.f_locals.items()):
        if name.startswith("__"):
            continue
        r = _cut(v)
        if seen.get(name) != r:
            seen[name] = r
            out[name] = r
    return out


def tracer(frame, event, arg):
    """The global hook: only 'call' arrives here, and returning a function is
    what asks for that frame's 'line' events. Returning None blinds the frame —
    which is how the first version of this recorded nothing at all."""
    if event != "call":
        return None
    name = os.path.basename(frame.f_code.co_filename)
    return local if name in CAND else None        # the oracle's lines: no


def local(frame, event, arg):
    global count
    if event != "line" or count >= MAX_LINES:
        return local
    count += 1
    name = os.path.basename(frame.f_code.co_filename)
    line = frame.f_lineno
    src = _src(frame.f_code.co_filename, line)
    d = _changes(frame, (name, frame.f_code.co_name))
    with open(os.path.join(HERE, "_dbg_trail.ndjson"), "a") as out:
        out.write(json.dumps({"f": name, "l": line, "fn": frame.f_code.co_name,
                              "src": src, "set": d}, default=str) + "\\n")
    return local


ns = {}
verdict, where, frames = "pass", "", []
os.chdir(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
test_src = open("test.py").read()
code_src = open("solution.py").read()
try:
    sys.settrace(tracer)
    try:
        exec(compile(code_src, "solution.py", "exec"), ns)
        exec(compile(test_src, "test.py", "exec"), ns)
    finally:
        sys.settrace(None)
except BaseException as e:
    kind = type(e)
    tb, fr = e.__traceback__, []
    while tb is not None:
        fr.append(tb.tb_frame)
        tb = tb.tb_next
    # Only frames inside the candidate or the oracle are worth reporting: a
    # compile error raises in this driver's own frame, and blaming it would
    # send the model to repair code it never wrote.
    inner = [x for x in fr if os.path.basename(x.f_code.co_filename)
             in CAND | {"test.py", "solution.py"}]
    frames = [{"f": os.path.basename(x.f_code.co_filename), "l": x.f_lineno,
               "fn": x.f_code.co_name,
               "locals": {k: _cut(v) for k, v in list(x.f_locals.items())
                          if not k.startswith("__")}} for x in inner]
    if inner:
        last = inner[-1]
        where = "{}:{} {}".format(os.path.basename(last.f_code.co_filename),
                                  last.f_lineno,
                                  _src(last.f_code.co_filename, last.f_lineno))
    else:
        where = " ".join("".join(traceback.format_exception_only(kind, e))
                         .split())[:200]
    verdict = "assert" if kind is AssertionError else kind.__name__

with open(os.path.join(HERE, "_dbg_result.json"), "w") as f:
    json.dump({"verdict": verdict, "where": where, "frames": frames}, f)
'''


# ------------------------------------------------------------------ the digest
@dataclass
class Digest:
    """What execution did, serialised for a model to read."""

    verdict: str = "pass"                 # pass | assert | <ExceptionName> | timeout
    where: str = ""                       # 'solution.py:4  return total // n'
    frames: list = field(default_factory=list)
    trail: list = field(default_factory=list)     # 'solution.py:3 total += v'
    history: list = field(default_factory=list)   # 'total: 0 @:3 -> 10 @:3'
    mutated: list = field(default_factory=list)   # lines that last changed them
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == "pass"

    def as_feedback(self, limit: int = FEEDBACK_CHARS) -> str:
        blocks = [f"EXECUTION VERDICT: {self.verdict} at {self.where or 'unknown'}"]
        if self.note:
            blocks.append(self.note)
        if self.history:
            blocks.append("VALUE HISTORY (what the compared names did):\n  "
                          + "\n  ".join(self.history[-12:]))
        if self.mutated:
            blocks.append("LAST MUTATED AT: " + "; ".join(self.mutated))
        if self.trail:
            blocks.append(f"EXECUTED ({len(self.trail)} steps shown, oldest first):\n  "
                          + "\n  ".join(self.trail[-40:]))
        if self.frames:
            locs = self.frames[-1].get("locals") or {}
            if locs:
                blocks.append("LOCALS AT FAILURE: "
                              + ", ".join(f"{k}={v}" for k, v in
                                          sorted(locs.items())[:14]))
        out = "\n\n".join(blocks)
        return out[-limit:] if len(out) > limit else out


def _compared_names(where: str) -> list[str]:
    """Names the failing line compares, taken from its own source text."""
    expr = re.split(r"\bassert\b", where, 1)[-1]
    out: dict[str, None] = {}
    for w in re.findall(r"[A-Za-z_]\w*", expr):
        out.setdefault(w, None)
    return list(out)[:12]


def _history(executed: list[tuple[str, int, str, str]], names: list[str]) -> list[str]:
    """The recorded value changes of `names`, oldest first, per name."""
    out = []
    for n in names:
        steps, seen = [], None
        for f, l, k, v in executed:
            if k != n or v == seen:
                continue
            seen = v
            steps.append(f"{n}={v} @{f}:{l}")
        if len(steps) > 1:
            out.append(" -> ".join(steps[-6:]))
    return out


# ------------------------------------------------------------------- the runner
def _run(files: dict[str, str], test: str, timeout: int) -> Digest:
    """Trace a candidate file set against its oracle. Never in-process."""
    from flash.harness import _hoist_path_bootstrap
    boot, body = _hoist_path_bootstrap(test)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        names: list[str] = []
        for rel, src in files.items():
            dest = (root / rel).resolve()
            if root not in dest.parents:          # path-escape guard (R-4.1)
                return Digest(verdict="error",
                              where=f"unsafe file path: {rel}")
            names.append(Path(rel).name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src)
        (root / "test.py").write_text(boot.replace("<TMPDIR>", str(root))
                                      + "\n" + body + "\n")
        (root / "solution.py").write_text(files.get("solution.py", ""))
        (root / "_dbg_spec.json").write_text(json.dumps({"files": sorted(set(names))}))
        (root / "_dbg_driver.py").write_text(_DRIVER)
        note, proc = "", None
        try:
            proc = subprocess.run([sys.executable, "-I",
                                   str(root / "_dbg_driver.py")],
                                  capture_output=True, text=True,
                                  timeout=timeout, cwd=str(root))
        except subprocess.TimeoutExpired:
            note = (f"The run exceeded {timeout}s. The trail below is what it "
                    "executed before it was killed — its last lines are where "
                    "it hung or spun.")
        steps, executed = [], []
        try:
            lines = (root / "_dbg_trail.ndjson").read_text().splitlines()
        except Exception:
            lines = []
        for ln in lines:
            try:
                rec = json.loads(ln)
            except Exception:
                continue
            step = f"{rec['f']}:{rec['l']} {rec['src']}"
            for k, v in (rec.get("set") or {}).items():
                if not k.startswith("_"):
                    executed.append((rec["f"], rec["l"], k, v))
            shown = ", ".join(f"{k}={v}" for k, v in sorted(
                (rec.get("set") or {}).items()) if not k.startswith("_"))
            steps.append(f"{step}  [{shown}]" if shown else step)
        try:
            res = json.loads((root / "_dbg_result.json").read_text())
        except Exception:
            res = None
        if res is None:                       # killed, or died before the trace
            tail = steps[-1] if steps else "(no executed line was recorded)"
            return Digest(verdict="timeout" if proc is None else "error",
                          where=tail, trail=steps, note=note or (
                              "" if proc is None else
                              "child exited " + str(proc.returncode) + ": "
                              + (proc.stderr or "").strip()[-200:]))
        names_cmp = _compared_names(res.get("where", ""))
        hist = _history(executed, names_cmp)
        if not hist:
            # The compared names live in the oracle's frame, which is not
            # traced — so fall back to what the candidate itself moved most.
            # That is the story the assert cannot tell: an aliasing bug's
            # `data.pop(old>` has no name in `assert src == {...}`.
            from collections import Counter
            hot = [k for k, _ in Counter(k for _, _, k, _ in executed).most_common(3)]
            hist = _history(executed, hot)
        mut = sorted({h.rsplit("@", 1)[1].strip()
                      for h in hist if "@" in h})
        return Digest(verdict=res["verdict"], where=res["where"],
                      frames=res["frames"], trail=steps, history=hist,
                      mutated=mut, note=note)


def watch(code: str, test: str, timeout: int = 15) -> Digest:
    """Watch a single-file candidate run its oracle."""
    return _run({"solution.py": code}, test, timeout)


def watch_files(files: dict[str, str], test: str, timeout: int = 15) -> Digest:
    """Watch a multi-file set; the oracle's own imports resolve in the package."""
    return _run(files, test, timeout)


# -------------------------------------------------------------------- selftest
# Seeded so the traceback misleads: the assert fires far from the line that
# made the value wrong. Each entry is (code, test, text the digest must blame).
_BUGS: list[tuple[str, str, str]] = [
    ("def top(scores, n=3):\n    ordered = scores\n    ordered.sort(reverse=True)\n"
     "    return ordered[:n]\n",
     "xs = [5, 3, 9, 1]\nassert top(xs) == [9, 5, 3]\nassert xs == [5, 3, 9, 1]\n",
     "ordered.sort"),
    ("def running(items, _acc=[]):\n    for v in items:\n        _acc.append(v)\n"
     "    return sum(_acc)\n",
     "assert running([1, 2]) == 3\nassert running([4]) == 4\n",
     "_acc.append"),
    ("def mean(values):\n    total = 0\n    for v in values:\n        total += v\n"
     "    return total // len(values)\n",
     "assert mean([1, 2, 3, 4]) == 2.5\n",
     "total //"),
    ("def pairs(seq):\n    out = []\n    for i in range(len(seq) - 2):\n"
     "        out.append((seq[i], seq[i + 1]))\n    return out\n",
     "assert pairs([1, 2, 3]) == [(1, 2), (2, 3)]\n",
     "len(seq) - 2"),
    ("def scale(xs, k):\n    out = []\n    for x in xs:\n        out.append(x * k)\n"
     "    return out\n",
     "assert scale([1, 2], 2) == [2, 3]\n",
     "out.append(x * k)"),
]


# ---------------------------------------------------------------- the suite
DBG_TASKS = "benchmarks/tasks/dbg_tasks.jsonl"


def run_suite_check(root: Path | None = None) -> list[tuple[str, bool, str]]:
    """Prove the dbg suite is what R-4.3 claims it is, with no model at all.

    For every task: the stored solution passes, the seeded bug fails, and —
    the premise of the whole arm — the line that made the value wrong is
    ABSENT from the traceback the model would otherwise get and PRESENT in
    the digest. A task that fails that test is not misleading, so it does not
    belong in this suite.
    """
    from flash.harness import diagnose, load_tasks
    path = (root or Path(__file__).resolve().parent.parent) / DBG_TASKS
    out = []
    if not path.exists():
        return [("the seeded-bug suite exists", False, str(path))]
    for t in load_tasks(path):
        sop, _ = diagnose(t["solution"], t["test"])
        kfp, tb = diagnose(t["seeded"], t["test"])
        d = watch(t["seeded"], t["test"])
        fb = d.as_feedback()
        out.append((f"{t['id']}: solution passes", sop, ""))
        out.append((f"{t['id']}: seeded bug fails", not kfp, tb[:60]))
        out.append((f"{t['id']}: the traceback cannot see the cause",
                    t["blame"] not in tb, t["blame"]))
        out.append((f"{t['id']}: the digest names the cause",
                    t["blame"] in fb, t["blame"]))
    return out


def run_selftest() -> int:
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, cond, note: str = "") -> None:
        checks.append((name, bool(cond), str(note)))

    for i, (code, test, blame) in enumerate(_BUGS):
        d = watch(code, test)
        ck(f"bug {i + 1}: the run fails on the oracle's line",
           d.verdict == "assert" and d.where.startswith("test.py:"), d.where)
        ck(f"bug {i + 1}: the digest blames the line that made it wrong",
           blame in " ".join(d.trail + d.mutated + d.history),
           "wanted " + repr(blame))
        ck(f"bug {i + 1}: feedback fits its budget",
           0 < len(d.as_feedback()) <= FEEDBACK_CHARS,
           f"{len(d.as_feedback())} chars")

    d = watch("def spin():\n    n = 0\n    while True:\n        n += 1\n",
              "assert spin() == 0\n", timeout=3)
    ck("an infinite loop reports a killed run, not a blank",
       d.verdict == "timeout" and "killed" in d.note.lower(),
       f"{d.verdict} / {d.note[:50]}")
    ck("the hung run still shows the line it spun on",
       any("n += 1" in s for s in d.trail), f"{len(d.trail)} steps kept")

    d = watch("def f(x):\n    return x / 0\n", "assert f(1) == 0\n")
    ck("a raised error names its frame and its locals",
       d.verdict == "ZeroDivisionError" and d.frames
       and "x" in d.frames[-1]["locals"], d.verdict)

    d = watch("def f(x):\n    return x + 1\n", "assert f(1) == 2\n")
    ck("a correct candidate reports pass, with its trail",
       d.ok and any("return x + 1" in s for s in d.trail), d.verdict)

    import os
    d = watch("import os\nPID = os.getpid()\nANSWER = 1\n", "assert ANSWER == 2\n")
    seen = re.search(r"PID=(\d+)", " ".join(d.trail))
    ck("execution was observed in a child process, not in this one",
       seen is not None and int(seen.group(1)) != os.getpid(),
       seen.group(0) if seen else "no PID in the trail")

    d = watch("def f(:\n    pass\n", "assert True\n")
    ck("a candidate that will not compile is blamed on the candidate",
       d.verdict == "SyntaxError" and "_dbg_driver" not in d.where, d.where)

    ck("a path-escaping file set is refused before anything runs",
       watch_files({"../evil.py": "x = 1"}, "assert True").verdict == "error")

    ck("the trail is capped, however long the run",
       len(watch("s = 0\nfor i in range(4000):\n    s += i\n",
                 "assert s == 1\n").trail) <= MAX_LINES,
       f"cap {MAX_LINES}")

    # the suite's premise, offline: each seeded bug must be invisible to the
    # traceback and named by the digest
    checks.extend(run_suite_check())

    width = max(len(n) for n, _, _ in checks)
    fails = 0
    for name, cond, note in checks:
        fails += 0 if cond else 1
        print(f"  {'ok  ' if cond else 'FAIL'} {name:<{width}}"
              + (f"  [{note}]" if note else ""))
    print(f"\nflash.debug selftest: {len(checks) - fails}/{len(checks)}")
    return 1 if fails else 0


if __name__ == "__main__":                       # pragma: no cover
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())
    print(__doc__)
    raise SystemExit(run_selftest())
