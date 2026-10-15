"""R-7.19, OFFLINE: does `flash setup` survive the ways a first run actually fails?

`flash setup` is the command a stranger types first, and the product definition
promises that every failure on that path is a sentence naming the fix. A promise
like that cannot be gated by reading the source for the word "remedy": the only
proof is to put the command in front of each failure and read what it printed. So
this vector runs the real `flash setup` against:

- **no network** — the download host is a closed port on loopback, and the run
  must answer with the host it could not reach and the fact that the bytes it did
  get are kept.
- **no room on the volume** — `models.plan` is told the disk has 1 MiB free, and
  the refusal must come BEFORE a byte is requested, with the free number and
  `FLASH_GGUF_DIR` in it.
- **bytes that are not the pinned bytes** — twice: once mid-download (the fixture
  server flips the last byte, so `fetch` prints both hashes) and once as a weight
  that rotted in the cache after it was registered, which is the corrupt-cache arm
  step 3 exists for.
- **no model library at all** — a CHILD process with `mlx`, `mlx_lm` and
  `llama_cpp` blocked at the import hook, which is the shape of the Linux box this
  repo keeps promising to tell the truth to.
- **an unwritable cache directory, Ctrl-C mid-download, and an error no step
  named** — the three that a traceback would be easiest to hide in.

Every arm asserts the same three things about the output: the exit code is
non-zero, the sentence names the next command, and the string `Traceback` appears
nowhere. `--verbose` is the one arm that is ALLOWED a frame, and that is checked
too, because a first-run command that dumps a stack sends the user to an issue
tracker instead of telling them what to do.

NOTHING HERE SPENDS A WEIGHT. The real pins are 4.68 GB and 18.63 GB on a
connection capped at 50 GB/day, so the download arms drive the shipping
`models.fetch` against the same 1,052,676-byte fixture and `pinned()` cache
`benchmarks/models_pull_check.py` already uses — the byte counts, the hashing and
the free-space gate are the shipping code, the file is small. Step 4 is stubbed
for every arm that is about steps 1-3, and the two arms that are about step 4 run
it for real: one in a child with no backend (which must refuse), and one live turn
on whatever this box can load. The live arm is the same command the user types, so
its number is the number the user gets.

    python benchmarks/setup_check.py            # gates + mutants
    python benchmarks/setup_check.py --mutant   # mutants only
    python benchmarks/setup_check.py --mutant skip_pull_still_pulls
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from flash import backend, cli, doctor, models, setup          # noqa: E402
from models_pull_check import (SIZE, PAYLOAD, fake_tier,       # noqa: E402
                               pinned, serve)

PY = sys.executable
CHECKS: list[tuple[str, bool, str]] = []

#: The four verbs step 5 is allowed to promise. Anything else on that screen is
#: machinery, and the PRODUCT tuple in flash/cli.py is the one place that knows.
PROMISES = ("flash chat", "flash run", "flash verify", "flash ui")


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


def ns(*argv: str):
    return cli.build_parser().parse_args(["setup", *argv])


def go(*argv: str) -> tuple[int, str]:
    """`setup.dispatch` in this process, with BOTH streams collected.

    dispatch and not run, because the two lines a first run most needs to prove —
    Ctrl-C is a sentence, and an unnamed error is a sentence — are written there.
    The model turn is stubbed unconditionally: an arm about steps 1-3 has no
    business costing a weight load, and the arms that ARE about step 4 run the real
    command in a child (`arms_no_backend`, `arms_live`).
    """
    real = setup.prove
    setup.prove = lambda tier, route, out: (True, "READY", 1.25)
    buf, err = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(err):
            rc = setup.dispatch(ns(*argv))
    except Exception as e:                                    # noqa: BLE001
        # Nothing is allowed to escape a first-run command, and a mutant that
        # breaks that must FAIL A CHECK rather than crash this file: an exception
        # here is the very traceback the product definition forbids.
        rc = 99
        err.write(f"\nRAISED OUT OF dispatch: {type(e).__name__}: {e}")
    finally:
        setup.prove = real
    return rc, buf.getvalue() + err.getvalue()


# ---------------------------------------------------------------- the failure arms
def arms_shape(tmp: Path) -> None:
    """The five steps, as five steps, on a cache nobody has used."""
    cache = tmp / "shape"
    with pinned(cache, "range", "http://127.0.0.1:1"):
        rc, out = go("--skip-pull", "--backend", "llama.cpp")
        nums = [out.index(f"[{i}/5]") for i in range(1, 6) if f"[{i}/5]" in out]
        ck("`flash setup` prints FIVE numbered steps, in order, and a stranger can "
           "tell which errand failed — `[3/5]` is not the same errand as `[4/5]`, "
           "and a wall of unlabelled prose is what a traceback looks like to "
           "somebody who has never seen one",
           rc == 0 and nums == sorted(nums) and len(nums) == 5, f"rc={rc} {nums}")
        ck("step 1 is `flash doctor`'s own report VERBATIM, not a summary setup "
           "wrote for itself — this repo has twice shipped the same fact in two "
           "places with two answers",
           doctor.report(doctor.answers()) in out, out[:160])
        ck("step 5 names the four verbs the product is, and nothing that is "
           "machinery — the screen a user finishes on is the screen they act on",
           all(p in out for p in PROMISES) and "flash session" not in out,
           out[-320:])
        ck("and the rate it quotes is the one step 4 measured: seconds for the "
           "turn, not a tokens/s figure the run has no tokenizer count behind "
           "(a borrowed unit is the number this project keeps having to take back)",
           "measured: that turn took" in out and "tokens/s" not in out, out[-200:])
        ck("no Traceback on the whole happy path", "Traceback" not in out,
           out[-200:])


def arms_clean(tmp: Path) -> None:
    """A first run on a machine with nothing downloaded yet."""
    cache = tmp / "clean"
    with pinned(cache, "range", "http://127.0.0.1:1"):
        rc, out = go("--skip-pull", "--backend", "llama.cpp")
        ck("with `--skip-pull` NOTHING is fetched: the step-2 line says so, and the "
           "cache directory is still not even created — a flag that only hides a "
           "prompt is a flag that still spends the bytes",
           rc == 0 and "--skip-pull" in out and not cache.exists(),
           f"rc={rc} {out[:200]} exists={cache.exists()}")
        ck("step 3 does not claim a check it could not run: on a cache with no "
           "weight it says there is no hash to check, instead of printing a green "
           "line about a file that is not there",
           "no hash to check" in out and "verified" not in out.split("[4/5]")[0],
           out[:400])
        ck("and step 5 does not promise weights this machine does not have",
           "weights: tier" not in out, out[-260:])


def arms_no_network(tmp: Path) -> None:
    """The host is a closed port: what a plane, a DNS failure or a firewall looks
    like to this command."""
    cache = tmp / "offline"
    with pinned(cache, "range", "http://127.0.0.1:1"):
        rc, out = go("--yes", "--backend", "llama.cpp")
        ck("no network is ONE sentence naming the host it could not reach, it exits "
           "non-zero, and it says the bytes it already has are kept so the retry is "
           "the same command rather than a repair",
           rc == 1 and "cannot reach" in out and "127.0.0.1:1" in out
           and "resume" in out and "Traceback" not in out, f"rc={rc} {out[-220:]}")
        ck("and the failure leaves nothing behind — no weight, no manifest, no "
           "half-file the next run would have to be told about",
           not list(cache.glob("*")) if cache.exists() else True,
           str(sorted(p.name for p in cache.glob("*")))[:160])


def arms_full(tmp: Path) -> None:
    """The volume is too small for what remains."""
    cache = tmp / "full"
    real_plan = models.plan
    with pinned(cache, "range", "http://127.0.0.1:1"):
        try:
            models.plan = lambda t: dict(real_plan(t), free_bytes=1024 ** 2,
                                         remaining=SIZE)
            rc, out = go("--yes", "--backend", "llama.cpp")
        finally:
            models.plan = real_plan
        ck("a disk with no room is refused BEFORE the request is made, with the free "
           "space named as a number and `FLASH_GGUF_DIR` named as the way out — a "
           "download that dies at 90% of 4.68 GB on a metered line costs the whole "
           "day",
           rc == 2 and "FLASH_GGUF_DIR" in out and "free" in out
           and "Traceback" not in out, f"rc={rc} {out[-240:]}")
        ck("and it asked the host for nothing at all: the cache holds no `.part`",
           not list(cache.glob("*.part*")), str(sorted(cache.glob("*")))[:160])


def arms_unwritable(tmp: Path) -> None:
    """`FLASH_GGUF_DIR` points somewhere the user cannot write — the one filesystem
    error `models.fetch` cannot see, because its `mkdir` is before its first `try`.

    The cache is asked to live UNDER a regular file, and `pinned()` still points the
    tier at the fixture and the host at a closed port: belt and braces, because an
    arm that accidentally reaches the real pins is an arm that downloads 4.68 GB on
    a metered line.
    """
    blocker = tmp / "blocker"
    blocker.write_text("not a directory")
    cache = tmp / "blocker" / "cache"
    with pinned(cache, "range", "http://127.0.0.1:1"):
        rc, out = go("--yes", "--backend", "llama.cpp")
    ck("an unwritable cache directory is setup's own sentence, and it names the "
       "directory, the error and the flag — not the permission problem the user has "
       "to guess at",
       rc == 1 and str(cache) in out and "FLASH_GGUF_DIR" in out
       and "Traceback" not in out, f"rc={rc} {out[-260:]}")


def arms_bad_bytes(tmp: Path, base: str) -> None:
    """The host answers 200 with bytes that are not the pinned file."""
    cache = tmp / "bad"
    with pinned(cache, "bad", base):
        rc, out = go("--yes", "--backend", "llama.cpp")
        ck("bytes that arrive whole but wrong stop the run where they arrived, "
           "print BOTH hashes so a reader can see which half moved, and never reach "
           "step 4 — a weight that is not the pinned one is not a weight to prove a "
           "first answer on",
           rc == 1 and "SHA256 MISMATCH" in out and "expected" in out
           and "one real turn answers" not in out and "Traceback" not in out,
           f"rc={rc} {out[-240:]}")


def arms_corrupt(tmp: Path) -> None:
    """A registered weight that has since rotted on disk: the corrupt-cache arm."""
    cache = tmp / "rotted"
    t = fake_tier("range")
    with pinned(cache, "range", "http://127.0.0.1:1"):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / t.filename).write_bytes(PAYLOAD[:-1] + bytes([PAYLOAD[-1] ^ 0xFF]))
        models.write_manifest("fast", t.filename)
        rc, out = go("--skip-pull", "--backend", "llama.cpp")
        ck("a weight that no longer matches its pin stops at step 3 with the tier "
           "named and `flash models pull fast --force` as the next command, and "
           "refuses to run a turn on it — the manifest is a claim, the bytes are the "
           "fact, and a first answer generated off bytes that are not the pinned "
           "ones is an answer nobody can reproduce",
           rc == 1 and "--force" in out and "not verified" in out
           and "one real turn answers" not in out and "Traceback" not in out,
           f"rc={rc} {out[-260:]}")


def arms_reaches(tmp: Path) -> None:
    """Every flag the parser declares has to arrive at the download it renames."""
    seen: dict = {}
    cache = tmp / "kwargs"
    real = models.fetch
    with pinned(cache, "range", "http://127.0.0.1:1"):
        def spy(t, **kw):
            seen.update(kw, tier=t.tier)
            return 0
        models.fetch = spy
        try:
            rc, out = go("--yes", "--backend", "llama.cpp", "--tier", "fast")
        finally:
            models.fetch = real
        ck("`--yes` and the progress bar both reach `models.fetch` as the arguments "
           "their names mean — an inert `--yes` is a prompt CI cannot answer, and an "
           "inert bar is a multi-gigabyte download that looks like a hang",
           seen.get("yes") is True and seen.get("progress") is True
           and seen.get("tier") == "fast" and rc == 0, f"{seen} rc={rc}")


def arms_pull_then_hash(tmp: Path, base: str) -> None:
    """A pull that SUCCEEDED, with the bytes really on disk: step 3's turn.

    The fixture server, the shipping `models.fetch`, and a stubbed step 4 — so this
    arm is about nothing but the state between the download and the proof.
    """
    cache = tmp / "then-hash"
    with pinned(cache, "range", base):
        rc, out = go("--yes", "--backend", "llama.cpp")
        step3 = out.split("[3/5]")[1].split("[4/5]")[0]
        ck("step 3 RE-READS the disk after a pull that succeeded, so the fresh bytes "
           "are hashed like any other weight this command vouches for — the state "
           "remembered from before the download would print `no hash to check` about "
           "a file the run just wrote, and a first answer would rest on an "
           "unverified weight",
           rc == 0 and "no hash to check" not in out
           and "SHA256" in step3.upper() and str(SIZE) in step3.replace(",", ""),
           step3[:260])
        ck("and step 5 can now say the honest thing it could not say on the clean "
           "cache: which tier is where",
           "weights: tier" in out and str(cache) in out, out[-200:])
        ck("the file the run registered is the fixture, byte for byte — the gate "
           "is reading the same disk the user's weight lives on",
           (cache / models.TIERS["fast"].filename).read_bytes() == PAYLOAD, "")


def arms_ctrlc(tmp: Path) -> None:
    """Ctrl-C during the download: the bytes are kept and the exit says so."""
    cache = tmp / "ctrlc"
    real = models.fetch
    with pinned(cache, "range", "http://127.0.0.1:1"):
        models.fetch = lambda t, **kw: (_ for _ in ()).throw(KeyboardInterrupt())
        try:
            rc, out = go("--yes", "--backend", "llama.cpp")
        finally:
            models.fetch = real
        ck("the user's own Ctrl-C is rc 130 and ONE line that says the download "
           "resumes where it stopped — a first run interrupted by impatience must not "
           "look like a crash",
           rc == 130 and "Ctrl-C" in out and "resumes" in out
           and "Traceback" not in out, f"rc={rc} {out[-200:]}")


def arms_unexpected(tmp: Path) -> None:
    """An error no step named, and the only flag allowed to print a frame."""
    cache = tmp / "boom"
    real = setup.check_machine
    with pinned(cache, "range", "http://127.0.0.1:1"):
        try:
            def boom(_out):
                raise RuntimeError("a library said something nobody predicted")
            setup.check_machine = boom
            rc, out = go("--skip-pull", "--backend", "llama.cpp")
            ck("an error no step predicted is one sentence with the type, the "
               "message and the flag that would show where it came from — and no "
               "frame names, because a stack trace on a first run reads as \"the "
               "install is broken\" rather than \"run this\"",
               rc == 1 and "RuntimeError" in out and "--verbose" in out
               and "Traceback" not in out, f"rc={rc} {out[-200:]}")
            # `go` catches what escapes dispatch, because a mutant that lets an
            # exception out must fail a check rather than crash this file.
            rc2, out2 = go("--skip-pull", "--backend", "llama.cpp", "--verbose")
            ck("`--verbose` is the ONLY way to get the traceback, and it is an "
               "opt-in rather than a default — the person who needs the frame is the "
               "one who asked for it",
               rc2 == 99 and "RAISED OUT OF dispatch: RuntimeError" in out2,
               f"rc={rc2} {out2[-200:]}")
        finally:
            setup.check_machine = real


def arms_parser(tmp: Path) -> None:
    """The parser half: a stranger's typo must not become a silent no-op."""
    rc, out = child(["setup", "--tier", "fastt"], {})
    ck("`flash setup --tier fastt` is refused before anything happens, with the "
       "tier words that DO pinned — a typo must not quietly set up the other model",
       rc == 2 and "invalid choice" in out and "fast" in out, f"rc={rc} {out[-160:]}")
    rc, out = child(["setup", "--help"], {})
    ck("and `flash setup --help` says what the command does in the user's terms and "
       "shows one example, because the first command is the one read from help "
       "rather than from a spec",
       rc == 0 and "Five steps" in out and "example:" in out
       and "--skip-pull" in out, f"rc={rc} {out[:220]}")


