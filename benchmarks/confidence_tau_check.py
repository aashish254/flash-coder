"""Is R-2.3's gate closable by moving the coverage threshold? Measured, both tiers.

The live arms on the wide instrument (`benchmarks/tasks/p6b_tasks.jsonl`) both
missed R-2.3's two clauses: recall 4/11 at the 7B and 44/52 at the 1.5B against
">= 90%", and 5 false offers over 48 hidden-accepted at the 7B against "< 1 per
20". The misses at the 1.5B are not mysterious — five of the eight answers the
signal missed had visible coverage between the shipped `COVERAGE_TAU` 0.55 and
0.85, so a stricter threshold would have caught them. This script asks the only
question that matters before changing a shipped constant: **does any single tau
satisfy BOTH clauses, on both tiers, at once?**

It answers without loading a model, by re-deriving the offer from the numbers the
arms already recorded. That re-derivation is not trusted on sight: it must
reproduce the shipped predicate's recorded `conf_offer` on EVERY answer of both
arms before a single sweep row is printed, and a mutation of the model (dropping
the static short-circuit) is put back to prove that clause bites. A sweep whose
instrument does not agree with the real thing is a fiction about a threshold.

Two things fall out of the table, and neither is a pass:

  the recall clause and the false-offer clause are in opposite directions across
  tiers — tau 0.85 clears recall at the 1.5B (48/52 = 92.3%) and costs the 7B
  eight more false offers (5 -> 13) while still leaving its recall at 63.6%;

  so the gate as written is not a threshold-tuning problem. The stated limit of
  this measurement is that it is a re-derivation from recorded per-answer fields,
  not a re-run of the four streams over the answers (the traces keep the evidence,
  not the source), and the 1.5B's false-offer denominator is 7 hidden-accepted
  answers, which cannot resolve a per-20-tasks clause at all.

    .venv/bin/python benchmarks/confidence_tau_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash.confidence import COVERAGE_TAU          # noqa: E402

ARMS = [("7B", "benchmarks/results/traces/20260927-001446-run-suite-bcf3.jsonl",
         "benchmarks/tasks/p6b_tasks.jsonl"),
        ("1.5B", "benchmarks/results/traces/20260927-002448-run-suite-7bb2.jsonl",
         "benchmarks/tasks/p6b_tasks.jsonl")]
TAUS = (COVERAGE_TAU, 0.67, 0.8, 0.85, 0.9, 0.95)
RECALL_GATE = 0.90
FALSE_PER_TASKS_GATE = 20.0          # "< 1 false escalation per 20 routine tasks"

CHECKS: list = []


def ck(name, ok, detail=""):
    CHECKS.append((name, bool(ok), detail))


def offer_from(row, tau: float) -> bool:
    """The shipped predicate, recomputed from a recorded answer's own numbers.

    `evaluate` short-circuits on a static error — an answer that does not parse
    never runs the other three streams — so `cov`/`seeds`/`edges` for those rows
    describe nothing, and the offer is demanded by the static strand alone. Any
    re-ordering of that rule is a different instrument, which is why the
    reproduction clause below exists.
    """
    if row["conf_static"]:
        return True
    return (row["conf_cov"] < tau or row["conf_seeds"] not in (0, 3)
            or row["conf_edges"] > 0)


def load(label: str, trace_rel: str, tasks_rel: str):
    tpath, kpath = ROOT / trace_rel, ROOT / tasks_rel
    if not tpath.exists() or not kpath.exists():
        ck(f"{label} arm is present to be measured", False,
           f"{tpath if not tpath.exists() else kpath} missing")
        return None
    rows = [json.loads(l) for l in tpath.read_text().split("\n") if l.strip()]
    answers = [r for r in rows if r.get("type") == "task_end" and "conf_offer" in r]
    gates = {}
    for l in kpath.read_text().splitlines():
        if l.strip():
            d = json.loads(l)
            gates[d["id"]] = d.get("gate")
    return label, answers, gates


def sweep(answers, gates, tau: float) -> dict:
    """Both gate clauses over one threshold, on one arm."""
    sunk = [r for r in answers if r["hidden_ok"] is False]
    floated = [r for r in answers if r["hidden_ok"] is True]
    routine = [r for r in answers if gates.get(r["task_id"]) == "routine"]
    rt_fl = [r for r in routine if r["hidden_ok"] is True]
    return {"n": len(answers), "sunk": len(sunk),
            "caught": sum(offer_from(r, tau) for r in sunk),
            "false": sum(offer_from(r, tau) for r in floated),
            "floated": len(floated),
            "rt_tasks": len(routine), "rt_false": sum(offer_from(r, tau) for r in rt_fl)}


def main() -> int:
    loaded = [load(*arm) for arm in ARMS]
    if any(x is None for x in loaded):
        print("refused: an arm is missing, so the sweep would describe one tier "
              "and claim two", file=sys.stderr)
        return 1
    print(f"COVERAGE_TAU as shipped: {COVERAGE_TAU}")
    print("recall gate >= "
          f"{RECALL_GATE:.0%}, false-offer gate < 1 per {FALSE_PER_TASKS_GATE:.0f} "
          "routine tasks\n")

    # ---- the instrument first: does the model of the predicate agree with it?
    for (label, answers, gates) in loaded:
        mism = [r["task_id"] for r in answers
                if bool(r["conf_offer"]) != offer_from(r, COVERAGE_TAU)]
        ck(f"{label}: the re-derived predicate reproduces the RECORDED offer on "
           f"every answer at the shipped tau ({len(answers) - len(mism)}/"
           f"{len(answers)} must match)", not mism, f"mismatches: {mism}")

    tables = {}
    for label, answers, gates in loaded:
        tables[label] = [sweep(answers, gates, t) for t in TAUS]
        print(f"--- {label} arm, {len(answers)} keyed answers")
        print(f"{'tau':<6}{'recall':>16}{'false offers':>22}{'per-20 clause':>20}")
        for t, s in zip(TAUS, tables[label]):
            rec = s["caught"] / s["sunk"] if s["sunk"] else None
            per = (f"1 per {s['rt_tasks'] / s['rt_false']:.1f}"
                   if s["rt_false"] else "NOT MEASURABLE")
            ratio = f"{s['caught']}/{s['sunk']}"
            pct = "n/a" if rec is None else f"{rec:.1%}"
            false = f"{s['false']}/{s['floated']}"
            print(f"{t:<6}{ratio:>9}{pct:>7}{false:>22}{per:>20}")
        print()

    # ---- the two clauses pull opposite ways, and no tau escapes that
    ship = {label: ts[0] for label, ts in tables.items()}
    ck("the shipped tau misses BOTH clauses somewhere: recall is under the gate at "
       "every tier measured, and the 7B's routine population already breaks the "
       "per-20 clause",
       all(s["caught"] / s["sunk"] < RECALL_GATE for s in ship.values() if s["sunk"])
       and any(s["rt_false"] and s["rt_tasks"] / s["rt_false"] < FALSE_PER_TASKS_GATE
               for s in ship.values()),
       {k: (f"{v['caught']}/{v['sunk']}", f"1 per {v['rt_tasks'] / v['rt_false']:.1f}"
            if v["rt_false"] else "no routine population") for k, v in ship.items()})

    ck("no threshold in the sweep gives the 7B its recall gate — its misses are not "
       "coverage-shaped: the answer ran every visible line and still returned a "
       "wrong value",
       all(ts["sunk"] and ts["caught"] / ts["sunk"] < RECALL_GATE
           for ts in tables["7B"]),
       [f"{ts['caught']}/{ts['sunk']}" for ts in tables["7B"]])

    clears = [t for t, ts in zip(TAUS, tables["1.5B"])
              if ts["sunk"] and ts["caught"] / ts["sunk"] >= RECALL_GATE]
    ck("and every threshold that DOES clear recall at the 1.5B breaks the 7B's "
       "per-20 clause harder — the gate is a trade, not a tuning problem",
       bool(clears) and all(
           tables["7B"][TAUS.index(t)]["rt_false"] > ship["7B"]["rt_false"]
           and (tables["7B"][TAUS.index(t)]["rt_false"] == 0
                or tables["7B"][TAUS.index(t)]["rt_tasks"]
                / tables["7B"][TAUS.index(t)]["rt_false"]
                < FALSE_PER_TASKS_GATE)
           for t in clears),
       f"recall-clearing taus: {clears}")

    at_ship = ship["1.5B"]
    ck("the 1.5B's false-offer denominator is too thin to resolve a per-20-tasks "
       "clause at any tau, and says so rather than printing a ratio",
       at_ship["floated"] < FALSE_PER_TASKS_GATE,
       f"{at_ship['floated']} hidden-accepted answer(s) at the 1.5B")

    # ---- mutation: the reproduction clause has to be able to fail
    naive = {label: [r["task_id"] for r in answers
                     if bool(r["conf_offer"]) != (r["conf_cov"] < COVERAGE_TAU
                                                  or r["conf_seeds"] not in (0, 3)
                                                  or r["conf_edges"] > 0)]
             for label, answers, gates in loaded}
    ck("mutation: a model that forgets the static short-circuit is caught by the "
       "reproduction clause — and only by it",
       any(naive.values()), naive)

    width = max(len(n) for n, _, _ in CHECKS)
    bad = 0
    for name, ok, detail in CHECKS:
        bad += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} {name.ljust(width)}"
              + (f"   [{detail}]" if detail and not ok else ""))
    print(f"\nR-2.3 threshold sweep: {len(CHECKS) - bad}/{len(CHECKS)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
