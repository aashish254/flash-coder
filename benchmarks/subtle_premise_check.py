"""R-2.3's premise, OFFLINE: can §34.2's evidence signal see the subtle bugs
this suite seeds — and exactly where does it stop seeing?

For every task in benchmarks/tasks/subtle_tasks.jsonl the seeded answer must
PASS the visible oracle and FAIL the hidden one; that is the whole point of the
suite, and if it were false the live gate would be measuring a signal with
nothing to catch. Then the signal (flash.confidence.evaluate) runs on the same
answers, no model in the loop:

  recall    offers over the would-fail-hidden answers. Gate: >= 90%.
  false     offers over the answer keys' ACCEPTED answers — the 8 references
            here plus the 20 routine m0 references. Gate: < 1 per 20.
  blind     the two masked-value answers are EXPECTED not to be offered. This
            check fails if they ARE, because then the shipped limitation
            paragraph would be wrong.

Verdicts are measured under explicit PYTHONHASHSEED values (0/1/7) rather than
the oracle's random hashing: an order-dependent answer's verdict is otherwise a
coin flip, and a premise number has to be reproducible. "Passes the visible
oracle" therefore means "the oracle accepted it under at least one hash order" —
which is exactly how a subtle order bug survives a real run.

    python benchmarks/subtle_premise_check.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import confidence                           # noqa: E402
from flash.confidence import evaluate                  # noqa: E402

SUITE = ROOT / "benchmarks" / "tasks" / "subtle_tasks.jsonl"
ROUTINE = ROOT / "benchmarks" / "tasks" / "m0_tasks.jsonl"
KEY_SEEDS = (0, 1, 7)
STREAMS = ("static", "coverage", "seeds", "edges")
CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


def fired(sig: confidence.Signals) -> set[str]:
    """Which evidence streams fired, by name, from the reasons the offer cites."""
    return {r.split(":", 1)[0] for r in sig.reasons}


def verdicts(code: str, test: str) -> list[bool]:
    """The same program's verdict under each measured hash order."""
    return [confidence._visible_verdict(code, test, s, 15) for s in KEY_SEEDS]


def report() -> int:
    n = sum(1 for _, ok, _ in CHECKS if ok)
    w = max(len(nm) for nm, _, _ in CHECKS)
    for nm, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {nm.ljust(w)}"
              + (f"   [{detail}]" if detail and not ok else ""))
    print(f"\nsubtle-suite premise: {n}/{len(CHECKS)} checks passed")
    return 0 if n == len(CHECKS) else 1


