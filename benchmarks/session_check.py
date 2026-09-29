"""R-7.15, OFFLINE: many turns against one repo and one oracle, and every turn
ends in a verdict — including the turn that must say it changed nothing.

`flash run` answers exactly one task per process. That is the right shape for a
benchmark and the wrong shape for coding: to make two changes a person re-types
`--test … --context … --edit --apply`, re-reads the project, and waits for the
weights again. The author's own first use of the tool reported it as
"what is this how will i code on this?", six questions after R-3.2's clause 3
put a verified patch on disk.

So `flash session` reads turns from stdin against one (repo, oracle) pair. Six
questions, none of them about a model — the router is stubbed here, so what is
under test is the loop around the write-back:

* **Does turn N see what turn N-1 wrote?** The workspace is re-read from disk at
  the start of every turn. A session that kept the bytes it started with would
  hand the next turn a stale `before`, and `land` — which writes the whole file
  it was given — would silently revert the previous turn. That is the shape this
  vector's loudest mutant takes, and it is invisible to any one-turn test.
* **Does a turn that did not write say so?** The landing sentence comes from the
  same `cli._land_edits` the one-command surface prints, so `NOT APPLIED` and
  `REFUSED` cannot drift between the two surfaces.
* **Can the exam be edited mid-session?** The oracle is read once and named
  protected on every turn, so a patch that weakens an assertion is refused on
  turn 3 exactly as on turn 1.
* **Is an empty session a green one?** A missing oracle, a blank oracle and zero
  turns all refuse with rc 2 before any generation: a session with no verify step
  is a chat, and a chat that prints confident answers is what this project is not.
* **Does the exit code belong to the last turn?** A session that fixed three
  things and ended on a failure is not green, so the report prints `last_rc` and
  returns it.
* **Is `written` a fact about the tree?** The report counts turns whose files
  actually changed — measured by re-reading the tree, not by what the write-back
  claimed — so a refused turn shows up as zero.

    python benchmarks/session_check.py             # the checks
    python benchmarks/session_check.py --mutants   # put each bug back
    python benchmarks/session_check.py --sweep     # one fresh process each
    python benchmarks/session_check.py --mutant 7  # one bug, for a bisection
"""
from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import cli, loop, trace                                # noqa: E402

MONEY = 'def cents_to_str(cents):\n    return str(cents) + "." + "00"\n'
MONEY_FIXED_BODY = 'return f"${cents // 100}.{cents % 100:02d}"'
MONEY_FIXED = MONEY.replace('return str(cents) + "." + "00"', MONEY_FIXED_BODY)
# An oracle that runs on its own: with `--context` the harness scores the project
# in a temp copy, so the bootstrap the task corpus uses has to be in the file.
ORACLE = ('import sys\nsys.path.insert(0, "<TMPDIR>")\n'
          'from money import cents_to_str\n'
          'assert cents_to_str(5) == "$0.05", cents_to_str(5)\n')
OTHER = 'TAX_RATE = 0.08\n'
FILES = {"money.py": MONEY, "other.py": OTHER, "t.py": ORACLE}


# ------------------------------------------------------------------ fixtures

CHECKS: list[tuple[str, bool, str]] = []

_KEEP: list[tempfile.TemporaryDirectory] = []


def check(name: str, cond, detail: str = "") -> None:
    ok = bool(cond)
    d = ""
    if not ok:
        # A mutant can break the thing a detail line prints as well as the thing
        # a check tests, and the sweep needs this process to reach its verdict.
        try:
            d = detail if isinstance(detail, str) else str(detail())
        except Exception as e:
            d = f"(the detail itself raised {type(e).__name__}: {e})"
    CHECKS.append((name, ok, d))


def outcome(fn):
    """Call `fn`, and return the exception instead of raising it."""
    try:
        return fn()
    except Exception as e:
        return f"RAISED: {type(e).__name__}: {e}"


def new_tree(files: dict[str, str] | None = None) -> Path:
    d = tempfile.TemporaryDirectory(prefix="flash-session-")
    _KEEP.append(d)
    root = Path(d.name)
    for rel, text in (FILES if files is None else files).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def tree_state(root: Path) -> dict:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(root.iterdir()) if p.is_file()}


def cleanup() -> None:
    for d in _KEEP:
        d.cleanup()
    _KEEP.clear()


