"""Prospective confidence from VERIFICATION evidence (PLAN §34.2, SPEC R-2.3).

The trust gap is asymmetric: one subtle logic bug that passes the visible
tests destroys more credibility than ten fast correct completions build. So
BEFORE shipping a passing answer, the loop can ask the machine what it has
actually verified about it — and OFFER the escalation when the evidence is
thin. "Confidence" here is deliberately not the model's own probability:
self-rated prospective confidence is measured dead (§34.2, m7: leave-suite-out
AUC 0.569), while every signal below comes from execution.

Four evidence streams, each a real run, none a guess:

* `static`  — LSP/AST diagnostics on the answer (perceive.static_check).
* `coverage` — what fraction of the answer's statement lines the visible
  tests actually executed (via the §33.2 line tracer on a PASSING run). A
  guard that no test reaches is a guard nobody has ever seen work.
* `seeds`   — re-run the visible tests under several PYTHONHASHSEED values;
  a verdict that flips with dict/set iteration order is a bug the visible
  order happened to hide.
* `edges`   — call the answer's functions on adversarial VALUES of the shape
  its own visible test shows (empty, single, repeated, boundary), holding the
  other arguments where the test held them; a missing guard that survives the
  visible asserts dies here. Wrong-TYPED arguments are not probed: they test
  the interpreter, not the answer (measured: a type-indiscriminate battery
  flagged 8 of 20 correct reference answers), and an answer's own
  `ValueError`/`LookupError` is a decision, not a crash. Arguments are also
  bounded by SIZE: a probe nobody can afford reports our budget, not the
  answer's behavior (measured: `spiral(10000)` at 2.29 s / 1445 MB became a
  `HANG` about a correct reference).

What this CANNOT do, stated where it cannot be missed: a wrong-VALUE bug
that the visible tests neither reach, order-depend, nor crash on is
invisible to every verification signal — the offer would be a guess, and
guesses are what §34.2 killed. The gated suite (`benchmarks/tasks/
subtle_tasks.jsonl`) is seeded from the four detectable classes, and the
limitation ships with the number.

Offline: `python -m flash.confidence --selftest` (no model; real subprocess
runs, scripted answers).
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from flash import trace
from flash.debug import watch
from flash.perceive import static_check

COVERAGE_TAU = 0.55               # measured on benchmarks/tasks/subtle_tasks.jsonl
                                  # and the 20 m0 references: the two seeded
                                  # untested-block answers sit at 0.18 and 0.46,
                                  # every other answer in both populations is
                                  # >= 0.75 — including the correct references of
                                  # those two tasks, whose visible tests are thin
                                  # by construction and so ARE offered, correctly
HASH_SEEDS = (0, 1, 7)            # verdict must be stable across these
EDGE_BUDGET_S = 2                 # per edge call before it is counted as a hang


@dataclass
class Signals:
    """The evidence table one answer earned. `offer` is the decision §34.2
    gates; `reasons` names which stream(s) demanded it, and the numbers that
    did, so a reviewer can replay the judgement without re-running anything."""
    ok: bool = True                       # did the visible oracle pass at all
    static_errors: int = 0
    coverage: float = 1.0                 # executed / executable statement lines
    covered: int = 0
    total: int = 0
    seeds: list = field(default_factory=list)   # verdict per hash seed
    edge_events: list = field(default_factory=list)
    offer: bool = False
    reasons: list = field(default_factory=list)

    def fields(self) -> dict:
        return {"conf_static": self.static_errors, "conf_cov": round(self.coverage, 3),
                "conf_seeds": len(self.seeds), "conf_edges": len(self.edge_events),
                "conf_offer": self.offer, "conf_reasons": ",".join(self.reasons)}

    def describe(self) -> str:
        return (f"offer={'YES' if self.offer else 'no'} "
                f"static={self.static_errors} cov={self.coverage:.2f}"
                f"({self.covered}/{self.total}) seeds={''.join('P' if v else 'F' for v in self.seeds)}"
                f" edges={len(self.edge_events)}"
                + (" (" + "; ".join(self.reasons) + ")" if self.reasons else ""))


def _statement_lines(code: str) -> set[int]:
    """The coverage denominator: statements whose execution is evidence.

    A `def`/`class` line runs at import whether or not its body ever does,
    so counting it would flatter every untested function; docstrings are
    data, not behavior. What remains is exactly the lines that had to RUN
    for the answer to be the one the oracle passed.
    """
    lines: set[int] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return lines
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                continue
            lines.add(getattr(node, "lineno", 0) or 0)
    return {n for n in lines if n}


def coverage_of(code: str, test: str) -> tuple[float, int, int]:
    """Run the PASSING answer under the §33.2 tracer and count which of its
    statement lines execution reached. `f:l` comes from the trail records."""
    d = watch(code, test)
    stmts = _statement_lines(code)
    if not stmts:
        return 1.0, 0, 0
    hit = set()
    for step in d.trail:
        head = step.split(" ", 1)[0]
        f, _, l = head.rpartition(":")
        if f == "solution.py" and l.isdigit():
            hit.add(int(l))
    covered = len(stmts & hit)
    return covered / len(stmts), covered, len(stmts)


def _seeded_run(code: str, test: str, seed: int, timeout: int) -> subprocess.CompletedProcess:
    """The visible oracle once more, with dict/set iteration order moved by
    PYTHONHASHSEED. The program shape is harness.run_test's.

    NOT -I, deliberately: -I implies -E, and -E makes the interpreter ignore
    PYTHONHASHSEED — a seeded re-run under -I is just a random re-run (verified
    on this box: the same -I command gave a different set order every time,
    while a seeded one repeated exactly). Isolation is re-implemented instead:
    PYTHONPATH is dropped from the child env, -s drops the user site, and a
    preamble removes sys.path[0], which is all -I would have done for -c.
    """
    from flash.harness import _hoist_path_bootstrap
    boot, body = _hoist_path_bootstrap(test)
    prog = (boot + "\nimport sys\n"
            "sys.path = [p for p in sys.path if p not in ('', '.')]\n"
            + code + "\n\n" + body + "\nprint('__PASS__')\n")
    env = dict(os.environ, PYTHONHASHSEED=str(seed))
    env.pop("PYTHONPATH", None)
    try:
        # `-` with the program on stdin, not `-c`: a long answer plus a long test
        # as a single argv entry trips E2BIG (`Argument list too long`) and the
        # seeded stream would then be a crash, not a verdict. `python -` puts the
        # script's directory at sys.path[0] exactly like `-c` puts '' there, so
        # the preamble above still removes the one entry -I would have.
        return subprocess.run([sys.executable, "-s", "-"], input=prog,
                              capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return None


def _visible_verdict(code: str, test: str, seed: int, timeout: int) -> bool:
    r = _seeded_run(code, test, seed, timeout)
    return r is not None and "__PASS__" in r.stdout and r.returncode == 0


def _literal(node):
    """The value of a literal argument node, or None when it is computed.

    `literal_eval` covers what a test actually writes — numbers, strings,
    lists, dicts, tuples, True/False/None — and gives up on a name or a call,
    which is exactly the case where this module has no business guessing.
    """
    try:
        v = ast.literal_eval(node)
    except Exception:
        return None
    return v


def _shape(v):
    """The one word that says what kind of value this is, or None when the
    value teaches nothing (None, an empty container, an unfamiliar type)."""
    if isinstance(v, bool) or v is None or isinstance(v, (set, frozenset, tuple)):
        return None
    if isinstance(v, str):
        return "str"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, dict):
        return "dict" if v else None
    if isinstance(v, list):
        if not v:
            return None
        kinds = {_shape(x) for x in v}
        if kinds == {"str"}:
            return "list[str]"
        if kinds == {"int"}:
            return "list[int]"
        if kinds == {"float"}:
            return "list[float]"
        if kinds == {"list[int]"}:
            return "list[list[int]]"
        if kinds == {None} and all(isinstance(x, tuple) and len(x) == 2 for x in v):
            return "list[tuple]"
        return None
    return None


# Adversarial VALUES, one battery per observed shape. Everything here is a
# legal member of the type the task's own test showed — an empty one, a
# repeated one, a boundary one. That is the whole design: a wrong-TYPED
# argument tests the interpreter's error message, not the answer, and the
# first version of this module learned that the expensive way (feeding [] to
# a string function flagged 8 of 20 correct reference answers as suspect).
PROBE_BATTERIES = {
    "str": ["", " ", "x" * 40, "a-b-c", "  pad  ", "A b C", "\n", "1,2", "-"],
    "int": [0, 1, -1, 2, 7, 100, -100, 10 ** 3, -10 ** 3],
    "float": [0.0, -0.0, 1.0, 3.5, -3.5, 0.1],
    "list[int]": [[], [0], [1], [1, 1], [3, 1, 2], [2, 1], [0, 0, 0], [-1, -2],
                  list(range(20))],
    "list[str]": [[], [""], ["a"], ["a", "a"], ["b", "a"], ["Z", "a"], [" a "]],
    "list[float]": [[], [0.0], [-1.5, 2.5], [1e6, -1e6]],
    "list[tuple]": [[], [(1, 2)], [(2, 1)], [(1, 2), (1, 3)], [(1, 2), (1, 2)],
                    [(3, 4), (1, 2)]],
    "list[list[int]]": [[], [[1, 2]], [[2, 3], [1, 4]], [[1, 4], [2, 3]],
                        [[1, 2], [1, 2]], [[5, 6], [1, 2]]],
    "dict": [{}, {"a": 1}, {"b": 2, "a": 1}, {"a": {"b": 1}}],
}

# Which escapes count as a missing guard. A function that answers an
# adversarial input with ValueError/LookupError made a choice; one that dies
# with ZeroDivisionError or IndexError never looked.
GUARD_ERRORS = {"AttributeError", "IndexError", "KeyError", "NameError",
                "ZeroDivisionError", "StopIteration", "TypeError",
                "UnboundLocalError", "RecursionError", "OverflowError",
                "HANG", "TIMEOUT"}

# The probe budget, in argument size. Cost is a property of the ANSWER
# (spiral(n) is quadratic), so it cannot be computed per call — but the
# argument's size is readable, and a battery member too big to afford is a
# hang waiting to be reported as the answer's fault.
PROBE_MAX_INT = 10 ** 3           # the shipped 10 ** 4 made h39's correct
                                  # reference plan spiral(10000): a 10 ** 8
                                  # cell build, measured here at 0.068 s / 54 MB
                                  # for n=1000 and growing as n squared — which
                                  # the 2 s alarm fires through, so the module
                                  # reports `spiral(10000) -> HANG` about an
                                  # answer that is simply correct. The child
                                  # cannot be given a memory ceiling instead:
                                  # setrlimit(RLIMIT_AS, anything finite) raises
                                  # ValueError on this box (verified), so the
                                  # only lever is what we ask for.
PROBE_MAX_ITEMS = 32              # elements in a list, keys in a dict
PROBE_MAX_CHARS = 64              # characters in a string


def _affordable(v) -> bool:
    """Could this probe argument ever finish inside the probe's budget?

    Checked structurally, recursively, and only against size — an argument this
    module is willing to pass. A value that fails here is not adversarial, it
    is unaffordable, and the difference is the whole point: an unaffordable
    probe measures our interpreter, not the answer.
    """
    if isinstance(v, bool):
        return True
    if isinstance(v, int):
        return abs(v) <= PROBE_MAX_INT
    if isinstance(v, float):
        return abs(v) <= 10 ** 6
    if isinstance(v, str):
        return len(v) <= PROBE_MAX_CHARS
    if isinstance(v, dict):
        return (len(v) <= PROBE_MAX_ITEMS
                and all(_affordable(k) and _affordable(x) for k, x in v.items()))
    if isinstance(v, (list, tuple, set, frozenset)):
        return (len(v) <= PROBE_MAX_ITEMS
                and all(_affordable(x) for x in v))
    return True


def _declared_raises(code: str) -> set:
    """Exception names the answer raises ON PURPOSE (`raise TypeError(...)`).

    Those are design decisions the task's prompt made, not crashes: t20's
    reference answers a list with `TypeError('numeric only')` and that is its
    spec speaking. An exception the interpreter raised for it is the accident
    this stream is looking for.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if isinstance(target, ast.Name):
                out.add(target.id)
            elif isinstance(target, ast.Attribute):
                out.add(target.attr)
    return out


