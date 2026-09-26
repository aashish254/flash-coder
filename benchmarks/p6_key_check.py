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
  no expectation may be so large that nobody can read it, and no probe may be
  so large that nobody can afford it — the two bounds and the generator guards
  are each proven by putting the unbounded behavior back;
  the WIDE instrument (p6b_tasks.jsonl, which carries R-2.3's recall
  denominator) is held to the same clauses as the default one.

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


def audit(rows: list, label: str, min_routine: int = 0) -> None:
    """The data clauses, stated over one instrument.

    Run for both files because the gate is scored over both, and a clause that
    only ever looks at the 23-row default cannot tell me the 61-row wide
    instrument is sound.
    """
    keyed = [r for r in rows if r["gate"] != "subtle"]
    bad_self = [r["id"] for r in keyed if not passes(r["solution"], r["hidden"])]
    ck(f"{label}: the shipped reference passes its OWN held-out key, at every "
       f"hash order", not bad_self, f"{len(keyed)} keyed, fails: {bad_self}")

    offenders = [(r["id"], sorted(called_names(r["hidden"]) - bound_names(r["solution"])))
                 for r in keyed]
    ck(f"{label}: every name a key calls is bound by the reference it was "
       f"observed from", not [o for o in offenders if o[1]],
       f"unbound: {[o for o in offenders if o[1]]}")

    vacuous = [r["id"] for r in keyed
               if not any(l.startswith("assert ") and "==" in l
                          for l in r["hidden"].splitlines())]
    ck(f"{label}: no key is only a raise-expectation (nothing about a value)",
       not vacuous, f"vacuous: {vacuous}")

    ne = [r["id"] for r in keyed if "NameError" in r["hidden"]]
    ck(f"{label}: no key expects NameError — the generator bug's fingerprint",
       not ne, f"keys: {ne}")

    teeth = []
    for r in keyed:
        line = first_value_line(r["hidden"])
        if not line:
            continue
        mutated = r["hidden"].replace(
            line, line.split("==", 1)[0] + "== '__NOT_THE_REFERENCE__'")
        if passes(r["solution"], mutated):
            teeth.append(r["id"])
    ck(f"{label}: mutation — a flipped expected value makes the reference FAIL "
       f"its key (so the key has teeth)", not teeth, f"still passing: {teeth}")

    # A key line the visible oracle already asserts is not new evidence: the
    # empty-input probes are often also in the task's own test (`flatten({})`).
    # What the gate needs is that a key is MOSTLY inputs nobody tested, or
    # hidden_ok would be implied by the visible pass instead of held out beside
    # it — so the clause is a share, not a zero.
    overlap = total = 0
    dominated = []
    for r in keyed:
        v = [l for l in r["hidden"].splitlines()
             if l.startswith("assert ") and "==" in l]
        rep = [l for l in v if l.strip() in r["test"]]
        overlap += len(rep)
        total += len(v)
        if len(rep) * 2 > len(v):
            dominated.append(r["id"])
    ck(f"{label}: the key stays held out — no key is dominated by lines the "
       f"visible oracle already asserts", not dominated,
       f"{overlap}/{total} value assertions repeat the visible oracle; "
       f"over-half: {dominated}")

    nosol = [r["id"] for r in keyed if not passes(r["solution"], r["test"])]
    ck(f"{label}: the reference passes the visible test it was keyed beside",
       not nosol, f"fails: {nosol}")

    # --- the two size bounds, each proven by removing it --------------------
    # Measured on this box before either bound existed: the key built for
    # h39_spiral_matrix carried `assert spiral(10000) == [[1, 2, …` — a
    # 7 890 896-character expectation on the wide battery's 10 ** 3 entry, and
    # 1445 MB / 2.29 s of probe child at 10 ** 4, which the 2-second alarm
    # turned into `spiral(10000) -> HANG` about a CORRECT reference. The
    # committed p6 file's own worst key was 82 183 characters of one line.
    widths = [len(l) for r in rows for l in r["hidden"].splitlines()]
    ck(f"{label}: no expectation is bigger than the key budget "
       f"({G.MAX_EXPECTATION_CHARS} chars)",
       widths and max(widths) <= G.MAX_EXPECTATION_CHARS,
       f"widest key line {max(widths) if widths else 0} chars")

    planned = [v for r in rows if r["gate"] != "subtle"
               for _, a in confidence._probe_calls(r["solution"], r["test"])
               for v in a]
    ck(f"{label}: no probe is planned outside the affordability budget",
       all(confidence._affordable(v) for v in planned),
       f"{len(planned)} probe argument(s), "
       f"{sum(1 for v in planned if not confidence._affordable(v))} over budget")

    if min_routine:
        n_routine = sum(1 for r in rows if r["gate"] == "routine")
        ck(f"{label}: the routine half survives the widening (the false-offer "
           f"clause is stated per 20 ROUTINE tasks)", n_routine >= min_routine,
           f"{n_routine} routine row(s)")


