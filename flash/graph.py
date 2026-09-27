"""R-1.3: the codebase knowledge graph — what breaks if this symbol changes (PLAN §28).

§28's claim is that a graph, not a search, is what lets the agent *know the
scope*: "Nodes = functions, classes, files … edges = calls/imports … and **every
edge carries its explanation/provenance**. No opaque vector soup: the agent can
answer *who calls this? what breaks if I change it?* exactly." This module is
that deterministic AST pass — no embeddings, no model, no server — plus the
incremental merge §28.1 promises (re-extract only changed files, and a **shrink
guard** that refuses to drop nodes for files that were not scanned).

Four decisions worth their comments:

* **The spans are the patch arm's spans.** Nodes come from
  `flash.patches.definitions`, so the range `flash graph` reports for
  `Cart.total_cents` is literally the range `# edit: cart.py :: Cart.total_cents`
  would replace — decorators included. Two coordinate systems for one symbol
  would make the blast-radius answer wrong about a line and the edit answer wrong
  about the same line, and both would look fine.
* **Every edge says how it was resolved** (`own-scope`, `enclosing-class`,
  `import-alias`, `qualified-name`, `inherited`, `unique-attribute`). The last
  two are the rules that can guess: Python gives no type oracle at a call site,
  so a `self.touch()` only a base class defines, and an `l.total_cents` whose
  name matches exactly one symbol repo-wide, are labelled on the edge instead of
  presented as fact. A visible wrong edge beats an invisible missing one.
* **Unresolvable uses are recorded, not dropped.** `Unresolved` carries the
  expression and the reason, and `Radius.summary()` prints the count, so an
  answer states its own floor ("N symbols reach it, and M uses I could not
  place") rather than reading as a census.
* **Import edges are their own kind.** "Who pulls this file in" is a different
  question from "who calls this function"; merging them would over-report
  breakage by the repo's fan-in. They are computed, returned and printed apart.

The LSP stays the live truth (R-1.2): `live_upgrade()` asks
`flash.lsp.find_references` to settle what this pass could not place, and reports
how many it settled. The < 200 ms clause is measured on the deterministic pass,
because a language-server round trip is not a bound anyone can hold on a cold
index — that would be a timing claim about a process nobody has started yet.

§28.2 step 3 — feeding a subgraph into the loop's context — lives in
`scope_hint()` at the bottom of this file. It answers "who reaches the symbol
this failure turns on", and it borrows `flash.lsp.symbols_involved` to decide
which symbols those are, so the source hint and this one cannot name different
symbols for one error. The query path (`blast`, `render`) still imports stdlib
and `flash.patches` only; `flash.lsp` is imported lazily by `--live` and by
`scope_targets`, which is the only entry point here that needs a second module.
"""
from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from flash.patches import definitions

# Names that are never repo symbols. A graph with an unresolved edge for every
# `len()` buries the ones that matter, and `len` is not a thing this repo changes.
_BUILTINS = frozenset(dir(builtins)) | {
    "__name__", "__file__", "__doc__", "__dict__", "__all__", "__spec__",
    "__class__", "__self__", "annotations",
}

MAX_TEXT = 88            # an edge quotes its call site: one line, clipped
DEFAULT_DEPTH = 3
# SPEC R-1.3's clause, on the repo that clause names. Note how thin that repo is
# (five files) — the selftest says so and times a wide instrument beside it.
BUDGET_MS = 200.0
SKIP_DIRS = {"__pycache__", "venv", ".venv", "build", ".git", "node_modules",
             ".flash", "site-packages"}


def _now_ms() -> float:
    return time.perf_counter() * 1000.0


_STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))


def _external(dst: str) -> bool:
    """True when an unplaced edge is only a stdlib call leaving the repo.

    Import-shaped placeholders only: a relative import can never name a stdlib
    module, and a guess like `?unique#x` is not a boundary.
    """
    if not dst.startswith(("@", "#")):
        return False
    lvl_s, _, tail = dst[1:].partition("|")
    if lvl_s.isdigit() and int(lvl_s) > 0:
        return False
    return tail.partition("|")[0].split(".")[0] in _STDLIB


def _attr_target(dst: str, by_attr: dict[str, list[str]]) -> str:
    """The node an attribute guess names — or "" when the repo will not say.

    A function of its own because this is the one place the pass decides whether
    to trust a name match: exactly one candidate is evidence, two is a coin flip,
    and zero means the attribute belongs to something outside this repo.
    """
    hits = by_attr.get(dst.split("#", 1)[1], ())
    return hits[0] if len(hits) == 1 else ""


# ------------------------------------------------------------------- records

@dataclass(frozen=True)
class Node:
    """One addressable definition. `id` is the patch arm's own address form."""
    file: str
    symbol: str          # "Cart.total_cents"; "" for a module node
    kind: str            # module|class|function|method|property|async|constant
    start: int
    end: int

    @property
    def id(self) -> str:
        return f"{self.file}::{self.symbol}"


@dataclass(frozen=True)
class Edge:
    src: str             # the affected side (caller / importer)
    dst: str             # the changed side; a placeholder until the repo is bound
    kind: str            # calls|reads|imports
    file: str
    line: int
    text: str            # the source line, clipped — the explanation's evidence
    via: str             # which resolution rule produced this edge


@dataclass(frozen=True)
class Unresolved:
    file: str
    line: int
    expr: str
    reason: str


@dataclass
class Hit:
    node: Node
    depth: int
    edge: Edge | None    # the edge that connects it to the changed symbol
    path: tuple[str, ...] = ()


@dataclass
class Radius:
    """The blast radius of one symbol: who reaches it, and how."""
    target: Node | None
    hits: list[Hit] = field(default_factory=list)
    importers: list[Edge] = field(default_factory=list)
    blind_spots: int = 0
    external: int = 0

    def names(self) -> list[str]:
        return [h.node.id for h in self.hits]

    def summary(self) -> str:
        if self.target is None:
            return "symbol not in the graph"
        by_kind: dict[str, int] = {}
        for h in self.hits:
            k = h.edge.kind if h.edge else "?"
            by_kind[k] = by_kind.get(k, 0) + 1
        bits = ", ".join(f"{n} {k}" for k, n in sorted(by_kind.items()))
        return (f"{self.target.file}::{self.target.symbol}: "
                f"{len(self.hits)} symbol(s) reach it ({bits or 'nobody'}), "
                f"{len(self.importers)} file(s) import it, "
                f"{self.blind_spots} blind spot(s) in the repo "
                f"({self.external} stdlib edge(s) leave by design)")


# --------------------------------------------------------------- extraction

def _clip(line: str) -> str:
    t = line.strip()
    return t if len(t) <= MAX_TEXT else t[:MAX_TEXT - 1] + "…"


def _join(container: str, name: str) -> str:
    return f"{container}.{name}" if container else name


