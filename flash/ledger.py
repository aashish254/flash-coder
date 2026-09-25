"""The ledger (PLAN §M3): every policy run appends one JSONL record.

This is the data flywheel. Prospective self-assessment is overconfident
(App. A, 2026-09-23), so the REAL router must be learned from outcomes:
features (task text, ctx size, suite) -> label (did the small tier solve it?
how many attempts? what did escalation cost?). Every `solve_routed` call
appends here automatically.
"""
from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path

LEDGER_FILE = (Path(__file__).resolve().parent.parent
               / "benchmarks" / "results" / "ledger.jsonl")


def record(entry: dict, path: Path = LEDGER_FILE) -> None:
    """Append one outcome record. Never raises — the loop must not die for telemetry."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), **entry}) + "\n")
    except Exception:
        pass


def load(path: Path = LEDGER_FILE) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def summary(rows: list[dict]) -> str:
    if not rows:
        return "ledger is empty — run `flash run-suite` to start the flywheel"
    n = len(rows)
    solved = sum(r["solved"] for r in rows)
    by_tier = Counter(r["tier"] for r in rows)
    esc = by_tier.get("big", 0)
    fails = by_tier.get("failed", 0)
    shed = by_tier.get("shed", 0)
    esc_rescued = sum(r["solved"] for r in rows if r["tier"] == "big")
    small_time = sum(r["seconds"] for r in rows if r["tier"] == "small")
    big_time = sum(r["seconds"] for r in rows if r["tier"] not in ("small", "shed"))
    lines = [
        f"runs: {n}   solved: {solved}/{n} ({solved / n:.0%})",
        f"tiers: small {by_tier.get('small', 0)} · escalated {esc} "
        f"(rescued {esc_rescued}/{esc}) · failed {fails}"
        + (f" · shed {shed}" if shed else ""),
        f"time: small-tier {small_time:.0f}s · big-tier {big_time:.0f}s "
        f"· total {small_time + big_time:.0f}s",
    ]
    if shed:
        why = Counter(r.get("shed", "?") for r in rows if r["tier"] == "shed")
        lines.append("shed because (§34.1 governor): "
                     + "; ".join(f"{k} x{v}" for k, v in why.most_common(3)))
    # per-task view: the future router's training signal
    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r["task_id"], []).append(r)
    flaky = [tid for tid, rs in by_task.items()
             if len({r["solved"] for r in rs}) > 1 or any(r["tier"] != "small" for r in rs)]
    if flaky:
        lines.append(f"needs-big or flaky (router labels): {', '.join(sorted(flaky))}")
    return "\n".join(lines)
