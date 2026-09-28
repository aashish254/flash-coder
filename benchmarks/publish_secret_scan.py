"""R-7.14: is there anything in this repo that could be used against its author?

A download-and-run project publishes more than source. It publishes the run
records of its own development — traces carrying the exact text sent to a model,
job states, adapter configs, fitted vectors — and it publishes the whole git
history, not just the tip. So the question worth answering before a push is not
"does `grep` find a key today" but:

* does any file in the working tree carry one, including the ones written since
  the last commit and the ones `git ls-files` has never heard of — a push sends
  the tree, not HEAD;
* does any blob **ever committed** carry a credential shape (a secret scrubbed
  from the tree at HEAD still leaks from `git log -p`);
* does any filename in the tree or in history mean a key file (`.env`, `*.pem`,
  `id_rsa`);
* does the scan actually bite, or is it a list of patterns nothing matches —
  which is the shape of a green result that proves nothing.

Two answers exist and only one of them is a scan. `plant()` puts a synthetic
secret through every family and fails if any family passes it, and the file-count
arm fails rather than reporting a vacuous sweep if the tree or the object store
came back empty.

What is NOT claimed here, and the page says so: this is a pattern sweep, not a
proof of absence. It finds the shapes secrets have, and it names each match it
accepts with the reason it accepts it — an unlisted match exits 1. The reason is
printed beside the match, so a witness log argues for itself instead of asking a
reader to trust a count.

    python benchmarks/publish_secret_scan.py          # the sweep + the plant
    python benchmarks/publish_secret_scan.py --quiet  # counts only
"""
from __future__ import annotations

import gzip
import re
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))

# The marker list belongs to the portability gate; importing it rather than
# restating it is both the rule that gate enforces and the reason a second list
# is dangerous: two lists drift, and the one nobody re-reads is the one a path
# hides behind.
from portable_paths_check import HOST_PATHS                          # noqa: E402

#: Credential shapes, each wide enough to catch the real thing and named so a
#: match can be argued about. `assignment` is the noisy one and the reason the
#: allowlist below has to carry a reason per entry rather than a count. Its
#: trailing `\w*` is load-bearing: the classic key is named
#: `AWS_SECRET_ACCESS_KEY`, so the word that identifies it is followed by more
#: identifier, not by the separator, and without `\w*` the shape with the most
#: chance of being real is the one that does not match.
FAMILIES = {
    "provider_key": re.compile(
        rb"(?i)\b(sk|ghp|gho|ghu|ghs|ghr|github_pat|glpat|sgp|dckr_pat|amzn|"
        rb"AIza|ya29|AKIA|ASIA|xox[a-z])[-_]?[A-Za-z0-9]{12,}"),
    "npm_token": re.compile(rb"\bnpm_[A-Za-z0-9]{30,}"),
    "private_key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "bearer": re.compile(rb"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
    "jwt": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "basic_auth_url": re.compile(rb"[a-z][a-z0-9+.-]*://[^/\s:]{1,40}:"
                                 rb"[^/\s@]{4,64}@[a-zA-Z0-9.-]+"),
    "assignment": re.compile(
        rb"(?i)(password|passwd|pwd|secret|api_?key|access_?token|auth_?token|"
        rb"client_?secret|private_?token)\w*[\"' ]{0,3}[:=][\"' ]{0,3}"
        rb"[A-Za-z0-9._/+=-]{8,}"),
}

#: A secret is also a filename. Nothing here should ever be tracked.
BAD_NAMES = re.compile(r"(^|/)(\.env$|\.env\.[\w-]+$|id_rsa\w*$|"
                       r"[\w.-]*\.(pem|key|p12|pfx|keystore|jks)$)")

#: Every match this project accepts, with the sentence that makes it acceptable.
#: An entry that stops being true is an unlisted match, and the run exits 1.
#: The reasons are printed into this file's own witness, so they are worded
#: without the shape they excuse: a report that re-creates the match it is
#: excusing would make the next run of this scan fail on its own log.
ALLOWLIST = {
    ("assignment", "benchmarks/market_compare.py"):
        "the OpenAI key aider is handed for this run is the literal "
        "proxy-no-auth, so it talks to a local token-counting server that "
        "authenticates nobody. No service accepts that string and it is not a "
        "credential",
    ("provider_key", "site/package-lock.json"):
        "a base64 run inside an `sha512-…==` integrity hash, which is a content "
        "digest of a published npm tarball and not a token",
}

#: Identity, reported rather than gated: the author's own handle and host paths.
#: A public repo has to disclose that a human made it; the question is whether it
#: discloses more than the author chose, so the counts are printed and argued.
#: The path family is assembled from `HOST_PATHS` instead of spelling a prefix,
#: because this file is itself scanned by the gate whose rule is that only the
#: file declaring the markers may carry them — a secret sweep that tripped the
#: portability sweep would be the two gates disagreeing about the same tree.
IDENTITY = {
    "github-noreply-email": re.compile(
        rb"145881415\+aashish254@users\.noreply\.github\.com"),
    "personal-email": re.compile(
        rb"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)"
        rb"[A-Za-z0-9.-]+\.(?:com|net|org|io|dev|in)\b"),
    "host-path": re.compile(
        b"|".join(re.escape(p.encode()) + rb"[A-Za-z0-9._ -]*/"
                  for p in HOST_PATHS)),
}


