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

    python benchmarks/gen_p6_key.py            # p6_tasks.jsonl, the 23-row arm
    python benchmarks/gen_p6_key.py --wide     # p6b_tasks.jsonl, every keyed suite
    python benchmarks/gen_p6_key.py --out PATH # anywhere else, same rules

`--wide` exists because R-2.3's recall clause had an EMPTY denominator: the 7B
answered all 15 keyed routine tasks correctly, so ">= 90% of would-fail-hidden"
was untested rather than passed. Routine rows keep the `gate: "routine"` tag and
hard-suite rows are tagged `hard`, so widening the recall population cannot move
the per-20-routine-tasks false-offer clause the shipped gate is written against.
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

SUBTLE = ROOT / "benchmarks" / "tasks" / "subtle_tasks.jsonl"
OUT = ROOT / "benchmarks" / "tasks" / "p6_tasks.jsonl"
MAX_PROBES = 8

# `--wide` mines keys from every brief-to-code suite that ships a reference,
# because R-2.3's recall clause needs a denominator and 15 routine tasks cannot
# carry one: at the 7B all 15 answered correctly under a strict key, so recall
# was 0/0. Multi-file (mw) and change-request (edit) suites are deliberately
# absent — their answer shape is not the one the key prober walks.
#
# The tag survives, it does not blur: rows from ROUTINE suites stay `gate
# "routine"` (the false-offer clause is stated per 20 ROUTINE tasks) and rows
# from HARD suites are tagged `hard`, so widening the recall denominator cannot
# silently move the routine population the other clause is measured over.
ROUTINE_SUITES = ["m0_tasks", "m2_tasks"]
HARD_SUITES = ["m3_hard_tasks", "m3b_hard_tasks", "m4_heldout_tasks",
               "m5_heldout_tasks", "m6_heldout_tasks", "m7_heldout_tasks"]
WIDE_SUITES = ROUTINE_SUITES + HARD_SUITES
WIDE_OUT = ROOT / "benchmarks" / "tasks" / "p6b_tasks.jsonl"
MIN_WIDE_TOTAL = 40
MIN_WIDE_HARD = 20

# The largest expectation this script will write into a key, in characters.
# Without it a probe on a quadratic answer is not a held-out expectation but a
# multi-megabyte literal: h39_spiral_matrix's 10 ** 4-int battery entry made
# `assert spiral(10000) == [[1, 2, …` print 943 MB of repr before the parent
# gave up, and the key that came out of it timed out against its own reference
# at every seed. Oversized probes are counted and reported, never written.
MAX_EXPECTATION_CHARS = 2000
OVERSIZED: list = []

