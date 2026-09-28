"""R-1.4: the second language for perception — TypeScript and TSX (PLAN §28.1).

R-1.1..1.3 made PERCEIVE able to answer "what breaks if this symbol changes?" for
Python files and said out loud that the graph was Python-only. This module is the
box that decided which second language arrives, and the arrival.

**The choice came out of records, not taste.** Two were read on 2026-09-28. The
ledger (1148 outcome rows, every prompt template classified) contains **zero** rows
asking for SQL and **zero** asking for TypeScript: all of its demand is this
project's own Python suites plus 38 screenshot→HTML rows. So the ledger cannot pick
between the two languages R-1.4 named, and that null is its own finding. The second
record is the tree this index reads: `ts_files('.')` prints **21** — 14 `.tsx` and 7
`.ts`, 20 of them under `site/src` plus `site/vite.config.ts` — against **0** `.sql`
and **0** `.db` files. (An earlier
draft of this paragraph said 23 by sweeping in the two `.css` files, one of which is
`site/dist`'s generated bundle; a stylesheet is not a language this graph parses and a
build artifact is not a file anyone edits, so the count here is the one the tool
prints.) The front end of this repo is real work the graph was blind to; the data
layer is not present at all. TypeScript was therefore chosen by a file count, SQL was
not chosen because nothing here is written in it, and §10.2 keeps the call
overrulable.

Four decisions worth their comments:

* **The grammar is an optional extra and its absence is a refusal, not a guess.**
  `tree-sitter` plus `tree-sitter-typescript` is the only way to get a real
  TypeScript AST from Python; a hand-rolled scanner would be a regex pretending to
  be a parser, which is the failure `flash/patches.py` exists to prevent. So this
  module imports with the extra missing (R-7.7's rule), `available()` returns the
  reason, and the one call that needs the grammar raises with that reason in it.
  `requirements.txt` still names four packages.
* **The records are the graph's own.** `Node`, `Edge` and `Unresolved` come from
  `flash.graph`, so `blast()`, `Radius.summary()` and `to_json()` answer about
  TypeScript with no second query engine. The price is that every `dst` emitted
  here is already bound: `Graph._bind` places Python dotted module names, and
  `./ui` → `ui.tsx` is a fact about the filesystem, not about the interpreter.
  Anything that cannot be bound becomes a counted `Unresolved`, never an edge to a
  node that does not exist.
* **Names a scope binds are collected before uses are charged**, per definition,
  exactly as `graph._locals` does for Python. Without it every parameter and every
  destructured binding in a `.tsx` file is a blind spot, and the floor the count
  reports stops meaning anything.
* **`via` keeps its promise.** `ts-own-scope`, `ts-import`, `ts-default-export`,
  `ts-qualified-name`, `ts-jsx`, `ts-type`, `ts-unique-export`. The two rules that
  can guess — following `mod.thing` through a namespace import, and binding a name
  that appears exactly once repo-wide — are labelled so a reader can discount them.

**What this does not buy, stated so nobody has to discover it.** The patch arm
learned to address a TypeScript symbol on 2026-09-28 — `flash/patches.py` dispatches
by file suffix, so `# edit: App.tsx :: Widget` resolves to the span this module's
`_declarations` reports, the same span the `L11-L15` form always gave. Three things
are still Python-only: the loop's PERCEIVE hint ranks symbols with
`flash.lsp.symbols_involved` and `graph.scope_graph()`, so a failing `.tsx` test
gets no ranked hint; R-1.2's live-upgrade equivalent asks jedi and there is no
`tsserver` bound; and `harness.diagnose_files` has no `node`/`vitest` runner behind
it, so a `.tsx` patch that parses is accepted on the strength of a parse. All three
stay open in SPEC R-1.4 rather than being described as shipped.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path

from flash.graph import MAX_TEXT, Edge, Graph, Node, Unresolved, walk_files

_BUILD_DIRS = frozenset({"dist", "coverage", "out"})

TS_SUFFIXES = (".ts", ".tsx", ".d.ts")
JS_SUFFIXES = (".js", ".jsx", ".mjs")

# What the platform supplies. The mirror of `graph._BUILTINS`: an unresolved edge
# for every `Math.round` buries the ones that would matter.
_GLOBALS = frozenset({
    "Math", "JSON", "Object", "Array", "Promise", "Date", "Number", "String",
    "Boolean", "RegExp", "Map", "Set", "WeakMap", "WeakSet", "Symbol", "Error",
    "TypeError", "RangeError", "SyntaxError", "globalThis", "window", "document",
    "navigator", "location", "history", "localStorage", "sessionStorage",
    "console", "process", "require", "module", "exports", "__dirname",
    "__filename", "fetch", "setTimeout", "clearTimeout", "setInterval",
    "clearInterval", "requestAnimationFrame", "cancelAnimationFrame", "Intl",
    "BigInt", "URL", "URLSearchParams", "TextEncoder", "TextDecoder",
    "structuredClone", "performance", "encodeURIComponent", "decodeURIComponent",
    "React", "JSX", "Infinity", "NaN", "undefined", "arguments", "this",
})

# Type-level names from lib.d.ts. These arrive as `type_identifier` references, so
# they would otherwise be counted as blind spots on every typed signature.
_TS_LIB = frozenset({
    "any", "unknown", "never", "void", "string", "number", "boolean", "object",
    "symbol", "bigint", "Record", "Partial", "Required", "Readonly", "Omit",
    "Pick", "Exclude", "Extract", "NonNullable", "Parameters", "ConstructorParameters",
    "ReturnType", "InstanceType", "Awaited", "Uppercase", "Lowercase", "ThisType",
    "ThisParameterType", "Iterable", "AsyncIterable", "IterableIterator",
    "Iterator", "Function", "Console", "Window", "Document", "Element",
    "HTMLElement", "NodeList", "Event", "KeyboardEvent", "MouseEvent", "Response",
    "Request", "Headers", "AbortController", "Timeout", "NodeJS", "Schedule",
})

# tree-sitter declaration node type -> this graph's kind vocabulary.
_KIND = {
    "function_declaration": "function",
    "generator_function_declaration": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "interface_declaration": "interface",
    "type_alias_declaration": "type",
    "enum_declaration": "enum",
    "module": "namespace",
    "method_definition": "method",
    "function_expression": "function",
    "arrow_function": "function",
    "variable_declarator": "constant",
}

# A declaration's own `name` field is not a use of that name.
_NAME_FIELDS = frozenset({
    ("function_declaration", "name"), ("generator_function_declaration", "name"),
    ("class_declaration", "name"), ("abstract_class_declaration", "name"),
    ("interface_declaration", "name"), ("type_alias_declaration", "name"),
    ("enum_declaration", "name"), ("method_definition", "name"),
    ("variable_declarator", "name"), ("public_field_definition", "name"),
    ("property_signature", "name"), ("field_definition", "name"),
    ("shorthand_property_identifier_pattern", None),
    ("required_parameter", "pattern"), ("optional_parameter", "pattern"),
    ("labeled_statement", "label"), ("label", None), ("statement_identifier", None),
    ("accessibility_modifier", None), ("property_identifier", None),
    ("optional_call_expression", None), ("function_type", None),
    ("call_signature", None), ("construct_signature", None),
    ("method_signature", None), ("method_definition", "name"),
    ("pair", "key"), ("shorthand_property_identifier", None),
    ("required_parameter", None), ("spread_element", None),
})


def _clip(line: str) -> str:
    t = line.strip()
    return t if len(t) <= MAX_TEXT else t[:MAX_TEXT - 1] + "…"


def _join(container: str, name: str) -> str:
    return f"{container}.{name}" if container else name


def _text(n) -> str:
    return n.text.decode("utf-8", "replace") if n is not None else ""


def _same(a, b) -> bool:
    """Whether two handles are the same node.

    `tree_sitter.Node` wrappers are made fresh on every access, so `a is b` is
    False for a node fetched twice from the same parent. Comparing `is` here was
    the first version's bug: a declaration's own name failed its own "is this the
    name field?" test and came back as an edge from the symbol to itself.
    """
    return a is not None and b is not None and a.id == b.id


def _named(node):
    return [c for c in node.children if c.is_named]


# ------------------------------------------------------------------- grammar

_LANGS: dict[str, object] = {}
_REFUSAL = ""


def available() -> tuple[bool, str]:
    """Whether the TypeScript grammar loads, and the reason it does not.

    Lazy and once-only: `flash.graph` has to build a Python graph on a machine that
    never installed the extra, and a user who asks for TypeScript on that machine
    gets this sentence rather than an `ImportError` traceback.
    """
    global _REFUSAL
    if _LANGS:
        return True, "loaded"
    try:
        import tree_sitter
        import tree_sitter_typescript
    except Exception as exc:                       # noqa: BLE001 - report, do not crash
        _REFUSAL = (f"{type(exc).__name__}: {exc}. The TypeScript grammar is an "
                    f"optional extra: pip install 'flash-coder[ts]'")
        return False, _REFUSAL
    try:
        _LANGS["ts"] = tree_sitter.Language(tree_sitter_typescript
                                            .language_typescript())
        _LANGS["tsx"] = tree_sitter.Language(tree_sitter_typescript.language_tsx())
    except Exception as exc:                       # noqa: BLE001
        _REFUSAL = f"{type(exc).__name__}: {exc}"
        return False, _REFUSAL
    return True, "loaded"


def refusal() -> str:
    """The last `available()` reason, for a report that must not re-import."""
    return _REFUSAL


def parse(rel: str, src: str):
    """The tree for one file, from the grammar that owns its syntax."""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    import tree_sitter
    lang = _LANGS["tsx" if rel.endswith(".tsx") else "ts"]
    return tree_sitter.Parser(lang).parse(bytes(src, "utf-8"))


def ts_files(root: str | Path) -> list[Path]:
    """Every TypeScript-family file under a root, minus the build output.

    `dist`, `coverage` and `out` are the bundler's own directories: indexing a
    generated `index.d.ts` would put nodes in the graph that no one edits.
    """
    return walk_files(root, (".ts", ".tsx"), extra_skip=_BUILD_DIRS)


# ------------------------------------------------------------------- the index


@dataclass
class FileIndex:
    """What one file hands to the rest of the tree.

    `exports` maps a name an importer can write to this file's symbol string;
    `"default"` is the default export and a re-export adds the name it forwards.
    `follows_star` says an `export * from` is in play, which is why
    `ts-unique-export` refuses to guess past it.
    """
    path: str
    module_id: str
    exports: dict[str, str] = field(default_factory=dict)
    reexports: set[str] = field(default_factory=set)
    follows_star: bool = False
    ids: set[str] = field(default_factory=set)


@dataclass
class RepoIndex:
    """TypeScript module resolution for one tree, built before any edge exists."""
    files: dict[str, FileIndex] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    def place(self, spec: str, from_file: str) -> str:
        """Which indexed file a written specifier names, or "" when none does.

        Tried in `tsc`'s own order: the literal path, then the extensions, then the
        directory's index file. Returning "" for a bare specifier is the correct
        answer, not a failure — `react` is not in this tree.
        """
        if not spec.startswith("."):
            return ""
        target = _resolve(from_file.rpartition("/")[0], spec)
        for cand in _candidates(target):
            if cand in self.files:
                return cand
        return ""

    def unique_export(self, name: str) -> str:
        """The single file exporting `name`, or "" unless the tree says one.

        A re-export is not an answer: the file that writes `export { x } from`
        holds no node for `x`, so guessing it would point the edge at a symbol
        that does not exist in the file the graph claims to name.
        """
        hits = [f for f, ix in self.files.items()
                if name in ix.exports and name not in ix.reexports]
        return hits[0] if len(hits) == 1 else ""


def _resolve(dirname: str, spec: str) -> str:
    parts = dirname.split("/") if dirname else []
    for seg in spec.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/".join(parts)


def _candidates(target: str) -> list[str]:
    out = []
    if target.endswith((".ts", ".tsx", ".js", ".jsx", ".json", ".mjs", ".cjs")):
        out.append(target)
    for s in TS_SUFFIXES:
        out.append(target + s)
    for s in TS_SUFFIXES:
        out.append(f"{target}/index{s}")
    return out


# ------------------------------------------------------------- one file's shape


def _decl_and_default(stmt):
    """The declaration inside `export [default] <decl>`; (decl, is_default)."""
    if stmt.type == "export_statement":
        is_default = any(c.type == "default" for c in stmt.children)
        for c in _named(stmt):
            if c.type in _KIND or c.type in ("lexical_declaration",
                                             "variable_declaration",
                                             "internal_module",
                                             "expression_statement"):
                return c, is_default
        return None, is_default
    return stmt, False


def _declarator_kind(vd) -> str:
    value = vd.child_by_field_name("value")
    if value is not None and value.type in ("arrow_function", "function_expression",
                                            "function", "generator_function"):
        return "function"
    return "constant"


def _declarations(rel: str, root_node) -> tuple[list[Node], FileIndex]:
    """A file's nodes and its export table, from one top-level walk.

    Spans are the whole STATEMENT, `export` keyword included, because that is the
    range an address would replace — the rule `flash.patches.definitions` uses for
    Python, so a graph span and a patch span are the same two line numbers.
    """
    ix = FileIndex(rel, f"{rel}::")
    nodes = [Node(rel, "", "module", 1, root_node.end_point[0] + 1)]
    ids = {nodes[0].id}

    def add(symbol: str, kind: str, start: int, end: int) -> None:
        if symbol and f"{rel}::{symbol}" not in ids:
            nodes.append(Node(rel, symbol, kind, start, end))
            ids.add(f"{rel}::{symbol}")

    for stmt in _named(root_node):
        if stmt.type in ("import_statement", "import_declaration"):
            continue
        if stmt.type == "export_statement":
            src = stmt.child_by_field_name("source")
            for spec in _walk(stmt, "export_specifier"):
                nm = spec.child_by_field_name("name")
                al = spec.child_by_field_name("alias")
                if nm is not None:
                    out = _text(al or nm)
                    if src is None:
                        ix.exports[out] = _text(nm)
                    else:
                        ix.exports[out] = out
                        # Exported, but not defined here: no node of this file is
                        # its far end, so an edge to it would be an edge to nothing.
                        ix.reexports.add(out)
            has_star = any(c.type == "*" for c in stmt.children)
            namespaced = any(c.type == "namespace_export" for c in stmt.children)
            # The `*` in `export * from` is an anonymous token, and it is the whole
            # claim: names this file never mentions are visible from it. `export *
            # as ns` forwards no names, only `ns`, so it does not count.
            if src is not None and has_star and not namespaced:
                ix.follows_star = True
        decl, is_default = _decl_and_default(stmt)
        if decl is None:
            continue
        if decl.type in ("lexical_declaration", "variable_declaration"):
            for vd in _walk(decl, "variable_declarator"):
                name = _text(vd.child_by_field_name("name"))
                if not name:
                    continue
                add(name, _declarator_kind(vd), stmt.start_point[0] + 1,
                    stmt.end_point[0] + 1)
                if stmt.type == "export_statement":
                    ix.exports[name] = name
                if is_default:
                    ix.exports["default"] = name
            continue
        kind = _KIND.get(decl.type, "")
        name = _text(decl.child_by_field_name("name"))
        if not kind or not name:
            continue
        add(name, kind, stmt.start_point[0] + 1, stmt.end_point[0] + 1)
        if stmt.type == "export_statement" or _is_exported(decl):
            ix.exports[name] = name
        if is_default:
            ix.exports["default"] = name
        if decl.type in ("class_declaration", "abstract_class_declaration"):
            body = decl.child_by_field_name("body")
            for m in (_named(body) if body is not None else []):
                mname = _text(m.child_by_field_name("name"))
                if m.type == "method_definition" and mname:
                    add(_join(name, mname), "method", m.start_point[0] + 1,
                        m.end_point[0] + 1)
                elif m.type == "public_field_definition" and mname and (
                        m.child_by_field_name("value") is not None):
                    add(_join(name, mname), "constant", m.start_point[0] + 1,
                        m.end_point[0] + 1)
    ix.ids = ids
    return nodes, ix


def _is_exported(decl) -> bool:
    """`export` is the only truth about what another file may import."""
    return False


# ------------------------------------------------- one file, as an address

# tree-sitter's own markers. `ERROR` is a run of text it could not place, and
# `MISSING` is a token it needed and did not find; both are anonymous enough
# that the named-children walk the index uses never sees them.
_MARKED = frozenset({"ERROR", "MISSING"})


def _error_site(node):
    """The narrowest node the grammar marked as broken, or None.

    `node.has_error` is inherited, so the root is True for any error anywhere;
    a refusal that has to say WHERE has to look for the marker itself.
    """
    if node.type in _MARKED:
        return node
    for c in node.children:
        hit = _error_site(c)
        if hit is not None:
            return hit
    return None


def syntax_error(rel: str, src: str) -> str:
    """Why this file's TypeScript does not parse, or `""` when it does.

    The grammar never raises on a broken program: it marks the region and carries
    on, which is what an editor wants and what a patch protocol must not accept.
    A span read off a tree that had to resynchronise is a guess about where a
    statement ends, and `flash.patches` refuses guesses — this is the check its
    `ast.parse` is for Python.
    """
    root = parse(rel, src).root_node
    if not root.has_error and root.type not in _MARKED:
        return ""
    err = _error_site(root)
    if err is None:
        # An error the grammar swallowed at the end of the file: `has_error` is
        # set and no node is marked, so the far edge of the tree is the truth.
        return (f"line {root.end_point[0] + 1}: the grammar resynchronised past "
                f"a statement that never closes")
    shown = _text(err).splitlines()[0].strip()[:48]
    return f"line {err.start_point[0] + 1}: {err.type.lower()} {shown!r}"


def _begins_export(line: str) -> bool:
    """Whether a definition's own span starts with the `export` keyword.

    The keyword is inside the span `_declarations` reports, so a splice that
    replaces the span deletes it unless the replacement re-emits it. That is the
    TypeScript shape of Python's decorator rule, and it earns a check because a
    silently un-exported symbol breaks every file that imports it while the
    edited file still looks fine.
    """
    t = line.lstrip()
    if not t.startswith("export"):
        return False
    nxt = t[6:7]
    return not nxt.isalnum() and nxt not in ("_", "$")


def definitions(rel: str, src: str) -> list:
    """Every addressable definition in one TypeScript file.

    The spans come from `_declarations`, the same walk that fills the graph, so
    `# edit: Hero.tsx :: Cart.total` replaces exactly the lines `blast()` says
    that symbol owns — one rule for both arms, which is what `graph._patch_span`
    relies on for Python. `col` is the indentation of the span's first line, so
    `normalise` restores a column-0 replacement to the member's real nesting.
    """
    from flash.patches import Def          # lazy: flash.graph imports both
    nodes, _ix = _declarations(rel, parse(rel, src).root_node)
    lines = src.split("\n")
    out = []
    for n in nodes[1:]:                    # nodes[0] is the module itself
        head = lines[n.start - 1] if 0 < n.start <= len(lines) else ""
        container, _, name = n.symbol.rpartition(".")
        out.append(Def(name, container, n.start, n.end,
                       len(head) - len(head.lstrip()), _begins_export(head)))
    return out


def _walk(node, want: str) -> list:
    """Every node of one type below `node`, crossing into nested bodies."""
    out = []
    stack = [node]
    while stack:
        n = stack.pop()
        for c in _named(n):
            if c.type == want:
                out.append(c)
            stack.append(c)
    return out


# ------------------------------------------------------------- local bindings


def _pattern_names(n, out: set[str]) -> None:
    if n is None:
        return
    if n.type == "identifier":
        out.add(_text(n))
    elif n.type == "shorthand_property_identifier_pattern":
        out.add(_text(n))
    elif n.type in ("object_pattern", "array_pattern", "rest_pattern",
                    "object_assignment_pattern", "pair", "pair_pattern"):
        for c in _named(n):
            _pattern_names(c, out)
    elif n.type == "property_identifier":
        out.add(_text(n))


def _binds(node, out: set[str]) -> None:
    t = node.type
    if t in ("variable_declarator", "assignment_pattern", "assignment_expression"):
        _pattern_names(node.child_by_field_name("name")
                       or node.child_by_field_name("left"), out)
    elif t in ("required_parameter", "optional_parameter"):
        _pattern_names(node.child_by_field_name("pattern")
                       or node.child_by_field_name("name"), out)
    elif t == "formal_parameters":
        for c in _named(node):
            _binds(c, out)
    elif t == "catch_clause":
        for c in _walk(node, "identifier"):
            out.add(_text(c))
    elif t == "import_statement":
        out.update(_import_locals(node))
    elif t in ("method_definition", "public_field_definition", "field_definition",
               "property_signature"):
        _pattern_names(node.child_by_field_name("name"), out)


def _scope_locals(decl) -> set[str]:
    """Every name one definition binds, which therefore names no repo symbol."""
    out: set[str] = set()
    for f in ("parameters", "type_parameters"):
        c = decl.child_by_field_name(f)
        if c is not None:
            _binds(c, out)
    if decl.type == "formal_parameters":
        _binds(decl, out)
    body = (decl.child_by_field_name("body")
            or decl.child_by_field_name("value"))
    if body is not None:
        for c in _walk(body, "variable_declarator"):
            _binds(c, out)
        for c in _walk(body, "import_statement"):
            _binds(c, out)
        for c in _named(body):
            if c.type in ("catch_clause", "class_heritage"):
                _binds(c, out)
        for kind in ("function_declaration", "generator_function_declaration",
                     "class_declaration", "interface_declaration",
                     "type_alias_declaration", "enum_declaration"):
            for c in _walk(body, kind):
                _pattern_names(c.child_by_field_name("name"), out)
    return out


def _import_locals(node) -> set[str]:
    return {local for local, _, _ in _import_rows(node)}


def _import_rows(node) -> list[tuple[str, str, str]]:
    """(local name, specifier, imported name) for one `import` statement."""
    spec = _text(node.child_by_field_name("source")).strip("'\"")
    if not spec:
        return []
    out = []
    for clause in _named(node):
        if clause.type != "import_clause":
            continue
        for c in _named(clause):
            if c.type == "identifier":
                out.append((_text(c), spec, "default"))
            elif c.type == "namespace_import":
                # `import * as X` — tree-sitter puts X as a plain child, not a
                # `name` field, so reading the field bound nothing and every name
                # reached through the namespace became a blind spot.
                nm = c.child_by_field_name("name") or next(
                    (x for x in _named(c) if x.type == "identifier"), None)
                if nm is not None:
                    out.append((_text(nm), spec, "*"))
            elif c.type == "named_imports":
                for sp in _named(c):
                    if sp.type != "import_specifier":
                        continue
                    nm = sp.child_by_field_name("name")
                    if nm is None:
                        continue
                    local = _text(sp.child_by_field_name("alias") or nm)
                    out.append((local, spec, _text(nm)))
    return out


# ------------------------------------------------------------------- extraction


@dataclass
class _Scope:
    caller: str = ""      # the symbol an edge from here is attributed to
    cls: str = ""         # its class, for `this.member`
    locals: set[str] = field(default_factory=set)


def extract_ts(rel: str, tree, idx: RepoIndex) -> tuple[list[Node], list[Edge],
                                                        list[Unresolved]]:
    """One file's nodes, its edges, and the uses this pass could not place.

    The three buckets and their meanings are `flash.graph.extract`'s. Called with
    an `idx` whose `files` lacks `rel`, this still answers about the file itself —
    the cross-file edges are the part that degrades.
    """
    own_ix = idx.files.get(rel) or FileIndex(rel, f"{rel}::")
    nodes, fresh = _declarations(rel, tree.root_node)
    if not own_ix.ids:
        own_ix = fresh
    ids = own_ix.ids | {n.id for n in nodes}
    lines = tree.root_node.text.split(b"\n")
    module_id = f"{rel}::"
    edges: list[Edge] = []
    unres: list[Unresolved] = []

    def line_of(n) -> int:
        return n.start_point[0] + 1

    def text_of(n) -> str:
        i = line_of(n) - 1
        return _clip(lines[i].decode("utf-8", "replace")) if 0 <= i < len(lines) else ""

    # --- imports: the file→file edges, and the table the names resolve through
    imports: dict[str, tuple[str, str]] = {}     # local -> (specifier, imported)
    imports_line: dict[str, int] = {}
    seen: dict[str, tuple[int, str]] = {}
    for stmt in _named(tree.root_node):
        if stmt.type != "import_statement":
            continue
        rows = _import_rows(stmt)
        for local, spec, sym in rows:
            imports[local] = (spec, sym)
            imports_line[local] = line_of(stmt)
            seen.setdefault(spec, (line_of(stmt), text_of(stmt)))
    for spec, (line, text) in seen.items():
        dst = idx.place(spec, rel)
        if dst:
            edges.append(Edge(module_id, f"{dst}::", "imports", rel, line, text,
                              "ts-import"))
        else:
            unres.append(Unresolved(rel, line, spec, _missing_reason(spec)))
    for local, (spec, sym) in imports.items():
        # A name from a package outside the tree cannot reach a node, so it is
        # bound as a local: no edge, and no blind spot on every use of it.
        dst = idx.place(spec, rel)
        if not dst:
            own_ix.exports.pop(local, None)
            _EXTERNAL_LOCALS.setdefault(rel, set()).add(local)
            continue
        tix = idx.files[dst]
        if sym not in ("*", "default") and sym not in tix.exports \
                and not tix.follows_star and tix.ids:
            # A broken import is a fact about the statement, not about the use, so
            # it is reported once here rather than once per call site.
            unres.append(Unresolved(rel, imports_line.get(local, 1), local,
                                    f"{spec} does not export {sym!r}"))

    ext_locals = _EXTERNAL_LOCALS.get(rel, set())

    def _namespace(node) -> bool:
        """Whether this identifier is `import * as X` of a file in this tree."""
        hit = imports.get(_text(node))
        return bool(hit) and hit[1] == "*" and idx.place(hit[0], rel) != ""

    def resolve(name: str, stack: list[_Scope]) -> tuple[str, str, str]:
        """(node id, via, why-not) for one written name."""
        for sc in reversed(stack):
            if name in sc.locals:
                if sc.cls:
                    cand = f"{rel}::{_join(sc.cls, name)}"
                    if cand in ids:
                        return cand, "ts-own-scope", ""
                if not sc.caller:
                    continue
                return "", "", "local"
        if f"{rel}::{name}" in ids:
            return f"{rel}::{name}", "ts-own-scope", ""
        hit = imports.get(name)
        if hit:
            spec, sym = hit
            dst = idx.place(spec, rel)
            if not dst:
                return "", "", "outside"
            tix = idx.files.get(dst)
            if tix is None:
                return "", "", "outside"
            if sym == "default":
                s = tix.exports.get("default")
                return ((f"{dst}::{s}", "ts-default-export", "") if s
                        else (f"{dst}::", "ts-import-file", ""))
            s = tix.exports.get(sym)
            if s and f"{dst}::{s}" in tix.ids:
                return f"{dst}::{s}", "ts-import", ""
            if sym in tix.reexports:
                return "", "", "reexport"
            return ("", "", "star" if tix.follows_star else "not-exported")
        if name in _GLOBALS or name in _TS_LIB or name in ext_locals:
            return "", "", "global"
        cand = idx.unique_export(name)
        if cand:
            target = f"{cand}::{idx.files[cand].exports[name]}"
            if target in idx.files[cand].ids:
                return target, "ts-unique-export", ""
        return "", "", "unknown"

    def add(caller: str, site, kind_edge: str, name: str, stack: list[_Scope],
            via_hint: str = "") -> None:
        src = f"{rel}::{caller}" if caller else module_id
        line, text = line_of(site), text_of(site)
        if "." in name or name.startswith("this."):
            head, _, tail = name.rpartition(".")
            if head == "this" or (head == "" and name.startswith("this")):
                owner = stack[-1].cls if stack else ""
                cand = f"{rel}::{_join(owner, tail or name.split('.')[-1])}"
                if owner and cand in ids:
                    edges.append(Edge(src, cand, kind_edge, rel, line, text,
                                      "ts-own-scope"))
                else:
                    unres.append(Unresolved(rel, line, name,
                                            "member this class does not define"))
                return
            hit = imports.get(head)
            target = ""
            if hit:
                spec, _sym = hit
                dst = idx.place(spec, rel)
                if dst:
                    s = idx.files[dst].exports.get(tail)
                    if s and f"{dst}::{s}" in idx.files[dst].ids:
                        target = f"{dst}::{s}"
            elif f"{rel}::{name}" in ids:
                target = f"{rel}::{name}"
            if target:
                edges.append(Edge(src, target, kind_edge, rel, line, text,
                                  "ts-qualified-name"))
            elif hit and idx.place(hit[0], rel):
                unres.append(Unresolved(rel, line, name,
                                        f"{hit[0]} does not export {tail!r}"))
            elif head in _GLOBALS or head in ext_locals:
                return
            else:
                unres.append(Unresolved(rel, line, name,
                                        f"{head!r} does not resolve to a file here"))
            return
        dst, via, why = resolve(name, stack)
        if dst:
            edges.append(Edge(src, dst, kind_edge, rel, line, text,
                              via_hint or via))
        elif why == "not-exported":
            return          # the import statement itself already reported this
        elif why == "star":
            unres.append(Unresolved(rel, line, name,
                                    "reaches through `export *`, which this pass "
                                    "does not follow"))
        elif why == "reexport":
            unres.append(Unresolved(rel, line, name,
                                    "imported from a file that only re-exports it, "
                                    "one hop this pass does not follow"))
        elif why == "unknown":
            # The count `Radius.summary()` calls its floor is made of exactly
            # these: a use this pass saw and could not place. Dropping them would
            # turn an incomplete answer into a tidy one.
            unres.append(Unresolved(rel, line, name,
                                    "no definition, import or platform name here "
                                    "matches it"))

    def ref_name(n) -> str:
        if n is None:
            return ""
        t = n.type
        if t in ("identifier", "type_identifier"):
            return _text(n)
        if t == "member_expression":
            base = ref_name(n.child_by_field_name("object"))
            prop = _text(n.child_by_field_name("property"))
            return f"{base}.{prop}" if base and prop else ""
        if t == "this":
            return "this"
        if t == "generic_type":
            return ref_name(n.child_by_field_name("type"))
        if t == "jsx_namespace_name":
            return _text(n)
        return ""

    def walk(node, stack: list[_Scope], skip=None) -> None:
        for c in _named(node):
            if _same(c, skip):
                continue
            t = c.type
            if t == "import_statement":
                # Already answered: the file→file edge and the binding table. A
                # name inside `import` is a declaration of a local, not a use.
                continue
            if t == "member_expression":
                obj = c.child_by_field_name("object")
                prop = c.child_by_field_name("property")
                pname = _text(prop)
                if obj is not None and obj.type == "this" and pname:
                    add(stack[-1].caller, c, "reads", f"this.{pname}", stack)
                elif obj is not None and obj.type == "member_expression" and pname:
                    base = ref_name(obj)
                    if base:
                        add(stack[-1].caller, c, "reads", f"{base}.{pname}", stack)
                        walk(obj, stack)
                elif obj is not None and obj.type == "identifier":
                    # `ns.thing()` where `ns` is `import * as ns` of a file in this
                    # tree: the head names no symbol, the tail does, so the written
                    # name has to be charged whole. For any other object the head
                    # IS the use, and charging the tail would invent a member.
                    nm = ref_name(c)
                    if nm and _namespace(obj):
                        add(stack[-1].caller, c, "reads", nm, stack)
                    else:
                        add(stack[-1].caller, obj, "reads", _text(obj), stack)
                if prop is not None and prop.type == "computed_property_name":
                    walk(prop, stack)
                continue
            if t in ("function_declaration", "generator_function_declaration",
                     "class_declaration", "abstract_class_declaration",
                     "interface_declaration", "type_alias_declaration",
                     "enum_declaration", "internal_module"):
                name = _text(c.child_by_field_name("name"))
                outer = stack[-1]
                sym = _join(outer.cls, name) if outer.cls else name
                walk(c, stack + [_Scope(sym, _cls_of(t, sym), _scope_locals(c))])
                continue
            if t == "method_definition":
                outer = stack[-1]
                sym = _join(outer.cls, _text(c.child_by_field_name("name")))
                walk(c, stack + [_Scope(sym, outer.cls, _scope_locals(c))])
                continue
            if t in ("arrow_function", "function_expression", "function"):
                walk(c, stack + [_Scope(outer_caller(stack),
                                        stack[-1].cls, _scope_locals(c))])
                continue
            if t in ("lexical_declaration", "variable_declaration"):
                # `export const scale = (n) => twice(n)` — the uses inside belong
                # to `scale`, not to the file, or the graph could not answer who
                # reaches `twice` without naming the whole module.
                names = [_text(vd.child_by_field_name("name"))
                         for vd in _walk(c, "variable_declarator")]
                caller = next((n for n in names if f"{rel}::{n}" in ids),
                              stack[-1].caller)
                for nm in names:
                    stack[-1].locals.add(nm)
                walk(c, stack + [_Scope(caller, stack[-1].cls, set(names))])
                continue
            if t == "call_expression":
                fn = c.child_by_field_name("function")
                nm = ref_name(fn)
                if nm:
                    add(stack[-1].caller, fn, "calls", nm, stack)
                for ta in _walk(c, "type_arguments"):
                    _types(ta, add, stack)
                walk(c.child_by_field_name("arguments") or c, stack)
                continue
            if t == "new_expression":
                ctor = c.child_by_field_name("constructor")
                nm = ref_name(ctor)
                if nm:
                    add(stack[-1].caller, ctor, "calls", nm, stack)
                walk(c, stack)
                continue
            if t in ("jsx_opening_element", "jsx_self_closing_element"):
                tag = c.child_by_field_name("name")
                nm = ref_name(tag)
                # A lowercase tag with no definition or import behind it is an HTML
                # element, not a component this tree exports.
                if nm and not ("." not in nm and nm[:1].islower()
                               and nm not in imports and f"{rel}::{nm}" not in ids
                               and nm not in ext_locals):
                    add(stack[-1].caller, tag, "calls", nm, stack, "ts-jsx")
                walk(c, stack, skip=tag)
                continue
            if t == "jsx_closing_element":
                continue          # the tag was already charged at the opening one
            if t in ("extends_clause", "implements_clause"):
                for tc in _named(c):
                    nm = ref_name(tc)
                    if nm:
                        add(stack[-1].caller, tc, "reads", nm, stack, "ts-heritage")
                walk(c, stack)
                continue
            if t == "nested_type_identifier":
                # `children: React.ReactNode` — one dotted type name, so charging
                # its tail alone would blame `ReactNode` for a global called React.
                nm = _dotted_type(c)
                if nm:
                    add(stack[-1].caller, c, "reads", nm, stack, "ts-type")
                continue
            if t == "type_identifier":
                if not _is_own_name(node, c):
                    nm = _text(c)
                    if nm:
                        add(stack[-1].caller, c, "reads", nm, stack, "ts-type")
                walk(c, stack)
                continue
            if t == "identifier":
                if (_is_own_name(node, c) or _is_binding(node, c)
                        or _field_of(node, c) in _NOT_USES
                        or _text(c) == "this"):
                    walk(c, stack)
                    continue
                add(stack[-1].caller, c, "reads", _text(c), stack)
                walk(c, stack)
                continue
            walk(c, stack)

    # The module scope binds nothing that shadows: a top-level name is either a
    # node of this file or an import, and both are answers the graph should give.
    walk(tree.root_node, [_Scope("", "", set())])
    err = _first_error(tree.root_node)
    if err is not None:
        unres.append(Unresolved(rel, line_of(err), "<file>",
                                "the grammar reported a syntax error here, so this "
                                "file's edges are a floor"))
    return nodes, edges, unres


def _cls_of(kind: str, sym: str) -> str:
    return sym if kind in ("class_declaration", "abstract_class_declaration") else ""


def _dotted_type(n) -> str:
    """`React.ReactNode` from a `nested_type_identifier`, as one written name."""
    mod = n.child_by_field_name("module")
    name = _text(n.child_by_field_name("name"))
    if not name:
        return ""
    if mod is None:
        return name
    head = _dotted_type(mod) if mod.type == "nested_type_identifier" else _text(mod)
    return f"{head}.{name}" if head else name


def outer_caller(stack: list[_Scope]) -> str:
    """The named symbol an anonymous function's uses belong to."""
    for sc in reversed(stack):
        if sc.caller:
            return sc.caller
    return ""


