"""PERCEIVE, symbol-precise (§33.1 — the Serena pattern).

`flash/perceive.py` answers "what is wrong with this text". This module
answers "where is this symbol, what calls it, and what is its exact source" —
live truth from the language server, deterministically, in milliseconds.

Division of labor (the loop's design rule, App. A):
  * AST (stdlib) DISCOVERS candidates — cheap, always works, no server.
  * LSP (jedi) RESOLVES them — cross-file definitions and real references.
  * When pylsp is missing or slow, every call degrades to its AST answer
    instead of failing (§33.9 invariant 7: fallbacks everywhere).

Nothing here writes to disk; documents are opened as virtual buffers.
"""
from __future__ import annotations

import ast
import json
import re
import select
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- AST layer

PY_EXCLUDE = re.compile(r"(^|/)(__pycache__|\.venv|\.git|node_modules|build|\.tox)(/|$)")


def python_files(root: str | Path, limit: int = 2000) -> list[Path]:
    root = Path(root)
    if root.is_file():
        return [root]
    out = []
    for p in sorted(root.rglob("*.py")):
        if PY_EXCLUDE.search(str(p)):
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


@dataclass
class Occurrence:
    """One AST site that mentions `name` (a def, call, import, attribute...)."""
    path: Path
    line: int                 # 1-based
    col: int                  # 0-based
    name: str
    kind: str                 # def | class | use | import


def _walk_name(tree: ast.AST, name: str, path: Path) -> list[Occurrence]:
    hits: list[Occurrence] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                hits.append(Occurrence(path, node.lineno, node.col_offset, name,
                                       "class" if isinstance(node, ast.ClassDef) else "def"))
        elif isinstance(node, ast.Name):
            if node.id == name:
                hits.append(Occurrence(path, node.lineno, node.col_offset, name, "use"))
        elif isinstance(node, ast.Attribute):
            if node.attr == name:
                hits.append(Occurrence(path, node.lineno, node.end_col_offset - len(name),
                                       name, "use"))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                if a.name == name or (a.asname or "") == name:
                    hits.append(Occurrence(path, node.lineno, node.col_offset, name, "import"))
    return hits


def find_occurrences(root: str | Path, name: str, limit: int = 400) -> list[Occurrence]:
    """Every syntactic mention of `name` under `root` (no server needed)."""
    out: list[Occurrence] = []
    for f in python_files(root):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        out.extend(_walk_name(tree, name, f))
        if len(out) >= limit:
            break
    return out


def definitions_ast(root: str | Path, name: str) -> list[Occurrence]:
    return [o for o in find_occurrences(root, name) if o.kind in ("def", "class", "import")]


@dataclass
class SymbolIndex:
    """name -> symbols, for the whole repo (built once, reused per retry)."""
    by_name: dict[str, list[Symbol]] = field(default_factory=dict)

    @classmethod
    def build(cls, root: str | Path, max_files: int = 400) -> "SymbolIndex":
        idx = cls()
        for f in python_files(root)[:max_files]:
            for s in ast_symbols(f):
                idx.by_name.setdefault(s.name, []).append(s)
        return idx


def _signature(s: Symbol) -> str:
    qual = f"{s.container}.{s.name}" if s.container else s.name
    tag = {"property": "  # @property — NO parens",
           "cached_property": "  # @cached_property — NO parens"}.get(s.kind, "")
    return f"{qual} [{s.kind}]{tag}"


def _kind_priority(kind: str) -> int:
    return {"property": 0, "cached_property": 0, "method": 1, "function": 1,
            "field": 2, "constant": 2}.get(kind, 3)


def _call_sites(text: str) -> set[str]:
    """Names the code CALLS: `foo(` and `.bar(` — the API surface it assumes."""
    return (set(re.findall(r"(?:^|[^.\w])(\w+)\s*\(", text, re.M)) |
            set(re.findall(r"\.(\w+)\s*\(", text))) - {"if", "for", "while",
                                                       "with", "return", "print"}


def _bound_names(code: str) -> set[str]:
    """Names the candidate code owns — a repo hint about them is noise."""
    out: set[str] = set()
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add(a.asname or a.name.split(".")[0])
    return out