def main() -> int:
    rows = [json.loads(l) for l in SUITE.read_text().splitlines() if l.strip()]
    wide = [json.loads(l) for l in G.WIDE_OUT.read_text().splitlines() if l.strip()]
    routine = [r for r in rows if r["gate"] == "routine"]
    subtle = [r for r in rows if r["gate"] == "subtle"]
    m0 = [json.loads(l) for l in M0.read_text().splitlines() if l.strip()]

    ck("population: every row declares which gate clause it serves",
       all(r.get("gate") in ("routine", "subtle") for r in rows)
       and all(r.get("gate") in ("routine", "hard", "subtle") for r in wide),
       f"{len(subtle)} subtle + {len(routine)} routine in p6, "
       f"{sum(1 for r in wide if r['gate'] == 'hard')} hard in p6b")
    ck("population: the routine half is not quietly evaporating "
       "(< 1 per 20 needs a denominator)", len(routine) * 2 >= len(m0),
       f"{len(routine)}/{len(m0)} of m0 keyed")
    ck("population: the wide instrument is wider — the recall clause has a "
       "denominator the default one does not",
       len(wide) >= G.MIN_WIDE_TOTAL
       and sum(1 for r in wide if r["gate"] == "hard") >= G.MIN_WIDE_HARD
       and len(wide) > len(rows),
       f"{len(wide)} row(s), {sum(1 for r in wide if r['gate'] == 'hard')} keyed hard")

    audit(rows, "p6")
    audit(wide, "p6b", min_routine=len(routine))

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

    # the writer's size bound, proven from both directions on one task: WITH it
    # the unaffordable expectation is dropped and reported; WITHOUT it the same
    # probe becomes a key line nobody can read. Measured here: spiral(1000)
    # answers with 7 890 896 characters of repr, pascal_row(1000) with 218 190.
    src_task = next((t for t in G._load(ROOT / "benchmarks" / "tasks"
                                        / "m6_heldout_tasks.jsonl")
                     if t["id"] == "h39_spiral_matrix"), None)
    saved_cap, saved_oversized = G.MAX_EXPECTATION_CHARS, list(G.OVERSIZED)
    try:
        G.OVERSIZED.clear()
        bounded = G.key_for(src_task)
        bounded_report = list(G.OVERSIZED)
        G.MAX_EXPECTATION_CHARS = 10 ** 9
        G.OVERSIZED.clear()
        unbounded = G.key_for(src_task)
    finally:
        G.MAX_EXPECTATION_CHARS = saved_cap
        G.OVERSIZED[:] = saved_oversized
    widest_un = max([len(l) for l in unbounded.splitlines()] or [0])
    widest_b = max([len(l) for l in bounded.splitlines()] or [0])
    ck("mutation: with the key budget removed, the same probe writes an "
       "expectation nobody can read",
       widest_un > 10 ** 5 and widest_b <= saved_cap,
       f"unbounded widest line {widest_un} chars vs bounded {widest_b}")
    ck("mutation: the budget reports what it dropped instead of dropping it "
       "silently", bool(bounded_report), f"{bounded_report}")

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
