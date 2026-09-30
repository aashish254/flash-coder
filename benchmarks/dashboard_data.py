"""Collect the numbers every published Flash Coder chart is allowed to use.

The rule this file exists to enforce: a chart may only carry a figure that either
(a) a run in this repo prints, or (b) is quoted with its source and labelled as
third-party. There is no third category. That rule was broken once — a dashboard
was generated with invented competitor latencies and a fabricated monthly-cost
table — and the commit is on the record as reverted.

So this script does not draw anything. It measures, and it writes JSON:

    python benchmarks/dashboard_data.py            # -> benchmarks/results/...json
    python benchmarks/dashboard_data.py --repeats 5

Four sources, all local:

1. A committed §6 battery witness (a `battery_reread` print). Per-vector check
   counts and mutant counts come out of the log's own text, never retyped.
2. A committed cross-tool witness (a `market_compare` print). Every competitor
   figure on the page is a row of that table.
3. Wall-clock timing of every module selftest, `--repeats` runs each, reported as
   median with the min/max spread beside it. A single timing is not a property.
4. The graph blast-radius query against its own budget, read from the line
   `flash.graph --selftest` prints.

Everything is relative to this file's parent directory, so it runs from a clone or
an unpacked sdist and prints no host path.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"

# The §6 witness the counts are parsed from. A dated print, not a live run: the
# counts it carries are the counts the README publishes. Moved to the R-7.15c/h
# pass's print the day that pass ran — 37 lines, `checks 1420 … mutants 167` —
# because the page publishing yesterday's total while the tree prints a
# larger one is the same breach as publishing a number no run printed.
WITNESS = RESULTS / "battery_reread_r715h_20260930.log"

# The cross-tool print, same rule: a competitor's number enters the page only if a
# run wrote it into this file. One dated witness, parsed rather than quoted.
MARKET_WITNESS = RESULTS / "market_compare_20260928.log"

# `OK   <label>   <n>/<m>` with an optional `(+ k mutants)` tail, which is exactly
# what battery_reread prints.
LINE = re.compile(r"^OK\s+(?P<label>.+?)\s+(?P<got>\d+)/(?P<want>\d+)"
                  r"(?:\s+\(\+\s+(?P<mutants>\d+)\s+mutants?\))?")
TOTAL = re.compile(r"^checks (?P<checks>\d+)\s+oracle (?P<oracle>\d+)\s+"
                   r"§6 total (?P<total>\d+)\s+mutants (?P<mutants>\d+)")

# Only module selftests are timed: each is a single `python -m flash.X --selftest`
# with no model, and the whole set costs a couple of minutes. The wide benchmark
# vectors (dbg_band's 172 checks, the suites) are counted from the witness but not
# timed here — timing them would cost more than the chart is worth.
TIMED = [
    "harness", "power", "jobs", "trace", "web", "grammar", "patches", "debug",
    "tourney", "confidence", "sandbox", "checkpoint", "train", "graph", "ambient",
]

# `flash.graph --selftest` prints its own latency line; these captures pull the
# quoted timings and the instrument's size out of it.
GRAPH_FIXTURE = re.compile(r"fixtures query ([\d.]+) ms of (\d+) ms budget")
GRAPH_WIDE = re.compile(r"wide query ([\d.]+) ms")
GRAPH_COLD = re.compile(r"cold index: fixtures ([\d.]+) ms, wide ([\d.]+) ms "
                        r"\((?P<nodes>\d+) nodes, (?P<edges>\d+) edges, "
                        r"after a (?P<build>[\d.]+) ms build\)")
GRAPH_WARM = re.compile(r"a second pass over the wide repo costs ([\d.]+) ms")

# The cross-tool table: `arm  pass  s/task  requests  tokens`. The header row cannot
# match it (its second field is the word `pass`, not a fraction), so the parser needs
# no special case to skip it.
MARKET_ROW = re.compile(r"^(?P<arm>.+?)\s+(?P<passed>\d+)/(?P<n>\d+)\s+"
                        r"(?P<seconds>[\d.]+)\s+(?P<requests>\d+)\s+(?P<tokens>\d+)\s*$")
MARKET_SUITE = re.compile(r"^suite: (?P<suite>\S+) .*?(?P<tasks>\d+) tasks")
MARKET_GRADER = re.compile(r"^grader check: (?P<got>\d+)/(?P<want>\d+) stored reference")


def run(argv: list[str], timeout: int = 900) -> tuple[int, float, str]:
    """Return (rc, wall seconds, stdout) for one child run from the repo root."""
    start = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, *argv], cwd=ROOT,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, time.perf_counter() - start, ""
    return proc.returncode, time.perf_counter() - start, proc.stdout


def parse_witness(path: Path) -> dict:
    """Per-vector counts and the totals line, read out of a committed print."""
    text = path.read_text(errors="ignore")
    vectors, totals = [], None
    for line in text.splitlines():
        m = LINE.match(line)
        if m:
            vectors.append({
                "label": m["label"].strip(),
                "checks": int(m["got"]),
                "expected": int(m["want"]),
                "mutants": int(m["mutants"]) if m["mutants"] else 0,
                "kind": "module" if m["label"].strip().startswith("flash.")
                        or "selftest" in m["label"] else "vector",
            })
            continue
        t = TOTAL.match(line)
        if t:
            totals = {k: int(v) for k, v in t.groupdict().items()}
    if not vectors or totals is None:
        raise SystemExit(f"dashboard_data: {path.name} carries no parsable §6 print")
    return {"witness": path.name, "vectors": vectors, "totals": totals}


def parse_market(path: Path) -> dict:
    """The cross-tool table and its two gates, read out of the committed print.

    Refuses rather than emitting an empty `arms` list: a panel that renders "no
    competitor was run" from a file it failed to parse is the invention this
    pipeline exists to prevent."""
    text = path.read_text(errors="ignore")
    arms, suite, grader = [], None, None
    for line in text.splitlines():
        m = MARKET_ROW.match(line)
        if m:
            arms.append({"arm": m["arm"].strip(), "passed": int(m["passed"]),
                         "tasks": int(m["n"]), "seconds_per_task": float(m["seconds"]),
                         "requests": int(m["requests"]), "tokens": int(m["tokens"])})
            continue
        if suite is None:
            suite = MARKET_SUITE.match(line)
        elif grader is None:
            grader = MARKET_GRADER.match(line)
    if not arms or suite is None or grader is None:
        raise SystemExit(f"dashboard_data: {path.name} carries no parsable cross-tool "
                         f"table ({len(arms)} arm rows, suite {bool(suite)}, "
                         f"grader check {bool(grader)})")
    return {
        "witness": path.name,
        "suite": suite["suite"],
        "tasks": int(suite["tasks"]),
        "grader_check": f"{grader['got']}/{grader['want']}",
        "arms": arms,
        "note": "Watts per task is not in this table: measuring it needs sudo "
                "(SPEC §9, G6).",
    }


def time_selftests(repeats: int) -> list[dict]:
    """Median wall time per module selftest, with the spread it arrived in."""
    out = []
    for mod in TIMED:
        runs = []
        for _ in range(repeats):
            rc, secs, _stdout = run(["-m", f"flash.{mod}", "--selftest"])
            if rc != 0:
                print(f"  !! flash.{mod} --selftest exited {rc}; not timed")
                runs = []
                break
            runs.append(secs)
        if not runs:
            continue
        out.append({
            "module": mod,
            "median_s": round(statistics.median(runs), 3),
            "min_s": round(min(runs), 3),
            "max_s": round(max(runs), 3),
            "repeats": len(runs),
        })
        print(f"  flash.{mod:<11} median {statistics.median(runs):6.2f}s "
              f"(spread {min(runs):.2f}-{max(runs):.2f}, n={len(runs)})")
    return out


def graph_latency() -> dict:
    """The blast-radius query's own printed latency against its own budget."""
    rc, _secs, stdout = run(["-m", "flash.graph", "--selftest"])
    fix = GRAPH_FIXTURE.search(stdout)
    wide = GRAPH_WIDE.search(stdout)
    cold = GRAPH_COLD.search(stdout)
    warm = GRAPH_WARM.search(stdout)
    if rc != 0 or not fix:
        print("  !! graph latency not printed; the chart will say so rather than guess")
        return {"measured": False}
    return {"measured": True, "fixture_ms": float(fix[1]),
            "budget_ms": float(fix[2]),
            "wide_ms": float(wide[1]) if wide else None,
            "wide_nodes": int(cold["nodes"]) if cold else None,
            "wide_edges": int(cold["edges"]) if cold else None,
            "build_ms": float(cold["build"]) if cold else None,
            "warm_ms": float(warm[1]) if warm else None}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="benchmarks/dashboard_data.py")
    ap.add_argument("--repeats", type=int, default=3,
                    help="timed runs per module selftest (default 3)")
    ap.add_argument("--out", default=None,
                    help="where to write the JSON (default: benchmarks/results/)")
    args = ap.parse_args(argv)

    print(f"dashboard_data: parsing {WITNESS.name}")
    data = parse_witness(WITNESS)
    print(f"  {len(data['vectors'])} vectors, totals {data['totals']}")

    print(f"dashboard_data: parsing {MARKET_WITNESS.name}")
    data["market"] = parse_market(MARKET_WITNESS)
    print(f"  {len(data['market']['arms'])} arms on {data['market']['tasks']} "
          f"{data['market']['suite']} tasks, grader self-check "
          f"{data['market']['grader_check']}")

    print(f"dashboard_data: timing {len(TIMED)} module selftests x {args.repeats}")
    data["timings"] = time_selftests(args.repeats)
    data["graph_latency"] = graph_latency()
    data["source_of_truth"] = {
        "counts": "the §6 battery print named in `witness`",
        "timings": "wall clock of `python -m flash.<mod> --selftest` on this box",
        "competitor_figures": "one arm measured on this box against these weights, "
                              "named in `market.witness`; a tool that was not run has "
                              "no figure here",
    }

    out = Path(args.out) if args.out else RESULTS / "dashboard_data.json"
    out.write_text(json.dumps(data, indent=2) + "\n")
    print(f"dashboard_data: wrote {out.relative_to(ROOT)} "
          f"({len(data['timings'])} timings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