def _probe_calls(code: str, test: str) -> list:
    """(function, [args]) pairs to probe, read off the task's OWN visible test.

    For every call the test makes to a top-level function of the answer, each
    argument position is varied through its shape's battery while the other
    positions stay pinned to the values the test used. So a probe is always a
    legal-shaped call the spec might have to survive, never a type-error tour.
    A function the test never calls with a readable literal is not probed:
    no evidence, no offer. A battery member outside the probe budget is not
    planned either — see `_affordable`.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    names = {n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not names:
        return []
    try:
        ttree = ast.parse(test)
    except SyntaxError:
        return []
    calls = []
    seen = set()
    for node in ast.walk(ttree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        fn = node.func.id
        if fn not in names or node.keywords \
                or any(isinstance(a, ast.Starred) for a in node.args):
            continue
        vals = [_literal(a) for a in node.args]
        shapes = [_shape(v) if v is not None else None for v in vals]
        known = [v is not None for v in vals]
        for i, shape in enumerate(shapes):
            for v in PROBE_BATTERIES.get(shape or "", []):
                if not _affordable(v):
                    continue          # too big to afford is not too big to be a bug
                if any(not k for j, k in enumerate(known) if j != i):
                    continue          # another positional arg is computed
                args = list(vals)
                args[i] = v
                key = (fn, tuple(map(repr, args)))
                if key in seen:
                    continue
                seen.add(key)
                calls.append((fn, args))
    return calls


def edge_probe(code: str, test: str, budget: int = EDGE_BUDGET_S) -> list:
    """Crash/hang evidence from adversarial arguments of the declared shape.
    Returns [(function, arguments as written, exception-or-HANG)]; an empty list
    means every probed call survived — which is evidence, not absence of bugs."""
    calls = _probe_calls(code, test)
    if not calls:
        return []
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "answer.py").write_text(code)
        driver = _edge_script(root=str(root), budget=budget, calls=calls)
        (root / "_edge_driver.py").write_text(driver)
        try:
            r = subprocess.run([sys.executable, "-I", str(root / "_edge_driver.py")],
                               capture_output=True, text=True, timeout=budget * len(calls) + 15)
        except subprocess.TimeoutExpired:
            return [("<whole probe>", "*", "TIMEOUT")]
        try:
            out = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception:
            # The child died in a way that is OURS, not the answer's. The raw
            # stderr of a traceback is multi-line and full of temp paths, so it
            # goes into the evidence slot flattened to one line.
            return [("<probe crashed>", "",
                     " ".join((r.stderr or "").split())[-120:]
                     or f"no output, exit {r.returncode}")]
        if out.get("import_crash"):
            return [("<module import>", "", out["import_crash"])]
        return [tuple(e) for e in _filtered(out["events"])]


def _edge_script(root: str, budget: int, calls: list) -> str:
    """The child program: import the answer, make each planned call under an
    alarm, and log every escape with the line that raised it. The child judges
    nothing — triage happens in `_filtered`, where it can be tested."""
    return f'''
import json, signal, sys, traceback
sys.path.insert(0, {root!r})

# An answer whose module body raises on import is a broken answer, and the
# probe's child dies on `import answer` before it can log anything. Left
# unguarded that death reached the parent as a raw stderr tail — a temp path
# and a mid-traceback newline printed inside the arm's one-line evidence, i.e.
# our report of an answer-level fact, unreadable.
IMPORT_CRASH = ""
try:
    import answer
except BaseException as e:                          # noqa: BLE001
    IMPORT_CRASH = " ".join((type(e).__name__ if not str(e) else
                             f"{{type(e).__name__}}: {{e}}").split())[:120]

class Hang(Exception):
    pass

def _alarm(sig, frm):
    raise Hang()

signal.signal(signal.SIGALRM, _alarm)
CALLS = {calls!r}

events = []
def _raise_line(e):
    try:
        fr = traceback.extract_tb(e.__traceback__)[-1]
        return open(fr.filename).read().splitlines()[fr.lineno - 1].strip()
    except Exception:
        return ""

for name, args in ([] if IMPORT_CRASH else CALLS):
    fn = getattr(answer, name, None)
    if fn is None:
        continue
    # The log carries the call as it would be WRITTEN, not the repr of the arg
    # list: `f([[]])` reads as a nested list when the argument was an empty
    # list, and a reason string nobody can read is not evidence.
    shown = ", ".join(map(repr, args))[:60]
    signal.alarm({budget})
    try:
        fn(*args)
    except Hang:
        events.append([name, shown, "HANG", "", ""])
    except BaseException as e:                      # noqa: BLE001 — triage is the job
        events.append([name, shown, type(e).__name__, str(e)[:80],
                       _raise_line(e)])
    finally:
        signal.alarm(0)
print(json.dumps({{"events": events, "import_crash": IMPORT_CRASH}}))
'''


def _counts_as_guard(kind: str, raise_line: str) -> bool:
    """Would this escape be reported as missing-guard evidence?

    Two filters, both learned the expensive way. The exception has to name an
    accident the answer never looked for (`GUARD_ERRORS`), and the line that
    raised it must not be a `raise` the answer wrote for itself — t20's
    reference answers a bad element with `raise TypeError('numeric only')`,
    which is its spec speaking, and the first version of this module counted
    that as a crash on 8 of 20 routine reference answers.
    """
    if kind not in GUARD_ERRORS:
        return False
    return not raise_line.startswith("raise ")


def _filtered(raw: list) -> list:
    """The child's full escape log, triaged into the events this module reports."""
    out = []
    for name, args, kind, msg, line in raw:
        if kind == "HANG":
            out.append([name, args, "HANG"])
        elif _counts_as_guard(kind, line):
            # `str(e)` can carry newlines — an answer that interpolates a
            # multi-line value into the message it raises does — and a reason
            # that spans lines breaks the arm's one-line-per-task log.
            out.append([name, args, " ".join(f"{kind}: {msg}".split())])
    return out