def _attr_chain(node: ast.AST) -> list[str]:
    """`a.b.c` -> ["a","b","c"]; a bare Name -> ["a"]; anything else -> []."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        parts.reverse()
    return parts


def _kinds(tree: ast.AST) -> dict[str, str]:
    """symbol -> kind, from one AST walk.

    `definitions()` owns the SPANS so the two cannot disagree; this owns only
    the vocabulary, which the patch arm has no use for. The container convention
    is shared with it deliberately: a method's container is its class, a nested
    function's is its enclosing class too, so one symbol string names one thing
    in both modules.
    """
    out: dict[str, str] = {}

    def walk(node: ast.AST, container: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                out[_join(container, child.name)] = "class"
                walk(child, child.name)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if container:
                    dec = {getattr(d, "id", getattr(d, "attr", ""))
                           for d in child.decorator_list}
                    out[_join(container, child.name)] = (
                        "property" if dec & {"property", "cached_property"}
                        else "method")
                else:
                    out[child.name] = ("async" if isinstance(
                        child, ast.AsyncFunctionDef) else "function")
                walk(child, container)
            else:
                walk(child, container)

    walk(tree, "")
    return out


@dataclass(frozen=True)
class Bound:
    """What one imported name means: a module, or a symbol inside one."""
    level: int           # the leading-dot count of `from ..x import y`
    module: str          # the dotted module as written ("" for `from . import y`)
    symbol: str          # "" when the name binds the MODULE itself


def _bindings(tree: ast.AST, rel: str, module_id: str,
              lines: list[str]) -> tuple[list[Edge], dict[str, Bound]]:
    """The file→file import edges AND the bound-name table, from one reading.

    Both come from the same walk on purpose: an answer where "who imports this"
    and "what does the name `tax` mean" disagree would be two indexes pretending
    to be one.
    """
    edges: list[Edge] = []
    table: dict[str, Bound] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                head = (a.asname or a.name).split(".")[0]
                table[head] = Bound(0, a.name, "")
                edges.append(Edge(module_id, f"@0|{a.name}", "imports", rel,
                                  node.lineno, _clip(lines[node.lineno - 1]),
                                  "import-alias"))
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            lvl = node.level or 0
            for a in node.names:
                table[a.asname or a.name] = Bound(lvl, mod, a.name)
            edges.append(Edge(module_id, f"@{lvl}|{mod}", "imports", rel,
                              node.lineno, _clip(lines[node.lineno - 1]),
                              "import-alias"))
    return edges, table


# ------------------------------------------------- names a scope binds itself

def _binds(node: ast.AST, out: set[str]) -> None:
    """Every name one subtree binds: targets, parameters, aliases, handlers."""
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)


def _locals(tree: ast.AST) -> tuple[dict[str, set[str]], set[str]]:
    """Per-definition local names, plus the module's own top-level bindings.

    A name a scope binds is not a repo symbol, so it gets no edge and — just as
    important — no blind spot. Without this, every parameter and loop variable in
    the repo is an `Unresolved`, the count drowns the ones that mean something,
    and `live_upgrade` would spend a language-server query on `x`. The sets fold
    inward: a method sees its class body's names, a module sees only names bound
    at module level (a function's locals are not visible out here, and treating
    them as if they were would drop real blind spots repo-wide).
    """
    by_def: dict[str, set[str]] = {}
    top: set[str] = set()

    def walk(node: ast.AST, container: str, key: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                sym = _join(container, child.name)
                cls = by_def.setdefault(sym, set())
                for base in child.bases:
                    _binds(base, cls)
                for kw in child.keywords:
                    _binds(kw, cls)
                walk(child, sym, sym)
                continue
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sym = _join(container, child.name)
                s = by_def.setdefault(sym, set())
                _binds(child.args, s)
                for st in child.body:
                    _binds(st, s)
                walk(child, container, sym)
                continue
            _binds(child, top if not key else by_def.setdefault(key, set()))
            walk(child, container, key)

    walk(tree, "", "")
    for cls in [k for k in by_def if "." in k]:
        # a method may read a name its class body bound (`LIMIT = 5`, then
        # `def rows(self): ... LIMIT`)
        by_def[cls].update(by_def.get(cls.rpartition(".")[0], ()))
    return by_def, top


def extract(rel: str, src: str) -> tuple[list[Node], list[Edge], list[Unresolved]]:
    """One file's nodes, its call/read/import edges, and what it could not place.

    `rel` is the repo-relative path with forward slashes; it is also the first
    half of every node id, so an id survives moving the repo root.
    """
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        return ([Node(rel, "", "module", 1, src.count("\n") + 1)], [],
                [Unresolved(rel, 1, "<file>",
                            f"does not parse: {type(exc).__name__}")])
    lines = src.split("\n")
    module_id = f"{rel}::"
    kinds = _kinds(tree)
    nodes: list[Node] = [Node(rel, "", "module", 1, len(lines))]
    own: dict[str, str] = {}            # qualified and short name -> node id
    for d in definitions(src):
        sym = _join(d.container, d.name)
        nodes.append(Node(rel, sym, kinds.get(sym, "constant"), d.start, d.end))
        own[sym] = f"{rel}::{sym}"
        # A call site says `total_cents` and an address says `Cart.total_cents`;
        # both name the one definition, so both index to it. The qualified form
        # is written first, so a short name can never shadow it.
        own.setdefault(d.name, f"{rel}::{sym}")
    imp_edges, bound = _bindings(tree, rel, module_id, lines)
    edges: list[Edge] = list(imp_edges)
    unres: list[Unresolved] = []
    by_def, top_locals = _locals(tree)

    def dst_for(b: Bound, attr: str = "") -> str:
        sym = attr or b.symbol
        return f"#{b.level}|{b.module}|{sym}"

    def emit(caller: str, use: ast.AST, chain: list[str]) -> None:
        """One use of a dotted name: an edge, or a recorded blind spot."""
        if not chain:
            return
        line_no = getattr(use, "lineno", 1)
        text = _clip(lines[line_no - 1]) if line_no - 1 < len(lines) else ""
        kind = "calls" if isinstance(use, ast.Call) else "reads"
        head, attr = chain[0], chain[-1]
        if len(chain) == 1:
            if head in _BUILTINS or head in {"self", "cls"}:
                return
            if head in own:
                edges.append(Edge(caller, own[head], kind, rel, line_no, text,
                                  "own-scope"))
                return
            if head in bound:
                edges.append(Edge(caller, dst_for(bound[head]), kind, rel,
                                  line_no, text, "import-alias"))
                return
            locals_ = top_locals if caller == module_id \
                else by_def.get(caller.partition("::")[2], ())
            if head in locals_:
                return
            unres.append(Unresolved(rel, line_no, head,
                                    f"`{head}` is neither defined nor imported "
                                    f"in this file"))
            return
        container = caller.partition("::")[2]
        if head in {"self", "cls"} and container:
            cls = container.rpartition(".")[0]
            if cls and _join(cls, attr) in own:
                edges.append(Edge(caller, own[_join(cls, attr)], kind, rel,
                                  line_no, text, "enclosing-class"))
                return
            edges.append(Edge(caller, f"~inherit#{attr}", kind, rel, line_no,
                              text, "inherited"))
            return
        if head in bound and not bound[head].symbol:
            # `os.path.join(...)`: the head binds a module, so the attr is a
            # symbol inside it.
            edges.append(Edge(caller, dst_for(bound[head], attr), kind, rel,
                              line_no, text, "qualified-name"))
            return
        edges.append(Edge(caller, f"?unique#{attr}", kind, rel, line_no, text,
                          "unique-attribute"))

    def expr(node: ast.AST, caller: str) -> None:
        """Walk one subtree, emitting a use per name or attribute chain.

        An attribute chain is ONE use: `a.b.c` reads `c` of whatever `a.b` is, and
        `a` and `b` are not repo symbols unless `a` is a module alias.
        """
        if isinstance(node, ast.Call):
            chain = _attr_chain(node.func)
            if chain:
                emit(caller, node, chain)          # the Call IS the use
                if len(chain) > 1 and chain[0] in bound:
                    expr_chain_base(node.func, caller)
            else:
                expr(node.func, caller)            # foo()(0), or a lambda call
            for ch in list(node.args) + list(node.keywords):
                expr(ch, caller)
            return
        if isinstance(node, ast.Attribute):
            emit(caller, node, _attr_chain(node))
            base = node.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if isinstance(base, ast.Name) and base.id in bound:
                pass                              # a module alias, not a symbol
            else:
                expr(base, caller)                 # obj in obj.m(): obj may be a call
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                emit(caller, node, [node.id])
            return
        for ch in ast.iter_child_nodes(node):
            expr(ch, caller)

    def expr_chain_base(node: ast.AST, caller: str) -> None:
        """The object an attribute chain hangs off, when it is itself an expression."""
        while isinstance(node, ast.Attribute):
            node = node.value
        if isinstance(node, ast.Call):
            expr(node, caller)

    def walk(node: ast.AST, caller: str, container: str) -> None:
        """Emit every use, attributed to the definition that owns it.

        A class's bases, decorators and class-level statements belong to the
        MODULE scope, not to the class: `class Widget(Thing)` is a use of `Thing`
        made at import time, and `Cart.total` is not one.
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                is_class = isinstance(child, ast.ClassDef)
                sym = _join(container, child.name)
                for dec in child.decorator_list:
                    expr(dec, caller)
                if is_class:
                    for base in child.bases:
                        expr(base, module_id)
                    for kw in child.keywords:
                        expr(kw, module_id)
                walk(child, module_id if is_class else f"{rel}::{sym}",
                     child.name if is_class else container)
                continue
            expr(child, caller)

    walk(tree, module_id, "")
    return nodes, edges, unres


# ------------------------------------------------------------------ the graph

