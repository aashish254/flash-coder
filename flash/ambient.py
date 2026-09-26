"""Ambient mode (PLAN §33.5, SPEC R-7.2): work while the human is away, DRAFTS only.

The requirement is a permission boundary, not a capability. An idle window is the
only time this runs, and what it leaves behind is a *draft*: a local branch, the
`.diff` beside it, and a trace of every decision. It never pushes, never merges,
never auto-applies, and never writes a file outside its own worktree.

What makes that reviewable rather than decorative is that a draft is VERIFIED
before it is offered. So every kind of work here is expressed as a deterministic
repo check that is currently red, and the check itself is the oracle: the finding
must be gone in the worktree, and no new finding may appear there. A fix that
silences one problem by causing another is refused, and the refusal is recorded
with its reason — an ambient window that drafted nothing still leaves an
explanation for the morning.

Three checks ship, each cheap, each offline, each with a real subject in the
tree, together covering the work PLAN §33.5 says the idle cycles should spend on:
  lint      pyflakes over flash/*.py and benchmarks/*.py    ("lint drift")
  premise   every seeded suite task must still FAIL as seeded and still PASS on
            its stored reference                   ("flaky tests" / broken suites)
  drift     every module in flash/ must be named in the package's own module map,
            and no name in that map may point at a missing module
                                                        ("docs move together")

    flash ambient --dry-run                 what is red right now; touches nothing
    flash ambient --check                   may it run now? (exit 1 = no)
    flash ambient --limit 3 --small <repo>  draft, under the idle+AC gate
    flash ambient --status                  the drafts and their verdicts
    python -m flash.ambient --selftest      offline: temp repo, scripted fixes, no model
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from flash import harness, jobs, power, trace

ROOT = Path(__file__).resolve().parent.parent
# Outside every working tree, on purpose: the sandbox (worktrees, drafts, traces)
# must not be able to appear as a change in the repo it is drafting for.
AMBIENT_HOME = Path.home() / ".flash" / "ambient"
BUDGET_S = jobs.BUDGET_S

# The whole safety story is this list: git sub-commands that cannot move work off
# this machine or rewrite the user's history. `push`, `merge`, `rebase`, `reset`,
# `stash`, `clean` and `fetch` are absent by construction, and .git() refuses
# anything not named here — so reaching a remote requires editing this constant,
# which is the one change a reviewer is guaranteed to notice. `archive` is here
# because the window reads HEAD through it; it writes only the tar it is pointed
# at, and never the repository.
GIT_ALLOWED = frozenset({"rev-parse", "status", "diff", "log", "show",
                         "ls-files", "worktree", "add", "commit", "branch",
                         "checkout", "apply", "config", "archive"})

FILE_PROTOCOL = (
    "Return the COMPLETE new text of every file you change, each as a fenced "
    "block whose first line is exactly \"# file: <path>\", with <path> the path "
 "shown above. Change as little as possible. Do not explain."
)

FIXBACK = ("That change did not clear the problem, or it caused another one.\n"
           "Reason: {err}\n\n" + FILE_PROTOCOL)


@dataclass
class Finding:
    """One red check, and the files a fix may touch to make it green."""
    id: str
    kind: str
    files: list[str]
    detail: str
    prompt: str
    # An ADDITIVE finding asks for something to be put in; the answer may then
    # not take anything out. See verify()'s third clause for the measured diff
    # that made this a rule instead of a hope.
    additive: bool = False
    # True when answering the finding means ADDING something: then a draft that
    # removes any existing line is damage regardless of the check going green.
    additive: bool = False


@dataclass
class Draft:
    finding_id: str
    kind: str
    branch: str = ""
    diff: str = ""
    verified: bool = False
    attempts: int = 0
    seconds: float = 0.0
    note: str = ""

    def to_dict(self) -> dict:
        return {"finding": self.finding_id, "kind": self.kind, "branch": self.branch,
                "diff": self.diff, "verified": self.verified, "attempts": self.attempts,
                "seconds": self.seconds, "note": self.note}


# ------------------------------------------------------------------ the checks

def _pyflakes(root: Path) -> list[str]:
    """Lint findings in one tree, as 'relpath:line:col: message' lines."""
    targets = sorted(str(p) for p in (root / "flash").glob("*.py"))
    targets += sorted(str(p) for p in (root / "benchmarks").glob("*.py"))
    if not targets:
        return []
    try:
        p = subprocess.run([sys.executable, "-m", "pyflakes", *targets],
                           capture_output=True, text=True, timeout=180)
    except Exception as e:                       # pragma: no cover - tool missing
        return [f"flash/_pyflakes:0:0:{type(e).__name__}: {e}"]
    out = []
    for line in (p.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        try:
            rel = str(Path(parts[0]).resolve().relative_to(root.resolve()))
        except ValueError:
            continue
        out.append(":".join([rel, parts[1], parts[2],
                             ":".join(parts[3:]).strip()]))
    return out


def check_lint(root: Path) -> list[Finding]:
    """PLAN §33.5's "lint drift": one finding per file, so a draft stays reviewable."""
    by_file: dict[str, list[str]] = {}
    for line in _pyflakes(root):
        rel, rest = line.split(":", 1)
        by_file.setdefault(rel, []).append(rest)
    out: list[Finding] = []
    for f in sorted(by_file):
        p = root / f
        if not p.exists():
            continue
        msgs = by_file[f]
        out.append(Finding(
            id=f"lint:{f}", kind="lint", files=[f],
            detail="\n".join(msgs),
            prompt=f"pyflakes reports these problems in `{f}`:\n"
                   + "\n".join(f"  {m}" for m in msgs)
                   + "\n\nThe file is below, complete.\n\n"
                   + f"# file: {f}\n```python\n{p.read_text()}\n```\n\n"
                   + FILE_PROTOCOL))
    return out


def check_premise(root: Path) -> list[Finding]:
    """A frozen suite that no longer measures what it claims is a broken instrument.

    Every task that ships its own project text must FAIL as seeded — otherwise the
    suite scores a do-nothing answer — and must PASS on its stored reference,
    otherwise reference and test have drifted apart. Both are oracle calls on
    files already in the tree, which makes this PLAN §33.5's "flaky tests" watch
    in the only form a fully-local agent has.
    """
    out: list[Finding] = []
    tdir = root / "benchmarks" / "tasks"
    if not tdir.exists():
        return out
    for path in sorted(tdir.glob("*.jsonl")):
        rel = str(path.relative_to(root))
        for t in harness.load_tasks(path):
            if not (t.get("files") and t.get("test")):
                continue
            if harness.score_files(t["files"], t["test"]).ok:
                out.append(Finding(
                    id=f"premise:{rel}:{t['id']}:seeded-passes", kind="premise",
                    files=[rel],
                    detail=f"{t['id']}'s seeded project PASSES its own test, so the "
                           f"task no longer fails without a fix",
                    prompt=f"In the JSONL file `{rel}`, the task with id "
                           f"`{t['id']}` has stopped measuring anything: the buggy "
                           f"project in its `files` field passes that task's own "
                           f"`test`. Seed the bug back into `files` — undo the "
                           f"change its `fix` field describes — so the task fails "
                           f"as seeded and still passes on `solution`. Rewrite the "
                           f"whole file, every task row in it.\n\nThe file is "
                           f"below, complete.\n\n# file: {rel}\n```json\n"
                           f"{path.read_text()}\n```\n\n" + FILE_PROTOCOL))
            elif t.get("solution") and not harness.score_files(
                    t["solution"], t["test"]).ok:
                out.append(Finding(
                    id=f"premise:{rel}:{t['id']}:reference-fails", kind="premise",
                    files=[rel],
                    detail=f"{t['id']}'s stored reference FAILS its own test",
                    prompt=f"In `{rel}`, task `{t['id']}` ships a `solution` that "
                           f"no longer passes its own `test`. Repair that task row "
                           f"so the seeded project fails and the solution passes. "
                           f"Rewrite the whole file, every task row in it.\n\n"
                           f"# file: {rel}\n```json\n{path.read_text()}\n```\n\n"
                           + FILE_PROTOCOL))
    return out


