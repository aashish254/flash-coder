"""Symbol-precise edits — PLAN §33.1's ACT leg, SPEC R-3.2.

`flash/lsp.py` answers "where is this symbol, what is its exact range". This
module spends that knowledge: a patch names a SYMBOL (or a line range inside
one) and the applier replaces exactly the lines the AST says that symbol
owns. Nothing else in the file is re-emitted, so nothing else can drift.

Why ranges instead of text:
  * a text-guessed edit makes the model re-type a whole file, and every
    re-typed line is a chance to change something that was not the request —
    an import, a docstring, the other method in the same class;
  * the bytes outside the addressed range are copied, not regenerated — and an
    address that names a class is narrowed to the members whose bytes actually
    differ (`narrow`), because the noun in a request is not always where the
    change lives — so "did this edit touch something it shouldn't?" is
    decidable by construction, and `outside_lines()` measures the demand side
    of it: how many lines outside the symbol the change lives in were
    regenerated;
  * a refused patch costs nothing: the workspace keeps its previous content
    and the retry gets a diagnostic naming the refusal, instead of a
    half-edited file that fails for a different reason.

Refusals (all of them, in one attempt, are atomic — see `apply_patches`):
  unknown file | unknown symbol | ambiguous symbol | range outside the file
  or straddling two symbols | two patches whose ranges overlap | a patch
  whose result does not parse | a patch whose result loses the symbol it was
  addressing.

The address→range resolution is pure AST (stdlib) for Python, so this module
works with no language server and no extras; `flash.lang_ts` answers for a
`.ts`/`.tsx` address, and where that optional grammar is not installed the patch
is refused with the sentence that says how to install it, never resolved by
feeding TypeScript to `ast`. `flash.lsp` is what makes the same ranges resolvable
across files, and a patch set is per-file by construction.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: The fence tags a patch body may arrive in. A model mirrors the tag the project
#: listing uses for the file it is editing, so a `tsx` block has to read here as
#: readily as a `python` one. The tags are this tool's own languages rather than
#: any info string, so a fenced diagram or log line stays out of the protocol.
FENCE = re.compile(r"```[ \t]*(?:python|typescript|tsx|ts|jsx|js|py)?"
                   r"[ \t]*\n(.*?)```", re.DOTALL)
EDIT_MARKER = re.compile(r"^\s*\**\s*#\s*edit:\s*(?P<file>[\w./-]+)\s*::\s*"
                        r"(?P<addr>\S+?)\s*\**\s*$", re.MULTILINE)
RANGE_ADDR = re.compile(r"^[Ll](?P<a>\d+)(?:\s*-\s*[Ll]?(?P<b>\d+))?$")

#: The one character that turns an address into a request to ADD a definition.
#: R-7.15b's whole point: a symbol address is resolved against the AST, so the
#: ordinary instruction "add a function called `k_to_c`" names something the AST
#: does not have, and the correct refusal costs the turn. The prefix gives the
#: model a way to say "create" that the applier can verify, and it is a prefix
#: rather than a new header because `parse_patches` already reads any `\S+` as
#: an address — the tolerance is what let the form arrive without a parser
#: change, which is exactly why the resolution rule below has to be strict.
CREATE_PREFIX = "+"

#: The suffixes whose spans the second grammar owns. Everything else — including
#: the empty path every Python caller passes — is Python, which is what keeps the
#: published Python figures byte-identical while this module dispatches at all.
TS_SUFFIXES = (".ts", ".tsx")


def _is_ts(path: str) -> bool:
    """Whether an address names a file the second grammar owns.

    The test is on the tail of the name and nothing more: a patch carries no
    language flag, and a workspace is keyed by filename, so the name the model
    writes is the only evidence of what the file is written in.
    """
    return str(path or "").endswith(TS_SUFFIXES)


def is_python(path: str) -> bool:
    """Whether the stdlib has an opinion about this file's syntax.

    `perceive.static_check` is an `ast` pass. Run on a `.tsx` it reports a
    confident syntax error about code the other grammar is happy with, so the
    loop asks this before it checks: a false STATIC is a refusal, and a refusal
    costs a retry.
    """
    return not _is_ts(path)

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
    "* in a `.ts` or `.tsx` file it must also keep the `export` keyword the "
    "definition's own line begins with — the address spans it, so a replacement "
    "that drops it would un-export the symbol every other file imports;\n"
    "* to change a single statement inside a symbol, address the exact lines: "
    "`# edit: <file> :: L<start>-L<end>`; write that text exactly as it should "
    "appear, indentation included;\n"
    "* to ADD a function, method or class the file does not have yet, put a "
    "`+` in front of the name: `# edit: <file> :: +<NewName>`, or "
    "`+<Container>.<NewName>` for a method. Write the complete new definition "
    "and nothing else; the applier chooses where it goes — after the file's last "
    "definition, or after the container's last member — so never re-type the "
    "existing code around it. A `+` address on a name that already exists is "
    "refused, because that is a revision and must say so;\n"
    "* `# edit: <file> :: *` rewrites the whole file. Use it only when no "
    "symbol address fits — it is the thing patches exist to avoid.\n"
    "Change only what the request asks for. One patch per symbol."
)


class Refused(Exception):
    """A patch that cannot be applied. Carries the operator-facing reason."""


class LandError(Exception):
    """A verified workspace that cannot be written back. Names the key at fault
    and writes nothing at all, because half a patch set on disk is worse than
    none: the tree would no longer be the one the oracle scored."""


@dataclass
class Patch:
    file: str
    address: str
    body: str

    @property
    def kind(self) -> str:
        if self.address == "*":
            return "whole"
        if self.address.startswith(CREATE_PREFIX):
            return "create"
        if RANGE_ADDR.match(self.address):
            return "range"
        return "symbol"

    @property
    def target(self) -> str:
        """The name part of the address, with a create's `+` removed.

        Everything downstream of `parse_patches` — `find_defs`, `check_result`,
        the ambiguity sentence — wants the plain name, and only `kind` should
        have to know about the create prefix.
        """
        return (self.address[len(CREATE_PREFIX):] if self.kind == "create"
                else self.address)

    @property
    def name(self) -> str:
        return self.target.rpartition(".")[2]

    @property
    def container(self) -> str:
        return self.target.rpartition(".")[0]


@dataclass
class Def:
    """One addressable definition, with the span a patch on it owns."""
    name: str
    container: str
    start: int          # 1-based, first decorator line
    end: int            # 1-based, inclusive
    col: int            # 0-based indentation the replacement is restored to
    #: TypeScript only, and only ever True there: the span's first line is an
    #: `export` statement, so the replacement has to re-emit that keyword or the
    #: symbol stops being visible to the files that import it. Python's decorator
    #: rule has no equivalent field because `ast` puts the decorator inside the
    #: span and a decorator is not an interface.
    exported: bool = False

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
    #: The sub-ranges `apply_patches` actually spliced, or None when the patch
    #: was never run through it (then the whole owned span is assumed). An
    #: address names the NOUN in a request, which is often a class when the
    #: change lives in one method — narrowing keeps that over-wide address from
    #: re-emitting a sibling that did not change. See `narrow`.
    spans: tuple[tuple[int, int], ...] | None = None

    @property
    def regenerated(self) -> int:
        """Lines of the file whose bytes this patch wrote."""
        if self.spans is None:
            return self.end - self.start + 1
        return sum(e - s + 1 for s, e in self.spans)


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
        bits = []
        for a in self.applied:
            span = f"L{a.start}-L{a.end}"
            owned = a.end - a.start + 1
            if a.spans is not None and a.regenerated < owned:
                span += f" [{a.regenerated} of {owned} lines rewritten]"
            bits.append(f"{a.patch.file}:{a.patch.address} {span}")
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

def definitions(src: str, path: str = "") -> list[Def]:
    """Every addressable definition in one file, decorators included.

    `@property def total_cents` starts one line above `ast`'s `lineno`, and
    a patch that dropped the decorator would silently change every caller —
    so the owned span begins at the first decorator.

    `path` picks the grammar. Left empty it is Python, which is how `flash.graph`
    calls it and how every published Python count was measured; a `.ts`/`.tsx`
    name asks `flash.lang_ts` for the same file's spans, so the graph's node and
    the patch's `Def` are the two line numbers of one walk rather than two
    approximations of the same idea.
    """
    if _is_ts(path):
        return _ts_definitions(path, src)
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


def _ts_syntax(path: str, src: str) -> str:
    """The second grammar's parse verdict for a file: `""` when it is clean.

    Reached lazily because `flash.lang_ts` imports `flash.graph`, which imports
    this module at load time (R-7.7's rule for an optional grammar). A machine
    without `tree-sitter-typescript` gets a refusal that names the install, not
    an `ast` error over code that is perfectly good TypeScript.
    """
    from flash import lang_ts
    try:
        return lang_ts.syntax_error(path, src)
    except RuntimeError as exc:               # the grammar's own refusal
        raise Refused(f"{path}: {exc}") from exc


def _ts_definitions(path: str, src: str) -> list[Def]:
    """The second language's spans, or a refusal that says what is missing.

    Two reasons this cannot answer, kept apart because they need different
    sentences: the optional extra is not installed (so the fix is one `pip
    install`, and the patch must not be quietly resolved by `ast`, which finds a
    hundred syntax errors in valid TypeScript), and the file itself does not
    parse (so its spans are guesses).
    """
    from flash import lang_ts
    bad = _ts_syntax(path, src)
    if bad:
        raise Refused(f"{path} does not parse: {bad}")
    return lang_ts.definitions(path, src)


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

    defs = definitions(src, patch.file)
    if patch.kind == "create":
        return _resolve_create(patch, src, defs)
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
                      f"(it defines: {named or 'nothing'}) — to ADD a symbol that "
                      f"does not exist yet, address it as "
                      f"'{CREATE_PREFIX}{patch.address}'")
    if len(hits) > 1:
        where = ", ".join(str(d) for d in hits)
        raise Refused(f"{patch.address!r} is ambiguous in {patch.file}: {where} — "
                      f"address it as Container.name or by line range")
    d = hits[0]
    return Applied(patch, d.start, d.end, d.col, d.end - d.start + 1)


def _resolve_create(patch: Patch, src: str, defs: list[Def]) -> Applied:
    """Where a NEW definition goes, chosen by the AST and not by the patch.

    The address carries no position, because a position is the thing a model
    gets wrong and the extractor never gets wrong. So the applier owns it: a
    top-level create lands after the last top-level definition it can see, and a
    `Container.+name` create lands after that container's last member — or, for
    an empty class, inside it right after its header line, which is the one
    place a body can go and still belong to the class.

    The returned span is the pure-insertion form `splice` already reads
    (`start == end + 1`), so a create rewrites no existing line: it owns zero
    lines of the file, and `outside_lines` therefore charges it nothing. That is
    the honest accounting rather than a favour — the audit exists to catch a
    patch that re-types lines it did not need to, and an insertion does not.
    """
    n, _ = _bounds(src)
    name, container = patch.name, patch.container
    if not name or name == "*":
        raise Refused(f"'{patch.address}' names nothing to create — a create "
                      f"address is '{CREATE_PREFIX}Symbol' or "
                      f"'{CREATE_PREFIX}Container.symbol'")
    if find_defs(defs, patch.target):
        d = find_defs(defs, patch.target)[0]
        raise Refused(f"{name!r} already exists in {patch.file} at "
                      f"L{d.start}-L{d.end} — a create refuses to duplicate a "
                      f"definition, so address it without the "
                      f"'{CREATE_PREFIX}' to replace it")

    if not container:
        tops = [d for d in defs if not d.container]
        anchor = max((d.end for d in tops), default=0)
        return Applied(patch, anchor + 1, anchor, 0, 0)

    owners = [d for d in defs if d.name == container and not d.container]
    if not owners:
        named = ", ".join(sorted({d.name for d in defs if not d.container}))[:200]
        raise Refused(f"no class or container {container!r} in {patch.file} to add "
                      f"{name!r} to (its top-level definitions are: "
                      f"{named or 'nothing'})")
    if len(owners) > 1:
        raise Refused(f"{container!r} is defined more than once in {patch.file}: "
                      f"{', '.join(str(d) for d in owners)} — the create would "
                      f"have to pick one")
    owner = owners[0]
    members = [d for d in defs if d.container == container]
    if members:
        anchor = max(d.end for d in members)
        col = min(d.col for d in members)
    else:
        anchor = owner.end if owner.end > owner.start + 1 else owner.start
        col = owner.col + 4
    return Applied(patch, anchor + 1, anchor, col, 0)


def splice(src: str, start: int, end: int, replacement: str) -> str:
    """Replace lines [start..end] and copy every other line verbatim.

    `end == start - 1` replaces no line, which inserts `replacement` before
    `start` — the coordinate form `narrow` uses for a pure insertion.
    """
    _, lines = _bounds(src)
    return "\n".join(lines[:start - 1] + replacement.split("\n") + lines[end:])


def narrow(src: str, start: int, end: int, body: str) -> list[tuple[int, int, str]]:
    """The sub-splices that achieve the same file as one wide splice.

    An address names the noun in the request, and the noun is often a class
    where the change lives in one of its methods: e04's "clamp the width when
    the Box is built" was answered with `# edit: box.py :: Box` and a correct
    re-type of the whole class. Splicing that range re-emits every sibling
    method from the model's text, so a line outside the symbol the change
    belongs in has been regenerated even when it happens to come back
    identical — which is the exact drift this protocol exists to make
    impossible, and the reason R-3.2's clause 2 could not be met while the
    measure counted the addressed span.

    So the applier diffs the owned block against the replacement and splices
    only the runs whose bytes differ, copying every other line of the block
    out of the file. A sibling that really did change is still spliced, and
    still counted by `outside_lines`; a sibling that did not is not touched.
    The resulting text is byte-identical to the wide splice either way — proven
    by a selftest check, because a narrowing that changed the file would be a
    different edit than the one that was asked for.

    Runs come back in file order as (start, end, text); a run of pure insertions
    has end == start - 1, which is the form `splice` reads as "insert before
    this line".
    """
    _, lines = _bounds(src)
    block = lines[start - 1:end]
    repl = body.split("\n")
    ops = difflib.SequenceMatcher(None, block, repl, autojunk=False).get_opcodes()
    runs: list[tuple[int, int, str]] = []
    cur: list[tuple[str, int, int, int, int]] = []
    for op in list(ops) + [("equal", 0, 0, 0, 0)]:    # the sentinel flushes one
        if op[0] != "equal":
            cur.append(op)
            continue
        if cur:
            runs.append((start + cur[0][1],
                         start + cur[-1][2] - 1,
                         "\n".join(repl[cur[0][3]:cur[-1][4]])))
            cur = []
    return runs


def _touched(a: Applied, runs: list[tuple[int, int, str]]) -> tuple[tuple[int, int], ...]:
    """The line ranges a narrowed splice actually rewrote.

    A run of pure insertions (s > e) rewrites no existing line at all, so it is
    charged to the line it lands in FRONT OF, clamped into the block the patch
    owns. Two consequences, both stated rather than smoothed over: a patch that
    addresses the member itself can never be charged for its own inserted line
    (the insertion point is inside the block by construction), and an
    over-wide class-level address that appends a line to one member is charged
    the blank line it precedes — one line conservative, in the direction the
    audit exists to watch.
    """
    out: list[tuple[int, int]] = []
    for s, e, _ in runs:
        out.append((s, e) if e >= s else (min(s, a.end), min(s, a.end)))
    return tuple(out)


def _names(src: str, path: str = "") -> dict[tuple[str, str], bool]:
    """What one file defines, and whether each definition is still exported.

    A set would answer "is this name still here"; the value answers the harder
    question the TypeScript arm asks, which is "is it still visible to the file
    that imports it?" Python definitions always answer False, so the set form's
    behaviour — and every Python count — is unchanged.
    """
    return {(d.container, d.name): d.exported for d in definitions(src, path)}


def check_result(patch: Patch, before: str, after: str) -> None:
    """A patch may not break the file or lose the thing it addressed."""
    if _is_ts(patch.file):
        bad = _ts_syntax(patch.file, after)
        if bad:
            raise Refused(f"replacement does not parse: {bad}")
    else:
        try:
            ast.parse(after)
        except SyntaxError as e:
            raise Refused(f"replacement does not parse: line {e.lineno}: {e.msg}")
    if patch.kind == "create":
        # The inverse of the rule below: a create is only a create if the name
        # is there afterwards. A body that defines `format_dolar` under an
        # address saying `+format_dollar` would otherwise land a silently
        # different symbol than the one the oracle is about to be asked about.
        if not find_defs(definitions(after, patch.file), patch.target):
            raise Refused(f"the result does not define {patch.name!r}, which is "
                          f"what '{patch.address}' asked it to create")
        return
    if patch.kind != "symbol":
        return
    # a symbol patch must still define that symbol — a "fix" that renames or
    # deletes it is a different change than the one that was asked for
    hits = find_defs(definitions(before, patch.file), patch.address)
    keeps = _names(after, patch.file)
    lost = [d for d in hits if (d.container, d.name) not in keeps]
    if lost:
        raise Refused(f"replacement no longer defines {patch.address} "
                      f"(as {', '.join(str(d) for d in lost)})")
    # ...and it must still be the interface the rest of the tree imports. The
    # span an address owns begins at the `export` keyword, so a replacement that
    # re-types the definition without it leaves the file looking fine while every
    # importer breaks — TypeScript's version of dropping a decorator.
    unexported = [d for d in hits
                  if d.exported and not keeps.get((d.container, d.name))]
    if unexported:
        raise Refused(f"replacement no longer exports {patch.address} "
                      f"(as {', '.join(str(d) for d in unexported)}) — the "
                      f"definition has to begin with `export`")


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
            body = (normalise(a.patch.body, a.col)
                    if a.patch.kind in ("symbol", "create") else a.patch.body)
            if a.patch.kind == "whole":
                new = splice(new, a.start, a.end, body)
                a.spans = ((a.start, a.end),)
                continue
            if a.patch.kind == "create":
                # `resolve` handed back the pure-insertion span, so this splice
                # writes the new definition and re-copies every existing line
                # verbatim. Nothing is narrowed because nothing is replaced.
                text = body
                prior = new.split("\n")[a.end - 1] if a.end >= 1 else ""
                if prior.strip():
                    # Without this the new definition lands flush against the
                    # line above it, which parses and reads like a machine wrote
                    # it: a create is the one shape where the applier owns the
                    # whitespace, so it owns the blank lines too — two at top
                    # level under PEP 8, one where the file's own declarations
                    # are one line apart, which is how a `.tsx` is written.
                    gap = 2 if not a.col and is_python(a.patch.file) else 1
                    text = "\n" * gap + text
                new = splice(new, a.start, a.end, text)
                a.spans = ()
                continue
            runs = narrow(new, a.start, a.end, body)
            for s, e, text in sorted(runs, key=lambda x: -x[0]):
                new = splice(new, s, e, text)
            a.spans = _touched(a, runs)
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
    symbol's range", measured on what the applier actually regenerated — the
    splice itself cannot reach outside its owned range, which is exactly why
    the number is worth printing: a run that leans on `# edit: file :: *`
    scores here, not in the pass rate.

    It counts what was rewritten, not what was addressed: an address that names
    a class because the request named the class is narrowed to the members that
    differ (see `narrow`), so a sibling the model merely re-typed correctly is
    copied out of the file and scores nothing, while a sibling it changed is
    spliced and scores. Both halves are pinned by checks, because an audit that
    only ever reports zero is worse than no audit.
    """
    src = workspace.get(target.get("file", ""), "")
    if not src:
        return 0
    hits = find_defs(definitions(src, target.get("file", "")),
                     target.get("symbol", ""))
    allowed = set(range(hits[0].start, hits[0].end + 1)) if hits else set()
    total = 0
    for a in result.applied:
        if a.patch.file != target["file"]:
            continue
        if a.patch.kind == "whole":
            total += len(set(changed_lines(workspace[a.patch.file],
                                           result.files[a.patch.file]))
                         - allowed)
            continue
        touched: set[int] = set()
        for s, e in (a.spans if a.spans is not None else [(a.start, a.end)]):
            touched |= set(range(s, e + 1))
        total += len(touched - allowed)
    return total


# ------------------------------------------------------------------ prompts

def _fence_tag(fname: str) -> str:
    """The language a listing should claim for a file, or the model will not
    answer in the one the applier can read.

    `describe` is the only place the project's text reaches the patch protocol,
    and a `.tsx` shown inside a `python` fence is an invitation to write Python
    at it.
    """
    if fname.endswith(".tsx"):
        return "tsx"
    return "ts" if _is_ts(fname) else "python"


def describe(workspace: dict[str, str], max_chars: int = 6000) -> str:
    """The project as the patch protocol needs to see it: names + real text.

    Full source, not an outline — a symbol-addressed patch still has to write
    the symbol's body, and an outline would make it invent one.
    """
    parts = []
    used = 0
    for fname, src in workspace.items():
        tag = _fence_tag(fname)
        block = f"# file: {fname}\n```{tag}\n{src.rstrip()}\n```"
        if used + len(block) > max_chars:
            block = f"# file: {fname}\n```{tag}\n{src[:1200].rstrip()}\n```"
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def workspace_from_dir(root: str | Path, limit: int = 20) -> dict[str, str]:
    """A directory's source files as a patchable workspace.

    Keyed by path relative to `root`, because that is what the model has to
    write in an address, and two `__init__.py` files in one tree are not the
    same file.

    Python first, then TypeScript: on a pure-Python tree the order and the cap
    are exactly what they always were, and on a mixed one a `.tsx` is still
    addressable — which is the thing R-1.4's patch arm needs. Listing a `.tsx`
    does not need the grammar; using it needs the grammar, and asks for it by
    name if it is not installed.
    """
    from flash.lsp import python_files
    root = Path(root)
    files = list(python_files(root))
    from flash import lang_ts  # filename walk only, lazy by R-7.7
    files += lang_ts.ts_files(root)
    out = {}
    for p in files[:limit]:
        try:
            key = str(p.relative_to(root)) if root.is_dir() else p.name
            out[key] = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue
    return out


def land(root: str | Path, before: dict[str, str], after: dict[str, str],
         protected: tuple[str, ...] = ()) -> list[tuple[str, int, int]]:
    """Write a workspace back to the tree it was read from.

    Only files whose bytes differ are touched, and a file that is in `before`
    and not in `after` is left alone: the patch arm addresses symbols inside
    modules, it has no verb for deleting one, so a listing that stopped
    covering a file (the `limit` in `workspace_from_dir`, or a file that became
    unreadable) must not turn into a deletion in someone's project.

    A key that does not resolve back inside `root` raises rather than writing:
    an address is model text, and `# edit: ../../etc/hosts :: …` is the same
    string as a real one as far as `apply_patches` is concerned.

    `protected` names keys that must arrive here byte-identical — the oracle is
    one, because `workspace_from_dir` lists every Python file in the tree, the
    test file included, and a patch set that repaired a failure by editing the
    assertion is not a fix. It raises with the whole call unwritten, like the
    escape guard.

    Returns `[(rel, lines_added, lines_removed)]` for what changed, so the
    caller can print exactly what it did to the tree.
    """
    root = Path(root).resolve()
    todo: list[tuple[str, Path, int, int]] = []
    for rel, text in after.items():
        if before.get(rel) == text:
            continue
        if rel in protected:
            raise LandError(f"{rel}: the patch set changed a protected file, so "
                            "nothing was written")
        key = Path(rel)
        dest = (root / key).resolve()
        if key.is_absolute() or not dest.is_relative_to(root):
            raise LandError(f"{rel}: addresses a path outside the project "
                            f"root ({root}), so nothing was written")
        if rel not in before:
            raise LandError(f"{rel}: not a file this workspace was read from, "
                            "so nothing was written")
        ops = _opcodes(before[rel], text)
        added = sum(j2 - j1 for tag, _, _, j1, j2 in ops
                    if tag in ("insert", "replace"))
        removed = sum(i2 - i1 for tag, i1, i2, _, _ in ops
                      if tag in ("delete", "replace"))
        todo.append((rel, dest, added, removed))
    # Validated in full before the first byte: a LandError halfway through would
    # leave the tree a mixture of the patched and the scored state.
    out: list[tuple[str, int, int]] = []
    for rel, dest, added, removed in todo:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(after[rel], encoding="utf-8")
        out.append((rel, added, removed))
    return out


def _opcodes(old: str, new: str) -> list[tuple[str, int, int, int, int]]:
    # splitlines, not split("\n"): a file ending in a newline would otherwise
    # carry a trailing empty "line", and the numbers `land` returns are the ones
    # the CLI prints as lines.
    sm = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(),
                                 autojunk=False)
    return [op for op in sm.get_opcodes() if op[0] != "equal"]


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
        defs = find_defs(definitions(src, t["target"]["file"]),
                         t["target"]["symbol"])
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