@dataclass
class Graph:
    nodes: dict[str, Node]
    edges: list[Edge]
    unresolved: list[Unresolved]
    hashes: dict[str, str] = field(default_factory=dict)      # file -> sha1
    stats: dict[str, float] = field(default_factory=dict)

    def get(self, needle: str) -> list[Node]:
        """Nodes an address names: `file::Sym`, `Sym`, `Container.name`, or the
        `file::` module. An ambiguous needle returns them ALL — choosing for the
        user is not this module's call."""
        if "::" in needle:
            f, _, sym = needle.partition("::")
            return [n for n in self.nodes.values()
                    if n.file == f and (not sym or n.symbol == sym
                                        or n.symbol.endswith("." + sym))]
        return [n for n in self.nodes.values()
                if n.symbol and (n.symbol == needle
                                 or n.symbol.endswith("." + needle))]

    # ---- binding: placeholders become node ids once the whole repo is in hand

    def _cache(self) -> dict:
        """Repo-wide indexes, built once per Graph.

        `modules()`, `per_file` and the import binding are each O(nodes); called
        per edge they made a query on the wide repo take seconds, and the
        < 200 ms clause is about a QUERY, not a rebuild. Nothing mutates a Graph
        in place — `merge()` returns a new one, which is why these cannot go
        stale under a caller.
        """
        return self.__dict__.setdefault("_ix", {})

    def modules(self) -> dict[str, str]:
        """dotted module name -> repo file, for every file in the graph."""
        c = self._cache()
        if "modules" not in c:
            out: dict[str, str] = {}
            for n in self.nodes.values():
                if n.kind != "module" or not n.file.endswith(".py"):
                    continue
                parts = n.file[:-3].split("/")
                if parts[-1] == "__init__":
                    parts.pop()
                out[".".join(parts)] = n.file
            c["modules"] = out
        return c["modules"]

    def _per_file(self) -> dict[str, list[Node]]:
        c = self._cache()
        if "per_file" not in c:
            by: dict[str, list[Node]] = {}
            for n in self.nodes.values():
                by.setdefault(n.file, []).append(n)
            c["per_file"] = by
        return c["per_file"]

    def _place(self, level: int, mod: str, from_file: str) -> str:
        """Which repo file a written import names, or "" if none does."""
        c = self._cache()
        key = (level, mod, from_file)
        if key in c:
            return c[key]
        c[key] = placed = self._place_uncached(level, mod, from_file)
        return placed

    def _place_uncached(self, level: int, mod: str, from_file: str) -> str:
        idx = self.modules()
        mod = mod.lstrip(".")
        if level:
            pkg = from_file.rpartition("/")[0]
            for _ in range(max(level - 1, 0)):
                pkg = pkg.rpartition("/")[0]
            cand = f"{pkg}.{mod}" if pkg and mod else (mod or pkg)
            if cand in idx:
                return idx[cand]
        if mod in idx:
            return idx[mod]
        hits = [m for m in idx if mod and m.endswith("." + mod)]
        if len(hits) == 1:
            return idx[hits[0]]
        return ""

    def _bind(self, dst: str, from_file: str) -> str:
        """A placeholder target -> a node id, or "" when nothing places it."""
        if dst in self.nodes:
            return dst
        if dst.startswith("@"):
            lvl, _, mod = dst[1:].partition("|")
            f = self._place(int(lvl or 0), mod, from_file)
            return f"{f}::" if f else ""
        if not dst.startswith("#"):
            return ""
        lvl_s, _, tail = dst[1:].partition("|")
        mod, _, sym = tail.partition("|")
        f = self._place(int(lvl_s or 0), mod, from_file)
        if not f:
            return ""
        if not sym:
            return f"{f}::"
        want = f"{f}::{sym}"
        if want in self.nodes:
            return want
        # `from pkg import Thing` where Thing is a class in pkg/__init__.py, or a
        # method reached through a module alias: one match inside that file wins.
        hits = [n for n in self._per_file().get(f, ())
                if n.symbol.endswith("." + sym)]
        return hits[0].id if len(hits) == 1 else ""

    def resolved(self) -> list[Edge]:
        """Every edge whose far end names exactly one thing in this repo.

        Two rules bind the placeholders, and each keeps the `via` label it was
        written with so a reader can discount a guess:
        * `#level|module|symbol` — the import table, resolved repo-wide.
        * `?unique#attr` / `~inherit#attr` — an attribute use, and a `self.x` the
          class does not define, both bound only when EXACTLY ONE non-class symbol
          in the repo carries that name. The label then says "a base class,
          probably", which is the honest claim for a type-free pass.

        Every edge lands in exactly one of three buckets — bound, `unbound()`,
        `external()` — because the third is a boundary and the second is a hole,
        and an answer that conflates them either buries the holes in `json.dumps`
        or hides them behind a tidy zero.
        """
        c = self._cache()
        if "resolved" in c:
            return c["resolved"]
        by_attr: dict[str, list[str]] = {}
        for n in self.nodes.values():
            if n.symbol and n.kind != "class":
                by_attr.setdefault(n.symbol.rpartition(".")[2], []).append(n.id)
        out: list[Edge] = []
        drop: list[Edge] = []
        outside: list[Edge] = []
        for e in self.edges:
            dst = e.dst
            if dst.startswith("?unique#") or dst.startswith("~inherit#"):
                # An attribute use, where the name is the only evidence: the
                # repo's own symbol table decides what kind of fact this is.
                name = dst.split("#", 1)[1]
                target = _attr_target(dst, by_attr)
                if target:
                    out.append(replace(e, dst=target))
                elif name in by_attr:
                    drop.append(e)          # 2+ candidates: refusing is honest
                else:
                    outside.append(e)       # no repo symbol names that at all
                continue
            bound = self._bind(dst, e.file)
            if bound:
                out.append(replace(e, dst=bound))
            elif _external(dst):
                outside.append(e)
            else:
                drop.append(e)
        c["resolved"] = out
        c["unbound"] = drop
        c["outside"] = outside
        return out

    def unbound(self) -> list[Edge]:
        """Edges whose far end this repo could not place: an attribute name that
        matches TWO symbols, a relative import whose target was not scanned, a
        `self.x` no symbol defines.

        The alternative — filtering them out and saying nothing — makes an answer
        look both smaller and cleaner than the truth, so they are kept and
        `Radius.summary()` counts them beside the parse failures. A `?unique#`
        drop is the interesting case: name-matching found more than one candidate,
        which is exactly when a guess is not evidence.
        """
        self.resolved()
        return self._cache()["unbound"]

    def external(self) -> list[Edge]:
        """Edges that reach nothing in this repo: a stdlib import, or `x.append()`
        on an object no repo symbol names. A boundary, not a hole.

        Kept apart from `unbound()` because counting them as holes would bury the
        ones that mean something — the wide instrument has thousands — and kept
        at all because that split is a judgement THIS pass makes, so a reader is
        entitled to see it. A third-party import lands here only if its top-level
        name is stdlib: this pass cannot tell an uninstalled package from an
        unscanned file, so anything else stays in `unbound()`, which over-reports
        holes rather than hiding them.
        """
        self.resolved()
        return self._cache()["outside"]

    def blind_spots(self) -> int:
        """Uses this pass saw and could not turn into an edge, either kind."""
        return len(self.unresolved) + len(self.unbound())

    def _reverse(self) -> dict[str, list[Edge]]:
        """dst -> the edges pointing at it, so a walk costs hops not edges."""
        c = self._cache()
        if "rev" not in c:
            rev: dict[str, list[Edge]] = {}
            for e in self.resolved():
                rev.setdefault(e.dst, []).append(e)
            c["rev"] = rev
        return c["rev"]

    def callers_of(self, node_id: str) -> list[Edge]:
        """The edges that point at this node, from the cached reverse index.

        Scanning the edge list per hop instead is O(edges) per hop. Measured on
        the wide instrument — one hot symbol with 4800 direct callers, 5377 nodes
        and 10058 edges — the reverse index answers in 30-101 ms cold and 3-9 ms
        once warm, while the scan takes 443-943 ms. Those ranges are wide because
        this box was not quiet during the runs; the ratio, roughly fifteen-fold,
        held every time. The index is therefore what makes the clause's budget
        reachable at all, and `--mutants` keeps that claim honest by putting the
        scan back and requiring the budget check to notice.
        """
        return self._reverse().get(node_id, [])

    def blast(self, needle: str, depth: int = DEFAULT_DEPTH) -> Radius:
        """Every symbol that can reach this one, with the edge that proves it.

        Breadth-first over reverse edges, so `depth` counts hops from the changed
        symbol and a cycle cannot hang the walk: a symbol is entered once, at its
        shortest depth, and never re-entred.
        """
        targets = self.get(needle)
        if not targets:
            return Radius(None, blind_spots=self.blind_spots())
        best: dict[str, Hit] = {}
        frontier = {n.id: () for n in targets}
        seen = set(frontier)
        for d in range(1, depth + 1):
            nxt: dict[str, tuple[str, ...]] = {}
            for nid, path in frontier.items():
                for e in self.callers_of(nid):
                    if e.src in seen or e.src not in self.nodes:
                        continue
                    hop = path + (f"{e.file}:{e.line} {e.text}",)
                    best[e.src] = Hit(self.nodes[e.src], d, e, hop)
                    nxt[e.src] = hop
                    seen.add(e.src)
            frontier = nxt
            if not frontier:
                break
        ids = {n.id for n in targets}
        importers = [e for e in self.resolved()
                     if e.kind == "imports" and e.dst in ids]
        return Radius(targets[0],
                      sorted(best.values(), key=lambda h: (h.depth, h.node.id)),
                      importers, self.blind_spots(), len(self.external()))

    def to_json(self) -> str:
        return json.dumps({
            "nodes": [dict(n.__dict__, id=n.id) for n in self.nodes.values()],
            "edges": [e.__dict__ for e in self.edges],
            "unresolved": [u.__dict__ for u in self.unresolved],
            "hashes": self.hashes,
            "stats": self.stats,
        }, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Graph":
        raw = json.loads(text)
        nodes = {d["id"]: Node(d["file"], d["symbol"], d["kind"], d["start"],
                               d["end"]) for d in raw["nodes"]}
        return Graph(nodes, [Edge(**e) for e in raw["edges"]],
                     [Unresolved(**u) for u in raw["unresolved"]],
                     raw.get("hashes", {}), raw.get("stats", {}))


# ---------------------------------------------------------- scan and merge

def python_files(root: str | Path) -> list[Path]:
    root = Path(root)
    out = []
    for p in sorted(root.rglob("*.py")):
        if set(p.relative_to(root).parts[:-1]) & SKIP_DIRS:
            continue
        out.append(p)
    return out


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _without(rel: str, nodes: dict, edges: list, unres: list):
    prefix = f"{rel}::"
    return ({k: v for k, v in nodes.items() if not k.startswith(prefix)},
            [e for e in edges if e.file != rel and not e.src.startswith(prefix)],
            [u for u in unres if u.file != rel])


def build(root: str | Path) -> Graph:
    """The cold path: every file, one deterministic pass, no server."""
    root = Path(root)
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    unres: list[Unresolved] = []
    hashes: dict[str, str] = {}
    t0 = _now_ms()
    files = python_files(root)
    for p in files:
        rel = str(p.relative_to(root)).replace("\\", "/")
        src = _read(p)
        if src is None:
            continue
        ns, es, us = extract(rel, src)
        for n in ns:
            nodes[n.id] = n
        edges += es
        unres += us
        hashes[rel] = hashlib.sha1(src.encode()).hexdigest()
    return Graph(nodes, edges, unres, hashes,
                 {"files": len(files), "cold_ms": round(_now_ms() - t0, 1)})


def merge(old: Graph, root: str | Path, prune: bool = False) -> tuple[Graph, dict]:
    """§28.1's incremental merge: re-extract only what changed.

    The **shrink guard** is the part that is easy to get wrong and expensive to
    lose. A file in the store but absent from this scan — unreadable, excluded,
    deleted, or simply outside the directory handed in — keeps its nodes and
    edges. Dropping a subtree because one scan saw fewer files would silently
    shrink every future blast-radius answer, and the agent would have no way to
    learn the graph had holes in it. `prune=True` is the one explicit exception,
    and the report says how many nodes it removed.
    """
    root = Path(root)
    nodes, edges = dict(old.nodes), list(old.edges)
    unres, hashes = list(old.unresolved), dict(old.hashes)
    rep = {"reused": 0, "re-extracted": 0, "added": 0, "removed": 0,
           "kept-by-shrink-guard": 0, "skipped-unreadable": 0, "ms": 0.0}
    t0 = _now_ms()
    seen: set[str] = set()
    for p in python_files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        seen.add(rel)
        src = _read(p)
        if src is None:
            rep["skipped-unreadable"] += 1
            continue
        digest = hashlib.sha1(src.encode()).hexdigest()
        if hashes.get(rel) == digest:
            rep["reused"] += 1
            continue
        rep["re-extracted" if rel in hashes else "added"] += 1
        nodes, edges, unres = _without(rel, nodes, edges, unres)
        ns, es, us = extract(rel, src)
        for n in ns:
            nodes[n.id] = n
        edges += es
        unres += us
        hashes[rel] = digest
    if prune:
        for rel in [r for r in hashes if r not in seen]:
            before = len(nodes)
            nodes, edges, unres = _without(rel, nodes, edges, unres)
            rep["removed"] += before - len(nodes)
            del hashes[rel]
    else:
        rep["kept-by-shrink-guard"] = sum(1 for r in hashes if r not in seen)
    rep["ms"] = round(_now_ms() - t0, 1)
    return Graph(nodes, edges, unres, hashes,
                 old.stats | {"merge_ms": rep["ms"]}), rep


# ------------------------------------------------------------------- querying

def nearest(g: Graph, needle: str, limit: int = 5) -> list[str]:
    """The addresses closest to a needle this pass could not place.

    A substring test on the last dotted component rather than a fuzzy scorer: the
    point is that a mistyped symbol costs the caller one more look, so the answer
    has to say which names it DID have. The empty-name guard is load-bearing —
    `"Cart."` reduces to `""`, which is a substring of every symbol, and offering
    five unrelated nodes for it would be the mirror image of the failure this
    function exists to avoid.
    """
    short = needle.rpartition(".")[2]
    if not short:
        return []
    return [n.id for n in g.nodes.values() if short in n.symbol][:limit]


def render(root: str | Path, needle: str, depth: int = DEFAULT_DEPTH,
           g: Graph | None = None) -> tuple[str, Radius, float]:
    """The answer, its reachability record, and the milliseconds it took."""
    g = g or build(root)
    t0 = _now_ms()
    r = g.blast(needle, depth)
    ms = _now_ms() - t0
    lines = [r.summary()]
    if r.target is None:
        near = nearest(g, needle)
        if near:
            lines.append("  no node named that; nearest addresses: "
                         + ", ".join(near))
        return "\n".join(lines), r, ms
    for h in r.hits:
        e = h.edge
        lines.append(f"  d{h.depth} {h.node.id:<38} {h.node.kind:<9} "
                     f"{e.kind:>7s} it   at {e.file}:{e.line}  via {e.via}")
        lines.append(f"      | {e.text}")
    for e in r.importers[:12]:
        lines.append(f"  --  {e.src:<38} imports it at {e.file}:{e.line}")
    if r.blind_spots:
        lines.append(f"  ?   {r.blind_spots} use(s) this pass saw but could not "
                     f"place — the count above is a floor, not a census")
    return "\n".join(lines), r, ms


def live_upgrade(root: str | Path, g: Graph, limit: int = 25) -> dict:
    """Ask the language server to settle what the AST pass could not (R-1.2).

    Both blind-spot kinds go to the server: a bare name `extract` could not place,
    and an attribute edge `resolved` refused to bind because two symbols share the
    name. A type-aware server resolves in one second what a deterministic pass
    never can, so refusing to guess and then asking is better than either guessing
    or stopping.

    Deliberately off the < 200 ms path: this is the power option, not the default,
    and the report says how many names it settled, so a caller can see how much of
    its answer came from a server that had to start up and index first.
    """
    from flash.lsp import find_references
    names: list[str] = []
    for u in g.unresolved:
        short = u.expr.rpartition(".")[2]
        if not short.startswith("<"):
            names.append(short)
    names += [e.dst.split("#", 1)[1] for e in g.unbound()]
    asked = settled = 0
    hits: list[dict] = []
    for name in dict.fromkeys(names):             # dedupe, first seen wins
        if asked >= limit:
            break
        asked += 1
        try:
            found = find_references(root, name)
        except Exception:                          # a dead server is a result
            return {"asked": asked, "settled": settled, "hits": hits,
                    "names_not_asked": len(dict.fromkeys(names)) - asked,
                    "why": "no language server answered"}
        if found:
            settled += 1
            hits.append({"name": name, "refs": len(found),
                         "files": sorted({f.path for f in found})[:4]})
    return {"asked": asked, "settled": settled, "hits": hits,
            "names_not_asked": max(len(dict.fromkeys(names)) - asked, 0),
            "why": "find_references answered for each name in `hits`"}


# ------------------------------------------- §28.2 step 3: graph -> PERCEIVE

SCOPE_LIMIT = 3          # symbols at issue — same cap `lsp.symbol_hint` uses
SCOPE_DEPTH = 2          # not DEFAULT_DEPTH: §28.2's own words are "small context"
SCOPE_HITS = 6           # per symbol; a hub with 400 callers is not a hint
SCOPE_MAX_CHARS = 900
SCOPE_CACHE = 8          # repos one process may hold a graph for

SCOPE_HEADER = ("Dependents of the symbols at issue (AST call graph — these call "
                "sites break if you change them):")

_scope_cache: dict[str, Graph] = {}


def scope_graph(root: str | Path) -> Graph:
    """This process's graph for `root`: cold build once, `merge` after.

    §28.1's incremental merge exists for exactly this caller. Injecting context
    that is a hour stale would be worse than injecting none — the agent would be
    told a caller exists that has since been deleted — so every reuse runs the
    merge, which re-extracts only the files whose content hash moved. The first
    call pays the scan and every later one pays a stat-and-hash pass.
    """
    key = str(Path(root).resolve())
    g = _scope_cache.pop(key, None)
    if g is None and len(_scope_cache) >= SCOPE_CACHE:
        # Reinserting below keeps the dict in least-recently-used order, so this
        # drops the repo nobody has asked about lately and never the one in hand.
        del _scope_cache[next(iter(_scope_cache))]
    _scope_cache[key] = g = merge(g, key)[0] if g is not None else build(key)
    return g


def _rel(root: str | Path, path: Path) -> str:
    """A node id's file half, from an absolute path the AST index produced."""
    try:
        return str(Path(path).resolve().relative_to(
            Path(root).resolve())).replace("\\", "/")
    except (OSError, ValueError):
        return Path(path).name


def _one_node(g: Graph, root: str | Path, sym) -> Node | None:
    """The node one at-issue symbol names — or None when the graph will not say.

    This is `resolved()`'s rule applied to the injection: one candidate is
    evidence, two are a coin flip, and a hint that blames the wrong symbol's
    callers is worse than no hint, because the model has no way to see which it
    got. The file the AST index read is the tie-breaker, which is why
    `symbols_involved` hands back paths and not just names.
    """
    qual = f"{sym.container}.{sym.name}" if sym.container else sym.name
    cands = g.get(qual)
    if len(cands) > 1:
        rel = _rel(root, sym.path)
        cands = [n for n in cands if n.file == rel]
    return cands[0] if len(cands) == 1 else None


def scope_targets(root: str | Path, err: str = "", code: str = "",
                  limit: int = SCOPE_LIMIT, g: Graph | None = None,
                  index=None) -> list[Node]:
    """The nodes for the symbols a failure actually turns on.

    The ranking is imported from `flash.lsp.symbols_involved` on purpose. Two
    private notions of "what is at issue" would have the source hint and this one
    name different symbols for the same error, and the model would read two
    stories about one failure. `index` is shared with the source hint for the
    same reason: one parse, one ranking, one cost.
    """
    from flash.lsp import SymbolIndex, symbols_involved
    g = g or scope_graph(root)
    index = index if index is not None else SymbolIndex.build(root)
    out: list[Node] = []
    for s in symbols_involved(index, err, code, limit=limit):
        n = _one_node(g, root, s)
        if n is not None and not any(n.id == x.id for x in out):
            out.append(n)
    return out


def scope_hint(root: str | Path, err: str = "", code: str = "",
               limit: int = SCOPE_LIMIT, depth: int = SCOPE_DEPTH,
               max_chars: int = SCOPE_MAX_CHARS, index=None) -> str:
    """Feedback block: who reaches the symbols at issue, and from where.

    Silent when nothing repo-defined is at issue or the graph places none of it,
    which is the same contract `lsp.symbol_hint` has — a retry that gains nothing
    must not pay for a header. `index` is the shared symbol ranking; see
    `scope_targets`.
    """
    g = scope_graph(root)
    nodes = scope_targets(root, err, code, limit=limit, g=g, index=index)
    if not nodes:
        return ""
    blocks: list[tuple[str, list[str], int]] = []
    for n in nodes:
        rad = g.blast(n.id, depth)
        head = (f"  {n.symbol} [{n.kind}] {n.file}:{n.start} — "
                f"{len(rad.hits)} symbol(s) reach it"
                + (f", {len(rad.importers)} file(s) import it"
                   if rad.importers else ""))
        details = [f"      d{h.depth} {h.node.id} {h.edge.kind} it: "
                   f"{h.edge.text}  [{h.edge.file}:{h.edge.line} "
                   f"via {h.edge.via}]" for h in rad.hits[:SCOPE_HITS]]
        details += [f"      -- {e.src} imports it at {e.file}:{e.line}"
                    for e in rad.importers[:2]]
        blocks.append((head, details, max(len(rad.hits) - SCOPE_HITS, 0)))
    lines = [SCOPE_HEADER]
    used = len(SCOPE_HEADER) + 1
    unsaid = 0
    for head, details, hidden in blocks:
        if used + len(head) + 1 > max_chars:
            unsaid += 1
            continue
        lines.append(head)
        used += len(head) + 1
        cut = hidden
        for i, line in enumerate(details):
            if used + len(line) + 1 > max_chars:
                cut += len(details) - i
                break
            lines.append(line)
            used += len(line) + 1
        if cut:
            note = f"      … {cut} more not shown (context budget)"
            if used + len(note) + 1 <= max_chars:
                lines.append(note)
                used += len(note) + 1
    if unsaid and len(lines) > 1:
        lines.append(f"      … {unsaid} symbol(s) at issue left out "
                     f"(context budget)")
    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------- selftest

_FIXTURES = (Path(__file__).resolve().parent.parent
             / "benchmarks" / "fixtures" / "minishop")

_SHOP = {
    "shop/__init__.py": "",
    "shop/pricing.py": (
        '"""Rules."""\n'
        'TAX_PCT = 10\n\n\n'
        'def tax(cents):\n'
        '    return cents * TAX_PCT // 100\n\n\n'
        'def gross(cents):\n'
        '    return cents + tax(cents)\n'
    ),
    "shop/cart.py": (
        '"""Cart."""\n'
        'import json\n'
        'from pricing import gross, tax\n'
        'from fmt import money\n\n\n'
        'class Cart:\n'
        '    def __init__(self):\n'
        '        self.items = []\n\n'
        '    def add(self, n):\n'
        '        self.items.append(n)\n'
        '        self.recount()\n\n'
        '    def recount(self):\n'
        '        self._stamp = 1\n\n'
        '    def as_json(self):\n'
        '        return json.dumps(self.items)\n\n'
        '    def total(self):\n'
        '        return gross(sum(self.items))\n\n'
        '    @property\n'
        '    def pretty(self):\n'
        '        return money(self.total())\n'
    ),
    "shop/base.py": (
        '"""Base."""\n'
        'class Thing:\n'
        '    def touch(self):\n'
        '        return 1\n\n\n'
        'class Widget(Thing):\n'
        '    def go(self):\n'
        '        return self.touch()\n'
    ),
    "shop/fmt.py": (
        '"""Money."""\n'
        'def money(cents):\n'
        '    return f"${cents / 100:.2f}"\n\n\n'
        'def coins(cents):\n'
        '    return money(cents)\n'
    ),
    "shop/loop.py": (
        '"""Two functions that reach each other."""\n'
        'def a(n):\n'
        '    return b(n) if n else 0\n\n\n'
        'def b(n):\n'
        '    return a(n - 1)\n'
    ),
    "shop/broken.py": "def oops(:\n",
}


def _write_repo(dirs: dict[str, str], prefix: str = "repo") -> Path:
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix=f"flash-graph-{prefix}-"))
    for rel, src in dirs.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    return tmp


