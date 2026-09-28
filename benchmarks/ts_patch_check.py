"""R-1.4's open gap, OFFLINE: can the patch arm address a TypeScript symbol?

The box closed in R-1.4 was perception's, and `flash/lang_ts.py` recorded on its
own face what that did NOT buy: the ACT leg still could not follow.
`flash/patches.py` validated every replacement with `ast.parse` and took its
spans from Python `definitions()`, so

    # edit: site/src/components/Hero.tsx :: Hero

came back `no symbol 'Hero' in site/src/components/Hero.tsx (it defines:
nothing)` — printed, on this repo's own front end, before this vector existed —
while `# edit: ... :: L16-L18` applied. A range address always worked, which is
exactly why the gap was survivable and also why it was worth closing: the symbol
form is the one the protocol tells the model to prefer.

Five questions, all of them about the seam rather than about a model:

* **Does the address reach the right grammar?** A `.ts`/`.tsx` name dispatches to
  `flash.lang_ts`; every other name, including the empty path `flash.graph`
  passes, stays on `ast` — so the published Python counts are the same numbers
  from the same code path.
* **Do the two arms read one span?** The graph's node and the patch's `Def` come
  out of the same `_declarations` walk, so "what breaks if this changes" and
  "what this patch replaces" cannot drift into two different line ranges.
* **What does a replacement have to keep?** The symbol's own name, and — the
  TypeScript shape of Python's decorator rule — the `export` keyword the span
  begins with. Dropping it leaves the edited file looking fine while every
  importer breaks.
* **Does the protocol speak the language?** A body fenced `tsx` is read as a
  patch, a listing labels each file with its own language, and a workspace
  gathered from a directory contains the `.tsx` files it names.
* **What does the missing optional extra do?** A machine without
  `tree-sitter-typescript` gets one refusal naming `pip install 'flash-coder[ts]'`
  — never a graph of guesses, never an `ast` error about valid TypeScript, and
  never an exception escaping the atomic patch set.

No model loads, no network is used, and no JavaScript test runner is pretended
at: this is the patch protocol's own contract. The whole-file arm and a TS
*verification* (a vitest run behind `diagnose_files`) are not here and are said
to be not-here in SPEC R-1.4.

    python benchmarks/ts_patch_check.py             # the checks
    python benchmarks/ts_patch_check.py --mutants   # put each bug back
    python benchmarks/ts_patch_check.py --sweep     # one fresh process each
    python benchmarks/ts_patch_check.py --mutant 6  # one bug, for a bisection
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import graph, lang_ts, patches                        # noqa: E402
from flash.patches import (Def, Patch, apply_patches, describe,  # noqa: E402
                           definitions, outside_lines, parse_patches, splice)

NUM_BUGS = 13

#: The fence pattern as this module shipped it before R-1.4's second language:
#: one mutant puts it back, because a model that mirrors the listing's `tsx` tag
#: would otherwise answer in a format the protocol silently drops.
OLD_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)

# `ui.tsx` carries every shape an address has to tell apart, on line numbers this
# file states rather than infers: an exported interface (3), an exported const
# (5), a private arrow function (7), an exported class whose members include one
# *named for the verb* (11, so `exportAll` tests the export test against an
# identifier that merely starts like the keyword), a two-line exported function
# (16-18), a default export (20-22), two classes sharing one member name (24-30,
# so `label` is genuinely ambiguous) and a plain unexported function (32).
UI_TSX = (
    "import { Card } from './card'\n"
    "\n"
    "export interface Props { q: string }\n"
    "\n"
    "export const LIMIT = 8\n"
    "\n"
    "const helper = (n: number) => n * 2\n"
    "\n"
    "export class Grid {\n"
    "  cols = 3;\n"
    "  exportAll = 4;\n"
    "  constructor(c: number) { this.cols = c }\n"
    "  render(q: string) { return helper(q.length) }\n"
    "}\n"
    "\n"
    "export function Hero({ q }: Props) {\n"
    "  return <Card n={helper(1)}>{q}</Card>\n"
    "}\n"
    "\n"
    "export default function Shell() {\n"
    "  return <Hero q=\"x\" />\n"
    "}\n"
    "\n"
    "class Dup {\n"
    "  label() { return 'a' }\n"
    "}\n"
    "\n"
    "class Other {\n"
    "  label() { return 'b' }\n"
    "}\n"
    "\n"
    "function neverExported() { return 0 }\n"
)

CARD_TSX = ("export function Card({ n, children }: any) {\n"
            "  return <div id={String(n)}>{children}</div>\n"
            "}\n")

# A Python file rides along because the dispatch is only honest if the other
# branch is untouched: `build` is L4-L5 and `Box.size`'s span starts on the
# decorator at L9, which is the rule R-3.2 was filed over.
MOD_PY = ("from util import helper\n"
          "\n"
          "\n"
          "def build(n):\n"
          "    return n * 2\n"
          "\n"
          "\n"
          "class Box:\n"
          "    @property\n"
          "    def size(self):\n"
          "        return 1\n")

BROKEN_TS = "export function oops( {\n"

FIXTURE = {"src/ui.tsx": UI_TSX, "src/card.tsx": CARD_TSX, "src/mod.py": MOD_PY,
           "src/broken.ts": BROKEN_TS}

_REFUSAL = ("ModuleNotFoundError: No module named 'tree_sitter'. The TypeScript "
             "grammar is an optional extra: pip install 'flash-coder[ts]'")

_REPO: Path | None = None


def repo() -> Path:
    global _REPO
    if _REPO is None:
        root = Path(tempfile.mkdtemp(prefix="tspatch"))
        for rel, src in FIXTURE.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(src, encoding="utf-8")
        _REPO = root
    return _REPO


def outcome(fn):
    """`fn()`'s value, or the refusal it raised.

    A mutated module can raise anywhere in this list. Recorded as a failed check
    rather than allowed to abort the run, because the mutant sweep needs this
    process to reach its verdict lines, and because "it raised" is itself the
    answer some of these checks are asking for.
    """
    try:
        return fn()
    except patches.Refused as exc:
        return f"REFUSED: {exc}"
    except Exception as exc:                       # noqa: BLE001 - recorded
        return f"RAISED: {type(exc).__name__}: {exc}"


def _refusal(res):
    """The one refusal reason in an `ApplyResult`, or `''` when it applied."""
    if isinstance(res, str):                       # outcome() caught a raise
        return res.replace("REFUSED: ", "")
    return "" if res.ok else res.refusals[0][1]


def get(defs, qual: str) -> Def | None:
    """One definition by qualified name.

    Tolerates `outcome()`'s refusal string in `defs`' place: a mutated module can
    fail to produce a table at all, and the check that wanted the table has to
    FAIL rather than end the sweep with a traceback.
    """
    if not isinstance(defs, list):
        return None
    return next((d for d in defs if f"{d.container}.{d.name}".strip(".") == qual),
                None)


def edit_arm(files: dict, response: str):
    """One turn of `loop._solve_edits`, with what it reads stubbed.

    `_generate` is the generation seam and `harness.diagnose_files` the
    verification seam — `flash.patches` binds neither at import time — so this
    drives the arm that ACTUALLY consumes the language gate: the file-by-file
    `static_check` pass that runs between an accepted patch set and the
    verifier. Neither stub changes what the gate is asked to decide, and the
    `Attempt` handed back is the verdict a model would have been sent chasing.
    """
    from flash import harness, loop
    real_gen, real_diag = loop._generate, harness.diagnose_files
    loop._generate = lambda *a, **kw: response
    harness.diagnose_files = lambda f, t: (True, "")
    try:
        res = loop._solve_edits(None, None,
                                {"id": "ts-arm", "prompt": "raise the limit",
                                 "test": "assert True", "files": files},
                                1, 64)
    finally:
        loop._generate, harness.diagnose_files = real_gen, real_diag
    return res.attempts[0] if res.attempts else None


# ------------------------------------------------------------------- the checks

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    d = ""
    if not ok:
        # A mutant can break the thing a detail line prints as well as the thing
        # a check tests, and the sweep needs this process to reach its verdict:
        # a failure to explain a fail is recorded as one, not raised.
        try:
            d = detail if isinstance(detail, str) else str(
                detail(cond) if callable(detail) else detail)
        except Exception as exc:                    # noqa: BLE001 - recorded
            d = f"the detail raised {type(exc).__name__}"
    CHECKS.append((name, ok, d))


def run_checks() -> int:
    CHECKS.clear()
    root = repo()
    ok, why = lang_ts.available()
    check("the grammar is here, so this run measures the fix and not a stub",
          ok, why)
    if not ok:
        return 1

    # --- the dispatch, and the spans it reads
    defs = outcome(lambda: definitions(UI_TSX, "src/ui.tsx"))
    if isinstance(defs, str):
        defs = []
        check("second grammar for a `.tsx` address", False, defs)
    qual = {f"{d.container}.{d.name}".strip(".") for d in defs}
    check("the second grammar answers for a `.tsx` address, and finds all "
          "fifteen definitions the file has",
          len(defs) == 15 and qual == {
              "Props", "LIMIT", "helper", "Grid", "Grid.cols", "Grid.exportAll",
              "Grid.constructor", "Grid.render", "Hero", "Shell", "Dup",
              "Dup.label", "Other", "Other.label", "neverExported"},
          f"{len(defs)}: {sorted(qual)}")
    check("the module itself is not an addressable definition",
          all(d.name for d in defs) and not any(d.container == "" and d.name == ""
                                                for d in defs),
          str([str(d) for d in defs if not d.name]))

    hero = get(defs, "Hero") or Def("", "", 0, 0, 0)
    check("an exported function's span is the whole statement, `export` keyword "
          "included", hero.start == 16 and hero.end == 18, str(hero))
    nodes, _ix = lang_ts._declarations("src/ui.tsx",
                                       lang_ts.parse("src/ui.tsx", UI_TSX)
                                       .root_node)
    spans = {n.symbol: (n.start, n.end) for n in nodes[1:]}
    check("the patch span and the graph span are the same two line numbers for "
          "every symbol in the file",
          len(defs) == len(spans)
          and all(spans.get(f"{d.container}.{d.name}".strip("."))
                  == (d.start, d.end) for d in defs),
          str([(str(d), spans.get(f"{d.container}.{d.name}".strip(".")))
               for d in defs if spans.get(f"{d.container}.{d.name}".strip("."))
               != (d.start, d.end)]))
    mixed = outcome(lambda: graph.build(root, langs=("python", "typescript")))
    node = mixed.nodes.get("src/ui.tsx::Grid.render") if isinstance(mixed,
                                                                    graph.Graph) \
        else None
    check("and they are still the same after the graph the CLI builds, not just "
          "in one walk",
          node is not None and (node.start, node.end) == (13, 13)
          and str(get(defs, "Grid.render")) == "Grid.render L13-L13",
          str(node))

    member = get(defs, "Grid.render") or Def("", "", 0, 0, 0)
    check("a method is addressable as `Class.method`, and its container is the "
          "class", member.container == "Grid" and member.name == "render",
          str(member))
    check("a member's replacement is restored to the member's real indent, not "
          "to column 0",
          all(d.col == (2 if d.container else 0) for d in defs),
          str([(str(d), d.col) for d in defs if
               d.col != (2 if d.container else 0)]))
    exported = {f"{d.container}.{d.name}".strip("."): d.exported for d in defs}
    check("a class member named for the verb is not mistaken for an export",
          exported.get("Grid.exportAll") is False
          and exported.get("Grid.render") is False
          and exported.get("Hero") and exported.get("Shell")
          and exported.get("Props") and exported.get("LIMIT")
          and exported.get("Grid"),
          str(exported))
    check("the shapes an address must tell apart keep their own spans: "
          "interface L3, const L5, private arrow L7, default export L20-L22, "
          "unexported class L24-L26",
          (str(get(defs, "Props")), str(get(defs, "LIMIT")),
           str(get(defs, "helper")), str(get(defs, "Shell")),
           str(get(defs, "Dup")))
          == ("Props L3-L3", "LIMIT L5-L5", "helper L7-L7", "Shell L20-L22",
              "Dup L24-L26"),
          str([str(get(defs, k)) for k in ("Props", "LIMIT", "helper", "Shell",
                                           "Dup")]))

    as_py = outcome(lambda: definitions(UI_TSX))
    check("the same TypeScript read as Python defines nothing — the refusal this "
          "box was filed for", isinstance(as_py, list) and as_py == [],
          str(as_py)[:120])
    py = outcome(lambda: definitions(MOD_PY, "src/mod.py"))
    py_default = outcome(lambda: definitions(MOD_PY))
    check("a Python file's definitions do not move when the dispatch arrives",
          py == py_default and len(py) == 3, f"{py} vs {py_default}")
    check("and a Python decorator is still inside the span through the "
          "path-aware call",
          str(get(py, "Box.size")) == "Box.size L9-L11", str(get(py, "Box.size")))
    check("`is_python` keeps the `ast` static pass away from a `.tsx` and off "
          "nothing else",
          patches.is_python("src/mod.py") and patches.is_python("")
          and not patches.is_python("src/ui.tsx")
          and not patches.is_python("src/card.tsx"),
          str([patches.is_python(k) for k in ("src/mod.py", "", "src/ui.tsx")]))

    # --- resolution
    a = outcome(lambda: patches.resolve(Patch("src/ui.tsx", "Hero", "x"), UI_TSX))
    check("a `.tsx` symbol address resolves to exactly the lines it owns",
          isinstance(a, patches.Applied) and (a.start, a.end, a.col) == (16, 18, 0),
          str(a))
    amb = outcome(lambda: patches.resolve(Patch("src/ui.tsx", "label", "x"), UI_TSX))
    check("an ambiguous member name refuses and prints both places",
          isinstance(amb, str) and "ambiguous" in amb and "L25" in amb
          and "L29" in amb, str(amb)[:120])
    rng = outcome(lambda: patches.resolve(Patch("src/ui.tsx", "L17-L17", "x"),
                                          UI_TSX))
    check("a range address inside one TypeScript definition resolves",
          isinstance(rng, patches.Applied) and (rng.start, rng.end) == (17, 17),
          str(rng))
    strad = outcome(lambda: patches.resolve(Patch("src/ui.tsx", "L13-L16", "x"),
                                            UI_TSX))
    check("a range that straddles two TypeScript definitions refuses",
          isinstance(strad, str) and "not inside any symbol" in strad,
          str(strad)[:120])
    ghost = outcome(lambda: patches.resolve(Patch("src/ui.tsx", "Nope", "x"),
                                            UI_TSX))
    check("an unknown symbol still lists what the file defines",
          isinstance(ghost, str) and "no symbol" in ghost and "Hero" in ghost
          and "neverExported" in ghost, str(ghost)[:160])

    # --- applying
    ws = {"src/ui.tsx": UI_TSX, "src/card.tsx": CARD_TSX}
    body = ("export function Hero({ q }: Props) {\n"
            "  return <Card n={helper(2)} title={q}>{q}</Card>\n}")
    res = outcome(lambda: apply_patches(ws, [Patch("src/ui.tsx", "Hero", body)]))
    want = splice(UI_TSX, 16, 18, body)
    check("a patch on a real component applies, and every byte outside the span "
          "is the file's own",
          isinstance(res, patches.ApplyResult) and res.ok
          and res.files["src/ui.tsx"] == want
          and res.files["src/card.tsx"] == CARD_TSX,
          _refusal(res))
    jsx = ("export function Card({ n, children }: any) {\n"
           "  const rows: number[] = [n, n * 2]\n"
           "  return <div id={String(n)}>{rows.map((r) => r)}{children}</div>\n}")
    typed = outcome(lambda: apply_patches(
        {"src/card.tsx": CARD_TSX}, [Patch("src/card.tsx", "Card", jsx)]))
    check("valid TypeScript is never refused for not being Python — generics, a "
          "type annotation and an arrow function all survive the check",
          isinstance(typed, patches.ApplyResult) and typed.ok
          and typed.files["src/card.tsx"] == splice(CARD_TSX, 1, 3, jsx),
          _refusal(typed))
    def grid_of(text: str) -> str:
        """The whole `export class Grid` block as an address would re-type it.

        Slicing to the line before the next top-level statement is the point of
        a class-level address: the model writes every member it saw.
        """
        return text[text.index("export class Grid"):
                    text.index("\n\nexport function Hero")]

    wide = grid_of(UI_TSX.replace("  render(q: string) { return helper(q.length) }",
                                  "  render(q: string) { return helper(q.length)"
                                  " + 1 }"))
    tres = outcome(lambda: apply_patches(ws, [Patch("src/ui.tsx", "Grid", wide)]))
    tax = {"file": "src/ui.tsx", "symbol": "Grid.render"}
    owned = tres.applied[0].end - tres.applied[0].start + 1 \
        if isinstance(tres, patches.ApplyResult) and tres.ok and tres.applied else 0
    check("an over-wide class address is narrowed to the member whose bytes "
          "differ, so the siblings are copied not re-emitted",
          isinstance(tres, patches.ApplyResult) and tres.ok and owned
          and tres.applied[0].regenerated < owned
          and outside_lines(ws, tres, tax) == 0,
          f"{_refusal(tres)} regenerated {owned and tres.applied[0].regenerated}"
          f" of {owned} line(s)")
    drift = grid_of(UI_TSX.replace("  render(q: string) { return helper(q.length) }",
                                   "  render(q: string) { return helper(q.length) "
                                   "+ 1 }").replace("  cols = 3;", "  cols = 4;"))
    dres = outcome(lambda: apply_patches(
        ws, [Patch("src/ui.tsx", "Grid", drift)]))
    check("a sibling the model really did change is still spliced and still "
          "counted by the audit",
          isinstance(dres, patches.ApplyResult) and dres.ok
          and outside_lines(ws, dres, tax) == 1,
          lambda: f"{outside_lines(ws, dres, tax)} line(s) outside, or the "
                  f"result is {str(dres)[:80]}")
    two = outcome(lambda: apply_patches(ws, [Patch("src/ui.tsx", "Hero", body),
                                             Patch("src/ui.tsx", "L17-L17",
                                                   "  return null")]))
    check("two TypeScript patches whose spans overlap refuse",
          isinstance(two, patches.ApplyResult) and not two.ok
          and "overlaps" in _refusal(two) and two.files["src/ui.tsx"] == UI_TSX,
          _refusal(two)[:120])
    set_ = outcome(lambda: apply_patches(ws, [Patch("src/ui.tsx", "Hero", body),
                                              Patch("src/ui.tsx", "Shell",
                                                    "function Shell() {\n"
                                                    "  return null\n}")]))
    check("one refusal voids the set: the component keeps its bytes",
          isinstance(set_, patches.ApplyResult) and not set_.ok
          and "no longer exports Shell" in _refusal(set_)
          and set_.files == ws, _refusal(set_)[:120])

    # --- what a replacement must keep
    renamed = outcome(lambda: apply_patches(
        ws, [Patch("src/ui.tsx", "Hero", body.replace("function Hero",
                                                     "function Heroine"))]))
    check("a replacement that drops the symbol is refused",
          isinstance(renamed, patches.ApplyResult) and not renamed.ok
          and "no longer defines Hero" in _refusal(renamed),
          _refusal(renamed)[:120])
    unexported = outcome(lambda: apply_patches(
        ws, [Patch("src/ui.tsx", "Hero", body.replace("export function",
                                                     "function"))]))
    check("a replacement that drops the `export` keyword is refused, and says "
          "which definition",
          isinstance(unexported, patches.ApplyResult) and not unexported.ok
          and "no longer exports Hero" in _refusal(unexported)
          and "Hero L16-L18" in _refusal(unexported)
          and unexported.files == ws, _refusal(unexported)[:140])
    method = outcome(lambda: apply_patches(
        ws, [Patch("src/ui.tsx", "Grid.render",
                   "  render(q: string) { return helper(q.length) + 2 }")]))
    check("a method patch is never told to re-export its class's member",
          isinstance(method, patches.ApplyResult) and method.ok,
          _refusal(method)[:140])
    broken = ("export function Hero({ q }: Props) {\n"
              "  return <Card>{q}\n}")
    bres = outcome(lambda: apply_patches(ws, [Patch("src/ui.tsx", "Hero", broken)]))
    msg = _refusal(bres)
    line = re.search(r"line (\d+)", msg)
    check("a replacement that breaks TypeScript is refused with the line the "
          "grammar found",
          isinstance(bres, patches.ApplyResult) and not bres.ok
          and "replacement does not parse" in msg and bres.files == ws, msg[:140])
    check("and the line is the file's, pointing at the broken statement rather "
          "than at the address",
          line is not None and 16 <= int(line.group(1)) <= 18, msg[:140])
    check("and that message is the grammar's, not Python's",
          "invalid syntax" not in msg and "indented block" not in msg
          and "error 'return <Card>{q}'" in msg, msg[:140])
    half = "def build(n):\n    return n * (2"
    # The oracle is the spliced file, in file coordinates: `build` owns L4-L5, so
    # the line Python reports is 5 while the TypeScript branch would have said
    # `error '...'`. Deriving the sentence from `ast` rather than writing 5 here
    # is what keeps this check true on an interpreter that words it differently.
    after_py = splice(MOD_PY, 4, 5, half)
    try:
        ast.parse(after_py)
        pymsg = "the broken file parsed, so the check is wrong"
    except SyntaxError as exc:
        pymsg = f"replacement does not parse: line {exc.lineno}: {exc.msg}"
    pres = outcome(lambda: apply_patches(
        {"src/mod.py": MOD_PY}, [Patch("src/mod.py", "build", half)]))
    check("a Python replacement that does not parse still gets the `ast` "
          "sentence, not the grammar's",
          isinstance(pres, patches.ApplyResult) and not pres.ok
          and _refusal(pres) == pymsg, f"{_refusal(pres)} != {pymsg}")
    badws = {"src/broken.ts": BROKEN_TS}
    noaddress = outcome(lambda: apply_patches(
        badws, [Patch("src/broken.ts", "oops", "export function oops() {}\n")]))
    check("a workspace file that does not parse cannot be addressed, and says "
          "that rather than claiming it defines nothing",
          isinstance(noaddress, patches.ApplyResult) and not noaddress.ok
          and "does not parse" in _refusal(noaddress)
          and "defines: nothing" not in _refusal(noaddress),
          _refusal(noaddress)[:140])

    # --- the protocol surface
    resp = (f"# edit: src/ui.tsx :: Hero\n```tsx\n{body}\n```\n\n"
            "Here is the log line I also pasted:\n```text\n"
            "# edit: src/ui.tsx :: Shell\nboom\n```\n")
    ps = parse_patches(resp)
    check("a patch body fenced `tsx` is read as a patch, and a fenced log line "
          "still is not one",
          len(ps) == 1 and ps[0].file == "src/ui.tsx" and ps[0].address == "Hero"
          and ps[0].body == body, f"{len(ps)} patch(es)")
    listing = describe({"src/ui.tsx": UI_TSX, "src/mod.py": MOD_PY})
    check("`describe` labels every file with the language it is written in",
          "```tsx\n" in listing and "```python\n" in listing
          and "# file: src/ui.tsx\n```tsx" in listing
          and "# file: src/mod.py\n```python" in listing,
          listing[:80])
    check("the protocol text tells the model what a `.tsx` replacement must keep",
          "`.ts` or `.tsx`" in patches.PROTOCOL
          and "keep the `export` keyword" in patches.PROTOCOL,
          patches.PROTOCOL[:200])
    wsdir = patches.workspace_from_dir(root / "src", limit=20)
    check("`workspace_from_dir` hands a TypeScript tree to the patch arm, keyed "
          "by relative path",
          {"ui.tsx", "card.tsx", "broken.ts", "mod.py"} <= set(wsdir)
          and wsdir["ui.tsx"] == UI_TSX, str(sorted(wsdir)))
    one = patches.workspace_from_dir(root / "src", limit=1)
    check("and a mixed tree still leads with Python, under the same cap",
          list(one) == ["mod.py"], str(sorted(one)))
    check("the empty path the graph passes never dispatches",
          patches._is_ts("") is False and patches._is_ts("src/mod.py") is False
          and patches._is_ts("a.d.ts") is True,
          str([patches._is_ts(k) for k in ("", "src/mod.py", "a.d.ts")]))

    # --- the arm that consumes it
    ts_att = outcome(lambda: edit_arm({"src/ui.tsx": UI_TSX,
                                       "src/card.tsx": CARD_TSX},
                                      f"# edit: src/ui.tsx :: Hero\n```tsx\n"
                                      f"{body}\n```\n"))
    check("the loop's edit arm ACCEPTS a clean TypeScript patch, so the `ast` "
          "pass it runs before the verifier is kept away from a file it cannot "
          "read — a false STATIC there costs the model a retry for nothing",
          ts_att is not None and ts_att.ok and "STATIC" not in ts_att.err,
          "" if ts_att is None else ts_att.err[:140])
    py_att = outcome(lambda: edit_arm({"src/mod.py": MOD_PY},
                                      "# edit: src/mod.py :: build\n```python\n"
                                      "def build(n):\n    return n * (2\n```\n"))
    check("and the same arm still hands a broken Python patch back as a refusal, "
          "so the language gate is a skip and not a swallow",
          py_att is not None and not py_att.ok and "REFUSED" in py_att.err,
          "" if py_att is None else py_att.err[:140])

    # --- the optional extra, absent
    def without(fn):
        real = lang_ts.available
        lang_ts.available = lambda: (False, _REFUSAL)
        try:
            return fn()
        finally:
            lang_ts.available = real

    # `outcome` runs INSIDE `without`, not outside it: a mutant that deletes the
    # gate converting the grammar's `RuntimeError` into a refusal raises through
    # `apply_patches` while the stub is still the grammar's answer, and the stub
    # has to come off before anything else in the process notices.
    refused = without(lambda: _refusal(outcome(lambda: apply_patches(
        ws, [Patch("src/ui.tsx", "Hero", body)]))))
    check("without the grammar a `.tsx` address refuses with the install "
          "command, not an `ast` error about valid TypeScript",
          "optional extra" in refused and "flash-coder[ts]" in refused
          and "invalid syntax" not in refused and "defines: nothing" not in refused,
          refused[:160])
    escaped = without(lambda: outcome(lambda: apply_patches(
        ws, [Patch("src/ui.tsx", "Hero", body)])))
    check("and the refusal arrives inside the atomic patch set rather than as an "
          "exception through `apply_patches`",
          not str(escaped).startswith("RAISED:") and "REFUSED:" not in str(escaped),
          str(escaped)[:160])
    pyok = without(lambda: outcome(lambda: apply_patches(
        {"src/mod.py": MOD_PY}, [Patch("src/mod.py", "build",
                                       "def build(n):\n    return n")])))
    check("Python patches still work with the grammar absent, so the second "
          "language is an addition and not a dependency",
          isinstance(pyok, patches.ApplyResult) and pyok.ok,
          _refusal(pyok)[:120])
    return sum(1 for _, ok_, _ in CHECKS if not ok_)


def report() -> int:
    bad = run_checks()
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}"
              + ("" if ok or not detail else f"\n      -> {detail}"))
    print(f"\nR-1.4 TypeScript patch arm: {len(CHECKS) - bad}/{len(CHECKS)} "
          f"checks passed")
    return 1 if bad else 0


# ------------------------------------------------------------------- the mutants

def mutants() -> list[tuple[str, object, object, str]]:
    """Each entry: a bug, where it goes back, and the check that must catch it."""
    L = lang_ts
    P = patches
    _defs = L.definitions
    _names = P._names
    _listdir = P.workspace_from_dir

    def never_ts(path):
        return False                       # every file is Python again

    def always_ts(path):
        return True

    def exports_everything(line):
        return line.lstrip().startswith("export")   # `exportAll` is an export

    def exports_nothing(line):
        return False

    def syntax_blind(path, src):
        return ""                          # trust the tree, whatever it says

    def no_parse_gate(path, src):
        return L.definitions(path, src)

    def col_zero(rel, src):
        return [Def(d.name, d.container, d.start, d.end, 0, d.exported)
                for d in _defs(rel, src)]

    def name_whole(rel, src):
        return [Def(d.name if not d.container else f"{d.container}.{d.name}", "",
                    d.start, d.end, d.col, d.exported) for d in _defs(rel, src)]

    def names_never_exported(src, path=""):
        return {k: False for k in _names(src, path)}

    def tag_always_python(fname):
        return "python"

    def workspace_python_only(root, limit=20):
        return {k: v for k, v in _listdir(root, limit).items()
                if not k.endswith((".ts", ".tsx"))}

    def everything_is_python(path):
        return True

    return [
        ("routes every file to `ast`, so a TypeScript address is refused as an "
         "unknown symbol — the defect this box was filed for",
         (P, "_is_ts"), never_ts, "fifteen definitions the file has"),
        ("routes every file to the TypeScript grammar, so the published Python "
         "counts describe an index the tool no longer builds",
         (P, "_is_ts"), always_ts, "do not move when the dispatch arrives"),
        ("reads the word `export` at the front of any name as the keyword, so a "
         "member called `exportAll` is claimed to be an interface",
         (L, "_begins_export"), exports_everything, "named for the verb"),
        ("never notices an `export` keyword, so a patch that un-exports a "
         "component applies and every importer breaks",
         (L, "_begins_export"), exports_nothing, "drops the `export` keyword"),
        ("trusts a tree the grammar had to repair, so a replacement that does "
         "not parse is applied",
         (P, "_ts_syntax"), syntax_blind, "the line the grammar found"),
        ("addresses a file that does not parse instead of refusing it, reading "
         "spans off a tree that had to guess",
         (P, "_ts_definitions"), no_parse_gate, "cannot be addressed"),
        ("forgets the member's indentation, so a method patch lands at column 0 "
         "and the class body breaks",
         (L, "definitions"), col_zero, "the member's real indent"),
        ("flattens `Class.method` into one name, so no member is addressable",
         (L, "definitions"), name_whole, "container is the class"),
        ("forgets the export bit when it checks the result, so every correct "
         "patch on an exported symbol is refused",
         (P, "_names"), names_never_exported, "a patch on a real component applies"),
        ("reads only a `python` fence, so a model that mirrors the listing's "
         "`tsx` tag produces no patch at all",
         (P, "FENCE"), OLD_FENCE, "fenced `tsx` is read"),
        ("labels every file Python in the listing, so the model is told to write "
         "Python at a component",
         (P, "_fence_tag"), tag_always_python, "labels every file with the language"),
        ("gathers only Python into a workspace, so a `.tsx` address names a file "
         "that is not in the project",
         (P, "workspace_from_dir"), workspace_python_only, "hands a TypeScript tree"),
        ("sends a `.tsx` through the `ast` static pass, so a clean TypeScript "
         "edit comes back a false syntax error",
         (P, "is_python"), everything_is_python, "ACCEPTS a clean TypeScript patch"),
    ]


def run_mutants(one: int | None = None, verbose: bool = False) -> int:
    bugs = mutants()
    if len(bugs) != NUM_BUGS:
        raise SystemExit(f"NUM_BUGS is stale: {len(bugs)} bugs listed, "
                         f"{NUM_BUGS} promised to --sweep")
    escaped = 0
    ran = 0
    for i, (label, (holder, attr), bug, catcher) in enumerate(bugs):
        if one is not None and i != one:
            continue
        ran += 1
        real = getattr(holder, attr)
        try:
            setattr(holder, attr, bug)
            run_checks()
            fails = [nm for nm, ok, _ in CHECKS if not ok]
        finally:
            setattr(holder, attr, real)
        hit = any(catcher in f for f in fails)
        escaped += not hit
        if verbose or not hit:
            print(f"  {'ok  ' if hit else 'MISS'} MUTATION: {label} -> "
                  f"{len(fails)} check(s) fail"
                  + ("" if hit else f", none of them the one that catches it: "
                                    f"{fails[:3]}"))
    print(f"ts-patch mutants: {ran - escaped}/{ran} caught"
          + ("" if one is None else f"  [{one}]"))
    return escaped


def sweep() -> int:
    """One fresh process per mutant, then the whole list in this one.

    `--mutants` runs all thirteen in the interpreter that wrote them, where one
    bug's patched module is the next bug's starting state — `_is_ts` is read by
    six of the thirteen and `lang_ts.definitions` by two. A green count seen only
    in a shared process could be the previous mutant's leftover, so this spawns
    one process per bug, re-runs the list here, and fails unless both lanes catch
    all thirteen.
    """
    caught = 0
    for i in range(NUM_BUGS):
        proc = subprocess.run([sys.executable, "-u", str(Path(__file__).resolve()),
                               "--mutant", str(i)], capture_output=True, text=True,
                              cwd=ROOT)
        lines = [ln for ln in proc.stdout.splitlines() if "MUTATION:" in ln]
        if len(lines) != 1 or proc.returncode not in (0, 1):
            print(f"  MISS MUTATION [{i}]: no clean verdict "
                  f"(rc={proc.returncode})\n{proc.stdout[-300:]}")
            continue
        print("  fresh>" + lines[0].strip())
        caught += proc.returncode == 0
    print(f"ts-patch mutants, fresh process each: {caught}/{NUM_BUGS} caught")
    here = run_mutants()
    print(f"ts-patch mutants, one process:        "
          f"{NUM_BUGS - here}/{NUM_BUGS} caught")
    ok = caught == NUM_BUGS and here == 0
    print(("OK  each mutant is caught by its named check in a fresh process too, "
           "so no count here is a leftover from the previous bug"
           if ok else
           "FAIL  the two sweeps disagree or one escaped — the mutation claim is "
           "process-order-dependent"))
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    if "--mutant" in argv and "--sweep" not in argv:
        return 1 if run_mutants(one=int(argv[argv.index("--mutant") + 1]),
                                verbose=True) else 0
    # SPEC §6's line for this vector is `--sweep`, and one command has to print
    # both halves of its claim: the checks and the two mutation lanes.
    bad = report()
    if "--sweep" in argv:
        return 1 if (bad or sweep()) else 0
    if "--mutants" in argv:
        return 1 if bad or run_mutants(verbose="-v" in argv) else 0
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
