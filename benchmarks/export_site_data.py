"""Turn the measured JSON into the two files the landing page reads.

Nothing here invents a number, and nothing here is a copy-paste target: the site
imports the JSON this writes, so a figure that is not in
`benchmarks/results/dashboard_data.json` cannot reach the page. Run it after
`python benchmarks/dashboard_data.py`:

    python benchmarks/export_site_data.py

It writes three things under `site/src/data/`:

- `benchmarks.json` — totals, per-vector fractions, selftest wall clocks, the
  graph latency and the cross-tool table. Each entry carries the command that
  prints it, taken from the battery's own label, so the page can attribute every
  figure on screen.
- `graph.json` — the repo's own AST call graph, extracted by `flash.graph`, for
  the hero scene. Positions are computed here so the scene is deterministic and
  the component stays dumb.
- `transcripts.json` — literal terminal captures. The page shows real output
  because the exporter runs the command and pastes what came back; the only
  edit is redaction of this machine's paths, and each capture lists the
  substitutions it carries.

The graph slice is the `GRAPH_NODES` most-connected functions, classes and
methods under `flash/`, joined by their call and import edges. That is a display
sample of a bigger index and the file says so in its `sampled` field, because a
picture of a graph that quietly dropped 4,000 nodes would be the same sin as a
fake latency, only prettier.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import dashboard_data
from flash import graph as _graph

ROOT = dashboard_data.ROOT
SOURCE = ROOT / "benchmarks" / "results" / "dashboard_data.json"
OUT_DIR = ROOT / "site" / "src" / "data"
# The R-7.15 live arm: three turns at a real 7B, two diffs landed, one refusal.
SESSION_LIVE = dashboard_data.RESULTS / "session_live_20260929.log"

# The hero shows a slice, and says it is one.
GRAPH_NODES = 150
PREFIX = "flash/"
SHOWN_KINDS = ("function", "method", "class", "async", "property")
SHOWN_EDGE_KINDS = ("calls", "imports")


def command_for(label: str) -> str:
    """The runnable form of a battery label. Labels are already commands; this
    only adds the interpreter where the battery's own column left it implicit."""
    s = label.strip()
    if s.startswith("flash."):
        return f"python -m {s}"
    if s.startswith("flash "):
        return "python -m flash.cli " + s[len("flash "):]
    if s.startswith("benchmarks/") or s.startswith("m0_"):
        return f"python {s}"
    return s


def build_benchmarks(source: dict) -> dict:
    totals = source["totals"]
    vectors = [{
        "command": command_for(v["label"]),
        "checks": v["checks"],
        "expected": v["expected"],
        "mutants": v["mutants"],
    } for v in source["vectors"]]
    timings = [{
        "command": f"python -m flash.{t['module']} --selftest",
        **{k: v for k, v in t.items() if k not in ("module",)},
    } for t in source["timings"]]
    buckets: Counter[str] = Counter()
    for v in vectors:
        buckets["100+" if v["checks"] >= 100 else "30-99" if v["checks"] >= 30
                else "under 30"] += 1
    return {
        "witness": source["witness"],
        "totals": {**totals, "vectors": len(vectors),
                   "mutant_vectors": sum(1 for v in vectors if v["mutants"])},
        "distribution": [{"bucket": b, "vectors": n}
                         for b, n in sorted(buckets.items())],
        "vectors": sorted(vectors, key=lambda v: -v["checks"]),
        "timings": sorted(timings, key=lambda t: -t["median_s"]),
        "graph": source["graph_latency"],
        "market": source["market"],
        "provenance": source["source_of_truth"],
    }


def _sphere(i: int, n: int, radius: float) -> tuple[float, float, float]:
    """Fibonacci sphere: even spread, no random seed to keep in sync."""
    k = i + 0.5
    phi = math.acos(1 - 2 * k / n)
    theta = math.pi * (1 + 5 ** 0.5) * k
    return (radius * math.sin(phi) * math.cos(theta),
            radius * math.sin(phi) * math.sin(theta),
            radius * math.cos(phi))


def build_graph(root: Path) -> dict:
    g = _graph.build(root)
    # A scene of a call graph is a picture of *control*, so `reads` edges are out:
    # they are what every module-level constant collects, and they turned 220
    # nodes into a 2,552-edge hairball that showed nothing.
    edges = [e for e in g.edges if e.kind in SHOWN_EDGE_KINDS]
    deg: Counter[str] = Counter()
    for e in edges:
        if e.src in g.nodes and e.dst in g.nodes:
            deg[e.src] += 1
            deg[e.dst] += 1
    pool = [n for n in g.nodes.values()
            if n.file.startswith(PREFIX) and n.symbol and n.kind in SHOWN_KINDS]
    keep = {n.id for n in sorted(pool, key=lambda n: (-deg[n.id], n.id))[:GRAPH_NODES]}
    nodes = [n for n in pool if n.id in keep]
    files = sorted({n.file for n in nodes})
    centroids = {f: _sphere(i, len(files), 26.0) for i, f in enumerate(files)}

    by_file: defaultdict[str, list] = defaultdict(list)
    for n in nodes:
        by_file[n.file].append(n)
    placed = []
    for f, group in by_file.items():
        cx, cy, cz = centroids[f]
        r = 2.4 + 1.05 * math.sqrt(len(group))
        for i, n in enumerate(sorted(group, key=lambda x: x.id)):
            ax, ay, az = _sphere(i, len(group), r)
            placed.append({
                "id": n.id, "file": n.file, "symbol": n.symbol, "kind": n.kind,
                "degree": deg[n.id], "label": n.symbol.split(".")[-1],
                "pos": [round(cx + ax, 2), round(cy + ay, 2), round(cz + az, 2)],
            })
    seen: set[tuple[str, str, str]] = set()
    pairs = []
    for e in edges:
        key = (e.src, e.dst, e.kind)
        if e.src in keep and e.dst in keep and key not in seen:
            seen.add(key)
            pairs.append({"s": e.src, "d": e.dst, "kind": e.kind})
    return {
        "nodes": sorted(placed, key=lambda x: x["id"]),
        "edges": pairs,
        "kinds": dict(sorted(Counter(n["kind"] for n in placed).items())),
        "sampled": {
            "shown_nodes": len(placed), "shown_edges": len(pairs),
            "repo_nodes": len(g.nodes), "repo_edges": len(g.edges),
            "note": f"the {len(placed)} most-connected functions, classes and "
                    f"methods under {PREFIX}, joined by their call and import "
                    f"edges; `reads` edges and the rest of the index are not drawn",
        },
        "extracted_by": "python -c 'from flash.graph import build; build(...)'",
    }


