#!/usr/bin/env python3.11
"""SPEC R-7.5 clause 1, measured against the install rather than the tree this file
stands in.

Why this file was rewritten. The version it replaced built an sdist, installed it into
a throwaway venv, and then ran its verification steps as `python -m flash.cli …` with no
`cwd`, so each child inherited this checkout as its working directory — and `python -m`
puts the cwd on `sys.path[0]`. Every one of those children imported the CHECKOUT's
`flash` and never touched the copy the venv had installed. What it printed — `flash
doctor` exits 0 against the installed package — was a measurement of the author's disk
wearing an install's label. The shadowing is reproducible, so it is a gate in this file
rather than a footnote: a driver that cannot see which copy answered cannot report what
an install does.

What a downloader actually gets, measured here in the two shapes they can make:

  from the tarball — `flash --version` answers (rc 0); `flash doctor` answers that the
      verification surface is not beside the package (rc 1) and names its remedy;
      `flash selftest --all` refuses (rc 2) naming the `site-packages` battery path it
      wanted. The generating half works, and the page says which half is missing.
  from a clone with an editable install — `flash doctor` exits 0 with the battery line
      on `yes`, and `flash selftest --all --quick harness lsp power` runs three vectors
      out of the clone rather than out of this checkout.

`benchmarks/r75_sdist_battery_check.py` is the other half of R-7.5: the whole §6 battery
run inside an unpacked sdist. This file is what a stranger's terminal does.

EXIT CODES
  0  every shape printed the answer the requirement says it must
  1  one of them did not, or a witness could not be written clean
  2  setup failed (no build tools, no git, no writable temp directory)

USAGE
    python benchmarks/r75_fresh_install_check.py
    python benchmarks/r75_fresh_install_check.py --keep
    python benchmarks/r75_fresh_install_check.py --witness benchmarks/results/x.log
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = str(Path.home())
CHECKS: list[tuple[str, bool, str]] = []
LINES: list[str] = []


def tool() -> str:
    """An interpreter that can build an sdist. The venv this script is run from is not
    guaranteed to have `build` — the dev extra is installed on the checkout, not inside
    every interpreter — so each candidate is asked rather than assumed, the same way
    `r75_sdist_battery_check.py` chooses one. Every child that is ASKED about the install
    is a console script of the throwaway venv; this interpreter only ever builds and
    creates venvs, so it cannot answer for the package under test."""
    candidates = [sys.executable, *(p for name in ("python3.11", "python3")
                                   if (p := shutil.which(name)))]
    for cand in candidates:
        if subprocess.run([cand, "-c", "import build"],
                          capture_output=True, text=True).returncode == 0:
            return cand
    return ""


def emit(line: str = "") -> None:
    LINES.append(line)
    print(line, flush=True)


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append((name, ok, detail))
    emit(f"{'OK  ' if ok else 'BAD '} {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    """Run a child. `cwd` has no default: a child that inherits this checkout's
    directory can import the package under test from it and answer for the wrong copy."""
    proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def answered_from(python: Path, cwd: Path) -> str:
    """Which `flash` does an interpreter resolve from this working directory?"""
    rc, out = run([str(python), "-c", "import flash; print(flash.__file__)"], cwd=cwd)
    return out.strip() if rc == 0 else ""


def under(path: str, directory: Path) -> bool:
    """Is this resolved `__file__` inside that directory? `/tmp` is a symlink to
    `/private/tmp` on macOS and `__file__` comes back resolved, so both sides are
    compared resolved."""
    try:
        return Path(path).resolve().is_relative_to(directory.resolve())
    except OSError:
        return False


def make_venv(tool: str, base: Path, name: str) -> Path | None:
    venv = base / name
    rc, out = run([tool, "-m", "venv", str(venv)], cwd=base, timeout=900)
    if rc != 0:
        emit(out[-1500:])
        return None
    return venv


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="leave the temp tree behind")
    ap.add_argument("--witness", type=Path, help="write this run's print here")
    args = ap.parse_args(argv)

    base = Path(tempfile.gettempdir()).resolve() / f"flash-r75c-{os.getpid()}"
    ask = base / "nowhere"
    ask.mkdir(parents=True, exist_ok=True)
    builder = tool()
    if not builder:
        emit("R-7.5 clause 1 — the two installs a stranger can make")
        emit("")
        emit("BAD  no interpreter here can `import build`, so no sdist can be made to "
             "install — `pip install -e .[dev]` ships it")
        return 2
    emit("R-7.5 clause 1 — the two installs a stranger can make")
    _, builder_version = run([builder, "-c", "import sys; print(sys.version.split()[0])"],
                             cwd=base)
    emit(f"interpreter: python {sys.version.split()[0]}, builder: "
         f"python {builder_version.strip()}")
    emit("the checkout is the source of the build only; it is never the answer")
    emit("")

    try:
        dist = base / "dist"
        dist.mkdir()
        rc, out = run([builder, "-m", "build", "--sdist", "--outdir", str(dist)],
                      cwd=ROOT, timeout=900)
        tarballs = sorted(dist.glob("*.tar.gz"))
        if rc != 0 or not tarballs:
            emit(out[-1500:])
            return 2
        tarball = tarballs[0]
        emit(f"sdist: {tarball.name} ({tarball.stat().st_size // 1024} KB)")

        # ---- shape 1: installed from the tarball, asked from outside every tree
        venv_a = make_venv(builder, base, "venv-a")
        if venv_a is None:
            return 2
        rc, out = run([str(venv_a / "bin" / "pip"), "install", "--quiet", str(tarball)],
                      cwd=base, timeout=1800)
        if rc != 0:
            emit(out[-1500:])
            return 2
        flash_a = venv_a / "bin" / "flash"

        resolved = answered_from(venv_a / "bin" / "python", ask)
        check("the child that answers is the installed copy",
              bool(resolved) and "site-packages" in resolved and not under(resolved, ROOT),
              "`import flash` from a bare directory -> site-packages")

        rc, out = run([str(flash_a), "--version"], cwd=ask)
        printed = out.strip()
        check("`flash --version` on a tarball install answers with the version",
              rc == 0 and printed.startswith("flash "), f"{printed!r} [rc {rc}]")

        rc, out = run([str(flash_a), "doctor"], cwd=ask)
        check("`flash doctor` on a tarball install names the missing surface, the remedy, "
              "and the copy it is describing",
              rc == 1 and "(installed copy)" in out
              and "verification surface beside the package" in out
              and "the offline battery CANNOT run from this install" in out,
              f"rc {rc}, two `no` answers, each with its own remedy")
        check("and it does not call itself an editable checkout",
              "(editable checkout)" not in out,
              "R-7.10b's claim re-measured from the install side rather than the "
              "package's")

        rc, out = run([str(flash_a), "selftest", "--all"], cwd=ask)
        check("`flash selftest --all` refuses rather than totalling checks that never "
              "ran",
              rc == 2 and "battery_reread.py" in out and "site-packages" in out,
              f"rc {rc}, refusing with the path it wanted")

        # ---- the shadowing, shown rather than asserted away
        tree = base / "tree"
        tree.mkdir()
        rc, _ = run(["tar", "xzf", str(tarball), "-C", str(tree)], cwd=base, timeout=300)
        if rc != 0:
            check("the sdist unpacks", False, "tar failed")
            return 2
        unpacked = tree / tarball.name[: -len(".tar.gz")]
        shadow = answered_from(venv_a / "bin" / "python", unpacked)
        rc, out = run([str(venv_a / "bin" / "python"), "-m", "flash.cli", "doctor"],
                      cwd=unpacked)
        check("a `-m` run from inside a tree answers from THAT tree, not from the "
              "install — the shape that produced the old driver's rc 0",
              under(shadow, unpacked) and rc == 0 and "(editable checkout)" in out,
              f"same interpreter, same install: rc {rc}, `import flash` -> "
              + ("the unpacked sdist" if under(shadow, unpacked) else "site-packages"))

        # ---- shape 2: a real clone, installed editable, asked from inside itself
        clone = base / "clone"
        rc, out = run(["git", "clone", "--quiet", str(ROOT), str(clone)], cwd=base,
                      timeout=900)
        if rc != 0:
            emit(out[-1500:])
            return 2
        venv_b = make_venv(builder, base, "venv-b")
        if venv_b is None:
            return 2
        rc, out = run([str(venv_b / "bin" / "pip"), "install", "--quiet", "-e", ".[dev]"],
                      cwd=clone, timeout=1800)
        if rc != 0:
            emit(out[-1500:])
            return 2
        flash_b = venv_b / "bin" / "flash"

        resolved_b = answered_from(venv_b / "bin" / "python", clone)
        check("the editable install resolves `flash` to the clone and not to this "
              "checkout",
              under(resolved_b, clone) and not under(resolved_b, ROOT),
              "`import flash` -> " + ("the clone" if under(resolved_b, clone) else "elsewhere"))

        rc, out = run([str(flash_b), "doctor"], cwd=clone)
        check("`flash doctor` on a cloned, editable install exits 0 and advertises the "
              "battery",
              rc == 0 and "(editable checkout)" in out
              and "the offline battery runs here" in out, f"rc {rc}")

        rc, out = run([str(flash_b), "selftest", "--all", "--quick",
                       "harness", "lsp", "power"], cwd=clone, timeout=1200)
        subset = sum(1 for line in out.splitlines() if line.startswith("OK   "))
        check("and the battery it advertises really runs from the clone",
              rc == 0 and "run of 3/33 lines" in out and subset == 3,
              f"rc {rc}, {subset}/3 lines OK, and the print says its own totals are "
              "partial")

        total = len(CHECKS)
        green = sum(1 for _, ok, _ in CHECKS if ok)
        emit("")
        emit(f"R-7.5 clause 1 shapes: {green}/{total}")
        for name, ok, detail in CHECKS:
            if not ok:
                emit(f"  BAD {name} — {detail}")
        verdict = 0 if green == total else 1

        if args.witness:
            body = "\n".join(LINES) + "\n"
            subs = body.count(HOME)
            body = body.replace(HOME, "<home>")
            if str(ROOT) in body:
                emit(f"REFUSED the witness: this checkout's path appears in it "
                     f"({body.count(str(ROOT))} time(s)); the run is reported on "
                     f"stdout only")
                return 1
            args.witness.parent.mkdir(parents=True, exist_ok=True)
            args.witness.write_text(body + f"provenance  host paths in this witness: 0 "
                                           f"(substitutions: {subs} `<home>`)\n")
            emit(f"witness: {args.witness}")
        return verdict
    finally:
        if args.keep:
            emit(f"kept: {base}")
        else:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