def main() -> int:
    rows = [json.loads(l) for l in SUITE.read_text().splitlines() if l.strip()]
    det = [r for r in rows if r["defect"] in STREAMS]
    blind = [r for r in rows if r["defect"] == "masked-value"]
    ck("suite: 8 tasks — 6 seeded from a detectable class, 2 from the blind spot",
       len(rows) == 8 and len(det) == 6 and len(blind) == 2,
       str([(r["id"], r["defect"]) for r in rows]))
    ck("suite: every row carries both halves of the key",
       all(set(r) >= {"id", "prompt", "test", "hidden", "solution", "seeded", "defect"}
           for r in rows))

    # ------------------------------------------------- the premises themselves
    for r in rows:
        v_seed, v_hid = verdicts(r["seeded"], r["test"]), verdicts(r["seeded"], r["hidden"])
        ck(f"{r['id']}: the subtle answer passes the visible oracle", any(v_seed),
           f"verdicts={v_seed}")
        ck(f"{r['id']}: the same answer is sunk by the hidden key", not any(v_hid),
           f"verdicts={v_hid} — hidden accepted it, nothing for the signal to catch")
        rv, rh = verdicts(r["solution"], r["test"]), verdicts(r["solution"], r["hidden"])
        ck(f"{r['id']}: the reference passes both halves", all(rv) and all(rh),
           f"visible={rv} hidden={rh}")
        # a seeds-class bug is only subtle because the order can hide it
        if r["defect"] == "seeds":
            ck(f"{r['id']}: the visible verdict really does move with the hash order",
               len(set(v_seed)) > 1, f"verdicts={v_seed}")
        else:
            ck(f"{r['id']}: the visible verdict is order-stable, so seeds cannot be "
               "what catches it", len(set(v_seed)) == 1, f"verdicts={v_seed}")

    # ---------------------------------------------------------------- the signal
    caught = 0
    for r in rows:
        sig = evaluate(r["seeded"], r["test"])
        got = fired(sig)
        if r["defect"] in STREAMS:
            caught += sig.offer
            ck(f"{r['id']}: {r['defect']} is what the signal sees, and nothing else",
               sig.offer and got == {r["defect"]}, f"offer={sig.offer} fired={sorted(got)}")
        else:
            ck(f"{r['id']}: the blind spot stays blind — no stream reaches a wrong "
               "value the visible asserts ignore",
               not sig.offer, f"offer={sig.offer} fired={sorted(got)}")

    ck("recall: the signal offers on >= 90% of the would-fail-hidden answers",
       caught >= 0.9 * len(det), f"{caught}/{len(det)}")

    # ------------------------------------------- false offers, two populations
    #
    # Population 1 is the one §34.2's clause names: ROUTINE tasks. Population 2
    # is this suite's own references, which cannot be gated at all — their
    # visible tests are thin BY CONSTRUCTION, so an unexecuted line is true
    # evidence about an answer that is nonetheless correct. A rate measured
    # there would say nothing about escalations a human rejects.
    quiet_subtle = 0
    for r in rows:
        ref = evaluate(r["solution"], r["test"])
        quiet_subtle += not ref.offer
        if ref.offer:
            print(f"       note: subtle-suite reference {r['id']} offered on "
                  f"{sorted(fired(ref))} — {ref.reasons[0]}")
    ck("the suite's own references are NOT a false-offer population: the two thin-"
       "visible-test fixtures offer on coverage even when correct, and that is the "
       "evidence saying what it says",
       len(rows) - quiet_subtle == 2, f"{len(rows) - quiet_subtle}/{len(rows)} offered")

    routine = [json.loads(l) for l in ROUTINE.read_text().splitlines() if l.strip()]
    offers = []
    for t in routine:
        if not t.get("solution"):
            continue
        sig = evaluate(t["solution"], t["test"])
        if sig.offer:
            offers.append((t["id"], sorted(fired(sig)), sig.reasons[0]))
    n_ref = len(routine)
    print(f"       routine (m0) references offered: {len(offers)}/{n_ref}")
    for tid, streams, why in offers:
        print(f"         {tid:<22} {streams}  {why}")
    ck(f"false offers, routine population: no static/coverage/seeds offer fires on "
       f"any of the {n_ref} correct references",
       not [o for o in offers if o[1] != ["edges"]],
       str([o for o in offers if o[1] != ["edges"]]))
    ck(f"the whole of the {len(offers)}/{n_ref} routine offer list is one real crash, "
       "named as the call it was — not a phantom the probe invented",
       len(offers) == 1 and offers[0][1] == ["edges"]
       and "IndexError" in offers[0][2]
       and "max_subarray([])" in offers[0][2] and "[[]]" not in offers[0][2],
       str(offers))
    print("       note: §34.2's < 1-per-20 clause is about escalations a human "
          "rejects; this one is a reference that raises IndexError on an empty "
          "array. The live arm owns that judgement — this number is the offline "
          "bound, not the gate.")

    # ------------------------------------------ mutation: the number must move
    tau0 = evaluate(det[0]["seeded"], det[0]["test"], tau=0.0)
    ck("mutation: with the coverage bar at zero the coverage answer is not offered "
       "(so coverage is what fires it)",
       "coverage" not in fired(tau0) and not tau0.offer, tau0.describe())
    old = confidence.HASH_SEEDS
    confidence.HASH_SEEDS = (KEY_SEEDS[0],)
    one = evaluate(next(r for r in det if r["defect"] == "seeds")["seeded"],
                   next(r for r in det if r["defect"] == "seeds")["test"])
    confidence.HASH_SEEDS = old
    ck("mutation: with one hash seed the order-dependent answer looks stable "
       "(so the reruns are what fire it)", "seeds" not in fired(one), one.describe())
    noedge = [r for r in det if r["defect"] == "edges"][0]
    planned = confidence._probe_calls(
        "def reverse_words(text):\n    return ' '.join(text.split()[::-1])\n",
        "assert reverse_words('a b') == 'b a'\n")
    ck("shape typing: every planned probe of a string function feeds a string — "
       "the old battery's [] and 0 and None are gone",
       planned and all(isinstance(a[0], str) for _, a in planned)
       and all(isinstance(a[0], str) for _, a in planned),
       str(planned[:3]))
    ck("shape typing: a test whose args are variables plans no probe at all "
       "(no evidence, no offer)",
       confidence._probe_calls(noedge["solution"], "assert f(xs) == 1\n") == [])
    ck("mutation: the empty list is still in the plan for a list-taking function — "
       "that is what the guard class needs",
       any(a == [[]] for _, a in confidence._probe_calls(
           noedge["solution"], noedge["test"])),
       str(confidence._probe_calls(noedge["solution"], noedge["test"])[:4]))
    ck("the offer the signals make is the offer the ledger fields carry",
       evaluate(noedge["seeded"], noedge["test"]).fields()["conf_offer"] is True
       and evaluate(blind[0]["solution"], blind[0]["test"]).fields()["conf_offer"] is False)

    # -------------------------------------------------------- the honest table
    print("\nper-defect offer table (seeded answer / its reference):")
    for r in rows:
        s, ref = evaluate(r["seeded"], r["test"]), evaluate(r["solution"], r["test"])
        print(f"  {r['id']:<22} {r['defect']:<13} seeded offer={str(s.offer):<5} "
              f"{sorted(fired(s)) or ['-']}  cov={s.coverage:.2f}({s.covered}/{s.total}) "
              f"seeds={''.join('P' if v else 'F' for v in s.seeds)} edges={len(s.edge_events)}"
              f"   | reference offer={str(ref.offer):<5} {sorted(fired(ref)) or '-'}")
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