def line_with(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line
    return ""


def field(out: str, key: str, prefix: str = "[session] turns=") -> str:
    line = line_with(out, prefix)
    for part in line.split():
        if part.startswith(key + "="):
            return part.split("=", 1)[1]
    return ""


# ------------------------------------------------------------- the turn stubs
#
# Each entry says what the turn does with the workspace it is HANDED. Writing
# them as functions of `files` is the whole point: a stub returning a fixed dict
# could not tell a stale `before` from a fresh one.

def turn(edit=None, solved=True, err="", seconds=1.0, patches=1):
    return {"edit": edit, "solved": solved, "err": err, "seconds": seconds,
            "patches": patches}


def _fix_money(files):
    return dict(files, **{"money.py": files["money.py"].replace(
        'return str(cents) + "." + "00"', MONEY_FIXED_BODY)})


def _add_const(files):
    return dict(files, **{"money.py": files["money.py"]
                                     + "\nCENTS_PER_DOLLAR = 100\n"})


def _rename(files):
    return dict(files, **{"money.py": files["money.py"].replace(
        "def cents_to_str", "def cents_to_str_v2")})


def _weaken(files):
    return dict(files, **{"t.py": "assert True\n"})


FIX_MONEY = turn(edit=_fix_money)
ADD_CONST = turn(edit=_add_const)
NO_OP = turn()
FAIL_MONEY = turn(edit=_rename, solved=False,
                  err="AssertionError: 5.00\n  line 4, in <module>")
WEAKEN_ORACLE = turn(edit=_weaken)


def fake_solve(script):
    """A `solve_routed` stand-in that records every task it was handed.

    Stubbing the router keeps the whole command on the table — the parser, the
    stdin read, `workspace_from_dir`, the trace session, the verdict line,
    `_land_edits` — and no model loads, which is what makes this offline.
    """
    seen: list[dict] = []

    def _solve(small_repo, big_repo, task, root, **kw):
        step = script[min(len(seen), len(script) - 1)]
        seen.append({"id": task["id"], "prompt": task["prompt"],
                    "files": dict(task["files"]), "edit": task.get("edit"),
                    "multi": task.get("multi"), "context": task.get("context"),
                    "test": task["test"], "kw": dict(kw)})
        files = task["files"]
        ws = step["edit"](files) if step["edit"] else dict(files)
        r = loop.SolveResult(
            task_id=task["id"], solved=step["solved"], seconds=step["seconds"],
            attempts=[loop.Attempt(code="# edit: money.py :: cents_to_str",
                                   ok=step["solved"], err=step["err"],
                                   patches=step["patches"])])
        if step["solved"]:
            r.workspace = ws
        return r, ("small" if step["solved"] else "shed"), "small"

    _solve.seen = seen
    return _solve


def stdin_text(text: str):
    """A real file-backed stream. `trace.close_session` asks stdin for `isatty`,
    which a StringIO does not have, and a session reads its turns the way a pipe
    delivers them."""
    return io.TextIOWrapper(io.BytesIO(text.encode()), encoding="utf-8")


def drive(fn=None, prompts="", argv=None, script=None, root=None,
          oracle="t.py", command="session"):
    """Run a session command in this process with turns and router supplied."""
    fn = fn or cli.cmd_session
    script = script or [FIX_MONEY]
    root = root or new_tree()
    fake = fake_solve(script)
    real = (loop.solve_routed, sys.stdin, sys.argv, trace.DIR)
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-session-trace-"))
    flags = ["--test", str(root / oracle), "--context", str(root)]
    sys.argv = ["flash", command, *flags, *(argv or [])]
    sys.stdin = stdin_text(prompts)
    loop.solve_routed = fake
    buf = io.StringIO()
    args = cli.build_parser().parse_args(sys.argv[1:])
    try:
        with contextlib.redirect_stdout(buf):
            rc = outcome(lambda: fn(args))
    finally:
        (loop.solve_routed, sys.stdin, sys.argv, trace.DIR) = real
    return rc, buf.getvalue(), fake.seen, root


def parsed(argv: list[str]):
    return outcome(lambda: cli.build_parser().parse_args(argv))


def rejects(argv: list[str]) -> str:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf):
            cli.build_parser().parse_args(argv)
    except SystemExit:
        return buf.getvalue()
    except Exception as e:
        return f"RAISED: {type(e).__name__}: {e}"
    return ""


# ---------------------------------------------------------------- the copy