def _sh(*args: str) -> str:
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True).stdout


def tracked() -> list[str]:
    return [p for p in _sh("git", "ls-files").splitlines() if p]


def untracked() -> list[str]:
    """Files a `git add -A` would bring in that are not yet tracked.

    The tip of history is not what a push sends — the working tree is. A secret
    sitting in a file written five minutes ago is invisible to `ls-files` and
    completely visible on GitHub, so the sweep covers the ignore-filtered
    working set as well as the committed one.
    """
    return [p for p in _sh("git", "ls-files", "--others", "--exclude-standard")
            .splitlines() if p]


def history_blobs() -> dict[str, str]:
    """sha -> path for every blob every commit ever contained.

    The tip is the easy half. A path or key scrubbed out of the tree is still
    one `git log -p` away, and pushing makes the whole reachability graph
    public, so the sweep has to cover it or say that it does not.

    Keyed by SHA and not by path, which is the whole point of the function: the
    same path at three revisions is three blobs, and a secret removed from the
    tree is exactly the revision a path-keyed map throws away. One
    `cat-file --batch-check` rather than one per object, because the object list
    is longer than a process spawn deserves.
    """
    lines = [l for l in _sh("git", "rev-list", "--objects", "--all").splitlines()
             if l.strip()]
    shas = [l.split(" ", 1)[0] for l in lines]
    kinds = {}
    batch = subprocess.run(["git", "cat-file", "--batch-check"], cwd=ROOT,
                           input="\n".join(shas), capture_output=True, text=True)
    for line in batch.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            kinds[parts[0]] = parts[1]
    out: dict[str, str] = {}
    for line in lines:
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if kinds.get(sha) == "blob":
            out[sha] = path
    return out


def _looks_binary(data: bytes) -> bool:
    """Whether this chunk's bytes need their padding taken out before a regex
    can read them — and why that is not the same thing as skipping them.

    `.npz` is a zip and `.gz` is a deflate stream, so a compressed member's
    bytes are unreadable to a regex by construction; each is decompressed and
    the *unpacked* bytes are swept. What is left is padding: a `.npy` header
    inside a committed `.npz` is a fixed-width struct with NUL filler, and a
    credential-shaped run survives that filler. Dropping the NULs and sweeping
    anyway is the difference between "the arrays were checked" and "the arrays
    were the one thing not checked", which is where a planted secret would sit.
    """
    return b"\0" in data[:8192]


def _expand(data: bytes) -> list[bytes]:
    """The bytes to sweep for one blob: itself, plus anything it contains."""
    if data[:2] == b"PK":
        import io
        import zipfile
        parts = [data]
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    parts.append(n.encode())
                    parts.append(z.read(n))
        except Exception:                        # noqa: BLE001 - swept raw below
            pass
        return parts
    if data[:2] == b"\x1f\x8b":
        try:
            return [data, gzip.decompress(data)]
        except OSError:
            pass
    if data[:2] == b"\x78\x9c" or data[:2] == b"\x78\xda":
        try:
            return [data, zlib.decompress(data)]
        except zlib.error:
            pass
    return [data]


def mask(fragment: bytes) -> str:
    """Six characters and a length. Enough to identify a match, never enough to
    replay it — a witness log that pastes a real secret is a second leak."""
    s = fragment.decode("utf-8", "replace")
    return f"{s[:6]}…({len(s)})" if len(s) > 6 else s


def sweep(blobs: list[tuple[str, bytes]], names: list[str] = ()) -> dict:
    hits, padded = [], []
    for name in names:
        if BAD_NAMES.search(name):
            hits.append(("bad_filename", name, name.encode()[:40]))
    for path, data in blobs:
        for chunk in _expand(data):
            if _looks_binary(chunk):
                padded.append(path)
                chunk = chunk.replace(b"\0", b"")
            for family, rx in FAMILIES.items():
                for m in rx.finditer(chunk):
                    hits.append((family, path, m.group(0)[:80]))
    seen: dict[tuple[str, str, bytes], int] = {}
    for fam, path, frag in hits:
        seen[(fam, path, frag)] = seen.get((fam, path, frag), 0) + 1
    return {"hits": seen, "padded": sorted(set(padded))}


def identity_counts(blobs: list[tuple[str, bytes]]) -> dict[str, int]:
    out = {}
    for label, rx in IDENTITY.items():
        out[label] = sum(len(rx.findall(data)) for _, data in blobs)
    return out


