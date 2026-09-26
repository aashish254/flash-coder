"""Build benchmarks/tasks/p6_tasks.jsonl: PLAN §34.2's live-arm population.

Two halves in one file, because the gate has two clauses and they need
different populations:

  the 8 seeded subtle tasks (benchmarks/gen_subtle_tasks.py) — answers here are
  expected to fail the hidden key, which is what recall is measured over;

  the routine m0 tasks, with a hidden key this script WRITES by running each
  task's shipped reference on fresh legal inputs. The model never sees either
  test (the prompt is the task's prose), so the key is genuinely held out.

The fresh inputs come from flash.confidence._probe_calls — the same legal-shaped
calls the edge stream plans — so the key exercises exactly the argument space the
spec admits. The reference's behavior becomes the expectation, including where
it raises: that is the strict reading, and its bias runs AGAINST the gate (an
answer that handles a case the reference crashes on is scored as failing the
key), so a recall number measured here is a floor, not a flattering average.

A row is written only after its own reference passes the key it was built from
(verify), which is what keeps a bug in THIS script from being scored as a model
failure; tasks where no assertable value comes out are named as dropped rather
than left silently absent.

    python benchmarks/gen_p6_key.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import confidence                    # noqa: E402

KEY_SEED = confidence.HASH_SEEDS[0]

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
        key = (fn, tuple(map(_arg_repr, args)))
        if key in seen:
            continue
        seen.add(key)
        out.append((fn, args))
    return out[:MAX_PROBES]


def _is_empty(a):
    return isinstance(a, (list, dict, str, tuple)) and len(a) == 0


def _arg_repr(v) -> str:
    """A literal for this value whose order does not depend on the hash seed.

    Unordered containers print in member-sorted order. Equal sets and dicts
    keep their meaning under a re-ordering, so nothing about the probe changes —
    but a key whose TEXT churns between two runs of this script is a key nobody
    can diff, and a set argument from a visible test churns with the parent's
    own seed.
    """
    if isinstance(v, (set, frozenset)):
        if not v:
            return "set()"
        return "{" + ", ".join(map(_arg_repr, sorted(v, key=repr))) + "}"
    if isinstance(v, dict):
        return ("{" + ", ".join(f"{_arg_repr(k)}: {_arg_repr(x)}"
                                for k, x in sorted(v.items(), key=lambda kv: repr(kv[0])))
                + "}")
    if isinstance(v, list):
        return "[" + ", ".join(map(_arg_repr, v)) + "]"
    if isinstance(v, tuple):
        return "(" + ", ".join(map(_arg_repr, v)) + ("," if len(v) == 1 else "") + ")"
    return repr(v)


def observe(code: str, fn: str, args: list):
    """What the reference does on this call: ('value', repr) or ('raises', Name).

    Run under a pinned PYTHONHASHSEED, because the probe arguments are echoed
    into the key as reprs and an unpinned child re-orders a set literal every
    run — a key that differs between two runs of this script is not a held-out
    expectation. `-s` rather than `-I`: -I implies -E, which would drop the seed.
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ref.py"
        p.write_text(code)
        prog = (f"import sys, json\nsys.path.insert(0, {str(d)!r})\nimport ref\n"
                f"args = [{', '.join(map(_arg_repr, args))}]\n"
                f"try:\n"
                f"    print(json.dumps(['value', "
                f"repr(getattr(ref, {fn!r})(*args))]))\n"
                f"except BaseException as e:\n"
                f"    print(json.dumps(['raises', type(e).__name__]))\n")
        env = dict(os.environ, PYTHONHASHSEED=str(KEY_SEED))
        env.pop("PYTHONPATH", None)
        r = subprocess.run([sys.executable, "-s", "-c", prog],
                           capture_output=True, text=True, timeout=20, env=env)
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
        lit = ", ".join(map(_arg_repr, args))
        if kind == "raises":
            lines.append(f"try:\n    {fn}({lit})\n"
                         f"    assert False, 'expected {val}'\n"
                         f"except {val}:\n    pass")
        else:
            lines.append(f"assert {fn}({lit}) == {val}")
    key = "\n".join(lines) + "\n" if lines else ""
    # A key of nothing but raise-expectations scores an answer for throwing,
    # which is not ground truth of any kind. t15_parse_log lands here: the
    # probe generator's legal-shaped strings ('', ' ', '-', '1,2') are all
    # malformed log lines, so the reference never returns a value to hold out.
    if key and not any(l.startswith("assert ") for l in key.splitlines()):
        return ""
    return key


