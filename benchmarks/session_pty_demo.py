"""R-7.15 clause 7's live arm: drive `flash session` through a real pseudo-terminal.

Why a pty and not a pipe: every other vector here feeds stdin from a string, and a
string ends at EOF. The claim under test is that **turn N's answer reaches the
terminal before turn N+1 is typed** — a property that is invisible to a here-doc,
because a here-doc has already handed over all its lines. So this opens a terminal,
writes one line, waits for that turn's `solved=`, and only then writes the next,
timestamping both. The interleaving is checked offline in
`benchmarks/session_check.py` (four checks, two mutants); this file is the live half.

The tree is seeded here rather than assumed, because a transcript of a session that
ran against a file nobody can reproduce is a story, not a witness.

    python benchmarks/session_pty_demo.py
    python benchmarks/session_pty_demo.py --tree /tmp/flash-chat-demo --keep
    python benchmarks/session_pty_demo.py --interrupt

`--interrupt` is the second claim, and the one a here-doc cannot make: a person who
thinks a turn is hung presses Ctrl-C, and the session has to survive it. The offline
gate raises inside the router to simulate that; here the byte 0x03 goes into a real
terminal whose slave is the child's controlling terminal, so SIGINT lands wherever
the model load happens to be. It exits 130, closes its trace, and writes nothing
back. `benchmarks/session_ux_check.py` covers the same claims with a stubbed router;
this file is the live half of both.

`--keep` leaves the scratch tree on disk so `flash session` can be sat on by hand
afterwards. The model is the one `flash session` routes to, so this needs the
backend and takes ~40 s for two turns; it is NOT a §6 battery line for that reason.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MONEY = '''def cents_to_str(cents: int) -> str:
    return f"${cents / 100}"
'''

# `<TMPDIR>` is the token `benchmarks/portable_paths_check.py` requires in place of
# a machine path; this file writes it into a scratch tree, so the substitution
# happens at seed time and nothing here ever carries a checkout path.
ORACLE = '''import sys
sys.path.insert(0, "<TMPDIR>")
from money import cents_to_str

assert cents_to_str(5) == "$0.05"
assert cents_to_str(150) == "$1.50"
assert cents_to_str(-105) == "-$1.05"
print("ORACLE GREEN")
'''

# The asks are the two a person makes of a formatting function: fix the shape, then
# add a contract. They are written as a user would write them, because the arm that
# matters here is the one where the model, not the harness, chooses the patch.
TURNS = [
    "zero-pad the cents and keep a negative sign in front of the dollar sign, "
    "exactly as t.py asserts",
    "make cents_to_str raise ValueError when cents is not an int",
    "quit",
]


def seed(tree: Path) -> None:
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "money.py").write_text(MONEY)
    (tree / "t.py").write_text(ORACLE.replace("<TMPDIR>", str(tree)))


def run_session(tree: Path, interrupt: bool = False,
                interrupt_after: float = 3.0) -> tuple[list[str], int, float]:
    """Return (transcript lines, child exit code, wall seconds).

    Lines are stamped as they arrive, in arrival order, which is the whole point: a
    reader has to be able to see that the second keystroke came after the first
    verdict, not merely that both turns eventually printed one.

    `interrupt=True` is the other half of what a keyboard does: it sends the byte
    a person sends when they decide a turn is hung. A pty turns `\\x03` into SIGINT
    only when the slave is the child's controlling terminal and the child leads its
    own session, so this arm starts one with `TIOCSCTTY`; without those two the byte
    would sit in the input buffer and the signal path would never be exercised.
    """
    cmd = [sys.executable, "-u", "-m", "flash.cli", "session",
           "--context", str(tree), "--test", str(tree / "t.py"), "--apply"]
    master, slave = pty.openpty()
    started = time.time()
    spawn = {}
    if interrupt:
        spawn["start_new_session"] = True
        spawn["preexec_fn"] = lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave,
                            stderr=subprocess.STDOUT, cwd=str(tree.parent),
                            close_fds=True, **spawn)
    os.close(slave)
    out: list[str] = []
    pending = list(TURNS[:1] if interrupt else TURNS)
    buf = ""
    sent = False

    def emit(text: str) -> None:
        nonlocal buf
        buf += text.replace("\r\n", "\n")
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            stamp = f"[t+{time.time() - started:6.2f}s]"
            out.append(f"{stamp} {line.rstrip(chr(13))}")
            print(f"{stamp} {line}", flush=True)

    os.write(master, (pending.pop(0) + "\n").encode())
    while True:
        ready, _, _ = select.select([master], [], [], 180)
        if not ready:
            emit("\n!! no output for 180s — the session is not answering\n")
            proc.kill()
            break
        try:
            chunk = os.read(master, 4096)
        except OSError:
            break
        if not chunk:
            break
        text = chunk.decode(errors="replace")
        emit(text)
        if interrupt and not sent and "[working]" in text:
            # Sent into the wait, not at a prompt: with a small delay the signal
            # lands during the weight load, with a larger one it lands inside
            # generation, which is where a C++ library is free to behave
            # differently. A person reaches for Ctrl-C in whichever of those two
            # windows the turn has been sitting in.
            sent = True
            time.sleep(interrupt_after)
            emit("\n<<< keyboard sent Ctrl-C (\\x03) into the terminal\n")
            os.write(master, b"\x03")
        elif not interrupt and pending and "solved=" in text:
            time.sleep(0.2)
            nxt = pending.pop(0)
            emit(f"\n<<< typed: {nxt!r}\n")
            os.write(master, (nxt + "\n").encode())
    if buf.strip():
        out.append(f"[t+{time.time() - started:6.2f}s] {buf}")
    rc = proc.wait()
    return out, rc, round(time.time() - started, 1)


def recheck(tree: Path) -> str:
    """Run the oracle against the bytes now on disk, outside the session."""
    test = (tree / "t.py").read_text().replace("<TMPDIR>", str(tree))
    proc = subprocess.run([sys.executable, "-I", "-c", test], cwd=str(tree),
                          capture_output=True, text=True)
    tail = (proc.stdout.strip() + " " + proc.stderr.strip()[-300:]).strip()
    return f"rc={proc.returncode} {tail}"


TRACES = ROOT / "benchmarks" / "results" / "traces"


def trace_view(sid: str) -> str:
    """What a reader sees when they replay the killed session."""
    proc = subprocess.run([sys.executable, "-m", "flash.cli", "trace", "show", sid],
                          cwd=str(ROOT), capture_output=True, text=True)
    return (proc.stdout + proc.stderr).strip()


def trace_kinds(sid: str) -> str:
    """The loop's own records for the killed turn, read off the trace file.

    This is what separates the two interrupt windows: a session killed during the
    weight load leaves nothing between `session_start` and `session_end`, while one
    killed inside the work leaves the generations it paid for. Naming them keeps a
    witness from claiming it interrupted a turn that had not started any yet.
    """
    try:
        rows = [json.loads(line) for line in
                (TRACES / f"{sid}.jsonl").read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return "unreadable"
    kinds = [r.get("type") for r in rows if r.get("type") not in
             ("session_start", "session_end")]
    return ", ".join(kinds) if kinds else "none — the signal landed before the " \
        "loop recorded anything"


def interrupt_claims(lines: list[str], rc: int, tree: Path,
                     sid: str) -> list[tuple[bool, str]]:
    """What this arm proves, each claim carrying the number it was read from.

    The offline gate (`benchmarks/session_ux_check.py`) simulates the interrupt by
    raising inside the router; this is the path a keyboard takes, where SIGINT
    lands wherever the process happens to be — mid model load, mid generate — and
    a C++ library in the middle of it is free to behave differently.
    """
    transcript = "\n".join(lines)

    def at(needle: str) -> str | None:
        """The stamp of the first line carrying `needle`, in arrival order."""
        for line in lines:
            if needle in line:
                return line.split("]", 1)[0] + "]"
        return None

    working, killed_at = at("[working] turn 1"), at("sent Ctrl-C")
    money = (tree / "money.py").read_text()
    view = trace_view(sid) if sid else ""
    return [
        (bool(working), f"the turn announced itself while it was still working: "
                        f"`[working] turn 1` reached the terminal at {working or '?'}, "
                        f"before any verdict"),
        (bool(working) and bool(killed_at),
         f"Ctrl-C was sent into the wait, not at a prompt: the keystroke is stamped "
         f"{killed_at or '?'}, after the line that started the turn"),
        ("turn 1 was interrupted" in transcript,
         "the session named the turn it lost instead of dying silently"),
        ("Traceback" not in transcript,
         "no stack trace reached the terminal — this is the whole point of the arm"),
        ("solved=" not in transcript and "[turn 1]" not in transcript,
         "the killed turn printed no verdict: nothing here claims an oracle ran"),
        (rc == 130, f"the child exited {rc}, the code a shell gives SIGINT, so a "
                    f"script cannot read an interrupted session as a finished one"),
        (money == MONEY,
         "money.py is still the seeded bytes: a turn that never verified wrote "
         "nothing back"),
        (bool(sid) and "INTERRUPTED" not in view and "resume with" not in view,
         f"`flash trace show {sid or '?'}` reports the session closed — not a suite "
         f"waiting to be resumed — on the reader's side of the file"),
    ]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="/tmp/flash-chat-demo",
                    help="scratch project the session edits (default %(default)s)")
    ap.add_argument("--interrupt", action="store_true",
                    help="send a real Ctrl-C into the turn that just announced "
                         "itself, instead of typing the next ask (R-7.17c arm)")
    ap.add_argument("--interrupt-after", type=float, default=3.0,
                    help="seconds after the turn's `[working]` line to send the "
                         "Ctrl-C: ~3 lands in the weight load, ~20 lands inside "
                         "generation (default %(default)s)")
    ap.add_argument("--out", default=None,
                    help="witness to write (default: benchmarks/results/"
                         "session_pty_<date>.log, or session_pty_interrupt_<date>"
                         ".log with --interrupt)")
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch tree on disk afterwards")
    args = ap.parse_args(argv)

    tree = Path(args.tree)
    if tree.exists() and not tree.is_dir():
        print(f"session_pty_demo: {tree} exists and is not a directory")
        return 2
    stem = "session_pty_interrupt" if args.interrupt else "session_pty"
    out_path = Path(args.out or str(ROOT / "benchmarks" / "results" /
                                    ("%s_%s.log" % (stem, time.strftime("%Y%m%d")))))
    # The session writes its own trace file, and its name IS the session id. Taken
    # by diffing the directory rather than read off stdout, because a session whose
    # only turn was killed never prints the `[trace] replay` line — it returns 130
    # before the report.
    before = {p.stem for p in TRACES.glob("*.jsonl")}
    seed(tree)
    lines, rc, seconds = run_session(tree, args.interrupt,
                                     args.interrupt_after)
    money = (tree / "money.py").read_text()
    verdict = recheck(tree)
    new = sorted({p.stem for p in TRACES.glob("*.jsonl")} - before)
    sid = new[0] if len(new) == 1 else ""
    claims = interrupt_claims(lines, rc, tree, sid) if args.interrupt else []

    driver_extra = (" --interrupt"
                    + (f" --interrupt-after {args.interrupt_after:g}"
                       if args.interrupt_after != 3.0 else "")
                    if args.interrupt else "")
    header = [
        "# " + ("R-7.17c live arm, " if args.interrupt
                else "R-7.15 clause 7 live arm, ") + time.strftime("%Y-%m-%d") +
        ": a real pseudo-terminal, so the keyboard path is what runs.",
        "# command: python -m flash.cli session --context {c} --test {c}/t.py "
        "--apply".format(c=tree),
        (f"# turns: one ask typed, then Ctrl-C (byte 0x03) sent "
         f"{args.interrupt_after:g}s after the turn's own `[working]` line reached "
         "the terminal, so the signal lands inside the wait and not at a prompt."
         if args.interrupt else
         "# turns, in the order they were typed (each only AFTER the previous "
         "turn's verdict reached the terminal): " +
         " / ".join(repr(t) for t in TURNS)),
        "# tree: money.py seeded as cents_to_str(c) -> f\"${c / 100}\"; oracle t.py "
        "3 asserts, the third one being the negative case",
        f"# driver: python benchmarks/session_pty_demo.py --tree {tree}"
        + driver_extra + " --out <this file>",
        f"# session trace written by this run: {sid or 'none found'}",
        (f"# what the loop had already recorded when the signal arrived: "
         f"{trace_kinds(sid)}" if args.interrupt else
         "# turns, in the order they were typed (each only AFTER the previous "),
        f"# child exit code: {rc} after {seconds}s",
        "# Every line below is stamped at the moment the terminal received it.",
    ]
    body = header + lines + [
        f"=== child exited rc={rc} after {seconds}s ===",
        "=== final money.py ===",
        *money.rstrip("\n").split("\n"),
        "=== oracle re-run against the bytes now on disk ===",
        verdict,
    ]
    if args.interrupt:
        body += [
            "=== what this arm proves, each line read off the transcript above, "
            "the tree, or `flash trace show` ===",
            *[f"  {'OK  ' if ok else 'FAIL'} {text}" for ok, text in claims],
            f"=== {sum(1 for ok, _ in claims if ok)}/{len(claims)} claims hold ===",
        ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(body) + "\n")
    print(f"\nwitness: {out_path}")
    for ok, text in claims:
        print(f"  {'OK  ' if ok else 'FAIL'} {text}")
    if not args.keep:
        shutil.rmtree(tree)
        print(f"scratch tree {tree} removed (--keep leaves it for you to sit on)")
    return sum(1 for ok, _ in claims if not ok)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
