"""R-3.2's clause 3, OFFLINE: a patch set the oracle passed reaches the disk it
was read from — and a run that did not write says so out loud.

The defect this box was filed for was not in the patch arm's arithmetic. Every
attempt was scored in memory, `solved=True` was printed, and the project on disk
was never touched: `flash/loop.py` kept the accepted workspace in a local
variable and `flash/cli.py` printed it as text. A person reading the last line of
a terminal takes `solved=True` for an edit that happened. This ran live on a
throwaway tree on 2026-09-28 — three attempts, a green oracle, and
`benchmarks/results/traces/` for the transcript while `money.py` sat byte-
identical.

So the write-back is opt-in (`--apply`), and what is *not* optional is the
sentence naming which of the two states the tree is in. Seven questions, all of
them about the seam rather than about a model:

* **Does only the changed part move?** Files whose bytes match are not rewritten
  (their mtimes prove it), a file the listing stopped covering is left alone —
  the patch arm has no verb for deleting a module, so a `limit` in a listing must
  not become a deletion in someone's project — and the line counts the CLI prints
  are lines, not diff hunks.
* **Can an address escape?** `# edit: ../../etc/hosts :: …` is the same string as
  a real one as far as `apply_patches` is concerned, and a key that is not in the
  workspace the oracle scored names a file nobody verified.
* **Is the set atomic?** One refused address leaves the whole call unwritten. A
  tree that is half patched is no longer the tree that passed.
* **Can the model edit the exam?** `workspace_from_dir` lists every Python file in
  `--context`, the test file included, so a patch set that "fixed" a failure by
  weakening an assertion is in scope unless the oracle is named protected. That
  protection is scoped to the one file: guarding the whole tree would refuse every
  real patch.
* **Does the default refuse?** Without `--apply` the run prints `NOT APPLIED` and
  exits on its verdict. `--apply` on a task that never reached a workspace exits
  1 and writes nothing.
* **Did the command actually do it?** The real parser and the real `cmd_run` are
  driven here with the router replaced by a stub, over a temp tree, and the tree
  is read back afterwards. No model loads.
* **Does a create look different from a run that wrote nothing?** R-7.15b's verb
  differs from every other patch here by leaving a file whose only change is an
  *insertion*, so the printed line counts are the one thing telling `+4 -0` from
  `+0 -0`, and the disk has to keep the pre-existing definition first with the new
  one last where the AST put it.

The loop half is here too: `_solve_edits` hands the accepted workspace back on
`SolveResult.workspace` and still writes nothing itself, because "verified" and
"on disk" are two different claims and only the CLI is allowed to collapse them.

    python benchmarks/patch_landing_check.py             # the checks
    python benchmarks/patch_landing_check.py --mutants   # put each bug back
    python benchmarks/patch_landing_check.py --sweep     # one fresh process each
    python benchmarks/patch_landing_check.py --mutant 7  # one bug, for a bisection
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import cli, loop, patches, trace                          # noqa: E402
from flash.patches import (LandError, workspace_from_dir)            # noqa: E402

NUM_BUGS = 24

# A four-file project in the shapes the patch arm actually meets: a top-level
# module, a sibling it must not disturb, the oracle (in the tree, so listed by
# `workspace_from_dir`), and a module in a package directory.
MONEY = 'def cents(n):\n    return f"${n // 100}.{n % 100}"\n'
MONEY_FIXED = 'def cents(n):\n    return f"${n // 100}.{n % 100:02d}"\n'

#: R-7.15b's verb: `+k_to_c` CREATES a definition instead of revising one, so the
#: workspace it hands back differs from the file on disk by a PURE insertion —
#: nothing deleted, nothing re-typed. `+4 -0` is what that measures, and it is
#: what the CLI has to print, because a create reported as `+0 -0` is the same
#: sentence as a run that wrote nothing.
MONEY_CREATED = MONEY + '\n\ndef k_to_c(k):\n    return k - 273.15\n'
CREATE_TEST = ('import sys; sys.path.insert(0, "<TMPDIR>")\n'
               "from money import k_to_c\nassert k_to_c(0) == -273.15\n")
CREATE_PATCH = ("# edit: money.py :: +k_to_c\n```python\n"
                "def k_to_c(k):\n    return k - 273.15\n```\n")
CREATE_CLASH = ("# edit: money.py :: +cents\n```python\n"
                "def cents(n):\n    return 1\n```\n")
TAX = "def rate(v):\n    return v * 0.08\n"
DEEP = "LIMIT = 3\n"
ORACLE = ('import sys\nsys.path.insert(0, "<TMPDIR>")\n'
          'from money import cents\nassert cents(5) == "$0.05"\n')
FILES = {"money.py": MONEY, "tax.py": TAX, "pkg/deep.py": DEEP,
         "t.py": ORACLE}

#: The one patch the stub generator emits: the symbol-addressed form the protocol
#: tells the model to prefer, over `MONEY`'s real definition.
PATCH = ("# edit: money.py :: cents\n```python\n"
         'def cents(n):\n    return f"${n // 100}.{n % 100:02d}"\n```\n')

CHECKS: list[tuple[str, bool, str]] = []

_KEEP: list[tempfile.TemporaryDirectory] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    d = ""
    if not ok:
        # A mutant can break the thing a detail line prints as well as the thing a
        # check tests, and the sweep needs this process to reach its verdict: a
        # failure to explain a fail is recorded as one, not raised.
        try:
            d = detail if isinstance(detail, str) else str(detail())
        except Exception as e:
            d = f"(the detail itself raised {type(e).__name__}: {e})"
    CHECKS.append((name, ok, d))


def outcome(fn):
    """Call `fn`, and return the exception instead of raising it. The refusal
    checks want to look at what landed on disk *after* the raise."""
    try:
        return fn()
    except Exception as e:
        return f"RAISED: {type(e).__name__}: {e}"


def try_land(root, before, after, protected=()):
    """`patches.land` where a raise is a result rather than an abort: a mutant
    has to make its check FAIL, not end the run before the verdict prints."""
    return outcome(lambda: patches.land(root, before, after, protected))


def text(root, rel) -> str:
    """A file's bytes, or a named sentinel when the tree lost it — which is
    exactly the state one of the mutants creates, and a check may not raise on
    the thing it exists to catch."""
    p = Path(root) / rel
    return p.read_text(encoding="utf-8") if p.exists() else "<missing>"


def new_tree(files: dict[str, str] | None = None) -> Path:
    d = tempfile.TemporaryDirectory(prefix="flash-land-")
    _KEEP.append(d)
    root = Path(d.name)
    for rel, text in (FILES if files is None else files).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def snapshot(root: Path) -> dict:
    """The tree as the write-back would find it: bytes *and* mtimes, so a check
    for "not rewritten" cannot be satisfied by a rewrite with the same text."""
    return {k: (v, (root / k).stat().st_mtime_ns)
            for k, v in workspace_from_dir(root).items()}


def cleanup() -> None:
    for d in _KEEP:
        d.cleanup()
    _KEEP.clear()


def ws(root: Path, **changed: str) -> dict:
    """The workspace of `root` with whole files replaced — what a successful
    patch set leaves in `SolveResult.workspace`."""
    return dict(workspace_from_dir(root), **changed)


def args_for(root: Path, apply: bool, test: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(apply=apply, context=str(root),
                              test=test or str(root / "t.py"))


def result_for(workspace, solved=True) -> loop.SolveResult:
    return loop.SolveResult(task_id="landing", solved=solved, attempts=[
        loop.Attempt(code="# file: money.py\n" + (MONEY_FIXED if solved else MONEY),
                     ok=solved)], workspace=workspace)


def capture(fn, *a, **k) -> tuple[object, str]:
    """`fn` with stdout collected, and an exception turned into the exit code it
    would have been. Same reason as `try_land`."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            got = fn(*a, **k)
    except Exception as e:
        return f"RAISED: {type(e).__name__}: {e}", buf.getvalue()
    return got, buf.getvalue()