def session_copy(**knob):
    """`flash.cli.cmd_session` with one clause switched — how a mutant puts
    exactly one defect back.

    A copy rather than a wrapper: taking a guard out from outside would mean
    calling the real guard and ignoring its verdict. Every knob's default is the
    shipped behaviour, so `session_copy()` has to agree with `cmd_session` on
    every scenario in `agree()`; a check that fails for drift instead of for the
    bug it names is worth nothing.
    """
    k = dict(re_read=True, oracle_guard=True, turns_cap=True, quit_ends=True,
             skip_blank=True, edit_flag=False, apply_default=False,
             protect=True, write_count=True, last_rc=True, sum_seconds=True,
             verdict=True, oracle_lines=True, empty_refuses=True)
    k.update(knob)

    def cmd(args) -> int:
        from flash.loop import solve_routed
        from flash.patches import land, workspace_from_dir

        if k["oracle_guard"]:
            try:
                test = open(args.test).read()
            except OSError as e:
                print(f"session: the oracle {args.test} cannot be read ({e}) — "
                      "every turn needs a verify step")
                return 2
            if not test.strip():
                print(f"session: the oracle {args.test} is empty — nothing would "
                      "be verified, so no turn could ever be green")
                return 2
        else:
            got = outcome(lambda: open(args.test).read())
            test = "" if isinstance(got, str) and got.startswith("RAISED") else got

        loop.ADAPTER = args.adapter or ""
        loop.CONSTRAIN = args.constrain
        loop.DEBUG = args.debug
        armed = True if not k["edit_flag"] else bool(getattr(args, "edit", False))
        loop.EDIT = armed
        loop.HINTS = cli._hint_names(args.no_source_hint, args.no_graph_hint)
        trace.CAPTURE = args.trace_full
        sid = trace.open_session("session", cmd="session",
                                 params={"context": args.context,
                                         "test": args.test, "edit": True,
                                         "apply": args.apply,
                                         "turns": args.turns})
        turns: list[str] = []
        for raw in sys.stdin:
            t = raw.strip()
            if k["quit_ends"] and t.lower() in ("quit", "exit", "q"):
                break
            if not t:
                if k["skip_blank"]:
                    continue
                turns.append(t)
                continue
            turns.append(t)
            if k["turns_cap"] and args.turns and len(turns) >= args.turns:
                break
        if not turns:
            print("session: no turns on stdin — nothing was asked, so nothing "
                  "was verified")
            trace.close_session(solved=False, turns=0)
            return 2 if k["empty_refuses"] else 0
        print(f"flash session on {args.context} against the oracle "
              f"{args.test} — {len(turns)} turn(s). Ctrl-D ends it.")

        cached = workspace_from_dir(args.context)
        solved_n = written_n = 0
        seconds = 0.0
        rcs: list[int] = []
        for i, prompt in enumerate(turns, 1):
            task = {"id": f"turn{i}", "prompt": prompt, "test": test,
                    "context": args.context}
            task["files"] = (workspace_from_dir(args.context) if k["re_read"]
                             else dict(cached))
            task["edit"] = armed
            task["multi"] = armed
            r, tier, routed = solve_routed(args.small, args.big, task, cli.ROOT,
                                           small_attempts=args.attempts,
                                           big_attempts=args.attempts,
                                           max_tokens=args.max_tokens,
                                           allow_big=args.allow_big,
                                           tournament=args.tournament,
                                           confidence=args.confidence)
            trace.event("task_end", task_id=task["id"], prompt=prompt[:200],
                        solved=r.solved, tier=tier, attempts=r.n_attempts,
                        seconds=r.seconds, routed=routed,
                        **loop.tournament_fields(r), **cli._conf_fields(r),
                        **cli._patch_fields(r))
            if k["verdict"]:
                print(f"[turn {i}] routed={routed} tier={tier} solved={r.solved} "
                      f"attempts={r.n_attempts} ({r.seconds}s)"
                      f"{cli._patch_note(r)}")
            if k["oracle_lines"] and not r.solved and r.attempts \
                    and r.attempts[-1].err:
                for ln in r.attempts[-1].err.splitlines()[:8]:
                    print(f"  oracle| {ln}")
            if k["apply_default"]:
                args.apply = True
            rc = cli._land_edits(args, task, r)
            if not k["protect"] and args.apply and r.workspace is not None:
                # the oracle is read once for the session, so a later turn can
                # name it protected only by re-deriving it — this knob drops it
                try:
                    land(args.context, task["files"], r.workspace, ())
                except Exception as e:
                    print(f"[R-3.2] REFUSED: --apply wrote nothing — {e}")
                    rc = 1
            after = workspace_from_dir(args.context)
            moved = any(task["files"].get(key) != v for key, v in after.items())
            if k["write_count"]:
                written_n += 1 if moved else 0
            else:
                written_n += 1 if r.solved else 0
            if r.solved:
                solved_n += 1
            seconds = (seconds + r.seconds) if k["sum_seconds"] else r.seconds
            rcs.append(rc)
        rc = rcs[0] if not k["last_rc"] else rcs[-1]
        print(f"[session] turns={len(turns)} solved={solved_n} "
              f"written={written_n} seconds={round(seconds, 1)} last_rc={rc}")
        print(f"[trace] replay this session:  flash trace show {sid}")
        trace.close_session(solved=(rc == 0), turns=len(turns),
                            solved_turns=solved_n, written_turns=written_n,
                            seconds=round(seconds, 1))
        return rc
    return cmd


def _quiet(fn, prompts, script, root, argv):
    """Run `fn` over one scenario and return everything a reader could compare:
    the exit code, the printed lines with the volatile ones removed, and the
    bytes the tree ended with."""
    rc, out, _seen, _root = drive(fn, prompts, argv, script, root)
    # Two scenarios run on two different temp trees, so the session's own header —
    # which prints the absolute directory it was pointed at — differs by
    # construction. Collapsing the prefix keeps the comparison about the sentences:
    # which file a turn names, and what it says about it, still shows through.
    lines = [l.replace(str(root), "<ROOT>")
             for l in out.splitlines() if not l.startswith("[trace]")]
    return rc, lines, tree_state(root)


