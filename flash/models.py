"""R-7.18: `flash models pull` — the weights behind the second backend, pinned.

`--backend llama.cpp` needs a GGUF file on disk. Before this file there was no way
to get one that `flash` owned: `gguf_path()` could only resolve a path a user had
already downloaded by hand, so the second half of "works on any machine" was a
reader's errand — find the repo, find the quantisation, move 4.7 GB or 18.6 GB, and
hope the bytes are what the tutorial meant. Two of those steps are ones a program
does better, so it does them:

- **Which file.** `TIERS` pins one GGUF per tier — repo, filename, byte count and
  SHA256 — so `flash models pull fast` fetches the file the §22 tier table means,
  and `--small fast` runs exactly those bytes or refuses. The pin is the point:
  every number this project publishes cites its weights, and a model that quietly
  changed upstream between two runs turns a comparison into a coincidence.
- **Getting it.** A range request and a `.part` file, so a download that dies at
  12 GB of 18.6 GB resumes at 12 GB instead of starting the meter again. On a
  connection with a daily allowance — this author's is 50 GB/day, which the brain
  tier alone would eat a third of — a re-start is not slower, it is a day's quota
  thrown away.

What the rest of the file protects is the one claim a downloader can get wrong:
**a manifest entry is written only after the SHA256 matched.** `gguf_path()` trusts
the manifest, so the manifest is where trust is bought, and it is bought with a
hash, not with "the HTTP request returned 200". A download that finishes with the
wrong hash is deleted: the bytes cannot be repaired by resuming (there is nothing
to resume *from* when the whole file is suspect), and leaving them in the cache
means the next `--small fast` reads them.

Two deliberate limits, said out loud rather than papered over:

- `flash doctor` and `flash models list` read the state a verified pull wrote —
  one `stat` per file. Only `flash models verify` re-hashes, because hashing 18.6 GB
  every time somebody asks "what do I have" is not an answer, it is a minute of
  spinning. So `list` can say `pulled` (registered, right size) and only `verify`
  can say "these bytes are the pinned bytes".
- A file named directly (`--small /path/model.gguf`, or a bare filename sitting in
  the cache) is used as given. The pin governs what a TIER resolves to; a user who
  names their own bytes has outvoted the table, and this module does not delete
  what it was never asked to fetch.

MEASURED ON THIS BOX — 2026-10-15, and the numbers are the command's own. `flash
models pull fast` moved 4,683,074,336 bytes in 848.78 s at a steady 5.2-5.5 MB/s,
the streamed SHA256 equalled the pin, and the file landed in the cache registered.
`flash models verify fast` then re-hashed all 4.68 GB off disk in 1.59 s — which is
what separates this module's two words: `list` and `doctor` read a `stat`, only
`verify` reads bytes. A generation followed through `--backend llama.cpp` on those
weights (the measured run is in `LlamaCppBackend`'s docstring), so the pull is no
longer the end of the road it was written against. What is STILL unproven is the
CDN and the interrupted real download: `benchmarks/models_pull_check.py` drives the
client against local HTTP servers that speak `Range` (clean pull, resume, verified
hash, manifest), ignore `Range` (the fallback that restarts instead of appending
onto a prefix it cannot trust), answer 416 (the complete-but-unregistered case),
stop mid-stream (the `.part` that resumes), and serve bytes which do not match the
pin (the refusal that deletes) — that proves the download logic, and the real pull
above went through without needing any of the recovery arms. Which is also why
`_stream` never appends to a response that was not a 206: Hugging Face redirects
`resolve/main` to an LFS CDN, and whether that redirect preserves a `Range` header
is somebody else's implementation detail — a 200 answer is handled by starting
over, so the assumption cannot corrupt a file. And a pin is a claim about a file on
somebody else's server, so the one number that is not re-derived here — that the
pinned bytes are the same bytes tomorrow — stays OPEN and is what `verify` is for.

The same honesty is in `flash doctor`: the llama.cpp backend's profile still says
`verified: false`, and the GGUF line reports state, never a token decoded, because
neither command decodes a token.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from flash import backend

#: Where a GGUF is fetched from. `FLASH_HF_ENDPOINT` is the knob the Hugging Face
#: client libraries already expose, so a mirror or an internal proxy is a variable
#: rather than a code change — and it is how the gate points `pull` at a local
#: server without editing this file.
DEFAULT_HOST = "https://huggingface.co"

#: The streaming read size. 1 MiB is the granularity the progress line prints at
#: and the granularity a stop can land on, so the byte count of a `.part` is always
#: a whole number of these and a resume never re-reads a partial chunk.
CHUNK = 1 << 20


class PullError(RuntimeError):
    """The bytes on offer are not the bytes pinned, or did not all arrive.

    Separate from a connection error because the remedy differs: a `URLError` is
    retried as-is, while a stale pin or a changed file means the table in this
    module is the thing to fix, and re-running would just fail the same way.
    """


def host() -> str:
    return (os.environ.get("FLASH_HF_ENDPOINT") or DEFAULT_HOST).rstrip("/")


@dataclass(frozen=True)
class Tier:
    """One pinned GGUF. Every field is printed by `flash models list`, so a stale
    row is visible instead of silently run."""
    tier: str
    repo: str
    filename: str
    sha256: str
    size: int


# The §22 tiers in the quantisation llama.cpp users actually run: Q4_K_M is the
# size/quality knee, and both are single-file GGUFs, which is what `gguf_path()`
# and this module's one-file-per-tier manifest assume.
#
# MEASURED, not copied from a blog post: the byte counts and SHA256s below are the
# `size` and `oid` fields of
#   GET https://huggingface.co/api/models/<repo>/tree/main
# as read on 2026-10-14, one call per repo. A pin is a claim about a file on
# somebody else's server, so `benchmarks/models_pull_check.py` checks these rows
# against the tier table in `docs/models.md` that documents them, and
# `flash models verify` re-derives the hash from the bytes rather than trusting
# this line forever.
TIERS: dict[str, Tier] = {
    "fast": Tier("fast",
                 "bartowski/Qwen2.5-Coder-7B-Instruct-GGUF",
                 "Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
                 "1664fccab734674a50763490a8c6931b70e3f2f8ec10031b54806d30e5f956b6",
                 4_683_074_336),
    "brain": Tier("brain",
                  "lmstudio-community/Qwen3-Coder-30B-A3B-Instruct-GGUF",
                  "Qwen3-Coder-30B-A3B-Instruct-Q4_K_M.gguf",
                  "79ad15a5ee3caddc3f4ff0db33a14454a5a3eb503d7fa1c1e35feafc579de486",
                  18_632_186_176),
}


def tier(name: str) -> Tier:
    """The pinned row for a tier word, or a refusal that lists what IS pinned.

    A typo'd `flash models pull fastt` must not become a silent no-op, and must not
    fall back to a different tier.
    """
    t = TIERS.get(name)
    if t is None:
        raise ValueError(
            f"flash: no tier named `{name}` is pinned. Pinned: "
            + ", ".join(sorted(TIERS))
            + " — or point llama.cpp at your own file with "
              "`--small /path/to/model.gguf`.")
    return t


def url(t: Tier) -> str:
    return f"{host()}/{t.repo}/resolve/main/{t.filename}"


def cache() -> Path:
    """The GGUF directory, read at call time — so `FLASH_GGUF_DIR`, and a gate that
    reassigns `backend.GGUF_DIR`, both land where the next call will look."""
    return backend.GGUF_DIR


def part_path(t: Tier) -> Path:
    return cache() / (t.filename + ".part")


def _human(n: int) -> str:
    """Decimal GB, the way the pins and Hugging Face quote a file.

    A `GB` that means 1024**3 would print this cache's 4,683,074,336 bytes as
    4.36 GB while the tier table above says 4.68 GB, and the one number a
    metered connection is judged by is the one not to round sideways.
    """
    return f"{n / 1000 ** 3:.2f} GB"


def print_(out, line: str) -> None:
    out.write(line + "\n")
    out.flush()


# ---------------------------------------------------------------- state, cheap
def status() -> dict:
    """What the cache holds, without reading a byte of any weight.

    `complete` is a SIZE match: one `stat`, and it catches the two failures a
    download actually has — a truncated file and a resume that appended onto the
    wrong prefix. `verified` is the hash, and only `verify()` may speak it; a state
    reader that claimed "hash-checked" without hashing would be exactly the kind of
    lie this project has been hunting all along.
    """
    d = cache()
    man = backend.manifest()
    rows = []
    for name, t in TIERS.items():
        fname = man.get(name) or ""
        path = d / fname if fname else None
        on_disk = bool(path) and path.is_file()
        part = d / (t.filename + ".part")
        part_bytes = part.stat().st_size if part.is_file() else 0
        rows.append({
            "tier": name, "repo": t.repo, "file": t.filename,
            "sha256": t.sha256, "size": t.size,
            "pulled": bool(on_disk), "tracked": fname,
            "on_disk": on_disk, "bytes_on_disk": path.stat().st_size if on_disk else 0,
            "complete": on_disk and path.stat().st_size == t.size,
            "resumable": part_bytes > 0, "part_bytes": part_bytes,
        })
    tracked = {r["tracked"] for r in rows if r["tracked"]}
    loose = [] if not d.is_dir() else sorted(
        p.name for p in d.glob("*.gguf") if p.is_file() and p.name not in tracked)
    free = None
    probe = d
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        pass
    return {"dir": str(d), "tiers": rows, "host": host(),
            "bytes_on_disk": sum(r["bytes_on_disk"] for r in rows),
            "untracked": loose, "free_bytes": free,
            "pulled_gb": round(sum(r["bytes_on_disk"] for r in rows) / 1000 ** 3, 2)}


def plan(t: Tier) -> dict:
    """Everything a user needs before agreeing to a download, from `status()` and
    the pin — so the size warning and the pull cannot disagree about the number."""
    st = status()
    mine = [r for r in st["tiers"] if r["tier"] == t.tier][0]
    return {"tier": t.tier, "repo": t.repo, "file": t.filename,
            "sha256": t.sha256, "size": t.size, "url": url(t),
            "dir": st["dir"], "resumed": mine["part_bytes"],
            "remaining": t.size - mine["part_bytes"], "complete": mine["complete"],
            "free_bytes": st["free_bytes"]}


# ---------------------------------------------------------------- the pull
def _open(u: str, start: int):
    req = urllib.request.Request(u, headers={"User-Agent": "flash-coder/models"})
    if start:
        req.add_header("Range", f"bytes={start}-")
    return urllib.request.urlopen(req)


def _hash_prefix(path: Path, h, up_to: int) -> None:
    """Feed the bytes already on disk into the hash, so a resumed download is
    verified over the WHOLE file and not just the part fetched this run."""
    with open(path, "rb") as f:
        left = up_to
        while left > 0:
            b = f.read(min(CHUNK, left))
            if not b:
                break
            h.update(b)
            left -= len(b)


def _progress(done: int, total: int, t0: float) -> str:
    frac = min(1.0, done / max(1, total))
    rate = done / max(1e-6, time.time() - t0)
    return (f"\r  [{'#' * int(frac * 40):<40}] {frac * 100:5.1f}%  "
            f"{_human(done)} / {_human(total)}  {rate / 1000 ** 2:6.1f} MB/s")


def _stream(t: Tier, progress: bool = True) -> str:
    """Fetch what is missing and return the SHA256 of the whole `.part`.

    A host that ignores `Range` answers 200 with the entire body. Writing that body
    onto an existing prefix would corrupt the file while looking like a successful
    resume, so the response code decides the open mode: 206 appends, anything else
    truncates and re-hashes from byte 0.
    """
    part = part_path(t)
    start = part.stat().st_size if part.is_file() else 0
    resp = _open(url(t), start)
    code = getattr(resp, "status", None) or resp.getcode()
    length = resp.headers.get("Content-Length")
    if start and code != 206:
        sys.stderr.write(f"  note: the host answered {code}, not 206 — it ignored "
                         "the range, so this restarts from byte 0\n")
        start = 0
    if length is not None and int(length) != (t.size - start if code == 206
                                              else t.size):
        raise PullError(
            f"the host offers {int(length):,} bytes after byte {start} of "
            f"{t.filename}, which is not the {t.size:,} bytes flash pins. The file "
            "changed upstream, or the endpoint is serving something else; nothing "
            "was written, so the pin in flash/models.py is the thing to fix.")
    h = hashlib.sha256()
    if start:
        _hash_prefix(part, h, start)
    done = start
    t0 = time.time()
    with open(part, "ab" if start else "wb") as f:
        if progress:
            sys.stderr.write(_progress(done, t.size, t0))
        while True:
            b = resp.read(CHUNK)
            if not b:
                break
            f.write(b)
            h.update(b)
            done += len(b)
            if progress:
                sys.stderr.write(_progress(done, t.size, t0))
        f.flush()
        os.fsync(f.fileno())
    if progress:
        sys.stderr.write("\n")
    if done != t.size:
        # A short body is the interrupted case, not a failed one: the bytes are
        # good as far as they go, so the `.part` stays and the claim does not.
        raise PullError(f"the stream ended at {_human(done)} of {_human(t.size)}. "
                        "The partial file is kept — run the same command again to "
                        "resume from it.")
    return h.hexdigest()


def fetch(t: Tier, yes: bool = False, force: bool = False,
          progress: bool = True, out=None) -> int:
    """Download one tier into the cache, resumably, and register it only if the
    hash matched. Returns the exit code the CLI prints."""
    out = out or sys.stdout
    p = plan(t)
    d = cache()
    d.mkdir(parents=True, exist_ok=True)
    print_(out, f"tier       {t.tier}  ({t.repo})")
    print_(out, f"file       {t.filename}")
    print_(out, f"size       {_human(t.size)} ({t.size:,} bytes)"
                + (f" — resuming at {_human(p['resumed'])}, "
                   f"{_human(p['remaining'])} to go" if p["resumed"] else ""))
    print_(out, f"sha256     {t.sha256}")
    print_(out, f"source     {p['url']}"
                + ("" if host() == DEFAULT_HOST else "   (FLASH_HF_ENDPOINT)"))
    print_(out, f"dest       {d / t.filename}")
    if p["free_bytes"] is not None and p["remaining"] > p["free_bytes"]:
        print_(out, f"flash: {d} sits on a volume with {_human(p['free_bytes'])} "
                    f"free and this needs {_human(p['remaining'])}. Free the disk, "
                    "or move the cache with FLASH_GGUF_DIR.")
        return 2
    if force and p["resumed"]:
        part_path(t).unlink(missing_ok=True)
        p = dict(p, resumed=0, remaining=t.size)
        print_(out, "  --force: the partial file was discarded, starting at byte 0")
    if p["complete"] and not force:
        print_(out, f"  already pulled and registered: {d / t.filename}\n"
                    f"  to re-check the bytes: flash models verify {t.tier}")
        return 0
    if not yes:
        if not sys.stdin.isatty():
            print_(out, f"flash: this would download {_human(t.size)}, and nobody is "
                        "at the keyboard to agree to it. Re-run with --yes to "
                        "accept, or type the command in a terminal.")
            return 2
        if input(f"\npull {t.tier}: {_human(t.size)} into {d}? [y/N] ").lower() \
                .strip() not in ("y", "yes"):
            print_(out, "flash: not pulled. Nothing was downloaded.")
            return 1
    try:
        got = _stream(t, progress=progress)
    except PullError as e:
        print_(out, f"flash: {e}")
        return 1
    except urllib.error.HTTPError as e:
        kept = (part_path(t).stat().st_size if part_path(t).exists() else 0)
        if e.code == 416:
            print_(out, f"flash: the host has no bytes past {_human(kept)}, so what "
                        "is on disk is the whole file — and it is not registered, "
                        "because nothing proved its hash. `flash models verify "
                        f"{t.tier}` hashes it; `flash models pull {t.tier} --force` "
                        "re-fetches from byte 0.")
            return 1
        print_(out, f"flash: HTTP {e.code} from {url(t)}. The {_human(kept)} already "
                    "on disk is kept — run the same command again to resume.")
        return 1
    except (urllib.error.URLError, OSError) as e:
        print_(out, f"flash: cannot reach {host()}: {type(e).__name__}: {e}\n"
                    "  a partial download is kept; this command resumes from it.")
        return 1
    except KeyboardInterrupt:
        kept = (part_path(t).stat().st_size if part_path(t).exists() else 0)
        print_(out, f"\nflash: interrupted at {_human(kept)}. The bytes are kept — "
                    f"run `flash models pull {t.tier}` again to resume, or "
                    f"`--force` to start over.")
        return 130

    if got != t.sha256:
        part_path(t).unlink(missing_ok=True)
        print_(out, f"flash: SHA256 MISMATCH for {t.filename} — the bytes are not "
                    "the pinned file.\n"
                    f"  expected  {t.sha256}\n"
                    f"  got       {got}\n"
                    "  the download is deleted and the manifest was not touched: a "
                    "file that does not match its pin is not this tier. Re-run "
                    f"`flash models pull {t.tier}`; if it mismatches again, the pin "
                    "in flash/models.py is the thing that is stale.")
        return 1
    part_path(t).replace(d / t.filename)
    write_manifest(t.tier, t.filename)
    print_(out, f"  [{'#' * 40}] 100.0%  {_human(t.size)} / {_human(t.size)}")
    print_(out, f"  verified   sha256 {got[:16]}… == pinned")
    print_(out, f"  registered tier `{t.tier}` -> {t.filename} in "
                f"{d / backend.MANIFEST}")
    print_(out, f'  next       flash run "…" --test t.py --backend llama.cpp '
                f"--small {t.tier}")
    return 0


# ---------------------------------------------------------------- the manifest
def write_manifest(name: str, filename: str) -> dict:
    """Register one tier, atomically (temp file then `os.replace`).

    The SHA256 is deliberately NOT stored: the pin lives in `TIERS`, and a second
    copy of it in a cache file is a second thing that can be wrong — and the first
    thing a stale cache file would assert is that a bad weight is a good one.
    """
    import json
    d = cache()
    d.mkdir(parents=True, exist_ok=True)
    cur = backend.manifest()
    cur[name] = filename
    tmp = d / (backend.MANIFEST + ".tmp")
    tmp.write_text(json.dumps(dict(sorted(cur.items())), indent=1) + "\n")
    os.replace(tmp, d / backend.MANIFEST)
    return cur


def drop_tier(name: str) -> bool:
    """Unregister a tier, leaving its bytes alone.

    `verify` uses this when a registered file no longer matches its pin: the claim
    goes, the user's data stays, because those bytes may be a file somebody placed
    by hand and a tool that deletes what it never fetched is a tool nobody trusts.
    """
    import json
    cur = backend.manifest()
    if name not in cur:
        return False
    cur.pop(name)
    d = cache()
    tmp = d / (backend.MANIFEST + ".tmp")
    tmp.write_text(json.dumps(dict(sorted(cur.items())), indent=1) + "\n")
    os.replace(tmp, d / backend.MANIFEST)
    return True


def hash_file(path: Path, chunk: int = CHUNK) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def verify(name: str) -> tuple[bool, str]:
    """(ok, sentence). The only function here that reads a whole weight."""
    t = tier(name)
    fname = backend.manifest().get(name)
    path = cache() / fname if fname else None
    if path is None or not path.is_file():
        part = part_path(t)
        extra = (f" ({_human(part.stat().st_size)} of .part sits there, resumable)"
                 if part.is_file() else "")
        return False, (f"flash: tier `{name}` is not registered, so there is "
                       f"nothing to verify{extra}. `flash models pull {name}`.")
    got = hash_file(path)
    if got == t.sha256:
        return True, (f"{name}: {path.name} hashes to the pinned SHA256 "
                      f"({got[:16]}…), {path.stat().st_size:,} bytes.")
    drop_tier(name)
    return False, (f"flash: {path} hashes to {got}, not the pinned {t.sha256} — "
                   f"tier `{name}` was UNREGISTERED so `--small {name}` will refuse "
                   "rather than run these bytes. The file itself was left on disk; "
                   f"replace it with `flash models pull {name} --force`.")


# ---------------------------------------------------------------- the surface
def list_(out=None) -> int:
    out = out or sys.stdout
    st = status()
    print_(out, f"GGUF cache: {st['dir']}   (FLASH_GGUF_DIR moves it)")
    print_(out, f"source:     {st['host']}   (FLASH_HF_ENDPOINT changes it)")
    if st["free_bytes"] is not None:
        print_(out, f"volume:     {_human(st['free_bytes'])} free, "
                    f"{_human(st['bytes_on_disk'])} of GGUF here")
    for t in TIERS.values():
        r = [x for x in st["tiers"] if x["tier"] == t.tier][0]
        state = ("pulled" if r["complete"] else
                 "pulled, WRONG SIZE" if r["pulled"] else
                 f"partial {_human(r['part_bytes'])}" if r["resumable"] else "absent")
        print_(out, f"  {t.tier:<6} {state:<20} {_human(t.size):>9}  "
                    f"{t.filename}  sha256 {t.sha256[:12]}…")
        print_(out, f"         {t.repo}")
        print_(out, f"         run it: --backend llama.cpp --small {t.tier}"
                    f"   |   re-hash it: flash models verify {t.tier}")
    for name in st["untracked"]:
        print_(out, f"  in the cache but not a tier: {name} — reachable as "
                    f"`--small {name}`, never as a tier word")
    if not any(x["complete"] for x in st["tiers"]):
        print_(out, "\n  nothing pulled yet: `flash models pull fast` "
                    f"({_human(TIERS['fast'].size)}), or `--small /path/file.gguf` "
                    "for a file you already have.")
    return 0


def gguf_line(st: dict) -> str:
    """The one sentence `flash doctor` prints about this cache.

    Built here rather than in `flash/doctor.py` because the two must agree about
    what `pulled` means, and a report that paraphrases the module it is reporting
    on is how this repo has twice shipped a stale number.
    """
    got = [r["tier"] for r in st["tiers"] if r["complete"]]
    parts = [r["tier"] for r in st["tiers"] if r["resumable"] and not r["pulled"]]
    out = (f"GGUF cache {st['dir']}: {len(got)} of {len(st['tiers'])} tiers pulled "
           f"({', '.join(got) or 'none'}), {st['pulled_gb']} GB — this line checks "
           "sizes; only `flash models verify` checks the hash")
    if parts:
        out += f", partial: {', '.join(parts)}"
    if st["untracked"]:
        out += f", {len(st['untracked'])} untracked file(s)"
    return out


def add_flags(p: argparse.ArgumentParser) -> None:
    """`flash models` owns its verbs the way `flash doctor` owns its flags: one
    module, one parser, so the CLI cannot drift from it."""
    v = p.add_subparsers(dest="verb", required=True, metavar="VERB")
    pl = v.add_parser("pull", help="download a pinned tier's GGUF, resumably, and "
                                   "register it only if its SHA256 matches")
    pl.add_argument("tier", choices=sorted(TIERS), help="which pinned tier to fetch")
    pl.add_argument("--yes", action="store_true",
                    help="accept the size warning without asking (required when "
                         "stdin is not a terminal)")
    pl.add_argument("--force", action="store_true",
                    help="discard any partial download and start at byte 0")
    pl.add_argument("--quiet", action="store_true",
                    help="no per-chunk progress line (the plan and the verdict "
                         "still print)")
    st = v.add_parser("list", help="what the cache holds, without reading a byte")
    ver = v.add_parser("verify", help="re-hash a pulled tier — slow: it reads the "
                                       "whole file")
    for q in (st, ver):
        q.add_argument("tier", nargs="?", choices=sorted(TIERS),
                       help="one tier, or every pinned tier when omitted")


def dispatch(a: argparse.Namespace) -> int:
    if a.verb == "pull":
        return fetch(tier(a.tier), yes=a.yes, force=a.force,
                     progress=not a.quiet)
    if a.verb == "list":
        return list_()
    rc = 0
    for name in ([a.tier] if getattr(a, "tier", None) else sorted(TIERS)):
        ok, line = verify(name)
        print(line)
        rc = rc or (0 if ok else 1)
    return rc


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="flash models",
                                 description=__doc__.split("\n")[0])
    add_flags(ap)
    return dispatch(ap.parse_args(argv))


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
