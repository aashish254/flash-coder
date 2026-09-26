"""Vector for the R-4.3 wide-band instrument: `benchmarks/tasks/dbg_band_tasks.jsonl`.

The suite is the P2 follow-up — the A/B of traceback feedback against debug
feedback needs tasks this tier retries two or three times, because every suite
tried before it was a ceiling (8/8 both arms), a floor, or a band one task wide.
So the claims worth checking are about the SHAPE of the instrument, and none of
them is taken from the generator that produced it: the band set is recomputed
from `ledger.jsonl`, each seeded file is diffed line by line against its
solution, and each bug is re-loaded on its own by running the oracle.

    python benchmarks/dbg_band_check.py

Prints `N/N checks passed`, then the mutation half: five tampered suites that
must each be caught by the check that covers the property they break. The band
comparison honours the suite's frozen ledger cut (see gen_dbg_band.py) and
prints how many band tasks have arrived since, so growth is visible instead of
being a reason to fail — or a thing to quietly ignore.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash import debug, harness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BAND = ROOT / "benchmarks/tasks/dbg_band_tasks.jsonl"
LEDGER = ROOT / "benchmarks/results/ledger.jsonl"
GEN = ROOT / "benchmarks/gen_dbg_band.py"
MIN_TOTAL, MIN_GENERATED = 30, 12


def band_ids(cut: float | None = None) -> dict[str, dict]:
    """The measured band, recomputed here rather than trusted from the generator.

    `cut` is the suite's own frozen window: the newest `band_ts` its ledger rows
    carry. Comparing a frozen instrument against a ledger that has grown since
    would fail on growth alone, so the window is honoured — and what arrived
    AFTER it is printed, never hidden.
    """
    out: dict[str, dict] = {}
    for line in LEDGER.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if cut is not None and r["ts"] > cut:
            continue
        if (r.get("tier") == "small" and r.get("solved") and not r.get("shed")
                and int(r.get("attempts", 0)) >= 2):
            if r["task_id"] not in out or r["ts"] > out[r["task_id"]]["ts"]:
                out[r["task_id"]] = r
    return out


def suite_cut(rows: list[dict]) -> float:
    led = [r["band_ts"] for r in rows if r.get("band_source") == "ledger"]
    return max(led) if led else 0.0


def diff_lines(sol: str, seeded: str) -> list[tuple[str, str]]:
    """(solution line, seeded line) for every line that differs; [] if lengths differ."""
    a, b = sol.splitlines(), seeded.splitlines()
    if len(a) != len(b):
        return []
    return [(x, y) for x, y in zip(a, b) if x != y]


def revert(row: dict, which: int) -> str:
    """The seeded file with exactly one of its two bugs put back — the load-bearing test."""
    lines = row["seeded"].splitlines()
    for i, (sol_line, _) in enumerate(diff_lines(row["solution"], row["seeded"])):
        if i == which:
            lines[i] = sol_line
    return "\n".join(lines) + "\n"


def structural(rows: list[dict], band: dict[str, dict],
               today: dict[str, dict] | None = None) -> list:
    """Every shape claim, as (name, holds, note)."""
    out = [(f"suite has >= {MIN_TOTAL} tasks", len(rows) >= MIN_TOTAL,
            f"{len(rows)} rows")]
    led = [r for r in rows if r.get("band_source") == "ledger"]
    gen = [r for r in rows if r.get("band_source") == "generated"]
    out.append((f">= {MIN_GENERATED} generated two-bug repairs", len(gen) >= MIN_GENERATED,
                f"{len(gen)} rows"))
    out.append(("no duplicate ids", len({r["id"] for r in rows}) == len(rows), ""))
    out.append(("the measured half is exactly the band at its cut",
                {r["id"] for r in led} == set(band),
                f"suite {len(led)}, ledger at that ts says {len(band)}"))
    if today is not None:
        out.append(("every measured row is still in the band today",
                    all(r["id"] in today for r in led),
                    ", ".join(r["id"] for r in led if r["id"] not in today)))
    for r in sorted(led, key=lambda r: r["id"]):
        rec = band.get(r["id"])
        out.append((f"{r['id']}: in the band at its recorded attempts",
                    rec is not None and int(r["band_attempts"]) == int(rec["attempts"]) >= 2,
                    f"suite says {r.get('band_attempts')}, ledger says "
                    f"{rec['attempts'] if rec else 'nothing'}"))
    for r in sorted(gen, key=lambda r: r["id"]):
        d = diff_lines(r["solution"], r["seeded"])
        out.append((f"{r['id']}: two bugs, each on its own line", len(d) == 2,
                    f"{len(d)} lines differ"))
        out.append((f"{r['id']}: blame names both seeded lines",
                    # order-free: the two lines are a set, and blame is stored in
                    # the order the bugs were seeded, which need not be file order
                    sorted(y.strip() for _, y in d) == sorted(r["blame"]),
                    f"{r['blame']}"))
        out.append((f"{r['id']}: the tests are withheld from the prompt",
                    r["test"].strip() not in r["prompt"]
                    and "not shown" in r["prompt"], ""))
        out.append((f"{r['id']}: asked for the whole corrected file",
                    r.get("repair") is True and "fenced python block" in r["prompt"],
                    ""))
        for which in (0, 1):
            code = revert(r, which)
            ok, _ = harness.diagnose(code, r["test"])
            out.append((f"{r['id']}: bug {which + 1} is load-bearing alone",
                        not ok, "the suite's second bug would be decoration"))
    return out


def idempotent(rows: list[dict]) -> tuple[bool, str]:
    """Re-running the generator must reproduce the file byte for byte.

    The generator indexes the very directory it writes, and the ledger grows
    with every run, so this is the check that the frozen cut is real: the note
    says how many band tasks have arrived since it, and a generator that ignored
    its own cut would rewrite the suite and fail here.
    """
    before = BAND.read_bytes()
    subprocess.run([sys.executable, str(GEN)], cwd=ROOT, capture_output=True)
    after = BAND.read_bytes()
    cut = suite_cut(rows)
    grown = len(set(band_ids()) - set(band_ids(cut)))
    note = (f"the ledger has gained {grown} band task(s) since ts {cut:.0f}; "
            "the cut held")
    if after != before:
        BAND.write_bytes(before)
        return False, (f"rewrote {len(after)} bytes against {len(before)}; " + note)
    return True, note


def tamper_suite(rows: list[dict], band: dict[str, dict],
                 today: dict[str, dict]) -> list[tuple[str, list, str]]:
    """Five suites with exactly one property broken, and the check that must catch it."""
    out = []
    short = rows[:MIN_TOTAL - 1]
    out.append(("a suite one task short of the band", structural(short, band),
                f">= {MIN_TOTAL} tasks"))
    led = [r for r in rows if r.get("band_source") == "ledger"]
    drifted = [dict(r, band_attempts=1) if r is led[0] else r for r in rows]
    out.append(("a row whose recorded attempts no longer match the ledger",
                structural(drifted, band), f"{led[0]['id']}: in the band"))
    gen = [r for r in rows if r.get("band_source") == "generated"][0]
    one_bug = [dict(r, seeded=revert(r, 1)) if r is gen else r for r in rows]
    out.append(("a generated task with only one of its two bugs seeded",
                structural(one_bug, band), f"{gen['id']}: two bugs"))
    shown = [dict(r, prompt=r["prompt"] + "\n" + r["test"]) if r is gen else r
             for r in rows]
    out.append(("a repair prompt that shows the tests",
                structural(shown, band), f"{gen['id']}: the tests are withheld"))
    ghost = [dict(r, id="gone_forever") if r is led[0] else r for r in rows]
    out.append(("a measured row whose task the ledger no longer lists",
                structural(ghost, band, today), "still in the band"))
    return out


def main() -> int:
    rows = [json.loads(l) for l in BAND.read_text().splitlines() if l.strip()]
    cut, today = suite_cut(rows), band_ids()
    band = band_ids(cut)
    checks = structural(rows, band, today)
    ok, note = idempotent(rows)
    checks.append(("regenerating the suite is byte-identical", ok, note))
    for name, cond, err in debug.run_suite_check(BAND):
        checks.append((f"premise: {name}", cond, err))

    width = max(len(n) for n, _, _ in checks)
    fails = [n for n, c, _ in checks if not c]
    for name, cond, note in checks:
        print(f"  {'ok  ' if cond else 'FAIL'} {name:<{width}}"
              + (f"  [{note}]" if note else ""))
    print(f"\ndbg band: {len(checks) - len(fails)}/{len(checks)} checks passed")
    if fails:
        return 1

    defeated = 0
    for label, tampered, must_fail in tamper_suite(rows, band, today):
        caught = [n for n, c, _ in tampered if not c]
        hit = any(must_fail.split(":")[0] in n for n in caught)
        defeated += hit
        print(f"  {'ok  ' if hit else 'MISS'} MUTATION: {label}"
              f" -> {len(caught)} check(s) fail"
              + ("" if hit else f", none of them {must_fail!r}"))
    print(f"\nmutations: {defeated}/5 gates defeated by exactly their checks")
    return 0 if defeated == 5 else 1


if __name__ == "__main__":
    sys.exit(main())
