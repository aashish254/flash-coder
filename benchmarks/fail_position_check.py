"""Where do REAL hard-task failures land in the test? (P5 design input.)

The ledger stores `fail_output` as the failing assert's TEXT, not the
candidate's code, so a candidate's `score().passed` cannot be recomputed from
it directly. Locating that assert inside the task's test gives the position the
candidate died at — which IS the score it would have got (asserts run in order,
so everything before it passed).

Why this matters before building a tournament: §33.4's best-of-k needs the
oracle to pick a winner. If real failures cluster at the FIRST assert, then
every failing candidate scores 0 and `passed` cannot rank them — the
tournament's value must come from pass@k (a candidate that PASSES), not from
ranking the ones that don't.

`python benchmarks/fail_position_check.py`
"""
import json
import sys
import collections
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flash.harness import load_tasks

ROOT = Path(__file__).resolve().parent
SUITES = ("m3_hard_tasks.jsonl", "m3b_hard_tasks.jsonl")


def main() -> int:
    tasks = {}
    for f in SUITES:
        for t in load_tasks(ROOT / "tasks" / f):
            tasks[t["id"]] = t

    rows = [json.loads(l) for l in open(ROOT / "results" / "ledger.jsonl")
            if l.strip()]
    pos = collections.Counter()
    seen = set()
    unmatched = 0
    for r in rows:
        fo = r.get("fail_output") or ""
        tid = r["task_id"]
        if tid not in tasks or "FAILING_ASSERT:" not in fo:
            continue
        key = (tid, fo)
        if key in seen:                      # the same failure recorded twice
            continue
        seen.add(key)
        stmt = fo.split("FAILING_ASSERT: ", 1)[1].split(" |")[0].strip()
        asserts = [l.strip() for l in tasks[tid]["test"].splitlines()
                   if l.startswith("assert")]
        at = next((i for i, a in enumerate(asserts) if a == stmt), None)
        if at is None:
            unmatched += 1
            continue
        pos[(at, len(asserts))] += 1

    n = sum(pos.values())
    if not n:
        print("no locatable hard-task failures in the ledger")
        return 1
    print(f"distinct locatable real hard-task failures: {n}"
          + (f"  ({unmatched} unmatched assert text(s))" if unmatched else ""))
    for (at, total), c in sorted(pos.items(), key=lambda x: (-x[1], x[0])):
        print(f"  died at assert {at + 1}/{total}: {c} failure(s)")
    first = sum(c for (at, _), c in pos.items() if at == 0)
    print(f"\nfirst-assert deaths: {first}/{n} "
          f"({first / n:.0%}) — a candidate that dies there scores passed=0")
    print(f"later-assert deaths: {n - first}/{n} — the only ones `passed` can rank")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