# ----------------------------------------------------- the reference copies

def land_copy(**knob):
    """`flash.patches.land` with one clause switched, which is how a mutant puts
    exactly one defect back. A copy rather than a wrapper: removing a guard from
    outside would mean calling the real guard and ignoring its verdict.

    Every knob's default is the shipped behaviour, so
    `land_copy()` must agree with `patches.land` on every check here — that
    agreement is itself asserted below, and the sweep fails loudly if the copy
    drifts.
    """
    k = dict(skip=True, escape=True, newfile=True, protect=True, atomic=True,
             mkdir=True, write=True, ops=False, phantom=False, delete=False,
             stale=False, protect_unchanged=False, insert_zero=False)
    k.update(knob)

    def _counts(rel, text, before):
        if k["phantom"]:
            old, new = before[rel].split("\n"), text.split("\n")
        else:
            old, new = before[rel].splitlines(), text.splitlines()
        sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
        ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
        if k["insert_zero"] and ops and all(t == "insert" for t, *_ in ops):
            return (0, 0)                    # a pure insertion prints as a no-op
        if k["ops"]:
            return (sum(1 for t, *_ in ops if t in ("insert", "replace")),
                    sum(1 for t, *_ in ops if t in ("delete", "replace")))
        return (sum(j2 - j1 for t, _, _, j1, j2 in ops if t in ("insert", "replace")),
                sum(i2 - i1 for t, i1, i2, _, _ in ops if t in ("delete", "replace")))

    def _land(root, before, after, protected=()):
        root = Path(root).resolve()
        todo: list[tuple[str, Path, int, int]] = []
        out: list[tuple[str, int, int]] = []

        def _write(rel, dest, added, removed):
            if k["mkdir"]:
                dest.parent.mkdir(parents=True, exist_ok=True)
            if k["write"]:
                dest.write_text(before.get(rel, "") if k["stale"] else after[rel],
                                encoding="utf-8")
            out.append((rel, added, removed))

        for rel, text in after.items():
            if k["protect_unchanged"] and rel in protected:
                raise LandError(f"{rel}: protected")
            if k["skip"] and before.get(rel) == text:
                continue
            if k["protect"] and rel in protected:
                raise LandError(f"{rel}: changed a protected file")
            key = Path(rel)
            dest = (root / key).resolve()
            if k["escape"] and (key.is_absolute() or not dest.is_relative_to(root)):
                raise LandError(f"{rel}: outside the project root")
            if k["newfile"] and rel not in before:
                raise LandError(f"{rel}: not a scored file")
            added, removed = _counts(rel, text, before)
            if k["atomic"]:
                todo.append((rel, dest, added, removed))
            else:
                _write(rel, dest, added, removed)
        for rel, dest, added, removed in todo:
            _write(rel, dest, added, removed)
        if k["delete"]:
            for rel in before:
                if rel not in after:
                    (root / rel).unlink(missing_ok=True)
        return out
    return _land


