"""Build benchmarks/tasks/p6_tasks.jsonl: PLAN §34.2's live-arm population.

Two halves in one file, because the gate has two clauses and they need
different populations:

  the 8 seeded subtle tasks (benchmarks/gen_subtle_tasks.py) — answers here are
  expected to fail the hidden key, which is what recall is measured over;

  the 20 routine m0 tasks, with a hidden key this script WRITES by running each
  task's shipped reference on fresh legal inputs. The model never sees either
  test (the prompt is the task's prose), so the key is genuinely held out.

The fresh inputs come from flash.confidence._probe_calls — the same legal-shaped
calls the edge stream plans — so the key exercises exactly the argument space the
spec admits. The reference's behavior becomes the expectation, including where
it raises: that is the strict reading, and its bias runs AGAINST the gate (an
answer that handles a case the reference crashes on is scored as failing the
key), so a recall number measured here is a floor, not a flattering average.

    python benchmarks/gen_p6_key.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import confidence                    # noqa: E402

M0 = ROOT / "benchmarks" / "tasks" / "m0_tasks.jsonl"
SUBTLE = ROOT / "benchmarks" / "tasks" / "subtle_tasks.jsonl"
OUT = ROOT / "benchmarks" / "tasks" / "p6_tasks.jsonl"
MAX_PROBES = 8


def pick(calls: list) -> list:
    """A spread of the planned calls: the first, the empty cases, and the tail."""
    keep = list(calls[:2])
    keep += [c for c in calls if any(_is_empty(a) for a in c[1])][:2]
    keep += [c for c in calls if len(c[1]) == 2][:2]
    keep += calls[-2:]
    seen, out = set(), []
    for fn, args in keep:
        key = (fn, tuple(map(repr, args)))
        if key in seen:
            continue
        seen.add(key)
        out.append((fn, args))
    return out[:MAX_PROBES]


def _is_empty(a):
    return isinstance(a, (list, dict, str, tuple)) and len(a) == 0


def observe(code: str, fn: str, args: list):
    """What the reference does on this call: ('value', repr) or ('raises', Name)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ref.py"
        p.write_text(code)
        prog = (f"import sys, json\nsys.path.insert(0, {str(d)!r})\nimport ref\n"
                f"args = {args!r}\n"
                f"try:\n"
                f"    print(json.dumps(['value', repr(getattr(ref, fn)(*args))]))\n"
                f"except BaseException as e:\n"
                f"    print(json.dumps(['raises', type(e).__name__]))\n")
        r = subprocess.run([sys.executable, "-I", "-c", prog],
                           capture_output=True, text=True, timeout=20)
    try:
        kind, val = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    if kind == "value" and not _evalable(val):
        return None
    return (kind, val)


def _evalable(repr_str: str) -> bool:
    """Reject a repr nobody can assert against (`<obj at 0x…>`, `<lambda …>`)."""
    return "<" not in repr_str and " at 0x" not in repr_str


def key_for(task: dict) -> str:
    calls = pick(confidence._probe_calls(task["solution"], task["test"]))
    lines = []
    for fn, args in calls:
        obs = observe(task["solution"], fn, args)
        if obs is None:
            continue
        kind, val = obs
        if kind == "raises":
            lines.append(f"try:\n    {fn}({', '.join(map(repr, args))})\n"
                         f"    assert False, 'expected {val}'\n"
                         f"except {val}:\n    pass")
        else:
            lines.append(f"assert {fn}({', '.join(map(repr, args))}) == {val}")
    return "\n".join(lines) + "\n" if lines else ""


def main() -> int:
    subtle = [json.loads(l) for l in SUBTLE.read_text().splitlines() if l.strip()]
    rows = []
    for t in subtle:
        rows.append(dict(t, gate="subtle"))
    dropped = []
    for t in (json.loads(l) for l in M0.read_text().splitlines() if l.strip()):
        hidden = key_for(t)
        if not hidden:
            dropped.append(t["id"])
            continue
        rows.append({"id": t["id"], "prompt": t["prompt"], "test": t["test"],
                     "hidden": hidden, "solution": t["solution"], "gate": "routine"})
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} task(s) -> {OUT.relative_to(ROOT)}")
    print(f"  subtle (seeded-bug) tasks: {sum(1 for r in rows if r['gate'] == 'subtle')}")
    print(f"  routine tasks with a differential key: "
          f"{sum(1 for r in rows if r['gate'] == 'routine')}")
    if dropped:
        print(f"  dropped (no evaluable reference behavior): {dropped}")
    per = [len(r["hidden"].strip().splitlines()) for r in rows]
    print(f"  hidden key size: min {min(per)}, max {max(per)} lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
