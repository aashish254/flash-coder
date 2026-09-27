"""R-1.1b, LIVE: what did the four arms actually score, and did each arm's
injection actually reach the model?

The offline vector (`hint_ab_check.py`) proves the suite can tell the arms apart;
this file reads the arm runs and reports the only numbers that are claims about
the product: pass@1, pass@N and the retry count per arm, plus the two checks that
keep those honest —

  * the arm is read from the session's OWN `session_start` params
    (`no_source_hint` / `no_graph_hint`), never from the order the sessions are
    handed in, because a mislabelled arm is a wrong result printed confidently;
  * an ON arm must be seen injecting. `Attempt.err` is not the conversation
    (`flash/trace.py`'s `CAPTURE` under `--trace-full` is the only thing that
    stores prompts), so this counts retry prompts carrying each block's header
    and requires a non-zero count where the switch is on and zero where it is
    off. That is R-1.1's correction, enforced on every run instead of discovered
    once.

The attempt-0 identity control is printed, not gated: attempt 0 is greedy at
temperature 0 with a fixed seed and hints only enter retries, so the arms SHOULD
share one first answer per task. When they do, pass@1 is a measurement of the
same starting point in all four arms and the whole delta belongs to the blocks;
when they do not, the comparison is contaminated and the line says so.

    python benchmarks/hint_ab_report.py SESSION_A SESSION_B ...
    python benchmarks/hint_ab_report.py --dir benchmarks/results/traces --since 20260927
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash.lsp import HINT_HEADER                        # noqa: E402
from flash.graph import SCOPE_HEADER                     # noqa: E402

ARMS = {(False, False): "both", (True, False): "source only",
        (False, True): "graph only", (True, True): "off"}


def load(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def arm_of(params: dict) -> str:
    """Which blocks the session's own record says it withheld.

    The key is (graph OFF, source OFF) and the label names the blocks left ON, so
    a session that set neither flag is the `both` arm. Reading the flags in the
    other order would print a result for the wrong arm, confidently.
    """
    return ARMS.get((bool(params.get("no_graph_hint")),
                     bool(params.get("no_source_hint"))), "?unlabelled")


def session(path: Path) -> dict:
    rows = load(path)
    start = next((r for r in rows if r.get("type") == "session_start"), None)
    if start is None:
        raise SystemExit(f"{path.name}: no session_start, cannot read its arm")
    p = start.get("params") or {}
    tasks: dict[str, dict] = {}
    for r in rows:
        if r.get("type") not in ("generate", "verify"):
            continue
        t = tasks.setdefault(r["task_id"], {"gens": 0, "solved": False,
                                            "first_ok": None, "a0": ""})
        if r["type"] == "generate":
            t["gens"] += 1
            if int(r.get("attempt", -1)) == 0:
                t["a0"] = hashlib.sha1(
                    str(r.get("output", "")).encode()).hexdigest()[:12]
        else:
            if r.get("ok") and t["first_ok"] is None:
                t["first_ok"] = int(r.get("attempt", -1)) + 1
            t["solved"] = t["solved"] or bool(r.get("ok"))
    # Injection, at the seam the model reads: retry prompts only.
    inj = {"source": 0, "graph": 0, "retries": 0}
    for r in rows:
        if r.get("type") != "generate" or int(r.get("attempt", -1)) < 1:
            continue
        prompt = r.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            continue
        inj["retries"] += 1
        if HINT_HEADER in prompt:
            inj["source"] += 1
        if SCOPE_HEADER in prompt:
            inj["graph"] += 1
    return {"file": path.name, "arm": arm_of(p), "params": p, "tasks": tasks,
            "attempts": p.get("attempts"), "tier": p.get("small"),
            "trace_full": inj["retries"] > 0 or "prompt" in
            next((r for r in rows if r.get("type") == "generate"), {}),
            **inj}


def score(ses: dict) -> dict:
    n = len(ses["tasks"])
    p1 = sum(1 for t in ses["tasks"].values() if t["first_ok"] == 1)
    pn = sum(1 for t in ses["tasks"].values() if t["solved"])
    return {"n": n, "pass1": p1, "passN": pn,
            "retries": sum(t["gens"] for t in ses["tasks"].values()) - n,
            "attempts": ses["attempts"]}


def report(sessions: list[dict]) -> int:
    fails: list[str] = []
    ids = {ses["file"]: set(ses["tasks"]) for ses in sessions}
    for ses in sessions:
        if ses["arm"] == "?unlabelled":
            fails.append(f"{ses['file']}: params do not name an arm")
    if len({frozenset(v) for v in ids.values()}) > 1:
        fails.append("the arms ran different task sets, so their denominators "
                     "are not the same suite")
        for ses in sessions:
            print(f"  ..  {ses['file']}: {len(ses['tasks'])} tasks")
    width = max((len(ses["arm"]) for ses in sessions), default=4)
    print(f"  {'arm':<{width}}  {'n':>3}  {'pass@1':>7}  "
          f"{'pass@N':>7}  {'retries':>8}  {'src inj':>8}  {'grp inj':>8}  "
          f"{'a0 sha1 set':>12}")
    for ses in sessions:
        sc = score(ses)
        a0 = {t["a0"] for t in ses["tasks"].values()}
        want_src = ses["arm"] in ("both", "source only")
        want_grp = ses["arm"] in ("both", "graph only")
        if want_src and ses["source"] == 0:
            fails.append(f"{ses['file']}: {ses['arm']} arm injected the source "
                         "block into ZERO retry prompts")
        if not want_src and ses["source"]:
            fails.append(f"{ses['file']}: {ses['arm']} arm leaked the source "
                         f"block into {ses['source']} retry prompts")
        if want_grp and ses["graph"] == 0:
            fails.append(f"{ses['file']}: {ses['arm']} arm injected the "
                         "dependents block into ZERO retry prompts")
        if not want_grp and ses["graph"]:
            fails.append(f"{ses['file']}: {ses['arm']} arm leaked the "
                         f"dependents block into {ses['graph']} retry prompts")
        if not ses["trace_full"]:
            fails.append(f"{ses['file']}: no stored prompts — run the arms with "
                         "--trace-full, or the injection claim has no witness")
        print(f"  {ses['arm']:<{width}}  {sc['n']:>3}  {sc['pass1']:>3}/{sc['n']}"
              f"  {sc['passN']:>3}/{sc['n']}  {sc['retries']:>8}  "
              f"{ses['source']:>8}  {ses['graph']:>8}  "
              f"{len(a0):>4} distinct")
    off = [ses for ses in sessions if ses["arm"] == "off"]
    if off:
        base = score(off[0])
        for ses in sessions:
            if ses["arm"] == "off":
                continue
            sc = score(ses)
            print(f"  delta vs off, {ses['arm']:<{width}}: pass@N "
                  f"{sc['passN'] - base['passN']:+d} over n={sc['n']} "
                  f"(pass@1 {sc['pass1'] - base['pass1']:+d}), "
                  f"retries {sc['retries'] - base['retries']:+d}")
    shared = [{t["a0"] for t in ses["tasks"].values()} for ses in sessions]
    if shared and all(s == shared[0] for s in shared):
        print("  ..  control: every arm shares one greedy attempt-0 answer per "
              "task, so pass@1 is identical and the whole delta is the blocks")
    else:
        print("  ..  WARNING: the arms do NOT share their attempt-0 answers, so "
              "their first tries differ and pass@N is not a clean block effect")
    print(f"\nhint-ab live: {len(sessions)} arms, "
          f"{sessions[0]['params'].get('tasks') if sessions else '?'}, "
          f"attempts={sessions[0]['attempts'] if sessions else '?'}")
    for f in fails:
        print(f"FAIL {f}")
    return 1 if fails else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sessions", nargs="*", help="session ids or trace paths")
    ap.add_argument("--dir", default=str(ROOT / "benchmarks" / "results" /
                                         "traces"))
    ap.add_argument("--since", default="", help="only sessions on/after this date")
    ap.add_argument("--tasks", default="",
                    help="only arms that ran this task file")
    args = ap.parse_args(argv)
    paths: list[Path] = []
    for s in args.sessions:
        p = Path(s)
        paths.append(p if p.is_file() else
                     Path(args.dir) / (s if s.endswith(".jsonl")
                                       else f"{s}.jsonl"))
    if not paths:
        d = Path(args.dir)
        paths = sorted(p for p in d.glob("*.jsonl")
                       if p.stem[:8] >= args.since)
    sessions = []
    for p in paths:
        if not p.is_file():
            raise SystemExit(f"no trace at {p}")
        ses = session(p)
        if args.tasks and ses["params"].get("tasks") != args.tasks:
            continue
        sessions.append(ses)
    if not sessions:
        print("FAIL no sessions left after filtering")
        return 1
    return report(sessions)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