def symbols_involved(index: "SymbolIndex", err: str = "", code: str = "",
                     limit: int = 3) -> list[Symbol]:
    """The repo symbols a failure actually turns on, best hint first.

    Rank 0 is the M2 r02 class: the code CALLS a name the repo also defines as
    a property — the one confusion that costs an attempt every time. Rank 1:
    the code calls a repo name it never defines (external API in use). Rank 2:
    the error text merely mentions it.

    Error text ranks LAST because the oracle's own failing-assert line is full
    of constructor names the model isn't confused about; the model's call
    sites are where the misunderstanding lives (live find on minishop).
    """
    err_words = set(re.findall(r"[A-Za-z_]\w+", err))
    calls = _call_sites(code)
    external = calls - _bound_names(code)
    scored: list[tuple[int, int, str, int, Symbol]] = []
    for name, syms in index.by_name.items():
        if name.startswith("_"):
            continue
        prop = any(s.kind in ("property", "cached_property") for s in syms)
        if name in calls and prop:
            rank = 0
        elif name in external:
            rank = 1
        elif name in err_words:
            rank = 2
        else:
            continue
        for i, s in enumerate(sorted(syms, key=lambda x: _kind_priority(x.kind))):
            scored.append((rank, _kind_priority(s.kind), name, i, s))
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    out, seen = [], set()
    for _, _, name, _, s in scored:
        key = (name, s.container)
        if key in seen or len(out) >= limit:
            continue
        seen.add(key)
        out.append(s)
    return out


def symbol_hint(root: str | Path, err: str = "", code: str = "", limit: int = 3,
                max_chars: int = 1200, index: "SymbolIndex | None" = None) -> str:
    """Feedback block: the EXACT source of the repo symbols at issue.

    `index` is the caller's, when it has one: `loop` asks this module and
    `graph.scope_hint` about the SAME ranked symbols, and the parse behind the
    index is the expensive part of both — on this repo's own 26 files the pair
    costs 33 ms shared against ~170 ms parsed twice, which
    `benchmarks/graph_perceive_check.py` prints rather than asserts.
    """
    index = index if index is not None else SymbolIndex.build(root)
    syms = symbols_involved(index, err, code, limit)
    if not syms:
        return ""
    lines = ["Symbols in play (real repo source — call exactly as defined):"]
    used = 0
    for s in syms:
        src = symbol_source(root, s.name, path=s.path, line=s.line) or ""
        block = f"# {s.path.name}:{s.line}  {_signature(s)}\n{src}"
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block) + 1
    return "\n".join(lines) if len(lines) > 1 else ""


def ast_symbols(path: str | Path) -> list[Symbol]:
    """Definitions in one file WITH their enclosing scope and exact line range.

    The container matters: `Cart.total_cents()` and `CartLine.total_cents`
    are different APIs with the same name, and that confusion is the single
    most expensive hallucination the M2 suite produced (App. A).
    """
    p = Path(path).resolve()
    try:
        src = p.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src)
    except (SyntaxError, OSError, ValueError):
        return []
    out: list[Symbol] = []

    def walk(node, container: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                deco = "property" if any(
                    (isinstance(d, ast.Name) and d.id == "property") or
                    (isinstance(d, ast.Attribute) and d.attr in ("cached_property", "property"))
                    for d in child.decorator_list) else None
                kind = deco or ("class" if isinstance(child, ast.ClassDef) else
                                ("method" if container else "function"))
                out.append(Symbol(name=child.name, kind=kind, path=p,
                                  line=child.lineno,
                                  end_line=child.end_lineno or child.lineno,
                                  container=container))
                walk(child, child.name if isinstance(child, ast.ClassDef) else container)
            elif isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name):
                        out.append(Symbol(t.id, "field" if container else "constant", p,
                                          child.lineno, child.end_lineno or child.lineno,
                                          container))
            else:
                walk(child, container)
    walk(tree, "")
    return out


# --------------------------------------------------------------- LSP client

def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _read_msg(stream) -> dict | None:
    headers: dict[bytes, bytes] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        k, _, v = line.partition(b":")
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get(b"content-length", b"0"))
    return json.loads(stream.read(n)) if n else None


def _uri_to_path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse
    p = urlparse(uri)
    return Path(unquote(p.path))


@dataclass
class Location:
    path: Path
    line: int          # 1-based
    char: int          # 0-based


@dataclass
class Symbol:
    name: str
    kind: str
    path: Path
    line: int          # 1-based, definition start
    end_line: int
    container: str = ""

    def __str__(self) -> str:
        where = f"{self.path}:{self.line}"
        qual = f"{self.container}.{self.name}" if self.container else self.name
        return f"{qual:<34} {self.kind:<10} {where}"


