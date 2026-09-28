"""R-1.4, OFFLINE: does perception answer about TypeScript, and refuse well?

The box reads "Perception MUST extend to the second language of real work …
Vector: R-1.1..1.2 equivalents pass on a fixture tree in that language." So these
checks ask the same four questions the Python pass was asked, of a TypeScript tree:

* **What breaks if this symbol changes?** `blast()` over the mixed graph, with the
  edge that proves each hop and the `via` rule that made it.
* **What did you NOT find?** Every use the pass could not place is a counted blind
  spot, so an incomplete answer cannot read as a census — and a name a scope binds
  (a parameter, a destructured prop) must NOT be one.
* **Where do you stop?** An npm specifier, a broken import name, an `export *`
  re-export, a name that only passes through a re-export and a syntax-error file
  each get their own sentence, because "0 dependents" and "I could not look" are
  different facts.
* **What does it cost, and what does it refuse?** Cold-build and query times are
  printed apart (R-1.3's budget clause is about a query), and the missing grammar
  must produce a refusal the caller can print, not a graph built from string
  matching and not an `ImportError`.

No model loads and no network is used. The `tree_sitter` grammar is imported by
`flash.lang_ts` only, and the refusal checks fake its absence by replacing
`available()` — the module never un-imports what it already loaded.

    python benchmarks/ts_perception_check.py            # the checks
    python benchmarks/ts_perception_check.py --mutants  # put each bug back
    python benchmarks/ts_perception_check.py --sweep    # one fresh process each
    python benchmarks/ts_perception_check.py --mutant 6 # one bug, for a bisection
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import graph, lang_ts                          # noqa: E402

NUM_BUGS = 13

# A tree with one of each shape the pass has to tell apart. `data.ts` is the
# changed-file, `ui.tsx`/`main.ts`/`ns.ts` the callers, and the rest are the
# boundary cases: a directory import, a namespace import, a star re-export, a named
# re-export, a self-import cycle, a name nobody imports, and a file that does not
# parse. Two Python files ride along, because "the second language arrived" is only
# a fact if both are indexed in one tree and neither overwrites the other.
FIXTURE: dict[str, str] = {
    "src/data.ts": (
        "import { readFileSync } from 'fs'\n"
        "import * as util from 'util'\n"
        "export const VERSION = '1.0'\n"
        "export function int(x: number): number { return Math.round(x) }\n"
        "export function twice(x: number): number { return int(x) * 2 }\n"
        "export interface Row { a: string }\n"
        "export type Pair = Row & { b: number }\n"
        "export enum Mode { Fast = 1 }\n"
        "export class Loader {\n"
        "  path = '';\n"
        "  constructor(p: string) { this.path = p }\n"
        "  load(): Row { const raw = int(3); return this.parse(raw) }\n"
        "  parse(n: number): Row { return { a: String(n) } }\n"
        "}\n"
        "const hidden = (n: number) => n\n"
        "export const scale = (n: number) => hidden(n) * twice(n)\n"
        "export function uses_util(x: number): string {\n"
        "  return util.inspect(x) + readFileSync('')\n"
        "}\n"
    ),
    "src/ui.tsx": (
        "import { int, Loader } from './data'\n"
        "import { missing } from './data'\n"
        "import App2 from './main'\n"
        "import { motion } from 'framer-motion'\n"
        "export default function App() {\n"
        "  const [n, setN] = useState(1)\n"
        "  return <Card><span>{int(n)}</span><Loader /></Card>\n"
        "}\n"
        "function Card({ children }: { children: React.ReactNode })"
        " { return <div>{children}</div> }\n"
        "export function spun(): number { return motion(1) }\n"
        "export function reuse(): string { return App2 }\n"
    ),
    "src/lib/index.ts": "export function deep(): number { return 42 }\n",
    "src/main.ts": (
        "import { deep } from './lib'\n"
        "import { int } from './data'\n"
        "import type { Pair } from './data'\n"
        "export function boot(): Pair { return { a: 'x', b: deep() } }\n"
        "export function go(p: Pair): number { return int(p.b) }\n"
    ),
    "src/ns.ts": (
        "import * as data from './data'\n"
        "import { deep } from './rex'\n"
        "export function uses(): number { return data.int(1) + deep() }\n"
    ),
    "src/rex.ts": "export { deep } from './lib'\n",
    "src/star.ts": "export * from './data'\n",
    "src/via_star.ts": ("import { int } from './star'\n"
                        "export function wrap(n: number): number { return int(n) }\n"),
    "src/loop.ts": ("import { next } from './loop'\n"
                    "export function next(n: number): number"
                    " { return n > 0 ? next(n - 1) : 0 }\n"),
    "src/solo.ts": "export function only_here(): number { return 1 }\n",
    "src/guesser.ts": ("export function use_it(): number { return only_here() }\n"),
    "src/broken.ts": "export function oops( {\n",
}

# `boot` is deliberately a symbol in both languages: the check that a needle naming
# it cannot invent an edge between the two needs a real collision.
PY_FIXTURE: dict[str, str] = {
    "src/py_mod.py": (
        "from py_util import helper\n"
        "\n"
        "\n"
        "class Runner:\n"
        "    def run(self):\n"
        "        return helper()\n"
        "\n"
        "\n"
        "def boot():\n"
        "    return helper()\n"
    ),
    "src/py_util.py": "def helper():\n    return 1\n",
}

_REFUSAL = ("ModuleNotFoundError: No module named 'tree_sitter'. The TypeScript "
            "grammar is an optional extra: pip install 'flash-coder[ts]'")

_REPO: Path | None = None


def write_repo(files: dict[str, str], prefix: str = "tsrepo") -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    for rel, src in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    return root


def repo() -> Path:
    """One fixture tree per process, so a mutant and the checks share it."""
    global _REPO
    if _REPO is None:
        _REPO = write_repo({**FIXTURE, **PY_FIXTURE})
    return _REPO


# ------------------------------------------------------------------- the checks

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    d = ""
    if not ok:
        d = detail if isinstance(detail, str) else str(
            detail(cond) if callable(detail) else detail)
    CHECKS.append((name, ok, d))


def run_checks() -> int:
    """Every check, against a freshly built graph of the fixture tree."""
    CHECKS.clear()
    root = repo()
    ok, why = lang_ts.available()
    check("the grammar is here, so this run measures the pass and not a stub",
          ok, why)
    if not ok:
        return 1
    idx, trees = lang_ts.scan(root)
    g = lang_ts.build_ts(root)
    mixed = graph.build(root, langs=("python", "typescript"))
    py_only = graph.build(root)
    un = {(u.file, u.expr): u.reason for u in g.unresolved}

    # --- shape of what got indexed
    check("the scan finds every .ts and .tsx file and nothing else, and parses "
          "each one",
          len(trees) == len(FIXTURE) == len(idx.files)
          and all(p.suffix in (".ts", ".tsx") for p in lang_ts.ts_files(root)),
          f"{len(trees)} trees, {len(idx.files)} indexed")
    check("a file's own module node exists, so import edges have a far end",
          all(f"{rel}::" in g.nodes for rel in FIXTURE),
          [r for r in FIXTURE if f"{r}::" not in g.nodes])
    kinds = {n.symbol: n.kind for n in g.nodes.values() if n.file.endswith(".ts")}
    check("each declaration keeps its own kind: function, class, interface, type, "
          "enum and constant are five different answers to 'what is this'",
          (kinds.get("int"), kinds.get("Loader"), kinds.get("Row"),
           kinds.get("Pair"), kinds.get("Mode"), kinds.get("VERSION"))
          == ("function", "class", "interface", "type", "enum", "constant"),
          str(kinds))
    n = g.nodes["src/data.ts::Loader"]
    check("a class's span is the whole statement, so the range a graph reports is "
          "the range an address would replace",
          n.start == 9 and n.end == 14,
          f"L{n.start}-{n.end}")
    m = g.nodes["src/data.ts::Loader.load"]
    check("a method is addressable as `Class.method`, not only as its class",
          m.kind == "method" and m.start == 12, str(m))
    check("an exported arrow function is a `function`, because that is what a "
          "caller reaches for",
          g.nodes["src/data.ts::scale"].kind == "function",
          g.nodes["src/data.ts::scale"].kind)

    # --- edges, and the rule that made each one
    calls = {(x.src, x.dst, x.via) for x in g.resolved() if x.kind == "calls"}
    check("a call across two files lands on the exported symbol, not on the file",
          ("src/ui.tsx::App", "src/data.ts::int", "ts-import") in calls
          and ("src/main.ts::go", "src/data.ts::int", "ts-import") in calls,
          str(sorted(calls)))
    check("a call inside one file is labelled own-scope, so a reader can tell a "
          "module-local hop from a cross-file one",
          ("src/data.ts::twice", "src/data.ts::int", "ts-own-scope") in calls,
          str(sorted(calls)))
    check("a call through `import * as data` is followed by name, and says so "
          "(`ts-qualified-name`), because the head names no symbol and the tail "
          "does",
          ("src/ns.ts::uses", "src/data.ts::int", "ts-qualified-name") in calls,
          str(sorted(calls)))
    jsx = [x for x in g.resolved() if x.via == "ts-jsx"]
    check("a JSX tag is a call, which is what makes a component's blast radius a "
          "component's blast radius",
          {x.dst for x in jsx} == {"src/ui.tsx::Card", "src/data.ts::Loader"},
          str({(x.src, x.dst) for x in jsx}))
    check("an HTML tag with no definition or import behind it is nothing: `<span>` "
          "cannot be a repo symbol",
          not any("span" == x.dst.rpartition("::")[2] for x in g.resolved()))
    check("no symbol is its own dependent: a declaration's own name is a binding, "
          "not a read of itself (recursion is a call, and it is the one self-edge "
          "that means something)",
          not [e for e in g.resolved() if e.src == e.dst and e.kind == "reads"],
          str([(e.src, e.dst, e.via) for e in g.resolved()
               if e.src == e.dst and e.kind == "reads"][:4]))
    typ = {(x.src, x.dst) for x in g.resolved() if x.via == "ts-type"}
    check("a type position is a read, so a signature counts as a dependent",
          ("src/main.ts::boot", "src/data.ts::Pair") in typ
          and ("src/data.ts::Pair", "src/data.ts::Row") in typ, str(sorted(typ)))
    check("`React.ReactNode` is one written name: charging its tail would blame "
          "`ReactNode` for a global called React",
          not any(x.dst.endswith("::ReactNode") for x in g.resolved())
          and not any(u.expr == "ReactNode" for u in g.unresolved),
          [u.expr for u in g.unresolved if u.expr == "ReactNode"])
    this = [(x.src, x.dst) for x in g.resolved()
            if x.dst == "src/data.ts::Loader.path"]
    check("assigning to `this.field` in a constructor reads the field the class "
          "declares, which is the rename question in full",
          this == [("src/data.ts::Loader.constructor", "src/data.ts::Loader.path")],
          str(this))

    # --- what is NOT there, said out loud
    check("an npm specifier is a counted blind spot, never an edge to a node "
          "that does not exist",
          ("src/ui.tsx", "framer-motion") in un
          and "npm package" in un[("src/ui.tsx", "framer-motion")]
          and not any(x.dst.startswith("framer-motion") for x in g.resolved()),
          str(sorted(k for k in un if "framer" in k[1])))
    check("`fs` is the same answer for a Node builtin: this tree does not define it",
          ("src/data.ts", "fs") in un, str(sorted(un)[:6]))
    check("an import naming something the file does not export is reported at the "
          "statement, once",
          ("src/ui.tsx", "missing") in un
          and "does not export" in un[("src/ui.tsx", "missing")]
          and sum(1 for u in g.unresolved if u.expr == "missing") == 1,
          str([k for k in un if k[1] == "missing"]))
    check("a name reaching through `export *` is declared, not guessed",
          ("src/via_star.ts", "int") in un
          and "export *" in un[("src/via_star.ts", "int")],
          str([(k, un[k]) for k in un if k[0] == "src/via_star.ts"]))
    check("a name that only passes through a re-export is declared too, and the "
          "graph does not link it to a node that file does not have",
          ("src/ns.ts", "deep") in un
          and "re-exports" in un[("src/ns.ts", "deep")]
          and not any(x.dst == "src/rex.ts::deep" for x in g.resolved()),
          str([(k, un[k]) for k in un if k[0] == "src/ns.ts"]))
    check("a use no definition, import or platform name matches is a blind spot: "
          "`useState` here is neither React nor this tree",
          ("src/ui.tsx", "useState") in un
          and "no definition" in un[("src/ui.tsx", "useState")],
          str([k for k in un if k[1] == "useState"]))
    check("a parameter or a destructured binding is neither an edge nor a blind "
          "spot: `n`, `x` and `children` bind locally",
          not any(u.expr in ("n", "x", "children", "p", "raw") for u in g.unresolved)
          and not any(e.dst.endswith(("::n", "::x", "::children"))
                      for e in g.resolved()),
          str([u.expr for u in g.unresolved][:8]))
    check("a file the grammar cannot parse still gets a node and says its edges "
          "are a floor",
          "src/broken.ts::" in g.nodes
          and any(u.file == "src/broken.ts" and u.expr == "<file>"
                  and "syntax error" in u.reason for u in g.unresolved),
          str([u for u in g.unresolved if u.file == "src/broken.ts"]))
    check("a broken file cannot poison its neighbours: the good files' edges "
          "survive it",
          any(e.dst == "src/data.ts::int" for e in g.resolved()))

    # --- resolution rules that could get a file wrong
    check("`./lib` means `./lib/index.ts`, which is why the directory import "
          "resolves at all",
          any(e.dst == "src/lib/index.ts::" and e.kind == "imports"
              for e in g.resolved()),
          str([e.dst for e in g.resolved() if e.kind == "imports"]))
    check("extensionless `./data` means `data.ts`, not a guess at some other file",
          any(e.dst == "src/data.ts::" and e.file == "src/ui.tsx"
              for e in g.resolved()))
    check("a self-import is an edge, and `blast()` survives the cycle",
          any(e.dst == "src/loop.ts::" and e.file == "src/loop.ts"
              for e in g.resolved())
          and g.blast("next").target is not None)
    check("a default import of a file with no default export lands on the file "
          "and labels itself as doing that",
          any(e.via == "ts-import-file" and e.dst == "src/main.ts::"
              and e.src == "src/ui.tsx::reuse" for e in g.resolved()),
          str([(e.src, e.dst, e.via) for e in g.resolved()
               if e.src.startswith("src/ui.tsx::")]))
    check("a name nobody imports binds to the ONE file that exports it, and the "
          "edge admits it was guessed",
          any(e.via == "ts-unique-export" and e.dst == "src/solo.ts::only_here"
              for e in g.resolved()),
          str([(e.src, e.dst, e.via) for e in g.resolved()
               if e.file == "src/guesser.ts"]))

    # --- the query, which is the point of the whole exercise
    r = g.blast("int")
    d1 = {h.node.id: h.depth for h in g.blast("int", 1).hits}
    d3 = {h.node.id: h.depth for h in r.hits}
    check("who reaches `int`: five direct callers at one hop, and the caller of "
          "one of them at two",
          r.target is not None and set(d1) == {
              "src/data.ts::twice", "src/data.ts::Loader.load", "src/ui.tsx::App",
              "src/main.ts::go", "src/ns.ts::uses"}
          and d3.get("src/data.ts::scale") == 2
          and set(d1) <= set(d3),
          f"d1={sorted(d1)} d3={sorted(d3.items())}")
    check("the radius is the graph's own answer, not a TypeScript special case: "
          "`export *` keeps `wrap` out of it, and that is a declared hole rather "
          "than a missing dependent",
          "src/via_star.ts::wrap" not in d3 and ("src/via_star.ts", "int") in un,
          str(sorted(d3)))
    check("every hop carries the line that proves it",
          all(h.edge is not None and h.edge.line > 0 and h.edge.text
              for h in r.hits),
          str([(h.node.id, h.edge and h.edge.line) for h in r.hits]))
    check("a file's importers are answered apart from its callers, so `flash "
          "graph` can say who imports the module and who calls into it separately",
          all(e.kind == "imports" for e in g.blast("src/data.ts::").importers)
          and {e.file for e in g.blast("src/data.ts::").importers}
          == {"src/ui.tsx", "src/main.ts", "src/ns.ts"},
          str([(e.file, e.kind) for e in g.blast("src/data.ts::").importers]))
    check("a symbol's own radius holds no import edge: `Loader` is imported by a "
          "file, but its dependents are the code that reaches it",
          all(h.edge.kind != "imports" for h in g.blast("Loader").hits)
          and g.blast("Loader").importers == [],
          str([(h.node.id, h.edge.kind) for h in g.blast("Loader").hits]))
    summary = g.blast("int").summary()
    check("the summary states its own floor, so a partial answer cannot read as a "
          "census",
          f"{g.blind_spots()} blind spot" in summary, summary)
    t0 = graph._now_ms()
    mixed.blast("Loader")
    q_ms = graph._now_ms() - t0
    check("a query on the mixed index answers inside R-1.3's budget",
          q_ms < graph.BUDGET_MS, f"{q_ms:.1f} ms")

    # --- the two languages, in one index and not mixed up
    check("Python alone is still the default: the committed numbers describe that "
          "index, and a second language cannot enter it silently",
          all(not n.file.endswith((".ts", ".tsx")) for n in py_only.nodes.values())
          and len(py_only.nodes) > 0
          and any(n.file.endswith(".tsx") for n in mixed.nodes.values()),
          f"{len(py_only.nodes)} vs {len(mixed.nodes)} nodes")
    check("one file's nodes carry one language: no Python id was overwritten by "
          "TypeScript or the other way round",
          len(mixed.nodes) == len(py_only.nodes) + len(g.nodes),
          f"{len(py_only.nodes)} + {len(g.nodes)} != {len(mixed.nodes)}")
    check("a `flash graph` answer can name a symbol that exists in both languages "
          "without inventing an edge between them",
          len({n.id for n in mixed.get("boot")}) == 2
          and not any(e.src.startswith("src/py_") and e.dst.endswith(".ts::boot")
                      for e in mixed.resolved()),
          str(sorted({n.id for n in mixed.get("boot")})))
    check("the mixed graph still round-trips through JSON, which is how a report "
          "or the loop's cache would carry it",
          graph.Graph.from_json(mixed.to_json()).blast("int").target is not None,
          "blast target lost in the round trip")

    # --- refusal, which is the part that decides whether an install lies
    real_avail, real_parse = lang_ts.available, lang_ts.parse
    try:
        lang_ts.available = lambda: (False, _REFUSAL)

        def boom(*a, **k):
            raise RuntimeError(lang_ts.available()[1])

        lang_ts.parse = boom
        refused = None
        try:
            lang_ts.build_ts(root)
        except RuntimeError as exc:
            refused = str(exc)
        check("without the grammar the build raises one sentence that names the "
              "install command, rather than a traceback or a graph of nothing",
              refused is not None and "optional extra" in refused
              and "flash-coder[ts]" in refused, str(refused))
        gg = graph.build(root, langs=("python", "typescript"))
        check("the whole tool still answers with the language it has: the "
              "Python pass runs and the refusal is recorded, not swallowed",
              gg.stats.get("langs") == "python"
              and str(gg.stats.get("ts", "")).startswith("refused:")
              and any(n.kind == "class" for n in gg.nodes.values()),
              str(gg.stats))
        check("a refused grammar is a counted blind spot in the index too, so "
              "`summary()` cannot read as a complete answer",
              any(u.expr == "<grammar>" and "optional extra" in u.reason
                  for u in gg.unresolved),
              str(gg.unresolved[-1:]))
    finally:
        lang_ts.available, lang_ts.parse = real_avail, real_parse

    # --- the CLI surface
    check("`--lang ts` and `py,ts` both parse, and an empty spec is Python",
          graph.parse_langs("ts") == ("typescript",)
          and graph.parse_langs("python,tsx") == ("python", "typescript")
          and graph.parse_langs("") == ("python",),
          str(graph.parse_langs("ts")))
    try:
        graph.parse_langs("ruby")
        check("an unknown language raises rather than quietly indexing nothing",
              False, "no exception")
    except ValueError as exc:
        check("an unknown language raises rather than quietly indexing nothing",
              "ruby" in str(exc) and "typescript" in str(exc), str(exc))

    # --- cost, printed apart from the query budget
    check("the cold build is timed and reported, so an index claim cannot be "
          "mistaken for an answer claim",
          g.stats.get("ts_files") == len(FIXTURE)
          and float(g.stats.get("ts_cold_ms", 0)) > 0.0,
          str(g.stats))
    return sum(1 for _, ok_, _ in CHECKS if not ok_)


def report() -> int:
    bad = run_checks()
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}"
              + ("" if ok or not detail else f"\n      -> {detail}"))
    print(f"\nR-1.4 TypeScript perception: {len(CHECKS) - bad}/{len(CHECKS)} "
          f"checks passed")
    return 1 if bad else 0


# ------------------------------------------------------------------- the mutants

def mutants() -> list[tuple[str, object, object, str]]:
    """Each entry: a bug, where it goes back, and the check that must catch it."""
    L = lang_ts
    _place = L.RepoIndex.place                    # the real one, before any patch
    _decls = L._declarations
    _rows = L._import_rows
    _real_build = graph.build

    def place_guesses(self, spec, from_file):
        return ""                                 # nothing resolves, nothing is said

    def place_binds_packages(self, spec, from_file):
        if spec.startswith("."):
            return _place(self, spec, from_file)
        return next(iter(self.files))             # npm lands on a repo file

    def unique_refuses(self, name):
        return ""

    def binds_nothing(decl):
        return set()

    def index_never_tried(target):
        return [target + s for s in L.TS_SUFFIXES]   # no `dir/index.ts` form
    def every_name_is_a_use(parent, child):
        return False

    def cls_forgets_its_class(kind, sym):
        return ""

    def errors_are_silent(node):
        return None

    def type_name_tails(n):
        return L._text(n.child_by_field_name("name"))

    def star_is_not_a_star(rel, root_node):
        nodes, ix = _decls(rel, root_node)
        ix.follows_star = False                   # pretend it is known
        return nodes, ix

    def span_without_the_keyword(rel, root_node):
        nodes, ix = _decls(rel, root_node)
        return [graph.Node(n.file, n.symbol, n.kind, n.start + 1, n.end)
                if n.symbol == "Loader" else n for n in nodes], ix

    def ts_by_default(root, langs=("python", "typescript")):
        return _real_build(root, langs=langs)

    def rows_lose_the_namespace(node):
        return [r for r in _rows(node) if r[2] != "*"]

    return [
        ("resolves no relative specifier, so `./data` finds nothing and every "
         "cross-file edge disappears", (L.RepoIndex, "place"), place_guesses,
         "means `data.ts`"),
        ("treats an npm package as a repo file, pointing the tree at a file that "
         "merely shares its name", (L.RepoIndex, "place"), place_binds_packages,
         "npm specifier"),
        ("refuses to follow a name that only one file exports, so a real "
         "dependency is dropped without a word",
         (L.RepoIndex, "unique_export"), unique_refuses,
         "the ONE file that exports it"),
        ("forgets what a function binds, turning every parameter into a blind spot "
         "and drowning the ones that mean something",
         (L, "_scope_locals"), binds_nothing, "neither an edge nor a blind"),
        ("tries the suffixes but never a directory's index file, so `./lib` names "
         "nothing and a real dependency is lost",
         (L, "_candidates"), index_never_tried, "means `./lib/index.ts`"),
        ("charges a declaration's own name as a use of itself",
         (L, "_is_own_name"), every_name_is_a_use, "its own dependent"),
        ("loses the class a method lives in, so `this.path` and `this.parse()` "
         "name nothing",
         (L, "_cls_of"), cls_forgets_its_class, "this.field"),
        ("reads a syntax-error file as if it had answered: no floor, no note",
         (L, "_first_error"), errors_are_silent, "cannot parse"),
        ("splits a dotted type name in two and blames the tail",
         (L, "_dotted_type"), type_name_tails, "one written name"),
        ("follows `export *` as if it had read the forwarded file",
         (L, "_declarations"), star_is_not_a_star, "`export *` is declared"),
        ("reports a class by its declaration line, one line short of the range an "
         "address would replace", (L, "_declarations"), span_without_the_keyword,
         "whole statement"),
        ("binds no name for `import * as X`, so the namespace is never in scope "
         "and every dotted call through it is dropped",
         (L, "_import_rows"), rows_lose_the_namespace, "followed by name"),
        ("indexes TypeScript by default, so every published Python figure would "
         "describe an index the tool no longer builds",
         (graph, "build"), ts_by_default, "still the default"),
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
    print(f"ts-perception mutants: {ran - escaped}/{ran} caught"
          + ("" if one is None else f"  [{one}]"))
    return escaped


def sweep() -> int:
    """One fresh process per mutant, then the whole list in this one.

    `--mutants` runs all thirteen in the interpreter that wrote them, where one
    bug's patched module is the next bug's starting state — ` RepoIndex.place`
    and `_declarations` are shared, and two of the thirteen patch them. A green
    count seen only in a shared process could be the previous mutant's leftover,
    so this spawns one process per bug, re-runs the list here, and fails unless
    both lanes catch all thirteen.
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
    print(f"ts-perception mutants, fresh process each: {caught}/{NUM_BUGS} caught")
    here = run_mutants()
    print(f"ts-perception mutants, one process:        "
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