def evaluate(code: str, test: str, timeout: int = 15,
             tau: float = COVERAGE_TAU) -> Signals:
    """The prospective verdict on an answer the visible oracle already passed.

    Offer = ANY thin strand of evidence: one static error, coverage under
    tau, a verdict that moves with hash order, or an adversarial call that
    crashed. The gate (§34.2) wants ≥90% recall on would-fail-hidden outputs;
    a cheap, high-recall trigger is the correct shape — precision against
    routine tasks is the <1-per-20 clause, and each reason is recorded so a
    false offer can be traced to the strand that fired.
    """
    sig = Signals()
    diags = [x for x in static_check(code) if x.severity == "error"]
    sig.static_errors = len(diags)
    if sig.static_errors:
        # An answer that doesn't even parse cannot be executed, so the other
        # three streams would measure nothing but the failure to launch. The
        # offer is already demanded; running five subprocesses to re-derive
        # that is noise (and the edge probe's crash report buries the reason).
        sig.reasons.append(f"static: {sig.static_errors} error(s)")
        sig.offer = True
        trace.event("confidence", offer=True, reasons="; ".join(sig.reasons),
                    static=sig.static_errors, cov=None, seeds=[], edges=[])
        return sig
    sig.coverage, sig.covered, sig.total = coverage_of(code, test)
    sig.seeds = [_visible_verdict(code, test, s, timeout) for s in HASH_SEEDS]
    sig.edge_events = edge_probe(code, test)
    if sig.coverage < tau:
        sig.reasons.append(f"coverage: {sig.covered}/{sig.total} lines executed")
    if len(set(sig.seeds)) > 1:
        sig.reasons.append("seeds: verdict moves with hash order")
    if sig.edge_events:
        fn, arg, exc = sig.edge_events[0]
        sig.reasons.append(f"edges: answer does not import ({exc})"
                           if fn == "<module import>"
                           else f"edges: {fn}({arg}) -> {exc}")
    sig.offer = bool(sig.reasons)
    trace.event("confidence", offer=sig.offer, reasons="; ".join(sig.reasons) or None,
                static=sig.static_errors, cov=round(sig.coverage, 3),
                seeds=sig.seeds, edges=sig.edge_events[:5])
    return sig


