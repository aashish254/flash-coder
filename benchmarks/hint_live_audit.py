"""Audit every captured live prompt for R-1.1's symbol hint.

I-6 lets a run be replayed, but only with `--trace-full`, which is the one flag
that stores the prompt text (`flash/trace.py`'s `CAPTURE`). So the question
"did the resolved source ever reach the MODEL, or only the record?" is answered
by scanning the stored prompts, not by reading a trace render — the render
prints `Attempt.err`, which is exactly where the hint used to go, and why a dead
seam looked live since the baseline commit.

    python benchmarks/hint_live_audit.py              # the correction holds
    python benchmarks/hint_live_audit.py --print      # show each hit
    python benchmarks/hint_live_audit.py --expect present   # require a live arm

Sessions are split on the date `loop.solve` was fixed (20260927), because the
two halves carry opposite claims. Pre-fix: NO stored prompt may contain the
header — a hit there means the correction this file documents is wrong and has
to be re-opened. Post-fix: the count is reported, not required, because a run
whose failures name no repo-defined symbol correctly injects nothing; `--expect
present` asserts the other half, that at least one captured retry prompt DOES
carry it, and is what the live arm was checked with.

Retry prompts are the rows whose own `attempt` field is >= 1 — the loop numbers
every generation, so no grouping or inference is needed. (An earlier cut of this
file keyed on a `task` field that does not exist, collapsed 198 task groups into
14, and printed 246 retries out of 260 rows — a number true of nothing.)
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACES = ROOT / "benchmarks" / "results" / "traces"
# The default is R-1.1's hint; `--header` audits any other injected block with
# the same two-sided claim, so R-1.3b's dependents block is checked by this file
# rather than by a near-duplicate that has to re-derive which rows are retries.
HEADER = "Symbols in play"          # lsp.symbol_hint's own header line
FIXED_ON = "20260927"               # the session-date prefix of the fix commit


def scan(traces: Path, header: str = HEADER) -> dict:
    """Counts by session date: stored prompts, retry prompts, and the hits."""
    prompts, retries, hits = {}, {}, {}
    for path in sorted(traces.glob("*.jsonl")):
        day = path.name[:8]
        for line in path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = rec.get("prompt")
            if not (isinstance(prompt, str) and prompt):
                continue
            prompts[day] = prompts.get(day, 0) + 1
            attempt = rec.get("attempt")
            if not (isinstance(attempt, int) and attempt >= 1):
                continue
            retries[day] = retries.get(day, 0) + 1
            if header in prompt:
                hits.setdefault(day, []).append(
                    (path.name, rec.get("task_id"), attempt))
    return {"prompts": prompts, "retries": retries, "hits": hits}


def half(counts: dict, side: str) -> tuple[int, int, list]:
    """(stored prompts, retry prompts, hits) for one side of the fix date."""
    def keep(day: str) -> bool:
        return day < FIXED_ON if side == "pre" else day >= FIXED_ON

    days = [d for d in sorted(set(counts["prompts"]) | set(counts["hits"]))
            if keep(d)]
    hit_list = [h for d in days for h in counts["hits"].get(d, [])]
    return (sum(counts["prompts"].get(d, 0) for d in days),
            sum(counts["retries"].get(d, 0) for d in days), hit_list)


def main(argv: list) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--expect", choices=["corrected", "present"],
                    default="corrected")
    ap.add_argument("--header", default=HEADER,
                    help="the injected block's own header line to look for")
    ap.add_argument("--print", dest="show", action="store_true")
    ap.add_argument("--traces", default=str(TRACES))
    args = ap.parse_args(argv)
    traces = Path(args.traces)
    if not traces.is_dir():
        print(f"FAIL no trace corpus at {traces}")
        return 1
    counts = scan(traces, args.header)
    pre = half(counts, "pre")
    post = half(counts, "post")
    for label, (p, r, hits) in (("pre-fix ", pre), ("post-fix", post)):
        print(f"{label} prompts stored {p:4d} · retries {r:3d} · "
              f"carrying {args.header!r}: {len(hits)}")
    if args.show:
        for run, task, attempt in pre[2] + post[2]:
            print(f"  HIT {run} task={task} attempt={attempt}")
    ok = pre[2] == []
    if ok:
        print(f"OK pre-fix corpus clean: no prompt from before {FIXED_ON} carries "
              f"{args.header!r}, so no older live run may credit symbol injection "
              "with anything it solved")
    else:
        print(f"FAIL {len(pre[2])} pre-fix prompt(s) carry the hint — the "
              "correction is wrong, re-open R-1.1")
    if args.expect == "present":
        if post[2]:
            print(f"OK live arm: {len(post[2])} post-fix retry prompt(s) carry it")
        else:
            print(f"FAIL no post-fix retry prompt carries {args.header!r} — the "
                  "live half of this clause is unverified")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
