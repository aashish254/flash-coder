"""Build benchmarks/tasks/subtle_tasks.jsonl (SPEC R-2.3 / PLAN §34.2 vector suite).

The trust gap is asymmetric: one subtle logic bug that sails through the visible
tests costs more credibility than ten fast correct answers buy. So this suite is
built to be *subtle on purpose*: every task ships a `seeded` answer — the bug a
model typically writes — that PASSES the visible test and FAILS a hidden one.
The hidden test is the signal's answer key and is never shown to the model.

Seven of the eight answers are seeded from the classes §34.2's four evidence
streams can actually see, and each is built so that ONLY its own stream fires:

  coverage  the wrong code sits in a branch no visible test reaches
            (2-argument signatures, so the edge probe's one-arg calls are all
            wrong-shape and stay silent; no sets, so the seed reruns are stable)
  seeds     the visible assert bakes in one hash order; re-running it under
            PYTHONHASHSEED 0/1/7 moves the verdict
  edges     a missing guard that the visible asserts never touch and that
            crashes on an adversarial argument
  static    has NO seeded class, by construction: an answer that does not parse
            never passes the visible oracle, so there is nothing subtle to catch
            — PERCEIVE kills it one attempt earlier.

The last two (`mask01`, `mask02`) are the documented blind spot: a wrong VALUE
in a field the visible asserts do not look at. Nothing reaches it — every line
executes, no order matters, no argument crashes, the signature hides it from the
probe. `python benchmarks/subtle_premise_check.py` is expected to report those
two as NOT offered, and the limitation ships with the recall number rather than
being reworded away.
"""
import json
from pathlib import Path

