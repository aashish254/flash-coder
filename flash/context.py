"""CONTEXT — M2 repo-scale PERCEIVE: a token-budgeted project skeleton.

Real agents don't see one task — they see a codebase. This builds the
compact digest the DECIDE/ACT models get: file tree + per-file signatures
(classes, functions, args) extracted by AST — nothing imported, nothing
executed, works on any Python tree. Budgeted so the small model's context
window stays free for the actual work.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache",
              "dist", "build", ".mypy_cache", ".ruff_cache"}


@dataclass
class FileInfo:
    path: str
    lines: int
    docstring: str = ""
    imports: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)   # "def f(a, b)" / "class C"


def scan_file(path: str, root: str) -> FileInfo | None:
    rel = os.path.relpath(path, root)
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return FileInfo(path=rel, lines=0, symbols=["<unparseable>"])

    info = FileInfo(path=rel, lines=src.count("\n") + 1)
    doc = ast.get_docstring(tree)
    if doc:
        info.docstring = doc.splitlines()[0][:80]
    for node in tree.body:                      # top-level only: the API surface
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = getattr(node, "module", None) or ",".join(a.name for a in node.names[:3])
            info.imports.append(str(names).split(".")[0])
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id.isupper():
            # module constants — often the business rules (live find: r03)
            try:
                info.symbols.append(f"{node.targets[0].id} = {ast.unparse(node.value)[:40]}")
            except Exception:
                pass
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            args = ", ".join(a.arg for a in node.args.args)
            prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            info.symbols.append(f"{prefix} {node.name}({args})")
        elif isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases[:2]]
            info.symbols.append(f"class {node.name}({', '.join(bases)})" if bases
                                else f"class {node.name}")
            for item in node.body:              # one level: public methods/fields
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) \
                        and not item.target.id.startswith("_"):
                    # dataclass/attr fields — the data model (live find: r01)
                    info.symbols.append(f"  .{item.target.id}: {ast.unparse(item.annotation)}")
                elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                        and not item.name.startswith("_"):
                    args = ", ".join(a.arg for a in item.args.args if a.arg != "self")
                    decos = {ast.unparse(d) for d in item.decorator_list}
                    doc = (ast.get_docstring(item) or "").split("\n")[0][:60]
                    note = f"  # {doc}" if doc else ""
                    if "property" in decos:      # callability is semantic truth:
                        info.symbols.append(f"  .{item.name}  # @property (NO parens){note}")
                    else:
                        info.symbols.append(f"  .{item.name}({args}){note}")
    return info


def digest(root: str, max_chars: int = 4000) -> str:
    """Compact skeleton of a Python project, truncated to max_chars."""
    out: list[str] = [f"# Project skeleton: {os.path.abspath(root)}"]
    n_files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            info = scan_file(os.path.join(dirpath, fn), root)
            if info is None:
                continue
            n_files += 1
            out.append(f"\n## {info.path} ({info.lines} lines)")
            if info.docstring:
                out.append(f'"""{info.docstring}"""')
            for s in info.symbols:
                out.append(s)
            if sum(len(l) + 1 for l in out) > max_chars:
                out.append(f"\n... <truncated at {max_chars} chars>")
                return "\n".join(out)
    out.insert(1, f"# {n_files} python files")
    return "\n".join(out)
