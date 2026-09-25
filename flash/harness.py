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
    import tempfile
    if not files:
        return False, "no files extracted (expected '# file: path.py' headers)"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d).resolve()
        for rel, src in files.items():
            dest = (root / rel).resolve()
            if root not in dest.parents:          # path-escape guard
                return False, f"unsafe file path: {rel}"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src)
        return diagnose("", test.replace("<TMPDIR>", str(root)), timeout)


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


def diagnose(code: str, test: str, timeout: int = 15) -> tuple[bool, str]:
    """VERIFY upgrade: find WHICH assert fails and show actual vs expected.

    Runs each top-level assert individually; for the first failure the model
    sees 'GOT: X | WANT: Y' instead of a bare 'AssertionError'. This is the
    signal that makes error-feedback retry actually converge (PLAN §33.1:
    better oracle -> fewer blind retries).
    """
    # Prefix-replay: probe each TOP-LEVEL assert by replaying the exact test
    # prefix up to it, then swapping the assert for a GOT/WANT probe. This
    # preserves (a) state mutation between asserts and (b) asserts nested in
    # try/except blocks — both of which naive line-splitting corrupts (found
    # live: t05 stateful LRU, t20 try/except 'assert False').
    boot, body = _hoist_path_bootstrap(test)
    raw = [l for l in body.splitlines() if l.strip()]
    idx = [i for i, l in enumerate(raw) if l.startswith("assert")]
    if not idx:                          # all checks inside blocks: run whole
        return run_test(code, test, timeout)

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
        prog = (boot + "\n" + code + "\n" + "\n".join(raw[:i]) + "\n"
                + _DIAG.replace("__EXPR_STR__", repr(expr))
                        .replace("__ASSERT__", repr(a))
                        .replace("__EXPR__", expr))
        try:
            r = subprocess.run([sys.executable, "-I", "-c", prog],
                               capture_output=True, text=True, timeout=timeout)
            if r.returncode != 0:
                info = " | ".join(l for l in r.stdout.splitlines()
                                  if l.startswith(("FAILING_ASSERT", "GOT", "WANT", "ERROR")))
                tail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
                return False, (info or tail or f"failed: {a}")[:400]
        except subprocess.TimeoutExpired:
            return False, f"timeout>{timeout}s on: {a}"
    # probes only cover top-level asserts; gate on the full test once
    return run_test(code, test, timeout)