def _patch_span(src: str, address: str) -> tuple[int, int]:
    """The span `--edit` would replace for that address — the same coordinate
    system the graph must report in, reached through the same function."""
    from flash.patches import find_defs
    hits = find_defs(definitions(src), address)
    return (hits[0].start, hits[0].end) if hits else (-1, -1)


def _wide_repo(files: int = 400, per: int = 12) -> dict[str, str]:
    """A synthetic hub-and-spoke repo: one hot symbol, many honest callers.

    Each file also calls into the stdlib, so the wide instrument exercises the
    boundary bucket as well as the resolution buckets.
    """
    out: dict[str, str] = {}
    for i in range(files):
        body = [f'"""Module {i}."""', "", "import json",
                "from hub import shared, other", ""]
        for j in range(per):
            body += [f"def svc{j}(x):",
                     "    return shared(x) + other(x) + len(json.dumps(x))", ""]
        if i % 7 == 0:
            body += [f"class C{i}:", "    def go(self, x):",
                     "        return self.helper(x)", "",
                     "    def helper(self, x):", "        return x * 2", ""]
        out[f"m{i:03d}.py"] = "\n".join(body)
    out["hub.py"] = ('"""The hub."""\n\n\n'
                     'def shared(x):\n    return x + 1\n\n\n'
                     'def other(x):\n    return x - 1\n')
    return out


