"""R-7.15, OFFLINE: many turns against one repo — scored by an oracle, or a chat
that answers in prose — and every turn ends in something a person can use.

`flash run` answers exactly one task per process. That is the right shape for a
benchmark and the wrong shape for coding: to make two changes a person re-types
`--test … --context … --edit --apply`, re-reads the project, and waits for the
weights again. The author's own first use of the tool reported it as
"what is this how will i code on this?", six questions after R-3.2's clause 3
put a verified patch on disk.

So `flash session` reads turns from stdin against one (repo, oracle) pair — or
against the repo ALONE, which since R-7.15h is a mode rather than an error. Six
questions, none of them about a model — the router is stubbed for the command's
checks and only the weights are stubbed for the chat arm's, so what is under
test is the loop around the write-back and the arm around the answer:

* **Does turn N see what turn N-1 wrote?** The workspace is re-read from disk at
  the start of every turn. A session that kept the bytes it started with would
  hand the next turn a stale `before`, and `land` — which writes the whole file
  it was given — would silently revert the previous turn. That is the shape this
  vector's loudest mutant takes, and it is invisible to any one-turn test.
* **Does a turn that did not write say so?** The landing sentence comes from the
  same `cli._land_edits` the one-command surface prints, so `NOT APPLIED` and
  `REFUSED` cannot drift between the two surfaces — and in chat mode neither can
  the one claim this mode must never make, that an oracle passed.
* **Can the exam be edited mid-session?** The oracle is read once and named
  protected on every turn, so a patch that weakens an assertion is refused on
  turn 3 exactly as on turn 1.
* **Does `hi` get an answer?** With no `--test` the turn goes to `solve_chat`,
  which accepts prose: the reply prints, a patch inside the reply still lands,
  and a REFUSED patch is not green. `_solve_edits` cannot carry this — its
  "PATCH MISSING" clause is what made a question unanswerable.
* **Is an empty session a green one?** A missing oracle is now a mode, an
  unreadable one is still a refusal, and zero turns refuse with rc 2 before any
  generation: a session that answers with nothing behind it is what this
  project is not.
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

from flash import cli, harness, loop, patches, trace              # noqa: E402

MONEY = 'def cents_to_str(cents):\n    return str(cents) + "." + "00"\n'
MONEY_FIXED_BODY = 'return f"${cents // 100}.{cents % 100:02d}"'
MONEY_FIXED = MONEY.replace('return str(cents) + "." + "00"', MONEY_FIXED_BODY)
# An oracle that runs on its own: with `--context` the harness scores the project
# in a temp copy, so the bootstrap the task corpus uses has to be in the file.
ORACLE = ('import sys\nsys.path.insert(0, "<TMPDIR>")\n'
          'from money import cents_to_str\n'
          'assert cents_to_str(5) == "$0.05", cents_to_str(5)\n')
OTHER = 'TAX_RATE = 0.08\n'
# A file the project does not have, which only a chat turn is asked to write —
# R-7.15c's verb, reached through R-7.15h's arm.
LOG_SRC = 'def log(msg):\n    print("[app]", msg)\n'
# The three shapes a chat turn can come back as, in the words the arm uses.
CHAT_ANSWER = "Hi — this project formats cents as dollars in money.py."
CHAT_PATCH = ('I added a logger.\n\n# edit: logger.py :: *\n'
              '```python\n' + LOG_SRC + '```\n')
CHAT_BAD = ('Here it is.\n\n# edit: money.py :: no_such\n```python\n'
            'def no_such():\n    pass\n```\n')
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

def turn(edit=None, solved=True, err="", seconds=1.0, patches=1,
         code="# edit: money.py :: cents_to_str"):
    return {"edit": edit, "solved": solved, "err": err, "seconds": seconds,
            "patches": patches, "code": code}


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
# R-7.15h: a chat turn's answer is prose, and the turn that also writes a file
# carries the patch header inside that prose. Both shapes reach the CLI through
# the same `Attempt.code`, so which one prints is a property of the command, not
# of the stub.
HELLO = turn(code=CHAT_ANSWER, patches=0)
WRITE_FILE = turn(edit=lambda files: dict(files, **{"logger.py": LOG_SRC}),
                  code=CHAT_PATCH, patches=1)
REFUSED_TURN = turn(code="One patch, and it does not apply.",
                    solved=False, err="PATCH REFUSED: money.py:no_such — unknown "
                                      "symbol")


def fake_solve(script, log: list | None = None):
    """A `solve_routed` stand-in that records every task it was handed.

    Stubbing the router keeps the whole command on the table — the parser, the
    stdin read, `workspace_from_dir`, the trace session, the verdict line,
    `_land_edits` — and no model loads, which is what makes this offline.
    `log=` puts a `solveN` marker beside the stream's `readN` markers, which is
    the only way to see the ORDER the two happen in.
    """
    seen: list[dict] = []

    def _solve(small_repo, big_repo, task, root, **kw):
        step = script[min(len(seen), len(script) - 1)]
        # R-7.15f: the message the turn is GENERATED from is built inside
        # `solve_routed`, which this stand-in replaces, so the line the real one
        # runs first is run here — with the real `enrich_task`, so a mutant that
        # takes the composition out changes what these checks read.
        shown = loop.enrich_task(task, root, kw.get("max_chars", 4000))
        seen.append({"id": task["id"], "prompt": task["prompt"],
                    "message": patches.edit_prompt(shown),
                    "files": dict(task["files"]), "edit": task.get("edit"),
                    "multi": task.get("multi"), "context": task.get("context"),
                    "test": task["test"], "test_path": task.get("test_path"),
                    "kw": dict(kw)})
        if log is not None:
            log.append(f"solve{len(seen)}")
        files = task["files"]
        ws = step["edit"](files) if step["edit"] else dict(files)
        r = loop.SolveResult(
            task_id=task["id"], solved=step["solved"], seconds=step["seconds"],
            attempts=[loop.Attempt(code=step["code"],
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


class TtyStream:
    """A stdin that can claim to be a terminal, and says when it was read.

    Two things only this class can measure. `isatty` is what selects the `you> `
    marker, and a pipe cannot be asked to lie about being a keyboard. The read log
    is the one instrument that tells a chat from a batch file: the property
    R-7.15's second clause is about is not how many turns ran but that turn 1 ran
    BEFORE turn 2 was read, which a count cannot see and this can.
    """

    def __init__(self, text: str, log: list | None = None, tty: bool = True):
        self._lines = text.splitlines(keepends=True)
        self._log = log if log is not None else []
        self._tty = tty
        self._reads = 0

    def __iter__(self):
        return self

    def __next__(self) -> str:
        self._reads += 1
        self._log.append(f"read{self._reads}")
        if not self._lines:
            raise StopIteration
        return self._lines.pop(0)

    def isatty(self) -> bool:
        return self._tty


def drive(fn=None, prompts="", argv=None, script=None, root=None,
          oracle="t.py", command="session", tty=False, log=None):
    """Run a session command in this process with turns and router supplied."""
    fn = fn or cli.cmd_session
    script = script or [FIX_MONEY]
    root = root or new_tree()
    fake = fake_solve(script, log)
    real = (loop.solve_routed, sys.stdin, sys.argv, trace.DIR)
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-session-trace-"))
    # `oracle=None` is chat mode: the flag is absent, not pointed at a file, so
    # the command sees exactly what a stranger typing `flash session --context
    # .` sees.
    flags = [] if oracle is None else ["--test", str(root / oracle)]
    flags += ["--context", str(root)]
    sys.argv = ["flash", command, *flags, *(argv or [])]
    sys.stdin = (TtyStream(prompts, log, tty)
                 if tty or log is not None else stdin_text(prompts))
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
             verdict=True, oracle_lines=True, empty_refuses=True,
             interleaved=True, tty_prompt=True, oracle_key=True,
             chat_mode=True, chat_arg=True, answer_print=True)
    k.update(knob)

    def cmd(args) -> int:
        from flash.loop import solve_routed
        from flash.patches import land, workspace_from_dir

        use_oracle = args.test is not None
        if not use_oracle and not k["chat_mode"]:
            # R-7.15h put back: no oracle means no session, which is the state
            # that had nothing to type `hi` into.
            print(f"session: the oracle {args.test} is missing — every turn "
                  "needs a verify step")
            return 2
        if use_oracle:
            if k["oracle_guard"]:
                try:
                    test = open(args.test).read()
                except OSError as e:
                    print(f"session: the oracle {args.test} cannot be read ({e})"
                          " — every turn needs a verify step")
                    return 2
                if not test.strip():
                    print(f"session: the oracle {args.test} is empty — nothing "
                          "would be verified, so no turn could ever be green")
                    return 2
            else:
                got = outcome(lambda: open(args.test).read())
                test = "" if isinstance(got, str) and got.startswith("RAISED") \
                    else got
        else:
            test = ""
            print("session in CHAT MODE — no oracle, free-form questions and new "
                  "file creation allowed")

        loop.ADAPTER = args.adapter or ""
        loop.CONSTRAIN = args.constrain
        loop.DEBUG = args.debug
        armed = True if not k["edit_flag"] else bool(getattr(args, "edit", False))
        loop.EDIT = armed
        loop.HINTS = cli._hint_names(args.no_source_hint, args.no_graph_hint)
        trace.CAPTURE = args.trace_full
        sid = trace.open_session("session", cmd="session",
                                 params={"context": args.context,
                                         "test": args.test or "", "edit": True,
                                         "apply": args.apply,
                                         "turns": args.turns,
                                         "chat": not use_oracle})
        tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
        marker = cli.PROMPT if (tty and k["tty_prompt"]) else ""

        def asks():
            lines = iter(sys.stdin)
            n = 0
            while True:
                if marker:
                    print(marker, end="", flush=True)
                raw = next(lines, None)
                if raw is None:
                    return
                t = raw.strip()
                if k["quit_ends"] and t.lower() in ("quit", "exit", "q"):
                    return
                if not t and k["skip_blank"]:
                    continue
                yield t
                n += 1
                if k["turns_cap"] and args.turns and n >= args.turns:
                    return

        # The banner goes up before the first read, as in the command: on a
        # keyboard the alternative is a cursor and no sentence to read.
        if use_oracle:
            print(f"flash session on {args.context} against the oracle "
                  f"{args.test} — one ask per line, and each answer arrives "
                  "before you type the next. `quit` or Ctrl-D ends it.")
        elif k["chat_mode"]:
            print(f"flash chat session on {args.context} — free-form questions, "
                  "code creation, no verification. `quit` or Ctrl-D ends it.")
        # `interleaved=False` is the defect the author hit: drain stdin, then
        # run. Same turns, same counts, same sentences — and not a chat.
        stream = asks() if k["interleaved"] else iter(list(asks()))
        cached = workspace_from_dir(args.context)
        solved_n = written_n = ran = 0
        seconds = 0.0
        rcs: list[int] = []
        for prompt in stream:
            ran += 1
            i = ran
            task = {"id": f"turn{i}", "prompt": prompt, "test": test,
                    "context": args.context, "chat": not use_oracle}
            task["files"] = (workspace_from_dir(args.context) if k["re_read"]
                             else dict(cached))
            task["edit"] = armed
            task["multi"] = armed
            # R-7.15e: the same key the shipped command puts on every turn, so a
            # knob that drops it changes only what the arm is told.
            task["test_path"] = (cli._oracle_key(args, task)
                                 if k["oracle_key"] else "")
            r, tier, routed = solve_routed(args.small, args.big, task, cli.ROOT,
                                           small_attempts=args.attempts,
                                           big_attempts=args.attempts,
                                           max_tokens=args.max_tokens,
                                           allow_big=args.allow_big,
                                           tournament=args.tournament,
                                           confidence=args.confidence,
                                           **({"chat": not use_oracle}
                                              if k["chat_arg"] else {}))
            trace.event("task_end", task_id=task["id"], prompt=prompt[:200],
                        solved=r.solved, tier=tier, attempts=r.n_attempts,
                        seconds=r.seconds, routed=routed,
                        **loop.tournament_fields(r), **cli._conf_fields(r),
                        **cli._patch_fields(r))
            answer = r.attempts[-1].code if r.attempts else ""
            if use_oracle or not k["answer_print"]:
                if k["verdict"]:
                    print(f"[turn {i}] routed={routed} tier={tier} "
                          f"solved={r.solved} attempts={r.n_attempts} "
                          f"({r.seconds}s){cli._patch_note(r)}")
                if k["oracle_lines"] and not r.solved and r.attempts \
                        and r.attempts[-1].err:
                    for ln in r.attempts[-1].err.splitlines()[:8]:
                        print(f"  oracle| {ln}")
            else:
                # Chat: the prose is the reply, so it is what prints — the
                # telemetry line goes to the trace and nowhere else.
                print(answer.strip())
                if not r.solved and r.attempts and r.attempts[-1].err:
                    for ln in r.attempts[-1].err.splitlines()[:8]:
                        print(f"  {ln}")
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
        if not ran:
            print("session: no turns on stdin — nothing was asked, so nothing "
                  "was verified")
            trace.close_session(solved=False, turns=0)
            return 2 if k["empty_refuses"] else 0
        rc = rcs[0] if not k["last_rc"] else rcs[-1]
        print(f"[session] turns={ran} solved={solved_n} "
              f"written={written_n} seconds={round(seconds, 1)} last_rc={rc}")
        print(f"[trace] replay this session:  flash trace show {sid}")
        trace.close_session(solved=(rc == 0), turns=ran,
                            solved_turns=solved_n, written_turns=written_n,
                            seconds=round(seconds, 1))
        return rc
    return cmd


def _quiet(fn, prompts, script, root, argv, tty=False, oracle="t.py"):
    """Run `fn` over one scenario and return everything a reader could compare:
    the exit code, the printed lines with the volatile ones removed, and the
    bytes the tree ended with."""
    rc, out, _seen, _root = drive(fn, prompts, argv, script, root, oracle,
                                  tty=tty)
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
        # A claimed terminal, so the marker each side prints is under comparison
        # too: a copy that drops it would agree on every pipe and lie on a
        # keyboard, which is the one place a prompt is for.
        ("fix\nadd\n", [FIX_MONEY, ADD_CONST], True, [], True),
        ("one\n\nquit\n", [FIX_MONEY], False, [], True),
        # R-7.15h's lane: no oracle at all, so `oracle=None` and the answers are
        # prose, a file-write, and a refusal — the three shapes one chat turn can
        # take, each of which the copy has to print the same way.
        ("hi\nwhat is this?\n", [HELLO, HELLO], False, [], False, None),
        ("make a logger\n", [WRITE_FILE], True, [], False, None),
        ("write it\nbreak it\n", [WRITE_FILE, REFUSED_TURN], True, [],
         False, None),
        ("hi\nquit\n", [HELLO], False, [], True, None),
    ]
    for scenario in scenarios:
        prompts, script, apply, extra = scenario[:4]
        tty = scenario[4] if len(scenario) > 4 else False
        oracle = scenario[5] if len(scenario) > 5 else "t.py"
        seen = []
        for fn in (cli.cmd_session, session_copy()):
            seen.append(_quiet(fn, prompts, script, new_tree(),
                               (["--apply"] if apply else []) + extra, tty,
                               oracle))
        if seen[0] != seen[1]:
            return (f"{prompts!r} apply={apply} tty={tty} oracle={oracle} "
                    f"{extra}: shipped={seen[0]} copy={seen[1]}")
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
                 oracle_lines=False, empty_refuses=False, interleaved=False,
                 tty_prompt=False, chat_mode=False, chat_arg=False,
                 answer_print=False, oracle_key=False)
    for name in knobs:
        copy = session_copy(**{name: knobs[name]})
        # Both lanes: a knob that only misbehaves once there is no oracle would
        # pass the original scenario and still be an unfair mutant.
        for prompts, script, argv, oracle in (
                ("fix\nadd\n", [FIX_MONEY, ADD_CONST], ["--apply"], "t.py"),
                ("hi\nwrite it\n", [HELLO, WRITE_FILE], ["--apply"], None)):
            rc, lines, state = _quiet(copy, prompts, script, new_tree(),
                                      argv, False, oracle)
            if isinstance(rc, str):
                raise AssertionError(f"knob {name} broke its own copy: {rc}")
    return True


# ------------------------------------------------------------------- checks

def _parser_checks() -> None:
    a = parsed(["session", "--test", "t.py", "--context", "."])
    check("`session` is a command on the shipped parser and routes to "
          "`cmd_session`, so the gate below tests what a stranger actually runs",
          getattr(a, "fn", None) is cli.cmd_session, repr(getattr(a, "fn", None)))
    check("session asks for the one thing a turn cannot do without — the tree it "
          "patches — and never for a prompt, which comes in on stdin one turn at "
          "a time. `--test` became optional at R-7.15h: omitting it is a MODE, "
          "not a missing argument",
          "required: --context" in rejects(["session"])
          and parsed(["session", "--context", "."]).test is None
          and parsed(["session", "--test", "t.py", "--context", "."]).test
          == "t.py",
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

    # The clause the author's third report was actually about. A count of turns
    # cannot tell a chat from a batch file: only the ORDER of the reads and the
    # solves can, so this records when stdin was asked and when the router ran.
    log: list[str] = []
    rc, out, seen, _ = drive(prompts="one\ntwo\n",
                             script=[FIX_MONEY, ADD_CONST], log=log)
    check("turn 1 is ANSWERED before turn 2 is read — the reads interleave with "
          "the solves (read1, solve1, read2, solve2) instead of draining stdin "
          "up front, which is the whole difference between a chat and a batch "
          "file with a prompt painted on it",
          log[:4] == ["read1", "solve1", "read2", "solve2"], log)
    log2: list[str] = []
    rc, out, seen, _ = drive(prompts="one\ntwo\nthree\n",
                             script=[FIX_MONEY] * 3, log=log2,
                             argv=["--turns", "1"])
    check("a capped session stops ASKING, not just stops counting: with "
          "`--turns 1` stdin is read once and never again, so a keyboard is not "
          "left waiting for a fourth line after the session is over",
          log2 == ["read1", "solve1"] and field(out, "turns") == "1", log2)

    rc, out, seen, _ = drive(prompts="one\n", script=[FIX_MONEY], tty=True)
    check("a keyboard is told what to do before it is asked: the session prints "
          "`you> ` on a terminal, which is the line a person needs to see to "
          "believe it is waiting rather than hung",
          "you> " in out, out[:200])
    rc, out, seen, _ = drive(prompts="one\n", script=[FIX_MONEY])
    check("a pipe gets no marker at all, because a session's stdout is also a "
          "report and a driver's captured text must stay parseable",
          "you>" not in out, out[:200])


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

    # R-7.15f on the session rather than the one-command surface. A turn is asked
    # to patch a project it first has to be SHOWN, and on a keyboard the ask is a
    # line of prose with no path in it, so the message is the only place the tree
    # can appear. These read the message the arm is generated from, because that
    # is where the six refusals this clause was filed for came out of.
    shown_tree = new_tree({**FILES, "pkg/deep.py": "LIMIT = 3\n"})
    rc, out, seen, _ = drive(prompts="fix the rounding\nadd a constant\n",
                             root=shown_tree, script=[FIX_MONEY, ADD_CONST],
                             argv=["--apply"])
    check("turn 1's message names every file the session read as a `# file:` block, "
          "so an address the model writes is one it has seen — including a module in "
          "a package directory, the shape a flat module list hides",
          all(f"# file: {k}" in seen[0]["message"]
              for k in ("money.py", "other.py", "t.py", "pkg/deep.py")),
          f"{len(seen[0]['message'])} chars, keys={sorted(seen[0]['files'])}")
    check("...and it carries the target module's real body under the typed ask, "
          "because the protocol makes the model re-type the complete definition and "
          "'fix the rounding' cannot be about a body nobody quoted",
          "def cents_to_str(cents):" in seen[0]["message"]
          and 'return str(cents) + "." + "00"' in seen[0]["message"]
          and "Requested change: fix the rounding" in seen[0]["message"],
          seen[0]["message"][:160])
    check("the oracle's assertions are in EVERY turn's message, not only the first: "
          "a session's `oracle|` lines are answerable because the model was shown "
          "the test it is scored against, and R-7.15e's refusal has a file to point "
          "at rather than a guess at one",
          'assert cents_to_str(5) == "$0.05"' in seen[0]["message"]
          and 'assert cents_to_str(5) == "$0.05"' in seen[1]["message"],
          f"turn2={len(seen[1]['message'])} chars")
    check("turn N+1 is shown the bytes turn N wrote: the second message carries the "
          "fix that landed on disk and no longer carries the definition turn 1 "
          "replaced, so a re-read that reached the model stale would have it re-send "
          "the body it just retired",
          MONEY_FIXED_BODY in seen[1]["message"]
          and 'return str(cents) + "." + "00"' not in seen[1]["message"],
          seen[1]["message"][:200])
    check("the project is composed once per turn: a turn's message states the "
          "project header exactly one time, so a session that re-enriched its own "
          "output would grow with every turn rather than stay at the tree's size",
          seen[1]["message"].count(patches.PROJECT_HEADER) == 1,
          f"{seen[1]['message'].count(patches.PROJECT_HEADER)} header(s)")

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
    check("every turn is handed the oracle's workspace key, so the patch layer is "
          "the one that refuses a patch aimed at the assertion — and on the turn "
          "after a write, from the bytes the re-read found",
          seen[0]["test_path"] == "t.py" and seen[1]["test_path"] == "t.py",
          f"{seen[0].get('test_path')!r} {seen[1].get('test_path')!r}")

    root = new_tree({"money.py": MONEY_FIXED, "other.py": OTHER, "t.py": ORACLE})
    rc, out, seen, _ = drive(prompts="fix it\n", root=root, script=[NO_OP],
                            argv=["--apply"])
    check("a turn whose verified workspace is byte-identical to the tree says "
          "so, and is not counted as a write",
          "byte-identical" in out and field(out, "written") == "0", out[-200:])


def _shadow_checks() -> None:
    """R-7.15g: the session's oracle lives INSIDE the tree it tests.

    Every other check here scripts the turn's verdict, because a session's cost is
    a model. These are the ones that reach the real oracle, and they use the shape
    the release actually produces: `t.py` sits next to `money.py` and bootstraps
    with the folder it is in. VERIFY writes the patched workspace to a temp copy
    of the same keys, so that bootstrap keeps the pre-edit module first on
    `sys.path`. Measured on the live pty run of 2026-09-29: the 30B's correct
    `cents_to_str` scored `FAILING_ASSERT … GOT: '$1.5'`, which is what the module
    returned BEFORE the turn, and the turn was reported as lost.
    """
    root = new_tree()
    live_oracle = ('import sys\nsys.path.insert(0, %r)\n'
                   'from money import cents_to_str\n'
                   'assert cents_to_str(5) == "$0.05"\n' % str(root))
    tree = {"money.py": MONEY_FIXED, "other.py": OTHER, "t.py": live_oracle}
    ok, err = harness.diagnose_files(tree, live_oracle)
    check("VERIFY grades the patched money.py when the session's oracle names its "
          "own directory — the live demo's shape, where the run of 2026-09-29 "
          "scored the bytes from before the edit",
          ok, f"ok={ok} err={err[:180]}")
    pre_ok, pre_err = harness.diagnose_files({**tree, "money.py": MONEY},
                                             live_oracle)
    check("...and the same oracle still refuses the pre-edit body, so the fix moved "
          "WHICH COPY is graded rather than what passes",
          not pre_ok and 'GOT:' in pre_err, f"ok={pre_ok} err={pre_err[:180]}")
    check("the sentence a model is refused on is the oracle's own assert, byte for "
          "byte — a precedence fix that rewrote the exam would fail here even while "
          "turns started passing",
          'assert cents_to_str(5) == "$0.05"' in pre_err, pre_err[:200])

    # The shape a stranger's test file actually is: a bootstrap with a sentence
    # after it. Anything appended to that line is behind a `#`.
    commented = ('import sys\nsys.path.insert(0, %r)  # so this runs from '
                 'anywhere\nfrom money import cents_to_str\n'
                 'assert cents_to_str(5) == "$0.05"\n' % str(root))
    c_ok, c_err = harness.diagnose_files({**tree, "t.py": commented}, commented)
    check("a bootstrap that ends in a comment still grades the patched copy, "
          "because the first version of this fix appended the precedence statement "
          "to that line and the `#` swallowed it",
          c_ok, f"ok={c_ok} err={c_err[:180]}")


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


# ----------------------------------------------------------------- the chat arm


def ask_chat(reply, extra=None):
    """One chat turn through the REAL `loop.solve_routed`, weights replaced only.

    `load_model` and `_generate` are the two stubs. Everything the scored path
    does — the router, the power governor, the tournament, the oracle harness —
    runs as shipped, so the claim under test is exactly the one the command
    makes: a chat turn is short-circuited before any of it, and the prose is
    what comes back.
    """
    from flash import ledger
    repos: list[str] = []
    rows: list[dict] = []
    prompts: list[str] = []

    def _load(repo, adapter=None):
        repos.append(repo)
        return object(), object()

    def _gen(model, tok, messages, max_tokens, **gkw):
        prompts.append(messages[-1]["content"])
        return reply

    real = (loop.load_model, loop._generate, ledger.record, trace.DIR)
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-chat-trace-"))
    loop.load_model = _load
    loop._generate = _gen
    ledger.record = lambda row, *a, **k: rows.append(row)
    task = {"id": "chat1", "prompt": "hi", "test": "", "edit": True,
            "chat": True, "files": dict(FILES)}
    task.update(extra or {})
    try:
        r, tier, routed = loop.solve_routed("small-repo", "big-repo", task,
                                            ROOT, chat=True)
    finally:
        (loop.load_model, loop._generate, ledger.record, trace.DIR) = real
    return r, tier, routed, {"repos": repos, "rows": rows, "prompts": prompts}


def _chat_checks() -> None:
    # ---- the arm itself: prose, patch, refusal
    r, tier, routed, seen = ask_chat(CHAT_ANSWER)
    check("a chat turn is green on PROSE: `_solve_edits`'s 'PATCH MISSING' is the "
          "clause that made `hi` unanswerable, and this arm has no such check, so "
          "the text comes back as the answer rather than as a refusal",
          routed == "chat" and tier == "small" and r.solved
          and r.attempts[-1].code == CHAT_ANSWER,
          f"routed={routed} tier={tier} solved={r.solved} "
          f"err={r.attempts[-1].err[:120]!r}")
    check("a chat turn loads ONE model — the small tier — so the router, the "
          "governor and the big tier are never reached, and a question costs one "
          "generation rather than an escalation",
          seen["repos"] == ["small-repo"] and len(r.attempts) == 1,
          seen["repos"])
    check("a chat turn is asked with the PROJECT in front of it (R-7.15f carried "
          "into this arm): the message names money.py, so 'what is this' has a "
          "referent",
          all("money.py" in p and "Hi" not in p for p in seen["prompts"])
          and len(seen["prompts"]) == 1,
          [p[:80] for p in seen["prompts"]])
    check("the ledger row says CHAT, because a question spent the same GPU "
          "seconds as a scored turn and nobody should have to guess which it was",
          len(seen["rows"]) == 1 and seen["rows"][0].get("chat") is True
          and seen["rows"][0]["routed"] == "chat", seen["rows"])

    r2, tier2, routed2, seen2 = ask_chat(CHAT_PATCH)
    check("a chat turn that answers AND writes a file lands the file in the "
          "workspace — this is 'create me a logger module' in one turn, which is "
          "the shape R-7.15c's verb exists for",
          r2.solved and "logger.py" in r2.workspace
          and r2.workspace["money.py"] == FILES["money.py"],
          sorted(r2.workspace or {}))
    check("a chat turn that writes a file is ONE attempt, not a repair loop: the "
          "patch applied, so there is nothing to re-sample",
          len(r2.attempts) == 1 and r2.attempts[0].patches == 1,
          r2.n_attempts)

    r3, _t3, _r3, seen3 = ask_chat(CHAT_BAD)
    check("a chat turn whose patch is REFUSED is not green: the model said it "
          "changed something and changed nothing, and a session that prints that "
          "as an answer is a lie about the project",
          not r3.solved and r3.workspace is None
          and r3.attempts[-1].err.startswith("PATCH REFUSED"),
          f"solved={r3.solved} err={r3.attempts[-1].err[:140]!r}")
    check("the refusal is fed back as the repair prompt, so attempt 2 sees the "
          "reason — the same second-chance the scored arm gets, on the same text",
          len(seen3["prompts"]) == 2 and "PATCH REFUSED" in seen3["prompts"][1],
          [p[-90:] for p in seen3["prompts"]])
    r4, _t4, _r4, _s4 = ask_chat(CHAT_BAD, {"chat": False})
    check("the refusal text is the patch layer's own sentence, not a reworded "
          "one: an unknown symbol says which file and which name",
          "money.py" in r4.attempts[-1].err and "no_such" in r4.attempts[-1].err,
          r4.attempts[-1].err[:160])

    # ---- the command's side of the same contract
    root = new_tree()
    rc, out, seen, _ = drive(prompts="hi\nwhat is this?\n", script=[HELLO],
                             root=root, oracle=None)
    check("with no --test the session OPENS: it says it is a chat, takes the "
          "turn, and exits green — the state the author's 'it cant even reply hi' "
          "report was about, and there was no command to type it into",
          rc == 0 and "CHAT MODE" in out and len(seen) == 2
          and "flash chat session" in out,
          f"rc={rc} turns={len(seen)} out={out[:200]!r}")
    check("the reply IS the answer: the prose prints, and the per-turn telemetry "
          "line does not — a person who asked a question gets a sentence back, "
          "not a report about which tier served it",
          out.count(CHAT_ANSWER) == 2 and "[turn 1]" not in out,
          out[:260])
    check("every chat turn is handed chat=True, `test` empty and no oracle key, "
          "so the arm cannot be scored against a file that was never named",
          all(s["kw"].get("chat") is True and s["test"] == ""
              and s["test_path"] == "" for s in seen),
          [{key: s[key] for key in ("test", "test_path")} for s in seen])
    check("a chat turn is still an EDIT turn over the re-read tree: the same "
          "workspace plumbing that lets turn N+1 edit turn N's bytes, which is "
          "what makes 'now change that file' work after 'create it'",
          all(s["edit"] is True and "money.py" in s["files"] for s in seen),
          [s["edit"] for s in seen])

    rc, out, seen, _ = drive(prompts="fix it\n", script=[FIX_MONEY])
    check("naming an oracle puts the session back exactly where it was: the turn "
          "is NOT a chat, so this vector's other 60 checks are about the shipped "
          "scored surface and not about a mode that leaked into it",
          len(seen) == 1 and seen[0]["kw"].get("chat") is False,
          seen[0]["kw"].get("chat"))

    root = new_tree()
    rc, out, seen, _ = drive(prompts="make a logger\n", script=[WRITE_FILE],
                             root=root, oracle=None, argv=["--apply"])
    check("--apply lands a chat turn's NEW file on disk, and the sibling it did "
          "not touch keeps its bytes: R-7.15c's creation reaches the tree through "
          "the chat arm, not only through the scored one",
          rc == 0 and tree_state(root)["logger.py"] == LOG_SRC
          and tree_state(root)["money.py"] == MONEY,
          f"rc={rc} tree={sorted(tree_state(root))}")
    check("the chat landing sentence never claims an oracle passed: the only "
          "claim this mode can make is that the turn produced a workspace, and "
          "the banner's own 'no oracle' is not a verdict",
          rc == 0 and "[R-3.2] wrote logger.py (+2 -0 lines)" in out
          and "the oracle passed" not in out,
          out[-260:])

    root = new_tree()
    rc, out, _seen, _ = drive(prompts="make a logger\n", script=[WRITE_FILE],
                              root=root, oracle=None)
    check("without --apply a chat turn says NOT APPLIED in chat words, so the "
          "tree is never assumed to have changed by an answer that read like one",
          rc == 0 and "NOT APPLIED" in out
          and "this turn produced a workspace" in out
          and tree_state(root) == FILES,
          f"rc={rc} out={out[-200:]!r}")

    rc, out, _seen, _ = drive(prompts="hi\nbreak it\n",
                              script=[HELLO, REFUSED_TURN], oracle=None)
    check("a refused chat turn prints the refusal without the `oracle|` prefix — "
          "the prefix would attribute the failure to a verify step that this "
          "session has never seen",
          "PATCH REFUSED" in out and "oracle|" not in out, out[-200:])
    check("and it is not green: the session's last code is the refusal's",
          rc == 1, f"rc={rc}")

    ns = outcome(lambda: cli.build_parser().parse_args(
        ["session", "--context", "."]))
    got = outcome(lambda: cli._oracle_key(ns, {"files": dict(FILES)}))
    check("`_oracle_key` answers the no-oracle question with the empty string "
          "instead of a traceback: `land` and the patch arm both ask it on every "
          "turn, so a crash here is the session dying on `hi`",
          got == "", repr(got))
    check("a chat session's own report counts what ran, so the mode cannot make "
          "the parseable EOF line disappear",
          field(drive(prompts="hi\n", script=[HELLO], oracle=None)[1],
                "turns") == "1",
          drive(prompts="hi\n", script=[HELLO], oracle=None)[1][-160:])

    got = outcome(lambda: subprocess.run(
        [sys.executable, "-m", "flash.cli", "session", "--help"],
        capture_output=True, text=True, cwd=ROOT))
    check("the help names chat mode, because `--test` being optional is not "
          "guessable from a flag list — a reader has to be told what omitting it "
          "does",
          not isinstance(got, str) and "chat mode" in got.stdout.lower(),
          got if isinstance(got, str) else got.stdout[-200:])



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

    check("the optionality stopped at `session`: `run --test` is still required, "
          "so the scored surface cannot silently become a chat that prints "
          "confident answers with no verify step behind it",
          "required: --test" in rejects(["run", "fix it", "--context", "."]),
          rejects(["run", "fix it", "--context", "."])[-120:])

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
    _shadow_checks()
    _verdict_checks()
    _chat_checks()
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
    real_enrich = loop.enrich_task

    def blind_edit(task, root, max_chars=4000):
        """R-7.15f undone from below the command: an edit task comes back holding
        only the line the person typed, which is the shape that left a session's
        patch arm addressing files it had never been shown."""
        if task.get("edit"):
            return dict(task)
        return real_enrich(task, root, max_chars)

    def unguarded_oracle_key(args, task):
        """The no-oracle line taken out of the key every turn asks for: the mode
        check becomes a traceback, because `land` and the patch arm ask this
        question of a `--test` that chat mode never had."""
        rel = cli._rel_to(args.test, args.context)
        return rel if rel in (task.get("files") or {}) else ""

    real_land = cli._land_edits

    def scored_landing(args, task, r):
        """R-7.15h's other half undone: the tree facts stay right and the claim
        about them is the scored arm's, so a chat turn reports that an oracle
        passed a turn nothing ever verified."""
        if not args.apply:
            what = ("the oracle passed this patch set" if r.solved
                    else "nothing passed the oracle")
            print(f"[R-3.2] NOT APPLIED: {what} in memory; {args.context} on disk "
                  "is unchanged. Re-run with --apply to land the patch set.")
            return 0 if r.solved else 1
        # The write-back is not the clause under test, so the shipped one runs it.
        return real_land(args, task, r)

    real_chat = loop.solve_chat

    def prose_is_refused(model, tok, task, max_attempts=2, max_tokens=1024,
                         stage="small"):
        """`_solve_edits`'s PATCH MISSING rule reused verbatim one layer down: the
        prose is generated and printed exactly as shipped, then marked red — which
        is the refusal `hi` used to get, and no count of turns can tell it from a
        chat that answered."""
        r = real_chat(model, tok, task, max_attempts, max_tokens, stage)
        if r.solved and r.attempts and r.attempts[-1].patches == 0:
            r.solved = False
            r.workspace = None
            r.attempts[-1].ok = False
            r.attempts[-1].err = ("PATCH MISSING: no "
                                  "'# edit: <file> :: <symbol>' patch")
        return r

    def shadowed(test, root, files):
        """R-7.15g undone: VERIFY writes the patched workspace and then grades
        whatever the oracle's own `sys.path` line reaches first, which on a test
        file that lives in the project is the module from before the turn. Nothing
        else in a session notices — the address resolves, `applied=1` is true, and
        the verdict is about bytes nobody wrote."""
        return test

    def appended(test, root, files):
        """The FIRST version of R-7.15g: the precedence statement joined to the
        bootstrap line with `;` instead of put on a line of its own. Every report
        is identical until the oracle comments its bootstrap — which a stranger's
        `t.py` does — and then the statement is behind a `#`, the fix is a no-op,
        and the pre-edit module answers again."""
        out = []
        for line in test.splitlines():
            m = harness._INSERT0.search(line)
            if m:
                named = Path(m.group(2)).resolve()
                if named != root and any((named / rel).is_file()
                                         for rel in files):
                    line = f"{line}; sys.path.insert(0, {str(root)!r})"
            out.append(line)
        return "\n".join(out) + "\n"

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
        ("stdin is drained before turn 1 runs: every turn still answers, every "
         "count still matches, and a person at a keyboard watches a cursor "
         "instead of an answer — the exact shape of the report that said there "
         "was no chat here",
         session_copy(interleaved=False),
         "turn 1 is ANSWERED before turn 2 is read"),
        ("no marker on a terminal: the session is waiting and prints nothing at "
         "all, which is how a running model reads as a hung one",
         session_copy(tty_prompt=False),
         "a keyboard is told what to do before it is asked"),
        ("the turn stops carrying the oracle's key: `land` still recomputes it, so "
         "the tree is safe while the patch layer is never told, and an assertion "
         "edit comes back as a complaint about its own line numbers",
         session_copy(oracle_key=False),
         "every turn is handed the oracle's workspace key"),
        ("a turn is generated from the typed line alone: the session still reads "
         "the tree, still lands the patch, still prints the same verdict, and the "
         "model is asked to address files it was never shown — the state the six "
         "refusals of R-7.15f came out of, which no count of turns or writes sees",
         (loop, "enrich_task", blind_edit),
         "turn 1's message names every file the session read"),
        ("VERIFY writes the patch and then grades the tree that was there before "
         "it: every count in the report stays honest, the turn is decided by the "
         "unpatched module, and a correct answer is printed as a failure — the "
         "shape that lost the live demo turn after R-7.15f was fixed",
         (harness, "_unshadow", shadowed),
         "VERIFY grades the patched money.py when the session's oracle names its"),
        ("the precedence statement lands behind the oracle's own comment: the fix "
         "is present, correct, and reachable only by a test file that does not "
         "annotate its bootstrap — which is the shape the release ships into",
         (harness, "_unshadow", appended),
         "a bootstrap that ends in a comment still grades the patched copy"),
        ("no chat mode: a session with no oracle refuses to start, which is the "
         "state that left the author's `it cant even reply hi` with nowhere to go "
         "— the turns never ran, so no count of turns or writes notices",
         session_copy(chat_mode=False),
         "with no --test the session OPENS"),
        ("the turn is not told it is a chat: `cmd_session` reads the mode for its "
         "own banner and never passes it down, so the scored arm gets the prose "
         "and answers `hi` with a refusal while every printed line still claims "
         "chat mode",
         session_copy(chat_arg=False),
         "every chat turn is handed chat=True"),
        ("the answer is replaced by its own telemetry: the prose is generated, "
         "stored and traced, and the person gets `routed=chat tier=small "
         "solved=True` where the sentence should be — the shape of a chat that "
         "reports on itself instead of replying",
         session_copy(answer_print=False),
         "the reply IS the answer"),
        ("the oracle key is derived without asking whether there is one: every "
         "turn of a chat session raises on `Path(None)` instead of answering, so "
         "the mode check becomes a traceback on the first `hi`",
         (cli, "_oracle_key", unguarded_oracle_key),
         "`_oracle_key` answers the no-oracle question"),
        ("the chat turn's landing sentence is the scored one: nothing was "
         "verified and the report says an oracle passed, which is the one claim "
         "this mode is not allowed to make",
         (cli, "_land_edits", scored_landing),
         "a chat turn says NOT APPLIED in chat words"),
        ("prose is a failure inside the arm: `_solve_edits`'s PATCH MISSING rule "
         "reused verbatim on a chat turn, so `hi` is generated correctly, printed, "
         "and then marked red — the refusal that started R-7.15h, one layer down",
         (loop, "solve_chat", prose_is_refused),
         "a chat turn is green on PROSE"),
    ]


def _lane(copy) -> list[str]:
    """Run every check against one mutated surface; name the ones that fail.

    Usually that is a copy of the command. It can also be `(holder, attr, value)`
    for a clause that lives BELOW the command — R-7.15f composes the message in
    `loop.enrich_task`, which `cmd_session` never calls, so a copy of the session
    could not put that defect back without also changing another clause.
    """
    shipped = cli.__dict__["cmd_session"]
    if isinstance(copy, tuple):
        holder, attr, value = copy
        real = getattr(holder, attr)
        setattr(holder, attr, value)
        try:
            run_checks()
        finally:
            setattr(holder, attr, real)
        return [n for n, ok, _d in CHECKS if not ok]
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