def _field_of(parent, child) -> str:
    """Which field of its parent a node is, so keys and names stop being uses."""
    if parent is None or child is None:
        return ""
    for k in ("function", "name", "alias", "property", "key", "object", "type",
              "value", "arguments", "left", "right", "source", "operator"):
        if _same(parent.child_by_field_name(k), child):
            return k
    return ""


# Roles an identifier can hold that are NOT a use of a repo symbol.
_NOT_USES = frozenset({"property", "key", "alias", "name", "type", "operator",
                       "source"})


def _is_own_name(parent, child) -> bool:
    if parent.type in ("method_definition", "function_declaration",
                       "generator_function_declaration", "class_declaration",
                       "abstract_class_declaration", "interface_declaration",
                       "type_alias_declaration", "enum_declaration",
                       "variable_declarator", "public_field_definition",
                       "field_definition", "property_signature"):
        return _same(parent.child_by_field_name("name"), child)
    return False


def _is_binding(parent, child) -> bool:
    """A shorthand pattern member binds a name rather than reading one."""
    if child.type != "identifier":
        return False
    if parent.type == "shorthand_property_identifier_pattern":
        return True
    if parent.type in ("required_parameter", "optional_parameter"):
        return _same(parent.child_by_field_name("pattern"), child)
    return False