_SKIND = {1: "file", 2: "module", 3: "namespace", 4: "class", 5: "method",
          6: "property", 7: "field", 8: "constructor", 9: "enum",
          10: "interface", 11: "function", 12: "variable", 13: "constant",
          14: "string", 15: "number", 16: "boolean", 17: "array",
          18: "object", 19: "key", 20: "null", 21: "enum-member",
          22: "struct", 23: "event", 24: "operator", 25: "type-param"}


class Lsp:
    """One long-lived pylsp session rooted at a project directory.

    Loading once per process is the M2 rule applied to the language server:
    per-request server startup costs ~1.5s and OOM-thrashes under repetition.
    """

    def __init__(self, root: str | Path, timeout: float = 20.0):
        self.root = Path(root).resolve()
        self.timeout = timeout
        self._id = 0
        self._open: set[str] = set()
        self.proc = subprocess.Popen([sys.executable, "-m", "pylsp"],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, cwd=str(self.root))

    # -- plumbing ---------------------------------------------------------
    def _send(self, method: str, params: dict, msg_id: int | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if msg_id is not None:
            msg["id"] = msg_id
        self.proc.stdin.write(_frame(msg))
        self.proc.stdin.flush()

    def _await(self, msg_id: int) -> dict | None:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if not select.select([self.proc.stdout], [], [], 0.2)[0]:
                continue
            msg = _read_msg(self.proc.stdout)
            if msg is None:
                return None
            if msg.get("id") == msg_id:
                return msg
        return None

    def _request(self, method: str, params: dict):
        self._id += 1
        self._send(method, params, msg_id=self._id)
        msg = self._await(self._id)
        return (msg or {}).get("result")

    def open(self, path: str | Path) -> str:
        p = Path(path).resolve()
        uri = p.as_uri()
        if uri in self._open:
            return uri
        self._send("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "python", "version": 1,
            "text": p.read_text(encoding="utf-8", errors="replace")}})
        self._open.add(uri)
        return uri

    def start(self) -> "Lsp":
        self._send("initialize", {
            "processId": None,
            "rootUri": self.root.as_uri(),
            "capabilities": {"textDocument": {
                "documentSymbol": {"hierarchicalWorkspaceSymbol": True},
                "definition": {}, "references": {}, "hover": {}}},
            "initializationOptions": {"jedi": {"extraPaths": [str(self.root)]},
                                      "pylsp": {"plugins": {"jedi": {
                                          "extra_arguments": [],
                                          "env_vars": {"PYTHONPATH": str(self.root)}}}}}},
            msg_id=0)
        self._await(0)
        self._send("initialized", {})
        return self

    def close(self) -> None:
        try:
            self._send("shutdown", {}, msg_id=9999)
            self._send("exit", {})
        except Exception:
            pass
        finally:
            self.proc.kill()

    def __enter__(self) -> "Lsp":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- queries ----------------------------------------------------------
    def document_symbols(self, path: str | Path) -> list[Symbol]:
        """Live outline of one file: names and kinds from the server.

        Two wire shapes show up: pylsp's FLAT list (jedi) with `location` +
        `containerName`, and the LSP-standard HIERARCHICAL list with
        `range`/`selectionRange`/`children`. Both parse here. jedi reports
        the *name* range, so end_line is the body end only where the server
        supplies one — exact bodies come from symbol_source() (AST).
        """
        p = Path(path).resolve()
        uri = self.open(p)
        res = self._request("textDocument/documentSymbol",
                            {"textDocument": {"uri": uri}}) or []
        out: list[Symbol] = []

        def walk(items, container=""):
            for it in items:
                loc = it.get("location") or {}
                sel = loc.get("range") or it.get("selectionRange") or it.get("range") or {}
                full = loc.get("range") or it.get("range") or sel
                s = sel.get("start", {}).get("line", 0)
                e = full.get("end", {}).get("line", s)
                name = it.get("name", "?")
                out.append(Symbol(name=name, kind=_SKIND.get(it.get("kind", 0), "symbol"),
                                  path=p, line=s + 1, end_line=e + 1,
                                  container=container or it.get("containerName") or ""))
                walk(it.get("children") or [], container or name)
        walk(res)
        return out

    def definition(self, path: str | Path, line: int, char: int) -> list[Location]:
        p = Path(path).resolve()
        uri = self.open(p)
        res = self._request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": char}}) or []
        return _locs(res)

    def references(self, path: str | Path, line: int, char: int,
                   include_decl: bool = True) -> list[Location]:
        p = Path(path).resolve()
        uri = self.open(p)
        res = self._request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line - 1, "character": char},
            "context": {"includeDeclaration": include_decl}}) or []
        return _locs(res)