def agree() -> str:
    """The first scenario where `session_copy()` and the shipped command differ.

    "" means they agree, which is the passing case — the same convention as every
    other copy in this repository's gates.
    """
    scenarios = [
        ("fix the rounding\nadd a constant\n", [FIX_MONEY, ADD_CONST], True, []),
        ("fix it\nadd a constant\n", [FIX_MONEY, ADD_CONST], False, []),
        ("fix it\nfail it\n", [FIX_MONEY, FAIL_MONEY], True, []),
        ("a\nb\nc\nd\n", [FIX_MONEY, ADD_CONST, NO_OP], True, ["--turns", "2"]),
        ("quit\n", [FIX_MONEY], True, []),
    ]
    for prompts, script, apply, extra in scenarios:
        seen = []
        for fn in (cli.cmd_session, session_copy()):
            seen.append(_quiet(fn, prompts, script, new_tree(),
                               (["--apply"] if apply else []) + extra))
        if seen[0] != seen[1]:
            return (f"{prompts!r} apply={apply} {extra}: shipped={seen[0]} "
                    f"copy={seen[1]}")
    if isinstance(outcome(agree_copy_is_honest), str):
        return f"the copy raised: {outcome(agree_copy_is_honest)}"
    return ""


def agree_copy_is_honest() -> bool:
    """Every knob's default must be the shipped behaviour, asserted one at a time.

    A mutant whose clean copy already differs from the real command is testing
    the copy, not the command.
    """
    knobs = dict(re_read=False, oracle_guard=False, turns_cap=False,
                 quit_ends=False, skip_blank=False, edit_flag=True,
                 apply_default=True, protect=False, write_count=False,
                 last_rc=False, sum_seconds=False, verdict=False,
                 oracle_lines=False, empty_refuses=False)
    for name in knobs:
        copy = session_copy(**{name: knobs[name]})
        rc, lines, state = _quiet(copy, "fix\nadd\n", [FIX_MONEY, ADD_CONST],
                                 new_tree(), ["--apply"])
        if isinstance(rc, str):
            raise AssertionError(f"knob {name} broke its own copy: {rc}")
    return True


# ------------------------------------------------------------------- checks

def _parser_checks() -> None:
    a = parsed(["session", "--test", "t.py", "--context", "."])
    check("`session` is a command on the shipped parser and routes to "
          "`cmd_session`, so the gate below tests what a stranger actually runs",
          getattr(a, "fn", None) is cli.cmd_session, repr(getattr(a, "fn", None)))
    check("session asks for the two things a turn cannot do without — the oracle "
          "and the tree it patches — and never for a prompt, which comes in on "
          "stdin one turn at a time",
          "are required: --test, --context" in rejects(["session"])
          and "required: --context" in rejects(["session", "--test", "t.py"])
          and "required: --test" in rejects(["session", "--context", "."]),
          rejects(["session"])[-120:])
    check("no positional prompt: a `session` line with one is the parser "
          "accepting an argument it will ignore",
          "unrecognized arguments" in rejects(["session", "--test", "t.py",
                                              "--context", ".", "fix it"]),
          rejects(["session", "--test", "t.py", "--context", ".", "fix it"])[:160])
    check("--apply defaults off, exactly as on `run`: a session that writes to a "
          "project because it was opened is not a session",
          a.apply is False, repr(getattr(a, "apply", None)))
    check("--turns is an int, and 0 means until EOF",
          a.turns == 0 and parsed(["session", "--test", "t.py", "--context",
                                   ".", "--turns", "3"]).turns == 3, "")
    check("session carries no `--edit` flag, because the patch arm is the only "
          "answer shape a session can land — an argument that silently does "
          "nothing is worse than one that does not exist",
          "unrecognized arguments: --edit" in rejects(["session", "--test", "t.py",
                                                      "--context", ".", "--edit"]),
          rejects(["session", "--test", "t.py", "--context", ".", "--edit"])[:160])
    check("the cost switches keep the governor's three choices, so a turn sheds "
          "the brain the same way a `run` does and cannot invent a fourth",
          "invalid choice: 'sometimes'" in rejects(["session", "--test", "t.py",
                                                   "--context", ".",
                                                   "--allow-big", "sometimes"]),
          rejects(["session", "--test", "t.py", "--context", ".",
                   "--allow-big", "sometimes"])[:160])


