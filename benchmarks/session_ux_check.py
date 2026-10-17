"""R-7.17b/c — the session's own UX: a turn that says it started, four commands,
and a Ctrl-C that leaves a CLOSED trace behind.

Why a separate file: `benchmarks/session_check.py` is a §6 battery line with its
check and mutant counts written into `benchmarks/battery_reread.py`'s `BATTERY`
(`session_check.py --sweep` = 81 checks, 26 mutants), and the published headline
totals (checks 1429, oracle 20, §6 total 1449, mutants 172) are the sum of those
closed rows. This vector is not in that list, so it can grow without moving a
published number — and adding it to the list is the owner's call, not this file's.

What is under test, and why each sentence is worth a check:

1. **A turn announces itself before it works.** A turn is a model load, a
   generation and an oracle run — a minute or more — and the `[turn N]` verdict
   only exists at the end, so a terminal with no line during that time cannot be
   told apart from a hung process. The proof is not the transcript order (a
   buffer can reorder that): it is (a) the router stand-in reading the captured
   stdout at the moment it is called and finding its own turn's line already
   there, and (b) a real child process on a pipe, asleep inside the router, whose
   `[working]` line reaches the parent while the child is still running. (b) only
   happens with `flush=True`, which is the other half of the requirement.
2. **The line does not wear a verdict's clothes.** No `solved=`, no `tier=`, not
   prefixed `[turn `. `benchmarks/session_pty_demo.py` types the next ask the
   instant it sees `solved=` on the terminal; a promise carrying that token would
   drive the session by itself.
3. **`/help`, `/quit`, `/save <file>`, `/retry`.** Each is a line a person types,
   so each is checked as a line: what prints, and how many times the router is
   called. A command that charges for a turn is the failure mode worth a mutant.
   `/save` refuses to write over the session's own oracle — R-3.2's rule on a
   surface that is not the patch arm, checked against the bytes still on disk.
4. **Ctrl-C is a normal way to leave.** The turn it interrupted generated nothing
   that was graded, so it is not counted; the summary still prints; and the trace
   still CLOSES, because §33.7 reads an unclosed session as a suite to resume and
   a hand-typed conversation has no suite to resume. `flash trace`'s own view of
   the file is then read in a grandchild, so "intact" and "not resumable" are
   measured rather than asserted from the writer's side.
5. **The session bills itself on the way out (R-7.19).** Whether to run this again
   is a money question, so every figure comes off the records the loop wrote — the
   token counts and `ms` of each `generate`, the `verify` runs, the `escalation`
   records the power governor left — and the same numbers are printed AND stored as
   one `session_cost` record. Billed here: a per-turn row, the tier that answered
   versus the route the router chose, an escalation that was taken versus one that
   was refused, a generation whose tokenizer counted nothing (reported, not folded
   into the total as a zero), the turn Ctrl-C killed, which is spent but not
   counted, and the reader's side of it in a grandchild, where the check is that
   `summarize()` still reports the generations' own tokens rather than counting the
   bill among them.

    python benchmarks/session_ux_check.py
    python benchmarks/session_ux_check.py --mutant [name]
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BDIR = ROOT / "benchmarks"
sys.path.insert(0, str(BDIR))

CHECKS: list[tuple[str, bool, str]] = []


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


TB = "Traceback (most recent call last)"
WORKING = "[working]"

#: Every scenario runs in ONE process, against the real `cli.cmd_session` with
#: only the router supplied — the same stand-in `benchmarks/session_check.py`
#: drives its 81 checks with, so the parser, the stdin read, the trace session,
#: the verdict line and `_land_edits` are all the shipped ones.
#:
#: `@` in a prompt string is the scenario's own tree, substituted after the tree
#: is made, so nothing here carries a checkout path.
_CHILD = '''
import contextlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, "__ROOT__")
sys.path.insert(0, "__BDIR__")
from flash import cli, loop, trace
import session_check as sc

SCEN = {}
SAVE_DIR = tempfile.mkdtemp(prefix="flash-ux-save-")


def _records(tdir):
    out = []
    for p in sorted(Path(tdir).glob("*.jsonl")):
        out.append({"file": p.name})
        for line in p.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"corrupt": line})
    return out


class Keyboard:
    """A stdin that behaves like a keyboard until Ctrl-C lands.

    `at=0` raises on the first read: a Ctrl-C at the prompt, with nothing asked.
    """

    def __init__(self, text, at):
        self._lines = text.splitlines(keepends=True)
        self._at = at

    def __iter__(self):
        return self

    def __next__(self):
        if self._at <= 0:
            raise KeyboardInterrupt
        self._at -= 1
        if not self._lines:
            raise StopIteration
        return self._lines.pop(0)

    def isatty(self):
        return False


def run(name, prompts, argv=None, script=None, oracle="t.py", at=None,
        raise_at=0, keep=(), bill=None, fn=None):
    root = sc.new_tree()
    tdir = Path(tempfile.mkdtemp(prefix="flash-ux-trace-"))
    fake = sc.fake_solve(script or [sc.FIX_MONEY])
    buf = io.StringIO()
    at_call = []
    calls = []

    def billed(task, spec):
        """The records one real turn leaves in the trace, written by the stand-in.

        `session_check.fake_solve` replaces the router, so the generations a real
        turn performs never happen and the session has nothing to bill. These are
        the records `flash/loop.py` writes per turn — a `generate` for every tier
        attempt, carrying R-7.19's `backend=` field and the counted token pair,
        and a `verify` for the oracle run — which is the only input the cost
        report reads. So the report is computed off the trace here exactly the
        shipped command computes it on a real machine.

        `ms` is fixed: 1.0 s per generation and 0.5 s per verify, which makes
        `generating=2.0s` the trace's own sum over two records rather than a
        number this file guessed.
        """
        for backend, p, c in spec["gen"]:
            trace.event("generate", task_id=task["id"], attempt=1, temp=0.0,
                        max_tokens=512, backend=backend, prompt_tokens=p,
                        completion_tokens=c, ms=1000)
        for _ in range(spec.get("ver", 0)):
            trace.event("verify", task_id=task["id"], attempt=1, ok=True,
                        kind="oracle", ms=500)
        if "esc" in spec:
            trace.event("escalation", task_id=task["id"], denied=spec["esc"],
                        tier="big",
                        **({"why": "the machine is hot"} if spec["esc"] else
                           {"reason": "small failed after 1 attempt(s)"}))

    def wrapped(small, big, task, r, **kw):
        # What the reader can see at the exact moment the work is handed over.
        # This function IS the router's stand-in, so the buffer it reads is the
        # terminal at the start of the turn; nothing printed later can be
        # credited to it.
        at_call.append(buf.getvalue())
        n = len(calls) + 1
        spec = bill[n - 1] if bill and n <= len(bill) else None
        # A turn spends its generations before it can be interrupted, so they are
        # written first — that is the whole point of billing a killed turn.
        if spec is not None:
            billed(task, spec)
        if raise_at and n == raise_at:
            raise KeyboardInterrupt
        calls.append(task["prompt"])
        out = fake(small, big, task, r, **kw)
        if spec is None:
            return out
        # A spec may say which tier answered: the real router returns that, and
        # the per-turn row reads it, so an escalation needs this to be visible.
        return (out[0], spec.get("tier", out[1]), spec.get("routed", out[2]))

    real = (loop.solve_routed, sys.stdin, sys.argv, trace.DIR)
    trace.DIR = tdir
    flags = [] if oracle is None else ["--test", str(root / oracle)]
    sys.argv = ["flash", "session", *flags, "--context", str(root),
                *(argv or [])]
    prompts = prompts.replace("@", str(root))
    sys.stdin = sc.stdin_text(prompts) if at is None else Keyboard(prompts, at)
    loop.solve_routed = wrapped
    rc = None
    try:
        args = cli.build_parser().parse_args(sys.argv[1:])
        with contextlib.redirect_stdout(buf):
            rc = (fn or cli.cmd_session)(args)
    except BaseException as e:
        rc = "RAISED " + type(e).__name__
    finally:
        (loop.solve_routed, sys.stdin, sys.argv, trace.DIR) = real
    files = {}
    for rel in keep:
        for base in (root, Path(SAVE_DIR)):
            p = base / rel
            if p.exists():
                files[rel] = p.read_text()
                break
    jsonl = sorted(tdir.glob("*.jsonl"))
    SCEN[name] = dict(rc=rc, out=buf.getvalue(), calls=calls,
                      records=_records(str(tdir)), root=str(root),
                      tdir=str(tdir), sid=jsonl[0].stem if jsonl else "",
                      files=files, at_call=at_call)


def parity(name, prompts, script, argv, oracle):
    """The shipped command and `session_check.session_copy()` over the same asks.

    `session_check.agree()` compares the two on the scenarios that existed before
    these commands did; this extends the comparison to the new clauses, so the
    copy cannot be quietly greenlighting an older command. The `/save` path is
    under a directory both sides share, so the printed sentence is the same
    string on both.
    """
    seen = []
    for fn in (cli.cmd_session, sc.session_copy()):
        root = sc.new_tree()
        seen.append(sc._quiet(fn, prompts.replace("@", str(root)), script, root,
                              argv, False, oracle))
    SCEN[name] = {"same": seen[0] == seen[1],
                  "shipped": seen[0][1], "copy": seen[1][1]}


def bill_parity(name, prompts, script, argv, bill):
    """The shipped command and `session_check.session_copy()` over the same bill.

    `parity()` above cannot reach this clause: its scenarios go through
    `session_check.drive()`, whose router stand-in writes no `generate` record, so
    on those asks neither side prints a report and agreeing proves nothing. Here
    both sides are handed the same records, and the parent compares the bill's
    lines after folding the one number two processes cannot share — the wall clock
    they were each measured on.
    """
    run(name + "_shipped", prompts, script=script, argv=argv, bill=bill)
    run(name + "_copy", prompts, script=script, argv=argv, bill=bill,
        fn=sc.session_copy())
    a, b = SCEN[name + "_shipped"], SCEN[name + "_copy"]
    SCEN[name] = {"shipped": a["out"], "copy": b["out"],
                  "rc": [a["rc"], b["rc"]], "cost": bool(
                      [l for l in a["out"].splitlines()
                       if l.startswith("[cost]")])}


def scenarios():
    run("liveness_oracle", "fix the rounding\\n",
        argv=["--apply", "--attempts", "4"])
    run("liveness_chat", "hi\\n", oracle=None)
    run("liveness_no_big", "fix the rounding\\n", argv=["--allow-big", "never"])
    run("liveness_capped", "a\\nb\\nc\\n", argv=["--turns", "2"])
    run("fail_turn", "fix the rounding\\n", script=[sc.FAIL_MONEY],
        argv=["--apply"])
    run("help_only", "/help\\n")
    run("help_chat", "/help\\n", oracle=None)
    run("save_transcript", "fix the rounding\\n/save @/notes.md\\n",
        keep=["notes.md"])
    run("save_no_arg", "fix the rounding\\n/save\\n", keep=["notes.md"])
    run("save_early", "/save @/early.md\\n", keep=["early.md"])
    run("save_oracle", "fix the rounding\\n/save @/t.py\\n", keep=["t.py"])
    run("save_unwritable",
        "fix the rounding\\n/save @/no-such-dir/notes.md\\n")
    run("retry_reasks", "fix the rounding\\n/retry\\n")
    run("retry_early", "/retry\\n")
    run("unknown_command", "/frobnicate the thing\\n")
    run("quit_command", "fix the rounding\\n/quit\\nadd a constant\\n",
        script=[sc.FIX_MONEY, sc.ADD_CONST], argv=["--apply"])
    run("interrupt_mid", "fix the rounding\\nadd a constant\\n",
        script=[sc.FIX_MONEY, sc.ADD_CONST], argv=["--apply"], raise_at=2)
    run("interrupt_prompt", "fix the rounding\\n", at=0)
    run("interrupt_first", "fix the rounding\\n", raise_at=1, argv=["--apply"])

    # ---------------------------------------------------------- the session bill
    # `gen` entries are (backend, prompt tokens, completion tokens) and each one is
    # one `generate` record; `ver` is how many `verify` records the turn leaves;
    # `esc` is the `escalation` record's `denied` value; `tier` is which tier the
    # router says answered. The numbers are chosen so every clause of the printed
    # report has a different value from every other — a total that could also be
    # read off a mis-sum would gate nothing.
    run("cost_two_turns", "fix the rounding\\nadd a constant\\n",
        script=[sc.FIX_MONEY, sc.ADD_CONST], argv=["--apply"],
        bill=[{"gen": [("mlx", 900, 120)], "ver": 1},
              {"gen": [("mlx", 1000, 200)], "ver": 1}])
    run("cost_escalated", "fix the rounding\\n",
        bill=[{"gen": [("mlx", 800, 100), ("mlx", 400, 60)], "esc": False,
               "tier": "big"}])
    run("cost_denied", "fix the rounding\\n", script=[sc.FAIL_MONEY],
        argv=["--apply"], bill=[{"gen": [("llama.cpp", 640, 60)], "esc": True}])
    run("cost_uncounted", "fix the rounding\\n",
        bill=[{"gen": [("mlx", None, None), ("mlx", 500, 50)]}])
    run("cost_killed", "fix the rounding\\nadd a constant\\n",
        script=[sc.FIX_MONEY, sc.ADD_CONST], argv=["--apply"], raise_at=2,
        bill=[{"gen": [("mlx", 900, 120)]}, {"gen": [("mlx", 700, 300)]}])
    run("cost_killed_first", "fix the rounding\\n", raise_at=1,
        argv=["--apply"], bill=[{"gen": [("mlx", 700, 300)]}])
    # `bill=[]`: the turns run, the router answers, and NOT ONE generation is
    # recorded. This is the shape a session has when nothing was spent, and the
    # only honest witness that the report cannot appear out of thin air.
    run("cost_no_records", "/help\\nfix the rounding\\n", bill=[])

    bill_parity("parity_bill", "fix the rounding\\nadd a constant\\n",
                [sc.FIX_MONEY, sc.ADD_CONST], ["--apply"],
                [{"gen": [("mlx", 900, 120)], "ver": 1},
                 {"gen": [("mlx", 1000, 200)], "ver": 1}])

    parity("parity_commands",
           f"/help\\nfix the rounding\\n/save {SAVE_DIR}/n.md\\n/retry\\n"
           "/quit\\nadd a constant\\n",
           [sc.FIX_MONEY, sc.ADD_CONST], ["--apply"], "t.py")
    parity("parity_chat", f"/help\\nhi\\n/save {SAVE_DIR}/c.md\\nquit\\n",
           [sc.HELLO], [], None)


def pipe_mode():
    """One turn, a real pipe, and a router that sleeps — stdout NOT redirected.

    The parent reads the pipe with a deadline while this process is still inside
    `solve_routed`. What arrives at that moment has been flushed; what does not
    is sitting in a buffer, which is exactly the difference between a session
    that looks alive and one that looks hung.
    """
    root = sc.new_tree()
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-ux-trace-"))
    fake = sc.fake_solve([sc.FIX_MONEY])

    def slow(small, big, task, r, **kw):
        time.sleep(2.0)
        return fake(small, big, task, r, **kw)

    loop.solve_routed = slow
    sys.argv = ["flash", "session", "--context", str(root),
                "--test", str(root / "t.py")]
    sys.stdin = sc.stdin_text("fix the rounding\\n")
    args = cli.build_parser().parse_args(sys.argv[1:])
    rc = cli.cmd_session(args)
    sys.stderr.write(f"PIPE: child exiting rc={rc}\\n")


if __name__ == "__main__":
    if sys.argv[1:2] == ["--pipe"]:
        pipe_mode()
    else:
        scenarios()
        print(json.dumps(SCEN))
'''


def _src() -> str:
    return (_CHILD.replace("__ROOT__", str(ROOT))
            .replace("__BDIR__", str(BDIR)))


def child() -> tuple[dict, str]:
    """Run every scenario once and hand back the records the child collected."""
    r = subprocess.run([sys.executable, "-c", _src()], capture_output=True,
                       text=True, cwd=str(ROOT), timeout=600)
    line = next((l for l in reversed(r.stdout.splitlines()) if l.startswith("{")),
                "")
    if not line:
        raise AssertionError(f"child printed no scenario table "
                             f"(rc={r.returncode}): "
                             f"{(r.stdout + r.stderr)[-600:]}")
    return json.loads(line), r.stdout + r.stderr


def pipe_evidence() -> tuple[list[str], float, float]:
    """(the first two lines off a real pipe, the second one arrived at, the
    second the child exited).

    The child sleeps 2.0 s inside the router stand-in, so a line that arrives
    more than a second before the child exits was written while the turn was
    still working — that is the only way to see `flush=True`, because a captured
    in-process buffer cannot tell a flush from an exit. Two lines are read rather
    than one: the banner is not flushed by the session, and it is the `[working]`
    line's own flush that carries it out, so the banner arriving first is
    expected and the second line is the evidence.
    """
    proc = subprocess.Popen([sys.executable, "-c", _src(), "--pipe"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=str(ROOT))
    started = time.time()
    lines: list[tuple[float, str]] = []
    assert proc.stdout is not None
    for _ in range(2):
        line = proc.stdout.readline()
        if not line:
            break
        lines.append((round(time.time() - started, 2), line.rstrip("\n")))
    first = lines[0][0] if lines else 0.0
    proc.communicate(timeout=120)
    exit_at = round(time.time() - started, 2)
    return [l for _, l in lines], first, exit_at


def trace_view(tdir: str, sid: str) -> str:
    """`flash trace`'s own read of the file Ctrl-C left behind, in a grandchild.

    `trace.DIR` is repointed rather than the store copied, because the claim is
    about the file the interrupted session wrote, not about a duplicate of it.
    """
    src = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
from flash import trace
trace.DIR = Path({tdir!r})
s = trace.summarize({sid!r})
print("closed=" + str(s["closed"]), "turns=" + str(s["tasks"]),
      "solved=" + str(s["solved"]))
view = trace.render({sid!r})
print("render_lines=" + str(len(view.splitlines())),
      "has_turn=" + str("turn1" in view))
'''
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, cwd=str(ROOT), timeout=120)
    return (r.stdout + r.stderr).strip()


def last(records: list[dict], type_: str) -> dict:
    return next((r for r in reversed(records) if r.get("type") == type_), {})


def working_lines(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.startswith(WORKING)]


#: The one number in the session bill two processes cannot share: the wall clock
#: each was measured on. Folded only when the shipped command is compared with its
#: parity copy; every other character of the block stays under comparison.
WALL = re.compile(r"wall=[0-9.]+")


def bill_lines(text: str) -> list[str]:
    """Only the report — the `[cost]` header and its per-turn rows."""
    return [WALL.sub("wall=<N>", l) for l in text.splitlines()
            if l.startswith("[cost]") or l.startswith("       turn ")]


def field(text: str, key: str) -> str:
    """One `key=value` off the printed header, so a check can compare the number
    the reader saw with the number the trace stored instead of with my own
    arithmetic twice."""
    head = next((l for l in text.splitlines() if l.startswith("[cost]")), "")
    m = re.search(rf"\b{key}=(\S+)", head)
    return m.group(1) if m else ""


def loop_generate_fields() -> list[str]:
    """The keyword names on `flash/loop.py`'s own `generate` record (AST, not grep).

    The bill prints a backend name, and that name is only the library that
    answered if the loop writes it onto the record — so the field has to be there
    at the source, where a docstring cannot fake it and a stand-in cannot supply it.
    """
    tree = ast.parse((ROOT / "flash" / "loop.py").read_text())
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "event" and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "generate"):
            return sorted(kw.arg for kw in node.keywords if kw.arg)
    return []


def bill_view(tdir: str, sid: str) -> str:
    """`flash trace`'s own read of the session that printed a bill, in a grandchild.

    Two claims only the reader's side can make: the `session_cost` record renders
    with its numbers visible (its keys had to join `trace._ORDER`, or the log is a
    record nobody can read), and `summarize()`'s token totals still belong to the
    generations alone — storing the bill beside them must not count the same
    tokens a second time.
    """
    src = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
from pathlib import Path
from flash import trace
trace.DIR = Path({tdir!r})
s = trace.summarize({sid!r})
print("prompt=" + str(s["prompt_tokens"]), "complete=" + str(s["completion_tokens"]),
      "gen_s=" + str(s["gen_s"]), "verify_s=" + str(s["verify_s"]),
      "closed=" + str(s["closed"]))
cost = [l for l in trace.render({sid!r}).splitlines() if "session_cost" in l]
print("cost_line=" + (cost[0] if cost else "(none)"))
'''
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, cwd=str(ROOT), timeout=120)
    return (r.stdout + r.stderr).strip()


def commands_of() -> list[str]:
    """Which `cmd_*` bodies reference the new session helpers (AST, not grep)."""
    tree = ast.parse((ROOT / "flash" / "cli.py").read_text())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if used & {"_working_line", "_session_help", "_save_transcript",
                       "_session_cost"}:
                hits.append(node.name)
    return sorted(hits)


def run_gates() -> None:
    s, raw = child()
    arrived, first, exit_at = pipe_evidence()

    # ---------------------------------------------------------- the liveness line
    oc = s["liveness_oracle"]
    at_call = oc["at_call"][0] if oc["at_call"] else ""
    first_line = working_lines(oc["out"])
    ck("a turn prints its line BEFORE the work: the router stand-in, called for "
       "turn 1, reads the terminal and finds `[working] turn 1` already on it",
       WORKING in at_call and "turn 1" in at_call,
       f"terminal at call time: {at_call!r}")
    ck("…and flushes it, which a captured buffer cannot show: on a real pipe the "
       "line reaches the reader more than a second before the child exits, so it "
       "was written while the turn was still asleep in the router — and no "
       "verdict line has reached the reader at that point",
       len(arrived) == 2 and arrived[1].startswith(WORKING)
       and exit_at - first > 1.0
       and not any(l.startswith("[turn ") for l in arrived),
       f"arrived={[l[:46] for l in arrived]} from {first}s, child exited "
       f"{exit_at}s")
    ck("the line names the turn, the tier that answers first, the attempts per "
       "tier and the oracle it is graded against — all four before the wait "
       "starts",
       len(first_line) == 1 and "turn 1" in first_line[0]
       and "small tier first" in first_line[0]
       and "up to 4 attempt(s) per tier" in first_line[0]
       and "grading against" in first_line[0] and "t.py" in first_line[0],
       first_line[0] if first_line else oc["out"][:200])
    ck("the turn cap reaches it: `--turns 2` prints `turn 1/2` then `turn 2/2`, "
       "so a reader knows how many asks the session will take",
       [l.split(" — ")[0] for l in working_lines(s["liveness_capped"]["out"])]
       == ["[working] turn 1/2", "[working] turn 2/2"],
       s["liveness_capped"]["out"][:240])
    chat_line = working_lines(s["liveness_chat"]["out"])
    ck("chat mode says so on the line instead of naming an oracle it does not "
       "have",
       len(chat_line) == 1 and "no oracle (chat mode)" in chat_line[0]
       and "grading against" not in chat_line[0],
       chat_line[0] if chat_line else s["liveness_chat"]["out"][:200])
    nobig = working_lines(s["liveness_no_big"]["out"])
    ck("`--allow-big never` takes the escalation out of the promise: the line "
       "cannot offer a tier the session has been told not to use",
       len(first_line) == 1 and "big tier on failure" in first_line[0]
       and len(nobig) == 1 and "big tier" not in nobig[0],
       nobig[0] if nobig else s["liveness_no_big"]["out"][:200])
    dressed = [l for l in first_line
               if "solved=" in l or "tier=" in l or l.startswith("[turn ")]
    ck("the liveness line wears no verdict's clothes: no `solved=`, no `tier=`, "
       "not prefixed `[turn ` — `benchmarks/session_pty_demo.py` types the next "
       "ask the moment `solved=` reaches the terminal, so a promise carrying "
       "that token would drive the session by itself",
       not dressed, str(dressed))
    order = ["working" if l.startswith(WORKING) else "verdict"
             for l in oc["out"].splitlines()
             if l.startswith(WORKING) or l.startswith("[turn ")]
    ck("…and the two stay in order and distinguishable in one transcript: "
       "promise, then verdict",
       order == ["working", "verdict"], str(order))
    ft = s["fail_turn"]["out"]
    ck("a failing turn still prints the oracle's own words under `oracle|`: the "
       "new line reports the plan and takes nothing off the verdict",
       any(l.startswith("  oracle| ") for l in ft.splitlines())
       and "[turn 1]" in ft, ft[-200:])

    # ---------------------------------------------------------- the four commands
    h = s["help_only"]
    ck("`/help` names all four commands and is not a turn: the router is never "
       "called, so nobody pays a generation for a question about the interface",
       all(c in h["out"] for c in ("/help", "/quit", "/save", "/retry"))
       and h["calls"] == [],
       f"calls={h['calls']} out={h['out'][:200]!r}")
    ck("`/help` states the mode it is answering in: an oracle session says a "
       "turn is graded, a chat session says nothing is verified",
       "graded against the oracle" in h["out"]
       and "nothing is verified" in s["help_chat"]["out"],
       s["help_chat"]["out"][:220])
    uk = s["unknown_command"]
    ck("an unknown slash line names itself and points at `/help` without "
       "charging for a turn",
       "`/frobnicate`" in uk["out"] and "lists the four" in uk["out"]
       and uk["calls"] == [], uk["out"][:200])

    sv = s["save_transcript"]
    body = sv["files"].get("notes.md") or ""
    ck("`/save <file>` writes the asks and the answers it has, and says how "
       "many: the file is the conversation a person would want out of a session",
       "## turn 1" in body and "you> fix the rounding" in body
       and "cents_to_str" in body and "saved 1 turn(s)" in sv["out"],
       f"out={sv['out'][-160:]!r} body={body[:180]!r}")
    ck("`/save` with no file argues instead of inventing a name, and writes "
       "nothing",
       "/save wants a file" in s["save_no_arg"]["out"]
       and s["save_no_arg"]["files"].get("notes.md") is None,
       s["save_no_arg"]["out"][-160:])
    se = s["save_early"]
    ck("`/save` before the first turn says there is nothing to write rather than "
       "leaving an empty file that reads like a saved session",
       "nothing to write yet" in se["out"] and se["files"].get("early.md") is None,
       f"out={se['out'][-140:]!r} files={se['files']}")
    so = s["save_oracle"]
    ck("`/save` refuses to write over the session's own oracle, and the oracle's "
       "bytes prove it: R-3.2 holds on a surface that is not the patch arm",
       "refuses to write over the oracle" in so["out"]
       and "assert" in (so["files"].get("t.py") or ""),
       f"out={so['out'][-160:]!r} t.py={(so['files'].get('t.py') or '')[:60]!r}")
    su = s["save_unwritable"]
    ck("`/save` to a path that cannot be written reports it and keeps the "
       "session’s exit code — a refused write is not a failed turn",
       "wrote nothing" in su["out"] and TB not in su["out"] and su["rc"] == 0,
       f"rc={su['rc']} out={su['out'][-140:]!r}")

    rt = s["retry_reasks"]
    ck("`/retry` asks the last turn again: the router is handed the same prompt "
       "twice and the session reports two turns, because the second one really "
       "did charge",
       rt["calls"] == ["fix the rounding", "fix the rounding"]
       and "turns=2" in rt["out"],
       f"calls={rt['calls']} out={rt['out'][-140:]!r}")
    ck("the retried turn gets its own liveness line, its own verdict and its own "
       "trace id, so the transcript says which attempt answered",
       len(working_lines(rt["out"])) == 2 and rt["out"].count("[turn 2]") == 1
       and [r.get("task_id") for r in rt["records"]
            if r.get("type") == "task_end"] == ["turn1", "turn2"],
       f"out={rt['out'][:220]!r}")
    re_ = s["retry_early"]
    ck("`/retry` with nothing behind it refuses rather than sending the empty "
       "question to a model",
       "nothing to retry" in re_["out"] and re_["calls"] == [],
       re_["out"][-160:])

    qc = s["quit_command"]
    ck("`/quit` ends the session on the spot: the ask typed after it never runs, "
       "and the report is the one Ctrl-D would have printed",
       qc["calls"] == ["fix the rounding"] and "[session] turns=1" in qc["out"],
       f"calls={qc['calls']} out={qc['out'][-160:]!r}")
    ck("…and leaving by `/quit` is not a failure: the session exits with the "
       "last turn's code, the way the EOF path does",
       qc["rc"] == 0, f"rc={qc['rc']}")

    # ---------------------------------------------------------- Ctrl-C
    im = s["interrupt_mid"]
    ck("Ctrl-C during a turn prints the summary instead of a stack trace: the "
       "interrupted turn is announced but never verdicted",
       TB not in im["out"] and "[session] turns=" in im["out"]
       and "[working] turn 2" in im["out"] and "[turn 2]" not in im["out"],
       f"rc={im['rc']} out={im['out'][-220:]!r}")
    ck("…and the turn it killed is not counted: one turn finished, so the report "
       "says one turn and says why the second is missing",
       "turns=1" in im["out"] and "turn 2 was interrupted" in im["out"]
       and "not counted" in im["out"], im["out"][-220:])
    ck("Ctrl-C exits 130, the code a shell gives SIGINT, so a script cannot read "
       "an interrupted session as a completed one",
       im["rc"] == 130 and s["interrupt_prompt"]["rc"] == 130
       and s["interrupt_first"]["rc"] == 130,
       f"mid={im['rc']} prompt={s['interrupt_prompt']['rc']} "
       f"first={s['interrupt_first']['rc']}")
    end = last(im["records"], "session_end")
    ck("the trace CLOSES on Ctrl-C: §33.7 reads a session with no `session_end` "
       "as a suite to RESUME, and a hand-typed conversation has no suite, so the "
       "record is written with the honest counts",
       end.get("turns") == 1 and end.get("interrupted") is True,
       str(end or "no session_end record"))
    view = trace_view(im["tdir"], im["sid"])
    ck("…and the file is intact on the reader's side: `flash trace` summarises "
       "the killed session as CLOSED rather than `INTERRUPTED -> resume`, and "
       "still renders the turn that finished",
       "closed=True" in view and "turns=1" in view and "has_turn=True" in view,
       view.replace("\n", " | "))
    ip = s["interrupt_prompt"]
    ck("Ctrl-C at the prompt is the same story with no turns: a sentence saying "
       "nothing was asked, a closed trace, and the offline report never claims a "
       "verified turn",
       "interrupted at the prompt" in ip["out"]
       and "no turns on stdin" not in ip["out"]
       and last(ip["records"], "session_end").get("turns") == 0,
       ip["out"][-180:])
    ifirst = s["interrupt_first"]
    ck("a session whose FIRST turn was killed still closes its trace, prints no "
       "`[session]` report, and leaves `written=` unclaimed",
       ifirst["rc"] == 130 and "[session]" not in ifirst["out"]
       and last(ifirst["records"], "session_end").get("turns") == 0,
       ifirst["out"][-160:])
    ck("…and it does not then contradict itself: the ask reached the program and "
       "was killed, so a second sentence claiming no turn was asked would rewrite "
       "what the reader just watched",
       "turn 1 was interrupted" in ifirst["out"]
       and "no turn was asked" not in ifirst["out"],
       ifirst["out"][-160:])

    # ---------------------------------------------------------- the session bill
    ct = s["cost_two_turns"]
    head = next((l for l in ct["out"].splitlines() if l.startswith("[cost]")), "")
    ck("a session that spent generations bills itself at exit, and every number on "
       "the line is the trace's own: two turns, the library that answered, 1900 "
       "prompt plus 320 completion read off the two `generate` records, 2.0s of "
       "model time from their `ms`, 1.0s of verifying from the `verify` records",
       head.startswith("[cost] turns=2 backend=mlx ")
       and field(ct["out"], "tokens") == "2220"
       and "(prompt=1900 completion=320)" in head
       and "generating=2.0s" in head and "verifying=1.0s" in head
       and "wall=" in head, head)
    rows = [l for l in ct["out"].splitlines() if l.startswith("       turn ")]
    ck("…and it bills turn by turn, which is what a per-turn tier report has to be: "
       "each row names the library that answered THAT turn, what the router chose, "
       "the tier that ended up answering, its tokens and its model seconds — and the "
       "two rows add up to the header's total, so neither number was rounded into "
       "agreement",
       rows == ["       turn 1: mlx routed=small tier=small 1020 tok 1.0s",
                "       turn 2: mlx routed=small tier=small 1200 tok 1.0s"]
       and 1020 + 1200 == int(field(ct["out"], "tokens")), str(rows))
    rec = last(ct["records"], "session_cost")
    ck("the bill is LOGGED as well as printed, and it is one measurement rather "
       "than two claims: the `session_cost` record carries the same turns, backend, "
       "token and time numbers the terminal showed, so `flash trace` afterwards "
       "agrees with what the person watched",
       rec.get("turns") == 2 and rec.get("backend") == "mlx"
       and rec.get("tokens_in") == 1900 and rec.get("tokens_out") == 320
       and rec.get("generating_s") == 2.0 and rec.get("verifying_s") == 1.0
       and rec.get("escalations") == 0, str(rec))
    view = bill_view(ct["tdir"], ct["sid"])
    ck("…and the file is intact on the reader's side: a grandchild rendering this "
       "session shows the `session_cost` line with `tokens_in=` visible (the new "
       "keys had to join `trace._ORDER`, or the log records what nobody can read), "
       "while `summarize()` still reports the generations' own 1900/320 — storing "
       "the bill beside them cannot count the same tokens twice",
       "prompt=1900" in view and "complete=320" in view
       and "tokens_in=1900" in view and "closed=True" in view,
       view.replace("\n", " | "))
    ce = s["cost_escalated"]
    ck("an escalated turn shows the escalation twice over: its own row carries the "
       "route the router took (`routed=small`) against the tier that actually "
       "answered (`tier=big`) and the word `escalated`, and the header counts the "
       "one escalation that was taken",
       "escalations=1" in ce["out"]
       and "       turn 1: mlx routed=small tier=big 1360 tok 1.0s escalated"
       in ce["out"] and "escalations_denied" not in ce["out"],
       ce["out"][-200:])
    cd = s["cost_denied"]
    ck("an escalation the power governor refused is counted separately — "
       "`escalations=0 escalations_denied=1` — because a turn that never loaded the "
       "big tier must not be billed as though it had",
       "escalations=0 escalations_denied=1" in cd["out"]
       and "escalated" not in cd["out"], cd["out"][-200:])
    ck("…and the library is named off the records, not assumed: this session was "
       "answered by llama.cpp, so the header says `backend=llama.cpp` and the turn's "
       "row says it too — the one part of the bill a non-Apple reader can act on, "
       "and the one a hard-coded label would get wrong in silence",
       "backend=llama.cpp" in cd["out"]
       and "       turn 1: llama.cpp routed=small tier=shed 700 tok 1.0s"
       in cd["out"], cd["out"][-220:])
    cu = s["cost_uncounted"]
    ck("a generation whose tokenizer counted nothing is billed as what it is: "
       "`uncounted=1/2 generations`, with the total left at the 550 tokens that WERE "
       "counted — folding the unknown in as a zero is how a four-figure turn gets "
       "reported as free",
       "uncounted=1/2 generations" in cu["out"]
       and field(cu["out"], "tokens") == "550"
       and "(prompt=500 completion=50)" in cu["out"], cu["out"][-200:])
    ck("…and the uncounted generation still tells you what answered: the row reads "
       "`mlx`, because `backend` rides on every `generate` record whether or not the "
       "token count came out",
       "       turn 1: mlx routed=small tier=small 550 tok 1.0s" in cu["out"],
       cu["out"][-200:])
    ckl = s["cost_killed"]
    ck("a turn Ctrl-C killed is billed but not counted: the report still says "
       "`turns=1`, the token total still carries what the killed turn spent, and the "
       "line names the gap out loud as `1000 not on a counted turn` instead of "
       "leaving one row to look like an addition mistake",
       "[cost] turns=1 " in ckl["out"]
       and "(prompt=1600 completion=420)" in ckl["out"]
       and "(1000 not on a counted turn)" in ckl["out"]
       and len([l for l in ckl["out"].splitlines()
                if l.startswith("       turn ")]) == 1, ckl["out"][-260:])
    ckf = s["cost_killed_first"]
    ck("…and the bill reaches the exit the summary never prints: a session whose "
       "FIRST turn was killed has no `[session]` report to hang a total on, and the "
       "1000 tokens it spent are still gone, so the line prints on its own with "
       "`turns=0` and the killed turn still unattributed",
       "[cost] turns=0 " in ckf["out"] and "tokens=1000" in ckf["out"]
       and "(1000 not on a counted turn)" in ckf["out"]
       and "[session]" not in ckf["out"] and ckf["rc"] == 130,
       ckf["out"][-220:])
    cn = s["cost_no_records"]
    ck("and no bill appears where nothing was billed: these turns ran and answered "
       "with not one `generate` record behind them, so the report stays out of the "
       "transcript rather than printing zeros at a person who spent nothing — the "
       "same silence the offline §6 scenarios depend on",
       "[cost]" not in cn["out"] and "[session] turns=1" in cn["out"]
       and last(cn["records"], "task_end").get("task_id") == "turn1",
       cn["out"][-200:])

    # ---------------------------------------------------------- the mirror & blast
    text = (BDIR / "session_check.py").read_text()
    ck("the parity copy carries the new clauses: `session_check.session_copy()` "
       "prints the same liveness line, dispatches the same four commands, handles "
       "the same KeyboardInterrupt and bills the same session, so `agree()` "
       "compares the current command and not an older one",
       all(n in text for n in ("cli._working_line(", "cli._session_help(",
                               "cli._save_transcript(", "cli._session_cost(",
                               "except KeyboardInterrupt:")),
       "missing: " + ", ".join(n for n in ("cli._working_line(",
                                           "cli._session_help(",
                                           "cli._save_transcript(",
                                           "cli._session_cost(",
                                           "except KeyboardInterrupt:")
                                 if n not in text))
    ck("…and on the asks that use them the two agree line for line: `/help`, a "
       "turn, `/save`, `/retry`, `/quit`, and an ask typed after `/quit`",
       s["parity_commands"]["same"],
       f"shipped={s['parity_commands']['shipped'][:4]} "
       f"copy={s['parity_commands']['copy'][:4]}")
    ck("…in chat mode too, where the same commands print a different mode "
       "sentence",
       s["parity_chat"]["same"],
       f"shipped={s['parity_chat']['shipped'][:3]} "
       f"copy={s['parity_chat']['copy'][:3]}")
    ck("the new sentences stay inside the session: `cmd_session` is the only "
       "command that references them, so `flash run` and the suite commands "
       "cannot start printing turn scaffolding",
       commands_of() == ["cmd_session"], str(commands_of()))
    pb = s["parity_bill"]
    ck("…and the copy bills the session too: `session_copy()` prints the same "
       "`[cost]` header and the same two rows on the same two asks — the wall clock "
       "is the one number two processes cannot share, so that field alone is folded "
       "and everything else is compared character for character",
       bill_lines(pb["shipped"]) == bill_lines(pb["copy"])
       and len(bill_lines(pb["shipped"])) == 3,
       f"shipped={bill_lines(pb['shipped'])} copy={bill_lines(pb['copy'])}")
    ck("the backend the bill prints is put there by the loop, not by this report: "
       "`flash/loop.py`'s own `generate` record carries a `backend` field, which is "
       "the only way the name can be the library that answered rather than the "
       "library this file assumed",
       "backend" in loop_generate_fields(), str(loop_generate_fields()))


# ---------------------------------------------------------------- mutants

#: (label, [(path, old, new)]) — each restores a way to ship the defect this
#: vector is named for.
MUTANTS = {
    "liveness_never_prints": ("the turn starts and the terminal says nothing, "
                              "which is the report this phase is about",
                              [("flash/cli.py",
                                "        in_flight = i\n"
                                "        print(_working_line(args, i, "
                                "use_oracle), flush=True)\n",
                                "        in_flight = i\n")]),
    "liveness_after_the_work": ("the line prints, but only after the generation "
                               "is over — a verdict's timing in a promise's "
                               "place",
                               [("flash/cli.py",
                                 "        print(_working_line(args, i, "
                                 "use_oracle), flush=True)\n",
                                 ""),
                                ("flash/cli.py",
                                 "        trace.event(\"task_end\", "
                                 "task_id=task[\"id\"]",
                                 "        print(_working_line(args, i, "
                                 "use_oracle), flush=True)\n"
                                 "        trace.event(\"task_end\", "
                                 "task_id=task[\"id\"]")]),
    "no_flush": ("the sentence goes into a buffer, so a pipe — every driver, "
                 "and `flash session | tee` — reads it at exit",
                 [("flash/cli.py",
                   "print(_working_line(args, i, use_oracle), flush=True)",
                   "print(_working_line(args, i, use_oracle))")]),
    "liveness_promises_a_verdict": ("the new line takes the verdict's own shape, "
                                    "so a pty driver types the next ask at a "
                                    "turn that has not been graded",
                                    [("flash/cli.py",
                                      "        return (f\"[working] turn "
                                      "{turn}{cap} — generating: small tier "
                                      "first, \"",
                                      "        return (f\"[turn {turn}{cap}] "
                                      "routed=small solved=None tier=small \"")]),
    "commands_charge_a_turn": ("a slash line falls through to the router, so "
                               "`/help` costs a generation",
                               [("flash/cli.py",
                                 "                          \"the four\")\n"
                                 "                continue\n",
                                 "                          \"the four\")\n")]),
    "help_still_the_default": ("`/help` describes the oracle it is not using: a "
                               "chat reader is told every turn is verified",
                               [("flash/cli.py",
                                 "print(_session_help(use_oracle))",
                                 "print(_session_help(True))")]),
    "save_writes_over_the_oracle": ("`/save` overwrites the file the session "
                                    "grades itself against — the one write R-3.2 "
                                    "exists to forbid",
                                    [("flash/cli.py",
                                      "                print(f\"session: /save "
                                      "refuses to write over the oracle \"\n"
                                      "                      f\"{args.test} — "
                                      "the session grades every turn against "
                                      "it\")\n"
                                      "                return\n",
                                      "                pass\n")]),
    "retry_sends_a_blank_ask": ("`/retry` with nothing behind it asks the model "
                                "the empty question",
                                [("flash/cli.py", "                    if not "
                                  "last_ask:", "                    if False:")]),
    "interrupt_is_a_traceback": ("Ctrl-C goes back to being the shipped "
                                 "behaviour: the exception climbs out of the "
                                 "command and the session is left unclosed",
                                 [("flash/cli.py",
                                   "    except KeyboardInterrupt:\n"
                                   "        # Ctrl-C is a normal way to leave a "
                                   "session",
                                   "    except ZeroDivisionError:\n"
                                   "        # Ctrl-C is a normal way to leave a "
                                   "session")]),
    "interrupt_counts_the_killed_turn": ("the turn that generated nothing and "
                                         "verified nothing is reported as a turn",
                                         [("flash/cli.py",
                                           "            ran -= 1\n", "")]),
    "interrupt_denies_the_ask": ("a killed first turn is reported as a session "
                                 "nobody typed into — the second sentence "
                                 "contradicts the one above it",
                                 [("flash/cli.py", "            if not killed:",
                                   "            if True:")]),
    "copy_drops_the_liveness_line": ("the shipped command moves and the parity "
                                     "copy does not, so `agree()` starts "
                                     "greenlighting an older session",
                                     [("benchmarks/session_check.py",
                                       "            print(cli._working_line"
                                       "(args, i, use_oracle), flush=True)\n",
                                       "")]),
    "bill_never_prints": ("the guard that hides the bill when nothing was spent "
                          "hides it always: the session ends in silence and the "
                          "numbers stay in the trace where nobody reads them",
                          [("flash/cli.py",
                            "    gens = [r for r in recs if r.get(\"type\") == "
                            "\"generate\"]\n    if not gens:\n        return []\n",
                            "    gens = [r for r in recs if r.get(\"type\") == "
                            "\"generate\"]\n    return []\n")]),
    "uncounted_folded_as_zero": ("a generation the tokenizer could not count is "
                                 "billed as a free one, so a session that spent "
                                 "four figures reports three",
                                 [("flash/cli.py",
                                   "    uncounted = sum(1 for r in gens if r.get("
                                   "\"prompt_tokens\") is None\n"
                                   "                    and r.get("
                                   "\"completion_tokens\") is None)\n",
                                   "    uncounted = 0\n")]),
    "killed_turn_unexplained": ("the rows stop adding up to the header total and "
                                "the line says nothing about the difference, so "
                                "the killed turn's spend reads as an arithmetic "
                                "mistake in the report",
                                [("flash/cli.py", "    if attributed != tokens:",
                                  "    if False:")]),
    "bill_never_logged": ("printed but not stored: the trace closes with no "
                          "`session_cost` record, so `flash trace` afterwards "
                          "contradicts what the person watched",
                          [("flash/cli.py",
                            "    trace.event(\"session_cost\", "
                            "turns=len(turns),\n                backend=\""
                            "/\".join(all_backs) or None, "
                            "wall_s=round(wall, 1),\n                "
                            "generating_s=s[\"gen_s\"], "
                            "verifying_s=s[\"verify_s\"],\n                "
                            "tokens_in=s[\"prompt_tokens\"], "
                            "tokens_out=s[\"completion_tokens\"],\n"
                            "                uncounted=uncounted or None, "
                            "escalations=took,\n"
                            "                escalations_denied=denied or "
                            "None)\n",
                            "")]),
    "bill_logged_as_a_generation": ("the report writes itself as if it were work: "
                                    "`flash trace`'s totals now count the bill "
                                    "among the generations, so the denominator of "
                                    "`uncounted=` and the record a reader looks for "
                                    "both go wrong from one key word",
                                    [("flash/cli.py",
                                      "    trace.event(\"session_cost\", "
                                      "turns=len(turns),",
                                      "    trace.event(\"generate\", "
                                      "turns=len(turns),")]),
    "backend_assumed_not_read": ("the label is hard-coded to MLX, which is the one "
                                 "sentence in the bill a llama.cpp user can check "
                                 "and find wrong",
                                 [("flash/cli.py", "        name = r.get("
                                   "\"backend\")", "        name = \"mlx\"")]),
    "header_only_no_rows": ("the total with no per-turn detail: a reader cannot "
                            "tell which turn spent it, which tier answered or "
                            "whether one escalated",
                            [("flash/cli.py", "    out = [head]\n    for t in "
                              "turns:", "    out = [head]\n    for t in []:")]),
    "loop_stops_naming_the_backend": ("`flash/loop.py` writes its `generate` "
                                      "record without the library that answered, so "
                                      "the bill has nothing honest to read",
                                      [("flash/loop.py",
                                        "                max_tokens=max_tokens, "
                                        "ms=round((time.perf_counter() - t0) * "
                                        "1000),\n                backend=be.name,\n",
                                        "                max_tokens=max_tokens, "
                                        "ms=round((time.perf_counter() - t0) * "
                                        "1000),\n")]),
    "copy_drops_the_bill": ("the shipped command bills the session and the parity "
                            "copy stops printing the report, so `agree()` keeps "
                            "greenlighting a session that never said what it cost",
                            [("benchmarks/session_check.py",
                              "        for line in cli._session_cost(sid, turns, "
                              "time.monotonic() - started):\n            "
                              "print(line)\n", "")]),
}


def _apply(patches):
    """Patch the files and return the copies to put back.

    Keyed by path, not by hunk: two hunks of one mutant land in `flash/cli.py`,
    and a list would remember the once-patched text as the "original" to restore,
    leaving the second mutant to start from the first one's leftover.
    """
    saved: dict[str, str] = {}
    for rel, old, new in patches:
        p = ROOT / rel
        text = p.read_text()
        if text.count(old) != 1:
            for past, before in saved.items():
                (ROOT / past).write_text(before)
            raise AssertionError(f"mutant anchor is not unique in {rel}")
        saved.setdefault(rel, text)
        p.write_text(text.replace(old, new))
    return [(ROOT / rel, text) for rel, text in saved.items()]


def mutate(one: str | None = None) -> int:
    ran = failed = 0
    for name in ([one] if one else sorted(MUTANTS)):
        label, patches = MUTANTS[name]
        CHECKS.clear()
        saved = _apply(patches)
        broke = ""
        try:
            run_gates()
        except AssertionError as e:
            # A mutant that stops the command importing is caught by every check
            # at once; report it as such rather than ending the sweep.
            broke = str(e)
        finally:
            for p, text in saved:
                p.write_text(text)
        bad = [l for l, ok, _ in CHECKS if not ok]
        if broke:
            bad.append(broke)
        ran += 1
        print(f"  {'ok  ' if bad else 'FAIL'} MUTATION: {label} -> "
              f"{len(bad)} check(s) fail" + (f"  {bad[0][:78]}" if bad else ""))
        failed += not bad
    print(f"\nsession-ux mutants: {ran - failed}/{ran} gates defeated by exactly "
          "their checks")
    return failed


def main(argv: list) -> int:
    if argv and argv[0] == "--mutant":
        return 1 if mutate(argv[1] if len(argv) > 1 else None) else 0
    run_gates()
    n = len(CHECKS)
    bad = [c for c in CHECKS if not c[1]]
    for label, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}"
              + (f"\n       [{detail}]" if detail and not ok else ""))
    print(f"\nsession-ux checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
