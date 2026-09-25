"""Build the R-4.3 A/B substrate from measured failure, not by hand-picking.

`dbg_tasks.jsonl` (the seeded-bug suite) turned out to be useless as an
instrument: the fast tier solves all 8 on its first greedy attempt, so no retry
happens and no feedback of either kind is ever sent — an A/B on it can only
come out 8/8 vs 8/8. What the debugger can change is the outcome of a task the
model ALREADY failed, where the difference between arms is what it is told
about why.

So this selects by evidence: every task in the shipped suites that the small
tier failed on attempt 1 in `benchmarks/results/probe/*_one_attempt.log`
(`flash run-suite --attempts 1 --allow-big never`, 2026-09-26). Both arms then
run those with the same tier and the same attempt budget, and the only thing
that differs is the feedback.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "benchmarks/results/probe"
OUT = ROOT / "benchmarks/tasks/dbg_hard_tasks.jsonl"
FAIL = re.compile(r"^\s+\[\s*\w+\]\s+(\S+)\s+solved=False")


def failing() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for log in sorted(PROBE.glob("*_one_attempt.log")):
        suite = log.name.replace("_one_attempt.log", "")
        ids = [m.group(1) for m in (FAIL.match(l)
                                    for l in log.read_text().splitlines()) if m]
        if ids:
            out[suite] = ids
    return out


def main() -> int:
    picked = failing()
    if not picked:
        print(f"no probe logs in {PROBE} — run the attempt-1 sweep first")
        return 1
    rows, missing = [], set()
    for suite, ids in picked.items():
        src = ROOT / "benchmarks/tasks" / f"{suite}.jsonl"
        if not src.exists():
            missing.add(str(src))
            continue
        want = set(ids)
        for line in src.read_text().splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            if t["id"] in want:
                t["source_suite"] = suite
                t["dbg_reason"] = "failed the small tier's first greedy attempt"
                rows.append(t)
    if missing:
        print("suites named by a probe log but not found: " + ", ".join(sorted(missing)))
    rows.sort(key=lambda t: t["id"])
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} tasks to {OUT.name} "
          f"({', '.join(f'{k}:{len(v)}' for k, v in sorted(picked.items()))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