# ------------------------------------------------------------------ selftest

_ANS_CLEAN = (
    "def total_cents(qty, unit=25):\n"
    "    return qty * unit\n"
)
_T_CLEAN = "assert total_cents(2) == 50\nassert total_cents(3) == 75\n"

# untested block: the visible tests exercise slugify only; export_report's
# body — six statements — is code that ships without ever having executed
_ANS_UNCOVERED = (
    "def slugify(text):\n"
    "    return text.strip().lower().replace(' ', '-')\n"
    "\n"
    "def export_report(rows):\n"
    "    out = []\n"
    "    for r in rows:\n"
    "        if r is None:\n"
    "            raise ValueError('hole')\n"
    "        out.append(r)\n"
    "    return out\n"
)
_T_UNCOVERED = "assert slugify('A B') == 'a-b'\nassert slugify(' x ') == 'x'\n"

# order-dependent: picks the first member of a set; the visible assert bakes
# in one hash order (measured: P at seed 0, F at 1 and 7 for this key set)
_ANS_ORDER = (
    "def pick_tag(tags):\n"
    "    s = set(tags)\n"
    "    s.update(['alpha', 'bravo', 'charlie', 'delta', 'echo'])\n"
    "    return next(iter(s))\n"
)
_T_ORDER = "assert pick_tag([]) == 'alpha'\n"