_DATA_ROOT = Path(__file__).resolve().parent.parent


def run_selftest(verbose: bool = True) -> int:
    """Deterministic checks on the protocol itself — no model, no server."""
    from flash import doctor
    refused = doctor.vector_refusal("patches", _DATA_ROOT)
    if refused:
        return refused

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

    # 6b. an address WIDER than the change — e04's failure shape
    retype = (
        'class Cart:\n'
        '    TAX = 10\n\n'
        '    def __init__(self):\n'
        '        self.items = []\n\n'
        '    @property\n'
        '    def total(self):\n'
        '        return sum(self.items)\n\n'
        '    def with_tax(self, rate=None):\n'
        '        r = self.TAX if rate is None else rate\n'
        '        return cents(self.total * r / 100)'
    )
    tax = {"file": "cart.py", "symbol": "Cart.TAX"}
    wide = apply_patches(ws, [Patch("cart.py", "Cart", retype)])
    check("audit: the request named the class and the change lives in one member "
          "— a class-wide address now scores ZERO outside lines, because the "
          "applier copied every sibling it did not have to write",
          wide.ok and outside_lines(ws, wide, tax) == 0
          and wide.applied[0].spans == ((6, 6),),
          f"{outside_lines(ws, wide, tax)} line(s), spans {wide.applied[0].spans}")
    check("...and the narrowed splice writes the SAME file as the wide one: "
          "narrowing changes the tool's provenance, never the edit",
          wide.files["cart.py"] == splice(cart, 5, 17, retype),
          wide.summary())
    check("audit: the summary reports what was rewritten, not what was addressed",
          "[1 of 13 lines rewritten]" in wide.summary(), wide.summary())
    shrunk = retype.replace("    def __init__(self):\n        self.items = []",
                            "    def __init__(self): pass").replace(
        "return sum(self.items)", "return sum(self.items) + 1")
    shrink = apply_patches(ws, [Patch("cart.py", "Cart", shrunk)])
    check("audit: runs splice BOTTOM-UP, because a run that changes the line "
          "count moves every later run's coordinates under itself — three runs "
          "here, one of them two lines collapsed into one",
          shrink.ok and len(shrink.applied[0].spans) == 3
          and shrink.files["cart.py"] == splice(cart, 5, 17, shrunk),
          f"spans {shrink.applied[0].spans}")
    sibling = apply_patches(
        ws, [Patch("cart.py", "Cart",
                   retype.replace("return sum(self.items)",
                                  "return sum(self.items) + 1"))])
    check("audit: a sibling the model DID change is still spliced and still "
          "scores — an audit that can only report zero is worse than no audit",
          sibling.ok and outside_lines(ws, sibling, tax) == 1
          and sibling.applied[0].spans == ((6, 6), (13, 13)),
          f"{outside_lines(ws, sibling, tax)} line(s), "
          f"spans {sibling.applied[0].spans}")
    noop = apply_patches(ws, [Patch("cart.py", "Cart",
                                    "\n".join(cart.split("\n")[4:17]))])
    check("audit: a verbatim re-type of the addressed class splices NOTHING and "
          "scores nothing — `spans` empty is not the same fact as `spans` absent",
          noop.ok and noop.applied[0].spans == ()
          and noop.files["cart.py"] == cart
          and outside_lines(ws, noop, tax) == 0,
          f"spans {noop.applied[0].spans}")
    added = apply_patches(ws, [Patch("cart.py", "Cart.__init__",
                                     "def __init__(self):\n"
                                     "    self.items = []\n"
                                     "    self.kind = \"cart\"")])
    check("audit: a line ADDED at the end of a precisely addressed member scores "
          "nothing — the insertion point is inside the block the patch owns, so "
          "narrowing does not make the audit stricter than it was",
          added.ok and added.applied[0].spans == ((9, 9),)
          and outside_lines(ws, added, {"file": "cart.py",
                                        "symbol": "Cart.__init__"}) == 0,
          f"spans {added.applied[0].spans}")
    tail = apply_patches(ws, [Patch("cart.py", "Cart", retype.replace(
        "TAX = 10", "TAX = 8").replace(
        "        self.items = []",
        "        self.items = []\n        self.kind = \"cart\""))])
    check("audit: the same insertion under the OVER-WIDE class address is charged "
          "the blank line it precedes — one line conservative, which is the "
          "direction an audit has to err in",
          tail.ok and tail.applied[0].spans == ((10, 10),)
          and outside_lines(ws, tail, {"file": "cart.py",
                                       "symbol": "Cart.__init__"}) == 1,
          f"spans {tail.applied[0].spans}")
    newone = apply_patches(ws, [Patch("cart.py", "Cart", retype.replace(
        "TAX = 10", "TAX = 8").replace(
        "    @property\n",
        "    def shout(self):\n        return \"hi\"\n\n    @property\n"))])
    check("...and a NEW sibling method under that address scores too (it is "
          "indistinguishable from the case above at line granularity, and both "
          "score)",
          newone.ok and outside_lines(ws, newone,
                                      {"file": "cart.py",
                                       "symbol": "Cart.__init__"}) == 1,
          f"spans {newone.applied[0].spans}")

    # 7. a range patch inside a body is the most precise edit there is
    inner = apply_patches(ws, [Patch("cart.py", "L15-L15",
                             "        return cents(self.total * r / 100.0)")])
    check("range: a verbatim statement replacement keeps the file's indentation",
          inner.ok and "r / 100.0" in inner.files["cart.py"],
          "; ".join(f"{p.address} {w}" for p, w in inner.refusals)[:80])

    # 7b. CREATE: R-7.15b's shape. The address grammar could say "replace this
    # symbol" and "insert these lines inside this symbol", and between them there
    # was no way to ask for a definition the file does not have yet — so the
    # ordinary instruction "add a function" was a refusal that cost the turn.
    created = apply_patches(ws, [Patch("cart.py", "+shout",
                                       'def shout():\n    return "hi"')])
    car = created.files.get("cart.py", "")
    check("create: `# edit: f :: +Name` appends the definition after the file's "
          "last top-level one, and the result still parses",
          created.ok and car.rstrip().endswith('return "hi"')
          and len(find_defs(definitions(car), "shout")) == 1,
          "; ".join(f"{p.address} {w}" for p, w in created.refusals)[:90])
    _car = len(cart.rstrip("\n").split("\n"))
    check("create: ...as a PURE insertion — every line of the before file is "
          "still there at the same number, so nothing had to be re-typed to add "
          "the new one",
          created.ok and car.split("\n")[:_car] == cart.split("\n")[:_car]
          and created.applied[0].spans == ()
          and created.applied[0].regenerated == 0,
          f"{len(car.split(chr(10)))} lines after a {_car}-line file")
    _charged = changed_lines(cart, car)
    check("create: ...and `changed_lines` still charges the insertion point, "
          "because that is what its line means: exactly ONE line, at the very end "
          "of the before file, so nothing that already existed was re-typed. The "
          "audit that reads ZERO for a create is `outside_lines`, which works off "
          "`spans` and not off this",
          created.ok and len(_charged) == 1 and _charged[0] >= len(cart.split("\n")),
          f"changed_lines={_charged} over a "
          f"{len(cart.split(chr(10)))}-element split")
    check("create: the audit charges a create nothing, because there is no line "
          "of the target symbol it could have touched",
          created.ok and outside_lines(ws, created, {"file": "cart.py",
                                                     "symbol": "Cart.TAX"}) == 0)
    check("create: the applier supplies the blank lines above a top-level "
          "definition, so a landed create reads like hand-written code",
          car.endswith('    pass\n\n\ndef shout():\n    return "hi"') or
          "\n\n\ndef shout():" in car, repr(car[-46:]))
    check("create: the new symbol is addressable afterwards, which is what lets a "
          "later turn REVISE what this turn added",
          created.ok and len(find_defs(definitions(car), "shout")) == 1)
    mixed = apply_patches(ws, [Patch("cart.py", "Cart.TAX", "TAX = 10"),
                               Patch("cart.py", "+shout",
                                     'def shout():\n    return "hi"')])
    check("create: a create and a revision in ONE set both land — the money "
          "demo's real shape is a helper added and its caller changed",
          mixed.ok and len(mixed.applied) == 2 and "TAX = 10" in mixed.files["cart.py"]
          and 'return "hi"' in mixed.files["cart.py"], mixed.summary())
    meth = apply_patches(ws, [Patch("cart.py", "+Cart.discount",
                                    'def discount(self, pct):\n'
                                    '    return cents(self.total * pct)')])
    md = meth.files.get("cart.py", "")
    hits = find_defs(definitions(md), "Cart.discount")
    check("create: `Container.+name` lands INSIDE the container at its members' "
          "nesting, and after its last member — the only place a body can go and "
          "still be a method",
          meth.ok and len(hits) == 1 and hits[0].col == 4
          and md.index("def discount") > md.index("def with_tax"),
          str(hits[0]) if hits else meth.refusals[0][1][:80])
    empty_cls = apply_patches({"box.py": "class Box:\n    pass\n"},
                              [Patch("box.py", "+Box.side",
                                     'def side(self, n):\n    self.n = n')])
    check("create: an empty class gets its method before the `pass`, so the class "
          "body keeps owning it",
          empty_cls.ok and "def side" in empty_cls.files["box.py"]
          and empty_cls.files["box.py"].index("def side") <
          empty_cls.files["box.py"].index("    pass"),
          empty_cls.files["box.py"].replace("\n", "\\n")[:80])
    dup = apply_patches(ws, [Patch("cart.py", "+with_tax",
                                   "def with_tax(self):\n    return 1")])
    check("refuse: a create on a name that EXISTS is refused, and the sentence "
          "names the revise form — a landed create and a refused one cannot be "
          "confused, which is the pair this clause is gated on",
          not dup.ok and "already exists" in dup.refusals[0][1]
          and "without the '+'" in dup.refusals[0][1],
          dup.refusals[0][1][:80] if dup.refusals else "")
    wrong = apply_patches(ws, [Patch("cart.py", "+shout",
                                     'def shout_back():\n    return "hi"')])
    check("refuse: a create whose body defines a DIFFERENT name is refused: the "
          "address is the contract the oracle is about to test",
          not wrong.ok and "does not define 'shout'" in wrong.refusals[0][1],
          wrong.refusals[0][1][:80] if wrong.refusals else "")
    nook = apply_patches(ws, [Patch("cart.py", "+Wheel.spin",
                                    'def spin(self):\n    return 1')])
    check("refuse: a create whose container is not in the file names the "
          "containers that are",
          not nook.ok and "no class or container 'Wheel'" in nook.refusals[0][1]
          and "Cart" in nook.refusals[0][1],
          nook.refusals[0][1][:80] if nook.refusals else "")
    bare = apply_patches(ws, [Patch("cart.py", "+", "x = 1")])
    check("refuse: a bare `+` names nothing to create, and the refusal spells the "
          "form rather than leaving the model to guess it",
          not bare.ok and "+Symbol" in bare.refusals[0][1],
          bare.refusals[0][1][:80] if bare.refusals else "")
    miss = apply_patches(ws, [Patch("cart.py", "shout", 'def shout():\n    return 1')])
    check("refuse: the OTHER direction carries the remedy too — revising a symbol "
          "that is not there is told how to ADD it, which is the sentence that "
          "turns this clause's measured refusal into a next-attempt win",
          not miss.ok and "no symbol" in miss.refusals[0][1]
          and "'+shout'" in miss.refusals[0][1],
          miss.refusals[0][1][:100] if miss.refusals else "")
    check("create: the address parses with NO parser change — `+Name` was always "
          "a `\\S+`, so the tolerance that let it through is pinned here rather "
          "than discovered later",
          parse_patches("# edit: cart.py :: +shout\n"
                        '```python\ndef shout():\n    return "hi"\n```\n'
                        )[0].kind == "create")
    check("create: the PROTOCOL the model is shown actually names the form — a "
          "capability the prompt does not mention is a capability the model "
          "cannot ask for",
          ":: +" in PROTOCOL and "+<Container>" in PROTOCOL)

    voided = apply_patches(ws, [Patch("cart.py", "+net", 'def net(x):\n    return x'),
                                Patch("cart.py", "+TAX", "TAX = 1")])
    check("create: one bad create voids the whole set, like every other refusal",
          not voided.ok and voided.files == ws)

    # 8. degradation: a response with no patches at all costs the attempt, not the workspace
    empty = apply_patches(ws, [])
    check("apply: an empty patch set is a no-op, not an error",
          empty.ok and empty.files == ws and empty.applied == [])

    # 9. an ad-hoc project directory becomes a workspace keyed as the address says
    fixture = _DATA_ROOT / "benchmarks/fixtures/minishop"
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