def edits_copy(**knob):
    """`flash.cli._land_edits` with one clause switched. Same reason as above."""
    k = dict(protect=True, refuse_rc=1, honest=True, verdict_rc=True,
             say_noop=True, unsolved_rc=1, protect_tree=False)
    k.update(knob)

    def _edits(args, task, r):
        from flash.patches import land
        if not args.apply:
            what = ("the oracle passed this patch set" if r.solved
                    else "nothing passed the oracle")
            if k["honest"]:
                print(f"[R-3.2] NOT APPLIED: {what} in memory; {args.context} on "
                      "disk is unchanged. Re-run with --apply to land the patch set.")
            else:
                print(f"[R-3.2] APPLIED: {what}.")
            if k["verdict_rc"]:
                return 0 if r.solved else 1
            return 0
        if r.workspace is None:
            print(f"[R-3.2] NOT APPLIED: the task was not solved, so --apply wrote "
                  f"nothing to {args.context}")
            return k["unsolved_rc"]
        protected = ()
        test_rel = cli._rel_to(args.test, args.context)
        if k["protect_tree"]:
            protected = tuple(task["files"])
        elif k["protect"] and test_rel in task["files"]:
            protected = (test_rel,)
        try:
            changed = land(args.context, task["files"], r.workspace, protected)
        except LandError as e:
            print(f"[R-3.2] REFUSED: --apply wrote nothing — {e}")
            return k["refuse_rc"]
        for rel, added, removed in changed:
            print(f"[R-3.2] wrote {rel} (+{added} -{removed} lines)")
        if not changed and k["say_noop"]:
            print(f"[R-3.2] --apply wrote nothing: the verified workspace is "
                  f"byte-identical to what is already in {args.context}")
        return 0
    return _edits


def agree() -> str:
    """The first scenario on which `land_copy()` and the shipped `land` disagree.

    Every mutant here is a copy with one clause switched, so the copy has to be
    indistinguishable from the real function on the clean inputs — otherwise a
    check could fail for drift instead of for the bug it names.
    """
    scenarios = [
        ({"money.py": MONEY_FIXED}, ()),
        ({}, ()),
        ({k: v for k, v in FILES.items()}, ()),
        ({"money.py": MONEY_FIXED, "tax.py": TAX}, ("t.py",)),
        ({"t.py": "assert True\n"}, ("t.py",)),
        ({"money.py": MONEY_FIXED, "t.py": "assert True\n"}, ("t.py",)),
        ({"f.py": "a\nb\nc\n"}, ()),
    ]
    for changed, protected in scenarios:
        seen = []
        for fn in (patches.land, land_copy()):
            root = new_tree({"f.py": "a\nb\nc\n"} if changed == {"f.py": "a\nb\nc\n"}
                            else None)
            before = workspace_from_dir(root)
            start = snapshot(root)
            after = dict(before, **changed)
            try:
                got = ("OK", fn(root, before, after, protected))
            except Exception as e:
                got = ("RAISED", f"{type(e).__name__}")
            end = snapshot(root)
            seen.append((got, {k: v[0] for k, v in end.items()},
                         tuple(sorted(k for k in end
                                      if end[k] != start.get(k)))))
        if seen[0] != seen[1]:
            return f"{sorted(changed)} {protected}: shipped={seen[0]} copy={seen[1]}"
    return ""


# ---------------------------------------------------------------- the checks

