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

`--keep` leaves the scratch tree on disk so `flash session` can be sat on by hand
afterwards. The model is the one `flash session` routes to, so this needs the
backend and takes ~40 s for two turns; it is NOT a §6 battery line for that reason.
"""
from __future__ import annotations

import argparse
import os
import pty
import select
import shutil
import subprocess
import sys
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


def run_session(tree: Path) -> tuple[list[str], int, float]:
    """Return (transcript lines, child exit code, wall seconds).

    Lines are stamped as they arrive, in arrival order, which is the whole point: a
    reader has to be able to see that the second keystroke came after the first
    verdict, not merely that both turns eventually printed one.
    """
    cmd = [sys.executable, "-u", "-m", "flash.cli", "session",
           "--context", str(tree), "--test", str(tree / "t.py"), "--apply"]
    master, slave = pty.openpty()
    started = time.time()
    proc = subprocess.Popen(cmd, stdin=slave, stdout=slave,
                            stderr=subprocess.STDOUT, cwd=str(tree.parent),
                            close_fds=True)
    os.close(slave)
    out: list[str] = []
    pending = list(TURNS)
    buf = ""

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
        if pending and "solved=" in text:
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


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", default="/tmp/flash-chat-demo",
                    help="scratch project the session edits (default %(default)s)")
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / "results" /
                        ("session_pty_%s.log" % time.strftime("%Y%m%d"))),
                    help="witness to write (default: benchmarks/results/"
                         "session_pty_<date>.log)")
    ap.add_argument("--keep", action="store_true",
                    help="leave the scratch tree on disk afterwards")
    args = ap.parse_args(argv)

    tree = Path(args.tree)
    if tree.exists() and not tree.is_dir():
        print(f"session_pty_demo: {tree} exists and is not a directory")
        return 2
    seed(tree)
    lines, rc, seconds = run_session(tree)
    money = (tree / "money.py").read_text()
    verdict = recheck(tree)

    header = [
        "# R-7.15 clause 7 live arm, " + time.strftime("%Y-%m-%d") +
        ": a real pseudo-terminal, so the keyboard path is what runs.",
        "# command: python -m flash.cli session --context {c} --test {c}/t.py "
        "--apply".format(c=tree),
        "# turns, in the order they were typed (each only AFTER the previous "
        "turn's verdict reached the terminal): " + " / ".join(repr(t) for t in TURNS),
        "# tree: money.py seeded as cents_to_str(c) -> f\"${c / 100}\"; oracle t.py "
        "3 asserts, the third one being the negative case",
        f"# driver: python benchmarks/session_pty_demo.py --tree {tree} --out <this "
        "file>",
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
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(body) + "\n")
    print(f"\nwitness: {out_path}")
    if not args.keep:
        shutil.rmtree(tree)
        print(f"scratch tree {tree} removed (--keep leaves it for you to sit on)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