def _checks(wide: bool = True, live: bool = True
            ) -> tuple[list[tuple[str, bool, str]], dict[str, float]]:
    """R-1.3's vector as DATA, so `--mutants` can re-run it under a known bug.

    `wide` and `live` switch off the two slow sections — the 400-file instrument
    and the language-server round trip. The mutant sweep needs only the cheap
    resolution and merge checks, and would otherwise rebuild a synthetic repo
    ten times to prove nothing new. Printing is `_say`'s job, so a mutant run can
    collect checks it never shows.
    """
    checks: list[tuple[str, bool, str]] = []
    timing: dict[str, float] = {}

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))

    tmp = _write_repo(_SHOP)
    g = build(tmp)
    tax = g.blast("shop/pricing.py::tax")
    check("R-1.3's clause shape: a query names the symbols a change would break "
          "— `tax` is reached by `gross`, and through it by `Cart.total`",
          {"shop/pricing.py::gross", "shop/cart.py::Cart.total"} <= set(tax.names()),
          str(tax.names()))
    check("...and every edge carries its provenance: file, line, the source text, "
          "and which rule produced it",
          all(h.edge and h.edge.line > 0 and h.edge.via and h.edge.text
              for h in tax.hits), str([(h.node.id, h.edge) for h in tax.hits]))
    check("a call inside the same file is an `own-scope` edge, not a guess",
          any(h.node.id == "shop/pricing.py::gross"
              and h.edge.via == "own-scope" for h in tax.hits),
          str([(h.node.id, h.edge.via) for h in tax.hits]))
    check("`from pricing import gross` makes the CART's call an `import-alias` "
          "edge into the other file — cross-module resolution, not a name match",
          any(h.node.id == "shop/cart.py::Cart.total"
              and h.edge.via == "import-alias" for h in tax.hits),
          str([(h.node.id, h.edge.via) for h in tax.hits]))
    d1 = g.blast("shop/pricing.py::tax", 1)
    d2 = g.blast("shop/pricing.py::tax", 2)
    check("depth is depth: depth-1 `tax` has ONLY its direct user, and the "
          "transitive one appears at depth 2 rather than being absent — pinned by "
          "position, because `all()` over an empty list is how a broken depth "
          "check passes",
          d1.names() == ["shop/pricing.py::gross"]
          and [h.depth for h in d2.hits if h.node.id == "shop/cart.py::Cart.total"]
          == [2],
          f"d1={d1.names()} d2={[(h.node.id, h.depth) for h in d2.hits]}")
    check("a reading counts: changing the CONSTANT `TAX_PCT` reaches `tax` and "
          "nothing else at depth 1, which is most of the point of a graph over a "
          "grep — and the transitive readers still come in at depth 2 and 3",
          g.blast("shop/pricing.py::TAX_PCT", 1).names()
          == ["shop/pricing.py::tax"]
          and g.blast("shop/pricing.py::TAX_PCT").names()
          == ["shop/pricing.py::tax", "shop/pricing.py::gross",
              "shop/cart.py::Cart.total"],
          f"d1={g.blast('shop/pricing.py::TAX_PCT', 1).names()} "
          f"d3={g.blast('shop/pricing.py::TAX_PCT').names()}")
    check("`self.recount()` binds to `Cart.recount` through the enclosing class",
          any(h.node.id == "shop/cart.py::Cart.add"
              and h.edge.via == "enclosing-class"
              for h in g.blast("shop/cart.py::Cart.recount").hits),
          str(g.blast("shop/cart.py::Cart.recount").names()))
    check("a `self.x` the class does NOT define keeps its edge and says "
          "`inherited`, so a guess is at least a visible one — and it binds to "
          "`Thing.touch` once the repo is in",
          any(h.node.id == "shop/base.py::Widget.go"
              and h.edge.via == "inherited"
              for h in g.blast("shop/base.py::Thing.touch").hits),
          str(g.blast("shop/base.py::Thing.touch").names()))
    check("a two-function cycle answers instead of hanging: blast(a) reaches b",
          {"shop/loop.py::b"} <= set(g.blast("shop/loop.py::a").names()),
          str(g.blast("shop/loop.py::a").names()))
    check("a file that does not parse is a blind spot with a reason — not a crash, "
          "and not a silently empty node set",
          any(u.file == "shop/broken.py" and "does not parse" in u.reason
              for u in g.unresolved), str(g.unresolved[:3]))
    check("an answer states its own floor: the summary carries the blind-spot "
          "count, so a low number cannot read as a clean bill of health — and "
          "here that count is genuinely non-zero",
          "blind spot" in tax.summary() and tax.blind_spots == g.blind_spots()
          and g.blind_spots() > 0, tax.summary())
    check("a parameter, a list method and a stdlib call are NOT counted as blind "
          "spots: `cents` and `n` bind locally, `self.items.append` reaches no repo "
          "symbol, `json.dumps` leaves by an import — so the floor below is about "
          "real gaps and not about the language",
          not ({u.expr for u in g.unresolved} & {"cents", "n"})
          and any(e.dst == "~inherit#append" for e in g.external())
          and any(e.dst == "#0|json|dumps" for e in g.external())
          and not any(e.dst == "#0|json|dumps" for e in g.unbound()),
          f"unresolved={sorted({u.expr for u in g.unresolved})} "
          f"outside={sorted({e.dst for e in g.external()})} "
          f"unbound={sorted({e.dst for e in g.unbound()})}")
    check("a use that is a CALL is typed `calls`, not `reads`: the difference is "
          "the whole answer to 'what breaks if I change this'",
          all(h.edge.kind == "calls" for h in tax.hits),
          str([(h.node.id, h.edge.kind) for h in tax.hits]))
    check("class-level and module-level statements are uses by the MODULE node, "
          "so a call at import time is not charged to some function",
          any(e.src == "shop/base.py::" and e.dst == "shop/base.py::Thing"
              for e in g.resolved()),
          str([(e.src, e.dst) for e in g.resolved() if e.file == "shop/base.py"]))
    pretty = g.nodes["shop/cart.py::Cart.pretty"]
    src = (tmp / "shop/cart.py").read_text()
    check("a @property node is typed `property` and begins at its DECORATOR line",
          pretty.kind == "property"
          and src.split("\n")[pretty.start - 1].strip() == "@property",
          f"{pretty} / {src.split(chr(10))[pretty.start - 1]!r}")
    check("...stated as an identity: the graph's range for an address IS the patch "
          "arm's range for it, because both come from `definitions()`",
          (pretty.start, pretty.end) == _patch_span(src, "Cart.pretty"),
          f"graph {(pretty.start, pretty.end)} vs patch "
          f"{_patch_span(src, 'Cart.pretty')}")
    kinds = {n.kind for n in g.nodes.values()}
    check("kinds are vocabulary, not decoration: module, class, method, property "
          "and constant all appear",
          {"module", "class", "method", "property", "constant"} <= kinds,
          str(sorted(kinds)))
    check("imports are their own edge kind, so 'who pulls this file in' can never "
          "inflate a caller count",
          all(h.edge.kind != "imports" for h in tax.hits)
          and any(e.kind == "imports" for e in g.resolved()),
          str({e.kind for e in g.resolved()}))
    check("`from fmt import money` binds into fmt.py, so `money`'s callers show up "
          "WITHOUT its file being reported as a caller — and a same-file caller is "
          "found beside the cross-file one",
          g.blast("shop/fmt.py::money").names()
          == ["shop/cart.py::Cart.pretty", "shop/fmt.py::coins"],
          str(g.blast("shop/fmt.py::money").names()))
    check("...and the file-level question is answered separately: fmt.py has one "
          "importer, cart.py",
          [e.src for e in g.blast("shop/fmt.py::").importers]
          == ["shop/cart.py::"],
          str([(e.src, e.dst) for e in g.blast("shop/fmt.py::").importers]))
    check("the graph round-trips through its own JSON — a persisted index must "
          "answer exactly like a fresh pass",
          Graph.from_json(g.to_json()).blast("shop/pricing.py::tax").names()
          == tax.names(), "")

    # --- incremental merge, and the shrink guard
    (tmp / "shop/fmt.py").write_text(
        '"""Money."""\nfrom pricing import tax\n\n\n'
        'def money(cents):\n    return f"${tax(cents) / 100:.2f}"\n',
        encoding="utf-8")
    g2, rep = merge(g, tmp)
    n_files = len(python_files(tmp))
    check(f"a merge re-extracts exactly the ONE changed file ({n_files - 1} reused "
          f"by content hash) — §28's 'never scan everything again'",
          rep["re-extracted"] == 1 and rep["reused"] == n_files - 1
          and rep["added"] == 0, str(rep))
    check("...and the changed file's NEW edge is in the merged graph",
          "shop/fmt.py::money" in g2.blast("shop/pricing.py::tax").names(),
          str(g2.blast("shop/pricing.py::tax").names()))
    check("...and the definition the file NO LONGER HAS is gone from it: a merge "
          "that only ADDS would keep `coins`, deleted from the text, as a live "
          "caller of `money` forever, which is the other way an index rots",
          "shop/fmt.py::coins" not in g2.nodes
          and not [e for e in g2.edges if e.src == "shop/fmt.py::coins"]
          and "shop/fmt.py::coins" not in g2.blast("shop/fmt.py::money").names(),
          f"nodes={[k for k in g2.nodes if 'fmt' in k]} "
          f"edges={[(e.src, e.dst) for e in g2.edges if e.file == 'shop/fmt.py']}")
    (tmp / "shop/loop.py").unlink()
    g3, rep3 = merge(g2, tmp)
    check("THE SHRINK GUARD: a file that vanished from the scan keeps its nodes, "
          "so an incremental pass cannot silently shrink the graph",
          "shop/loop.py::a" in g3.nodes
          and rep3["kept-by-shrink-guard"] >= 1, str(rep3))
    g4, rep4 = merge(g2, tmp, prune=True)
    check("...and `prune=True` is the one explicit exception, which REPORTS what "
          "it removed instead of just being smaller",
          "shop/loop.py::a" not in g4.nodes and rep4["removed"] >= 2, str(rep4))
    g5, rep5 = merge(g4, _write_repo({}, "empty"))
    check("a scan of an EMPTY directory changes nothing at all — the worst-case "
          "shrink guard, which is why the guard is not a footnote",
          len(g5.nodes) == len(g4.nodes) and rep5["kept-by-shrink-guard"] >= 5,
          f"{len(g5.nodes)} vs {len(g4.nodes)} nodes; {rep5}")

    # --- the clause itself: the fixtures repo, under budget
    fx = build(_FIXTURES)
    text, rad, ms = render(_FIXTURES, "bulk_discount_cents", g=fx)
    check("R-1.3's named vector: `flash graph bulk_discount_cents` on the "
          "fixtures repo names the caller a change would break",
          any(h.node.symbol == "Cart.subtotal_cents" for h in rad.hits), text)
    check(f"...and answers in {ms:.1f} ms, under the clause's {BUDGET_MS:.0f} ms",
          ms < BUDGET_MS, f"{ms:.1f} ms")
    check("the fixtures repo is 5 files, so the clause's own repo is a thin "
          "instrument — recorded rather than banked",
          # A bare `<= 8` was true of an ABSENT directory, which is how a wheel
          # install with no `benchmarks/` got a green on a check about fixtures.
          4 <= len(python_files(_FIXTURES)) <= 8,
          f"{len(python_files(_FIXTURES))} files")
    check("`l.product.sku` binds to `Product.sku` and is labelled "
          "`unique-attribute`, because exactly one symbol in the repo carries that "
          "name — the only rule here that is a name-matching guess, so it names "
          "itself",
          any(h.node.id.endswith("Cart.add")
              and h.edge.via == "unique-attribute"
              for h in fx.blast("Product.sku").hits),
          str([(h.node.id, h.edge.via) for h in fx.blast("Product.sku").hits]))
    check("...and the SAME rule refuses to guess when the name is not unique: "
          "`l.total_cents` matches both `CartLine.total_cents` and "
          "`Cart.total_cents`, so it becomes no edge — but it is still counted as "
          "a blind spot rather than silently dropped",
          fx.blast("CartLine.total_cents").names() == []
          and any(e.via == "unique-attribute"
                  and e.dst == "?unique#total_cents" for e in fx.unbound()),
          f"hits={fx.blast('CartLine.total_cents').names()} "
          f"unbound={[(e.src, e.dst, e.via) for e in fx.unbound()][:6]}")
    check("the fixtures graph places its RELATIVE imports "
          "(`from .models import CartLine`) instead of reporting them unresolved: "
          "`Cart.add` constructs `CartLine`, and that edge crosses the file "
          "boundary",
          not any(u.file.endswith("cart.py") and "CartLine" in u.expr
                  for u in fx.unresolved)
          and "cart.py::Cart.add" in fx.blast("models.py::CartLine").names(),
          f"{fx.blast('models.py::CartLine').names()} "
          f"unresolved={str(fx.unresolved[:4])}")
    check("a self method called through `self.` is placed too: "
          "`Cart.total_cents` reaches `Cart.subtotal_cents`",
          any(h.node.id == "cart.py::Cart.total_cents"
              and h.edge.via == "enclosing-class"
              for h in fx.blast("Cart.subtotal_cents").hits),
          str([(h.node.id, h.edge.via)
               for h in fx.blast("Cart.subtotal_cents").hits]))
    check("a symbol no address names is reported as ABSENT, with the nearest "
          "addresses offered, rather than returning an empty answer that reads "
          "as a clean result",
          "not in the graph" in render(_FIXTURES, "no_such_thing", g=fx)[0], "")
    near_txt = render(_FIXTURES, "Cart.subtot", g=fx)[0]
    check("and the ABSENT answer names what the graph DOES have once the needle "
          "shares text with a node (`Cart.subtot` -> `Cart.subtotal_cents`), so a "
          "typo is a short round trip rather than a dead end",
          "nearest addresses" in near_txt
          and "cart.py::Cart.subtotal_cents" in near_txt, near_txt[:160])
    check("...but a needle with no textual neighbour is met with a bare ABSENT, "
          "never with neighbours invented for it — `Cart.` reduces to the empty "
          "name, which is a substring of EVERY symbol, so without the guard it "
          "would be offered five arbitrary nodes as if they were close",
          "nearest addresses" not in render(_FIXTURES, "Cart.", g=fx)[0]
          and "nearest addresses" not in render(_FIXTURES, "shop::gross", g=fx)[0],
          render(_FIXTURES, "Cart.", g=fx)[0][:160])
    timing["fx_ms"] = ms
    if live:
        up = live_upgrade(_FIXTURES, fx, limit=3)
        check("the LSP seam is wired, not a stub: `live_upgrade` asks the language "
              "server about this pass's OWN blind spots and reports what it settled "
              f"({up['asked']} asked, {up['settled']} settled) — measured here, and "
              "deliberately outside the < 200 ms clause",
              up["asked"] > 0 and up["settled"] > 0
              and len(up["hits"]) == up["settled"]
              and all(h["refs"] > 0 for h in up["hits"]), str(up)[:220])
        timing["live_asked"] = up["asked"]
        timing["live_settled"] = up["settled"]
        # I-4 for the seam: the graph's own answer must not depend on a server
        # being up. This runs OUTSIDE the mutant sweep (which passes live=False so
        # no mutant has to start a language server), so it is a check without a
        # mutant of its own, and it is written as such rather than left to look
        # covered.
        from flash import lsp as _lsp
        real_refs = _lsp.find_references

        def dead(root, name):
            raise RuntimeError("no server on this box")

        _lsp.find_references = dead
        try:
            down = live_upgrade(_FIXTURES, fx, limit=2)
        finally:
            _lsp.find_references = real_refs
        check("with no language server answering, `--live` returns "
              "`no language server answered` and keeps the counts it got, instead "
              "of raising into the caller — the offline path does not need a "
              "server",
              down["why"] == "no language server answered"
              and down["asked"] > 0 and down["settled"] == 0
              and down["hits"] == [], str(down)[:200])
    if wide:
        timing.update(_wide_checks(fx, check))
    return checks, timing


