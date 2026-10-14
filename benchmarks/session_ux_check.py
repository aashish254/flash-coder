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

    python benchmarks/session_ux_check.py
    python benchmarks/session_ux_check.py --mutant [name]
"""
from __future__ import annotations

import ast
import json
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
        raise_at=0, keep=()):
    root = sc.new_tree()
    tdir = Path(tempfile.mkdtemp(prefix="flash-ux-trace-"))
    fake = sc.fake_solve(script or [sc.FIX_MONEY])
    buf = io.StringIO()
    at_call = []
    calls = []

    def wrapped(small, big, task, r, **kw):
        # What the reader can see at the exact moment the work is handed over.
        # This function IS the router's stand-in, so the buffer it reads is the
        # terminal at the start of the turn; nothing printed later can be
        # credited to it.
        at_call.append(buf.getvalue())
        if raise_at and len(calls) + 1 == raise_at:
            raise KeyboardInterrupt
        calls.append(task["prompt"])
        return fake(small, big, task, r, **kw)

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
            rc = cli.cmd_session(args)
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


def commands_of() -> list[str]:
    """Which `cmd_*` bodies reference the new session helpers (AST, not grep)."""
    tree = ast.parse((ROOT / "flash" / "cli.py").read_text())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("cmd_"):
            used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            if used & {"_working_line", "_session_help", "_save_transcript"}:
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

    # ---------------------------------------------------------- the mirror & blast
    text = (BDIR / "session_check.py").read_text()
    ck("the parity copy carries the new clauses: `session_check.session_copy()` "
       "prints the same liveness line, dispatches the same four commands and "
       "handles the same KeyboardInterrupt, so `agree()` compares the current "
       "command and not an older one",
       all(n in text for n in ("cli._working_line(", "cli._session_help(",
                               "cli._save_transcript(",
                               "except KeyboardInterrupt:")),
       "missing: " + ", ".join(n for n in ("cli._working_line(",
                                           "cli._session_help(",
                                           "cli._save_transcript(",
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
    "copy_drops_the_liveness_line": ("the shipped command moves and the parity "
                                     "copy does not, so `agree()` starts "
                                     "greenlighting an older session",
                                     [("benchmarks/session_check.py",
                                       "            print(cli._working_line"
                                       "(args, i, use_oracle), flush=True)\n",
                                       "")]),
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