def child(argv: list, env: dict | None = None) -> tuple[int, str]:
    e = dict(os.environ, PYTHONPATH=str(ROOT))
    e.update(env or {})
    p = subprocess.run([PY, "-m", "flash.cli", *argv], cwd=ROOT, env=e,
                       capture_output=True, text=True, timeout=1800,
                       stdin=subprocess.DEVNULL)
    return p.returncode, p.stdout + p.stderr


def arms_no_backend(tmp: Path) -> None:
    """The box with neither library: a CHILD with the import shim in front.

    In a subprocess, not in here, because `flash setup`'s no-backend arm is a claim
    about what a fresh install prints — and the only honest witness is an interpreter
    that genuinely cannot import `mlx`, `mlx_lm` or `llama_cpp`.
    """
    from backend_free_check import shim_env
    env = shim_env()
    env["PYTHONPATH"] = (env["PYTHONPATH"] + os.pathsep + str(ROOT)
                         if env.get("PYTHONPATH") else str(ROOT))
    env["FLASH_GGUF_DIR"] = str(tmp / "nobackend")
    rc, out = child(["setup", "--skip-pull", "--tier", "fast"], env)
    ck("a machine with no model library at all gets steps 1, 2 and 3 and then the "
       "step-4 refusal as sentences, with a `pip install` in them and exit non-zero "
       "— this is the exact box the README promises not to leave guessing",
       rc == 2 and "[1/5]" in out and "[3/5]" in out and "pip install" in out
       and "Traceback" not in out, f"rc={rc} {out[-320:]}")
    ck("and the refusal says what this machine CAN still do instead of the answer, "
       "so the command is not a dead end — `flash doctor` and the offline numbers "
       "survive the missing library",
       "flash doctor" in out or "offline" in out, out[-200:])