# missing guard that survives visible asserts and dies on an empty input
_ANS_NO_GUARD = (
    "def mean(values):\n"
    "    t = 0\n"
    "    for v in values:\n"
    "        t += v\n"
    "    return t / len(values)\n"
)
_T_NO_GUARD = "assert mean([2, 4]) == 3\nassert mean([1]) == 1\n"

# a quadratic reference, i.e. the answer shape whose COST is what the probe
# budget is measured against (h39_spiral_matrix, trimmed to its reading)
_ANS_SPIRAL = (
    "def spiral(n):\n"
    "    m = [[0] * n for _ in range(n)]\n"
    "    return m\n"
)
_T_SPIRAL = "assert spiral(2) == [[1, 2], [4, 3]]\n"

# the shape of the 7B's answer for h15_shell_split, read off the arm's own log
# line (the trace keeps the evidence, not the source): a right-looking function
# with a self-check at module level asserting that the function RETURNS its
# exception class. Importing it raises, so the probe child died at `import
# answer` before printing its JSON line, and the parent filed the last 120 chars
# of the child's stderr as an edge finding. What that printed, verbatim:
#   edges: <probe crashed>() -> rt shell_split("unmatched 'quote") == ValueError
#              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#   AssertionError
_ANS_IMPORT_CRASH = (
    "def shell_split(text):\n"
    "    return text.split()\n"
    "rt = __import__('sys').modules[__name__]\n"
    "assert rt.shell_split(\"unmatched 'quote\") == ValueError\n"
)
_T_IMPORT_CRASH = "assert shell_split('a b') == ['a', 'b']\n"

