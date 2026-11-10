"""`flash setup` — one command from a finished install to a chat that answers.

The product is a person who has never seen this repo running one command and
then asking the agent about their own code. Everything that has to happen
before the first answer — is the interpreter new enough, is a model library
present, are the weights on disk and are they the weights this repo pins, does
a token actually come out — happens here, in this order, and each step prints
the sentence a stranger needs rather than a traceback they have to interpret.

Five steps, printed as five steps, because the shape of the command is the
shape of the problem: a failure at step 2 has nothing to do with step 4, and a
user who can see which step failed can act on it.

    1  what this machine is         `flash doctor`'s own answers
    2  the weights come down        `flash models pull`'s bar, not a new one
    3  the bytes are verified       the pinned SHA256, re-read off the disk
    4  one real turn answers        `flash chat`'s own loop, 24 tokens
    5  what you can now do          the commands, named, with the turn's seconds

Every exit that is not 0 prints a sentence naming the remedy, and `--verbose`
is the only way to see a traceback: a first-run command that dies with a stack
of frame names sends the user to an issue tracker instead of telling them what
to do.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from flash import backend, doctor, models

#: The turn step 4 asks for. Answered in under ten tokens by any of the pinned
#: tiers, so the step costs seconds rather than a generation's worth of time —
#: and it is a question whose correct answer is checkable by eye, which a
#: "write me a fibonacci" prompt is not.
PROOF_PROMPT = "Reply with exactly one word: READY"

#: Step 4's ceiling. Small enough that a model which rambles still finishes in a
#: few seconds, large enough that a correct one-word answer is not truncated by
#: the ceiling rather than by the model.
PROOF_TOKENS = 24

#: The MLX snapshot a Mac generates on when no GGUF is asked for. Read off the
#: CLI's own default at call time rather than copied, so `flash setup` proves the
#: same weights `flash chat` is about to load.
def mlx_default() -> str:
    from flash import cli
    return cli.DEFAULT_MODEL


def say(line: str = "", out=None) -> None:
    """One line, flushed. A setup run waits on a multi-gigabyte download; a
    buffered stdout would report the past and look like a hang."""
    print(line, file=out or sys.stdout, flush=True)


def step(n: int, total: int, title: str, out=None) -> None:
    say(f"[{n}/{total}] {title}", out=out)


# ------------------------------------------------------------------ step 1
def check_machine(out) -> dict:
    """doctor's own report, printed whole. It already names a remedy per line,
    so setup does not paraphrase it — a second copy of those sentences is a
    second thing that can be wrong."""
    ans = doctor.answers()
    say(doctor.report(ans), out=out)
    return ans


# ------------------------------------------------------------------ step 2
def need_pull(tier_name: str) -> bool:
    """Is the pinned file for this tier already registered AND on disk?

    Both halves: the manifest is the claim and the file is the fact. A
    manifest that names a file somebody deleted is exactly the corrupt cache
    this command has to survive, and `models.verify` says so in step 3.
    """
    have = backend.manifest().get(tier_name)
    return not (have and (models.cache() / have).is_file())


def pull(tier_name: str, yes: bool, out) -> int:
    """Hand the download to `flash models`, entirely. No second bar, no second
    size warning, no second resume logic — the seam that already passed
    `benchmarks/models_pull_check.py` is the one that runs here.

    The return code is the only thing this function reads, because the sentences
    are already written where they belong: an unreachable host, a volume with no
    room for what remains, a 416 and a SHA256 mismatch are each answered by
    `models.fetch` with the remedy and the resume hint, and a second copy of
    those words here would be a second thing that can be wrong. What does NOT
    reach `fetch` is the cache directory itself — `fetch` does its `mkdir` before
    its first `try`, so an unwritable `FLASH_GGUF_DIR` lands here, and it is the
    one failure a first run can hit that the download layer cannot name.
    """
    t = models.tier(tier_name)
    try:
        return models.fetch(t, yes=yes, progress=True, out=out)
    except OSError as e:
        d = models.cache()
        say(f"setup stopped: {d} cannot be written ({e}). Point flash at a "
            f"directory you own with FLASH_GGUF_DIR=/path and run `flash setup` "
            f"again.", out=out)
        return 1


# ------------------------------------------------------------------ step 3
def verify(tier_name: str, out) -> bool:
    """Re-hash the file that is on disk against the pin. Deliberately a second
    read after the pull's own hash: the claim this step makes is about the disk
    now, not about the bytes the download saw."""
    ok, line = models.verify(tier_name)
    say(line, out=out)
    return ok


# ------------------------------------------------------------------ step 4
def prove(tier_name: str, backend_name: str, out) -> tuple[bool, str, float]:
    """One real turn through the chat loop. Returns (answered, text, seconds).

    This goes through `flash.loop.solve_chat` rather than the seam directly
    because the thing worth proving is the whole path a chat turn takes —
    template, load, generate, decode — and not merely that a library imports.
    """
    from flash import loop

    ref = tier_name if backend_name == "llama.cpp" else mlx_default()
    t0 = time.perf_counter()
    model, tok = backend.load(ref)
    task = {"id": "setup", "prompt": PROOF_PROMPT, "test": "", "chat": True,
            "context": os.getcwd(), "files": {}, "edit": False, "multi": True}
    loop.ADAPTER = ""
    r = loop.solve_chat(model, tok, task, max_attempts=1,
                        max_tokens=PROOF_TOKENS, stage="small")
    secs = time.perf_counter() - t0
    text = (r.attempts[-1].code if r.attempts else "").strip()
    return bool(r.solved and text), text, secs


# ------------------------------------------------------------------ step 5
def capabilities(ans: dict, tier_name: str, present: bool, secs: float,
                 out) -> None:
    profiles = ans["backend"].get("profiles") or []
    for p in profiles:
        say(f"       {p['backend']:10s} {'ready' if p['available'] else 'not here'}"
            f"   {p.get('library') or p.get('why') or ''}", out=out)
    say("", out=out)
    say("  you can now run:", out=out)
    say("    flash chat            ask about this folder, in prose", out=out)
    say("    flash run --test t.py one task, scored against your own test", out=out)
    say("    flash verify          is this install and this project healthy?",
        out=out)
    say("    flash ui              the same chat in a local page", out=out)
    if present:
        say(f"    weights: tier `{tier_name}` in {models.cache()}", out=out)
    if secs:
        # The turn's wall clock and the word count, which is what step 4 actually
        # measured. A tokens/s figure would need the tokenizer's count, and the
        # trace keeps that only while a session is open — so the line says seconds
        # rather than borrowing a unit it did not earn.
        say(f"    measured: that turn took {secs:.1f}s on this machine", out=out)


def add_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--tier", choices=sorted(models.TIERS), default="fast",
                   help="which pinned GGUF tier to make sure is on disk "
                        "(default: fast, the one a first chat should use)")
    p.add_argument("--yes", action="store_true",
                   help="accept the download's size without asking (required "
                        "when stdin is not a terminal)")
    p.add_argument("--skip-pull", dest="pull", action="store_false",
                   help="check and prove what is already here; download nothing")
    p.add_argument("--verbose", action="store_true",
                   help="let an unexpected error raise its traceback instead of "
                        "printing one sentence")


def run(a, out=None) -> int:
    out = out or sys.stdout
    total = 5
    say("flash setup — five steps from this install to a first answer", out=out)
    say("", out=out)

    step(1, total, "what this machine is", out=out)
    ans = check_machine(out)

    route = getattr(a, "backend", "auto") or "auto"
    if route == "auto":
        # The same rule `flash chat` uses, asked of the seam rather than
        # guessed at, so setup proves the turn on the library the user is about
        # to run: a Mac with MLX answers on MLX, every other box on the GGUF.
        prof = {p["backend"]: p for p in (ans["backend"].get("profiles") or [])}
        route = "mlx" if (prof.get("mlx") or {}).get("available") else "llama.cpp"
    say("", out=out)

    # A GGUF is only worth gigabytes when something is going to load it. On the
    # MLX route the chat answers from MLX's own Hub snapshot, so downloading the
    # pinned tier here would be bytes the user never spends, and a metered
    # download is the one cost this project cannot take back. So: fetch the tier
    # when llama.cpp is the route or the file is already here, and say which
    # weight step 4 loads either way.
    present = not need_pull(a.tier)
    gguf = route == "llama.cpp" or present
    fetching = gguf and not present and a.pull

    step(2, total,
         "the weights come down" if fetching else
         "nothing to fetch — the tier is already in the cache" if gguf and present
         else "no download (--skip-pull), so step 4 has no GGUF to load" if gguf
         else f"no download — {route} answers from its own Hub snapshot",
         out=out)
    if fetching:
        rc = pull(a.tier, a.yes, out)
        if rc:
            return rc
        # The pull put bytes on disk, so step 3 has an answer it did not have
        # above. `present` is re-read rather than remembered: a fresh download is
        # hashed like every other weight this command vouches for, and carrying
        # the old False forward would skip the one check that makes the pull
        # worth trusting.
        present = not need_pull(a.tier)
    elif gguf and present:
        say(f"       {models.cache()}", out=out)
    say("", out=out)

    step(3, total, "the bytes on disk are the bytes this repo pins", out=out)
    if not present:
        say(f"       nothing on disk for tier `{a.tier}`, so there is no hash to "
            f"check here.", out=out)
    elif not verify(a.tier, out):
        say("setup stopped: the weight is not verified, so nothing was run. "
            "The line above names the file and the tier; `flash models pull "
            f"{a.tier} --force` replaces it.", out=out)
        return 1
    say("", out=out)

    step(4, total, f"one real turn answers ({route})", out=out)
    if route == "mlx":
        # The MLX snapshot is not this repo's file to hash or meter, so the only
        # honest thing is to name it before the library starts fetching it:
        # a stranger watching a multi-gigabyte bar they did not agree to is the
        # first-run failure this command exists to prevent.
        say(f"       loading {mlx_default()} — the Hub fetches it once, "
            f"if this box has never answered", out=out)
    try:
        ok, text, secs = prove(a.tier, route, out)
    except (backend.NoBackend, backend.NoFeature) as e:
        # Both are already the house refusal: several aligned lines, each
        # naming a fact, the last naming the command. Printed whole.
        say(str(e), out=out)
        return 2
    if not ok:
        say(f"setup stopped: the model loaded but the turn came back empty in "
            f"{secs:.1f}s. Run `flash chat` and ask one question — if that "
            "answers too, this step was too strict and belongs in an issue.",
            out=out)
        return 1
    say(f"       answered in {secs:.1f}s: {text[:60]!r}", out=out)
    say("", out=out)

    step(5, total, "what you can now do", out=out)
    capabilities(ans, a.tier, present, secs, out)
    return 0


def dispatch(a) -> int:
    """The CLI's entry for the command: one try, and any error the steps above
    did not already name becomes a sentence about this command rather than a
    stack frame about somebody's library."""
    try:
        return run(a)
    except KeyboardInterrupt:
        say("\nsetup stopped: you pressed Ctrl-C. Run `flash setup` again; the "
            "download resumes where it stopped.", out=sys.stderr)
        return 130
    except Exception as e:                                   # noqa: BLE001
        if getattr(a, "verbose", False):
            raise
        say(f"setup stopped: {type(e).__name__}: {e}. Re-run with `flash setup "
            "--verbose` to see where it came from.", out=sys.stderr)
        return 1


def main(argv: list) -> int:                              # pragma: no cover
    ap = argparse.ArgumentParser(prog="flash setup",
                                 description=__doc__.split("\n")[0])
    add_flags(ap)
    return dispatch(ap.parse_args(argv))