def _land_checks() -> None:
    check("the copy of `land` this vector mutates agrees with the shipped one on "
          "every clean scenario, so a mutant fails for its own bug and not for "
          "drift", agree() == "", agree())

    root = new_tree()
    before = workspace_from_dir(root)
    rows = try_land(root, before, ws(root, **{"money.py": MONEY_FIXED}))
    check("the file that changed lands with the workspace's bytes, and is the only "
          "row the call reports",
          text(root, "money.py") == MONEY_FIXED
          and rows == [("money.py", 1, 1)], f"{rows}")

    root = new_tree()
    before = workspace_from_dir(root)
    snap = snapshot(root)
    try_land(root, before, ws(root, **{"money.py": MONEY_FIXED}))
    check("an untouched sibling keeps its bytes *and* its mtime, so a patch on one "
          "module does not re-timestamp a project",
          snapshot(root)["tax.py"] == snap["tax.py"], str(snapshot(root)["tax.py"]))

    root = new_tree()
    before = workspace_from_dir(root)
    try_land(root, before, {k: v for k, v in before.items()
                                if k != "pkg/deep.py"} | {"money.py": MONEY_FIXED})
    check("a file the listing stopped covering is left on disk — the patch arm has "
          "no verb for deleting a module, so a `limit` is not a deletion",
          text(root, "pkg/deep.py") == DEEP,
          "the tree lost it")

    root = new_tree()
    before = workspace_from_dir(root)
    (root / "pkg" / "deep.py").unlink()
    (root / "pkg").rmdir()
    rows = try_land(root, before, ws(root, **{"pkg/deep.py": "LIMIT = 9\n"}))
    check("a nested key whose directory is gone is recreated, not skipped",
          text(root, "pkg/deep.py") == "LIMIT = 9\n"
          and rows == [("pkg/deep.py", 1, 1)], f"{rows}")

    root = new_tree()
    before = workspace_from_dir(root)
    snap = snapshot(root)
    escaped = {"money.py": MONEY_FIXED, "../evil.py": "import os\n"}
    err = try_land(root, before, escaped)
    check("an address that resolves outside the project root is refused by name, "
          "never silently landed",
          isinstance(err, str) and err.startswith("RAISED: LandError")
          and "outside the project root" in err, str(err)[:160])
    check("the whole set stays unwritten when one address in it is refused, so a "
          "half-landed patch is never mistaken for the tree the oracle scored",
          snapshot(root) == snap, "money.py landed before the refusal")

    root = new_tree()
    before = workspace_from_dir(root)
    snap = snapshot(root)
    err = try_land(root, before, {"money.py": MONEY_FIXED,
                                  "new.py": "X = 1\n"})
    check("a key the oracle never scored is refused rather than added to someone's "
          "project",
          isinstance(err, str) and err.startswith("RAISED: LandError")
          and "not a file this workspace was read from" in err
          and not (root / "new.py").exists(), str(err)[:160])

    root = new_tree()
    before = workspace_from_dir(root)
    snap = snapshot(root)
    err = try_land(root, before, ws(root, **{"t.py": "assert True\n"}),
                   ("t.py",))
    check("a protected file the patch set changed is refused by name, because a "
          "failure fixed by weakening an assertion is not a fix",
          isinstance(err, str) and err.startswith("RAISED: LandError")
          and "protected" in err, str(err)[:160])
    check("a refusal that names a protected file writes nothing at all, including "
          "the sibling it was happy with",
          snapshot(root) == snap, "the tree moved")

    root = new_tree()
    before = workspace_from_dir(root)
    rows = try_land(root, before, ws(root, **{"money.py": MONEY_FIXED}),
                        ("t.py",))
    check("a protected file the patch set left byte-identical is not refused, so "
          "protection costs nothing on a clean patch",
          rows == [("money.py", 1, 1)]
          and text(root, "t.py") == ORACLE, f"{rows}")

    root = new_tree({"f.py": "a\nb\nc\n"})
    rows = try_land(root, {"f.py": "a\nb\nc\n"}, {"f.py": "a\nX\nY\nZ\nc\n"})
    check("the line counts `land` reports are lines, not diff hunks — one hunk that "
          "adds three is +3, and the CLI prints whatever this returns",
          rows == [("f.py", 3, 1)], f"{rows}")

    root = new_tree({"f.py": "a\nb\n"})
    rows = try_land(root, {"f.py": "a\nb\n"}, {"f.py": "a\nb"})
    check("a difference in the trailing newline alone lands as a change of zero "
          "lines, not a phantom deleted line",
          text(root, "f.py") == "a\nb"
          and rows == [("f.py", 0, 0)], f"{rows}")

    root = new_tree()
    before = workspace_from_dir(root)
    snap = snapshot(root)
    rows = try_land(root, before, dict(before))
    check("an identical workspace reports an empty list and rewrites nothing, so "
          "the caller can say byte-identical instead of claiming a write",
          rows == [] and snapshot(root) == snap, f"{rows}")

    root = new_tree()
    before = workspace_from_dir(root)
    rows = try_land(root, before, ws(root, **{"money.py": MONEY_CREATED}))
    check("a create — R-7.15b's `+Name`, whose workspace differs from the file on "
          "disk by a PURE insertion — lands as +4 -0: four lines added, none "
          "removed, which is not the sentence a no-op gets",
          rows == [("money.py", 4, 0)] and text(root, "money.py") == MONEY_CREATED,
          f"{rows} -> {text(root, 'money.py')!r}")
    check("...and the definition that was already there is still the first thing "
          "in the file, byte for byte, because a create rewrites nothing it was "
          "not asked to touch",
          text(root, "money.py").startswith(MONEY)
          and text(root, "tax.py") == TAX and text(root, "t.py") == ORACLE,
          text(root, "money.py")[:60])


