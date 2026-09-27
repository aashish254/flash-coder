"""Build benchmarks/tasks/hint_ab_tasks.jsonl — the R-1.1b A/B instrument.

Why this generator exists: the first version of this suite was written BY HAND,
and hand-writing a "wrong first answer" guessed at the difficulty. The live arms
settled the question in 97 seconds: the frozen 7B tier solved 8 of those 10 tasks
on its greedy attempt 0, so 8 tasks never reached a retry and the A/B's real
denominator was n=2 — the same degeneracy `gen_dbg_band.py` documents for the
debugger arms, arriving here by a different route.

So the band is measured instead. `--pilot` points at a `run-suite --attempts 1
--trace-full` session over the candidate pool, and a task qualifies only if:

  * the loop's own `verify` record says its attempt-0 answer FAILED — the task
    reaches a retry, which is the only place a hint can enter;
  * that answer was not cut off by the token cap (`completion_tokens >=
    max_tokens`) — a truncated program fails on its syntax, and the retry would
    be scoring the cap rather than the hint;
  * something repo-defined is AT ISSUE in the verdict the loop actually composed
    (`STATIC: …` when `static_check` fires, else `diagnose`'s verdict) — on a
    silent ranking `_perceive` injects nothing under either arm, so the task
    cannot tell the arms apart;
  * its (failure class, top at-issue symbol) pair has not already been claimed
    by a kept task. This
    tier's single most common eligible failure is forgetting to import
    `BULK_MIN_QTY`, and three tasks that fail that way are one observation
    counted three times — the vector's signature gate would rightly refuse them,
    so the generator does not emit them. Tasks are visited in sorted id order,
    which makes the survivor of a tie the alphabetically-first one.

`naive` is written as the exact string `solve` verified — `extract_code` over the
stored output, not the raw prose-and-fences answer — because the hint ranks on
that code, and a record holding the raw text would have the vector certify a
`STATIC: unterminated string literal` that no live arm ever sees. `naive_from`
names the session, so the answer's provenance travels with the record.

Deterministic given the pilot trace: no RNG, no clock, no model. Re-running it
over the same session rewrites the same bytes.

    python benchmarks/gen_hint_ab.py --pilot A --pilot B --pilot C
    python benchmarks/gen_hint_ab.py --pilot A --report    # also the rejects
    python benchmarks/gen_hint_ab.py --pilot A --dry-run   # print, don't write

Several pilots are the union of their bands: the first two runs of this project's
A/B covered different candidate files, and one task's attempt 0 is greedy at
temperature 0 with a fixed seed, so a task that appears twice must agree with
itself — disagreement is a `SystemExit`, not a silent last-write-wins, because a
tier that cannot reproduce its own first answer cannot anchor a frozen suite.

Then certify: `python benchmarks/hint_ab_check.py`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import graph, harness                                # noqa: E402
from flash.harness import extract_code                          # noqa: E402
from flash.lsp import SymbolIndex, symbols_involved              # noqa: E402
from flash.perceive import format_errors, static_check          # noqa: E402

POOL = ROOT / "benchmarks" / "tasks" / "hint_ab_candidates.jsonl"
OUT = ROOT / "benchmarks" / "tasks" / "hint_ab_tasks.jsonl"


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def pilot_rows(session: str) -> list[dict]:
    """The trace of one pilot run, by path or by session id."""
    p = Path(session)
    if not p.is_file():
        p = ROOT / "benchmarks" / "results" / "traces" / f"{session}.jsonl"
    if not p.is_file():
        raise SystemExit(f"no pilot trace at {session} (looked for {p})")
    return load(p)


def attempt_zero(rows: list[dict]) -> dict[str, dict]:
    """task_id -> the loop's own first answer and first verdict."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("type") == "generate" and int(r.get("attempt", -1)) == 0:
            out[r["task_id"]] = {
                "code": extract_code(r.get("output", "")),
                "capped": int(r.get("completion_tokens", 0)) >= int(
                    r.get("max_tokens", 1 << 30)),
            }
        elif r.get("type") == "verify" and int(r.get("attempt", -1)) == 0:
            got = out.get(r["task_id"])
            if got is not None:
                got["ok"] = bool(r.get("ok"))
                got["vkind"] = r.get("kind", "")
    return out


def loop_verdict(task: dict, answers: dict[str, dict]) -> str:
    """The verdict text `_perceive` reads on this task's first retry.

    `solve` runs `static_check` on the extracted code and SHORT-CIRCUITS the test
    run on a finding, so the text `_perceive` ranks on is `STATIC: …` for those
    answers and `diagnose`'s GOT/WANT for the rest. This reproduces that choice
    rather than picking the nicer of the two. Pass/fail is NOT recomputed here:
    the band takes it from the pilot's own `verify` record.
    """
    rec = answers[task["id"]]
    naive = rec["code"]
    static = format_errors(static_check(naive))
    if static:
        return f"STATIC: {static}"
    return harness.diagnose(naive, task["test"])[1]


