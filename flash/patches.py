"""Symbol-precise edits — PLAN §33.1's ACT leg, SPEC R-3.2.

`flash/lsp.py` answers "where is this symbol, what is its exact range". This
module spends that knowledge: a patch names a SYMBOL (or a line range inside
one) and the applier replaces exactly the lines the AST says that symbol
owns. Nothing else in the file is re-emitted, so nothing else can drift.

Why ranges instead of text:
  * a text-guessed edit makes the model re-type a whole file, and every
    re-typed line is a chance to change something that was not the request —
    an import, a docstring, the other method in the same class;
  * the bytes outside the addressed range are copied, not regenerated, so
    "did this edit touch something it shouldn't?" is decidable by
    construction, and `outside_lines()` measures the demand side of it: how
    many lines the model asked to change that are NOT in the symbol the
    change actually lives in;
  * a refused patch costs nothing: the workspace keeps its previous content
    and the retry gets a diagnostic naming the refusal, instead of a
    half-edited file that fails for a different reason.

Refusals (all of them, in one attempt, are atomic — see `apply_patches`):
  unknown file | unknown symbol | ambiguous symbol | range outside the file
  or straddling two symbols | two patches whose ranges overlap | a patch
  whose result does not parse | a patch whose result loses the symbol it was
  addressing.

The address→range resolution is pure AST (stdlib), so this module works with
no language server; `flash.lsp` is what makes the same ranges resolvable
across files, and a patch set is per-file by construction.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)
EDIT_MARKER = re.compile(r"^\s*\**\s*#\s*edit:\s*(?P<file>[\w./-]+)\s*::\s*"
                        r"(?P<addr>\S+?)\s*\**\s*$", re.MULTILINE)
RANGE_ADDR = re.compile(r"^[Ll](?P<a>\d+)(?:\s*-\s*[Ll]?(?P<b>\d+))?$")

PROTOCOL = (
    "Reply ONLY with patches. One patch is a header line naming what it "
    "replaces, then a fenced block with the new text:\n"
    "  # edit: <file> :: <Symbol>\n"
    "      ```python\n      <complete new definition>\n      ```\n"
    "* address a function, method or class as its bare name, or "
    "`Container.method` for a method;\n"
    "* address the SMALLEST symbol that owns the lines you need to change — a "
    "method, not the class around it, and never a whole file to change one "
    "line inside it;\n"
    "* write a symbol's replacement at column 0 — it is re-indented to the "
    "symbol's real nesting from the address, so you do not have to match the "
    "file's indentation;\n"
    "* the replacement must be the symbol's COMPLETE definition including its "
    "decorators, and must keep its name;\n"
    "* to change a single statement inside a symbol, address the exact lines: "
    "`# edit: <file> :: L<start>-L<end>`; write that text exactly as it should "
    "appear, indentation included;\n"
    "* `# edit: <file> :: *` rewrites the whole file. Use it only when no "
    "symbol address fits — it is the thing patches exist to avoid.\n"
    "Change only what the request asks for. One patch per symbol."
)


class Refused(Exception):
    """A patch that cannot be applied. Carries the operator-facing reason."""


@dataclass
class Patch:
    file: str
    address: str
    body: str

    @property
    def kind(self) -> str:
        if self.address == "*":
            return "whole"
        if RANGE_ADDR.match(self.address):
            return "range"
        return "symbol"

    @property
    def name(self) -> str:
        return self.address.rpartition(".")[2]

    @property
    def container(self) -> str:
        return self.address.rpartition(".")[0]


@dataclass
class Def:
    """One addressable definition, with the span a patch on it owns."""
    name: str
    container: str
    start: int          # 1-based, first decorator line
    end: int            # 1-based, inclusive
    col: int            # 0-based indentation the replacement is restored to

    def __str__(self) -> str:
        qual = f"{self.container}.{self.name}" if self.container else self.name
        return f"{qual} L{self.start}-L{self.end}"


@dataclass
class Applied:
    patch: Patch
    start: int
    end: int
    col: int            # 0-based nesting the replacement is restored to
    lines: int          # lines of the file this patch owns


@dataclass
class ApplyResult:
    files: dict[str, str]
    applied: list[Applied] = field(default_factory=list)
    refusals: list[tuple[Patch, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refusals

    @property
    def whole_rewrites(self) -> int:
        return sum(1 for a in self.applied if a.patch.kind == "whole")

    def summary(self) -> str:
        bits = [f"{a.patch.file}:{a.patch.address} L{a.start}-L{a.end}"
                for a in self.applied]
        return ", ".join(bits) if bits else "nothing applied"


# ------------------------------------------------------------------ parsing

def parse_patches(text: str) -> list[Patch]:
    """Read the patch protocol out of a model response.

    Tolerances mirror `flash.harness.extract_files` (the shapes models
    actually produce): the header may sit inside the fence as its first line
    or on the nearest non-empty line above it, and `**bold**` decoration
    around either is stripped. Prose between patches is ignored; a block with
    no address anywhere near it is dropped rather than guessed at.
    """
    out: list[Patch] = []
    pos = 0
    for m in FENCE.finditer(text):
        block = m.group(1)
        addr = None
        head = block.lstrip()
        hm = EDIT_MARKER.match(head)
        if hm:
            addr = hm
            block = head[hm.end():]
        else:
            above = [l for l in text[pos:m.start()].splitlines() if l.strip()]
            if above:
                addr = EDIT_MARKER.match(above[-1])
        pos = m.end()
        if addr is None:
            continue
        out.append(Patch(file=addr.group("file"), address=addr.group("addr"),
                         body=block.rstrip("\n")))
    if out:
        return out
    # fenceless responses: headers followed by raw code up to the next header
    parts = re.split(r"(?m)^\s*#\s*edit:\s*[\w./-]+\s*::\s*\S+\s*$", text)
    heads = re.findall(r"(?m)^\s*#\s*edit:\s*([\w./-]+)\s*::\s*(\S+)\s*$", text)
    for (fname, address), body in zip(heads, parts[1:]):
        lines = [l for l in body.splitlines() if not l.strip().startswith("```")]
        if "".join(lines).strip():
            out.append(Patch(file=fname, address=address,
                             body="\n".join(lines).rstrip("\n")))
    return out


# -------------------------------------------------------------- resolution

def definitions(src: str) -> list[Def]:
    """Every addressable definition in one file, decorators included.

    `@property def total_cents` starts one line above `ast`'s `lineno`, and
    a patch that dropped the decorator would silently change every caller —
    so the owned span begins at the first decorator.
    """
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return []
    out: list[Def] = []

    def walk(node, container: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                out.append(Def(child.name, container, start,
                               child.end_lineno or child.lineno, child.col_offset))
                walk(child, child.name if isinstance(child, ast.ClassDef) else container)
            elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                out.append(Def(child.target.id, container, child.lineno,
                               child.end_lineno or child.lineno, child.col_offset))
            elif isinstance(child, ast.Assign):
                for t in child.targets:
                    if isinstance(t, ast.Name):
                        out.append(Def(t.id, container, child.lineno,
                                       child.end_lineno or child.lineno,
                                       child.col_offset))
                walk(child, container)
            else:
                walk(child, container)

    walk(tree, "")
    return out


def find_defs(defs: list[Def], address: str) -> list[Def]:
    """Definitions an address names: bare name, or `Container.name`."""
    name = address.rpartition(".")[2]
    container = address.rpartition(".")[0]
    hits = [d for d in defs if d.name == name]
    if container:
        hits = [d for d in hits
                if d.container == container or d.container.endswith("." + container)
                or container.endswith("." + d.container)]
    return hits


def normalise(body: str, col: int) -> str:
    """Re-indent a column-0 replacement to the symbol's real nesting.

    The common indent is stripped first, so a body the model already indented
    correctly passes through unchanged and an over-indented one is repaired
    rather than refused. Blank lines never get padding.
    """
    lines = [l for l in body.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    base = min(indents) if indents else 0
    pad = " " * col
    return "\n".join((pad + l[base:]) if l.strip() else "" for l in lines)


def _bounds(src: str) -> tuple[int, list[str]]:
    lines = src.split("\n")
    return len(lines), lines


def resolve(patch: Patch, src: str) -> Applied:
    """The exact (start, end) lines this patch owns in `src`, 1-based inclusive.

    Raises `Refused` for every way an address can fail to name one place.
    """
    n, lines = _bounds(src)
    if patch.kind == "whole":
        return Applied(patch, 1, n, 0, n)

    defs = definitions(src)
    if patch.kind == "range":
        m = RANGE_ADDR.match(patch.address)
        a = int(m.group("a"))
        b = int(m.group("b") or m.group("a"))
        if a > b:
            raise Refused(f"L{a}-L{b} runs backwards")
        if b > n:
            raise Refused(f"L{a}-L{b} is past the end of a {n}-line file")
        inside = [d for d in defs if d.start <= a and b <= d.end]
        if not inside:
            raise Refused(f"L{a}-L{b} is not inside any symbol — a range patch "
                          f"must sit within one definition's lines")
        return Applied(patch, a, b, inside[0].col, b - a + 1)

    hits = find_defs(defs, patch.address)
    if not hits:
        named = ", ".join(sorted({d.name for d in defs}))[:200]
        raise Refused(f"no symbol {patch.address!r} in {patch.file} "
                      f"(it defines: {named or 'nothing'})")
    if len(hits) > 1:
        where = ", ".join(str(d) for d in hits)
        raise Refused(f"{patch.address!r} is ambiguous in {patch.file}: {where} — "
                      f"address it as Container.name or by line range")
    d = hits[0]
    return Applied(patch, d.start, d.end, d.col, d.end - d.start + 1)


def splice(src: str, start: int, end: int, replacement: str) -> str:
    """Replace lines [start..end] and copy every other line verbatim."""
    _, lines = _bounds(src)
    return "\n".join(lines[:start - 1] + replacement.split("\n") + lines[end:])


def _names(src: str) -> set[tuple[str, str]]:
    return {(d.container, d.name) for d in definitions(src)}


def check_result(patch: Patch, before: str, after: str) -> None:
    """A patch may not break the file or lose the thing it addressed."""
    try:
        ast.parse(after)
    except SyntaxError as e:
        raise Refused(f"replacement does not parse: line {e.lineno}: {e.msg}")
    if patch.kind != "symbol":
        return
    # a symbol patch must still define that symbol — a "fix" that renames or
    # deletes it is a different change than the one that was asked for
    lost = [d for d in find_defs(definitions(before), patch.address)
            if (d.container, d.name) not in _names(after)]
    if lost:
        raise Refused(f"replacement no longer defines {patch.address} "
                      f"(as {', '.join(str(d) for d in lost)})")


# ----------------------------------------------------------------- applying

def apply_patches(workspace: dict[str, str], patches: list[Patch]) -> ApplyResult:
    """Apply a patch set to a file set. Atomic per patch set.

    Atomicity is the whole point of keeping the workspace as state: a
    half-applied set can produce a file that does not parse, and the next
    attempt would then be repairing the tool's output rather than the
    model's change. So one refusal voids the set and the caller gets the
    list of reasons.

    Every address is resolved against the ORIGINAL file, so overlapping
    patches are detected in one coordinate system, and the splices then run
    bottom-up so an earlier replacement cannot shift a later range.
    """
    files = dict(workspace)
    applied: list[Applied] = []
    refusals: list[tuple[Patch, str]] = []

    by_file: dict[str, list[Patch]] = {}
    for p in patches:
        by_file.setdefault(p.file, []).append(p)

    staged: dict[str, tuple[str, list[Applied]]] = {}
    for fname, ps in by_file.items():
        if fname not in workspace:
            refusals.extend((p, f"{fname} is not one of the project files "
                              f"({', '.join(sorted(workspace))})") for p in ps)
            continue
        src0 = workspace[fname]
        resolved: list[Applied] = []
        for p in ps:
            try:
                a = resolve(p, src0)
            except Refused as e:
                refusals.append((p, str(e)))
                continue
            clash = next((b for b in resolved
                          if b.start <= a.end and a.start <= b.end), None)
            if clash:
                refusals.append((p, f"overlaps the patch already addressed to "
                                    f"{clash.patch.address} "
                                    f"(L{clash.start}-L{clash.end})"))
                continue
            resolved.append(a)
        if not resolved or len(resolved) != len(ps):
            continue
        new = src0
        for a in sorted(resolved, key=lambda x: -x.start):
            body = (normalise(a.patch.body, a.col) if a.patch.kind == "symbol"
                    else a.patch.body)
            new = splice(new, a.start, a.end, body)
        for a in resolved:
            try:
                check_result(a.patch, src0, new)
            except Refused as e:
                refusals.append((a.patch, str(e)))
        if len(resolved) == len(ps):
            staged[fname] = (new, resolved)

    if refusals:
        return ApplyResult(dict(workspace), [], refusals)

    for fname, (src, resolved) in staged.items():
        files[fname] = src
        applied.extend(resolved)
    return ApplyResult(files, applied, [])


# ------------------------------------------------------------------- audit

def changed_lines(before: str, after: str) -> list[int]:
    """Lines of `before` that the edit did not leave alone."""
    out: list[int] = []
    sm = difflib.SequenceMatcher(None, before.split("\n"), after.split("\n"),
                                 autojunk=False)
    for tag, a1, a2, b1, b2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.extend(range(a1 + 1, a2 + 1))
        if tag == "insert" and a1 == a2 and b1 != b2:
            out.append(a1 + 1)      # insertion point counts as a touched line
    return sorted(set(out))


def outside_lines(workspace: dict[str, str], result: ApplyResult,
                  target: dict) -> int:
    """Lines this patch set changed that are NOT inside the symbol the change
    belongs in. This is R-3.2's "never rewrite a line outside the target
    symbol's range", measured on what the model asked for — the splice itself
    cannot touch outside, which is exactly why the number is worth printing:
    a run that leans on `# edit: file :: *` scores here, not in the pass rate.
    """
    src = workspace.get(target.get("file", ""), "")
    if not src:
        return 0
    hits = find_defs(definitions(src), target.get("symbol", ""))
    allowed = set(range(hits[0].start, hits[0].end + 1)) if hits else set()
    total = 0
    for a in result.applied:
        if a.patch.file == target["file"]:
            if a.patch.kind == "whole":
                total += len(set(changed_lines(workspace[a.patch.file],
                                               result.files[a.patch.file]))
                             - allowed)
            else:
                total += len(set(range(a.start, a.end + 1)) - allowed)
    return total


# ------------------------------------------------------------------ prompts

def describe(workspace: dict[str, str], max_chars: int = 6000) -> str:
    """The project as the patch protocol needs to see it: names + real text.

    Full source, not an outline — a symbol-addressed patch still has to write
    the symbol's body, and an outline would make it invent one.
    """
    parts = []
    used = 0
    for fname, src in workspace.items():
        block = f"# file: {fname}\n```python\n{src.rstrip()}\n```"
        if used + len(block) > max_chars:
            block = f"# file: {fname}\n```python\n{src[:1200].rstrip()}\n```"
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def workspace_from_dir(root: str | Path, limit: int = 20) -> dict[str, str]:
    """A directory's Python files as a patchable workspace.

    Keyed by path relative to `root`, because that is what the model has to
    write in an address, and two `__init__.py` files in one tree are not the
    same file.
    """
    from flash.lsp import python_files
    root = Path(root)
    out = {}
    for p in python_files(root)[:limit]:
        try:
            key = str(p.relative_to(root)) if root.is_dir() else p.name
            out[key] = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return out


def edit_prompt(task: dict) -> str:
    """The shared request plus the patch protocol.

    The project listing and the test already live in `task["prompt"]`, because
    the whole-file control (R-3.2's A/B) is shown exactly the same material and
    only told a different answer format — a protocol comparison has to hold the
    input still.
    """
    return f"{task['prompt']}\n\n{PROTOCOL}"


def refusal_feedback(err: str) -> str:
    return (f"{err}\n\nYour patch set was refused, so nothing changed. "
            f"Address each patch at a symbol that exists in the file as shown "
            f"above, and replace its complete definition.")


# ------------------------------------------------------------- suite premise

def load_tasks(path: str | Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def reference_patches(task: dict) -> list[Patch]:
    """A task's reference solution as patches over the seeded workspace."""
    out = []
    for fname, body in task["fix"].items():
        out.append(Patch(file=fname, address=task["target"]["symbol"]
                         if fname == task["target"]["file"] else "*",
                         body=body))
    return out


def run_premise(tasks_path: str | Path, verbose: bool = True) -> int:
    """Every edit task must be an edit task: fails before, passes after the
    reference patch, and the reference patch fits inside one symbol.

    Runs no model — the oracle subprocess is the only thing executed. If a
    task violates this, the suite is measuring the wrong thing and says so
    here rather than in a live run's numbers.
    """
    from flash.harness import diagnose_files
    tasks = load_tasks(tasks_path)
    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))

    for t in tasks:
        tid = t["id"]
        src = t["files"].get(t["target"]["file"], "")
        ships = all(f"# file: {n}" in t["prompt"] and body.rstrip() in t["prompt"]
                    for n, body in t["files"].items())
        check(f"{tid}: the task ships its own project text in the prompt "
              "(both arms read the same input)", ships,
              f"{len(t['files'])} file(s), {len(t['prompt'])} chars")
        defs = find_defs(definitions(src), t["target"]["symbol"])
        check(f"{tid}: the target symbol exists to be addressed", bool(defs),
              str(defs[0]) if defs else f"not in {t['target']['file']}")
        ok_before, err_before = diagnose_files(dict(t["files"]), t["test"])
        check(f"{tid}: the seeded project fails the test (there is a change to make)",
              not ok_before, err_before[:60])
        res = apply_patches(dict(t["files"]), reference_patches(t))
        check(f"{tid}: the reference patch applies", res.ok,
              "; ".join(f"{p.file}:{p.address} {w}" for p, w in res.refusals)[:120])
        if res.ok:
            ok_after, err_after = diagnose_files(res.files, t["test"])
            check(f"{tid}: the reference patch passes the test", ok_after,
                  err_after[:80])
            n_out = outside_lines(dict(t["files"]), res, t["target"])
            check(f"{tid}: the reference change lives inside the target symbol",
                  n_out == 0, f"{n_out} line(s) outside")
    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\nedit premise: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


