"""Build benchmarks/tasks/dbg_band_tasks.jsonl — the R-4.3 A/B instrument with a
band wide enough to measure (the gate R-4.3 leaves open; TODO's P2-follow-up).

Why this exists: every suite the debugger arm was run against was degenerate.
`dbg_tasks` 8/8 vs 8/8 and `dbg_blind_tasks` 8/8 vs 8/8 are ceilings — the tier
solves them on attempt 0, so no retry ever happens and neither arm's feedback is
ever sent; `dbg_hard_tasks` 6/21 vs 5/21 is a floor with one task of daylight,
and 11 of its 12 shared solves have identical attempt counts. A "B beats A by
>= 2" gate cannot be observed on any of them at any effect size. The requirement
is a set where this tier needs TWO OR THREE tries: tasks already recorded in
`benchmarks/results/ledger.jsonl` as solved on the small tier, never escalated,
attempts >= 2. That is the band, and it is measured, not guessed.

Two parts, so the set is both anchored and growable:
  * `band_source: "ledger"` — every task the ledger itself says took >= 2 small
    tier tries. These are the difficulty shape, selected by evidence.
  * `band_source: "generated"` — blind repairs in that shape: the tests are NOT
    shown, and TWO independent bugs are seeded instead of one, which is what
    makes a single retry insufficient (fix the line the assert blames and the
    second bug still fails a later assert).

Nothing here is trusted on sight. Each generated task is proved by running the
code: both singles fail the oracle (so neither bug is decorative), the doubly
broken module fails, and each of its two causing lines is ABSENT from the
failure text the traceback feedback arm would read and PRESENT in the debug
digest — that pair is the suite's whole premise, and a mutant that violates it
is discarded rather than shipped. Re-run with `--report` to see the discard
counts and the surplus of qualifying pairs that were not emitted.

    python benchmarks/gen_dbg_band.py            # write the suite
    python benchmarks/gen_dbg_band.py --report   # and the enumeration's bookkeeping
    python benchmarks/gen_dbg_band.py --recut    # re-select against today's ledger

The ledger half is a FROZEN CUT: with the file already on disk the generator
re-selects only the band rows at or before the newest `band_ts` the file
carries, so it stays byte-identical as the ledger grows and the 30 tasks an arm
cited stay the 30 tasks on disk. `--recut` takes today's whole ledger instead —
a different instrument, which needs its own A/B.

The generator is deterministic: no RNG, no clock, no model. The ledger rows are
carried with the timestamp of the run that put them in the band, so a re-cut
against a grown ledger is traceable to a window.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash import debug, harness  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "benchmarks/tasks/dbg_band_tasks.jsonl"
LEDGER = ROOT / "benchmarks/results/ledger.jsonl"
TASKS_DIR = ROOT / "benchmarks/tasks"
MIN_GENERATED = 12

# The measured band, stated as code so the selection cannot drift from the claim.
BAND = dict(tier="small", min_attempts=2, solved=True, escalated=False)


# --------------------------------------------------------------- bug library
# (label, pattern to find in a correct solution, buggy replacement). Every entry
# is a bug a model actually writes: an order dropped, a bound moved, floor
# division where true division was asked for, an accumulator overwritten rather
# than added to, a nested result flattened. Patterns are applied to ONE
# occurrence and must change exactly one line, so a mutant's blame is a line the
# model can see and repair.
MUTATIONS = [
    ("order-lost", "sorted(", "list("),
    ("ascending", ", reverse=True", ""),
    ("bound-minus-1", " - 1)", " - 2)"),
    ("bound-size-minus", " - size + 1)", " - size)"),
    ("bound-plus-1", "range(0, ", "range(1, "),
    ("floor-div", " / ", " // "),
    ("get-default-1", ".get(v, 0)", ".get(v, 1)"),
    ("assign-not-add", "acc += v", "acc = v"),
    ("record-input", "out.append(acc)", "out.append(v)"),
    ("drop-clamp", "min(max(v, lo), hi)", "max(v, lo)"),
    ("swap-args", "min(max(v, lo), hi)", "min(max(v, hi), lo)"),
    ("insert-head", "out.append(v)", "out.insert(0, v)"),
    ("reverse-iter", "for v in items:", "for v in reversed(items):"),
    ("slice-tail", "[i:i + size]", "[i:i + size - 1]"),
    ("slice-from-k", "[:k]", "[k:]"),
    ("flat-not-nested", "out.append(items[i:i + n])", "out.extend(items[i:i + n])"),
    ("max-not-sum", "sum(values[i", "max(values[i"),
    ("reversed-lines", "for r in rows:", "for r in rows[::-1]:"),
    ("skip-first", "for v in r:", "for v in r[1:]:"),
    ("join-dash", "' '.join(out)", "'-'.join(out)"),
    ("upper", "w.capitalize()", "w.upper()"),
    ("pair-self", "range(i + 1, len(values))", "range(i, len(values))"),
    ("pair-le", "== target", "<= target"),
    ("pair-swapped", "return (i, j)", "return (j, i)"),
    ("bisect-le", "out[i] < x", "out[i] <= x"),
    ("step-2", "i += 1", "i += 2"),
    ("append-not-insert", "out.insert(i, x)", "out.append(x)"),
    ("no-strip", "out[k.strip()] = v.strip()", "out[k] = v"),
    ("count-not-sum", "total += v", "total += 1"),
    ("start-at-zero", "best = values[0]", "best = 0"),
    ("reset-acc", "counts[v] = counts.get(v, 0) + 1", "counts[v] = 1"),
]
BY_LABEL = {label: (pat, rep) for label, pat, rep in MUTATIONS}

# ------------------------------------------------------- correct references
# One brief + one test + one solution per shape. The tests are written to catch
# BOTH ends of each mutation pair: an order assertion and a value assertion, a
# clamp bound and a nesting depth.
BASES = [
    dict(key="topk",
         brief="top_k(scores, k) returns the k highest scores, highest first.",
         solution="def top_k(scores, k):\n    ordered = sorted(scores, reverse=True)\n"
                  "    return ordered[:k]\n",
         test="assert top_k([5, 3, 9, 1, 7], 3) == [9, 7, 5]\n"
              "assert top_k([2, 8], 1) == [8]\n"),
    dict(key="window",
         brief="window_sum(values, size) returns the sum of every consecutive "
               "window of `size` items.",
         solution="def window_sum(values, size):\n    out = []\n"
                  "    for i in range(len(values) - size + 1):\n"
                  "        out.append(sum(values[i:i + size]))\n    return out\n",
         test="assert window_sum([1, 2, 3, 4], 2) == [3, 5, 7]\n"
              "assert window_sum([4, 4], 2) == [8]\n"),
    dict(key="mean",
         brief="mean(values) returns the arithmetic mean, as a float.",
         solution="def mean(values):\n    total = 0\n    for v in values:\n"
                  "        total += v\n    return total / len(values)\n",
         test="assert mean([1, 2, 3, 4]) == 2.5\nassert mean([7, 8]) == 7.5\n"),
    dict(key="dedupe",
         brief="dedupe(items) removes duplicates keeping first-seen order.",
         solution="def dedupe(items):\n    seen = set()\n    out = []\n"
                  "    for v in items:\n        if v not in seen:\n"
                  "            seen.add(v)\n            out.append(v)\n"
                  "    return out\n",
         test="assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]\n"
              "assert dedupe([1, 1]) == [1]\n"),
    dict(key="running-max",
         brief="running_max(values) returns, at each position, the largest value "
               "seen so far.",
         solution="def running_max(values):\n    best = values[0]\n    out = []\n"
                  "    for v in values:\n        if v > best:\n            best = v\n"
                  "        out.append(best)\n    return out\n",
         test="assert running_max([1, 5, 2, 9, 3]) == [1, 5, 5, 9, 9]\n"
              "assert running_max([-2, -5]) == [-2, -2]\n"),
    dict(key="chunk",
         brief="chunk(items, n) splits a list into consecutive lists of n items.",
         solution="def chunk(items, n):\n    out = []\n"
                  "    for i in range(0, len(items), n):\n"
                  "        out.append(items[i:i + n])\n    return out\n",
         test="assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]\n"
              "assert chunk([], 3) == []\n"),
    dict(key="flatten",
         brief="flatten(rows) returns every value of a list of lists, in order.",
         solution="def flatten(rows):\n    out = []\n    for r in rows:\n"
                  "        for v in r:\n            out.append(v)\n    return out\n",
         test="assert flatten([[1, 2], [3]]) == [1, 2, 3]\n"
              "assert flatten([[], [4]]) == [4]\n"),
    dict(key="histogram",
         brief="histogram(items) returns {value: how many times it appears}.",
         solution="def histogram(items):\n    counts = {}\n    for v in items:\n"
                  "        counts[v] = counts.get(v, 0) + 1\n    return counts\n",
         test="assert histogram(['a', 'b', 'a']) == {'a': 2, 'b': 1}\n"
              "assert histogram([]) == {}\n"),
    dict(key="first-pair",
         brief="first_pair(values, target) returns the indices of the first two "
               "distinct positions that sum to target, or None.",
         solution="def first_pair(values, target):\n    for i in range(len(values)):\n"
                  "        for j in range(i + 1, len(values)):\n"
                  "            if values[i] + values[j] == target:\n"
                  "                return (i, j)\n    return None\n",
         test="assert first_pair([1, 7, 3, 5], 8) == (0, 1)\n"
              "assert first_pair([1, 2], 9) is None\n"),
    dict(key="insert-sorted",
         brief="insert_sorted(items, x) returns a new sorted list with x placed "
               "before any value equal to it.",
         solution="def insert_sorted(items, x):\n    out = list(items)\n    i = 0\n"
                  "    while i < len(out) and out[i] < x:\n        i += 1\n"
                  "    out.insert(i, x)\n    return out\n",
         test="assert insert_sorted([1, 3, 5], 4) == [1, 3, 4, 5]\n"
              "assert insert_sorted([2, 2], 2) == [2, 2, 2]\n"),
    dict(key="clamp",
         brief="normalize(values, lo, hi) clamps every value into [lo, hi].",
         solution="def normalize(values, lo, hi):\n    out = []\n    for v in values:\n"
                  "        out.append(min(max(v, lo), hi))\n    return out\n",
         test="assert normalize([-5, 3, 99], 0, 10) == [0, 3, 10]\n"
              "assert normalize([7], 0, 10) == [7]\n"),
    dict(key="cumulative",
         brief="cumulative(values) returns the running totals.",
         solution="def cumulative(values):\n    out = []\n    acc = 0\n"
                  "    for v in values:\n        acc += v\n        out.append(acc)\n"
                  "    return out\n",
         test="assert cumulative([1, 2, 3]) == [1, 3, 6]\n"
              "assert cumulative([4]) == [4]\n"),
    dict(key="flags",
         brief="parse_flags(text) reads 'key = value' lines into a dict with the "
               "names and values stripped of spaces.",
         solution="def parse_flags(text):\n    out = {}\n"
                  "    for line in text.splitlines():\n        if '=' in line:\n"
                  "            k, v = line.split('=', 1)\n"
                  "            out[k.strip()] = v.strip()\n    return out\n",
         test="assert parse_flags(' a = 1\\nb=2') == {'a': '1', 'b': '2'}\n"
              "assert parse_flags('skip me') == {}\n"),
    dict(key="title",
         brief="title_words(text) capitalizes each word and joins them with "
               "single spaces.",
         solution="def title_words(text):\n    out = []\n    for w in text.split():\n"
                  "        out.append(w.capitalize())\n    return ' '.join(out)\n",
         test="assert title_words('hello  world') == 'Hello World'\n"
              "assert title_words('x') == 'X'\n"),
]


def changed_line(sol: str, seeded: str) -> str | None:
    """The one line a mutant differs by, in the mutant's own text (that is the
    line the digest has to blame), or None when the edit is ambiguous."""
    a, b = sol.splitlines(), seeded.splitlines()
    if len(a) != len(b):
        return None
    diff = [y for x, y in zip(a, b) if x != y]
    return diff[0].strip() if len(diff) == 1 else None


def proves(code: str, test: str, blames: list[str]) -> str | None:
    """Why this mutant is (or is not) part of the suite. None means it qualifies.

    Runs the code for real: it must fail, every causing line must be invisible in
    the failure text the traceback arm reads, and every one must be named by the
    debug digest.
    """
    ok, tb = harness.diagnose(code, test)
    if ok:
        return "the oracle cannot see it: not a bug"
    if "ERROR:" in tb:
        # A mutant that RAISES hands the traceback arm its own answer: the
        # exception names the line. The band needs programs that are silently
        # wrong, which is the only shape where execution evidence can help.
        return "it raises instead of returning a wrong value"
    for blame in blames:
        if blame in tb:
            return f"{blame!r} is already in the traceback: not misleading"
    fb = debug.watch(code, test).as_feedback()
    for blame in blames:
        if blame not in fb:
            return f"{blame!r} never reaches the digest"
    return None


def singles(base: dict) -> list[tuple[str, str, str]]:
    """(label, code, blame) for every single mutation that is a real misleading bug."""
    out = []
    for label, pat, rep in MUTATIONS:
        if pat not in base["solution"]:
            continue
        seeded = base["solution"].replace(pat, rep, 1)
        blame = changed_line(base["solution"], seeded)
        if blame is None or proves(seeded, base["test"], [blame]) is not None:
            continue
        out.append((label, seeded, blame))
    return out


def pairs(base: dict, s: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """Two-bug blind repairs from one base: every qualifying pair of singles."""
    s = singles(base) if s is None else s
    out = []
    for i in range(len(s)):
        for j in range(i + 1, len(s)):
            la, _, ba = s[i]
            lb, _, bb = s[j]
            if ba == bb:
                continue
            code = base["solution"]
            for label in (la, lb):
                pat, rep = BY_LABEL[label]
                code = code.replace(pat, rep, 1)
            # A pattern the first edit already destroyed would leave a one-bug
            # task labelled as two, so both causing lines must be in the file.
            if not (ba in code and bb in code):
                continue
            if proves(code, base["test"], [ba, bb]) is not None:
                continue
            out.append(dict(key=base["key"], code=code, blame=[ba, bb],
                            bugs=[la, lb], brief=base["brief"], test=base["test"],
                            solution=base["solution"]))
    return out


def band_from_ledger(cut: float | None = None) -> tuple[list[dict], str]:
    """The measured half: every task the ledger says took >= 2 small-tier tries.

    `cut` freezes the window. The ledger grows with every run, so an uncut
    selection would silently re-number the instrument a measured arm cites — and
    the A/B on this suite names its 30 tasks. With the file already on disk the
    generator re-cuts at the newest `band_ts` it carries, which makes a rerun
    byte-identical however much the ledger has grown; `--recut` takes today's
    whole ledger instead, and that is a NEW instrument that needs its own A/B.
    """
    rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l.strip()]
    best: dict[str, dict] = {}
    for r in rows:
        if cut is not None and r["ts"] > cut:
            continue
        if r.get("tier") != BAND["tier"] or bool(r.get("solved")) != BAND["solved"]:
            continue
        # tier "small" is itself the never-escalated record, but a row that also
        # carries a shed verdict contradicts it, and contradicting rows are
        # dropped rather than kept because they are convenient.
        if r.get("shed"):
            continue
        if int(r.get("attempts", 0)) < BAND["min_attempts"]:
            continue
        if r["task_id"] not in best or r["ts"] > best[r["task_id"]]["ts"]:
            best[r["task_id"]] = r
    index = {}
    for f in sorted(TASKS_DIR.glob("*.jsonl")):
        if f == OUT:
            continue
        for l in f.read_text().splitlines():
            if l.strip():
                t = json.loads(l)
                index.setdefault(t["id"], t)
    out, missing = [], []
    for tid in sorted(best):
        if tid not in index:
            missing.append(tid)
            continue
        t = dict(index[tid])
        t["band_source"] = "ledger"
        t["band_attempts"] = int(best[tid]["attempts"])
        t["band_ts"] = best[tid]["ts"]
        out.append(t)
    note = ("ledger through ts "
            + f"{max(r['ts'] for r in best.values()):.0f}"
            + (" (frozen at the cut the file already carries)"
               if cut is not None else " (today's whole ledger)")) \
        if best else "empty ledger"
    if missing:
        print("in the band but no task row found for: " + ", ".join(missing))
    return out, note


def prompt_for(base_brief: str, code: str) -> str:
    return (base_brief + " The module is broken in TWO places, and the tests are "
            "not shown: repair the module so the behaviour the brief describes is "
            "what it does.\n\n```python\n" + code + "```\n\n"
            "Return the whole corrected file, one fenced python block, no prose.\n")


def main(argv: list[str]) -> int:
    report = "--report" in argv
    cut = None
    if OUT.exists() and "--recut" not in argv:
        prior = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()]
        seen = [r["band_ts"] for r in prior if r.get("band_source") == "ledger"]
        cut = max(seen) if seen else None
    ledger_rows, cut_note = band_from_ledger(cut)

    all_pairs: list[dict] = []
    stats = []
    for base in BASES:
        s = singles(base)
        ps = pairs(base, s)
        stats.append((base["key"], len(s), len(ps)))
        all_pairs.extend(ps)
    # Round-robin by base so the generated half spans as many distinct shapes as
    # it can: first the first pair of each base, then the second, and so on.
    by_key: dict[str, list[dict]] = {}
    for p in all_pairs:
        by_key.setdefault(p["key"], []).append(p)
    chosen: list[dict] = []
    rank = 0
    while len(chosen) < MIN_GENERATED and any(len(v) > rank for v in by_key.values()):
        for base in BASES:
            ps = by_key.get(base["key"], [])
            if rank < len(ps) and len(chosen) < MIN_GENERATED:
                chosen.append(ps[rank])
        rank += 1
    rows = list(ledger_rows)
    for n, p in enumerate(sorted(chosen, key=lambda p: p["key"]), 1):
        rows.append(dict(
            id=f"band{n:02d}_{p['key'].replace('-', '_')}",
            prompt=prompt_for(p["brief"], p["code"]),
            test=p["test"], solution=p["solution"], seeded=p["code"],
            blame=p["blame"], multi=False, repair=True, band_source="generated",
            band_bugs=p["bugs"]))
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    gen = len(rows) - len(ledger_rows)
    print(f"wrote {len(rows)} tasks to {OUT.name}: {len(ledger_rows)} from the "
          f"measured band ({cut_note}), {gen} generated two-bug blind repairs")
    if report:
        for key, ns, np_ in stats:
            print(f"  {key:14s} {ns} single-bug mutants qualify, {np_} two-bug "
                  f"pairs qualify")
        print(f"  emitted {gen} of {len(all_pairs)} qualifying pairs")
    if gen < MIN_GENERATED:
        print(f"SHORT: needed >= {MIN_GENERATED} generated tasks to reach a band "
              f"of 30; only {gen} pairs survived the premise")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