def arms_live(tmp: Path) -> None:
    """The command a user types, on this machine, with this machine's weights.

    No stub anywhere in this arm: it loads whatever `flash chat` would load and asks
    it for one word, which is the only way the proof and the promise are the same
    thing. Costs one model load, and that is the cost the first run has anyway.
    """
    can = any(p["available"] for p in backend.report())
    rc, out = child(["setup", "--skip-pull", "--tier", "fast"],
                    {"FLASH_GGUF_DIR": str(tmp / "live")})
    if can:
        ck("the live happy path: `flash setup --skip-pull` on a machine that CAN "
           "answer runs one real turn and prints the words it got and the seconds "
           "they took — this is the same command and the same weights the user "
           "types, so the number on this line is the number they get",
           rc == 0 and "one real turn answers" in out and "answered in" in out
           and "Traceback" not in out, f"rc={rc} {out[-300:]}")
        ck("and step 5 lists the verbs WITHOUT inventing weights: the `weights:` "
           "line appears exactly when the pinned tier is on disk, which is what "
           "`--skip-pull` on a cache of its own cannot be",
           ("weights: tier" in out) == (backend.manifest().get("fast") is not None),
           out[-200:])
    else:
        ck("this box answers nothing, so the live arm is the no-backend arm: setup "
           "must still exit non-zero with the remedy named, and not claim a turn it "
           "did not run",
           rc != 0 and "Traceback" not in out and "answered in" not in out,
           f"rc={rc} {out[-300:]}")