def _docstring(root: Path) -> str:
    init = root / "flash" / "__init__.py"
    if not init.exists():
        return ""
    import ast
    try:
        return ast.get_docstring(ast.parse(init.read_text())) or ""
    except (SyntaxError, ValueError):
        return ""


def search_name(doc: str, mod: str) -> bool:
    """`flash.mod` or a backticked `mod` — the two forms the map is written in."""
    return bool(re.search(rf"(?:flash\.|`){re.escape(mod)}\b", doc))


def names_in(doc: str) -> list[str]:
    return sorted({m.group(1) for m in re.finditer(r"flash\.([a-z_][a-z0-9_]*)", doc)})


def module_blurb(root: Path, mod: str, limit: int = 180) -> str:
    """What the module says ITSELF is, in its own docstring's first line.

    Measured 2026-09-26 on the first live draft this repo accepted
    (window 20260926-213336): every line was additive, the names were in the right
    form, the oracle was green — and the descriptions were filler,
    'Ambient context processing for the coding environment', because the prompt
    asked what each module does and showed the model only the map. A draft a
    reviewer would merge quotes the module, and a module with no docstring says so
    instead of inviting a guess.
    """
    import ast
    p = root / "flash" / f"{mod}.py"
    if not p.exists():
        return ""
    try:
        doc = ast.get_docstring(ast.parse(p.read_text())) or ""
    except (SyntaxError, ValueError):
        return ""
    return next((l.strip() for l in doc.splitlines() if l.strip()), "")[:limit]


def check_drift(root: Path) -> list[Finding]:
    """Every module in flash/ named in the package map, and every name real.

    "Docs move together" is a rule PLAN §6 states and nothing enforces. As a
    check it costs one glob and one docstring, and it catches the exact mistake
    this project has kept making: a module shipped, its map line forgotten.
    """
    doc = _docstring(root)
    if not doc:
        return []
    mods = sorted(p.stem for p in (root / "flash").glob("*.py") if p.stem != "__init__")
    missing = [m for m in mods if not search_name(doc, m)]
    ghost = [n for n in names_in(doc) if n not in mods]
    init = root / "flash" / "__init__.py"
    src = init.read_text() if init.exists() else ""
    out: list[Finding] = []
    if missing:
        hints = "\n".join(
            "  flash." + m + ": " + (module_blurb(root, m)
                                     or "(no docstring to quote — say only what the "
                                        "name states, invent nothing)")
            for m in missing)
        out.append(Finding(
            id="drift:flash/__init__.py:unmapped", kind="drift",
            files=["flash/__init__.py"],
            detail="module(s) in flash/ that the package map never names: "
                   + ", ".join(missing),
            prompt=f"`flash/__init__.py` is this package's module map, and these "
                   f"files exist in `flash/` without being named in it: "
                   f"{', '.join(missing)}.\n\nAdd one entry per module to the map "
                   f"in its docstring, in the existing house style (a section tag, "
                   f"the module name, one clause saying what it does). Take that "
                   f"clause from the module's own first docstring line, quoted "
                   f"here; do not invent behaviour:\n{hints}\n\n"
                   f"Write each name in the form the map already uses, "
                   f"`flash.{missing[0]}` or "
                   f"a backticked `{'` / `'.join(missing)}`: a bare name is NOT "
                   f"read as a map entry, so the check stays red however well the "
                   f"clause is written. This is an "
                   f"ADDITIVE fix: every line already in the file must survive "
                   f"exactly as it is, so do not re-wrap, re-order, re-indent or "
                   f"delete any existing line, and add nothing but the entries "
                   f"themselves.\n\n"
                   f"# file: flash/__init__.py\n```python\n{src}\n```\n\n"
                   + FILE_PROTOCOL, additive=True))
    if ghost:
        out.append(Finding(
            id="drift:flash/__init__.py:missing-module", kind="drift",
            files=["flash/__init__.py"],
            detail="map names with no module behind them: " + ", ".join(ghost),
            prompt=f"The map in `flash/__init__.py` names modules that are not in "
                   f"`flash/`: {', '.join(ghost)}. Delete those entries and change "
                   f"nothing else.\n\n# file: flash/__init__.py\n```python\n"
                   f"{src}\n```\n\n" + FILE_PROTOCOL))
    return out


CHECKS = {"drift": check_drift, "lint": check_lint, "premise": check_premise}


def scan(root: Path) -> list[Finding]:
    """Every red check in one tree, in a fixed order so a work list repeats."""
    return [f for k in ("drift", "lint", "premise") for f in CHECKS[k](root)]


def head_tree(repo: Path, log: list[str] | None = None) -> Path:
    """HEAD's own content, unpacked into a temp dir, or `repo` if it is not a repo.

    The work list MUST be read from the same tree a draft branch is cut from, and
    that tree is HEAD. Measured 2026-09-26 on this repo with the first live
    window: the checkout held an untracked module, so `--dry-run` reported it as
    unmapped, the model dutifully mapped it inside the worktree, and the drift
    check then flagged the mapping as a name with no module behind it. The window
    was right to refuse that draft — no edit can clear a finding about a file the
    drafted tree never had. Local uncommitted work is invisible the other way
    too: an unstaged fix would hide a check that is still red at HEAD, and the
    morning draft would be for a bug the human already fixed.
    """
    if not (repo / ".git").exists():
        return repo
    d = Path(tempfile.mkdtemp(prefix="flash-ambient-head-"))
    tar = d / "HEAD.tar"
    for argv, logged, kind in ((["git", "-C", str(repo), "archive",
                                 "--format=tar", "-o", str(tar), "HEAD"],
                                "git archive HEAD", "git"),
                               (["tar", "-xf", str(tar), "-C", str(d)],
                                "tar -xf <HEAD.tar> <tempdir>", "unpack")):
        if log is not None:
            log.append((kind, logged, str(d)))
        p = subprocess.run(argv, capture_output=True, text=True)
        if p.returncode:
            shutil.rmtree(d, ignore_errors=True)
            raise Refused(f"{argv[0]} failed: {p.stderr.strip()[:200]}")
    return d


# ------------------------------------------------------------------ the sandbox

class Refused(Exception):
    """A command or a path this module will not act on. Never retried silently."""