def _wide_checks(fx: Graph, check) -> dict[str, float]:
    """The 400-file instrument, because < 200 ms on five files is not a bound.

    Its own function so `--mutants` can skip it: every bug in the resolution
    ladder shows up in the cheap checks first. Its SIZE is chosen, not found: at
    240 files the scan-per-hop bug answers in 135-169 ms, so the budget gate
    waves it through and the clause would have been a formality. 400 files put
    that bug at 443-943 ms — always over the budget, on every run I took — while
    the honest pass stays at 30-101 ms cold. The spread is this box's load, not
    the algorithm's: both are printed live rather than quoted from here, and the
    gate is the printed `wms < BUDGET_MS` in this function.
    """
    wdir = _write_repo(_wide_repo(files=400, per=12), "wide")
    tw = _now_ms()
    wg = build(wdir)
    cold = _now_ms() - tw
    _, wrad, wms = render(wdir, "shared", depth=DEFAULT_DEPTH, g=wg)
    check(f"the wide instrument ({len(python_files(wdir))} files: 400 service "
          f"modules plus the hub they import, {len(wg.nodes)} nodes): a "
          f"depth-{DEFAULT_DEPTH} blast radius in "
          f"{wms:.1f} ms, still inside the budget at "
          f"{len(wg.nodes) / max(len(fx.nodes), 1):.0f}x the fixtures' size",
          wms < BUDGET_MS, f"{wms:.1f} ms")
    check("...and it is a real answer rather than a fast empty one: the hub "
          "symbol has hundreds of callers",
          len(wrad.hits) > 100, f"{len(wrad.hits)} hits")
    check("the cold full pass is timed and printed apart, so an index-build claim "
          "cannot be mistaken for an answer-time claim",
          cold > 0 and "cold_ms" in wg.stats, f"{cold:.0f} ms cold")
    wg2, rep2 = merge(wg, wdir)
    check(f"the merge is what makes a repeat query cheap: a second pass over the "
          f"wide repo costs {rep2['ms']:.1f} ms and re-extracts nothing",
          rep2["re-extracted"] == 0 and rep2["reused"] == len(python_files(wdir)),
          str(rep2))
    check("and on the wide repo the floor is still reported rather than assumed: "
          "a 400-file pass with no blind spots says so, and a reader can see that "
          "it is a property of THIS instrument, not of the algorithm",
          wg.blind_spots() == 0 and len(wg.external()) > 0,
          f"{wg.blind_spots()} blind, {len(wg.external())} outside")
    return {"wide_ms": wms, "cold_ms": cold, "wide_nodes": len(wg.nodes),
            "wide_edges": len(wg.resolved())}