def _locs(res) -> list[Location]:
    if isinstance(res, dict):
        res = [res]
    out = []
    for r in res:
        if "uri" not in r:
            continue
        out.append(Location(_uri_to_path(r["uri"]),
                            r["range"]["start"]["line"] + 1,
                            r["range"]["start"]["character"]))
    return out


# ---------------------------------------------------------- high-level API

def find_symbol(root: str | Path, name: str, use_lsp: bool = True) -> list[Symbol]:
    """Where is `name` defined — with container, kind and exact line range.

    The AST owns KIND (it reads the decorators; jedi's documentSymbol does
    not — live check: pylsp labels the fixture's imported names 'method' and
    reports `Cart.total_cents`, a plain method, as a property). The server
    owns RESOLUTION OUTSIDE THE TREE: when no file under `root` defines the
    name, go-to-def from a use site finds it in site-packages/stdlib instead.
    """
    root = Path(root)
    syms: list[Symbol] = []
    for f in python_files(root):
        syms.extend(s for s in ast_symbols(f) if s.name == name)
    if syms:
        return _dedup(syms)
    if not use_lsp:
        return []
    occs = sorted(find_occurrences(root, name),
                  key=lambda o: {"use": 0, "def": 1, "class": 1, "import": 2}[o.kind])
    if not occs:
        return []
    try:
        with Lsp(_project_root(root, occs)) as lsp:
            for o in occs[:4]:
                locs = lsp.definition(o.path, o.line, o.col)
                out = []
                for loc in locs:
                    out.extend(s for s in ast_symbols(loc.path)
                               if s.line == loc.line or s.name == name)
                    if not out and locs:
                        out = [Symbol(name, "external", loc.path, loc.line, loc.line)]
                if out:
                    return _dedup(out)
    except Exception:
        pass
    return []


def find_references(root: str | Path, name: str, use_lsp: bool = True,
                    max_sites: int = 8) -> list[Location]:
    """Every site that mentions `name`, resolved by the server when possible.

    The server needs a *resolved* position to answer, and jedi answers empty
    when the cursor sits on a method's `def` name (live find on the minishop
    fixture: 0 refs at cart.py:25, the complete set from cart.py:22). So call
    sites are offered before definition sites; one non-empty answer is
    already project-wide.
    """
    root = Path(root)
    occs = find_occurrences(root, name)
    if not occs:
        return []
    order = {"use": 0, "def": 1, "class": 1, "import": 2}
    occs = sorted(occs, key=lambda o: order.get(o.kind, 3))
    if use_lsp:
        try:
            with Lsp(_project_root(root, occs)) as lsp:
                for o in occs[:max_sites]:
                    locs = lsp.references(o.path, o.line, o.col)
                    if locs:
                        return _dedup_locs(locs)
        except Exception:
            pass
    return _dedup_locs([Location(o.path, o.line, o.col) for o in occs])