TASKS = [
    # ---------------------------------------------------------------- coverage
    dict(
        id="cov01_invoice_tax",
        defect="coverage",
        prompt="Write render_invoice(items, tax_rate) -> str. items is a list of "
               "(name, price) pairs. With no items return exactly 'empty'. "
               "Otherwise return an 'INVOICE' header line, one 'name: price' line "
               "per item with the price to two decimals, and a final 'TOTAL: t' "
               "line where t is the subtotal grown by tax_rate, rounded to two "
               "decimals. Lines are joined with newline. Return only the code.",
        test="assert render_invoice([], 0.2) == 'empty'\n",
        hidden="assert render_invoice([('a', 10.0)], 0.2) == 'INVOICE\\na: 10.00\\nTOTAL: 12.00'\n",
        solution=(
            "def render_invoice(items, tax_rate):\n"
            "    if not items:\n"
            "        return 'empty'\n"
            "    lines = ['INVOICE']\n"
            "    subtotal = 0\n"
            "    for name, price in items:\n"
            "        line = f'{name}: {price:.2f}'\n"
            "        subtotal += price\n"
            "        lines.append(line)\n"
            "    total = round(subtotal * (1 + tax_rate), 2)\n"
            "    lines.append(f'TOTAL: {total:.2f}')\n"
            "    return '\\n'.join(lines)\n"),
        seeded=(
            "def render_invoice(items, tax_rate):\n"
            "    if not items:\n"
            "        return 'empty'\n"
            "    lines = ['INVOICE']\n"
            "    subtotal = 0\n"
            "    for name, price in items:\n"
            "        line = f'{name}: {price:.2f}'\n"
            "        subtotal += price\n"
            "        lines.append(line)\n"
            "    total = round(subtotal * tax_rate, 2)\n"      # forgot the 1 +
            "    lines.append(f'TOTAL: {total:.2f}')\n"
            "    return '\\n'.join(lines)\n")),
    dict(
        id="cov02_report_sum",
        defect="coverage",
        prompt="Write format_report(rows, footer) -> str. rows is a list of "
               "(name, score) pairs. Return one 'name=score' line per row, then "
               "the footer as its own line, and — only when there is more than "
               "one row — a final line 'N rows, sum S, best B' where S is the "
               "total of all scores and B the largest one. Lines joined with "
               "newline. Return only the code.",
        test="assert format_report([('a', 1)], 'END') == 'a=1\\nEND'\n",
        hidden="assert format_report([('a', 1), ('b', 5), ('c', 3)], 'END') == "
               "'a=1\\nb=5\\nc=3\\nEND\\n3 rows, sum 9, best 5'\n",
        solution=(
            "def format_report(rows, footer):\n"
            "    lines = []\n"
            "    for name, score in rows:\n"
            "        lines.append(f'{name}={score}')\n"
            "    lines.append(str(footer))\n"
            "    if len(rows) > 1:\n"
            "        total = 0\n"
            "        best = rows[0][1]\n"
            "        for name, score in rows:\n"
            "            total += score\n"
            "            if score > best:\n"
            "                best = score\n"
            "        lines.append(f'{len(rows)} rows, sum {total}, best {best}')\n"
            "    return '\\n'.join(lines)\n"),
        seeded=(
            "def format_report(rows, footer):\n"
            "    lines = []\n"
            "    for name, score in rows:\n"
            "        lines.append(f'{name}={score}')\n"
            "    lines.append(str(footer))\n"
            "    if len(rows) > 1:\n"
            "        total = 0\n"
            "        best = rows[0][1]\n"
            "        for name, score in rows:\n"
            "            total = score\n"                                # not +=
            "            if score > best:\n"
            "                best = score\n"
            "        lines.append(f'{len(rows)} rows, sum {total}, best {best}')\n"
            "    return '\\n'.join(lines)\n")),

    # -------------------------------------------------------------------- seeds
    dict(
        id="seed01_tag_order",
        defect="seeds",
        prompt="Write pick_tag(tags). The tag pool is the caller's tags merged "
               "with the five defaults alpha, bravo, charlie, delta, echo. "
               "Return the pool member that comes first alphabetically, except "
               "that a caller who passed exactly one tag gets that tag back "
               "whatever it is. Return only the code.",
        test="assert pick_tag([]) == 'alpha'\n",
        hidden="assert pick_tag(['uniform']) == 'uniform'\n",
        solution=(
            "def pick_tag(tags):\n"
            "    defaults = ['alpha', 'bravo', 'charlie', 'delta', 'echo']\n"
            "    if len(tags) == 1:\n"
            "        return tags[0]\n"
            "    return min(set(tags) | set(defaults))\n"),
        seeded=(
            "def pick_tag(tags):\n"
            "    s = set(tags)\n"
            "    s.update(['alpha', 'bravo', 'charlie', 'delta', 'echo'])\n"
            "    return next(iter(s))\n")),
    dict(
        id="seed02_shared_name",
        defect="seeds",
        prompt="Write pick_shared(names, blocked). Return the alphabetically "
               "first name in names that is not in blocked. When nothing is "
               "left, raise LookupError('all blocked'). Return only the code.",
        test="assert pick_shared(['alpha', 'bravo', 'charlie', 'delta'], ['bravo']) == 'alpha'\n",
        hidden="assert pick_shared(['charlie', 'hotel'], []) == 'charlie'\n"
               "assert pick_shared(['golf', 'kilo'], ['kilo']) == 'golf'\n",
        solution=(
            "def pick_shared(names, blocked):\n"
            "    for n in sorted(names):\n"
            "        if n not in blocked:\n"
            "            return n\n"
            "    raise LookupError('all blocked')\n"),
        seeded=(
            "def pick_shared(names, blocked):\n"
            "    free = set(names) - set(blocked)\n"
            "    if not free:\n"
            "        raise LookupError('all blocked')\n"
            "    return next(iter(free))\n")),

    # -------------------------------------------------------------------- edges
    dict(
        id="edge01_mean_guard",
        defect="edges",
        prompt="Write average(numbers). Return the arithmetic mean of a list of "
               "numbers; an empty list averages to 0.0. Return only the code.",
        test="assert average([2, 4]) == 3\nassert average([1]) == 1\n",
        hidden="assert average([]) == 0.0\n",
        solution=(
            "def average(numbers):\n"
            "    if not numbers:\n"
            "        return 0.0\n"
            "    return sum(numbers) / len(numbers)\n"),
        seeded=(
            "def average(numbers):\n"
            "    return sum(numbers) / len(numbers)\n")),
    dict(
        id="edge02_last_pair",
        defect="edges",
        prompt="Write last_pair(values). Return the final two values of the "
               "sequence as a tuple. When there are fewer than two values, "
               "return (None, None). Return only the code.",
        test="assert last_pair([1, 2, 3]) == (2, 3)\nassert last_pair([7, 8]) == (7, 8)\n",
        hidden="assert last_pair([]) == (None, None)\nassert last_pair([5]) == (None, None)\n",
        solution=(
            "def last_pair(values):\n"
            "    if len(values) < 2:\n"
            "        return (None, None)\n"
            "    return (values[-2], values[-1])\n"),
        seeded=(
            "def last_pair(values):\n"
            "    return (values[-2], values[-1])\n")),

    # ------------------------------------------------- the documented blind spot
    dict(
        id="mask01_bucket_sign",
        defect="masked-value",
        prompt="Write bucket(value, limits). limits is an ascending list of "
               "thresholds. Return the pair (count, overshoot) where count is "
               "how many limits the value exceeds and overshoot is how far past "
               "the LAST limit it exceeds the value is (0 when it exceeds none). "
               "Return only the code.",
        test="assert bucket(50, [1, 10])[0] == 2\nassert bucket(0, [1, 10])[0] == 0\n",
        hidden="assert bucket(50, [1, 10]) == (2, 40)\nassert bucket(5, [1, 10]) == (1, 4)\n",
        solution=(
            "def bucket(value, limits):\n"
            "    count = 0\n"
            "    far = 0\n"
            "    for lim in limits:\n"
            "        if value > lim:\n"
            "            count += 1\n"
            "            far = value - lim\n"
            "    return count, far\n"),
        seeded=(
            "def bucket(value, limits):\n"
            "    count = 0\n"
            "    far = 0\n"
            "    for lim in limits:\n"
            "        if value > lim:\n"
            "            count += 1\n"
            "            far = lim - value\n"        # the sign, on a line that runs
            "    return count, far\n")),
    dict(
        id="mask02_leftovers",
        defect="masked-value",
        prompt="Write split_rows(rows, per_page). Return the pair (pages, "
               "leftovers) where pages is rows chopped into consecutive lists "
               "of per_page items with the final short chunk included, and "
               "leftovers is how many rows landed on that final chunk when it "
               "is short, 0 when rows divides evenly. Return only the code.",
        test="assert split_rows([1, 2, 3], 2)[0] == [[1, 2], [3]]\n",
        hidden="assert split_rows([1, 2, 3, 4, 5, 6, 7], 4) == ([[1, 2, 3, 4], [5, 6, 7]], 3)\n"
               "assert split_rows([1, 2, 3, 4], 2) == ([[1, 2], [3, 4]], 0)\n",
        solution=(
            "def split_rows(rows, per_page):\n"
            "    pages = [list(rows[i:i + per_page]) for i in range(0, len(rows), per_page)]\n"
            "    leftovers = len(rows) % per_page\n"
            "    return pages, leftovers\n"),
        seeded=(
            "def split_rows(rows, per_page):\n"
            "    pages = [list(rows[i:i + per_page]) for i in range(0, len(rows), per_page)]\n"
            "    r = len(rows) % per_page\n"
            "    leftovers = per_page - r if r else 0\n"   # the complement, unseen
            "    return pages, leftovers\n")),
]

OUT = Path(__file__).resolve().parent / "tasks" / "subtle_tasks.jsonl"


def main() -> int:
    rows = [{k: t[k] for k in ("id", "prompt", "test", "hidden", "solution",
                              "seeded", "defect")} for t in TASKS]
    OUT.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(f"wrote {len(rows)} task(s) -> {OUT.relative_to(Path(__file__).resolve().parent.parent)}")
    for r in rows:
        print(f"  {r['id']:<22} {r['defect']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