def plant() -> list[str]:
    """Every family has to bite on a synthetic secret. A sweep with no matches
    is only evidence if the patterns can match, and this is the half that says
    so; the planted strings are invented here, not copied from anywhere."""
    samples = {
        "provider_key": b"export TOKEN=ghp_" + b"A" * 36,
        "npm_token": b"//registry.npmjs.org/:_authToken=npm_" + b"B" * 36,
        # Written in two pieces on purpose. Every other sample here is a
        # concatenation, so no family's literal appears in this file's own
        # bytes; a whole PEM header does, and this file is in the tree a push
        # sends. The alternative was to let this scanner exempt itself by name,
        # which is the one allowlist that makes a secret gate say "whatever is
        # in the gate's own file is fine".
        "private_key": b"-----BEGIN OPENSSH PR" + b"IVATE KEY-----\nb3BlbnNzaC1r",
        "bearer": b"Authorization: Bearer " + b"C" * 24 + b".D" + b"E" * 12,
        "jwt": b"eyJ" + b"F" * 20 + b"." + b"G" * 20 + b".H",
        "basic_auth_url": b"postgres://admin:" + b"H" * 12 + b"@db.internal:5432",
        "assignment": b"AWS_SECRET_ACCESS_KEY: \"" + b"I" * 24 + b'"',
    }
    escaped = []
    for family, rx in FAMILIES.items():
        sample = samples.get(family, b"")
        if not rx.search(sample):
            escaped.append(family)
    if not BAD_NAMES.search("svc/.env") or not BAD_NAMES.search("keys/deploy.pem"):
        escaped.append("bad_filename")
    return escaped


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    tracked_files = tracked()
    new_files = untracked()
    hist = history_blobs()
    if not tracked_files or not hist:
        print("FAIL  the sweep found no files or no history to sweep: "
              f"{len(tracked_files)} tracked, {len(hist)} historical blobs. "
              "Without `.git` the history arm cannot run — clone the repo instead "
              "of using a ZIP download, or this is a scan of nothing.")
        return 1

    # What a push sends right now: the working tree, not what HEAD says the tree
    # is. A tracked file edited five minutes ago carries its new bytes here and
    # its old ones in `hist_blobs`, so both are swept and both are named.
    current: dict[str, bytes] = {}
    for rel in sorted(set(tracked_files) | set(new_files)):
        p = ROOT / rel
        if p.is_file():
            current[rel] = p.read_bytes()
    hist_blobs = []
    for sha, path in sorted(hist.items(), key=lambda kv: (kv[1], kv[0])):
        hist_blobs.append((path, subprocess.run(
            ["git", "cat-file", "blob", sha], cwd=ROOT, capture_output=True).stdout))

    at_tree = sweep(list(current.items()), list(current))
    at_hist = sweep(hist_blobs, list(current) + sorted(set(hist.values())))
    hits: dict[tuple[str, str, bytes], int] = {}
    for arm in (at_tree, at_hist):
        for key, n in arm["hits"].items():
            # max, not sum: an unchanged file is swept once from the tree and
            # once from HEAD's blob, and reporting two occurrences of a string
            # that exists once would be a count inflated by the scan's own shape.
            hits[key] = max(n, hits.get(key, 0))
    padded = sorted(set(at_tree["padded"]) | set(at_hist["padded"]))
    bad = sorted(k for k in hits if (k[0], k[1]) not in ALLOWLIST)
    unlisted = len(bad)

    if not quiet:
        print(f"publish sweep — {len(current)} files in the working tree "
              f"({len(new_files)} of them not yet tracked) and "
              f"{len(hist_blobs)} historical blobs, "
              f"{len(FAMILIES)} credential families + filename shapes")
        for (fam, path, frag), n in sorted(hits.items()):
            if (fam, path) in ALLOWLIST:
                print(f"  allowed  {fam:16s} {path}  {mask(frag)} x{n}")
                print(f"           because: {ALLOWLIST[(fam, path)]}")
            else:
                print(f"  UNLISTED {fam:16s} {path}  {mask(frag)} x{n}")
        print(f"\n  matches accepted by name: {len(hits) - unlisted}"
              f"   unlisted: {unlisted}")
        print(f"  blobs carrying NUL padding, swept with the padding removed: "
              f"{len(padded)}")
        for label, n in identity_counts(list(current.items()) + hist_blobs).items():
            print(f"  identity `{label}`: {n} occurrences")

    escapes = plant()
    if not quiet:
        print(f"  planted-secret proof: {len(FAMILIES) + 1 - len(escapes)}"
              f"/{len(FAMILIES) + 1} families bite"
              + (f", these do not: {escapes}" if escapes else ""))

    if unlisted:
        # Named even under `--quiet`: a gate that says "there is a secret"
        # without saying which file is a warning nobody can act on.
        for fam, path, frag in bad:
            print(f"  UNLISTED {fam:16s} {path}  {mask(frag)}")
        print("FAIL  a credential-shaped match is not in the allowlist, so this "
              "page cannot say the tree is clean.")
        return 1
    if escapes:
        print("FAIL  a pattern family matched nothing it was planted with — the "
              "sweep would pass on any content whatsoever.")
        return 1
    print(f"OK  no unlisted credential shape in the {len(current)} files a push "
          f"sends or in any of the {len(hist_blobs)} blobs ever committed, and "
          f"{len(FAMILIES) + 1 - len(escapes)}/{len(FAMILIES) + 1} families "
          f"proved they can bite.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