# a probe child that dies before it can print its JSON line — the parent's own
# last resort. os._exit is not catchable, so nothing here can be graceful; what
# matters is that the raw stderr (multi-line, temp paths) reaches the log
# flattened, because the arm's contract is one readable line per task.
_ANS_PROBE_DEATH = (
    "import os, sys\n"
    "def f(x): return x\n"
    "sys.stderr.write(chr(10).join(['Traceback (most recent call last):',"
    " '/private/var/folders/x/answer.py line 9 in <module>', '']))\n"
    "os._exit(1)\n"
)


def _hashseed_ignored_under(flag: str) -> bool:
    """Premise of the seeds stream, re-checked every run: with `-I` the
    interpreter skips PYTHON* env vars, PYTHONHASHSEED included, so the same
    command gives a different hash each time. `-s` keeps the seed live."""
    outs = set()
    for _ in range(2):
        r = subprocess.run([sys.executable, flag, "-c", "print(hash('a'))"],
                           capture_output=True, text=True, timeout=15,
                           env=dict(os.environ, PYTHONHASHSEED="0"))
        outs.add(r.stdout)
    return len(outs) > 1


def run_selftest() -> int:
    checks = []

    def ck(name, cond, note=""):
        checks.append((name, bool(cond), str(note)))
        print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f"  [{note}]" if note and not cond else ""))

    ck("_statement_lines: a def-line runs at import, so it is not evidence",
       _statement_lines("def f():\n    return 1\n") == {2})
    ck("_statement_lines: docstrings are not evidence",
       _statement_lines('def f():\n    """note"""\n    return 1\n') == {3})

    s = evaluate(_ANS_CLEAN, _T_CLEAN)
    ck("a clean, fully-tested answer gets NO offer", not s.offer, s.describe())
    ck("clean answer: coverage is total", s.coverage == 1.0, s.describe())

    s = evaluate(_ANS_UNCOVERED, _T_UNCOVERED)
    ck("an answer with a block the tests never reach is offered on coverage",
       s.offer and any(r.startswith("coverage") for r in s.reasons), s.describe())
    ck("coverage names the fraction", s.covered == 1 and s.total == 7,
       f"{s.covered}/{s.total}")

    s = evaluate(_ANS_NO_GUARD, _T_NO_GUARD)
    ck("missing guard: edges fire even though visible tests pass coverage",
       any(r.startswith("edges") for r in s.reasons), s.describe())
    ck("the edge event names the function, arg and exception",
       any(e[0] == "mean" and "ZeroDivision" in e[2] for e in s.edge_events),
       str(s.edge_events))

    s = evaluate(_ANS_ORDER, _T_ORDER)
    ck("hash-seed instability is detected as a moving verdict",
       s.offer and any("seeds" in r for r in s.reasons)
       and s.seeds == [True, False, True], s.describe())

    ck("-I would break the seeds stream: it ignores PYTHONHASHSEED",
       _hashseed_ignored_under("-I"))
    ck("-s keeps the seed live, so a seeded re-run is reproducible",
       not _hashseed_ignored_under("-s"))

    # --- the edge probe's shape discipline (its first version had none) ------
    ck("_shape: what a test's literal arguments teach",
       _shape([2, 4]) == "list[int]" and _shape("ab") == "str" and _shape(3) == "int"
       and _shape([]) is None and _shape(True) is None and _shape({"a": 1}) == "dict")
    _RW = "def reverse_words(text):\n    return ' '.join(text.split()[::-1])\n"
    _T_RW = "assert reverse_words('a b') == 'b a'\n"
    plan_rw = _probe_calls(_RW, _T_RW)
    ck("_probe_calls: a string function is fed strings only — [] and 0 and None "
       "are not evidence about it",
       plan_rw and all(isinstance(a[0], str) for _, a in plan_rw), str(plan_rw[:3]))
    ck("_probe_calls: a test that passes a variable teaches nothing, so nothing "
       "is probed", _probe_calls(_RW, "assert reverse_words(xs) == 'b a'\n") == [])
    ck("_probe_calls: the empty container IS in the plan for a list-taking "
       "function — the guard class needs it",
       any(a == [[]] for _, a in _probe_calls(_ANS_NO_GUARD, _T_NO_GUARD)),
       str(_probe_calls(_ANS_NO_GUARD, _T_NO_GUARD)[:2]))
    ck("_counts_as_guard: an accident counts, a `raise` the answer wrote does not",
       _counts_as_guard("ZeroDivisionError", "return t / len(values)")
       and not _counts_as_guard("TypeError", "raise TypeError('numeric only')")
       and not _counts_as_guard("ValueError", "raise ValueError('bad')")
       and _counts_as_guard("HANG", ""))
    ck("_filtered: the child's raw log becomes the events, with the reason string",
       _filtered([["f", "[]", "IndexError", "list index out of range", "return v[1]"],
                  ["g", "0", "ValueError", "nope", "raise ValueError('nope')"]])
       == [["f", "[]", "IndexError: list index out of range"]])
    s = evaluate(_ANS_NO_GUARD, _T_NO_GUARD)
    ck("an edge reason prints the call as written — mean([]), not mean([[]])",
       any("mean([])" in r for r in s.reasons)
       and not any("[[]]" in r for r in s.reasons), "; ".join(s.reasons))
    s = evaluate(_ANS_IMPORT_CRASH, _T_IMPORT_CRASH)
    ck("an answer whose own module body raises is an import crash, not a probe "
       "crash — the child used to die with it and file the raw traceback",
       s.edge_events == [("<module import>", "", "AssertionError")],
       str(s.edge_events))
    ck("the import crash reads as a finding about the answer and cannot leak a "
       "temp path or a newline into the arm's one-line-per-task log",
       any("answer does not import" in r for r in s.reasons)
       and all("\n" not in r and "tmp" not in r for r in s.reasons),
       "; ".join(s.reasons))
    ck("_filtered flattens a multi-line exception message (an answer that "
       "interpolates a multi-line value into its raise does)",
       _filtered([["f", "[]", "TypeError", "bad rows: line1\n   line2",
                   "return x"]])
       == [["f", "[]", "TypeError: bad rows: line1 line2"]])
    dead = edge_probe(_ANS_PROBE_DEATH, "assert f(1) == 1\n")
    ck("a probe child that dies before printing its JSON line reports one "
       "flattened line, not a raw traceback with temp paths",
       len(dead) == 1 and "\n" not in dead[0][2] and "<probe crashed>" == dead[0][0],
       str(dead))

    # --- the probe BUDGET: an argument we cannot afford is not evidence ------
    # h39_spiral_matrix's shipped reference, measured on this box: the battery's
    # 10 ** 4 entry planned spiral(10000), the probe child took 2.29 s and
    # 1445 MB and the 2-second alarm fired, so edge_probe reported
    # ('spiral', '10000', 'HANG') about an answer that is CORRECT — our budget
    # failing, written into the trace as the answer's bug.
    ck("_affordable: size is checked, and the largest int is the stated bound",
       _affordable(10 ** 3) and not _affordable(10 ** 4)
       and _affordable("x" * PROBE_MAX_CHARS) and not _affordable("x" * 10 ** 3)
       and _affordable(list(range(PROBE_MAX_ITEMS)))
       and not _affordable(list(range(PROBE_MAX_ITEMS + 1))))
    ck("_affordable: a nested value is only as cheap as its biggest member",
       _affordable([[1, 2]] * 4) and not _affordable({"k": list(range(200))}))
    ck("every shipped battery member is inside the probe budget by construction",
       all(_affordable(v) for vals in PROBE_BATTERIES.values() for v in vals),
       [v for vals in PROBE_BATTERIES.values() for v in vals if not _affordable(v)])
    _saved = dict(PROBE_BATTERIES)
    PROBE_BATTERIES["int"] = [100, 10 ** 4]
    try:
        ck("_probe_calls: a battery member over budget is never planned — the "
           "bound lives in the planner, so a hand-edited battery cannot re-open "
           "the hang",
           [v for _, a in _probe_calls(_ANS_SPIRAL, _T_SPIRAL) for v in a]
           == [100], str(_probe_calls(_ANS_SPIRAL, _T_SPIRAL)))
    finally:
        PROBE_BATTERIES.clear()
        PROBE_BATTERIES.update(_saved)

    s = evaluate("def f(:\n    pass\n", "assert True\n")
    ck("unparsable answer is a static error, offered without any subprocess",
       s.static_errors >= 1 and s.offer and s.reasons == [f"static: {s.static_errors} error(s)"]
       and s.seeds == [] and s.edge_events == [], s.describe())

    ck("signals serialize for the ledger with every number behind the offer",
       set(Signals(offer=True, reasons=["x"]).fields()) ==
       {"conf_static", "conf_cov", "conf_seeds", "conf_edges", "conf_offer",
        "conf_reasons"})
    ck("describe prints the evidence table in one line",
       "cov=" in Signals(coverage=0.5, covered=2, total=4).describe())

    n_bad = sum(not ok for _, ok, _ in checks)
    print(f"\nconfidence selftest: {len(checks) - n_bad}/{len(checks)} checks passed")
    return 1 if n_bad else 0


if __name__ == "__main__":                       # pragma: no cover
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        raise SystemExit(run_selftest())
    print(__doc__)