# ------------------------------------------------------------------ the gates
def run_gates(live: bool = True) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="setup-check-"))
    srv, base = serve()
    try:
        arms_shape(tmp)
        arms_clean(tmp)
        arms_no_network(tmp)
        arms_full(tmp)
        arms_unwritable(tmp)
        arms_bad_bytes(tmp, base)
        arms_corrupt(tmp)
        arms_reaches(tmp)
        arms_pull_then_hash(tmp, base)
        arms_ctrlc(tmp)
        arms_unexpected(tmp)
        arms_parser(tmp)
        arms_no_backend(tmp)
        # The one arm that loads a weight, and the one arm a mutation harness must
        # not: eight mutants re-running it would cost eight model loads to prove
        # nothing the first load did not already prove.
        if live:
            arms_live(tmp)
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ mutants
#: (label, [(path, old, new)]) — each restores a first run that LOOKS finished.
MUTANTS = {
    "skip_pull_still_pulls": ("`--skip-pull` hides the prompt but still spends the "
                              "bytes, so a flag that promises no download is a flag "
                              "that lies about it",
                              [("flash/setup.py",
                                "    fetching = gguf and not present and a.pull\n",
                                "    fetching = gguf and not present\n")]),
    "fresh_download_never_hashed": ("step 3 keeps the state it read BEFORE the pull, "
                                    "so a weight that just arrived is reported as "
                                    "`no hash to check` and never verified",
                                    [("flash/setup.py",
                                      "        present = not need_pull(a.tier)\n",
                                      "        pass\n")]),
    "bar_is_a_decoration": ("the download runs with `progress=False`, so a "
                            "multi-gigabyte pull prints nothing for minutes and "
                            "looks like a hung install",
                            [("flash/setup.py",
                              "        return models.fetch(t, yes=yes, "
                              "progress=True, out=out)\n",
                              "        return models.fetch(t, yes=yes, "
                              "progress=False, out=out)\n")]),
    "unverified_weight_still_runs": ("a weight whose hash does not match is still "
                                     "loaded for step 4, so the first answer comes "
                                     "from bytes that are not the pinned model",
                                     [("flash/setup.py",
                                       "    elif not verify(a.tier, out):\n",
                                       "    elif False:\n")]),
    "traceback_always": ("an error no step named re-raises whatever `--verbose` "
                         "says, which puts a stack frame on the screen of the one "
                         "reader who cannot use it",
                         [("flash/setup.py",
                           '        if getattr(a, "verbose", False):\n',
                           '        if True:\n')]),
    "weights_line_is_a_wish": ("step 5 promises weights whenever the user meant to "
                               "fetch them, whether or not anything is on disk",
                               [("flash/setup.py",
                                 "    capabilities(ans, a.tier, present, secs, out)\n",
                                 "    capabilities(ans, a.tier, True, secs, out)\n")]),
    "doctor_paraphrased": ("step 1 writes its own tidy summary of the machine "
                          "instead of printing doctor's report, so one fact has two "
                          "wordings and only one of them is gated",
                          [("flash/setup.py",
                            "    say(doctor.report(ans), out=out)\n",
                            "    say(\"this machine looks fine for flash\", out=out)\n")]),
    "invented_token_rate": ("the closing line quotes tokens/s from a word count the "
                            "run never tokenised, which is the borrowed unit this "
                            "project keeps having to take back",
                            [("flash/setup.py",
                              '        say(f"    measured: that turn took '
                              '{secs:.1f}s on this machine", out=out)\n',
                              '        say(f"    measured: {1000.0 / max(secs, '
                              '0.001):.1f} tokens/s on this machine", out=out)\n')]),
}


