"""The p6 live-arm population, OFFLINE: is every held-out key in
benchmarks/tasks/p6_tasks.jsonl a real expectation about an answer?

§34.2's gate is scored over this file, so a defect here is not a cosmetic one.
An uninterpolated function name in benchmarks/gen_p6_key.py once made every
probe look like it raised `NameError`; all 17 routine answers then "failed" a
key that asserted nothing about them, and the live arm printed recall 2/17 while
measuring its own generator. These checks are what makes that class of bug
loud instead of silent:

  the reference must pass its own key, at every measured hash order;
  the key must call only names the reference actually binds;
  the key must have teeth — flip an expected value and the reference must fail;
  a key of nothing but raise-expectations, or one that expects NameError from a
  bare call, is not ground truth;
  the key must stay mostly held out (no key dominated by lines the visible
  oracle already asserts);
  the generator's own guards must fire when the bug is put back.

    python benchmarks/p6_key_check.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

import gen_p6_key as G                                    # noqa: E402
from flash import confidence                              # noqa: E402

SUITE = ROOT / "benchmarks" / "tasks" / "p6_tasks.jsonl"
M0 = ROOT / "benchmarks" / "tasks" / "m0_tasks.jsonl"
KEY_SEEDS = confidence.HASH_SEEDS
CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


def report() -> int:
    n = sum(1 for _, ok, _ in CHECKS if ok)
    w = max(len(nm) for nm, _, _ in CHECKS)
    for nm, ok, detail in CHECKS:
        print(f"  {'ok  ' if ok else 'FAIL'} {nm:<{w}}  {detail}")
    print(f"{n}/{len(CHECKS)} premise check(s) passed")
    return 0 if n == len(CHECKS) else 1


def passes(code: str, test: str) -> bool:
    return all(confidence._visible_verdict(code, test, s, 20) for s in KEY_SEEDS)


CALL = re.compile(r"^\s*(?:assert\s+)?([A-Za-z_]\w*)\(")


def called_names(key: str) -> set:
    return {m.group(1) for m in (CALL.search(l) for l in key.splitlines()) if m}


def bound_names(code: str) -> set:
    ns: dict = {}
    try:
        exec(compile(code, "<ref>", "exec"), ns)
    except Exception:
        return set()
    return set(ns)


def first_value_line(key: str) -> str:
    for l in key.splitlines():
        if l.startswith("assert ") and "==" in l:
            return l
    return ""


def main() -> int:
    rows = [json.loads(l) for l in SUITE.read_text().splitlines() if l.strip()]
    routine = [r for r in rows if r["gate"] == "routine"]
    subtle = [r for r in rows if r["gate"] == "subtle"]
    m0 = [json.loads(l) for l in M0.read_text().splitlines() if l.strip()]

    ck("population: every row declares which gate clause it serves",
       all(r.get("gate") in ("routine", "subtle") for r in rows),
       f"{len(subtle)} subtle + {len(routine)} routine")
    ck("population: the routine half is not quietly evaporating "
       "(< 1 per 20 needs a denominator)", len(routine) * 2 >= len(m0),
       f"{len(routine)}/{len(m0)} of m0 keyed")

    bad_self = [r["id"] for r in routine if not passes(r["solution"], r["hidden"])]
    ck("the shipped reference passes its OWN held-out key, at every hash order",
       not bad_self, f"fails: {bad_self}")

    bad_bind = [(r["id"], sorted(called_names(r["hidden"]) - bound_names(r["solution"])))
                for r in routine]
    offenders = [b for b in bad_bind if b[1]]
    ck("every name a key calls is bound by the reference it was observed from",
       not offenders, f"unbound: {offenders}")

    vacuous = [r["id"] for r in routine
               if not any(l.startswith("assert ") and "==" in l
                          for l in r["hidden"].splitlines())]
    ck("no routine key is only a raise-expectation (nothing about a value)",
       not vacuous, f"vacuous: {vacuous}")

    ne = [r["id"] for r in routine if "NameError" in r["hidden"]]
    ck("no routine key expects NameError — the generator bug's fingerprint",
       not ne, f"keys: {ne}")

    teeth = []
    for r in routine:
        line = first_value_line(r["hidden"])
        if not line:
            continue
        mutated = r["hidden"].replace(
            line, line.split("==", 1)[0] + "== '__NOT_THE_REFERENCE__'")
        if passes(r["solution"], mutated):
            teeth.append(r["id"])
    ck("mutation: a flipped expected value makes the reference FAIL its key "
       "(so the key has teeth)", not teeth, f"still passing: {teeth}")

    # A key line the visible oracle already asserts is not new evidence: the
    # empty-input probes are often also in the task's own test (`flatten({})`).
    # What the gate needs is that a key is MOSTLY inputs nobody tested, or
    # hidden_ok would be implied by the visible pass instead of held out beside
    # it — so the clause is a share, not a zero.
    overlap = total = 0
    dominated = []
    for r in routine:
        v = [l for l in r["hidden"].splitlines()
             if l.startswith("assert ") and "==" in l]
        rep = [l for l in v if l.strip() in r["test"]]
        overlap += len(rep)
        total += len(v)
        if len(rep) * 2 > len(v):
            dominated.append(r["id"])
    ck("the key stays held out: no key is dominated by lines the visible oracle "
       "already asserts", not dominated,
       f"{overlap}/{total} value assertions repeat the visible oracle; "
       f"over-half: {dominated}")

    nosol = [r["id"] for r in routine if not passes(r["solution"], r["test"])]
    ck("the reference passes the visible test it was keyed beside", not nosol,
       f"fails: {nosol}")

    # the two guards that stop the generator writing a file like this again,
    # each proven by putting the bug back
    src = (ROOT / "benchmarks" / "gen_p6_key.py").read_text()
    broken = src.replace("repr(getattr(ref, {fn!r})(*args))",
                         "repr(getattr(ref, fn)(*args))")
    ck("the sabotage applies (the interpolation is still in the generator)",
       broken != src, "no change made")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.jsonl"
        ns = {"__name__": "__notmain__", "__file__": str(ROOT / "benchmarks" / "gen_p6_key.py")}
        exec(compile(broken, "gen_p6_key_broken.py", "exec"), ns)
        ns["OUT"] = out
        ns["ROOT"] = ROOT
        rc = ns["main"]()
        ck("mutation: with the uninterpolated name back, the generator writes "
           "nothing and says so", rc == 1 and not out.exists(),
           f"rc={rc} wrote={out.exists()}")

    def rendered(seed: str) -> str:
        return subprocess.run(
            [sys.executable, "-c",
             "print(repr({'hot','dog','cog','lot','dot','log'}))"],
            capture_output=True, text=True, timeout=20,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin"}).stdout.strip()

    orders = {rendered(s) for s in ("3", "11", "17", "23", "41")}
    ck("premise: an unpinned child re-orders a set literal between runs "
       "(which is why keys are rendered canonically)", len(orders) > 1,
       f"{len(orders)} distinct order(s) over 5 seeds")
    ck("canonical rendering is seed-independent and round-trips to an equal value",
       G._arg_repr({"hot", "dog", "cog", "lot", "dot", "log"})
       == "{'cog', 'dog', 'dot', 'hot', 'log', 'lot'}"
       and eval(G._arg_repr({"b": {2, 1}, "a": [3, {5, 4}]}))
       == {"b": {2, 1}, "a": [3, {5, 4}]},
       G._arg_repr({"b": {2, 1}, "a": [3, {5, 4}]}))

    return report()


if __name__ == "__main__":
    raise SystemExit(main())