def _first_error(node):
    if node.has_error or node.type == "ERROR":
        return node
    for c in _named(node):
        hit = _first_error(c)
        if hit is not None:
            return hit
    return None


def _types(ta, add, stack) -> None:
    for c in _walk(ta, "type_identifier"):
        add(stack[-1].caller, c, "reads", _text(c), stack, "ts-type")


def _missing_reason(spec: str) -> str:
    if spec.startswith("."):
        return ("relative import no indexed TypeScript file answers "
                "(wrong path, or the target is data rather than a module)")
    return "npm package, outside this tree"


# ------------------------------------------------------------------- the build


_EXTERNAL_LOCALS: dict[str, set[str]] = {}


def scan(root: str | Path) -> tuple[RepoIndex, dict[str, object]]:
    """Parse every TypeScript file once, so the index exists before the edges."""
    ok, why = available()
    if not ok:
        raise RuntimeError(why)
    root = Path(root)
    idx = RepoIndex()
    trees: dict[str, object] = {}
    _EXTERNAL_LOCALS.clear()
    for p in ts_files(root):
        rel = str(p.relative_to(root)).replace("\\", "/")
        try:
            tree = parse(rel, p.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:                   # noqa: BLE001
            # An unreadable or unparseable file must not vanish: its absence would
            # read as "nothing reaches this symbol" to whoever asks next.
            idx.files[rel] = FileIndex(rel, f"{rel}::")
            idx.failed[rel] = f"{type(exc).__name__}: {exc}"
            continue
        trees[rel] = tree
        _nodes, ix = _declarations(rel, tree.root_node)
        idx.files[rel] = ix
    return idx, trees


def collect(root: str | Path) -> tuple[dict[str, Node], list[Edge],
                                       list[Unresolved], dict[str, str],
                                       dict[str, float]]:
    """The second language's records, in the shapes `graph.build` folds together."""
    root = Path(root)
    t0 = time.perf_counter() * 1000.0
    idx, trees = scan(root)
    nodes: dict[str, Node] = {}
    edges: list[Edge] = []
    unres: list[Unresolved] = []
    hashes: dict[str, str] = {}
    for rel, tree in trees.items():
        ns, es, us = extract_ts(rel, tree, idx)
        for n in ns:
            nodes[n.id] = n
        edges += es
        unres += us
        p = root / rel
        hashes[rel] = hashlib.sha1(p.read_bytes()).hexdigest() if p.is_file() else ""
    for rel, why in idx.failed.items():
        unres.append(Unresolved(rel, 1, "<file>", f"not indexed: {why}"))
    return nodes, edges, unres, hashes, {
        "ts_files": float(len(trees)),
        "ts_unreadable": float(len(idx.failed)),
        "ts_cold_ms": round(time.perf_counter() * 1000.0 - t0, 1)}


def build_ts(root: str | Path) -> Graph:
    """A graph of the TypeScript files under a root, alone."""
    nodes, edges, unres, hashes, stats = collect(root)
    return Graph(nodes, edges, unres, hashes, stats)
