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
  source tree present. This one is **unsatisfiable by design**: eleven of the 33
  vectors index *this tree* (the graph selftest builds its index over the source it
  is standing in, `portable_paths_check.py` scans it, `documented_commands_check.py`
  reads its documents), and R-7.10 made the rest refuse rather than pass quietly on
  a wheel install with no `benchmarks/` beside the package. A battery run that way
  would report a total from checks that could not have run.
* **useful** — the battery is green in the tree a person actually gets when they
  download this project, with that tree's own `flash` package and its own
  verification surface, installed into a throwaway venv that this checkout is not
  on. That is the property the README sells: nothing here is a number that only
  reproduces on the author's disk.

This script measures the second reading and prints the first one's refusal, so the
witness carries both shapes. Steps, each of which prints a provenance line:

    1  build an sdist from the checkout          -> /tmp/flash-r75-<pid>/dist
    2  unpack it                                 -> /tmp/flash-r75-<pid>/src
    3  venv + `pip install <the tarball>`        -> /tmp/flash-r75-<pid>/venv
    4  prove which `flash` a child imports, from inside the unpacked tree and from
       outside it, and require the outside one to be site-packages
    5  run the WHOLE `benchmarks/battery_reread.py` inside the unpacked tree
    6  run `flash selftest --all` from outside any tree, and record the refusal
    7  require the checkout's own path to appear nowhere in the witness

    python benchmarks/r75_sdist_battery_check.py            # ~18 min, one battery
    python benchmarks/r75_sdist_battery_check.py --keep     # leave /tmp alone

Exit codes: 0 the battery printed all 33 lines green and the provenance held; 1 a
line failed or the provenance did not; 2 setup failed (no build tool, no /tmp).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

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
LINE = "OK   "


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
    witness = RESULTS / f"r75_sdist_battery_{stamp}.log"
    log: list[str] = []

    def say(line: str = "") -> None:
        print(line)
        log.append(line)

    say("r75_sdist_battery: R-7.5 clause 2 — the whole §6 battery, in the download")
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

        # 5 — the whole battery, in the download, with the venv's interpreter.
        # `-u` because this is a 15-minute run: the child's stdout is a pipe, so
        # without it every line sits in an 8 KB buffer and a driver that promises
        # progress prints nothing until the battery is already over.
        say("")
        say("$ python benchmarks/battery_reread.py            # inside the tree")
        started = time.perf_counter()
        proc = subprocess.Popen([str(vpy), "-u", "benchmarks/battery_reread.py"],
                                cwd=str(tree), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=os.environ.copy())
        out_lines: list[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            out_lines.append(line)
            if line.startswith(("OK   ", "BAD", "checks ", "--quick", "matches ")):
                print(f"[{int(time.perf_counter() - started) // 60}m] {line}",
                      flush=True)
        rc = proc.wait()
        secs = time.perf_counter() - started
        log.extend(out_lines)
        ok_lines = sum(1 for l in out_lines if l.startswith(LINE))
        say("")
        say(f"provenance  battery rc={rc} in {int(secs // 60)} min "
            f"{int(secs % 60)} s, {ok_lines} OK lines printed")

        # 6 — the other shape, on the record: no tree, installed package only.
        rc2, out2 = sh([str(ve / "flash"), "selftest", "--all"], cwd=base,
                       timeout=120)
        say(f"provenance  `flash selftest --all` with no tree present: rc {rc2}")
        for line in [l for l in out2.splitlines() if l.strip()][:3]:
            say("            " + line)

        # 7 — the witness must not name this disk.
        body = "\n".join(log) + "\n"
        leaked = body.count(str(ROOT)) + body.count(str(Path.home()))
        say(f"provenance  host paths in this witness: {leaked}")
        if leaked:
            die("the witness carries a host path", rc=1)
        witness.write_text(body)
        print(f"\nr75_sdist_battery: wrote benchmarks/results/{witness.name}")
        print(f"r75_sdist_battery: {'ALL 33 LINES GREEN' if rc == 0 and ok_lines == 33 else 'see the BAD lines above'}"
              f" (battery rc {rc}, {ok_lines} OK lines)")
        return 0 if rc == 0 and ok_lines == 33 and rc2 == 2 else 1
    finally:
        if keep:
            print(f"r75_sdist_battery: leaving {base} in place (--keep)")
        else:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