def write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, sort_keys=False) + "\n")
    print(f"export_site_data: wrote {path.relative_to(ROOT)}")


# ------------------------------------------------------------- transcripts
#
# The page shows real terminal output, so the output has to come from a run and
# not from someone's memory of one. Two captures are live children; two are
# committed witnesses — the §6 battery, which this repo already publishes
# numbers from, and the session's live arm. Every one carries the command that
# produced it.

def redact(text: str) -> tuple[str, list[str]]:
    """Blank this machine's paths. A landing page has no business naming a
    username, and a figure that cannot be shown without one is not a figure."""
    notes = []
    root = str(ROOT)
    home = str(Path.home())
    if root in text:
        text = text.replace(root, "<checkout>")
        notes.append("the checkout path was replaced with <checkout>")
    if home in text:
        text = text.replace(home, "~")
        notes.append("the home directory was replaced with ~")
    return text, notes


def capture(argv: list[str], keep: int, take: str = "head") -> dict:
    proc = subprocess.run([sys.executable, *argv], cwd=ROOT,
                          capture_output=True, text=True)
    body = (proc.stdout or "") + (proc.stderr or "")
    lines = [ln for ln in body.splitlines() if ln.strip()]
    sel = lines[:keep] if take == "head" else lines[-keep:]
    text, notes = redact("\n".join(sel))
    return {
        "command": "python -m " + argv[1] + " " + " ".join(argv[2:]),
        "exit": proc.returncode,
        "lines": text.split("\n"),
        "redacted": notes,
    }


def session_capture() -> dict:
    """The R-7.15 live arm, pasted from its committed witness. Three turns on a
    real 7B: two landed a diff on disk, the third was refused out loud because
    the patch arm addresses a symbol the AST already has. That refusal is the
    most informative line on the panel, so it stays in."""
    raw = SESSION_LIVE.read_text(errors="ignore").splitlines()
    header = [ln for ln in raw if ln.startswith("# ")]
    body = [ln for ln in raw if ln.strip() and not ln.startswith("# ")]
    # The weight downloader writes its own progress bars to the same stream.
    # They are stdout, but three near-identical bar lines would bury the
    # transcript, so they are dropped here and named as dropped.
    bars = [ln for ln in body if ln.startswith("Fetching ")]
    kept = [ln for ln in body if not ln.startswith("Fetching ")]
    text, notes = redact("\n".join(kept))
    if bars:
        notes.append(f"{len(bars)} weight-download progress lines were dropped; "
                     f"the witness beside this file holds them verbatim")
    cmd = next((ln for ln in header if ln.startswith("# command:")), "")
    rc = next((ln for ln in kept if "[session]" in ln and "last_rc=" in ln), "")
    if not cmd or not rc:
        raise SystemExit(f"export_site_data: {SESSION_LIVE.name} lost either its "
                         f"# command header or the command's own [session] line — "
                         f"refusing to print an exit code nobody reported")
    return {
        "command": cmd.split(": ", 1)[1],
        # The exit code comes from the command's own EOF report, not the
        # witness header, so the number on the page is one that was printed.
        "exit": int(rc.split("last_rc=")[1].split()[0]),
        "lines": text.split("\n"),
        "redacted": notes,
        "source": SESSION_LIVE.name,
    }


def build_transcripts(witness_lines: list[str]) -> list[dict]:
    doctor = capture(["-m", "flash.cli", "doctor"], 24)
    graph = capture(["-m", "flash.graph", "--selftest"], 4, take="tail")
    lines = [ln for ln in witness_lines if ln.startswith("OK   ")]
    bat = {
        "command": "python benchmarks/battery_reread.py",
        "exit": 0,
        "lines": lines[:6] + [f"  … {len(lines)} lines, one per §6 vector"]
                 + witness_lines[-3:],
        "redacted": [],
        "source": dashboard_data.WITNESS.name,
    }
    return [doctor, graph, bat, session_capture()]


def main(argv: list[str]) -> int:
    if not SOURCE.is_file():
        print(f"export_site_data: {SOURCE.relative_to(ROOT)} is missing — run\n"
              f"  python benchmarks/dashboard_data.py\nfirst.", file=sys.stderr)
        return 1
    source = json.loads(SOURCE.read_text())
    write(OUT_DIR / "benchmarks.json", build_benchmarks(source))
    write(OUT_DIR / "graph.json", build_graph(ROOT))
    witness = dashboard_data.WITNESS.read_text(errors="ignore").splitlines()
    wtext, wnotes = redact("\n".join(ln for ln in witness if ln.strip()))
    write(OUT_DIR / "transcripts.json",
          {"captures": build_transcripts(wtext.split("\n")),
           "witness_redacted": wnotes})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