def symbol_source(root: str | Path, name: str, path: str | Path | None = None,
                  line: int | None = None) -> str:
    """Exact source of one symbol — the ACT upgrade (§33.1): edit the symbol,
    not a text guess. `line` disambiguates same-named symbols in one file.
    Returns '' when not found."""
    roots = [Path(path)] if path else python_files(root)
    for f in roots:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(src)
        except (SyntaxError, OSError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and node.name == name and (line is None or node.lineno == line):
                # decorators are part of the truth: dropping @property is how
                # the model learns the wrong call signature
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                body = ast.get_source_segment(src, node) or ""
                if start == node.lineno:
                    return body
                head = "\n".join(src.splitlines()[start - 1:node.lineno - 1])
                return f"{head}\n{body}"
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == name \
                            and (line is None or node.lineno == line):
                        lines = src.splitlines()
                        return "\n".join(lines[node.lineno - 1:node.end_lineno])
    return ""


def outline(root: str | Path, max_files: int = 200) -> list[Symbol]:
    """Package-wide symbol table (the cheap half of the §28 knowledge graph)."""
    out: list[Symbol] = []
    for f in python_files(root)[:max_files]:
        out.extend(ast_symbols(f))
    return sorted(out, key=lambda s: (str(s.path), s.line))


def _project_root(root: Path, occs: list) -> Path:
    """jedi resolves a package only when rooted at its PARENT directory."""
    paths = [(o.path if hasattr(o, "path") else o) for o in occs]
    if paths:
        first = max(paths, key=lambda p: len(str(p))).parent
    else:
        first = root
    for cand in [first, *first.parents]:
        if (cand / "__init__.py").exists():
            return cand.parent
    return root if root.is_dir() else root.parent


def _dedup(syms: list[Symbol]) -> list[Symbol]:
    seen: set[tuple] = set()
    out = []
    for s in syms:
        k = (str(s.path), s.line, s.name)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def _dedup_locs(locs: list[Location]) -> list[Location]:
    seen: set[tuple] = set()
    out = []
    for l in locs:
        k = (str(l.path), l.line, l.char)
        if k not in seen:
            seen.add(k)
            out.append(l)
    return sorted(out, key=lambda l: (str(l.path), l.line))


# ------------------------------------------------------------- selftest

def run_selftest(verbose: bool = True) -> int:
    """Deterministic checks on the repo itself — no model, no network.

    The assertions are written against the minishop fixture's REAL shape:
    `total_cents` exists twice, as a METHOD on Cart and a PROPERTY on
    CartLine. That pair is the hallucination that cost the most in M2
    (App. A), so if perception can't tell them apart, perception is broken.
    """
    root = Path(__file__).resolve().parent.parent
    fixture = root / "benchmarks" / "fixtures" / "minishop"
    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))

    # 1. symbol tables: containers and the @property distinction
    cart = {f.name: ast_symbols(f) for f in [fixture / "cart.py", fixture / "models.py"]}
    props = [s for syms in cart.values() for s in syms
             if s.name == "total_cents"]
    kinds = sorted(f"{s.container or '-'}::{s.kind}" for s in props)
    check("ast: total_cents defined twice, method vs property",
          kinds == ["Cart::method", "CartLine::property"], kinds)
    check("ast: definitions carry exact line ranges",
          all(s.end_line > s.line for s in props),
          ", ".join(f"{s.name} L{s.line}-L{s.end_line}" for s in props))

    # 2. symbol_source returns the executable-granularity text
    src = symbol_source(root / "flash", "solve")
    check("source: solve() extracted whole", src.startswith("def solve(")
          and "max_attempts" in src, f"{len(src.splitlines())} lines")

    # 3. outline covers the package
    syms = outline(root / "flash")
    names = {s.name for s in syms}
    check("outline: package-wide symbol table",
          {"solve", "solve_routed", "record", "load_tasks"} <= names,
          f"{len(syms)} symbols over {len({str(s.path) for s in syms})} files")

    # 4. the live server adds cross-file truth the AST cannot know
    try:
        with Lsp(root) as lsp:
            ms = lsp.document_symbols(fixture / "cart.py")
            check("lsp: documentSymbol on cart.py", len(ms) >= 3,
                  f"{len(ms)} names: " + ", ".join(f"{s.name}/{s.kind}" for s in ms[:5]))
            imp = [o for o in find_occurrences(fixture, "loyalty_discount_cents")]
            use = next((o for o in imp if o.kind == "use"), None)
            if use:
                d = lsp.definition(use.path, use.line, use.col)
                check("lsp: cross-file go-to-def lands in pricing.py",
                      any(x.path.name == "pricing.py" for x in d),
                      ", ".join(f"{x.path.name}:{x.line}" for x in d) or "none")
            refs = find_references(fixture, "subtotal_cents")
            check("lsp: project-wide references from a use site",
                  len(refs) >= 2, ", ".join(f"{l.path.name}:{l.line}" for l in refs))
    except Exception as e:
        check("lsp: server session", False, f"{type(e).__name__}: {e}")

    # 6. symbol-precise feedback (the §33.1 ACT upgrade)
    hint = symbol_hint(fixture,
                       'FAILING_ASSERT: assert line_cost(CartLine(Product("a", 2))) '
                       "| ERROR: TypeError 'int' object is not callable",
                       "def line_cost(line):\n    return line.total_cents()")
    check("hint: a property called as a method surfaces as a property",
          "@property" in hint and "CartLine.total_cents" in hint and "NO parens" in hint,
          f"{len(hint)} chars")
    check("hint: call sites outrank names that merely appear in the assert",
          hint.index("CartLine.total_cents") < hint.index("class CartLine"),
          "ordering")
    check("hint: silent when nothing repo-defined is at issue",
          symbol_hint(fixture, "KeyError: 'zzz'", "x = 1") == "")

    # 6b. THE WIRING, not the helper. R-1.1's clause is about what the MODEL is
    # shown, and every check above calls `symbol_hint` directly — so all of them
    # passed, on every run since the baseline commit 0ea2798, while `loop.solve`
    # appended the hint to the recorded Attempt and never to the retry prompt. A
    # trace of such a run looks completely convincing, because the hint IS in the
    # record, which is what `flash trace` prints. These checks drive the real loop
    # with a stubbed generator and read the messages back.
    import tempfile
    from flash import loop as _loop

    seen: list[list[dict]] = []

    def fake_generate(model, tokenizer, messages, max_tokens, **kw):
        seen.append([dict(m) for m in messages])
        if len(seen) == 1:
            return ("```python\nimport cart\n\n\ndef answer(lines):\n"
                    "    return cart.subtotal_cents(lines)\n```\n")
        return "```python\nanswer = 0\n```\n"

    wire = Path(tempfile.mkdtemp(prefix="lsp-wire-"))
    (wire / "pricing.py").write_text(
        '"""Pricing."""\nBULK_MIN_QTY = 5\n\n\n'
        'def bulk_discount_cents(price_cents, qty):\n'
        '    return price_cents * 10 if qty >= BULK_MIN_QTY else 0\n')
    bare = "NameError: name 'BULK_MIN_QTY' is not defined"
    real_gen, real_diag = _loop._generate, _loop.diagnose
    _loop._generate = fake_generate
    _loop.diagnose = lambda code, test: (False, bare)
    try:
        # (a) a task WITH repo context: the resolved source must reach the model.
        _loop.solve(None, None,
                    _loop.enrich_task({"id": "wire", "prompt": "sum the subtotal",
                                       "test": "assert False", "context": "."},
                                      wire),
                    max_attempts=2, debug=False)
        with_ctx = [dict(m) for m in seen[1]] if len(seen) > 1 else []
        seen.clear()
        # (b) a task WITHOUT it: the same machinery must add nothing at all.
        _loop.solve(None, None,
                    {"id": "bare", "prompt": "sum the subtotal",
                     "test": "assert False"},
                    max_attempts=2, debug=False)
        without_ctx = [dict(m) for m in seen[1]] if len(seen) > 1 else []
    finally:
        _loop._generate, _loop.diagnose = real_gen, real_diag
    retry = with_ctx[-1]["content"] if with_ctx else ""
    want = symbol_hint(wire, bare, _loop.extract_code(with_ctx[-2]["content"])) \
        if len(with_ctx) > 1 else ""
    check("wiring: the RETRY PROMPT the model is actually shown carries the "
          "resolved source — not only the recorded attempt, which is the failure "
          "shape that made a dead seam look live",
          "Symbols in play" in retry and "BULK_MIN_QTY = 5" in retry,
          f"{len(retry)} chars in the retry, {len(want)} of them hint")
    check("wiring: ...and what arrives is `symbol_hint`'s own output verbatim and "
          "exactly once, so the loop neither truncates it nor injects it twice",
          bool(want) and retry.count(want) == 1, f"{retry.count(want)} copies")
    check("wiring: with no repo context on the task the retry is what it always "
          "was — the hint stays silent rather than resolving against a directory "
          "the task never named",
          with_ctx and without_ctx
          and "Symbols in play" not in without_ctx[-1]["content"]
          and without_ctx[-1]["content"].count(bare) == 1,
          f"{len(without_ctx[-1]['content']) if without_ctx else 0} chars")

    # 7. degradation: no server, same answers (§33.9 invariant 7)
    fs = find_symbol(fixture, "total_cents", use_lsp=False)
    check("fallback: find_symbol works without the server",
          len(fs) == 2 and {s.kind for s in fs} == {"method", "property"},
          ", ".join(f"{s.container}.{s.name}/{s.kind}" for s in fs))
    r_ast = find_references(fixture, "subtotal_cents", use_lsp=False)
    check("fallback: find_references degrades to AST sites", len(r_ast) >= 2,
          f"{len(r_ast)} sites")

    # 8. kinds are AST truth even with the server live (jedi mislabels them)
    fs_live = find_symbol(fixture, "total_cents")
    check("truth: server on, kinds unchanged (method vs property)",
          sorted(f"{s.container}::{s.kind}" for s in fs_live) ==
          ["Cart::method", "CartLine::property"],
          ", ".join(f"{s.container}::{s.kind}" for s in fs_live))
    ext = find_symbol(fixture, "dataclass")
    check("resolve: a name defined outside the tree resolves via the server",
          any("dataclasses" in str(s.path) for s in ext),
          ", ".join(f"{s.path.name}:{s.line}" for s in ext) or "none")

    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\nlsp selftest: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(run_selftest())