# ---------------------------------------------------------------- selftest

EDIT_TASKS = (Path(__file__).resolve().parent.parent
              / "benchmarks" / "tasks" / "edit_tasks.jsonl")


def run_wiring(verbose: bool = True, into: list | None = None) -> int:
    """The loop's patch arm end to end with no model: a scripted generator
    stands in for the sampled text, so what is under test is the wiring —
    parse, refuse, keep the workspace, feed the reason back, apply, verify.

    Two tasks are enough to cover both shapes: one that has to survive a
    refused patch to get solved, one that is solved by the first patch set.
    `into=` lets `run_selftest` fold these checks into its own tally.
    """
    import flash.loop as loop

    tasks = {t["id"]: t for t in load_tasks(EDIT_TASKS)}
    checks = into if into is not None else []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))

    seen: list[str] = []
    original = loop._generate

    def stub(responses):
        stream = iter(responses)

        def _gen(model, tokenizer, messages, max_tokens, **kw):
            seen.append(messages[-1]["content"])
            return next(stream)
        return _gen

    def code_of(files: dict[str, str]) -> str:
        return "\n\n".join(f"# file: {p}\n{c}" for p, c in files.items())

    refused = ("Here it is:\n\n# edit: rules.py :: titleCasse\n"
               "```python\ndef titleCasse(text):\n    return text\n```\n")
    fixed = ("# edit: rules.py :: title_case\n```python\n"
             "def title_case(text):\n"
             '    return " ".join(w[:1].upper() + w[1:].lower() for w in text.split())\n'
             "```\n")
    loop._generate = stub([refused, fixed])
    try:
        r = loop.solve(None, None, tasks["e01_title_case"], max_attempts=3,
                       edit=True)
    finally:
        loop._generate = original
    check("loop: a refused patch costs an attempt, not the project",
          r.solved and r.n_attempts == 2, f"{r.n_attempts} attempts")
    check("loop: the refusal reason reaches the retry",
          "no symbol" in seen[1] and "PATCH REFUSED" in seen[1],
          seen[1][:70] if len(seen) > 1 else "no retry sent")
    check("loop: the answer is the patched project, file by file",
          r.attempts[-1].code == code_of(tasks["e01_title_case"]["solution"]),
          f"{len(r.attempts[-1].code)} chars")
    check("loop: the attempt that failed is recorded as a refusal, not a test failure",
          r.attempts[0].refused == 1 and r.attempts[0].patches == 1
          and not r.attempts[0].ok)
    check("loop: the accepted attempt touched nothing outside its symbol",
          r.attempts[-1].outside == 0 and r.attempts[-1].whole == 0)

    loop._generate = stub([
        "# edit: stock.py :: Stock.LOW_TAIL\n```python\nLOW_TAIL = 12\n```\n"])
    try:
        r2 = loop.solve(None, None, tasks["e07_reorder_threshold"],
                        max_attempts=2, edit=True)
    finally:
        loop._generate = original
    task7 = tasks["e07_reorder_threshold"]
    solved7 = r2.attempts[-1].code
    check("loop: a patch on one module leaves its sibling byte-identical",
          r2.solved and r2.n_attempts == 1
          and code_of({k: v for k, v in task7["files"].items()
                       if k != "stock.py"})
          in solved7, f"{r2.n_attempts} attempts")
    check("loop: the one-symbol change reproduces the reference project",
          solved7 == code_of(task7["solution"]))

    if into is not None:
        return 0
    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\npatch wiring: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def run_selftest(verbose: bool = True) -> int:
    """Deterministic checks on the protocol itself — no model, no server."""
    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))

    cart = (
        '"""Cart maths."""\n'
        'from utils import cents\n\n\n'
        'class Cart:\n'
        '    TAX = 8\n\n'
        '    def __init__(self):\n'
        '        self.items = []\n\n'
        '    @property\n'
        '    def total(self):\n'
        '        return sum(self.items)\n\n'
        '    def with_tax(self, rate=None):\n'
        '        r = self.TAX if rate is None else rate\n'
        '        return cents(self.total * r / 100)\n\n\n'
        'def total(x):\n'
        '    return x + 1\n'
    )
    ws = {"cart.py": cart, "utils.py": "def cents(n):\n    return int(round(n))\n"}

    # 1. the AST owns the span, decorators included
    defs = definitions(cart)
    prop = find_defs(defs, "Cart.total")
    check("resolve: a decorated property's span starts at its decorator",
          len(prop) == 1 and cart.split("\n")[prop[0].start - 1].strip() == "@property",
          str(prop[0]) if prop else "not found")
    check("resolve: a class attribute is addressable",
          str(find_defs(defs, "Cart.TAX")[0]) == "Cart.TAX L6-L6",
          str(find_defs(defs, "Cart.TAX")))
    check("resolve: a bare name that lives twice is ambiguous, never guessed",
          len(find_defs(defs, "total")) == 2,
          ", ".join(str(d) for d in find_defs(defs, "total")))

    # 2. parsing both header placements
    resp = ("Sure:\n\n# edit: cart.py :: Cart.TAX\n```python\nTAX = 10\n```\n\n"
            "```python\n# edit: cart.py :: with_tax\n"
            "def with_tax(self, rate=None):\n"
            "    return cents(self.total * (self.TAX if rate is None else rate) / 100)\n"
            "```\n")
    ps = parse_patches(resp)
    check("parse: a header above the fence and inside it both read",
          len(ps) == 2 and ps[0].address == "Cart.TAX" and ps[1].file == "cart.py",
          f"{len(ps)} patches")
    check("parse: prose and unaddressed blocks are dropped, not guessed",
          parse_patches("```python\nprint(1)\n```") == [])

    # 3. a clean patch set: replaced exactly, everything else byte-identical
    res = apply_patches(ws, ps)
    check("apply: two symbols in one file both land", res.ok and len(res.applied) == 2,
          "; ".join(f"{p.file}:{w}" for p, w in res.refusals)[:160])
    after = res.files["cart.py"]
    outside_ok = (cart.split("\n")[0] == after.split("\n")[0] and
                  "self.items = []" in after and "def total(x):" in after)
    check("apply: the lines outside the addressed ranges are copied, not rewritten",
          outside_ok, "")
    method = find_defs(definitions(after), "Cart.with_tax")[0]
    check("apply: a column-0 replacement is restored to the class's nesting",
          method.col == 4 and after.split("\n")[method.start - 1].startswith("    def with_tax"),
          f"col={method.col}")
    check("apply: a method-body change keeps the method's own name",
          "return cents(" in after)

    # 4. the refusals R-3.2 names: out of range and overlapping
    bad = apply_patches(ws, [Patch("cart.py", "Cart.no_such", "x = 1")])
    check("refuse: a symbol that does not exist", not bad.ok and
          "no symbol" in bad.refusals[0][1], bad.refusals[0][1][:70])
    amb = apply_patches(ws, [Patch("cart.py", "total", "def total(x):\n    return x")])
    check("refuse: an ambiguous address names its candidates",
          not amb.ok and "ambiguous" in amb.refusals[0][1], amb.refusals[0][1][:70])
    far = apply_patches(ws, [Patch("cart.py", "L300-L305", "x = 1")])
    check("refuse: a range past the end of the file", not far.ok and
          "past the end" in far.refusals[0][1], far.refusals[0][1][:70])
    straddle = apply_patches(ws, [Patch("cart.py", "L9-L20", "pass")])
    check("refuse: a range that is not inside one symbol", not straddle.ok and
          "not inside any symbol" in straddle.refusals[0][1],
          straddle.refusals[0][1][:70])
    back = apply_patches(ws, [Patch("cart.py", "L8-L6", "pass")])
    check("refuse: a range that runs backwards", not back.ok)
    over = apply_patches(ws, [Patch("cart.py", "Cart.with_tax", "def with_tax(self):\n    return 1"),
                      Patch("cart.py", "Cart.TAX", "TAX = 5")])
    check("accept: two non-overlapping symbols in one file are not a clash",
          over.ok and len(over.applied) == 2)
    clash = apply_patches(ws, [Patch("cart.py", "Cart", "class Cart:\n    pass"),
                       Patch("cart.py", "Cart.TAX", "TAX = 5")])
    check("refuse: a patch nested inside another patch's range is a clash",
          not clash.ok and "overlap" in clash.refusals[0][1],
          clash.refusals[0][1][:70])
    check("refuse: a voided set changes nothing (a retry never repairs the tool)",
          clash.files == ws)
    ghost = apply_patches(ws, [Patch("missing.py", "x", "x = 1")])
    check("refuse: a file that is not in the project", not ghost.ok and
          "not one of the project files" in ghost.refusals[0][1],
          ghost.refusals[0][1][:70])

    # 5. a patch may not break the file or delete the symbol it addressed
    broken = apply_patches(ws, [Patch("cart.py", "Cart.TAX", "TAX = ")])
    check("refuse: a replacement that does not parse", not broken.ok and
          "does not parse" in broken.refusals[0][1], broken.refusals[0][1][:70])
    renamed = apply_patches(ws, [Patch("cart.py", "Cart.with_tax",
                               "def with_tax_changed(self):\n    return 1")])
    check("refuse: a replacement that loses the symbol it addressed",
          not renamed.ok and "no longer defines" in renamed.refusals[0][1],
          renamed.refusals[0][1][:70])
    dropped = apply_patches(ws, [Patch("cart.py", "total",
                               "def other(x):\n    return x")])
    check("refuse: the same rule at module level", not dropped.ok)

    # 6. the audit is about the demand, not the splice
    drifted = cart.replace("TAX = 8", "TAX = 10").replace("return x + 1",
                                                          "return x + 2")
    whole = apply_patches(ws, [Patch("cart.py", "*", drifted)])
    check("audit: a whole-file rewrite that also moves an unrelated line "
          "scores it as out-of-symbol editing",
          whole.ok and outside_lines(ws, whole, {"file": "cart.py",
                                                 "symbol": "Cart.TAX"}) == 1,
          f"{outside_lines(ws, whole, {'file': 'cart.py', 'symbol': 'Cart.TAX'})} lines")
    check("audit: a whole-file rewrite is reported, not silently praised",
          whole.whole_rewrites == 1, f"{whole.whole_rewrites}")
    exact = apply_patches(ws, [Patch("cart.py", "*", cart.replace("TAX = 8", "TAX = 10"))])
    check("audit: a rewrite with no collateral lines scores zero but is still "
          "counted as imprecise",
          exact.ok and outside_lines(ws, exact, {"file": "cart.py",
                                                 "symbol": "Cart.TAX"}) == 0
          and exact.whole_rewrites == 1)
    precise = apply_patches(ws, [Patch("cart.py", "Cart.TAX", "TAX = 10")])
    check("audit: a symbol patch scores zero outside lines",
          precise.ok and outside_lines(ws, precise, {"file": "cart.py",
                                                     "symbol": "Cart.TAX"}) == 0)
    check("audit: changed_lines finds the one line that differs",
          changed_lines(cart, cart.replace("TAX = 8", "TAX = 10")) == [6],
          str(changed_lines(cart, cart.replace("TAX = 8", "TAX = 10"))))

    # 7. a range patch inside a body is the most precise edit there is
    inner = apply_patches(ws, [Patch("cart.py", "L15-L15",
                             "        return cents(self.total * r / 100.0)")])
    check("range: a verbatim statement replacement keeps the file's indentation",
          inner.ok and "r / 100.0" in inner.files["cart.py"],
          "; ".join(f"{p.address} {w}" for p, w in inner.refusals)[:80])

    # 8. degradation: a response with no patches at all costs the attempt, not the workspace
    empty = apply_patches(ws, [])
    check("apply: an empty patch set is a no-op, not an error",
          empty.ok and empty.files == ws and empty.applied == [])

    # 9. an ad-hoc project directory becomes a workspace keyed as the address says
    fixture = Path(__file__).resolve().parent.parent / "benchmarks" / "fixtures" / "minishop"
    proj = workspace_from_dir(fixture)
    check("workspace: a project directory loads under its relative path",
          {"cart.py", "pricing.py"} <= set(proj) and
          all(v.startswith(('"""', 'from')) for v in proj.values()),
          ", ".join(sorted(proj)))
    src = proj.get("cart.py", "")
    a = resolve(Patch("cart.py", "Cart.total_cents",
                      "def total_cents(self):\n    return 0"), src)
    check("workspace: a minishop method is addressable in the loaded tree",
          src.split("\n")[a.start - 1].strip().startswith("def total_cents")
          and a.col == 4, f"L{a.start}-L{a.end} col={a.col}")

    run_wiring(verbose=verbose, into=checks)

    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\npatches selftest: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="python -m flash.patches")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--suite", type=str, default=None,
                    help="prove every edit task in this JSONL is a real one-symbol edit")
    ap.add_argument("--wire", action="store_true",
                    help="the loop's patch arm against a scripted generator, no model")
    args = ap.parse_args()
    if args.suite:
        raise SystemExit(run_premise(args.suite))
    if args.wire:
        raise SystemExit(run_wiring())
    raise SystemExit(run_selftest())