def _edits_checks() -> None:
    root = new_tree()
    task = {"files": workspace_from_dir(root)}
    snap = snapshot(root)
    rc, out = capture(cli._land_edits, args_for(root, False), task,
                      result_for(ws(root, **{"money.py": MONEY_FIXED})))
    check("without --apply a solved patch set prints NOT APPLIED, names the flag "
          "that would land it, and still exits on its verdict",
          rc == 0 and "NOT APPLIED" in out and "--apply" in out, out[:200])

    rc, out = capture(cli._land_edits, args_for(root, False), task,
                      result_for(None, solved=False))
    check("without --apply a failed run exits 1, and its sentence says nothing "
          "passed the oracle rather than the one a green run gets",
          rc == 1 and "nothing passed the oracle" in out
          and "passed this patch set" not in out, out[:200])

    rc, out = capture(cli._land_edits, args_for(root, True), task,
                      result_for(None, solved=False))
    check("--apply on a run that never reached a workspace exits 1 and writes "
          "nothing, because the flag is not a licence to guess",
          rc == 1 and snapshot(root) == snap, f"rc={rc} {out[:120]}")

    rc, out = capture(cli._land_edits, args_for(root, True), task,
                      result_for(ws(root, **{"money.py": MONEY_FIXED})))
    check("--apply writes the changed file and prints its path with the line "
          "counts `land` reported",
          rc == 0 and text(root, "money.py") == MONEY_FIXED
          and "wrote money.py (+1 -1 lines)" in out, out[:200])

    root = new_tree()
    task = {"files": workspace_from_dir(root)}
    rc, out = capture(cli._land_edits, args_for(root, True), task,
                      result_for(dict(workspace_from_dir(root))))
    check("--apply on a byte-identical workspace says so instead of printing a "
          "write that did not happen",
          rc == 0 and "byte-identical" in out and "wrote money" not in out,
          out[:200])

    root = new_tree()
    task = {"files": workspace_from_dir(root)}
    snap = snapshot(root)
    rc, out = capture(cli._land_edits, args_for(root, True), task,
                      result_for(ws(root, **{"money.py": MONEY_FIXED,
                                             "../escape.py": "x = 1\n"})))
    check("when `land` refuses, --apply exits 1 and prints REFUSED with the reason, "
          "so a failed write-back is not the same verdict as a successful one",
          rc == 1 and "REFUSED" in out and "outside the project root" in out
          and snapshot(root) == snap, f"rc={rc} {out[:160]}")

    root = new_tree()
    task = {"files": workspace_from_dir(root)}
    snap = snapshot(root)
    rc, out = capture(cli._land_edits, args_for(root, True), task,
                      result_for(ws(root, **{"t.py": "assert True\n",
                                             "money.py": MONEY_FIXED})))
    check("an oracle that lives inside --context is protected: a patch set that "
          "repaired the failure by editing the assertion is refused, the sibling "
          "edit and all",
          rc == 1 and "REFUSED" in out and "t.py" in out and snapshot(root) == snap,
          f"rc={rc} {out[:160]}")

    root = new_tree()
    outside = new_tree({"t.py": ORACLE})
    task = {"files": workspace_from_dir(root)}
    rc, out = capture(cli._land_edits, args_for(root, True,
                                                test=str(outside / "t.py")),
                      task, result_for(ws(root, **{"money.py": MONEY_FIXED})))
    check("protection is scoped to the oracle rather than the tree: a test kept "
          "outside --context leaves a real patch free to land",
          rc == 0 and text(root, "money.py") == MONEY_FIXED,
          f"rc={rc} {out[:160]}")

    root = new_tree()
    check("_rel_to gives the oracle the same key form the workspace uses, however "
          "the path was spelled — an absolute path, a ./ step and a package "
          "subdirectory all resolve back to one name",
          cli._rel_to(str(root / "t.py"), str(root)) == "t.py"
          and cli._rel_to(str(root / "./t.py"), str(root)) == "t.py"
          and cli._rel_to(str(root / "pkg" / ".." / "t.py"), str(root)) == "t.py",
          f"{cli._rel_to(str(root / './t.py'), str(root))!r}")

    cwd = Path.cwd()
    try:
        os.chdir(root)
        spelled = cli._rel_to("t.py", ".")
    finally:
        os.chdir(cwd)
    check("the spelling a person actually types — `--test t.py --context .` from "
          "inside the project — produces the workspace's key, so the oracle is "
          "protected in the demo as much as in the tree",
          spelled == "t.py", repr(spelled))

    check("_rel_to leaves a path outside the tree alone, so it can never name a "
          "protected key by accident and disarm the guard it was answering",
          cli._rel_to(str(root.parent / "elsewhere.py"), str(root))
          == str(root.parent / "elsewhere.py"),
          cli._rel_to(str(root.parent / "elsewhere.py"), str(root)))


# ---------------------------------------------------------------- the command

def drive(argv: list[str], workspace: dict | None = None, solved: bool = True):
    """The real parser and the real `cmd_run`, in this process, with the router
    replaced by a stub that hands back the given workspace.

    Stubbing `solve_routed` rather than `_generate` keeps the whole command on the
    table — the parser, `workspace_from_dir`, the trace session, the verdict line
    and `_land_edits` — and no model loads, which is what makes this offline. The
    trace store is redirected so the run lands in a temp directory rather than in
    the repository's own record.
    """
    box = {"reached": False}
    real = (loop.solve_routed, sys.argv, trace.DIR)
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-land-trace-"))

    def fake(small_repo, big_repo, task, root, **kw):
        box["reached"] = True
        r = result_for(None, solved)
        if workspace is not None and task.get("files"):
            r.workspace = dict(task["files"], **workspace)
        return r, "small", "small"

    loop.solve_routed = fake
    sys.argv = ["flash", *argv]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                rc = cli.main()
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 2
            except Exception as e:
                rc = f"RAISED: {type(e).__name__}: {e}"
    finally:
        loop.solve_routed, sys.argv, trace.DIR = real
    return rc, buf.getvalue(), box["reached"]


def parsed(argv: list[str]):
    """A command line through the real parser, with a crash returned rather than
    raised — one mutant replaces `build_parser` itself."""
    return outcome(lambda: cli.build_parser().parse_args(argv))


