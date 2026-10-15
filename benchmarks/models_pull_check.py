"""R-7.18, OFFLINE: does `flash models pull` earn the trust the manifest buys?

`gguf_path()` (flash/backend.py) resolves `--small fast` by reading a JSON file in
the cache. That file is the whole of llama.cpp's safety story: once it says a tier
is `Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf`, the loop loads those bytes and the run's
numbers are that model's. So the question this vector asks is not "can Python
download a file" but the four ways a downloader can be wrong and still print
success:

- **Does the file on disk match the file that was pinned?** A 200 is not a hash.
  So every arm here compares SHA256, and the pin is what the manifest entry is
  conditional on — wrong bytes must never leave a registered tier behind.
- **Is a resumed download the same program as a fresh one?** A resume that appends
  the missing bytes but hashes only what it fetched this run reports a corrupt file
  as verified. That is the single most likely bug in this feature, so it is tested
  by writing half a file, pulling, and requiring the *whole* hash.
- **What happens when the host does not behave?** Four shapes are answered here:
  a server that ignores `Range` (append would corrupt, so it restarts), a server
  that has no bytes past the prefix (416 — the file is complete but unproven), a
  server whose file is a different length than the pin (the pin is stale, and
  nothing is written), and a stream interrupted halfway (the `.part` that resumes).
- **Is the state `flash doctor` prints the state `flash models` measured?** The
  report line is compared VERBATIM against `models.gguf_line(models.status())`, and
  the generation verdict is asked about a synthetic box that has llama.cpp and no
  MLX — the box this whole phase exists for, and the one a report that probes only
  MLX would tell it that it cannot generate.

NOTHING HERE TOUCHES THE NETWORK, and no real weight is involved: the pins in
`flash/models.py` are 4.68 GB and 18.63 GB, this author's connection is capped at
50 GB/day, and a test that fetched either would be a bill, not a proof. The arms
drive the real `models.fetch`/`_stream` against `http.server` threads in this
process, serving a 1,052,676-byte fixture with a `Tier` patched to the fixture's
length and hash, so the byte counts, the range arithmetic and the hashing are the
shipping code and the file being fetched is small. The parity check at the end is
what keeps the REAL pins honest: it re-reads `flash/models.py`'s table against the
tier table in `docs/models.md`, so a number that only exists in one of them fails.

Which is also why this file does not claim a llama.cpp run. The gate proves the
downloader; the arithmetic inside llama.cpp and the quality of the quantisation are
still measured nowhere in this repo, and `flash/models.py`'s docstring says so.

    python benchmarks/models_pull_check.py            # gates + mutants
    python benchmarks/models_pull_check.py --mutant   # mutants only
    python benchmarks/models_pull_check.py --mutant prefix_bytes_not_hashed
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import backend, cli, doctor, models                    # noqa: E402

PY = sys.executable
CHECKS: list[tuple[str, bool, str]] = []

#: A fixture bigger than `models.CHUNK` (1 MiB), so a pull is at least two reads
#: and a progress loop is exercised rather than short-circuited.
PAYLOAD = (bytes((i * 37 + 11) % 256 for i in range(1 << 20))
           + bytes((i * 13 + 7) % 256 for i in range(4100)))
SHA = hashlib.sha256(PAYLOAD).hexdigest()
SIZE = len(PAYLOAD)


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


# ------------------------------------------------------------------ the servers
class _Handler(BaseHTTPRequestHandler):
    """One handler, four behaviours, chosen by the first path segment.

    `flash/models.py` builds `{host}/{repo}/resolve/main/{file}`, so the repo field
    of the fixture tier is the mode. Every request is recorded, because a claim
    about resuming is only a claim if the `Range` header was actually sent.
    """
    def log_message(self, *a):                                # pragma: no cover
        pass

    def do_GET(self):                                         # noqa: N802
        mode = self.path.strip("/").split("/")[0]
        self.server.requests.append((mode, self.headers.get("Range")))
        body = PAYLOAD
        if mode == "bad":
            body = PAYLOAD[:-1] + bytes([PAYLOAD[-1] ^ 0xFF])
        start = 0
        rng = self.headers.get("Range")
        if rng and mode == "range":
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(body):
                self.send_response(416)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range",
                             f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(body) - start))
            self.end_headers()
            self.wfile.write(body[start:])
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    """Silences only the broken-pipe noise.

    The interrupted arm closes the socket mid-body ON PURPOSE, and a traceback from
    the fixture server in the middle of a witness print reads like a bug in the code
    under test rather than a fact about the arm.
    """
    def handle_error(self, request, client_address):            # noqa: D102
        if not isinstance(sys.exc_info()[1],
                          (ConnectionResetError, BrokenPipeError)):
            super().handle_error(request, client_address)


def serve() -> tuple[ThreadingHTTPServer, str]:
    srv = _Server(("127.0.0.1", 0), _Handler)
    srv.requests = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def fake_tier(mode: str, size: int = SIZE, sha: str = SHA) -> models.Tier:
    """A `Tier` shaped like a pin but pointing at the fixture.

    The repo field carries the mode so one server can be four servers; `filename`
    keeps the real suffix so nothing about the path handling is special-cased.
    """
    return models.Tier("fast", mode, "fixture-q4_k_m.gguf", sha, size)


@contextlib.contextmanager
def pinned(cache: Path, mode: str, host: str, size: int = SIZE, sha: str = SHA):
    """Point the shipping module at a temp cache and the fixture server, and put it
    back. `TIERS` keeps BOTH tier words so `list` and the CLI's `choices` still see
    the two-tier table the docs describe."""
    old_dir, old_ti, old_host = backend.GGUF_DIR, models.TIERS, models.DEFAULT_HOST
    old_chunk = models.CHUNK
    backend.GGUF_DIR = cache
    models.TIERS = {"fast": fake_tier(mode, size, sha),
                    "brain": models.Tier("brain", mode, "brain-fixture.gguf",
                                         sha, SIZE)}
    models.DEFAULT_HOST = host
    os.environ["FLASH_HF_ENDPOINT"] = host
    try:
        yield
    finally:
        backend.GGUF_DIR = old_dir
        models.TIERS = old_ti
        models.DEFAULT_HOST = old_host
        models.CHUNK = old_chunk
        os.environ.pop("FLASH_HF_ENDPOINT", None)


def child(argv: list, env: dict | None = None, stdin=subprocess.DEVNULL):
    e = dict(os.environ, PYTHONPATH=str(ROOT))
    e.pop("FLASH_HF_ENDPOINT", None)
    e.update(env or {})
    p = subprocess.run([PY, "-m", "flash.cli", *argv], cwd=ROOT, env=e,
                       capture_output=True, text=True, timeout=300, stdin=stdin)
    return p.returncode, p.stdout + p.stderr


def pull(t: models.Tier, cache: Path, progress: bool = False,
         **kw) -> tuple[int, str]:
    """`models.fetch` with its output collected, so the printed plan is evidence.

    The bar is off by default and only the arm that asserts about it turns it on:
    it writes to this process's stderr, and a witness print that a progress line is
    interleaved with is a witness nobody can re-read.
    """
    buf = io.StringIO()
    rc = models.fetch(t, out=buf, progress=progress, **kw)
    return rc, buf.getvalue()


# ------------------------------------------------------------------ the gates
def run_gates() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="models-pull-"))
    srv, base = serve()
    try:
        arms_empty(tmp, base)
        arms_pull(tmp, srv, base)
        arms_resume(tmp, srv, base)
        arms_host(tmp, base)
        arms_verify(tmp, base)
        arms_cli(tmp, base)
        arms_doctor(tmp, base)
    finally:
        srv.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
    parity_checks()


def arms_empty(tmp: Path, base: str) -> None:
    cache = tmp / "empty"
    with pinned(cache, "range", base):
        buf = io.StringIO()
        rc, out = models.list_(out=buf), buf.getvalue()
        st = models.status()
        ck("`flash models list` on a cache nobody has used answers with BOTH tier "
           "words, each marked absent, and it names the directory and the source it "
           "would fetch from — a stranger's first question is what do I have and "
           "where would it come from",
           all(x in out for x in ("fast", "brain", "absent", str(cache), base))
           and rc == 0, f"rc={rc} {out[:200]}")
        ck("and the state object says the same thing without reading a byte of any "
           "weight: nothing complete, nothing registered, nothing to resume",
           [t["complete"] for t in st["tiers"]] == [False, False]
           and st["bytes_on_disk"] == 0 and st["untracked"] == [], str(st)[:220])
        ck("`flash doctor`'s GGUF line is `models.gguf_line` VERBATIM, not a "
           "paraphrase doctor wrote for itself — this repo has twice shipped a "
           "number that lived in two places",
           doctor.gguf_cache()["line"] == models.gguf_line(models.status())
           and "0 of 2 tiers pulled" in doctor.gguf_cache()["line"],
           doctor.gguf_cache()["line"][:160])
        ck("and the GGUF line says out loud that it checked sizes, not hashes, so "
           "a green `list` cannot be read as a verified weight",
           "sizes" in doctor.gguf_cache()["line"]
           and "flash models verify" in doctor.gguf_cache()["line"], "")

    # the size gate: nothing is downloaded before somebody agrees to it
    cache = tmp / "refuse"
    with pinned(cache, "range", base):
        rc, out = pull(fake_tier("range"), cache, yes=False)
        ck("a pull with nobody at the keyboard REFUSES, names the byte count it is "
           "about to spend, and exits non-zero — on a metered connection the number "
           "is the decision, and a tool that starts an 18 GB download while you "
           "read its help text is not one",
           rc == 2 and "--yes" in out and "GB" in out, f"rc={rc} {out[:240]}")
        ck("and the refusal wrote nothing: not the file, not the manifest, not a "
           "partial — the cache directory is empty",
           not (cache / "fixture-q4_k_m.gguf").exists()
           and not (cache / "fixture-q4_k_m.gguf.part").exists()
           and not list(cache.glob("*")), str(sorted(cache.glob("*")))[:160])

    # the free-disk gate, on a plan that says the volume is full
    cache = tmp / "full"
    real_plan = models.plan
    try:
        models.plan = lambda t: dict(real_plan(t), free_bytes=1024 ** 2,
                                     remaining=SIZE)
        rc, out = pull(fake_tier("range"), cache, yes=True)
        ck("and a volume too small for what remains is refused BEFORE the request "
           "is made, with the free space named and FLASH_GGUF_DIR offered as the "
           "way out — an interrupted download at 90% is a wasted one",
           rc == 2 and "FLASH_GGUF_DIR" in out and not list(cache.glob("*.gguf*")),
           f"rc={rc} {out[:200]}")
    finally:
        models.plan = real_plan


def arms_pull(tmp: Path, srv, base: str) -> None:
    cache = tmp / "clean"
    t = fake_tier("range")
    with pinned(cache, "range", base):
        rc, out = pull(t, cache, yes=True)
        dest = cache / t.filename
        ck("a clean pull exits 0, lands the file at the cache path it promised, and "
           "the bytes on disk are the fixture byte for byte — not `same length`, "
           "the same bytes",
           rc == 0 and dest.is_file() and dest.read_bytes() == PAYLOAD,
           f"rc={rc} {out[-200:]}")
        ck("the whole file is hashed and it is the PINNED hash the run prints, so a "
           "reader can tell the pin matched rather than the length matched",
           SHA in out or SHA[:16] in out, out[-240:])
        ck("and the manifest is the thing that makes it a TIER: `--small fast` "
           "resolves through it to exactly the file that was verified",
           json.loads((cache / backend.MANIFEST).read_text()) == {"fast":
                                                                  t.filename}
           and backend.gguf_path("fast") == str(dest),
           str(backend.manifest()))
        ck("the manifest stores the tier and the filename and NOT a second copy of "
           "the hash — a pin kept in two places is two claims, and the cache's copy "
           "would be the one that goes stale",
           "sha" not in (cache / backend.MANIFEST).read_text().lower(), "")
        ck("`flash models list` now calls the pulled tier pulled and the other tier "
           "still absent, with the size it verified — one row per tier, because a "
           "cache that half-answers is the state a user has to see",
           "  fast   pulled" in models_list_out(cache)
           and "  brain  absent" in models_list_out(cache)
           and f"{SIZE / 1000 ** 3:.2f} GB" in models_list_out(cache),
           models_list_out(cache)[:260])
        ck("and `flash doctor` counts it: 1 of 2 tiers pulled, in the cache it read",
           "1 of 2 tiers pulled" in doctor.gguf_cache()["line"],
           doctor.gguf_cache()["line"][:200])
        ck("a second pull of a complete, registered tier does not re-download: it "
           "says what is there and points at `verify` — re-fetching 18 GB because "
           "somebody re-typed the command is the opposite of what this file is for",
           arm_no_refetch(cache, srv, t), "")
        ck("while --force really does re-fetch the bytes from zero, with no range "
           "header on the wire",
           pull(t, cache, yes=True, force=True)[0] == 0
           and srv.requests[-1] == ("range", None), str(srv.requests[-1]))
        ck("a loose file in the cache that no tier names is reported as reachable BY "
           "NAME and never as a tier — so that `--small other.gguf` stays possible "
           "and `--small fast` cannot silently become it",
           arm_untracked(cache), "")

    # The bar itself, on a cache of its own so it is the only thing the arm proves.
    bar = tmp / "bar"
    with pinned(bar, "range", base):
        noisy = io.StringIO()
        with contextlib.redirect_stderr(noisy):
            rc, _ = pull(t, bar, yes=True, progress=True)
        ck("without `--quiet` the run prints a real progress line — percent, the "
           "bytes done against the pinned total, and a rate, on stderr so a piped "
           "`flash models pull` still parses its plan off stdout",
           rc == 0 and "%" in noisy.getvalue() and "MB/s" in noisy.getvalue()
           and models._human(SIZE) in noisy.getvalue(), noisy.getvalue()[:160])
        quiet = io.StringIO()
        with contextlib.redirect_stderr(quiet):
            rc, _ = pull(t, bar, yes=True, force=True, progress=False)
        ck("and `--quiet` prints NO bar — a progress line in a log file is precisely "
           "the thing that flag promises to take away, and `--quiet` reaching `_stream` "
           "is the difference between a flag and a decoration",
           rc == 0 and "MB/s" not in quiet.getvalue(), quiet.getvalue()[:160])


def models_list_out(cache: Path) -> str:
    buf = io.StringIO()
    models.list_(out=buf)
    return buf.getvalue()


def arm_untracked(cache: Path) -> bool:
    (cache / "loose.gguf").write_bytes(b"not a tier")
    try:
        out = models_list_out(cache)
        return "loose.gguf" in out and "--small loose.gguf" in out
    finally:
        (cache / "loose.gguf").unlink()


def arm_no_refetch(cache: Path, srv, t: models.Tier) -> bool:
    """A completed tier must not cost a second download, counted on the wire."""
    n = len(srv.requests)
    rc, out = pull(t, cache, yes=True)
    return rc == 0 and "already pulled" in out and "verify" in out \
        and len(srv.requests) == n


def arms_resume(tmp: Path, srv, base: str) -> None:
    """The half-file arms: this is where a downloader is most often wrong.

    A resume that appends the missing bytes and hashes only what it fetched this
    run produces a file that is corrupt and reports `verified`. Nothing else in
    this file catches that, which is why the fixture is built by writing the first
    half and pulling the second.
    """
    t = fake_tier("range")
    cache = tmp / "resume"
    half = 1 << 20
    with pinned(cache, "range", base):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / (t.filename + ".part")).write_bytes(PAYLOAD[:half])
        rc, out = pull(t, cache, yes=True)
        ck("a pull onto a `.part` that holds the first 1 MiB asks the host for "
           "`Range: bytes=1048576-` — the resume is on the wire, not only in the "
           "docstring, and the byte it asks for is the byte it stopped at",
           srv.requests[-1] == ("range", f"bytes={half}-"), str(srv.requests[-1]))
        ck("and the result is the whole file: the prefix was hashed BEFORE the new "
           "bytes were appended, so the SHA256 that registered the tier covers all "
           f"{SIZE:,} bytes rather than the {SIZE - half:,} it fetched",
           rc == 0 and (cache / t.filename).read_bytes() == PAYLOAD
           and "resuming" in out, f"rc={rc} tail={out[-200:]}")
        ck("a `.part` that is exactly the whole file, unregistered, is answered with "
           "the 416 the host gives and turned into the one sentence that is true: "
           "complete but UNPROVEN, so `verify` is the next command",
           arm_416(tmp, base, t), "")
        ck("and an interrupted stream keeps the bytes it wrote and exits 130, naming "
           "the same command as the way to carry on — a download that restarts from "
           "zero after a Ctrl-C on this connection costs a day's allowance",
           arm_interrupt(tmp, base), "")


def arm_416(tmp: Path, base: str, t: models.Tier) -> bool:
    cache = tmp / "at-eof"
    with pinned(cache, "range", base):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / (t.filename + ".part")).write_bytes(PAYLOAD)
        rc, out = pull(t, cache, yes=True)
        return (rc == 1 and "verify" in out and "--force" in out
                and not (cache / t.filename).exists()
                and not (cache / backend.MANIFEST).exists())


def arm_interrupt(tmp: Path, base: str) -> bool:
    """Kill the stream mid-read through the seam `fetch` actually uses."""
    cache = tmp / "interrupt"
    written = []

    class _Resp:
        headers = {"Content-Length": str(SIZE)}
        status = 200

        def __init__(self):
            self.n = 0

        def read(self, k):
            self.n += 1
            if self.n > 3:
                raise KeyboardInterrupt
            b = PAYLOAD[(self.n - 1) * k:self.n * k]
            written.append(len(b))
            return b

    real_open, real_chunk = models._open, models.CHUNK
    with pinned(cache, "range", base):
        models._open = lambda u, start: _Resp()
        models.CHUNK = 4096
        rc, out = pull(fake_tier("range"), cache, yes=True)
        kept = (cache / (fake_tier('range').filename + ".part"))
        held = kept.is_file() and kept.stat().st_size == sum(written)
        models._open = real_open
        models.CHUNK = real_chunk
        if not (rc == 130 and held and "resume" in out):
            return False
        # and the part it left must be a USABLE prefix, not just a file
        rc2, out2 = pull(fake_tier("range"), cache, yes=True)
        return rc2 == 0 and (cache / fake_tier("range").filename) \
            .read_bytes() == PAYLOAD


def arms_host(tmp: Path, base: str) -> None:
    """Four ways a host can fail to be the server the docstring assumed."""
    t = fake_tier("plain")
    cache = tmp / "no-range"
    half = 1 << 20
    with pinned(cache, "plain", base):
        cache.mkdir(parents=True, exist_ok=True)
        (cache / (t.filename + ".part")).write_bytes(PAYLOAD[:half])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc, out = pull(t, cache, yes=True)
        ck("a host that IGNORES the range and answers 200 with the whole body is "
           "detected by the status code and restarts from byte 0 — because appending "
           "a full body onto a prefix is how a resume silently produces a file "
           f"{SIZE + half:,} bytes long that the run then certifies",
           rc == 0 and (cache / t.filename).stat().st_size == SIZE
           and "not 206" in err.getvalue(),
           f"rc={rc} note={err.getvalue()[:140]}")

    cache = tmp / "bad-bytes"
    t = fake_tier("bad")
    with pinned(cache, "bad", base):
        rc, out = pull(t, cache, yes=True)
        ck("bytes that arrive whole but are NOT the pinned file are refused: the run "
           "prints both hashes, deletes what it fetched, and leaves the manifest "
           "alone — a wrong hash is not a warning, because `--small fast` reads "
           "whatever the manifest names",
           rc == 1 and "MISMATCH" in out and SHA in out
           and re.findall(r"got\s+([0-9a-f]{64})", out) != []
           and all(h != SHA for h in re.findall(r"got\s+([0-9a-f]{64})", out))
           and not list(cache.glob("*.gguf")) and not list(cache.glob("*.part"))
           and not (cache / backend.MANIFEST).exists(),
           f"rc={rc} {out[-220:]} dir={sorted(p.name for p in cache.iterdir())}")

    cache = tmp / "short-file"
    t = fake_tier("range", size=SIZE + 1)
    with pinned(cache, "range", base):
        rc, out = pull(t, cache, yes=True)
        ck("a host serving a DIFFERENT LENGTH than the pin is the pin being stale, "
           "said as such before a byte is written — flash does not download 4.68 GB "
           "to discover the upstream file is 1 byte shorter",
           rc == 1 and "flash/models.py" in out and not list(cache.glob("*.part*")),
           f"rc={rc} {out[-200:]}")

    cache = tmp / "gone"
    with pinned(cache, "range", "http://127.0.0.1:1"):
        rc, out = pull(fake_tier("range"), cache, yes=True)
        ck("an unreachable host is one sentence and an exit code, not a traceback, "
           "and it says the partial file survives — so the retry is the same command "
           "rather than a repair",
           rc == 1 and "Traceback" not in out and "cannot reach" in out,
           f"rc={rc} {out[-180:]}")


def arms_verify(tmp: Path, base: str) -> None:
    t = fake_tier("range")
    cache = tmp / "verify"
    with pinned(cache, "range", base):
        pull(t, cache, yes=True)
        ok, line = models.verify("fast")
        dest = cache / t.filename
        ck("`flash models verify` is the one verb that reads the whole file, and on a "
           "pulled tier it says so with the hash it computed",
           ok and SHA[:16] in line and dest.is_file(), line[:200])

        # a registered file whose bytes have since rotted
        real = dest.read_bytes()
        dest.write_bytes(real[:-1] + bytes([real[-1] ^ 0xFF]))
        ok, line = models.verify("fast")
        ck("and when the bytes no longer match, the CLAIM goes and the DATA stays: "
           "the tier is unregistered so `--small fast` refuses, while the file — "
           "which flash did not put there and is not owed an opinion about — is left "
           "on disk for a human to look at",
           not ok and "UNREGISTERED" in line
           and "fast" not in backend.manifest() and dest.is_file(),
           f"ok={ok} {line[:200]}")
        ck("and that refusal is the one a caller can act on: `gguf_path` now raises "
           "for `fast`, naming the command that would fix it, rather than loading "
           "the rotted weight the manifest used to name",
           arm_gguf_refuses(cache), "")
        dest.write_bytes(real)

        # a truncated file is visible in the cheap state read, without a hash
        short = tmp / "truncated"
        with pinned(short, "range", base):
            short.mkdir(parents=True, exist_ok=True)
            (short / t.filename).write_bytes(PAYLOAD[:4096])
            models.write_manifest("fast", t.filename)
            st = models.status()
            row = [x for x in st["tiers"] if x["tier"] == "fast"][0]
            ck("a registered file that is SHORTER than the pin is not `pulled`: the "
               "cheap `stat` catches the failure a hash-free state read is allowed "
               "to catch, and `list` prints it as the wrong size rather than as a "
               "weight ready to run",
               row["pulled"] and not row["complete"]
               and "WRONG SIZE" in models_list_out(short),
               f"row={row} list={models_list_out(short)[:160]}")
            ck("and `doctor` does not count it: the line reports 0 of 2 pulled even "
               "though the manifest names the file, because `pulled` means complete "
               "and not registered",
               "0 of 2 tiers pulled" in models.gguf_line(st),
               models.gguf_line(st)[:200])
        ck("an unregistered tier has nothing to verify, and the sentence names the "
           "command that would give it something",
           models.verify("brain")[0] is False
           and "flash models pull brain" in models.verify("brain")[1], "")


def arm_gguf_refuses(cache: Path) -> bool:
    try:
        backend.gguf_path("fast")
        return False
    except backend.NoBackend as e:
        return "models pull fast" in str(e) or "no GGUF behind" in str(e)


def arms_cli(tmp: Path, base: str) -> None:
    """The parser, the flags, and the real pins — as far as they can go offline."""
    rc, out = child(["models", "list"], {"FLASH_GGUF_DIR": str(tmp / "cli")})
    ck("`flash models list` runs through the real parser on the REAL pins and prints "
       "both sizes as the table in flash/models.py holds them — the numbers a reader "
       "is about to spend are not allowed to differ from the ones the code fetched",
       rc == 0 and "4.68 GB" in out and "18.63 GB" in out
       and "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf" in out, f"rc={rc} {out[:200]}")
    ck("and on a cache with no tiers the CLI still exits 0 with the pull hint: "
       "`list` is a question about state, not a failed operation",
       rc == 0 and "nothing pulled yet" in out and "flash models pull fast" in out,
       out[-200:])

    rc, out = child(["models", "pull", "fastt"])
    ck("an unpinned tier word is refused by argparse before anything happens, and it "
       "names the words that exist — a typo must not become a silent no-op or, "
       "worse, the other tier",
       rc == 2 and "invalid choice" in out and "fast" in out, f"rc={rc} {out[-160:]}")
    rc, out = child(["models", "pull", "fast"],
                    {"FLASH_GGUF_DIR": str(tmp / "cli2"),
                     "FLASH_HF_ENDPOINT": base})
    ck("`flash models pull fast` with no terminal on stdin refuses at the size gate "
       "and asks for --yes, so CI cannot spend a day's bandwidth by accident — and "
       "this is the REAL pin, so the number it printed is the number the manifest "
       "would have to match",
       rc == 2 and "--yes" in out and "4.68 GB" in out
       and not (tmp / "cli2" / "fixture-q4_k_m.gguf").exists(),
       f"rc={rc} {out[-200:]}")

    seen = {}
    real_fetch = models.fetch
    try:
        models.fetch = lambda t, **kw: seen.update(kw, tier=t.tier) or 0
        models.dispatch(cli.build_parser().parse_args(
            ["models", "pull", "fast", "--yes", "--force", "--quiet"]))
    finally:
        models.fetch = real_fetch
    ck("every flag the parser declares reaches `fetch` as the argument its name "
       "means — an inert `--force` is a lie about discarding bytes, and an inert "
       "`--quiet` is a progress bar in a log file",
       seen.get("yes") is True and seen.get("force") is True
       and seen.get("progress") is False and seen.get("tier") == "fast", str(seen))

    ck("and `models.tier` (the function behind the parser's choices) lists what IS "
       "pinned when handed a word that is not, so a refusal never falls back to a "
       "different model than the one asked for",
       arm_tier_typo(), "")

    # the module's own import hygiene: this file is the download half, and it must
    # stay loadable on a box with no backend at all
    src = (ROOT / "flash" / "models.py").read_text()
    imported = set()
    for node in __import__("ast").parse(src).body:
        if isinstance(node, __import__("ast").Import):
            imported |= {n.name.split(".")[0] for n in node.names}
        elif isinstance(node, __import__("ast").ImportFrom) and node.level == 0:
            imported.add(node.module.split(".")[0])
    ck("flash/models.py imports nothing at module scope but the standard library "
       "and the seam — so `flash doctor` and `flash models list` stay answerable on "
       "the Linux box where neither model library exists, which is the whole "
       "promise R-7.7 made and R-7.18 keeps",
       imported <= {"__future__", "argparse", "hashlib", "os", "shutil", "sys",
                    "time", "urllib", "dataclasses", "pathlib", "flash"},
       str(sorted(imported)))

    # The stronger claim, in the environment that actually lacks the backends: the
    # same interpreter-level block `backend_free_check` uses, so `mlx`, `mlx_lm`,
    # `mlx_vlm` and `llama_cpp` all raise here.
    from backend_free_check import shim_env
    env = shim_env()
    env["PYTHONPATH"] = (env["PYTHONPATH"] + os.pathsep + str(ROOT)
                        if env.get("PYTHONPATH") else str(ROOT))
    env["FLASH_GGUF_DIR"] = str(tmp / "imports")
    imp = subprocess.run([PY, "-c", "import flash.models, flash.backend; "
                                    "import flash.doctor; flash.doctor.answers()"],
                         cwd="/tmp", env=env, capture_output=True, text=True,
                         timeout=180)
    ck("and a CHILD imports it with NO backend at all — no mlx, no mlx_lm, no "
       "llama_cpp — from a different working directory, answers `flash doctor` from "
       "it, and creates no cache directory on the way",
       imp.returncode == 0 and not (tmp / "imports").exists()
       and "No module named" not in imp.stdout,
       f"rc={imp.returncode} {imp.stderr[-300:]}")


def arm_tier_typo() -> bool:
    try:
        models.tier("fastt")
        return False
    except ValueError as e:
        return "brain" in str(e) and "no tier named `fastt`" in str(e)


# ------------------------------------------------------------------ the report
def synth(can_generate: bool | None, has_backend: bool,
          profiles: list[dict] | None) -> dict:
    """A `doctor.answers()` for a box this Mac is not.

    `can_generate`/`profiles`/`gguf` are passed through as `None` for the old
    shape, because `benchmarks/backend_free_check.py` feeds `verdict` a synthetic
    install that predates the seam — a report that KeyErrors on that fixture is a
    report that cannot be gated on a Linux runner.
    """
    be: dict = {"mlx": None if not has_backend else "0.32.4",
                "mlx_lm": None if not has_backend else "0.32.0",
                "error": None if has_backend else "import mlx.core: ModuleNotFoundError",
                "has_backend": has_backend}
    if can_generate is not None:
        be["can_generate"] = can_generate
    if profiles is not None:
        be["profiles"] = profiles
    return {"version": "test",
            "shape": {"package_dir": "/site-packages/flash", "root": "/site-packages",
                      "has_benchmarks": False, "has_battery": False, "has_git": False,
                      "editable": False, "tasks": 0},
            "backend": be,
            "cache": {"dir": "hub", "present": False, "models": [], "matrix_have": [],
                      "matrix_missing": list(doctor.MATRIX)},
            "tools": {k: True for k in doctor.VECTOR_TOOLS},
            "machine": {"python": "3.11.15", "platform": "linux", "machine": "x86_64",
                        "os": "Linux", "may_load": "profile low-power, 4.4 GB",
                        "why": ""}}


def verdict_safe(a: dict) -> tuple[list, str]:
    """`doctor.verdict`, or the exception it raised — a mutant must fail a check,
    not crash the run and leave the vector with no print at all."""
    try:
        return doctor.verdict(a), ""
    except Exception as e:                                    # noqa: BLE001
        return [], f"{type(e).__name__}: {e}"


def row(rows: list, needle: str) -> tuple[bool | None, str]:
    """The (ok, text) of the first verdict line containing `needle`."""
    for _, ok, line in rows:
        if needle in line:
            return ok, line
    return None, ""


def arms_doctor(tmp: Path, base: str) -> None:
    """The report a Linux box reads, and the one this Mac reads.

    Both are the point of the phase: `flash doctor` is the first command a stranger
    types, and if its generation verdict probes only MLX it tells the exact machine
    this phase exists for that it can do nothing.
    """
    mlx_off = {"backend": "mlx", "available": False,
               "why": "no module named 'mlx_lm'",
               "supports": {"constrain": True, "probe": True}}
    gguf_on = {"backend": "llama.cpp", "available": True, "why": None,
               "supports": {"constrain": False, "probe": False}}

    rows, err = verdict_safe(synth(True, False, [mlx_off, gguf_on]))
    ok, line = row(rows, "generation ")
    ck("`flash doctor` on a box with llama.cpp and NO MLX says generation RUNS — the "
       "verdict is read from the seam's own availability, not from `import mlx.core`, "
       "which is the difference between telling a Linux user to install something and "
       "telling them they are out of luck",
       not err and ok is True and "runs here" in line, err or line)
    ok, line = row(rows, "every backend the seam knows")
    ck("and it names BOTH candidates with each one's own reason, so the reader can "
       "see which of the two to install rather than being told the box is short of "
       "something unnamed",
       ok is None and "llama.cpp: can run" in line
       and "mlx: cannot (no module named 'mlx_lm')" in line, line)

    rows, err = verdict_safe(synth(False, False, [mlx_off,
                                                  dict(gguf_on, available=False,
                                                       why="no module named 'llama_cpp'")]))
    ok, line = row(rows, "generation ")
    ck("a box with neither route open still gets a `no`, and the remedy it prints "
       "walks the GGUF path in order — install the extra, pull a tier, name the "
       "backend — because a refusal that stops at `no MLX` sends the reader to a "
       "page about a library their platform cannot use",
       not err and ok is False and "flash models pull fast" in line
       and "llama-cpp-python" in line and "--backend llama.cpp" in line, err or line)

    rows, err = verdict_safe(synth(None, False, None))
    ck("an answers() dict from BEFORE the seam — no `can_generate`, no `profiles`, "
       "no `gguf` — still produces a report instead of a KeyError, which is what "
       "lets `backend_free_check.py` gate this function on a machine with no "
       "backend to lose",
       not err and len(rows) > 5 and row(rows, "generation ")[0] is False, err)

    # The live machine: one sentence, two readers, byte-for-byte the same words.
    cache = tmp / "doctor"
    with pinned(cache, "range", base):
        rows, err = verdict_safe(doctor.answers())
        want = models.gguf_line(models.status())
        got = row(rows, "GGUF cache")[1]
        ck("the GGUF line `flash doctor` prints is `models.gguf_line(models.status())` "
           "VERBATIM — doctor does not paraphrase the module it reports on, which is "
           "how this repo has twice shipped a stale number",
           not err and got == want and want.startswith("GGUF cache"), f"{got!r}")
        ck("and on a cache nothing has been pulled into, that sentence says so in "
           "the same words `flash models list` uses — 0 of 2, not an empty line "
           "that reads as a section that was skipped",
           "0 of 2 tiers pulled" in got and str(cache) in got, got)

        cache.mkdir(parents=True, exist_ok=True)
        (cache / models.TIERS["fast"].filename).write_bytes(PAYLOAD)
        models.write_manifest("fast", models.TIERS["fast"].filename)
        rows = doctor.verdict(doctor.answers())
        got = row(rows, "GGUF cache")[1]
        st = models.status()
        ck("once one tier is pulled and registered, the same line counts it — and it "
           "counts it from the SIZE the pin declares, so the report and `list` cannot "
           "disagree about what `pulled` means",
           "1 of 2 tiers pulled (fast)" in got
           and st["tiers"][0]["complete"] and str(st["pulled_gb"]) in got, got)
        ck("the line says out loud that it checks sizes and not hashes, so a reader "
           "who has 18 GB of weights on disk is not told they are verified",
           "only `flash models verify` checks the hash" in got, got)


# ------------------------------------------------------------------ the pins
def parity_checks() -> None:
    """The two tables that describe the same files must say the same five things.

    `docs/models.md` is the page a reader chooses a quantisation from;
    `flash/models.py` is what the download checks against. This is the check that
    stops them drifting, and the reason both were written from one API call rather
    than one from each.
    """
    text = (ROOT / "docs" / "models.md").read_text(errors="ignore")
    for name, t in models.TIERS.items():
        ck(f"the §22 tier table in docs/models.md carries the SAME repo, file, byte "
           f"count and SHA256 as flash/models.py's pin for `{name}` — a document "
           "that disagrees with the code is the bug this project was built to catch",
           t.repo in text and t.filename in text
           and f"{t.size:,}" in text and t.sha256 in text,
           f"{t.repo} {t.size}")
    ck("and the table is labelled with where its numbers came from, because a pin "
       "copied from a web page without a date is a rumour: the API call and the day "
       "are both on the page",
       "api/models" in text and "2026-10-14" in text, "")
    ck("the pins themselves are the shape a verifier needs: 64 hex characters, a "
       "positive length, and a filename ending in .gguf so `status()`'s untracked "
       "scan and llama.cpp's loader both see the same kind of file",
       all(len(t.sha256) == 64 and int(t.size) > 0 and t.filename.endswith(".gguf")
           for t in models.TIERS.values())
       and set(models.TIERS) == {"fast", "brain"},
       str({k: (v.filename, v.size) for k, v in models.TIERS.items()})[:200])
    py = (ROOT / "pyproject.toml").read_text(errors="ignore")
    ck("llama-cpp-python is an EXTRA and not a dependency, so `pip install .` on a "
       "Mac does not go and compile a C++ tree nobody asked for — and it is not in "
       "the `dev` extra either, which would put a source build on every CI job",
       'local = [' in py and "llama-cpp-python" in py
       and "llama-cpp-python" not in py.split("dev = [")[1].split("]")[0],
       py[py.find("local = ["):py.find("local = [") + 90] if "local = [" in py
       else "no [local] extra")


# ------------------------------------------------------------------ mutants
#: (label, [(path, old, new)]) — each restores a downloader that looks finished.
MUTANTS = {
    "prefix_bytes_not_hashed": ("a resume hashes only the bytes it fetched, so a "
                                ".part that was wrong registers as verified",
                                [("flash/models.py",
                                  "        _hash_prefix(part, h, start)\n",
                                  "        h.update(b\"\")\n")]),
    "append_whatever_returns": ("the response code is ignored and a full body is "
                                "appended onto the existing prefix, producing a "
                                "longer file that the run then certifies",
                                [("flash/models.py",
                                  '    with open(part, "ab" if start else "wb") as f:\n',
                                  '    with open(part, "ab") as f:\n')]),
    "register_before_hash": ("the manifest is written before the comparison, so a "
                             "mismatched download is already a tier by the time the "
                             "run notices",
                             [("flash/models.py",
                               "    if got != t.sha256:\n",
                               "    write_manifest(t.tier, t.filename)\n"
                               "    if got != t.sha256:\n")]),
    "bad_bytes_kept": ("a mismatch leaves its bytes in the cache, where the next "
                       "`--small fast` finds a file that is not the pinned model",
                       [("flash/models.py",
                         "        part_path(t).unlink(missing_ok=True)\n"
                         "        print_(out, f\"flash: SHA256 MISMATCH",
                         "        print_(out, f\"flash: SHA256 MISMATCH")]),
    "size_is_truth": ("`pulled` means registered, so a truncated weight reads as "
                      "ready to run in `list` and in `doctor`",
                      [("flash/models.py",
                        '            "complete": on_disk and path.stat().st_size == '
                        't.size,\n',
                        '            "complete": on_disk,\n')]),
    "verify_keeps_the_claim": ("a rotted file stays registered, because unregistering "
                               "it was the part that cost a disk write",
                               [("flash/models.py",
                                 "    drop_tier(name)\n",
                                 "    pass\n")]),
    "no_size_gate": ("the confirmation is skipped, so an 18 GB download starts from a "
                     "typo in a terminal nobody is watching",
                     [("flash/models.py",
                       '    if not yes:\n',
                       '    if False:\n')]),
    "doctor_asks_only_mlx": ("`flash doctor`'s generation verdict reads MLX alone, so "
                             "the Linux box with llama.cpp installed is told it "
                             "cannot generate",
                             [("flash/doctor.py",
                               '    can = be.get("can_generate", be["has_backend"])\n',
                               '    can = be["has_backend"]\n')]),
    "doctor_paraphrases_the_cache": ("the GGUF line is rewritten inside doctor "
                                     "instead of read from models, which is how one "
                                     "fact ends up with two answers",
                                     [("flash/doctor.py",
                                       '    st["line"] = models.gguf_line(st)\n',
                                       '    st["line"] = "GGUF cache: nothing to '
                                       'report"\n')]),
}


def _apply(patches):
    """Patch every file this mutant touches, and return what to write back.

    Keyed by PATH, first text wins: a mutant whose second patch is on a file its
    first patch already rewrote reads that file AFTER the mutation, so saving per
    patch would restore the mid-mutant copy and leave half the mutation in the tree.
    """
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
    """Put the patched file, or the restored one, into the RUNNING interpreter.

    These arms drive `models.fetch` and `doctor.verdict` in this process, because a
    downloader's range arithmetic and its hash comparison are only worth checking
    against the same objects the CLI calls. That also means writing `flash/models.py`
    to disk changes nothing the gates execute until the module is re-executed:
    without this, every mutant here silently passes 53/53 — which is exactly what
    this harness printed before it was pointed at the interpreter. `importlib.reload`
    re-runs the module in the object every other module already holds, so
    `doctor`'s `from flash import models` and this file's `models.fetch` both answer
    with the mutated code.
    """
    for mod in (models, doctor):
        importlib.reload(mod)


def mutate(one: str | None = None) -> int:
    #: Every file any mutant touches, captured before the first patch. This repo has
    #: twice shipped a checkout with half a mutant still in it, so a run now proves
    #: the tree came back: a FAIL here means the code you are reading is not the code
    #: the checks ran against.
    pristine = {(ROOT / rel): (ROOT / rel).read_bytes()
                for _, patches in MUTANTS.values() for rel, _, _ in patches}
    ran = failed = 0
    for name in ([one] if one else sorted(MUTANTS)):
        label, patches = MUTANTS[name]
        CHECKS.clear()
        saved = _apply(patches)
        try:
            _repatched()
            run_gates()
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
    print(f"\nmodels-pull mutants: {ran - failed}/{ran} gates defeated by "
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
    print(f"\nmodels-pull checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