def compiles(code: str) -> bool:
    """Whether the answer is a whole program, which is the band's floor.

    A cap-cut answer fails on its syntax, so its retry scores the harness's
    token budget rather than the symbol the hint names — and `hint_ab_check.py`
    gates on exactly this property, which is why it is a function here.
    """
    try:
        compile(code, "<naive>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def vclass(verdict: str) -> str:
    """The verdict's failure class, for SELECTION only.

    `hint_ab_check.py`'s `exc_class` is the authority and its gate re-checks the
    pair this heuristic dedupes on; a deliberate near-miss here costs a re-freeze,
    not a false green.
    """
    if verdict.startswith("STATIC:"):
        m = re.search(r"(undefined name|syntax error|before assignment)", verdict,
                      re.I)
        return "static:" + (m.group(1).lower().replace(" ", "-") if m
                            else "other")
    names = sorted(set(re.findall(r"\b([A-Z]\w*(?:Error|Exception))\b", verdict)))
    return names[0] if names else ("assert-diff" if verdict else "no verdict")


def rank(idx: SymbolIndex, verdict: str, naive: str) -> list[str]:
    return [s.name for s in symbols_involved(idx, verdict, naive, 3)]


def select(pilots: list[str], pool_path: Path,
           report: bool) -> tuple[list[dict], list]:
    tasks = {t["id"]: t for t in load(pool_path)}
    answers: dict[str, dict] = {}
    for p in pilots:
        for tid, rec in attempt_zero(pilot_rows(p)).items():
            old = answers.get(tid)
            if old is not None and old["ok"] != rec["ok"]:
                # Greedy attempt 0 is temperature 0 with a fixed seed, so two
                # pilots of one task agreeing is the control; a disagreement
                # means the tier is not reproducible and the band is not frozen.
                raise SystemExit(f"{tid}: pilots {old['from']} and {p} disagree "
                                 f"on attempt 0 ({old['ok']} vs {rec['ok']})")
            if old is None:
                rec["from"] = p
                answers[tid] = rec
    missing = sorted(set(answers) - set(tasks))
    if missing:
        raise SystemExit(f"pilot ran ids the pool does not have: {missing}")
    indexes: dict[str, SymbolIndex] = {}
    keep: list[dict] = []
    drops: list[tuple[str, str, str]] = []
    kept_sig: set[tuple[str, str]] = set()
    for tid, rec in sorted(answers.items()):
        task = tasks[tid]
        ctx = str(ROOT / task["context"])
        if rec.get("ok"):
            drops.append((tid, "solved blind on attempt 0", "never retries"))
            continue
        if rec["capped"]:
            drops.append((tid, "answer hit the token cap", "syntax, not a symbol"))
            continue
        verdict = loop_verdict(task, {tid: rec})
        if not compiles(rec["code"]):
            drops.append((tid, "extracted answer does not compile",
                          "a cut-off program, not a symbol"))
            continue
        idx = indexes.setdefault(ctx, SymbolIndex.build(ctx))
        names = rank(idx, verdict, rec["code"])
        if not names:
            drops.append((tid, "no repo-defined symbol at issue",
                          "both blocks are silent"))
            continue
        dep = graph.scope_hint(ctx, verdict, rec["code"], index=idx)
        if not dep:
            drops.append((tid, "dependents block empty", "nothing to show"))
            continue
        sig = (f"{vclass(verdict)}/{rec.get('vkind', '')}", names[0])
        if sig in kept_sig:
            # One signature, one task: three tasks that all fail on forgetting to
            # import `BULK_MIN_QTY` are one observation counted three times, and
            # `hint_ab_check.py`'s signature gate would (correctly) refuse them.
            drops.append((tid, f"{sig[0]} on {sig[1]} again",
                          "one signature scores once"))
            continue
        kept_sig.add(sig)
        keep.append({"id": tid, "context": task["context"],
                     "prompt": task["prompt"], "test": task["test"],
                     "solution": task["solution"], "naive": rec["code"],
                     "naive_from": Path(rec["from"]).stem})
    keep.sort(key=lambda t: t["id"])
    if report:
        for tid, why, because in drops:
            print(f"  drop {tid:<28} {why} — {because}")
    return keep, drops


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="append", required=True,
                    help="pilot session id or trace path (attempts=1, "
                         "--trace-full); repeatable, and the band is their union")
    ap.add_argument("--pool", default=str(POOL))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--report", action="store_true", help="print the rejects too")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    keep, drops = select(args.pilot, Path(args.pool), args.report)
    for t in keep:
        print(f"  keep {t['id']:<28} naive={len(t['naive'])} chars "
              f"from {t['naive_from']}")
    print(f"\nhint_ab: {len(keep)}/{len(keep) + len(drops)} pool tasks are in "
          f"the retry-and-eligible band")
    if not keep:
        print("FAIL nothing qualified — the band is empty, so the A/B has no "
              "denominator; widen the pool or lower the tier")
        return 1
    if args.dry_run:
        return 0
    text = "".join(json.dumps(t) + "\n" for t in keep)
    path = Path(args.out)
    if path.is_file() and path.read_text() == text:
        print(f"unchanged: {path} already holds these {len(keep)} records")
        return 0
    path.write_text(text)
    print(f"wrote {len(keep)} records to {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