class Sandbox:
    """One worktree per finding, plus the log every safety claim is read from.

    Every git call goes through .git(), which appends its argv to the in-memory
    log and to the session trace before running it. "0 pushes" is therefore a
    property of a file the vector reads back rather than an absence someone has
    to trust: a command that was refused shows up in `refused`, and one that was
    issued shows up in the trace.
    """

    def __init__(self, repo: Path | None = None, home: Path | None = None,
                 sid: str = "", name: str = "", keep: bool = False):
        self.repo = Path(repo or ROOT)
        self.home = Path(home or AMBIENT_HOME)
        self.sid = sid or trace.new_id("ambient")
        self.keep = keep
        self.git_log: list[str] = []
        self.writes: list[str] = []
        self.refused: list[str] = []
        # one worktree per finding: `name` keeps them apart while the drafts of a
        # window stay together in one index
        self.wt = (self.home / "worktrees" / (name or self.sid)).resolve()
        self.draft_dir = self.home / "drafts" / self.sid
        self.staged: list[str] = []

    # -- git ----------------------------------------------------------------
    def git(self, *args: str, cwd: Path | None = None) -> str:
        if not args or args[0] not in GIT_ALLOWED:
            self.refused.append("git " + " ".join(args))
            trace.event("git_refused", argv="git " + " ".join(args))
            raise Refused("git sub-command not allowed in ambient mode: "
                          f"'{args[0] if args else '(none)'}'")
        where = Path(cwd or self.wt)
        self.git_log.append("git " + " ".join(args))
        trace.event("git", argv="git " + " ".join(args), cwd=str(where))
        p = subprocess.run(["git", *args], cwd=str(where),
                           capture_output=True, text=True)
        if p.returncode:
            raise Refused(f"git {' '.join(args)} -> {p.stderr.strip()[:200]}")
        return p.stdout

    # -- files --------------------------------------------------------------
    def write(self, rel: str, text: str) -> Path:
        """Write only inside this worktree. Escaping it is a hard error."""
        dest = (self.wt / rel).resolve()
        if dest != self.wt and self.wt not in dest.parents:
            self.refused.append(f"write {rel}")
            trace.event("write_refused", path=rel)
            raise Refused(f"ambient may not write outside its worktree: {rel}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        self.writes.append(str(dest))
        return dest

    def read_safe(self, rel: str) -> str:
        p = self.wt / rel
        return p.read_text() if p.exists() else ""

    # -- lifecycle ----------------------------------------------------------
    def open(self, branch: str) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.draft_dir.mkdir(parents=True, exist_ok=True)
        self.git("worktree", "add", "-B", branch, str(self.wt), "HEAD",
                 cwd=self.repo)

    def close(self) -> None:
        if self.keep:
            return
        try:
            self.git("worktree", "remove", "--force", str(self.wt), cwd=self.repo)
        except Refused:
            pass

    def commit(self, message: str) -> str:
        self.git("add", "--", *sorted(self.staged))
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def diff(self) -> str:
        return self.git("diff", "HEAD", "--")


# -------------------------------------------------------------------- drafting

def slug(finding_id: str) -> str:
    """A finding id embeds a path (`drift:flash/__init__.py:unmapped`); a git ref
    and a filename cannot. Keep the id readable, drop the separators."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", finding_id)


def additive_kept(original: str, new: str) -> list[str]:
    """Every non-blank line the draft dropped from a file it was only allowed to
    add to, compared stripped so re-wrapping counts as a drop."""
    have = {l.strip() for l in new.splitlines() if l.strip()}
    return [l for l in original.splitlines() if l.strip() and l.strip() not in have]


def verify(sb: Sandbox, finding: Finding, before: list[str],
           original: dict[str, str]) -> tuple[bool, str]:
    """The oracle is the check itself: gone from the worktree, and nothing new.

    `before` is the finding-id set of THIS worktree before the edit. Demanding
    "nothing new" is what stops a draft from silencing one finding by breaking a
    file the first scan never reached — the classic shape of a fix that looks
    green and is not.

    The third clause exists because of a measured miss. The first live window
    (2026-09-26, Qwen2.5-Coder-7B) cleared an ADDITIVE finding — "these modules
    are not in the map" — and did it by re-wrapping every existing map line and
    appending a non-house-style block. All three original checks went green on
    that diff, so `verified` meant "the check is satisfied" while the draft was
    something no reviewer would merge: the finding was answered with collateral
    damage to 20 untouched lines. A finding that asks only to add something
    therefore may not REMOVE anything either, which is what `additive` enforces
    and what a scripted, well-behaved generator could never have surfaced. (The
    same live diff also dropped the file's trailing newline — not a line, so no
    line-level clause can see it; `carry_newline` handles that at the layer that
    loses it.)
    """
    after_findings = scan(sb.wt)
    after = [f.id for f in after_findings]
    if finding.id in after:
        # the retry is only useful if it says WHAT is still wrong: the first live
        # windows fed back "the finding is still there" three times and the model
        # re-offered the same near-miss, because a refusal with no content in it
        # is not error feedback, it is just a second no.
        still = next((f.detail for f in after_findings if f.id == finding.id), "")
        return False, (f"{finding.id} is still red after the edit — {still}"
                       if still else f"the finding is still there: {finding.id}")
    new = [i for i in after if i not in before]
    if new:
        return False, "the fix introduced new finding(s): " + ", ".join(new)
    if finding.additive:
        for f in finding.files:
            dropped = additive_kept(original.get(f, ""), sb.read_safe(f))
            if dropped:
                return False, (f"{finding.id} asks only to ADD to {f}, but the "
                               f"draft removes {len(dropped)} line(s) of it: "
                               + " | ".join(dropped[:3]))
    return True, f"cleared {finding.id}; {len(after)} finding(s) still red"


def carry_newline(original: str, text: str) -> str:
    """Put back the final newline the `# file:` fence cannot carry.

    Measured on the first ACCEPTED live draft (2026-09-26, Qwen2.5-Coder-7B, a
    4-line map, 6.5s): the map entry was exactly right and the diff still said
    "No newline at end of file". `harness.extract_files` returns '...\"\"\"' for
    both '...\"\"\"\n' and '...\"\"\"', so the protocol strips the byte on the way
    in and this is where it comes back — otherwise every ambient draft
    de-newlines every file it touches.
    """
    if original.endswith("\n") and text and not text.endswith("\n"):
        return text + "\n"
    return text


def draft_one(sb: Sandbox, finding: Finding, generate, max_attempts: int = 2,
              verbose: bool = True) -> Draft:
    """One finding -> one local branch, verified before it is offered as a draft."""
    t0 = time.perf_counter()
    branch = f"ambient/{sb.sid}/{slug(finding.id)}"
    sb.open(branch)
    d = Draft(finding_id=finding.id, kind=finding.kind, branch=branch)
    sb.staged = list(finding.files)
    before = [f.id for f in scan(sb.wt)]
    original = {f: sb.read_safe(f) for f in finding.files}
    messages = [{"role": "user", "content": finding.prompt}]
    reason = "no attempt was made"
    for attempt in range(max_attempts):
        if attempt:
            # a retry that is not told WHY it failed is a second guess, not a fix
            messages.append({"role": "user", "content": FIXBACK.format(err=reason)})
        out = generate(messages, attempt)
        files = harness.extract_files(out, expected=finding.files)
        touched = [f for f in finding.files
                   if f in files and files[f].strip() != original[f].strip()]
        if not touched:
            reason = ("the response rewrote nothing in the allowed file set, so "
                      "there is no change to verify")
            trace.event("draft_attempt", task_id=finding.id, attempt=attempt,
                        verified=False, files=[], note=reason)
            continue
        for f in finding.files:
            sb.write(f, carry_newline(original[f], files.get(f, original[f])))
        ok, note = verify(sb, finding, before, original)
        d.attempts = attempt + 1
        trace.event("draft_attempt", task_id=finding.id, attempt=attempt,
                    verified=ok, files=touched, note=note,
                    ms=round((time.perf_counter() - t0) * 1000))
        if ok:
            d.verified, d.note = True, note
            d.diff = str(sb.draft_dir / f"{slug(finding.id)}.diff")
            Path(d.diff).write_text(sb.diff())
            sb.commit(f"ambient draft {finding.id}\n\nPrepared by `flash ambient` "
                      f"in idle window {sb.sid}. Verified by re-running, inside "
                      f"this worktree only, the check that found it. Not merged, "
                      f"not pushed.")
            break
        reason = note
        for f, text in original.items():          # clean slate for the retry
            sb.write(f, text)
    if not d.verified:
        d.note = reason
        for f, text in original.items():
            sb.write(f, text)
    d.seconds = round(time.perf_counter() - t0, 1)
    with (sb.draft_dir / "index.jsonl").open("a") as fh:
        fh.write(json.dumps(d.to_dict()) + "\n")
    trace.event("draft", **d.to_dict())
    sb.close()
    if verbose:
        print(f"  [{'DRAFT' if d.verified else '  NONE'}] {finding.id}  {d.note}"
              f"  ({d.seconds}s)")
    return d


# --------------------------------------------------------------------- the run

def make_generator(small_repo: str, adapter: str | None = None,
                   max_tokens: int = 1024):
    """The live generator: one model load for a whole idle window."""
    from flash import loop
    model, tok = loop.load_model(small_repo, adapter=adapter)

    def gen(messages: list[dict], attempt: int) -> str:
        return loop._generate(model, tok, messages, max_tokens=max_tokens,
                              temp=0.0 if attempt == 0 else 0.8,
                              seed=attempt + 1, task_id="ambient",
                              attempt=attempt)
    return gen


def run(repo: Path | None = None, home: Path | None = None, generate=None,
        limit: int = 3, budget_s: float = BUDGET_S, force: bool = False,
        dry: bool = False, max_attempts: int = 2, verbose: bool = True,
        small_repo: str = "", adapter: str | None = None,
        state: "power.SystemState | None" = None, keep: bool = False) -> dict:
    """An idle-window pass. Returns the run record and prints the morning report."""
    repo = Path(repo or ROOT)
    home = Path(home or AMBIENT_HOME)
    st = state if state is not None else power.read_state()
    # jobs.eligibility's second clause (min_new ledger rows) belongs to the ROUTER
    # REFIT: do not refit on data you have already seen. Drafting has no such
    # rule — what deserves a draft is decided by a red check, not by row novelty —
    # so the novelty term is asked for by name (min_new=0) and only the power/idle
    # half of §34.3's gate is enforced here.
    ok, why = jobs.eligibility(force=force, min_new=0, trained_n=0, rows=[],
                               state=st)
    rec: dict = {"sid": trace.new_id("ambient"),
                 "gate": "open" if ok else "refused", "why": why,
                 "findings": [], "findings_seen": 0, "drafts": [], "seconds": 0.0,
                 "pushes": 0, "repo": str(repo), "home": str(home)}
    if verbose or not ok:
        print(f"[ambient] gate {'OPEN' if ok else 'REFUSED'}: "
              + ("; ".join(why) if why else "idle + AC + no shed"))
    def read_work_list() -> list[Finding]:
        """The window's work list: every red check in HEAD, in one temp tree.

        Shared by --dry-run and a live window on purpose. The two used to read
        different trees (the checkout vs the drafted HEAD), which made --dry-run
        promise work the window was then right to refuse — the bug that produced
        the first refused live draft on 2026-09-26. `trace.event` is a no-op with
        no session open, so the dry read simply leaves no trace.
        """
        log: list[tuple[str, str, str]] = []
        tree = head_tree(repo, log)
        rec["scanned"] = "HEAD" if tree != repo else "checkout (no git repo here)"
        try:
            for kind, argv, cwd in log:
                trace.event(kind, argv=argv, cwd=cwd)
            return scan(tree)
        finally:
            if tree != repo:
                shutil.rmtree(tree, ignore_errors=True)
    if dry or ok:
        red = read_work_list()
        rec["findings"] = [f.id for f in red]
        rec["findings_seen"] = len(red)
        if verbose:
            print(f"[ambient] work list read from {rec['scanned']}: local "
                  f"uncommitted work is not this window's job")
        if dry:
            for f in (red if not limit else red[:limit]):
                print(f"  [  RED ] {f.id}  "
                      f"({f.detail.splitlines()[0][:88]})")
            return rec
    if not ok:
        return rec
    if generate is None:
        generate = make_generator(small_repo, adapter=adapter)
    real_dir, trace.DIR = trace.DIR, home / "traces"
    (home / "traces").mkdir(parents=True, exist_ok=True)
    try:
        sid = trace.open_session("ambient", repo=str(repo), home=str(home),
                                 limit=limit, budget_s=budget_s, force=force,
                                 gate="open", why=why, scanned=rec["scanned"],
                                 small=small_repo or None, adapter=adapter)
        rec["sid"] = sid
        red = read_work_list()
        rec["findings"] = [f.id for f in red]
        rec["findings_seen"] = len(red)
        trace.event("findings", ids=rec["findings"], kinds=[f.kind for f in red])
        t0 = time.perf_counter()
        for f in red:
            if limit and len(rec["drafts"]) >= limit:
                break
            if time.perf_counter() - t0 >= budget_s:
                trace.event("budget", spent=round(time.perf_counter() - t0),
                            budget_s=budget_s)
                if verbose:
                    print(f"  [ BUDGET ] window spent ({budget_s:.0f}s) — the rest "
                          f"of the {rec['findings_seen']} finding(s) wait for the "
                          f"next idle window")
                break
            sb = Sandbox(repo, home, sid=sid,
                         name=f"{sid}-{len(rec['drafts'])}", keep=keep)
            rec["drafts"].append(draft_one(sb, f, generate,
                                           max_attempts=max_attempts,
                                           verbose=verbose).to_dict())
            rec.setdefault("writes", []).extend(sb.writes)
        rec["pushes"] = len(pushes_of(sid, home))
        rec["seconds"] = round(time.perf_counter() - t0, 1)
        v = sum(1 for d in rec["drafts"] if d["verified"])
        trace.close_session(drafts=v, attempted=len(rec["drafts"]),
                            findings=rec["findings_seen"], seconds=rec["seconds"])
        if verbose:
            print(f"[ambient] {v} verified draft(s) of {len(rec['drafts'])} "
                  f"attempted over {rec['findings_seen']} red check(s) in "
                  f"{rec['seconds']}s")
            print(f"  drafts: {home / 'drafts' / sid}")
            print(f"  trace : {home / 'traces' / (sid + '.jsonl')}")
            if v:
                print(f"  review: git log --oneline --glob 'ambient/{sid}/*' --all")
    finally:
        trace.DIR = real_dir
    return rec


def pushes_of(sid: str, home: Path) -> list[str]:
    """Every push the window issued, read back from its own trace."""
    return [r["argv"] for r in trace.read(sid, Path(home) / "traces")
            if r.get("type") == "git" and "push" in (r.get("argv") or "").split()]


def status(home: Path | None = None, verbose: bool = True) -> list[dict]:
    """The drafts this machine has prepared, newest idle window first."""
    d = Path(home or AMBIENT_HOME) / "drafts"
    rows: list[dict] = []
    if d.exists():
        for p in sorted(d.glob("*/index.jsonl"), reverse=True):
            for line in p.read_text().splitlines():
                if line.strip():
                    r = json.loads(line)
                    r["window"] = p.parent.name
                    rows.append(r)
    if verbose:
        if not rows:
            print("no ambient drafts yet — `flash ambient` has never closed a window")
        for r in rows:
            print(f"  [{'DRAFT' if r['verified'] else 'none '}] {r['window']} "
                  f"{r['finding']}  {r['note'][:70]}")
            if r["verified"]:
                print(f"        {r['branch']}")
    return rows


def add_flags(ap: argparse.ArgumentParser) -> None:
    """The window's flags, defined once so `flash ambient` (R-7.1's surface) and
    `python -m flash.ambient` cannot drift apart. A REMAINDER passthrough would
    have looked cleaner and does not work: argparse will not hand an option-like
    token to a positional, so `flash ambient --check` would die in the parent."""
    ap.add_argument("--limit", type=int, default=3,
                    help="drafts per idle window (0 = every red check)")
    ap.add_argument("--budget", type=float, default=BUDGET_S,
                    help="wall-clock cap for the window")
    ap.add_argument("--attempts", type=int, default=2,
                    help="generations per finding; the second is fed the refusal reason")
    ap.add_argument("--small", default="", help="model repo for a live run")
    ap.add_argument("--adapter", default=None, help="adapter name for a live run")
    ap.add_argument("--force", action="store_true",
                    help="run regardless of idle/AC (explicit foreground use)")
    ap.add_argument("--check", action="store_true", help="only: may it run now?")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what is red, touch nothing")
    ap.add_argument("--status", action="store_true", help="list prepared drafts")
    ap.add_argument("--keep-worktree", action="store_true",
                    help="leave the worktree on disk after drafting")
    ap.add_argument("--selftest", action="store_true")


def dispatch(a: argparse.Namespace) -> int:
    if a.selftest:
        return run_selftest()
    if a.status:
        return 0 if status() is not None else 1
    if a.check:
        ok, why = jobs.eligibility(force=a.force, min_new=0, trained_n=0, rows=[])
        print(("eligible: " if ok else "refused: ") + ("; ".join(why) or "gate open"))
        return 0 if ok else 1
    if not (a.dry_run or a.small):
        print("[ambient] a live window needs --small <model repo> (or --dry-run)")
        return 2
    rec = run(limit=a.limit, budget_s=a.budget, force=a.force, dry=a.dry_run,
              max_attempts=a.attempts, small_repo=a.small, adapter=a.adapter,
              keep=a.keep_worktree)
    return 0 if rec["gate"] == "open" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="flash ambient",
                                 description="PLAN §33.5 ambient mode: drafts only")
    add_flags(ap)
    return dispatch(ap.parse_args(argv))


# --------------------------------------------------------------- offline vector

def run_selftest(verbose: bool = True) -> int:
    """R-7.2's acceptance vector, in the form this box can actually run.

    SPEC's vector is an overnight run that leaves >= 1 reviewable draft plus a
    trace, 0 commits pushed, and 0 files modified outside its own worktree. The
    first is a capability claim and needs a model; the last three are PERMISSION
    claims, fully decidable offline against a temp git repo with a seeded red
    check and a scripted generator — so they are measured here, and then each
    guarantee is broken on purpose to prove the boundary is enforced rather than
    merely intended.
    """
    import tempfile
    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}"
                  + (f"  {detail}" if detail else ""))

    def state(on_ac=True, idle=600.0):
        return power.SystemState(on_ac=on_ac, battery_pct=100.0 if on_ac else 12.0,
                                 low_power_mode=False, thermal_limit_pct=100.0,
                                 thermal_warning=False, mem_free_pct=60.0,
                                 mem_total_gb=24.0, swap_used_gb=0.0,
                                 load_per_core=0.2, idle_seconds=idle, cores=8)

    def fixture(r: Path) -> Path:
        """A committed repo holding exactly the three red checks this one looks for."""
        (r / "flash").mkdir(parents=True)
        (r / "benchmarks" / "tasks").mkdir(parents=True)
        (r / "flash" / "__init__.py").write_text(
            '"""Demo package:\n'
            '  M1: flash.known    (the module the map does name)\n'
            '"""\n')
        (r / "flash" / "known.py").write_text("X = 1\n")
        (r / "flash" / "unmapped.py").write_text("import os\nY = 2\n")
        good = {"id": "t_ok", "files": {"m.py": "def f():\n    return 1\n"},
                "test": 'import sys; sys.path.insert(0, "<TMPDIR>")\n'
                        "from m import f\nassert f() == 2\n",
                "solution": {"m.py": "def f():\n    return 2\n"}}
        rotted = {"id": "t_rot", "files": {"m.py": "def f():\n    return 2\n"},
                  "test": good["test"], "solution": good["solution"]}
        (r / "benchmarks" / "tasks" / "s.jsonl").write_text(
            "\n".join(json.dumps(t) for t in (good, rotted)) + "\n")
        for cmd in (["init", "-q", "-b", "main", str(r)],
                    ["-C", str(r), "config", "user.email", "a@b.c"],
                    ["-C", str(r), "config", "user.name", "ambient-test"],
                    ["-C", str(r), "add", "-A"],
                    ["-C", str(r), "commit", "-q", "-m", "baseline"]):
            subprocess.run(["git", *cmd], check=True, capture_output=True)
        return r

    def snapshot(root: Path) -> dict:
        """path -> (size, mtime_ns) for every file outside .git and __pycache__:
        the witness that no working-tree file was touched."""
        out = {}
        for p in sorted(root.rglob("*")):
            if ".git" in p.parts or "__pycache__" in p.parts or not p.is_file():
                continue
            s = p.stat()
            out[str(p.relative_to(root))] = (s.st_size, s.st_mtime_ns)
        return out

    def porcelain(root: Path) -> str:
        return subprocess.run(["git", "-C", str(root), "status", "--porcelain"],
                              capture_output=True, text=True).stdout

    def head_of(root: Path) -> str:
        return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def gen_fix(messages, attempt):
        """A scripted model that returns the real repair for each of the three."""
        txt = messages[0]["content"]
        if "# file: flash/__init__.py" in txt:
            body = txt.split("```python\n", 1)[1].rsplit("\n```", 1)[0]
            body = body.replace(
                "  M1: flash.known    (the module the map does name)",
                "  M1: flash.known    (the module the map does name)\n"
                "  X1: flash.unmapped  (what it does)")
            return f"# file: flash/__init__.py\n```python\n{body}\n```\n"
        if "# file: flash/unmapped.py" in txt:
            body = txt.split("```python\n", 1)[1].rsplit("\n```", 1)[0]
            return (f"# file: flash/unmapped.py\n```python\n"
                    f"{body.replace('import os' + chr(10), '')}\n```\n")
        if "# file: benchmarks/tasks/s.jsonl" in txt:
            body = txt.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
            rows = [json.loads(l) for l in body.splitlines() if l.strip()]
            for row in rows:
                if row["id"] == "t_rot":
                    row["files"] = {"m.py": "def f():\n    return 1\n"}
            return ("# file: benchmarks/tasks/s.jsonl\n```json\n"
                    + "\n".join(json.dumps(r) for r in rows) + "\n```\n")
        return "no change here"

    tmp = Path(tempfile.mkdtemp())
    repo = fixture(tmp / "repo")
    home = tmp / "home"
    files_before, porcel_before = snapshot(repo), porcelain(repo)
    head_before = head_of(repo)

    ids = [f.id for f in scan(repo)]
    check("the work list IS the red checks, and all three seeded ones are found",
          ids == ["drift:flash/__init__.py:unmapped", "lint:flash/unmapped.py",
                  "premise:benchmarks/tasks/s.jsonl:t_rot:seeded-passes"], str(ids))
    check("a task whose seeded project already passes its own test is reported: the "
          "suite would score a do-nothing answer, so it measures nothing",
          ids[-1].endswith(":seeded-passes"), ids[-1])
    check("a sound task in the same file is NOT reported — the premise check "
          "discriminates instead of crying wolf",
          not any("t_ok" in i for i in ids), str(ids))
    check("the drift check names the module the map forgot rather than 'docs wrong'",
          "unmapped" in scan(repo)[0].detail, scan(repo)[0].detail)

    # ---- where the work list is read from: HEAD, never a dirty checkout
    def head_ids(r: Path) -> list[str]:
        t = head_tree(r)
        try:
            return [f.id for f in scan(t)]
        finally:
            if t != r:
                shutil.rmtree(t, ignore_errors=True)

    dirt = fixture(tmp / "repo-dirty")
    (dirt / "flash" / "untracked.py").write_text("import os\nZ = 9\n")
    (dirt / "flash" / "known.py").write_text("X = 1\n# unstaged, uncommitted\n")
    check("an untracked file that is itself red, and an unstaged edit, are NOT in "
          "the window's work list: a draft branches from HEAD, so it cannot fix "
          "what HEAD has no copy of — this exact mismatch made the first live "
          "window (2026-09-26) map a module its own worktree could not see, and be "
          "right to refuse the result",
          head_ids(dirt) == ids, str([d for d in head_ids(dirt) if d not in ids]))
    check("and the dirty checkout on its own WOULD have reported more: the "
          "untracked file's own lint finding, and a drift detail naming a module "
          "HEAD has never heard of — so reading HEAD is a choice with a visible "
          "consequence, not an accident of implementation",
          {f.id for f in scan(dirt)} - set(ids) == {"lint:flash/untracked.py"}
          and "untracked" in next(f.detail for f in scan(dirt)
                                  if f.id.startswith("drift")).split(": ", 1)[1],
          str(sorted({f.id for f in scan(dirt)})))
    check("the temp HEAD tree is not left behind once the list is read",
          not [p for p in Path(tempfile.gettempdir()).glob(
              "flash-ambient-head-*")
               if p.stat().st_mtime >= time.time() - 5], "")
    ghost = tmp / "ghost"
    (ghost / "flash").mkdir(parents=True)
    (ghost / "flash" / "__init__.py").write_text(
        '"""Demo:\n  M1: flash.known  (real)\n  M9: flash.gone  (fiction)\n"""\n')
    (ghost / "flash" / "known.py").write_text("X = 1\n")
    check("and it bites the other way too: a map entry with no module behind it "
          "is a finding", any(f.id.endswith("missing-module")
                              for f in check_drift(ghost)), str(check_drift(ghost)))

    rec = run(repo=repo, home=home, generate=gen_fix, limit=3, force=True,
              verbose=False, state=state())
    check("the gate opens under --force and says it is an override, because a "
          "silent override would let an overnight arm run on battery",
          rec["gate"] == "open" and "forced" in rec["why"][0], str(rec["why"]))
    v = [d for d in rec["drafts"] if d["verified"]]
    check("R-7.2's >= 1 reviewable draft: a window over red checks produces "
          "verified drafts — here all three", len(v) == 3,
          f"{len(v)}/3 {rec['drafts']}")
    check("each draft's diff is on disk and is a real patch, not an empty file",
          all(d["diff"] and Path(d["diff"]).read_text().startswith("diff ")
              for d in v), str([d["diff"] for d in v]))
    check("no draft strips a file's trailing newline — the '# file:' fence cannot "
          "carry that byte, so the write layer puts it back; the first accepted live "
          "draft had a perfect map entry and still said 'No newline at end of file'",
          not any("No newline at end of file" in Path(d["diff"]).read_text()
                  for d in v),
          str([Path(d["diff"]).read_text().count("No newline") for d in v]))
    check("carry_newline is a round trip, not a blanket append: a file that ended "
          "cleanly keeps one newline, a file that never had one does not gain it, "
          "and an empty response is left alone",
          carry_newline("a\n", "a").endswith("\n")
          and carry_newline("a\n", "a") == "a\n"
          and carry_newline("a", "a\n") == "a\n" and carry_newline("a\n", "") == "",
          "")
    check("'reviewable' means a human can take it: every draft applies cleanly to "
          "the untouched checkout with git apply",
          all(subprocess.run(["git", "-C", str(repo), "apply", "--check", d["diff"]],
                             capture_output=True).returncode == 0 for d in v), "")
    files_of = {f.id: set(f.files) for f in scan(repo)}
    check("a draft touches only the files its own finding was allowed to name",
          all({l[6:] for l in Path(d["diff"]).read_text().splitlines()
               if l.startswith("+++ b/")} <= files_of.get(d["finding"], set())
              for d in v),
          str([{l[6:] for l in Path(d["diff"]).read_text().splitlines()
                if l.startswith("+++ b/")} for d in v]))
    check("each finding sits on its own local branch, so review is a `git log` away",
          len({d["branch"] for d in v}) == 3 and all(
              d["branch"].startswith(f"ambient/{rec['sid']}/") for d in v), "")
    check("R-7.2's 0 commits pushed, measured where pushing would show: "
          "`git branch -r` is still empty after a window that committed three times",
          not subprocess.run(["git", "-C", str(repo), "branch", "-r"],
                             capture_output=True, text=True).stdout.strip(), "")
    gitcalls = [r["argv"] for r in trace.read(rec["sid"], home / "traces")
                if r["type"] == "git"]
    check("and the window's own log of every git command it issued contains no "
          "push — read back from the trace, not assumed from silence",
          gitcalls and not pushes_of(rec["sid"], home), f"{len(gitcalls)} calls")
    check("every git sub-command issued is in the allowlist, so the audit is a set "
          "comparison rather than a glance",
          {a.split()[1] for a in gitcalls} <= GIT_ALLOWED,
          str({a.split()[1] for a in gitcalls} - GIT_ALLOWED))
    check("R-7.2's 0 files modified outside its worktree: every working-tree file "
          "is byte-for-byte where it was, mtime included",
          snapshot(repo) == files_before,
          str(set(snapshot(repo).items()) ^ set(files_before.items())))
    check("and the main checkout's `git status --porcelain` is identical before and "
          "after — nothing was half-applied", porcelain(repo) == porcel_before, "")
    check("the repo's HEAD never moved: a draft is a branch, never a merge",
          head_of(repo) == head_before, f"{head_of(repo)[:8]} vs {head_before[:8]}")
    tr = trace.read(rec["sid"], home / "traces")
    check("R-7.2's trace: the window records its gate decision, its work list, "
          "every attempt, every draft verdict and a session end",
          {r["type"] for r in tr} >= {"session_start", "findings", "draft_attempt",
                                      "draft", "session_end"},
          str(sorted({r["type"] for r in tr})))
    check("and that trace is written under the ambient home, never into the repo's "
          "own trace store where it would look like a benchmark run",
          (home / "traces" / f"{rec['sid']}.jsonl").exists()
          and not (ROOT / "benchmarks" / "results" / "traces"
                   / f"{rec['sid']}.jsonl").exists(), "")
    check("every path the window wrote is inside a worktree under its own home: "
          "the run records each write, so this is read back rather than trusted",
          rec["writes"] and all(Path(w).is_relative_to((home / "worktrees").resolve())
                                for w in rec["writes"])
          and not any(Path(w).is_relative_to(repo.resolve())
                      for w in rec["writes"]),
          str(rec["writes"][:3]))
    st = status(home, verbose=False)
    check("flash ambient --status lists this window's drafts with their verdicts",
          len(st) == 3 and all(r["window"] == rec["sid"] for r in st), str(len(st)))

    # ---- the boundary, enforced at the command and path seams
    sb = Sandbox(repo, home, sid="boundary")
    sb.wt.mkdir(parents=True, exist_ok=True)
    for cmd in (["push", "origin", "main"], ["merge", "main"], ["reset", "--hard"],
                ["stash"], ["pull"], ["clean", "-fd"]):
        raised = ""
        try:
            sb.git(*cmd)
        except Refused as e:
            raised = str(e)
        check(f"`git {' '.join(cmd)}` raises before it can act — never merging, "
              f"never auto-applying is enforced at the command boundary",
              "not allowed" in raised, raised)
    check("refusals are recorded in the sandbox, so a window can report what it was "
          "asked to do and declined", len(sb.refused) == 6, str(sb.refused))
    for bad in ("../outside.py", "../../etc/passwd", "/tmp/absolute.py"):
        raised = ""
        try:
            sb.write(bad, "x")
        except Refused as e:
            raised = str(e)
        check(f"a write that would leave the worktree ({bad}) is refused, not "
              f"normalised", "outside its worktree" in raised, raised)
    check("a path inside the worktree is still writable: the guard is a boundary, "
          "not a refusal of all work", sb.write("flash/known.py", "X = 2\n").exists(),
          "")
    check("and nothing escaped: no file appeared beside the worktree or above it",
          not (home / "outside.py").exists() and not (tmp / "outside.py").exists(), "")

    refused = run(repo=repo, home=tmp / "home2", generate=gen_fix, limit=1,
                  force=False, state=state(on_ac=False, idle=3.0), verbose=False)
    check("on battery with an active user the window refuses, prints the numbers "
          "that produced the refusal, and drafts nothing",
          refused["gate"] == "refused" and refused["drafts"] == []
          and "battery" in " ".join(refused["why"]), str(refused["why"]))
    check("the refusal is not a silent no: 'may it run now?' answers the same way",
          refused["why"] and refused["findings_seen"] == 0, str(refused["why"]))
    dry = run(repo=repo, home=tmp / "home3", generate=gen_fix, dry=True, limit=0,
              force=True, verbose=False, state=state())
    check("--dry-run prints the work list and creates no ambient home at all",
          len(dry["findings"]) == 3 and not (tmp / "home3").exists(), str(dry))

    none = run(repo=repo, home=tmp / "home4", generate=lambda m, a: "no change here",
               limit=3, force=True, verbose=False, state=state())
    check("a generator that changes nothing earns no draft, and the reason names "
          "the missing change rather than failing silently",
          not any(d["verified"] for d in none["drafts"]) and all(
              "rewrote nothing" in d["note"] for d in none["drafts"]),
          str([d["note"] for d in none["drafts"]]))
    check("an unverified attempt is still written to the draft index: the ledger "
          "records WHY a task stays unsolved",
          (tmp / "home4" / "drafts" / none["sid"] / "index.jsonl").exists(), "")

    def gen_harm(messages, attempt):
        # clears the drift finding by naming every real module — and one that does
        # not exist: the fix silences its own finding and creates another one.
        txt = messages[0]["content"]
        if "# file: flash/__init__.py" in txt:
            return ('# file: flash/__init__.py\n```python\n'
                    '"""Demo package:\n'
                    "  M1: flash.known     (the module the map does name)\n"
                    "  X1: flash.unmapped  (what it does)\n"
                    "  X2: flash.gone      (a name this fix invented)\n"
                    '"""\n\n```\n')
        return gen_fix(messages, attempt)

    harm = run(repo=repo, home=tmp / "home5", generate=gen_harm, limit=1,
               force=True, verbose=False, state=state())
    check("a fix that clears its finding by creating another is REFUSED — the "
          "'nothing new' clause is the difference between a draft and damage",
          not harm["drafts"][0]["verified"]
          and "introduced new" in harm["drafts"][0]["note"],
          harm["drafts"][0]["note"][:130])
    check("and refusing costs the repo nothing: the branch a refused draft opened "
          "still points at the baseline commit — drafting made no commit",
          subprocess.run(["git", "-C", str(repo), "rev-parse",
                          harm["drafts"][0]["branch"]],
                         capture_output=True, text=True).stdout.strip()
          == head_before, harm["drafts"][0]["branch"])

    def gen_sloppy(messages, attempt):
        """The SHAPE THE FIRST LIVE 7B ACTUALLY RETURNED: the missing entry is
        added, so all three checks go green — and existing lines are re-wrapped
        into one, so twenty lines of the file are collateral damage."""
        txt = messages[0]["content"]
        if "# file: flash/__init__.py" in txt:
            return ('# file: flash/__init__.py\n```python\n'
                    '"""Demo package:\n'
                    "  M1: flash.known (the module the map does name) "
                    "X1: flash.unmapped (what it does)\n"
                    '"""\n\n```\n')
        return gen_fix(messages, attempt)

    sloppy = run(repo=repo, home=tmp / "home6b", generate=gen_sloppy, limit=1,
                 force=True, verbose=False, state=state())
    check("a draft that clears an additive finding by re-wrapping the file it was "
          "only asked to ADD to is REFUSED — the shape the first live Qwen2.5-Coder-7B "
          "window returned on this exact finding, which all three checks accepted as "
          "'verified' before this clause existed",
          not sloppy["drafts"][0]["verified"]
          and "asks only to ADD" in sloppy["drafts"][0]["note"],
          sloppy["drafts"][0]["note"][:150])
    check("and the clause discriminates rather than blocking all editing: the "
          "well-behaved additive fix on the same finding still ships, so 'every "
          "line survives' is not 'nothing may move'",
          rec["drafts"][0]["verified"] and rec["drafts"][0]["attempts"] == 1, "")

    def gen_stubborn(messages, attempt):
        """Maps the WRONG name: the finding survives, so the only question worth
        asking is whether the retry was told what is still missing."""
        seen.append([m["content"] for m in messages])
        return ('# file: flash/__init__.py\n```python\n'
                '"""Demo package:\n'
                "  M1: flash.known    (the module the map does name)\n"
                "  X1: flash.wrong    (a name that is not a module)\n"
                '"""\n\n```\n')

    seen: list[list[str]] = []
    stubborn = run(repo=repo, home=tmp / "home6c", generate=gen_stubborn, limit=1,
                   force=True, max_attempts=2, verbose=False, state=state())
    check("a near-miss is refused by name, not by mood: the refusal quotes the "
          "check's own detail, so 'still red' arrives with the module list still "
          "missing from the map",
          "still red" in stubborn["drafts"][0]["note"]
          and "unmapped" in stubborn["drafts"][0]["note"],
          stubborn["drafts"][0]["note"][:130])
    check("and the retry is actually fed that reason: attempt 2's conversation has "
          "a second turn that was not there for attempt 1, and it carries the "
          "missing module's name — M1's error-feedback loop, not a second "
          "independent guess",
          len(seen) == 2 and len(seen[0]) == 1 and len(seen[1]) == 2
          and "unmapped" in seen[1][1] and seen[1][1] != seen[0][0],
          f"{len(seen)} attempts, {len(seen[-1])} turns in the last one")

    grounded = tmp / "repo-ground"
    (grounded / "flash").mkdir(parents=True)
    (grounded / "flash" / "__init__.py").write_text(
        '"""Demo package:\n  M1: flash.known\n"""\n')
    (grounded / "flash" / "known.py").write_text('"""Known."""\n')
    (grounded / "flash" / "widget.py").write_text(
        '"""Register widgets and refuse duplicates."""\n')
    (grounded / "flash" / "silent.py").write_text("SILENT = 1\n")
    gfind = check_drift(grounded)[0]
    check("the drift prompt QUOTES each unmapped module's own first docstring line, "
          "and says so plainly when a module has none — the filler in the first "
          "accepted live draft ('Ambient context processing for the coding "
          "environment') came from a prompt that showed the model only the map, and "
          "a reviewable draft cannot be built on a guess",
          "Register widgets and refuse duplicates." in gfind.prompt
          and "no docstring to quote" in gfind.prompt
          and "invent nothing" in gfind.prompt,
          gfind.prompt[gfind.prompt.find("do not invent"):][:200].replace("\n", " | "))

    def gen_wrongform(messages, attempt, right_at=-1):
        """THE SHAPE THE REAL 7B RETURNED ON GREEDY DECODE, twice on the real repo
        and once on a synthetic one (benchmarks/ambient_echo_probe.py --inspect,
        2026-09-26): the entry is there, the description is plausible, and the name
        is bare — `ambient — ambient context and environment handling.` — which the
        map parser does not read as a map entry at all. `right_at` is the attempt
        index where it switches to the readable form (-1 = never)."""
        line = ("  X1: flash.unmapped  (what it does)" if attempt == right_at
                else "  X1: unmapped      (what it does)")
        return ('# file: flash/__init__.py\n```python\n'
                '"""Demo package:\n'
                "  M1: flash.known    (the module the map does name)\n"
                + line + '\n"""\n\n```\n')

    badform = run(repo=repo, home=tmp / "home6d", generate=gen_wrongform, limit=1,
                  force=True, max_attempts=2, verbose=False, state=state())
    check("a correctly-MEANING draft in the wrong FORM is refused: the oracle reads "
          "the map the way the check reads it, so `X1: unmapped` does not clear an "
          "unmapped-module finding — the failure every live window actually hit",
          not badform["drafts"][0]["verified"]
          and "still red" in badform["drafts"][0]["note"],
          badform["drafts"][0]["note"][:130])
    rightform = run(repo=repo, home=tmp / "home6e",
                    generate=lambda m, a: gen_wrongform(m, a, right_at=1), limit=1,
                    force=True, max_attempts=2, verbose=False, state=state())
    check("and the refusal is enough to fix it: the same generator, told the form on "
          "the second turn, ships at attempt 2 — so the live miss is a prompt the "
          "model can satisfy, not a ceiling on the tier",
          rightform["drafts"][0]["verified"] and rightform["drafts"][0]["attempts"] == 2,
          f"verified={rightform['drafts'][0]['verified']} "
          f"attempts={rightform['drafts'][0]['attempts']}")
    drift_prompt = next(f.prompt for f in scan(repo) if f.id.endswith(":unmapped"))
    check("and the prompt now STATES that form, because the measured miss was a "
          "form failure and a fix that lives only in a log line will be lost",
          "flash.unmapped" in drift_prompt and "backticked" in drift_prompt
          and "bare name" in drift_prompt, drift_prompt[230:330].replace("\n", " "))

    spent = run(repo=repo, home=tmp / "home6", generate=gen_fix, limit=0,
                budget_s=0.0, force=True, verbose=False, state=state())
    check("the wall-clock budget is honoured before the first generation, so a "
          "window that is already spent costs nothing and says so",
          spent["drafts"] == [] and spent["findings_seen"] == 3, str(spent["drafts"]))
    check("and a spent window leaves the repo exactly as it found it",
          snapshot(repo) == files_before and porcelain(repo) == porcel_before, "")

    # ---- mutation: break each guarantee on purpose and show a check notices
    # sys.modules[__name__], not `import flash.ambient`: under `python -m` the
    # code running IS __main__, and patching the other copy would prove nothing.
    A = sys.modules[__name__]
    real_git, real_write, real_verify = Sandbox.git, Sandbox.write, A.verify
    try:
        Sandbox.git = lambda self, *a, cwd=None: (
            self.git_log.append("git " + " ".join(a)) or subprocess.run(
                ["git", *a], cwd=str(cwd or self.wt), capture_output=True,
                text=True))
        ms = Sandbox(repo, home, sid="mut-push")
        ms.wt.mkdir(parents=True, exist_ok=True)
        ms.git("push", "origin", "main")
        check("MUTATION: with the allowlist guard removed, `git push` is issued "
              "instead of refused — so the guard, not convention, is what R-7.2 "
              "rests on", any("push" in g for g in ms.git_log), str(ms.git_log))
    finally:
        Sandbox.git = real_git
    try:
        def naked_write(self, rel, text):
            dest = (self.wt / rel).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            self.writes.append(str(dest))
            return dest
        Sandbox.write = naked_write
        ms2 = Sandbox(repo, home, sid="mut-write")
        ms2.wt.mkdir(parents=True, exist_ok=True)
        ms2.write("../../escaped.py", "x = 1\n")
        check("MUTATION: with the containment guard removed, the same write that "
              "the test above refuses lands beside the worktree and above it — so "
              "the boundary is that guard, not the path shape",
              (home / "escaped.py").exists(), str(ms2.writes))
    finally:
        Sandbox.write = real_write
        (home / "escaped.py").unlink(missing_ok=True)
    try:
        A.verify = lambda sb, f, before, original: (
            True, "cleared (verification skipped)")
        mrun = run(repo=repo, home=tmp / "home7", generate=gen_harm, limit=1,
                   force=True, verbose=False, state=state())
        check("MUTATION: skipping verification ships the harmful draft as a "
              "'verified' draft — exactly the failure the oracle clause exists to "
              "stop", mrun["drafts"][0]["verified"], str(mrun["drafts"][0]["note"]))
    finally:
        A.verify = real_verify
    try:
        A.additive_kept = lambda original, new: []
        m2 = run(repo=repo, home=tmp / "home8", generate=gen_sloppy, limit=1,
                 force=True, verbose=False, state=state())
        check("MUTATION: with the additive clause removed, the re-wrapping draft is "
              "'verified' again — so the clause, not the model's manners, is what "
              "holds the line", m2["drafts"][0]["verified"],
              str(m2["drafts"][0]["note"]))
    finally:
        A.additive_kept = additive_kept
    try:
        A.carry_newline = lambda original, text: text
        m3 = run(repo=repo, home=tmp / "home9", generate=gen_fix, limit=1,
                 force=True, verbose=False, state=state())
        md = Path(m3["drafts"][0]["diff"]).read_text()
        check("MUTATION: with the newline carried straight through instead of back, "
              "the same draft ships with 'No newline at end of file' in it — the "
              "artifact the live window actually produced",
              "No newline at end of file" in md, str(md.count("No newline")))

        # The live miss was a FORM failure, so the strictness that refuses it is
        # load-bearing: widen the name matcher and the very draft the check above
        # refuses goes green — the map would then claim a module it cannot resolve.
        real_search = A.search_name
        A.search_name = lambda doc, mod: re.search(rf"\b{re.escape(mod)}\b", doc)
        widened = run(repo=repo, home=tmp / "home-mut-form",
                      generate=gen_wrongform, limit=1, force=True,
                      verbose=False, state=state())
        A.search_name = real_search
        check("MUTATION: let the map parser accept a bare name and the wrong-form "
              "draft is 'verified' — so the refusal above is the matcher's rigor, "
              "not the model's luck",
              widened["drafts"][0]["verified"], widened["drafts"][0]["note"][:120])
    finally:
        A.carry_newline = carry_newline
        A.search_name = search_name
    return _report(checks, verbose)


def _report(checks, verbose: bool) -> int:
    bad = [c for c in checks if not c[1]]
    if verbose:
        print(f"{len(checks) - len(bad)}/{len(checks)} checks passed"
              + ("" if not bad else " — FAILURES:"))
        for label, _, detail in bad:
            print(f"  FAIL {label}  {detail}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