def rejects(argv: list[str]) -> str:
    """The parser's complaint about a command line, or "" if it accepted it."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            cli.build_parser().parse_args(argv)
    except SystemExit:
        return buf.getvalue()
    except Exception as e:
        return f"RAISED: {type(e).__name__}: {e}"
    return ""


def _cli_checks() -> None:
    tdir = ROOT / "benchmarks" / "results" / "traces"
    before = sorted(p.name for p in tdir.glob("*.jsonl")) if tdir.is_dir() else []

    a = parsed(["run", "p", "--test", "t.py"])
    check("--apply is a flag on `run` and defaults to off, so writing to someone's "
          "project is never what a first command does",
          getattr(a, "apply", None) is False, repr(getattr(a, "apply", None)))
    a = parsed(["run", "p", "--test", "t.py", "--edit", "--context", ".", "--apply"])
    check("--apply parses together with --edit and --context, the three flags that "
          "make a write-back meaningful",
          getattr(a, "apply", None) is True and a.edit is True and a.context == ".",
          repr(a)[:200])
    check("--apply is not accepted by `run-suite` or `resume`, where there is no "
          "single tree to land and a flag that silently does nothing is worse than "
          "one that does not exist",
          "unrecognized arguments: --apply" in rejects(["run-suite", "--apply"])
          and "unrecognized arguments: --apply" in rejects(["resume", "--apply"]),
          rejects(["run-suite", "--apply"])[:120])

    root = new_tree()
    rc, out, reached = drive(["run", "zero-pad the cents", "--test",
                              str(root / "t.py"), "--apply"],
                             workspace={"money.py": MONEY_FIXED})
    check("--apply without --edit is refused before anything is routed: with no "
          "patch arm there is no verified workspace, and the flag would be a "
          "promise the command cannot keep",
          rc == 2 and not reached and "--apply needs --edit" in out
          and text(root, "money.py") == MONEY,
          f"rc={rc} reached={reached} {out[:160]}")

    root = new_tree()
    rc, out, reached = drive(["run", "zero-pad the cents", "--test", str(root / "t.py"),
                              "--context", str(root), "--edit", "--apply"],
                             workspace={"money.py": MONEY_FIXED})
    check("flash run --edit --context <tree> --apply lands the oracle-passing patch "
          "set on the tree it read, and prints the path with its line counts",
          rc == 0 and reached and text(root, "money.py")
          == MONEY_FIXED and "wrote money.py (+1 -1 lines)" in out, out[:200])
    check("the command that landed a patch left its sibling, its package module and "
          "its oracle exactly as it found them",
          text(root, "tax.py") == TAX
          and text(root, "t.py") == ORACLE
          and text(root, "pkg/deep.py") == DEEP,
          "the tree moved outside the symbol")

    root = new_tree()
    rc, out, _ = drive(["run", "zero-pad the cents", "--test", str(root / "t.py"),
                        "--context", str(root), "--edit", "--apply"],
                       workspace={"money.py": MONEY_CREATED})
    check("through the real command a landed create prints the lines it ADDED and "
          "none removed, and the file on disk defines what the address asked for — "
          "the half of the pair that a session's `refused=1` line is read against",
          rc == 0 and "wrote money.py (+4 -0 lines)" in out
          and text(root, "money.py") == MONEY_CREATED, f"rc={rc} {out[:200]}")

    root = new_tree()
    snap = snapshot(root)
    rc, out, _ = drive(["run", "zero-pad the cents", "--test", str(root / "t.py"),
                        "--context", str(root), "--edit"],
                       workspace={"money.py": MONEY_FIXED})
    check("the same command without --apply leaves the tree byte-identical, exits "
          "on its verdict, and says NOT APPLIED over the verdict line",
          rc == 0 and snapshot(root) == snap and "NOT APPLIED" in out, out[:200])

    root = new_tree()
    snap = snapshot(root)
    rc, out, _ = drive(["run", "zero-pad the cents", "--test", str(root / "t.py"),
                        "--context", str(root), "--edit", "--apply"],
                       workspace={"t.py": "assert True\n",
                                  "money.py": MONEY_FIXED})
    check("through the real command, a patch set that edited the oracle inside "
          "--context comes back REFUSED with a nonzero code and a tree nobody "
          "has to audit",
          rc == 1 and "REFUSED" in out and snapshot(root) == snap, out[:200])

    root = new_tree()
    rc, out, _ = drive(["run", "zero-pad the cents", "--test", str(root / "t.py"),
                        "--context", str(root), "--edit", "--apply"],
                       workspace=None, solved=False)
    check("--apply after a run that did not solve exits nonzero and writes nothing, "
          "so the flag never turns a failure into a partial edit",
          rc == 1 and "wrote nothing" in out
          and text(root, "money.py") == MONEY, out[:200])

    after = sorted(p.name for p in tdir.glob("*.jsonl")) if tdir.is_dir() else []
    check("every command this vector drove left its session in the trace store it "
          "was given, so a check run adds nothing to the repository's record",
          after == before, f"{len(after) - len(before)} new session(s)")


def _solve(task: dict, outs: list[str]) -> loop.SolveResult:
    """`loop.solve` on the edit arm with the generator replaced by a script."""
    real = loop._generate
    calls = iter(outs)

    def stub(model, tokenizer, messages, max_tokens, **kw):
        return next(calls, outs[-1])

    loop._generate = stub
    try:
        return loop.solve(None, None, task, max_attempts=2, edit=True)
    finally:
        loop._generate = real


def _loop_checks() -> None:
    root = new_tree()
    snap = snapshot(root)
    task = {"id": "landing-loop", "prompt": "zero-pad the cents in money.py",
            "test": ORACLE, "edit": True, "multi": True,
            "files": workspace_from_dir(root)}
    r = outcome(lambda: _solve(task, [PATCH]))
    check("the patch arm hands the accepted workspace back on SolveResult, so the "
          "CLI can tell an edit that passed the oracle from an edit that is on "
          "disk",
          not isinstance(r, str) and r.solved and isinstance(r.workspace, dict)
          and r.workspace["money.py"].strip() == MONEY_FIXED.strip()
          and r.workspace["t.py"] == ORACLE, str(r)[:200])
    check("the patch arm writes nothing itself: a verified workspace stays a "
          "proposal until the command is given --apply",
          not isinstance(r, str) and snapshot(root) == snap,
          "the arm edited the tree underneath the CLI")

    root = new_tree()
    task = dict(task, files=workspace_from_dir(root))
    refused = ("# edit: money.py :: cents_that_never_was\n```python\n"
               "def cents_that_never_was():\n    return 1\n```\n")
    r2 = outcome(lambda: _solve(task, [refused]))
    check("an arm that never passed the oracle hands back no workspace, so --apply "
          "has nothing to land and says so rather than writing the last proposal",
          not isinstance(r2, str) and not r2.solved and r2.workspace is None,
          str(r2)[:200])

    # R-7.15b's verb, driven through the arm that has to accept it: the oracle
    # below can only pass a file that did not exist before the patch.
    root = new_tree()
    snap = snapshot(root)
    ct = {"id": "create-arm", "prompt": "add k_to_c to money.py",
          "test": CREATE_TEST, "edit": True, "multi": True,
          "files": workspace_from_dir(root)}
    r3 = outcome(lambda: _solve(ct, [CREATE_PATCH]))
    arm_snap = snapshot(root)
    rows3 = [] if isinstance(r3, str) else try_land(root, ct["files"], r3.workspace)
    check("create: the arm ACCEPTS `+k_to_c`, passes an oracle only the new "
          "definition can satisfy, and hands the grown file back as the workspace",
          not isinstance(r3, str) and r3.solved and isinstance(r3.workspace, dict)
          and r3.workspace["money.py"] == MONEY_CREATED, str(r3)[:200])
    check("create: ...and once --apply is given, that workspace is on disk with "
          "the old definition first and the new one last at +4 -0 — the position "
          "the AST chose, not one the model typed",
          not isinstance(r3, str) and text(root, "money.py") == MONEY_CREATED
          and rows3 == [("money.py", 4, 0)], f"{rows3} {text(root, 'money.py')!r}")
    check("create: the arm itself wrote nothing, even for a verb that only adds, "
          "so the tree a create passed on in memory is still the tree it was read "
          "from until --apply says otherwise",
          arm_snap == snap and text(root, "t.py") == ORACLE
          and text(root, "tax.py") == TAX, "the create moved a file it never named")

    root = new_tree()
    snap = snapshot(root)
    ct2 = dict(ct, files=workspace_from_dir(root))
    r4 = outcome(lambda: _solve(ct2, [CREATE_CLASH]))
    check("create: a create the arm REFUSES — the name is already there, so the "
          "address duplicates a definition — hands back no workspace and leaves "
          "the tree byte-identical, which is the state a session's `refused=1` "
          "line is read against",
          not isinstance(r4, str) and not r4.solved and r4.workspace is None
          and snapshot(root) == snap, f"{str(r4)[:140]}")
    check("create: ...and the refusal the model gets back on its next attempt "
          "carries the remedy, because `+cents` is one character away from the "
          "form that would have worked",
          not isinstance(r4, str) and r4.attempts
          and "already exists" in r4.attempts[-1].err
          and "without the '+'" in r4.attempts[-1].err,
          f"{str(r4.attempts[-1].err)[:160] if r4.attempts else str(r4)[:140]}")


def _doc_checks() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    check("--apply is in the README next to the --edit it belongs to, so nobody "
          "learns the only safe way to write by reading source",
          "--apply" in readme and "NOT APPLIED" in readme,
          f"{readme.count('--apply')} mention(s) in README.md")
    check("SPEC R-3.2 carries the clause this vector gates, including that writing "
          "is opt-in and the state of the tree is always printed",
          "--apply" in spec and "NOT APPLIED" in spec,
          f"{spec.count('--apply')} mention(s) in SPEC.md")


def run_checks() -> int:
    cleanup()
    CHECKS.clear()
    _land_checks()
    _edits_checks()
    _cli_checks()
    _loop_checks()
    _doc_checks()
    return sum(1 for _, ok, _ in CHECKS if not ok)


def report() -> int:
    bad = run_checks()
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}"
              + ("" if ok or not detail else f"\n      -> {detail}"))
    print(f"\nR-3.2 clause 3, patch landing: {len(CHECKS) - bad}/{len(CHECKS)} "
          f"checks passed")
    return 1 if bad else 0


# ------------------------------------------------------------------- the mutants

def mutants() -> list[tuple[str, object, object, str]]:
    """Each entry: a bug, where it goes back, and the check that must catch it."""
    P, C, L = patches, cli, loop
    _solve_edits, _build = L._solve_edits, C.build_parser
    _mkcreate = P._resolve_create

    def never_relativizes(path, root):
        return path

    def guard_shrugs(args):
        return None

    def forgets_workspace(*a, **k):
        r = _solve_edits(*a, **k)
        r.workspace = None
        return r

    def widens_apply():
        p = _build()
        subs = p._subparsers._group_actions[0].choices
        for name in ("run-suite", "resume"):
            subs[name].add_argument("--apply", action="store_true",
                                   help="mutant: a flag that does nothing here")
        return p

    def create_ignores_the_anchor(patch, src, defs):
        """R-7.15b's position rule, dropped: every create is told to insert above
        the file's first line, so the definition that was already there ends up
        under the one the model just added."""
        a = _mkcreate(patch, src, defs)
        return patches.Applied(patch, 1, 0, a.col, 0)

    return [
        ("rewrites every file in the workspace, so an untouched sibling loses its "
         "mtime and a whole tree looks like a change",
         (P, "land"), land_copy(skip=False), "keeps its bytes *and* its mtime"),
        ("lets an address escape the project root, where `# edit: ../../etc/hosts` "
         "is one string away from a real one",
         (P, "land"), land_copy(escape=False), "outside the project root is refused"),
        ("adds a file the oracle never scored, so a name that merely appeared in "
         "the response enters the project unverified",
         (P, "land"), land_copy(newfile=False), "never scored is refused"),
        ("ignores the protected list, so the patch set that weakened the assertion "
         "is a fix after all",
         (P, "land"), land_copy(protect=False), "protected file the patch set"),
        ("refuses any protected key it sees, changed or not, so a patch on one "
         "module can never land while its oracle sits in the tree",
         (P, "land"), land_copy(protect_unchanged=True), "left byte-identical"),
        ("writes as it validates, so one refused address leaves the rest of the "
         "set on disk and the tree is no longer what passed",
         (P, "land"), land_copy(atomic=False), "stays unwritten"),
        ("skips the directory creation, so a nested key in a package that moved "
         "raises instead of landing",
         (P, "land"), land_copy(mkdir=False), "is recreated, not skipped"),
        ("counts diff hunks and prints them as lines, which is how a one-hunk "
         "rewrite of twenty lines arrives as +1 -1",
         (P, "land"), land_copy(ops=True), "are lines, not diff hunks"),
        ("splits on newlines, so a file's trailing empty element is reported as a "
         "line that was deleted",
         (P, "land"), land_copy(phantom=True), "trailing newline alone"),
        ("reports the rows without writing them, so the command claims a change "
         "the disk does not have",
         (P, "land"), land_copy(write=False), "--apply writes the changed file"),
        ("deletes whatever the listing stopped covering, turning a `limit` in a "
         "workspace into missing source",
         (P, "land"), land_copy(delete=True), "is left on disk"),
        ("writes the pre-patch bytes back over the tree, so the file that changed "
         "is the one that did not",
         (P, "land"), land_copy(stale=True), "lands with the workspace's bytes"),
        ("computes no protected key at all, so through the command an edited "
         "oracle lands beside the fix",
         (C, "_land_edits"), edits_copy(protect=False), "is refused, the sibling"),
        ("protects the whole tree instead of the oracle, which refuses every real "
         "patch and turns --apply into a no-op",
         (C, "_land_edits"), edits_copy(protect_tree=True), "outside --context leaves"),
        ("exits 0 on a refusal, so a write-back that wrote nothing reads as a "
         "success to whatever scripted the command",
         (C, "_land_edits"), edits_copy(refuse_rc=0), "prints REFUSED with the reason"),
        ("calls an in-memory patch set APPLIED, which is the sentence that was the "
         "bug this box exists for",
         (C, "_land_edits"), edits_copy(honest=False), "prints NOT APPLIED"),
        ("exits 0 for an unsolved run because it printed a verdict line anyway, so "
         "a suite could adopt a failure",
         (C, "_land_edits"), edits_copy(verdict_rc=False), "a failed run exits 1"),
        ("stays silent when the workspace matches the tree, leaving the reader no "
         "statement about which state the disk is in",
         (C, "_land_edits"), edits_copy(say_noop=False), "says so instead of printing"),
        ("never relativizes, so the oracle's key never matches the workspace's and "
         "the guard disarms itself",
         (C, "_rel_to"), never_relativizes, "however the path was spelled"),
        ("accepts --apply without --edit and routes the task, so the flag promises "
         "a write-back no arm can produce",
         (C, "_apply_guard"), guard_shrugs, "without --edit is refused"),
        ("hands back no workspace from the arm that solved, so --apply has nothing "
         "to land and says the task was not solved",
         (L, "_solve_edits"), forgets_workspace, "hands the accepted workspace back"),
        ("puts --apply on `run-suite` and `resume`, where there is no single tree "
         "to land and the flag would do nothing at all",
         (C, "build_parser"), widens_apply, "not accepted by `run-suite`"),
        ("writes the verified create onto the disk but prints `+0 -0` for it, so "
         "the sentence for a landed insertion is the one a no-op gets",
         (P, "land"), land_copy(insert_zero=True), "prints the lines it ADDED"),
        ("takes a create's position from the patch instead of from the AST, so the "
         "new definition lands above the ones that were already in the file",
         (P, "_resolve_create"), create_ignores_the_anchor,
         "old definition first and the new one last"),
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
    print(f"patch-landing mutants: {ran - escaped}/{ran} caught"
          + ("" if one is None else f"  [{one}]"))
    return escaped


def sweep() -> int:
    """One fresh process per mutant, then the whole list in this one.

    The in-process lane puts a copy over `flash.patches.land` — the name three of
    the command lanes reach through `flash.cli`'s call-time import — and the copy
    one bug leaves behind is the next bug's starting state. So this spawns one
    process per bug, re-runs the list here, and fails unless both lanes catch all
    of them.
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
    print(f"patch-landing mutants, fresh process each: {caught}/{NUM_BUGS} caught")
    here = run_mutants()
    print(f"patch-landing mutants, one process:        "
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
    bad = report()
    if "--sweep" in argv:
        return 1 if (bad or sweep()) else 0
    if "--mutants" in argv:
        return 1 if bad or run_mutants(verbose="-v" in argv) else 0
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