def _composition(hidden: str) -> tuple:
    """(value assertions, raise expectations) — the two things a key can be made of."""
    v = sum(1 for l in hidden.splitlines() if l.startswith("assert "))
    r = sum(1 for l in hidden.splitlines() if l.strip().startswith("assert False,"))
    return v, r


def verify(task: dict) -> str | None:
    """Why this row's key is unusable, or None when it is sound.

    A key the shipped reference itself fails cannot be held-out ground truth —
    it is a bug in this script wearing a costume. An uninterpolated function
    name once made every probe look like it raised `NameError`, every routine
    answer "failed" that key, and the gate printed a recall of 2/17 that was
    measuring the generator. This clause is what catches that class of bug
    before the file is written.
    """
    from flash.confidence import HASH_SEEDS, _visible_verdict
    bad = [s for s in HASH_SEEDS
           if not _visible_verdict(task["solution"], task["hidden"], s, 20)]
    if bad:
        return f"reference fails its own key at seed(s) {bad}"
    return None


def main() -> int:
    subtle = [json.loads(l) for l in SUBTLE.read_text().splitlines() if l.strip()]
    m0 = [json.loads(l) for l in M0.read_text().splitlines() if l.strip()]
    rows = [dict(t, gate="subtle") for t in subtle]
    dropped, broken = [], []
    for t in m0:
        hidden = key_for(t)
        if not hidden:
            dropped.append(t["id"])
            continue
        row = {"id": t["id"], "prompt": t["prompt"], "test": t["test"],
               "hidden": hidden, "solution": t["solution"], "gate": "routine"}
        why = verify(row)
        if why:
            broken.append((t["id"], why))
            continue
        rows.append(row)
    if broken:
        for tid, why in broken:
            print(f"  REFUSED {tid}: {why}", file=sys.stderr)
        print(f"wrote nothing: {len(broken)} key(s) unusable", file=sys.stderr)
        return 1
    routine = [r for r in rows if r["gate"] == "routine"]
    # Half the population missing is this script breaking, not tasks being hard.
    # Without this clause a bug that makes every reference look like it raises
    # leaves a well-formed file with no routine rows, and the gate silently
    # loses its false-offer denominator.
    if len(routine) * 2 < len(m0):
        print(f"wrote nothing: {len(routine)}/{len(m0)} routine tasks yielded a "
              f"key — that is a generator bug, not attrition "
              f"(dropped: {dropped})", file=sys.stderr)
        return 1
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} task(s) -> {OUT.relative_to(ROOT)}")
    print(f"  subtle (seeded-bug) tasks: {len(subtle)}")
    print(f"  routine tasks with a differential key: "
          f"{len(routine)}/{len(m0)} of m0")
    if dropped:
        print(f"  dropped (no differential key available — the reference yields "
              f"no assertable value on the probed inputs): {dropped}")
    per = [len(r["hidden"].strip().splitlines()) for r in rows]
    print(f"  hidden key size: min {min(per)}, max {max(per)} lines")
    stats = [_composition(r["hidden"])[0] for r in routine]
    raises = sum(_composition(r["hidden"])[1] for r in routine)
    print(f"  routine keys verified against their own reference: "
          f"{len(routine)}/{len(routine)}   value assertions per key: "
          f"min {min(stats)}, max {max(stats)}   raise expectations total: {raises}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
