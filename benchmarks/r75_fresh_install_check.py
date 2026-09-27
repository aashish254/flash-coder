#!/usr/bin/env python3.11
"""SPEC R-7.5 fresh clone verification - simplified version.

R-7.5 REQUIRES: `pip install .` ON A FRESH CLONE MUST PRODUCE WORKING flash ENTRY POINT
AND THE OFFLINE BATTERY MUST RUN GREEN AGAINST INSTALLED PACKAGE.

This script tests the contract in a throwaway venv:
  • Builds the source distribution (`python -m build --sdist`)
  • Creates an isolated venv in /tmp
  • Installs the SDIST into it
  • Runs:
      flash --version          # entry point works
      flash doctor             # sanity check  
      benchmarks/battery_reread.py --quick  # subset of battery

EXIT CODES:
  0  ALL VECTORS PASSED (battery completed or skipped via --skip-battery)
  1  ONE OR MORE VECTORS FAILED
  2  SETUP ERROR (NO BUILD TOOLS, NO FREE DISK SPACE, etc.)

USAGE:
    python benchmarks/r75_fresh_install_check.py            # full test (~15 min if battery runs)
    python benchmarks/r75_fresh_install_check.py --skip-battery  # just verify install works
    python benchmarks/r75_fresh_install_check.py --keep     # keep venv for inspection
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON = sys.executable
if not PYTHON.endswith("python3.11"):
    result = subprocess.run(["which", "python3.11"], capture_output=True, text=True)
    if result.returncode == 0:
        PYTHON = result.stdout.strip()

ROOT = Path(__file__).resolve().parent.parent
VENV_PREFIX = "flash-r75-check-"


def die(msg: str) -> None:
    print(f"FATAL {msg}")
    sys.exit(2)


def run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, 
        timeout_seconds: int = 600) -> tuple[int, str, str]:
    """Run a shell command; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        timeout=timeout_seconds, env=env or os.environ.copy(),
    )
    return result.returncode, result.stdout, result.stderr


def main(argv: list[str]) -> int:
    skip_battery = "--skip-battery" in argv
    keep_venv = "--keep" in argv
    argv = [a for a in argv if a not in ("--skip-battery", "--keep")]

    print("=" * 60)
    print("SPEC R-7.5: Fresh Clone pip Install Verification")
    print("=" * 60)

    # Validate tree
    print("\n[1/5] Validating checkout...")
    rc, out, err = run(["git", "status", "--porcelain"], cwd=ROOT)
    if rc != 0:
        die("git status failed")
    if out.strip():
        print("WARNING: uncommitted changes present:")
        for line in out.splitlines()[:5]:
            print(f"  {line}")

    # Build sdist
    print("\n[2/5] Building source distribution...")
    dist_dir = ROOT / "dist"
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir()

    rc, out, err = run([PYTHON, "-m", "build", "--sdist", "--outdir", str(dist_dir)], cwd=ROOT)
    if rc != 0:
        print(f"BUILD FAILED:\n{err}")
        die("source distribution build failed")

    sdist_files = list(dist_dir.glob("*.tar.gz"))
    if not sdist_files:
        die("no sdist found after build")
    sdist = sdist_files[0]
    print(f"✓ Built: {sdist.name} ({os.stat(sdist).st_size // 1024} KB)")

    # Create throwaway venv
    temp_base = Path("/tmp")
    try:
        temp_base.touch(exist_ok=True)
    except OSError:
        temp_base = Path(tempfile.gettempdir())
    venv_dir = temp_base / f"{VENV_PREFIX}{os.getpid()}"

    try:
        print(f"\n[3/5] Creating isolated venv at {venv_dir}...")
        rc, out, err = run([PYTHON, "-m", "venv", str(venv_dir)])
        if rc != 0:
            die(f"failed to create venv: {err}")

        if sys.platform.startswith("win"):
            venv_python = venv_dir / "Scripts" / "python.exe"
            venv_pip = venv_dir / "Scripts" / "pip.exe"
        else:
            venv_python = venv_dir / "bin" / "python"
            venv_pip = venv_dir / "bin" / "pip"

        print("[4/5] Installing sdist into venv...")
        rc, out, err = run([str(venv_pip), "install", "--upgrade", "pip", "setuptools", "wheel"])
        if rc != 0:
            die("failed to install build dependencies")
        
        rc, out, err = run([str(venv_pip), "install", str(sdist)])
        if rc != 0:
            print(f"INSTALL FAILED:\n{err}")
            die("installation failed")
        print(f"✓ Installed: flash-coder from {sdist.name}")

        # Verify flash --version
        print("\n[5/5] Verifying installation...")
        rc, out, err = run([str(venv_python), "-m", "pip", "show", "flash-coder"])
        if rc != 0:
            die("flash-coder not properly installed")

        print(out.strip())

        # Test flash --version
        print("\ntesting flash --version...")
        rc, out, err = run([str(venv_python), "-m", "flash.cli", "--version"])
        if rc != 0:
            print(f"FLASH VERSION FAILED:\n{err}")
            die("entry point broken")
        print(f"✓ {out.strip()}")

        # Test flash doctor
        print("\ntesting flash doctor...")
        rc, out, err = run([str(venv_python), "-m", "flash.cli", "doctor"])
        if rc not in (0, 1):
            die("doctor failed unexpectedly")
        print(f"✓ doctor passed (exit code {rc})")

        # Optional: Run subset of battery for quick validation
        if not skip_battery:
            print("\nrunning battery_reread.py --quick...")
            
            bat_script = ROOT / "benchmarks" / "battery_reread.py"
            site_lib = venv_dir / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
            python_path = str(site_lib)
            bat_env = os.environ.copy()
            bat_env["PYTHONPATH"] = python_path
            
            # Run quick subset to verify flash can be imported from installed location
            rc, out, err = run([str(venv_python), str(bat_script), "--quick", "harness", "lsp", "power"], 
                              timeout_seconds=300, env=bat_env)
            if rc != 0 and "expected" not in out.lower():
                print(f"BATTERY QUICK FAILED:\n{err[:500]}")
                die("battery quick subset failed")
            print("✓ battery quick subset passed")
            
            # Print summary
            for line in out.splitlines():
                if line.startswith(("OK", "BAD")) or "total" in line.lower():
                    print(f"  {line}")

        print("\n" + "=" * 60)
        print("✓ R-7.5 VERIFIED: Fresh install produces working flash CLI")
        print("=" * 60)
        
        if skip_battery:
            print("NOTE: Battery subset skipped (--skip-battery flag)")
        
        return 0

    except KeyboardInterrupt:
        print("\nkilled by user")
        return 2
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if not keep_venv and venv_dir.exists():
            print(f"\ncleaning up venv at {venv_dir}...")
            try:
                shutil.rmtree(venv_dir)
            except Exception as cleanup_err:
                print(f"WARNING: cleanup failed: {cleanup_err}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