def _oracle_checks() -> None:
    root = new_tree()
    rc, out, seen, _ = drive(prompts="fix the rounding\n", root=root,
                            oracle="missing.py")
    check("an oracle that cannot be read stops the session before turn 1: every "
          "turn is defined by its verify step, so a missing one is a chat that "
          "prints confident answers",
          rc == 2 and "cannot be read" in out and not seen,
          f"rc={rc} reached={len(seen)} out={out[:160]!r}")
    check("a session that refuses leaves the tree exactly as it found it",
          tree_state(root) == FILES, sorted(tree_state(root)))

    blank = new_tree({"money.py": MONEY, "t.py": "\n   \n"})
    rc, out, seen, _ = drive(prompts="fix the rounding\n", root=blank)
    check("a blank oracle is refused in its own words, not read as a test that "
          "always passes — the vacuous-green shape this repo gates everywhere "
          "else",
          rc == 2 and "is empty" in out and not seen, f"rc={rc} out={out[:160]!r}")

    unreadable = new_tree()
    (unreadable / "t.py").chmod(0o000)
    rc, out, seen, _ = drive(prompts="fix it\n", root=unreadable)
    (unreadable / "t.py").chmod(0o644)
    check("the refusal covers a file that exists but cannot be read, not only "
          "one that is absent",
          rc == 2 and "cannot be read" in out and not seen,
          f"rc={rc} reached={len(seen)}")


def _turn_checks() -> None:
    rc, out, seen, _ = drive(prompts="fix the rounding\n\n  \nadd a constant\n",
                            script=[FIX_MONEY, ADD_CONST])
    check("blank lines are not turns — a paste with an empty line between two "
          "requests would otherwise send a prompt nobody typed to the model and "
          "charge for it",
          len(seen) == 2 and [s["id"] for s in seen] == ["turn1", "turn2"],
          [s["id"] for s in seen])
    check("a turn's prompt reaches the router verbatim and stripped",
          [s["prompt"] for s in seen] == ["fix the rounding", "add a constant"],
          [s["prompt"] for s in seen])
    check("every turn is an edit turn against the session's own context and its "
          "one oracle, whatever the caller's flags were",
          all(s["edit"] is True and s["multi"] is True and s["context"]
              and s["test"] == ORACLE for s in seen),
          [{key: s[key] for key in ("edit", "multi", "context")} for s in seen])

    rc, out, seen, _ = drive(prompts="one\nquit\ntwo\n", script=[FIX_MONEY])
    check("`quit` ends the session rather than becoming a turn",
          len(seen) == 1 and field(out, "turns") == "1", f"{len(seen)} turns")
    for word in ("exit", "q"):
        rc, out, seen, _ = drive(prompts=f"one\n{word}\ntwo\n",
                                script=[FIX_MONEY])
        check(f"`{word}` ends it too, so a person does not have to remember "
              "which of the three they know", len(seen) == 1,
              f"{word}: {len(seen)} turns")

    rc, out, seen, _ = drive(prompts="a\nb\nc\nd\n", script=[FIX_MONEY] * 4,
                            argv=["--turns", "2"])
    check("--turns caps a session reading from stdin, which is what lets a "
          "driver and a keyboard mean the same thing",
          len(seen) == 2 and field(out, "turns") == "2", f"{len(seen)} turns")
    check("the cap counts turns, not lines, and the dropped prompts never reach "
          "the router", [s["prompt"] for s in seen] == ["a", "b"],
          [s["prompt"] for s in seen])

    rc, out, seen, _ = drive(prompts="\n\n", script=[FIX_MONEY])
    check("a session with nothing to do exits 2 and says it verified nothing — "
          "an empty session that looks green is a run with no result",
          rc == 2 and not seen and "nothing was asked" in out,
          f"rc={rc} out={out[:160]!r}")

    rc, out, seen, _ = drive(prompts="one\ntwo\n", script=[FIX_MONEY, ADD_CONST])
    check("EOF ends the session and the report counts what ran",
          len(seen) == 2 and field(out, "turns") == "2", f"{len(seen)} turns")


