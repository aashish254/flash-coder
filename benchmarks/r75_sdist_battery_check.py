"""R-7.5's second clause, measured: every published number re-prints from the download.

The clause has two halves. The first — `pip install .` on a fresh clone produces a
working `flash` entry point — was measured on 2026-09-28 by
`benchmarks/r75_fresh_install_check.py`. The second — "the offline battery MUST be
green against the installed package rather than the checkout" — was not. The file
that claimed it had been, `battery_reread_r75_20260928.log`, is identical to the
checkout's own R-7.10c re-read except for a trailing `battery rc=0`, so it was made
in the checkout and labelled as if it had been made in a download. That is written
into `CHANGELOG.md` and `docs/portability.md`; this file is the run that should
have been there instead.

"Against the installed package rather than the checkout" needs a definition before
it can be measured, because the two readings differ:

* **literal** — every child process imports `flash` out of `site-packages`, with no
  source tree present. This one is **unsatisfiable by design**: eleven of the
  battery's own vectors index *this tree* (the graph selftest builds its index over
  the source it is standing in, `portable_paths_check.py` scans it,
  `documented_commands_check.py`
  reads its documents), and R-7.10 made the rest refuse rather than pass quietly on
  a wheel install with no `benchmarks/` beside the package. A battery run that way
  would report a total from checks that could not have run.
* **useful** — the battery is green in the tree a person actually gets when they
  download this project, with that tree's own `flash` package and its own
  verification surface, installed into a throwaway venv that this checkout is not
  on. That is the property the README sells: nothing here is a number that only
  reproduces on the author's disk.

This script measures the second reading and prints the first one's refusal, so the
witness carries both shapes. It measures them on **one tarball**, because an install
shape is not a property of the download: the same sdist answers differently with and
without an optional extra, and R-1.4's TypeScript vectors cannot run without it. A
driver that expected every line from a plain `pip install <tarball>` would call a
documented install shape a failure; one that excused the two refusals without proving
the second shape would hide a real break. Steps, each of which prints a line:

    1  build an sdist from the checkout          -> /tmp/flash-r75-<pid>/dist
    2  unpack it                                 -> /tmp/flash-r75-<pid>/src
    3  venv + `pip install <the tarball>`        -> /tmp/flash-r75-<pid>/venv
    4  prove which `flash` a child imports, from inside the unpacked tree and from
       outside it, and require the outside one to be site-packages
    5  shape A — run the WHOLE `benchmarks/battery_reread.py` inside the unpacked
       tree, and require the ONLY failures to be the grammar-gated lines, each of
       which must actually have refused (a gated line that passes on a plain
       install means the probe is not testing what it says)
    6  `pip install "<the tarball>[ts]"`, then prove from inside the tree that the
       grammar loads — through the download's own `flash`, not site-packages'
    7  shape B — run the WHOLE battery again, same tree, and require every line
       green AND the battery's own `matches SPEC §6 as written` verdict
    8  run `flash selftest --all` from outside any tree, and record the refusal
    9  require the checkout's own path to appear nowhere in the witness

    python benchmarks/r75_sdist_battery_check.py            # ~28 min, two batteries
    python benchmarks/r75_sdist_battery_check.py --keep     # leave /tmp alone

Exit codes: 0 both install shapes printed what this file predicts of them (the line
count is read from `battery_reread.BATTERY`, not written here) and the provenance
held; 1 a line failed outside its shape's expectation, or a refusal did not happen,
or the provenance did not; 2 setup failed (no build tool, no network for the extra,
no /tmp).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import battery_reread                                        # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "benchmarks" / "results"
PY = sys.executable
# `python -m build` is not in the project venv, so look for any 3.11 on PATH that
# can import it. The candidate is named by executable, never by a prefix written
# into this file: `benchmarks/` is inside the portability scan, and a literal
# install prefix here would be the exact defect
# `benchmarks/portable_paths_check.py` exists to fail.
BUILDERS = [PY, *[str(p) for name in ("python3.11", "python3")
                 if (p := shutil.which(name))]]
# The line count comes from the battery's own list rather than a literal written
# here: this gate's job is to require every published line to re-print, and a
# hardcoded number would turn the day a 34th vector ships into a run that fails
# for the wrong reason — or, worse, a run that is edited to match.
WANT_LINES = len(battery_reread.BATTERY)
# The battery lines that need R-1.4's optional extra to run at all. Named here, not
# guessed at from a failure: `pyproject.toml`'s `ts` extra is what installs the
# grammar, and `pip install <tarball>` installs no extras. Kept as a set the driver
# must see refuse on shape A and pass on shape B, so this list cannot quietly grow to
# cover a broken vector — a gated label that passes without the extra is a FAIL line
# in the witness.
GRAMMAR_GATED = frozenset({
    "benchmarks/ts_perception_check.py",
    "benchmarks/ts_patch_check.py",
})
LABELS = [entry[0] for entry in battery_reread.BATTERY]
# What the battery prints when its own totals agree with SPEC §6's pre-registered
# claim. Required on shape B and required ABSENT on shape A, because the two gated
# vectors really do contribute 91 checks and 26 mutants to that claim.
AGREES = "matches SPEC §6 as written"


def builder() -> str:
    for exe in BUILDERS:
        if not shutil.which(exe) and not Path(exe).is_file():
            continue
        r = subprocess.run([exe, "-c", "import build"], capture_output=True)
        if r.returncode == 0:
            return exe
    die("no interpreter here can import `build`, so no sdist can be made")


def die(msg: str, rc: int = 2) -> None:
    print(f"FATAL r75_sdist_battery: {msg}")
    raise SystemExit(rc)


def _same_tree(module_file: str, tree: Path) -> bool:
    """Is a resolved `__file__` inside this tree? `/tmp` is a symlink to
    `/private/tmp` on macOS and `__file__` comes back resolved, so both sides are
    compared resolved. The assertion itself does not move."""
    try:
        return Path(module_file).resolve().is_relative_to(tree.resolve())
    except OSError:
        return False


def sh(argv: list[str], cwd: Path | None = None,
       timeout: int | None = None) -> tuple[int, str]:
    """Run argv, return (rc, stdout+stderr). Never through a shell."""
    proc = subprocess.run(argv, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, timeout=timeout,
                          env=os.environ.copy())
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def main(argv: list[str]) -> int:
    keep = "--keep" in argv
    stamp = time.strftime("%Y%m%d")
    base = Path("/tmp") / f"flash-r75-{os.getpid()}"
    if base.exists():
        die(f"{base} already exists; refusing to share a work directory")
    base.mkdir(parents=True)
    witness = RESULTS / f"r75_sdist_battery_shapes_{stamp}.log"
    log: list[str] = []

    def say(line: str = "") -> None:
        print(line)
        log.append(line)

    say("r75_sdist_battery: R-7.5 clause 2 — the whole §6 battery, in the download,")
    say("                on both install shapes one tarball supports")
    say(f"provenance  interpreter: python {sys.version.split()[0]}, "
        f"{'the project venv' if '.venv' in PY else 'not the project venv'}")
    say(f"provenance  work directory: {base}")

    try:
        # 1 — build the sdist a stranger would download.
        dist = base / "dist"
        dist.mkdir()
        exe = builder()
        rc, out = sh([exe, "-m", "build", "--sdist", "--outdir", str(dist)],
                     cwd=ROOT, timeout=600)
        if rc != 0:
            say(out[-2000:])
            die("sdist build failed")
        tarballs = sorted(dist.glob("*.tar.gz"))
        if len(tarballs) != 1:
            die(f"expected one sdist, found {len(tarballs)}")
        sdist = tarballs[0]
        say(f"provenance  sdist: {sdist.name} ({sdist.stat().st_size // 1024} KB)")

        # 2 — unpack it. This tree, and nothing else from this disk, is the
        # checkout for everything that follows.
        src = base / "src"
        src.mkdir()
        with tarfile.open(sdist) as tf:
            tf.extractall(src)
        trees = [p for p in src.iterdir() if p.is_dir()]
        if len(trees) != 1:
            die(f"the sdist unpacked to {len(trees)} directories")
        tree = trees[0]
        say(f"provenance  unpacked tree: {tree.relative_to(base)}/")
        for missing in ("flash", "benchmarks", "docs", "SPEC.md"):
            if not (tree / missing).exists():
                die(f"the download has no {missing}, so the battery cannot run there")

        # 3 — a throwaway venv, fed the tarball rather than the tree.
        rc, out = sh([PY, "-m", "venv", str(base / "venv")], timeout=300)
        if rc != 0:
            say(out[-1500:])
            die("venv creation failed")
        vpy = base / "venv" / "bin" / "python"
        ve = base / "venv" / "bin"
        rc, out = sh([str(vpy), "-m", "pip", "install", "--quiet", "--upgrade",
                      "pip", "setuptools", "wheel"], timeout=900)
        if rc != 0:
            say(out[-1500:])
            die("pip could not bootstrap the venv")
        rc, out = sh([str(vpy), "-m", "pip", "install", "--quiet", str(sdist)],
                     timeout=900)
        if rc != 0:
            say(out[-1500:])
            die("pip install of the sdist failed")
        rc, out = sh([str(vpy), "-c", "import flash, sys; print(flash.__file__)"],
                     cwd=base)
        if rc != 0:
            die("the installed package does not import")
        outside = out.strip()
        if "site-packages" not in outside:
            die(f"from outside a tree, `import flash` resolved to {outside}, "
                "which is not site-packages — the venv is not holding the install")
        say("provenance  `import flash` outside the tree -> site-packages: yes")
        rc, out = sh([str(ve / "flash"), "--version"], cwd=base)
        say(f"provenance  the installed entry point, `flash --version`: "
            f"{out.strip() or '(nothing)'} [rc {rc}]")
        if rc != 0:
            die("`flash --version` failed on the installed entry point")

        # 4 — and inside the tree, where the battery will actually run, `flash` is
        # the download's copy. Printed, not assumed: this is the line whose absence
        # let a checkout print be filed as a fresh-clone witness.
        rc, out = sh([str(vpy), "-u", "-c",
                      "import flash; print(flash.__file__)"], cwd=tree)
        inside = out.strip()
        if not _same_tree(inside, tree):
            die(f"inside the unpacked sdist, `import flash` resolved to {inside}, "
                "which is not that tree — the run below would not be a download's")
        say("provenance  `import flash` inside the tree -> the unpacked sdist: yes")
        if str(ROOT) in inside + outside:
            die("the checkout path reached a resolved module; the download is not "
                "self-contained")

        # 5 — shape A: the whole battery, in the download, with the venv's
        # interpreter, on the install a stranger gets from one `pip install`.
        # `-u` because this is a 13-minute run: the child's stdout is a pipe, so
        # without it every line sits in an 8 KB buffer and a driver that promises
        # progress prints nothing until the battery is already over.
        def battery(what: str) -> tuple[int, list[str], list[str], int, list[str]]:
            say("")
            say(f"$ python benchmarks/battery_reread.py        # {what}")
            started = time.perf_counter()
            proc = subprocess.Popen([str(vpy), "-u", "benchmarks/battery_reread.py"],
                                    cwd=str(tree), stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    env=os.environ.copy())
            lines: list[str] = []
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip("\n")
                lines.append(line)
                if line.startswith(("OK   ", "BAD", "checks ", "--quick", "matches ")):
                    print(f"[{int(time.perf_counter() - started) // 60}m] {line}",
                          flush=True)
            rc = proc.wait()
            secs = int(time.perf_counter() - started)
            log.extend(lines)

            def labelled(prefix: str) -> list[str]:
                found = []
                for line in lines:
                    if not line.startswith(prefix):
                        continue
                    hit = [l for l in LABELS if l in line]
                    found.append(hit[0] if hit else line.split(None, 2)[1])
                return found

            return (rc, labelled("OK   "), labelled("BAD"), secs, lines)

        rc_a, ok_a, bad_a, secs_a, out_a = battery("shape A, plain install")
        say("")
        say(f"provenance  shape A (pip install {sdist.name}): battery rc={rc_a} in "
            f"{secs_a // 60} min {secs_a % 60} s, {len(ok_a)}/{WANT_LINES} lines green")
        unexpected = sorted(set(bad_a) - GRAMMAR_GATED)
        refused = sorted(set(bad_a) & GRAMMAR_GATED)
        silent = sorted(GRAMMAR_GATED - set(bad_a))
        for label in refused:
            say(f"refused     {label} — no TypeScript grammar in this venv, which is "
                "what a plain install of the sdist gives")
        if unexpected:
            say(f"FAIL        {len(unexpected)} line(s) failed that no install shape "
                f"excuses: {', '.join(unexpected)}")
        if silent:
            say(f"FAIL        {', '.join(silent)} printed no verdict at all")
        passed_gated = sorted(set(ok_a) & GRAMMAR_GATED)
        if passed_gated:
            say(f"FAIL        {', '.join(passed_gated)} ran green WITHOUT the extra, "
                "so its gate is not the grammar and this shape proves nothing")
        if AGREES in "\n".join(out_a):
            say("FAIL        shape A agreed with SPEC §6's claim while 2 of its "
                "vectors refused — the totals would be a fiction")

        # 6 — the extra a stranger installs deliberately, then proved loadable by
        # the download's own `flash` from inside the unpacked tree.
        rc, out = sh([str(vpy), "-m", "pip", "install", "--quiet",
                      f"{sdist}[ts]"], timeout=900)
        if rc != 0:
            say(out[-1500:])
            die("pip could not add the `ts` extra, so shape B was not measured",
                rc=2)
        rc, out = sh([str(vpy), "-u", "-c",
                      "import flash, flash.lang_ts as L; "
                      "print(L.available()[0], flash.__file__)"], cwd=tree)
        loaded, _, where = out.strip().partition(" ")
        if rc != 0 or loaded != "True" or not _same_tree(where, tree):
            say(f"provenance  grammar probe inside the tree: rc {rc}, "
                f"out {out.strip()[:200]}")
            die("the `ts` extra installed but the download's own flash.lang_ts still "
                "cannot load it", rc=1)
        say(f"provenance  shape B (pip install '{sdist.name}[ts]'): the download's "
            "own flash.lang_ts reports the grammar loaded")

        # 7 — shape B: the same battery, the same tree, now with the extra present.
        rc_b, ok_b, bad_b, secs_b, out_b = battery("shape B, with the ts extra")
        say("")
        say(f"provenance  shape B: battery rc={rc_b} in {secs_b // 60} min "
            f"{secs_b % 60} s, {len(ok_b)}/{WANT_LINES} lines green")
        if bad_b:
            say(f"FAIL        with every dependency installed these still failed: "
                f"{', '.join(sorted(bad_b))}")
        if AGREES not in "\n".join(out_b):
            say(f"FAIL        shape B did not print `{AGREES}`, so the download does "
                "not reproduce SPEC §6's totals")
        shapes_ok = (rc_a == 1 and not unexpected and not silent and not passed_gated
                     and AGREES not in "\n".join(out_a)
                     and rc_b == 0 and len(ok_b) == WANT_LINES and not bad_b
                     and AGREES in "\n".join(out_b))

        # 8 — the other shape, on the record: no tree, installed package only.
        rc2, out2 = sh([str(ve / "flash"), "selftest", "--all"], cwd=base,
                       timeout=120)
        say(f"provenance  `flash selftest --all` with no tree present: rc {rc2}")
        for line in [l for l in out2.splitlines() if l.strip()][:3]:
            say("            " + line)

        # 9 — the witness must not name this disk.
        body = "\n".join(log) + "\n"
        leaked = body.count(str(ROOT)) + body.count(str(Path.home()))
        say(f"provenance  host paths in this witness: {leaked}")
        if leaked:
            die("the witness carries a host path", rc=1)
        witness.write_text(body)
        print(f"\nr75_sdist_battery: wrote benchmarks/results/{witness.name}")
        print(f"r75_sdist_battery: shape A {len(ok_a)}/{WANT_LINES} lines green with "
              f"{len(refused)} grammar refusals named; shape B "
              f"{len(ok_b)}/{WANT_LINES} green"
              + (" — both shapes match this file's prediction"
                 if shapes_ok else " — SEE THE FAIL LINES ABOVE"))
        return 0 if shapes_ok and rc2 == 2 else 1
    finally:
        if keep:
            print(f"r75_sdist_battery: leaving {base} in place (--keep)")
        else:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