def _apply(patches):
    """Patch every file this mutant touches, and return what to write back."""
    saved: dict[Path, str] = {}
    for rel, old, new in patches:
        p = ROOT / rel
        text = p.read_text()
        if text.count(old) != 1:
            for rp, rt in saved.items():
                rp.write_text(rt)
            raise AssertionError(f"mutant anchor is not unique in {rel}")
        saved.setdefault(p, text)
        p.write_text(text.replace(old, new))
    return list(saved.items())


def _repatched() -> None:
    """Re-execute `flash/setup.py` inside THIS interpreter.

    These arms call `setup.dispatch` in-process, so writing the file to disk changes
    nothing the gates run until the module is re-executed — without this every
    mutant here silently passes, exactly the failure `models_pull_check.py` had to
    fix the same way.
    """
    import importlib
    importlib.reload(setup)


def mutate(one: str | None = None) -> int:
    #: Every file any mutant touches, captured before the first patch: a run that
    #: left half a mutant in the tree would certify code nobody tested.
    pristine = {(ROOT / rel): (ROOT / rel).read_bytes()
                for _, patches in MUTANTS.values() for rel, _, _ in patches}
    ran = failed = 0
    for name in ([one] if one else sorted(MUTANTS)):
        label, patches = MUTANTS[name]
        CHECKS.clear()
        saved = _apply(patches)
        try:
            _repatched()
            run_gates(live=False)
        finally:
            for p, text in saved:
                p.write_text(text)
            _repatched()
        left = [str(p.relative_to(ROOT)) for p, b in pristine.items()
                if p.read_bytes() != b]
        bad = [l for l, ok, _ in CHECKS if not ok]
        ran += 1
        print(f"  {'ok  ' if bad and not left else 'FAIL'} MUTATION: {label} -> "
              f"{len(bad)} check(s) fail" + (f"  {bad[0]}" if bad else "")
              + (f"  MUTANT LEFT IN TREE: {left}" if left else ""))
        failed += not bad or bool(left)
    print(f"\nsetup mutants: {ran - failed}/{ran} gates defeated by "
          "exactly their checks")
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
    print(f"\nsetup checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0