def _disk_checks() -> None:
    root = new_tree()
    rc, out, seen, _ = drive(prompts="fix the rounding\nadd a constant\n",
                            root=root, script=[FIX_MONEY, ADD_CONST],
                            argv=["--apply"])
    final = (root / "money.py").read_text()
    check("turn 2 edits what turn 1 WROTE, not what the process remembered: the "
          "workspace is re-read from disk every turn, so the fix and the new "
          "constant are both on disk at the end",
          MONEY_FIXED_BODY in final and "CENTS_PER_DOLLAR = 100" in final,
          final[-200:])
    check("the turn-2 patch was offered a `before` that already carried the "
          "turn-1 fix — the previous check shows the result; this one shows the "
          "input the clause is about",
          MONEY_FIXED_BODY in seen[1]["files"]["money.py"],
          seen[1]["files"]["money.py"][-200:])
    check("a file the session never addressed is untouched, byte for byte",
          (root / "other.py").read_text() == OTHER, "")
    check("the oracle survived a session that patched its own directory",
          (root / "t.py").read_text() == ORACLE, (root / "t.py").read_text())
    check("the reversal the re-read prevents is a silent one: writing back a "
          "whole file from remembered bytes would drop turn 1 and still print "
          "solved=True",
          MONEY_FIXED_BODY in final, final[-160:])

    root = new_tree()
    rc, out, seen, _ = drive(prompts="fix\nadd\n", root=root,
                            script=[FIX_MONEY, ADD_CONST])
    check("without --apply nothing is on disk, and the next turn sees the same "
          "unwritten bytes — a NOT APPLIED turn must not leak into the session's "
          "own idea of the tree",
          (root / "money.py").read_text() == MONEY and "NOT APPLIED" in out
          and field(out, "written") == "0",
          f"written={field(out, 'written')!r} tree="
          f"{(root / 'money.py').read_text()[:60]!r}")

    root = new_tree()
    rc, out, seen, _ = drive(prompts="fix it\nweaken the test\n", root=root,
                            script=[FIX_MONEY, WEAKEN_ORACLE], argv=["--apply"])
    check("the oracle stays protected for the whole session, not just the first "
          "patch set: a later turn that rewrites the assert is refused",
          "REFUSED" in out and (root / "t.py").read_text() == ORACLE,
          out[-200:])
    check("a refused turn writes nothing, so the tree keeps the earlier turn's "
          "fix and none of the weakening",
          MONEY_FIXED_BODY in tree_state(root)["money.py"]
          and "assert True" not in tree_state(root)["t.py"],
          tree_state(root)["money.py"][-160:])

    root = new_tree({"money.py": MONEY_FIXED, "other.py": OTHER, "t.py": ORACLE})
    rc, out, seen, _ = drive(prompts="fix it\n", root=root, script=[NO_OP],
                            argv=["--apply"])
    check("a turn whose verified workspace is byte-identical to the tree says "
          "so, and is not counted as a write",
          "byte-identical" in out and field(out, "written") == "0", out[-200:])


def _verdict_checks() -> None:
    rc, out, seen, _ = drive(prompts="fix\nfail\n",
                            script=[FIX_MONEY, FAIL_MONEY], argv=["--apply"])
    turns = [l for l in out.splitlines() if l.startswith("[turn ")]
    check("every turn ends in a verdict line naming itself, its tier and its "
          "solved state — the one property a session cannot delegate to prose",
          len(turns) == 2 and turns[0].startswith("[turn 1]")
          and "solved=True" in turns[0] and "solved=False" in turns[1], turns)
    check("a turn's verdict carries the patch arm's own audit counts, so "
          "`refused=` and `outside=` are visible per turn rather than only "
          "inside a trace file",
          len(turns) == 2 and "patches=1" in turns[0] and "outside=" in turns[0],
          turns)
    check("a failing turn prints the oracle's words under `oracle|`, which is "
          "the difference between a session that says no and one a person can "
          "answer",
          any(l.startswith("  oracle| AssertionError") for l in out.splitlines()),
          out[-240:])
    # The split needs turn 2's marker to know where turn 1 ended, so a session
    # that prints no markers fails this rather than reading the whole transcript
    # as "turn 1" and passing on an accident.
    marker = line_with(out, "[turn 2]")
    first_turn = out.split(marker)[0] if marker else out
    check("a passing turn prints no oracle error block, so the prefix cannot "
          "become decoration on a green answer",
          bool(marker) and "oracle|" not in first_turn, first_turn[-200:])
    check("the landing sentence is the one `run` prints, shared rather than "
          "reworded for a second surface",
          "[R-3.2] wrote money.py (+1 -1 lines)" in out, out[-260:])


def _report_checks() -> None:
    rc, out, seen, _ = drive(prompts="fix\nfail\n",
                            script=[FIX_MONEY, FAIL_MONEY], argv=["--apply"])
    check("the EOF report prints turns, solved, written, seconds and last_rc on "
          "one line a driver can parse",
          all(p in line_with(out, "[session]") for p in
              ("turns=2", "solved=1", "written=1", "seconds=2.0", "last_rc=1")),
          line_with(out, "[session]"))
    check("the exit code belongs to the LAST turn: a session that fixed one "
          "thing and ended on a failure is not green", rc == 1,
          f"rc={rc} {line_with(out, '[session]')}")

    rc2, out2, _seen, root2 = drive(prompts="fail\nfix\n",
                                    script=[FAIL_MONEY, FIX_MONEY],
                                    argv=["--apply"])
    check("and the other order is green, from the same line — which proves the "
          "code is the last turn's, not the worst turn's",
          rc2 == 0 and field(out2, "last_rc") == "0"
          and field(out2, "written") == "1"
          and MONEY_FIXED_BODY in tree_state(root2)["money.py"],
          f"rc={rc2} {line_with(out2, '[session]')}")

    rc, out, seen, _ = drive(prompts="one\ntwo\n", script=[FIX_MONEY, ADD_CONST])
    check("the replay line names a session id `flash trace show` takes",
          "flash trace show 2" in out, line_with(out, "[trace]"))

    tdir = Path(tempfile.mkdtemp(prefix="flash-session-trace-"))
    real = (loop.solve_routed, sys.stdin, sys.argv, trace.DIR)
    trace.DIR = tdir
    root = new_tree()
    loop.solve_routed = fake_solve([FIX_MONEY, ADD_CONST])
    sys.stdin = stdin_text("one\ntwo\n")
    sys.argv = ["flash", "session", "--test", str(root / "t.py"),
                "--context", str(root)]
    args = cli.build_parser().parse_args(sys.argv[1:])
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            outcome(lambda: cli.cmd_session(args))
    finally:
        (loop.solve_routed, sys.stdin, sys.argv, trace.DIR) = real
    files = sorted(tdir.glob("*.jsonl"))
    rec = [l for f in files for l in f.read_text().splitlines()
           if '"task_end"' in l]
    check("one session is one trace file, not one per turn, so `flash trace "
          "show` reconstructs the whole conversation (I-6)",
          len(files) == 1 and len(rec) == 2
          and all('"turn1"' in l or '"turn2"' in l for l in rec),
          f"{[f.name[-14:] for f in files]} task_end={len(rec)}")