# Probes skipped because the task's own visible test already asserts them — the
# key would be re-stating evidence the gate already has. Reported, never silent.
NOVELTY: list = []


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

    The child truncates the repr it prints, because the parent would otherwise
    read a 943 MB expectation into memory to discover it cannot be written: an
    over-budget value comes back as the sentinel below and the probe is dropped.
    """
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "ref.py"
        p.write_text(code)
        prog = (f"import sys, json\nsys.path.insert(0, {str(d)!r})\nimport ref\n"
                f"args = [{', '.join(map(_arg_repr, args))}]\n"
                f"try:\n"
                f"    r = repr(getattr(ref, {fn!r})(*args))\n"
                f"    print(json.dumps(['value', r[:{MAX_EXPECTATION_CHARS + 1}], "
                f"len(r)]))\n"
                f"except BaseException as e:\n"
                f"    print(json.dumps(['raises', type(e).__name__, 0]))\n")
        env = dict(os.environ, PYTHONHASHSEED=str(KEY_SEED))
        env.pop("PYTHONPATH", None)
        try:
            # The program goes in on stdin: as one argv entry it can trip E2BIG,
            # and a key generator that dies on one task loses the whole suite.
            r = subprocess.run([sys.executable, "-s", "-"],
                               input=prog, capture_output=True, text=True,
                               timeout=20, env=env)
        except OSError:
            return None
    try:
        kind, val, full_len = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None
    if kind == "value" and full_len > MAX_EXPECTATION_CHARS:
        return ("oversized", str(full_len))
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
            line = (f"try:\n    {fn}({lit})\n"
                    f"    assert False, 'expected {val}'\n"
                    f"except {val}:\n    pass")
        elif kind == "oversized":
            OVERSIZED.append(f"{task['id']}:{fn}({lit[:24]}…) value {val} chars")
            continue
        else:
            line = f"assert {fn}({lit}) == {val}"
        if len(line) > MAX_EXPECTATION_CHARS:
            OVERSIZED.append(f"{task['id']}:{fn}({lit[:24]}…) line {len(line)} chars")
            continue
        # A probe the visible oracle already asserts verbatim is not held-out
        # evidence — hidden_ok would be implied by the visible pass, not held
        # out beside it. The battery's own small values (0, 1, 2, 7) collide
        # with what a test typically writes, and three hard tasks were more
        # than half collisions (h26_pascal_row, h33_base32_decode,
        # h45_phone_letters) before this line existed. Skipping keeps the key
        # genuinely differential and keeps the row.
        if line.strip() in task["test"]:
            NOVELTY.append(f"{task['id']}:{fn}({lit[:24]}…)")
            continue
        lines.append(line)
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


def _load(path: Path) -> list:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def main(argv: list = ()) -> int:
    wide = "--wide" in argv
    out = WIDE_OUT if wide else OUT
    if "--out" in argv:
        nxt = argv[argv.index("--out") + 1:]
        if not nxt:
            print("--out needs a path", file=sys.stderr)
            return 2
        out = Path(nxt[0])
        if not out.is_absolute():
            out = ROOT / out
    subtle = _load(SUBTLE)
    sources = ([(n, "routine") for n in ROUTINE_SUITES]
               + [(n, "hard") for n in HARD_SUITES]) if wide else \
        [("m0_tasks", "routine")]
    rows = [dict(t, gate="subtle") for t in subtle]
    dropped, broken, unusable = [], [], []
    keyed, seen_ids = {"routine": 0, "hard": 0}, {r["id"] for r in subtle}
    by_tag: dict[str, int] = {}
    for name, tag in sources:
        for t in _load(ROOT / "benchmarks" / "tasks" / f"{name}.jsonl"):
            by_tag[tag] = by_tag.get(tag, 0) + 1
            # A task whose own reference does not pass its own visible test is
            # not a keyable instrument and not a generator bug either: drop it
            # and name it. Only a key that the reference FAILS is broken — that
            # is this script's own defect, and it stops the whole run.
            if not confidence._visible_verdict(t["solution"], t["test"], KEY_SEED, 20):
                unusable.append(t["id"])
                continue
            hidden = key_for(t)
            if not hidden:
                dropped.append(t["id"])
                continue
            if t["id"] in seen_ids:
                print(f"  REFUSED {t['id']}: its id is already in the population "
                      f"— a duplicated id would silently double its weight",
                      file=sys.stderr)
                broken.append((t["id"], "duplicate id"))
                continue
            seen_ids.add(t["id"])
            row = {"id": t["id"], "prompt": t["prompt"], "test": t["test"],
                   "hidden": hidden, "solution": t["solution"], "gate": tag}
            why = verify(row)
            if why:
                broken.append((t["id"], why))
                continue
            rows.append(row)
            keyed[tag] += 1
    if broken:
        for tid, why in broken:
            print(f"  REFUSED {tid}: {why}", file=sys.stderr)
        print(f"wrote nothing: {len(broken)} key(s) unusable", file=sys.stderr)
        return 1
    # Half the population missing is this script breaking, not tasks being hard.
    # Without this clause a bug that makes every reference look like it raises
    # leaves a well-formed file with no routine rows, and the gate silently
    # loses its false-offer denominator.
    for tag, want in by_tag.items():
        if keyed[tag] * 2 < want:
            print(f"wrote nothing: {keyed[tag]}/{want} {tag} tasks yielded a key "
                  f"— that is a generator bug, not attrition "
                  f"(dropped: {dropped})", file=sys.stderr)
            return 1
    if wide and (len(rows) < MIN_WIDE_TOTAL or keyed["hard"] < MIN_WIDE_HARD):
        print(f"wrote nothing: the wide instrument is {len(rows)} row(s) with "
              f"{keyed['hard']} keyed hard task(s), and R-2.3's denominators are "
              f"only measurable over >= {MIN_WIDE_TOTAL} / >= {MIN_WIDE_HARD} — a "
              f"suite this thin cannot carry a >=90% recall clause", file=sys.stderr)
        return 1
    out.write_text("".join(json.dumps(r) + "\n" for r in rows))
    shown = out.relative_to(ROOT) if out.is_absolute() and ROOT in out.parents else out
    print(f"wrote {len(rows)} task(s) -> {shown}")
    print(f"  subtle (seeded-bug) tasks: {len(subtle)}")
    for tag in ("routine", "hard"):
        if by_tag.get(tag):
            print(f"  {tag} tasks with a differential key: "
                  f"{keyed[tag]}/{by_tag[tag]}")
    if dropped or unusable:
        if dropped:
            print(f"  dropped (no differential key available — the reference yields "
                  f"no assertable value on the probed inputs): {dropped}")
        if unusable:
            print(f"  dropped (the task's own reference does not pass its own "
                  f"visible test, so no key built on it could be ground truth): "
                  f"{unusable}")
    per = [len(r["hidden"].strip().splitlines()) for r in rows]
    print(f"  hidden key size: min {min(per)}, max {max(per)} lines")
    if OVERSIZED:
        print(f"  probes dropped for exceeding {MAX_EXPECTATION_CHARS} chars "
              f"(an expectation nobody can read is not held-out ground truth): "
              f"{len(OVERSIZED)} — {OVERSIZED[:3]}")
    if NOVELTY:
        print(f"  probes skipped for repeating the task's own visible test "
              f"(held-out means NEW evidence): {len(NOVELTY)} — {NOVELTY[:3]}")
    stats = [_composition(r["hidden"])[0] for r in rows if r["gate"] != "subtle"]
    raises = sum(_composition(r["hidden"])[1] for r in rows if r["gate"] != "subtle")
    print(f"  keys verified against their own reference: "
          f"{len(stats)}/{len(stats)}   value assertions per key: "
          f"min {min(stats)}, max {max(stats)}   raise expectations total: {raises}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