def _say(checks: list[tuple[str, bool, str]], timing: dict[str, float],
         verbose: bool) -> int:
    """Print a run, and return how many checks failed."""
    if verbose:
        for label, ok, detail in checks:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}"
                  + (f"  {detail}" if detail and not ok else ""))
        print(f"  ..  first query on a cold index: fixtures "
              f"{timing['fx_ms']:.1f} ms, wide "
              f"{timing.get('wide_ms', 0):.1f} ms "
              f"({timing.get('wide_nodes', 0):.0f} nodes, "
              f"{timing.get('wide_edges', 0):.0f} edges, after a "
              f"{timing.get('cold_ms', 0):.0f} ms build); language server settled "
              f"{timing.get('live_settled', 0)}/{timing.get('live_asked', 0)}")
    bad = sum(not ok for _, ok, _ in checks)
    print(f"\ngraph selftest: {len(checks) - bad}/{len(checks)} checks passed"
          f"  [fixtures query {timing['fx_ms']:.1f} ms of "
          f"{BUDGET_MS:.0f} ms budget, "
          f"wide query {timing.get('wide_ms', 0):.1f} ms]")
    return bad


def run_selftest(verbose: bool = True, mutants: bool = False) -> int:
    """R-1.3's vector, offline and deterministic: the named callers, under budget."""
    if not _FIXTURES.is_dir():
        # Measured on a wheel installed into a clean venv: 8 of these 44 checks
        # build a graph over the fixtures package, so with no `benchmarks/` beside
        # the installed `flash` they all print FAIL and the report reads as though
        # the graph were broken. It is not; the data is not there. Exit 2 — "this
        # is not a tree this vector can run in" — rather than a count that invites
        # someone to diff a graph nobody built.
        print(f"flash.graph --selftest cannot run here: it builds a graph over "
              f"{_FIXTURES}, and that directory does not exist.\n"
              f"  A wheel install carries the package only, no `benchmarks/`, so "
              f"8 of the 44 checks would report a failure that is a missing "
              f"directory wearing one. Run this from a clone or an unpacked sdist "
              f"(both ship `benchmarks/`). To check the install itself: "
              f"`flash power`, `flash --help`, `python -m flash.harness --selftest`.")
        return 2
    checks, timing = _checks()
    bad = _say(checks, timing, verbose)
    escaped = mutate(verbose) if mutants else 0
    return 1 if (bad or escaped) else 0