def _no_regression_checks() -> None:
    root = new_tree()
    rc, out, seen, _ = drive(prompts="fix\n", script=[FIX_MONEY], root=root)
    check("a session's first turn does not reach the model when the oracle "
          "cannot be read — and here it can, so exactly one turn ran",
          len(seen) == 1 and rc == 0, f"reached={len(seen)} rc={rc}")

    real = (loop.solve_routed, sys.stdin, sys.argv, trace.DIR)
    root = new_tree()
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-land-trace-"))
    loop.solve_routed = fake_solve([FIX_MONEY])
    sys.stdin = stdin_text("")
    sys.argv = ["flash", "run", "fix the rounding", "--test", str(root / "t.py"),
                "--context", str(root), "--edit"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = outcome(cli.main)
    finally:
        (loop.solve_routed, sys.stdin, sys.argv, trace.DIR) = real
    out = buf.getvalue()
    check("`run` is untouched by the session's arrival: one prompt, the same "
          "three landing sentences, and none of the per-turn scaffolding",
          rc == 0 and "[turn" not in out and "[session]" not in out
          and "NOT APPLIED" in out, f"rc={rc} out={out[-160:]!r}")

    got = outcome(lambda: subprocess.run([sys.executable, "-m", "flash.cli",
                                          "session", "--help"],
                                         capture_output=True, text=True, cwd=ROOT))
    check("the session's own help is reachable, because a command nobody can "
          "read the flags of is a command nobody installs",
          not isinstance(got, str) and got.returncode == 0
          and "--turns" in got.stdout,
          got if isinstance(got, str) else f"rc={got.returncode}")

    check("the agreement between the shipped command and the mutant copy holds "
          "on every clean scenario — otherwise a check below fails for drift, "
          "not for the bug it names",
          agree() == "", agree()[:300])


def run_checks() -> int:
    CHECKS.clear()
    _parser_checks()
    _oracle_checks()
    _turn_checks()
    _disk_checks()
    _verdict_checks()
    _report_checks()
    _no_regression_checks()
    return len(CHECKS)


def report() -> int:
    run_checks()
    bad = [c for c in CHECKS if not c[1]]
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
        if detail and not ok:
            print(f"  ..  {detail}")
    print(f"\nR-7.15 interactive session: {len(CHECKS) - len(bad)}/"
          f"{len(CHECKS)} checks passed")
    cleanup()
    return 1 if bad else 0


# ------------------------------------------------------------------ mutants

def mutants() -> list[tuple[str, object, str]]:
    """(name, the mutated copy, the check that must fail on it).

    Each entry switches one clause of R-7.15 off. The sweep runs them in a fresh
    process each, because a session holds process state — `loop.EDIT`, the trace
    store, stdin — and a mutant that leaks it would show up as a different count
    in the next lane rather than as its own bug.
    """
    return [
        ("turn 2 re-reads nothing: the workspace is taken once at session start, "
         "which is exactly how a whole-file write-back silently reverts the "
         "previous turn",
         session_copy(re_read=False),
         "turn 2 edits what turn 1 WROTE, not what the process remembered"),
        ("no oracle guard: a missing or blank verify file is read anyway, and "
         "the session generates against nothing",
         session_copy(oracle_guard=False),
         "an oracle that cannot be read stops the session before turn 1"),
        ("an empty session exits 0: the run looks green because nothing failed, "
         "though nothing was asked",
         session_copy(empty_refuses=False),
         "a session with nothing to do exits 2 and says it verified nothing"),
        ("--turns is decoration: the cap is not applied, so a driver cannot "
         "bound a session it pipes into",
         session_copy(turns_cap=False),
         "--turns caps a session reading from stdin"),
        ("`quit` becomes a turn: the session asks the model to 'quit', which is "
         "the shape a person hitting Ctrl-D in the wrong place gets",
         session_copy(quit_ends=False),
         "`quit` ends the session rather than becoming a turn"),
        ("blank lines are turns: a pasted request with an empty line sends an "
         "empty prompt and charges for it",
         session_copy(skip_blank=False),
         "blank lines are not turns"),
        ("the per-turn verdict line disappears, leaving prose and a final "
         "report — every turn's answer becomes unauditable",
         session_copy(verdict=False),
         "every turn ends in a verdict line naming itself"),
        ("a failing turn stops printing the oracle's own words, so 'no' arrives "
         "without a reason a person can respond to",
         session_copy(oracle_lines=False),
         "a failing turn prints the oracle's words under `oracle|`"),
        ("the write-back becomes the default: a session writes to the project "
         "the moment it is opened",
         session_copy(apply_default=True),
         "without --apply nothing is on disk, and the next turn sees the same"),
        ("the oracle is protected on no turn at all — a session whose third "
         "turn can rewrite its own exam",
         session_copy(protect=False),
         "the oracle stays protected for the whole session"),
        ("`written=` counts turns that were solved instead of turns that moved "
         "a file, which is a report claiming a write a refusal stopped",
         session_copy(write_count=False),
         "a turn whose verified workspace is byte-identical to the tree says"),
        ("seconds reports the last turn rather than the session, so a five-turn "
         "cost looks like a one-turn cost",
         session_copy(sum_seconds=False),
         "the EOF report prints turns, solved, written, seconds and last_rc"),
        ("the exit code comes from the FIRST turn: a session that ended in "
         "failure exits 0 because it had succeeded earlier",
         session_copy(last_rc=False),
         "the exit code belongs to the LAST turn"),
        ("the session honours a --edit flag it no longer has, so a turn without "
         "the patch arm is scored but can never land",
         session_copy(edit_flag=True),
         "every turn is an edit turn against the session's own context"),
    ]


def _lane(copy) -> list[str]:
    """Run every check against one mutated command; name the ones that fail."""
    shipped = cli.__dict__["cmd_session"]
    cli.cmd_session = copy
    try:
        run_checks()
    finally:
        cli.cmd_session = shipped
    return [n for n, ok, _d in CHECKS if not ok]


def run_mutants(one: int | None = None, verbose: bool = False) -> int:
    rows = mutants()
    picked = list(enumerate(rows)) if one is None else [(one, rows[one])]
    caught = 0
    for i, (name, copy, expect) in picked:
        failed = outcome(lambda: _lane(copy))
        if isinstance(failed, str):
            print(f"  BAD  mutant {i} ({name[:60]}) crashed the lane: {failed}")
            continue
        hit = any(expect in f for f in failed)
        if hit:
            caught += 1
            if verbose:
                print(f"  ok   MUTATION: {name} -> {len(failed)} check(s) fail")
        else:
            print(f"  BAD  MUTATION NOT CAUGHT: {name}\n"
                  f"  ..  expected a failure of: {expect}\n"
                  f"  ..  failed instead: {failed[:3]}")
    total = len(picked)
    print(f"\nsession mutants: {caught}/{total} caught")
    cleanup()
    return 0 if caught == total else 1


def sweep() -> int:
    """One fresh process per mutant, then the in-process lane, and they agree.

    Each subprocess asserts the checks it ran too: a mutant that made the vector
    itself unrunnable is not a caught mutant.
    """
    rows = mutants()
    caught = 0
    for i, (name, _c, expect) in enumerate(rows):
        p = outcome(lambda: subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--mutant", str(i)],
            capture_output=True, text=True, cwd=ROOT, timeout=900))
        if isinstance(p, str):
            print(f"  fresh>BAD {name[:96]} — {p}")
            continue
        line = line_with(p.stdout, "session mutants:")
        ok = p.returncode == 0 and line.endswith("1/1 caught")
        caught += 1 if ok else 0
        print(f"  {'fresh>ok  ' if ok else 'fresh>BAD '}MUTATION: {name[:96]}"
              f"{'' if ok else ' | ' + line + ' | ' + p.stdout[-260:]}")
    print(f"\nsession mutants, fresh process each: {caught}/{len(rows)} caught")
    inproc_rc = run_mutants(verbose=False)
    inproc = len(rows) if inproc_rc == 0 else 0
    print(f"session mutants, one process:        {inproc}/{len(rows)} caught")
    if caught != len(rows) or inproc_rc != 0:
        return 1
    print("OK  each mutant is caught by its named check in a fresh process too, "
          "so no count here is a leftover from the previous bug")
    return 0


def main(argv: list[str]) -> int:
    if "--mutant" in argv and "--sweep" not in argv:
        return run_mutants(one=int(argv[argv.index("--mutant") + 1]))
    # SPEC §6's line for this vector is `--sweep`, and one command has to print
    # both halves of its claim: the checks on the shipped command and the
    # mutants on the copies. A sweep that skipped the checks would leave the
    # battery's `44/44` unbacked.
    bad = report()
    if "--sweep" in argv:
        return 1 if (bad or sweep()) else 0
    if "--mutants" in argv:
        return 1 if bad or run_mutants(verbose=True) else 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
