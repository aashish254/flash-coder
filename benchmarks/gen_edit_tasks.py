"""Generate the R-3.2 acceptance suite: ten changes, each one symbol wide.

Every task here is built to the shape the requirement names — a real project,
a request that is answerable by replacing ONE symbol's body, and a test that
fails before the change and passes after it. `python -m flash.patches --suite
benchmarks/tasks/edit_tasks.jsonl` proves that premise for all ten before any
model is charged for it: if a task needs two symbols touched, or passes as
seeded, it is not an edit task and the suite says so here.

Three projects, deliberately different in shape:

  prose     two flat modules, one class + module functions
  shop      a class with a decorated property, a class-level constant and a
            module function whose arithmetic is wrong
  shift     a priority queue and a stopwatch, both importable by name

The targets cover the addressable surface on purpose: a bare module function,
a method, a constructor, a decorated property (the decorator is inside the
owned span, so dropping it is a refusal), a class-level constant, and a
statement-level change expressed as a method body.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

OUT = Path(__file__).resolve().parent / "tasks" / "edit_tasks.jsonl"

BOOT = 'import sys; sys.path.insert(0, "<TMPDIR>")\n'

PROSE_BOX = '''"""A fixed-width text box."""


class Box:
    def __init__(self, width, fill=" "):
        self.width = width
        self.fill = fill

    def rule(self):
        return self.fill * self.width

    def render(self, text):
        return text.ljust(self.width)
'''

PROSE_RULES = '''"""Text normalisation rules used by the box renderer."""
PUNCT = ".,;:!?()\\"'"


def strip_punct(text):
    return "".join(c for c in text if c not in PUNCT)


def title_case(text):
    return text.lower()
'''

SHOP_STOCK = '''"""Stock levels for one warehouse line."""


class Stock:
    LOW_TAIL = 5

    def __init__(self, sku, count=0, reserved=0):
        self.sku = sku
        self.count = count
        self.reserved = reserved

    def add(self, n):
        self.count += n

    @property
    def available(self):
        return self.count

    def needs_reorder(self):
        return self.available <= self.LOW_TAIL
'''

SHOP_INVOICE = '''"""Invoice totals in whole cents."""


def totals(price_cents, qty, tax_pct=10):
    sub = price_cents * qty
    tax = sub * tax_pct // 100
    return sub, tax, sub + tax
'''

SHIFT_QUEUE = '''"""A task queue: (priority, name) pairs, highest priority first out."""


class TaskQueue:
    def __init__(self):
        self._items = []

    def push(self, priority, name):
        self._items.append((priority, name))

    def pop(self):
        if not self._items:
            return None
        return self._items.pop(0)[1]

    def __len__(self):
        return len(self._items)
'''

SHIFT_CLOCK = '''"""A stopwatch over timestamps the caller supplies (seconds)."""


class Clock:
    def __init__(self):
        self.start = None
        self.stop = None

    def begin(self, at):
        self.start = at
        self.stop = None

    def finish(self, at):
        self.stop = at

    def elapsed_ms(self):
        if self.start is None or self.stop is None:
            return 0
        return (self.stop - self.start) * 1000
'''

PROSE = {"box.py": PROSE_BOX, "rules.py": PROSE_RULES}
SHOP = {"stock.py": SHOP_STOCK, "invoice.py": SHOP_INVOICE}
SHIFT = {"taskq.py": SHIFT_QUEUE, "clock.py": SHIFT_CLOCK}

TASKS = [
    {
        "id": "e01_title_case", "project": PROSE,
        "target": {"file": "rules.py", "symbol": "title_case"},
        "prompt": "Make `title_case` capitalise the first letter of every word "
                  "and lower the rest, so 'hello WORLD' becomes 'Hello World'. "
                  "A blank string still comes back blank.",
        "fix": {"rules.py": 'def title_case(text):\n'
                           '    return " ".join(w[:1].upper() + w[1:].lower()'
                           ' for w in text.split())'},
        "test": BOOT + "from rules import title_case\n"
                       'assert title_case("hello WORLD") == "Hello World"\n'
                       'assert title_case("aBc DeF") == "Abc Def"\n'
                       'assert title_case("") == ""\n',
    },
    {
        "id": "e02_render_truncate", "project": PROSE,
        "target": {"file": "box.py", "symbol": "Box.render"},
        "prompt": "`Box.render` must never return more than `width` characters: "
                  "text that is too long gets cut to width-1 characters plus an "
                  "ellipsis (\\u2026), short text still gets padded with the fill.",
        "fix": {"box.py": '    def render(self, text):\n'
                         '        if len(text) > self.width:\n'
                         '            return text[:self.width - 1] + "\\u2026"\n'
                         '        return text.ljust(self.width)'},
        "test": BOOT + "from box import Box\n"
                       "b = Box(5)\n"
                       'assert b.render("ab") == "ab   "\n'
                       'assert b.render("abcdefg") == "abcd\\u2026"\n'
                       'assert len(b.render("x" * 10)) == 5\n'
                       'assert len(b.render("")) == 5\n',
    },
    {
        "id": "e03_keep_apostrophes", "project": PROSE,
        "target": {"file": "rules.py", "symbol": "strip_punct"},
        "prompt": "Copy has to keep its contractions: `strip_punct` should drop "
                  "the punctuation it drops today but leave apostrophes alone.",
        "fix": {"rules.py": 'def strip_punct(text):\n'
                           '    return "".join(c for c in text if c not in PUNCT or c == "\'")'},
        "test": BOOT + "from rules import strip_punct\n"
                       'assert strip_punct("hi, there.") == "hi there"\n'
                       'assert strip_punct("don\'t") == "don\'t"\n'
                       'assert strip_punct("it\'s (fine)!") == "it\'s fine"\n',
    },
    {
        "id": "e04_box_min_width", "project": PROSE,
        "target": {"file": "box.py", "symbol": "Box.__init__"},
        "prompt": "A box is never invisible: a zero or negative width should be "
                  "clamped to 1 when the Box is built. Everything else about "
                  "construction stays as it is.",
        "fix": {"box.py": '    def __init__(self, width, fill=" "):\n'
                         '        self.width = max(1, width)\n'
                         '        self.fill = fill'},
        "test": BOOT + "from box import Box\n"
                       'assert Box(0).rule() == " "\n'
                       "assert Box(-3).width == 1\n"
                       'assert Box(4).rule() == "    "\n'
                       'assert Box(3, fill="-").rule() == "---"\n',
    },
    {
        "id": "e05_available_minus_reserved", "project": SHOP,
        "target": {"file": "stock.py", "symbol": "Stock.available"},
        "prompt": "Reserved units are not sellable: `Stock.available` must "
                  "report the count with what is reserved taken off. Keep it a "
                  "property — callers read it without parentheses.",
        "fix": {"stock.py": '    @property\n'
                           '    def available(self):\n'
                           '        return self.count - self.reserved'},
        "test": BOOT + "from stock import Stock\n"
                       'assert Stock("a", count=10, reserved=3).available == 7\n'
                       'assert Stock("b", count=4).available == 4\n'
                       'assert Stock("c", count=6, reserved=6).available == 0\n',
    },
    {
        "id": "e06_tax_rounds_half_up", "project": SHOP,
        "target": {"file": "invoice.py", "symbol": "totals"},
        "prompt": "`totals` must round the tax to the nearest whole cent "
                  "(half up) instead of always rounding it down. The subtotal "
                  "and the grand total keep their current meaning.",
        "fix": {"invoice.py": 'def totals(price_cents, qty, tax_pct=10):\n'
                              '    sub = price_cents * qty\n'
                              '    tax = (sub * tax_pct + 50) // 100\n'
                              '    return sub, tax, sub + tax'},
        "test": BOOT + "from invoice import totals\n"
                       "assert totals(199, 1) == (199, 20, 219)\n"
                       "assert totals(105, 1) == (105, 11, 116)\n"
                       "assert totals(50, 3, 8) == (150, 12, 162)\n"
                       "assert totals(7, 2, 5) == (14, 1, 15)\n",
    },
    {
        "id": "e07_reorder_threshold", "project": SHOP,
        "target": {"file": "stock.py", "symbol": "Stock.LOW_TAIL"},
        "prompt": "Restocking needs to be noticed earlier: a line is due for "
                  "reorder once 12 or fewer units are available. Change the one "
                  "number that decides it, not the method that reads it.",
        "fix": {"stock.py": "    LOW_TAIL = 12"},
        "test": BOOT + "from stock import Stock\n"
                       "assert Stock(\"a\", count=10).needs_reorder() is True\n"
                       "assert Stock(\"b\", count=12).needs_reorder() is True\n"
                       "assert Stock(\"c\", count=13).needs_reorder() is False\n",
    },
    {
        "id": "e08_add_refuses_negative", "project": SHOP,
        "target": {"file": "stock.py", "symbol": "Stock.add"},
        "prompt": "`add` is for receiving stock, not for writing it off: a "
                  "negative amount must raise ValueError and leave the count "
                  "exactly as it was.",
        "fix": {"stock.py": '    def add(self, n):\n'
                           '        if n < 0:\n'
                           '            raise ValueError("cannot receive a negative amount")\n'
                           '        self.count += n'},
        "test": BOOT + "from stock import Stock\n"
                       "s = Stock(\"a\", count=5)\n"
                       "s.add(2)\n"
                       "assert s.count == 7\n"
                       "try:\n"
                       "    s.add(-1)\n"
                       '    raise AssertionError("negative receipt was accepted")\n'
                       "except ValueError:\n"
                       "    pass\n"
                       "assert s.count == 7\n",
    },
    {
        "id": "e09_pop_by_priority", "project": SHIFT,
        "target": {"file": "taskq.py", "symbol": "TaskQueue.pop"},
        "prompt": "`TaskQueue.pop` should hand back the name of the highest "
                  "priority task waiting, and when two tasks share the top "
                  "priority the one pushed first wins. Empty queue still "
                  "returns None.",
        "fix": {"taskq.py": '    def pop(self):\n'
                            '        if not self._items:\n'
                            '            return None\n'
                            '        best = 0\n'
                            '        for i, it in enumerate(self._items):\n'
                            '            if it[0] > self._items[best][0]:\n'
                            '                best = i\n'
                            '        return self._items.pop(best)[1]'},
        "test": BOOT + "from taskq import TaskQueue\n"
                       "q = TaskQueue()\n"
                       'q.push(1, "low")\n'
                       'q.push(5, "high")\n'
                       'q.push(5, "later")\n'
                       'assert q.pop() == "high"\n'
                       'assert q.pop() == "later"\n'
                       'assert q.pop() == "low"\n'
                       "assert q.pop() is None\n"
                       "assert len(q) == 0\n",
    },
    {
        "id": "e10_clock_never_negative", "project": SHIFT,
        "target": {"file": "clock.py", "symbol": "Clock.elapsed_ms"},
        "prompt": "A stopwatch that was stopped before it was started has "
                  "measured nothing: `elapsed_ms` should clamp to 0 rather than "
                  "report a negative time. Unstarted clocks keep returning 0.",
        "fix": {"clock.py": '    def elapsed_ms(self):\n'
                            '        if self.start is None or self.stop is None:\n'
                            '            return 0\n'
                            '        return max(0, (self.stop - self.start) * 1000)'},
        "test": BOOT + "from clock import Clock\n"
                       "c = Clock()\n"
                       "c.begin(5)\n"
                       "c.finish(7)\n"
                       "assert c.elapsed_ms() == 2000\n"
                       "c.begin(9)\n"
                       "assert c.elapsed_ms() == 0\n"
                       "c.finish(3)\n"
                       "assert c.elapsed_ms() == 0\n"
                       "assert Clock().elapsed_ms() == 0\n",
    },
]


def build() -> list[dict]:
    """One task record per change request, plus its reference-solved project.

    The stored `prompt` is the whole input the model sees — project listing,
    request, test — so the patch arm and the whole-file control are shown
    identical material and differ only in the answer format they are asked
    for. That is what makes their A/B a protocol comparison.
    """
    from flash.patches import (Patch, apply_patches, changed_lines,
                               outside_lines, project_prompt)

    tasks, notes = [], []
    for spec in TASKS:
        workspace = dict(spec["project"])
        target = spec["target"]
        patch = Patch(file=target["file"], address=target["symbol"],
                      body=spec["fix"][target["file"]])
        res = apply_patches(workspace, [patch])
        assert res.ok, f"{spec['id']}: reference patch refused: {res.refusals}"
        assert outside_lines(workspace, res, target) == 0, spec["id"]
        changed = changed_lines(workspace[target["file"]],
                                res.files[target["file"]])
        notes.append(f"{spec['id']}: {len(changed)} line(s) changed inside "
                     f"{target['symbol']}")
        # One shared composer with the CLI's edit arm (R-7.15f): the stored
        # prompt and the prompt a typed `flash session` ask becomes are the same
        # text built by the same function, so R-3.2 clause 1's "same material,
        # different answer format" holds on the release shape and not only here.
        prompt = project_prompt(spec["prompt"], workspace, spec["test"])
        tasks.append({
            "id": spec["id"],
            "edit": True,
            "multi": True,
            "prompt": prompt,
            "files": workspace,
            "target": target,
            "fix": spec["fix"],
            "test": spec["test"],
            "solution": res.files,
        })
    return tasks, notes


def main() -> int:
    tasks, notes = build()
    lines = "\n".join(json.dumps(t) for t in tasks) + "\n"
    OUT.write_text(lines)
    for n in notes:
        print(f"  {n}")
    print(f"wrote {len(tasks)} edit tasks to {OUT.relative_to(OUT.parent.parent)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