# --------------------------------------------------------------- mutation cover

class _NeverReused:
    """A `hashlib` stand-in whose digest is new every call, so a merge that
    thought it could skip a file by content hash is wrong every time."""
    n = 0

    @staticmethod
    def sha1(_b):
        _NeverReused.n += 1
        return _NeverReused()

    def __init__(self):
        self.k = _NeverReused.n

    def hexdigest(self):
        return f"fresh-{self.k}"


def mutate(verbose: bool = True) -> int:
    """Put each bug back, and require that ITS OWN check fails.

    12 bugs this pass can plausibly have — each one is a way a graph answer looks
    right and is not. `_CATCHES`'s job is done by the 4th field: the check that
    MUST break, so a sweep that fails for an unrelated reason is not counted as
    coverage, and a mutant that breaks nothing at all is reported as a MISS. The
    wide instrument is switched off for all but the one mutant it is the witness
    for (scanning per hop costs 0.08 ms on the fixtures repo — only 400 files make
    the budget feel it).

    Returns the number that ESCAPED, which is the number the run fails on.
    """
    g = globals()
    real = {"_attr_target": g["_attr_target"], "_external": g["_external"],
            "_locals": g["_locals"], "hashlib": g["hashlib"],
            "extract": g["extract"], "merge": g["merge"],
            "_without": g["_without"], "definitions": g["definitions"],
            "nearest": g["nearest"]}
    real_extract = real["extract"]
    real_merge = real["merge"]
    real_blast = Graph.blast
    real_get = Graph.get
    real_callers = Graph.callers_of
    real_defs = real["definitions"]

    def guess_anyway(dst, by_attr):
        hits = by_attr.get(dst.split("#", 1)[1], ())
        return hits[0] if hits else ""          # a coin flip, taken as fact

    def no_outside(dst):
        return False                            # every boundary is a hole

    def no_locals(tree):
        return {}, set()                        # every parameter is a hole

    def reads_only(rel, src):
        ns, es, us = real_extract(rel, src)
        return ns, [replace(e, kind="reads") if e.kind == "calls" else e
                    for e in es], us

    def depth_one(self, needle, depth=DEFAULT_DEPTH):
        return real_blast(self, needle, 1)      # `depth` accepted, ignored

    def prune_always(old, root, prune=False):
        return real_merge(old, root, True)      # the guard is off by default

    def keep_stale(rel, nodes, edges, unres):
        return nodes, edges, unres              # a merge that only adds

    def shift_spans(src):
        defs = real_defs(src)
        for d in defs:
            d.start += 1                        # decorators left out
        return defs

    def fuzzy_get(self, needle):
        return real_get(self, needle) or [next(iter(self.nodes.values()))]

    def scan_per_hop(self, node_id):
        return [e for e in self.resolved() if e.dst == node_id]

    def nearest_by_first(g_, needle, limit=5):
        return list(g_.nodes)[:limit]     # whatever came first, dressed as a match

    bugs: list[tuple[str, dict, str, str, bool]] = [
        ("binds an attribute to the FIRST name match, not the only one",
         {"_attr_target": guess_anyway}, "_attr_target",
         "refuses to guess when the name is not unique", False),
        ("counts `json.dumps` and `list.append` as blind spots",
         {"_external": no_outside}, "_external",
         "a parameter, a list method and a stdlib call", False),
        ("counts every parameter as a blind spot",
         {"_locals": no_locals}, "_locals",
         "a parameter, a list method and a stdlib call", False),
        ("types every call as a read",
         {"extract": reads_only}, "extract",
         "a use that is a CALL is typed `calls`", False),
        ("accepts `depth` and ignores it",
         {}, "Graph.blast", "depth is depth", False),
        ("prunes files that vanished from a scan, by default",
         {"merge": prune_always}, "merge", "THE SHRINK GUARD", False),
        ("keeps a changed file's stale nodes and edges",
         {"_without": keep_stale}, "_without",
         "the definition the file NO LONGER HAS", False),
        ("starts a node's span BELOW its decorators",
         {"definitions": shift_spans}, "definitions",
         "begins at its DECORATOR line", False),
        ("answers a query about a symbol the graph does not have",
         {}, "Graph.get", "reported as ABSENT", False),
        # The absence answer has two failure directions and both are cheap to
        # make: offer nothing, or offer whatever the node dict happened to yield
        # first. The second is the one a reader cannot see through, because a
        # "nearest addresses:" line looks like help.
        ("offers the first nodes it has as a mistyped symbol's nearest addresses",
         {"nearest": nearest_by_first}, "nearest",
         "names what the graph DOES have", False),
        ("re-extracts every file on every merge",
         {"hashlib": _NeverReused}, "hashlib",
         "re-extracts exactly the ONE changed file", False),
        # The one mutant that needs the wide instrument. Measured on the fixtures
        # repo (31 nodes, 28 edges), scanning the edge list per hop costs 0.08 ms
        # against 0.11 ms for the index — at that size the bug is FASTER, so a
        # budget check there could never catch it. Only at 400 files does the
        # index earn its keep, which is what makes the instrument's size part of
        # the gate rather than a way to make it look impressive.
        ("scans every edge to answer one hop",
         {}, "Graph.callers_of", "blast radius in", True),
    ]
    escaped = 0
    for label, patch, attr, catcher, need_wide in bugs:
        for k, v in patch.items():
            g[k] = v
        if attr == "Graph.blast":
            Graph.blast = depth_one
        elif attr == "Graph.get":
            Graph.get = fuzzy_get
        elif attr == "Graph.callers_of":
            Graph.callers_of = scan_per_hop
        try:
            checks, _ = _checks(wide=need_wide, live=False)
            fails = [nm for nm, ok, _ in checks if not ok]
        finally:
            for k, v in real.items():
                g[k] = v
            Graph.blast = real_blast
            Graph.get = real_get
            Graph.callers_of = real_callers
        hit = any(catcher in f for f in fails)
        escaped += not hit
        if verbose or not hit:
            print(f"  {'ok  ' if hit else 'MISS'} MUTATION: {label} -> "
                  f"{len(fails)} check(s) fail"
                  + ("" if hit else f", none of them the one that catches it: "
                                    f"{fails[:2]}"))
    print(f"graph mutants: {len(bugs) - escaped}/{len(bugs)} caught")
    return escaped


# --------------------------------------------------------------------- main

def add_flags(p: argparse.ArgumentParser) -> None:
    """The flags, defined once so `flash graph` and `python -m flash.graph` cannot
    drift apart."""
    p.add_argument("symbol", nargs="?", help="a symbol name, or `file::Symbol`")
    p.add_argument("--path", default=".", help="repo root to scan (default: .)")
    p.add_argument("--depth", type=int, default=DEFAULT_DEPTH,
                   help=f"hops out from the changed symbol "
                        f"(default {DEFAULT_DEPTH})")
    p.add_argument("--json", action="store_true",
                   help="the answer as an object, for the loop to inject")
    p.add_argument("--live", action="store_true",
                   help="after answering, ask the language server about this "
                        "pass's blind spots (slow: a server has to start up)")
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--mutants", action="store_true",
                   help="with --selftest: put each of the 12 bugs back and "
                        "require its own check to fail")


def dispatch(a: argparse.Namespace) -> int:
    if a.selftest:
        return run_selftest(mutants=a.mutants)
    if not a.symbol:
        print(__doc__)
        return 0
    # `--live` rebuilds rather than reusing a cached index: a cache keyed by path
    # would be a stale graph pretending to be current. §28.1's watcher-merge loop
    # is the thing that will legitimately keep one warm, and it is not shipped, so
    # this pays the cold build and the caller sees why the flag is slow.
    g = build(a.path) if a.live else None
    text, rad, ms = render(a.path, a.symbol, a.depth, g=g)
    rep = live_upgrade(a.path, g) if a.live else None
    if a.json:
        out = {
            "target": rad.target and rad.target.id,
            "hits": [{"id": h.node.id, "kind": h.node.kind, "depth": h.depth,
                      "span": [h.node.start, h.node.end],
                      "edge": h.edge and h.edge.__dict__} for h in rad.hits],
            "importers": [e.__dict__ for e in rad.importers],
            "blind_spots": rad.blind_spots, "external": rad.external,
            "ms": round(ms, 2), "depth": a.depth, "path": str(a.path)}
        if rep is not None:
            out["live"] = rep
        print(json.dumps(out, indent=1))
    else:
        print(text)
        print(f"  [{ms:.1f} ms, depth {a.depth}]")
        if rep is not None:
            print(f"  ..  language server: {rep['asked']} blind spot(s) asked, "
                  f"{rep['settled']} settled — {rep['why']}"
                  + (f"; {rep['names_not_asked']} not asked"
                     if rep["names_not_asked"] else ""))
            for h in rep["hits"]:
                print(f"      {h['name']}: {h['refs']} site(s) in "
                      + ", ".join(str(f) for f in h["files"]))
    return 0 if rad.target else 1


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="flash graph",
                                 description=__doc__.split("\n")[0])
    add_flags(ap)
    return dispatch(ap.parse_args(argv))


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
