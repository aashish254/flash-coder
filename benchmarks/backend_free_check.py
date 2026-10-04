"""R-7.6/R-7.7, OFFLINE: does one command tell the truth about this install?

`flash doctor`, `flash --version` and `flash selftest --all` exist to answer a
stranger's first question — "is what I just installed the thing the README
describes?" — which means a wrong answer is worse than none. This vector gates
each of the ways that happens:

- A report that reads the filesystem and still says yes. So `install_shape` and
  `vector_tools` are called against SYNTHETIC trees built in a temp directory: a
  wheel-shaped one with no `benchmarks/` beside the package, a source-shaped one
  with it, and a third that sits under `site-packages/` the way `pip install .`
  does. The same function has to answer the three differently, which a report that
  learned to say "fine" cannot do.
- A `--backend-free` that blocks nothing. The flag's whole claim is that the
  battery's totals were printed with no generative model available, so the shim
  is tested from a CHILD process — where the failure has to carry the shim's own
  sentence, because a blocked package and an absent one are different facts —
  and a sibling gate requires the shim to leave `numpy` alone, since a blocker
  that broke everything would "prove" the claim by making the battery unrunnable.
- An exit code that does not follow the report, which is how a red page becomes
  a green CI badge.
- A module selftest that reads the data tree beside the package and, on a wheel
  install where that tree does not exist, raises `FileNotFoundError` for a path
  inside site-packages — which a stranger reads as a bug in the package. The
  modules that do are run here against a synthetic wheel-shaped root and must come
  back with rc 2 and one sentence, and a sibling gate points the same guard at a
  root that HAS the data and requires it to say nothing.
- That list being a list. The first version of the gate above named four modules,
  and the wheel run then found a fifth (`flash.tourney`, whose data path was
  resolved against the caller's cwd) and a sixth (`flash.lsp`, which does not even
  raise a path error: it dies with `ValueError: substring not found` three layers
  away from the absent fixture). So the table is now checked against a MEASUREMENT:
  the package is copied into a temp directory that carries no `benchmarks/`, every
  module in that copy exposing a `run_selftest` is run there, and the ones that
  die are classified by re-running them in the same copy with the data symlinked
  back in. A module that fails only when the tree is absent is data-dependent and
  must be tabled; a module that fails either way is this box's state and is on a
  named three-entry allow-list rather than being skipped.
- A lane that cannot tell a refusal from a bug. `battery_reread --backend-free`
  runs rows on a machine whose backend is gone, so two of them die with a sentence
  naming `mlx`, and §34.1's governor can kill a third on a warm laptop. Those are
  REFUSED lines rather than failures — but only if the death SAYS which cause it
  is, and only in the lane that was asked to forgive it. So the decoder is gated
  against the deaths it must not read (an unrelated `AssertionError`, a healthy
  line that merely names `mlx_lm`), the named lists are gated against the rows the
  battery actually has, and one live child runs the PLAIN lane with the block
  injected to prove the flag, not the environment, decides what gets forgiven.

Nothing here loads a model. It runs `flash`'s entry points as subprocesses and
the rest in-process, and it takes seconds.

    python benchmarks/backend_free_check.py            # gates + mutants
    python benchmarks/backend_free_check.py --mutant   # mutants only
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

import flash                                                   # noqa: E402
from flash import cli, doctor                                  # noqa: E402
import battery_reread                                          # noqa: E402

PY = sys.executable
CHECKS: list[tuple[str, bool, str]] = []

# The harness row's own fraction, read out of the battery's table instead of
# written as a second literal here: a check added to `flash.harness --selftest`
# moves that row, and a copy of the number in this file turns a green battery
# into a red one for a reason that has nothing to do with what doctor reports.
HARNESS_ROW = next(f"{want}/{want}" for label, _argv, want, _mut, _kind
                   in battery_reread.BATTERY
                   if label == "flash.harness --selftest")


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


def run(*argv: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([PY, *argv], cwd=ROOT, capture_output=True,
                          text=True, timeout=600, env=env)


def capture(fn, *a, **kw) -> tuple:
    """Call something that PRINTS, and keep its rc and its text."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*a, **kw)
    return rc, buf.getvalue()


def tmp_install(with_benchmarks: bool) -> Path:
    """A directory shaped like an INSTALL: `<root>/flash/__init__.py`, and the
    `benchmarks/` beside it only in the source case."""
    d = Path(tempfile.mkdtemp(prefix="flash-install-"))
    (d / "flash").mkdir()
    (d / "flash" / "__init__.py").write_text('__version__ = "test"\n')
    if with_benchmarks:
        b = d / "benchmarks"
        b.mkdir()
        (b / "battery_reread.py").write_text("# present\n")
        (b / "tasks").mkdir()
        for i in range(3):
            (b / "tasks" / f"t{i}.jsonl").write_text("\n")
    return d


def site_install() -> Path:
    """A package directory under `lib/python3.11/site-packages/`, which is where
    `pip install .` actually puts `flash`. The path shape is the whole point: the
    report's editable/installed answer used to come from the interpreter, and an
    interpreter in a Mac venv resolves to a prefix that has nothing to do with
    where the package lives."""
    d = Path(tempfile.mkdtemp(prefix="flash-site-"))
    pkg = d / "lib" / "python3.11" / "site-packages" / "flash"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "test"\n')
    return pkg


def data_tree() -> Path:
    """A directory shaped like the tree BESIDE an installed `flash`, carrying
    exactly the paths `doctor.VECTOR_DATA` names — files for the corpora, a
    directory for the fixtures package."""
    d = Path(tempfile.mkdtemp(prefix="flash-data-"))
    for rel in sorted(set(doctor.VECTOR_DATA.values())):
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.mkdir() if p.suffix == "" else p.write_text("\n")
    return d


# The selftests that fail on a package-only tree AND with the data symlinked back
# in. Those three are not about the install: `ambient` asserts a wall-clock budget,
# `power` reads this machine's governor, `sandbox` reports the memory rlimit the
# kernel refuses to lower. Naming them is the checked exemption — a fourth module
# appearing here fails the gate rather than being waved through, which is the only
# way the sweep below can be both honest about a loaded box and blind to nothing.
MACHINE_STATE = {"ambient": "wall-clock budget",
                 "power": "reads this box's governor",
                 "sandbox": "memory rlimit the kernel denies"}


def pkg_only_copy(with_data: bool = False) -> Path:
    """The real `flash/` copied whole into a temp directory, with a `foreign/` to
    run from and — in the second shape only — the repo's `benchmarks/` symlinked in
    beside it. This is a package-only install without pip: the copy's `__file__`
    anchors `_DATA_ROOT` at the temp directory, so a module that needs the data tree
    finds it or does not, and the cwd is outside the repo so nothing can be resolved
    relative to where the command happened to be typed."""
    import shutil
    d = Path(tempfile.mkdtemp(prefix="flash-pkgonly-"))
    shutil.copytree(ROOT / "flash", d / "flash",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (d / "foreign").mkdir()
    if with_data:
        (d / "benchmarks").symlink_to(ROOT / "benchmarks")
    return d


def selftest_mods(pkg: Path) -> list[str]:
    """Every module in a package tree that exposes `run_selftest`, read off the
    sources. Not `doctor.VECTOR_DATA`'s keys: the point is to find the modules that
    are not on any list."""
    import ast
    out = []
    for p in sorted((pkg / "flash").glob("*.py")):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        if any(isinstance(n, ast.FunctionDef) and n.name == "run_selftest"
               for n in tree.body):
            out.append(p.stem)
    return out


def run_in_copy(mod: str, pkg: Path, with_shim: bool = False) -> tuple[int, str]:
    env = dict(os.environ, PYTHONPATH=str(pkg))
    if with_shim:
        env["PYTHONPATH"] = str(_shim_dir()) + os.pathsep + str(pkg)
    try:
        proc = subprocess.run([PY, "-m", f"flash.{mod}", "--selftest"],
                              cwd=pkg / "foreign", env=env, capture_output=True,
                              text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return 124, "<timed out after 600s>"
    return proc.returncode, proc.stdout + proc.stderr


_SWEEP: dict = {}
_SHIM_DIR: Path | None = None


def pkg_only_sweep():
    """The package-only sweep, run ONCE per process and replayed after that, keyed
    on the list of modules in the copy. The replay is what keeps ten mutant runs
    from each paying for sixteen subprocess selftests; the key is what makes the
    replay honest — a mutation that PLANTS a module changes the key, so the mutant
    cannot be caught by a stale cache it happened to warm up first.

    The second arm is the same copy with the backend blocked, and it is the only
    local shape a Linux install has: `mlx-lm` is behind a platform marker, so a
    selftest that reaches for it is green here and red on the runner. Blocking beats
    waiting for the runner."""
    bare = pkg_only_copy()
    mods = selftest_mods(bare)
    if _SWEEP.get("key") == mods:
        return _SWEEP["result"]
    with_data = pkg_only_copy(with_data=True)
    out = {m: run_in_copy(m, bare) for m in mods}
    refused = {m for m, (rc, text) in out.items()
               if rc == 2 and "cannot run here" in text}
    suspects = {m: v for m, v in out.items() if v[0] not in (0, 2)}
    passed_twice = 0
    for m in sorted(set(suspects) - set(doctor.VECTOR_DATA)):
        if run_in_copy(m, bare)[0] in (0, 2):
            passed_twice += 1
            suspects.pop(m)
    died = {m: run_in_copy(m, with_data) for m in sorted(suspects)}
    shimmed = {m: run_in_copy(m, bare, with_shim=True) for m in mods}
    dispatched = {m for m in mods
                  if "__main__" in (bare / "flash" / f"{m}.py").read_text()}
    _SWEEP["key"] = mods
    _SWEEP["result"] = (bare, with_data, mods, refused, died, passed_twice,
                        dispatched, shimmed)
    return _SWEEP["result"]


def _shim_dir() -> Path:
    """The directory holding the shipped `--backend-free` shim, built once per
    process, read from the file the battery uses rather than a copy this vector
    could keep true on its own."""
    global _SHIM_DIR
    if _SHIM_DIR is None:
        import battery_reread as battery
        d = Path(tempfile.mkdtemp(prefix="flash-shim-"))
        (d / "sitecustomize.py").write_text(battery._SHIM)
        _SHIM_DIR = d
    return _SHIM_DIR


def shim_env() -> dict:
    """An env carrying that shim ahead of whatever this process already had."""
    prior = os.environ.get("PYTHONPATH")
    return dict(os.environ, PYTHONPATH=str(_shim_dir())
                + (os.pathsep + prior if prior else ""))


def answers(shape: dict, has_backend: bool, tools_ok: bool) -> dict:
    """A synthetic `doctor.answers()`, so the report gates do not depend on what
    this particular machine has installed."""
    return {"version": "test", "shape": shape,
            "backend": {"mlx": "0.32" if has_backend else None,
                        "mlx_lm": "0.31" if has_backend else None,
                        "error": None if has_backend
                        else "import mlx.core: ModuleNotFoundError",
                        "has_backend": has_backend},
            "cache": {"dir": "hub", "present": True, "models": ["a/b"],
                      "matrix_have": [], "matrix_missing": list(doctor.MATRIX)},
            "tools": {k: tools_ok for k in doctor.VECTOR_TOOLS},
            "machine": {"python": "3.11.15", "platform": "linux",
                        "machine": "x86_64", "os": "Linux", "may_load": None,
                        "why": "governor unreadable: RuntimeError"}}


def ns(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def run_gates() -> None:
    # ---- flash --version
    proc = run("-m", "flash.cli", "--version")
    line = proc.stdout.strip()
    ck(f"`flash --version` prints one line, exits 0, and carries the package's own "
       f"version ({line!r}) rather than a copy that can go stale in a parser",
       proc.returncode == 0 and line == f"flash {flash.__version__}",
       f"rc={proc.returncode} stdout={proc.stdout!r} "
       f"stderr={proc.stderr[-160:]}")
    ck("...and the line has the shape a script can parse — a bare `flash <x.y.z>` "
       "— so changing its format is a decision rather than an accident",
       bool(re.fullmatch(r"flash \d+\.\d+\.\d+\S*", line)), line)

    # ---- the parser knows every command the CI files call
    for argv in (["doctor"], ["doctor", "--json"], ["selftest", "--all"],
                 ["selftest", "--all", "--backend-free"],
                 ["selftest", "--all", "--quick", "harness"]):
        try:
            ok, why = callable(getattr(ns(*argv), "fn", None)), ""
        except SystemExit as e:
            ok, why = False, f"argparse exited {e.code}"
        ck(f"`flash {' '.join(argv)}` parses to a command", ok, why)

    # ---- doctor's answers, on synthetic installs at the seam that matters
    wheel = doctor.install_shape(tmp_install(False) / "flash")
    src = doctor.install_shape(tmp_install(True) / "flash")
    ck("install_shape tells apart the two installs this project ships — a wheel "
       "has no benchmarks/ beside the package, a checkout does. One report "
       "function that answered them alike would be a README in disguise",
       not wheel["has_benchmarks"] and not wheel["has_battery"]
       and src["has_benchmarks"] and src["has_battery"],
       f"wheel={wheel} src={src}")
    ck("...and it counts the corpora it finds, so a `benchmarks/` holding nothing "
       "cannot read as a full install",
       src["tasks"] == 3 and wheel["tasks"] == 0,
       f"{src['tasks']} vs {wheel['tasks']}")
    piped = doctor.install_shape(site_install())
    ck("`editable checkout` versus `installed copy` is read off the package's own "
       "path, not off `sys.executable` — a `flash` under "
       "`lib/python3.11/site-packages/` answers installed even though the "
       "interpreter running this check resolves through a venv symlink to a prefix "
       "that is not its own (R-7.5's wheel witness had this one line lying about a "
       "`pip install .`)",
       piped["editable"] is False and "site-packages" in piped["root"]
       and src["editable"] is True and wheel["editable"] is True,
       f"site-packages={piped['editable']} src={src['editable']} "
       f"wheel={wheel['editable']}")
    no_data = doctor.verdict(answers(wheel, has_backend=True, tools_ok=True))
    ck("an install with no verification surface is told so, and the line names "
       "what to do about it rather than only failing",
       any(ok is False and "benchmarks/" in l and "sdist" in l
           for _, ok, l in no_data),
       f"{[l for _, ok, l in no_data if ok is False]}")
    no_be = doctor.verdict(answers(src, has_backend=False, tools_ok=True))
    ck("a box with no MLX hears that generation is missing AND that the offline "
       "battery still runs — R-7.7's promise, placed in the report a stranger "
       "reads first",
       any(ok is False and "generation" in l.lower() for _, ok, l in no_be)
       and any(ok is True and "battery" in l for _, ok, l in no_be),
       f"{[l for _, ok, l in no_be if not ok]}")
    text = doctor.report(answers(src, has_backend=True, tools_ok=True))
    ck("report() prints one mark per answer and no Python residue — no `None`, no "
       "traceback, no raw dict — because this text is the install's first "
       "impression",
       "None" not in text and "Traceback" not in text and "{" not in text
       and "[yes]" in text, text[-160:])
    ck("...and a complete install's report carries ZERO `no` marks and says so",
       "[no ]" not in text and "Both halves" in text, text[-120:])
    real_tools = dict(doctor.VECTOR_TOOLS)
    try:
        doctor.VECTOR_TOOLS = dict(real_tools,
                                   definitely_not_a_module_xyz="a planted name")
        probed = doctor.vector_tools()
    finally:
        doctor.VECTOR_TOOLS = real_tools
    truth = {m: run("-c", f"import {m}").returncode == 0 for m in real_tools}
    ck("the tools answer comes from an actual import: asked about a module nobody "
       "wrote, `vector_tools` says no about it, and its answers about the four "
       "real ones match what a child process reports for the same names",
       probed.get("definitely_not_a_module_xyz") is False
       and all(probed[m] is truth[m] for m in real_tools),
       f"probe={probed} child={truth}")

    old = answers(src, has_backend=True, tools_ok=True)
    old["machine"] = dict(old["machine"], python="3.9.1")
    ck("an interpreter below the floor IS a `no`, while a governor this platform "
       "cannot read is not — the two kinds of bad news the report must keep apart",
       sum(1 for _, ok, _ in doctor.verdict(old) if ok is False) == 1
       and sum(1 for _, ok, _ in doctor.verdict(answers(src, True, True))
               if ok is None) == 3,
       f"{[(ok, line[:44]) for _, ok, line in doctor.verdict(answers(src, True, True))]}")
    text_py = doctor.report(old)
    ck("...and the line that says no names the version it wanted, so the reader "
       "knows what to install",
       "needs 3.11+" in text_py, text_py[-160:])
    pyproject = (ROOT / "pyproject.toml").read_text(errors="ignore")
    floor = re.search(r'requires-python = ">=\s*(\d+)\.(\d+)', pyproject)
    ck(f"`doctor`'s python floor ({'.'.join(map(str, doctor.PY_FLOOR))}) is the one "
       "in `pyproject.toml` — a report that checks a different number than the "
       "package does is two claims, and one of them is stale",
       floor is not None and tuple(int(x) for x in floor.groups()) == doctor.PY_FLOOR,
       f"pyproject={floor.groups() if floor else 'not found'}")
    broken = doctor.report(answers(wheel, has_backend=False, tools_ok=False))
    ck("a broken install's report says no to exactly its own failures — "
       f"{broken.count('[no ]')} of 9 answers, computed per line, not one banner "
       "over the whole page",
       broken.count("[no ]") == 5, broken[-220:])

    real_answers = doctor.answers
    try:
        doctor.answers = lambda: answers(wheel, has_backend=False, tools_ok=False)
        rc_broken, _ = capture(doctor.dispatch, ns("doctor"))
        doctor.answers = lambda: answers(src, has_backend=True, tools_ok=True)
        rc_ok, _ = capture(doctor.dispatch, ns("doctor"))
    finally:
        doctor.answers = real_answers
    ck("the exit code follows the page: 1 for the synthetic broken install, 0 for "
       f"the synthetic complete one (got {rc_broken} and {rc_ok}) — a CI job that "
       "exits 0 on a red report is worse than no job",
       rc_broken == 1 and rc_ok == 0, "")
    proc = run("-m", "flash.cli", "doctor", "--json")
    ck("`flash doctor --json` prints parseable JSON carrying every section the "
       "text report walks through",
       proc.returncode in (0, 1)
       and set(json.loads(proc.stdout)) == {"version", "shape", "backend",
                                            "cache", "tools", "machine"},
       f"rc={proc.returncode} {proc.stdout[:140]}")

    # ---- flash selftest: the refusal arms, at the seam they are read
    real_root = cli.ROOT
    try:
        cli.ROOT = tmp_install(False)
        rc, out = capture(cli.cmd_selftest, ns("selftest", "--all"))
    finally:
        cli.ROOT = real_root
    ck("`flash selftest --all` on an install with no battery REFUSES with rc 2 and "
       "names the missing path instead of printing a total from vectors that never "
       "ran — the lesson R-7.5 learned from `flash.graph --selftest`",
       rc == 2 and "battery_reread.py" in out and "doctor" in out,
       f"rc={rc} {out[:180]}")
    rc2, out2 = capture(cli.cmd_selftest, ns("selftest"))
    ck("`flash selftest` with no --all explains the flag rather than quietly "
       "running something smaller and looking like a pass",
       rc2 == 2 and "--all" in out2, f"rc={rc2} {out2[:140]}")

    # ---- the backend shim: live, narrow, and wired into the children
    env = shim_env()
    blocked = run("-c", "import mlx.core", env=env)
    ck("the `--backend-free` shim really blocks `import mlx.core` in a child, and "
       "the failure carries the shim's own sentence — so a blocked package and an "
       "absent one cannot be confused, including on a box that has no MLX to lose",
       blocked.returncode != 0 and "--backend-free" in blocked.stderr,
       f"rc={blocked.returncode} err={blocked.stderr[-180:]}")
    untouched = run("-c", "import numpy, json, sqlite3; print('ok')", env=env)
    ck("...and it blocks ONLY the backend: numpy, json and sqlite3 still import "
       "under it — a blanket blocker would 'prove' the claim by making the "
       "battery impossible to run",
       untouched.returncode == 0 and "ok" in untouched.stdout,
       f"rc={untouched.returncode} err={untouched.stderr[-180:]}")
    plain = run("-c", "import mlx.core")
    import battery_reread as battery
    saved_env = dict(os.environ)
    try:
        shim_path = battery.install_backend_block()
        first = os.environ.get("PYTHONPATH", "").split(os.pathsep)[0]
        wired = first == str(Path(shim_path).parent)
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
    ck("the battery's own installer puts the shim FIRST on the PYTHONPATH its "
       "children inherit, and the proof names what it found on this box (plain "
       f"`import mlx.core`: "
       f"{'succeeds' if plain.returncode == 0 else 'fails'})",
       wired, f"PYTHONPATH[0]={first!r} shim={shim_path!r}")
    proc = run("benchmarks/battery_reread.py", "--backend-free", "--quick",
               "harness")
    out = proc.stdout + proc.stderr
    ck("`battery_reread --backend-free --quick harness` proves the blocker before "
       "it prints a fraction, and says out loud that a --quick total is not a §6 "
       "re-read",
       HARNESS_ROW in out and "--quick run" in out
       and ("proof --backend-free" in out or "note --backend-free" in out),
       out[-220:])
    proc = run("-m", "flash.cli", "selftest", "--all", "--quick", "harness")
    ck("`flash selftest --all` is a thin wrapper: the same line, the same "
       "fraction, printed through the CLI",
       HARNESS_ROW in proc.stdout and proc.returncode == 0,
       f"rc={proc.returncode} {proc.stdout[-160:]}")

    # ---- R-7.7: the package itself, with no backend importable
    proc = run("-c", "import flash, flash.decide, flash.route; print('imported')",
               env=env)
    ck("with the backend blocked, `import flash.decide` and `import flash.route` "
       "succeed — R-7.7's replacement for the old build, which failed at import "
       "and took the router and every caller of it down with it",
       proc.returncode == 0 and "imported" in proc.stdout,
       f"rc={proc.returncode} err={proc.stderr[-200:]}")
    proc = run("-c",
               "import importlib, pkgutil, sys\n"
               "import flash\n"
               "names = sorted(m.name for m in pkgutil.iter_modules(flash.__path__))\n"
               "bad = []\n"
               "for n in names:\n"
               "    try:\n"
               "        importlib.import_module(f'flash.{n}')\n"
               "    except Exception as e:\n"
               "        bad.append(f'{n}:{type(e).__name__}')\n"
               "print(f'SWEEP {len(names) - len(bad)}/{len(names)} ' + ' '.join(bad))\n",
               env=env)
    sweep = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or ["<no output>"]
    m = re.search(r"SWEEP (\d+)/(\d+)", sweep[0])
    ck("EVERY submodule of `flash` imports with the backend blocked, not just the "
       "two that used to fail — R-7.7's clause is that the numerator equals the "
       "denominator, and a sweep is "
       f"the only way a new module cannot quietly join the failures ({sweep[0]})",
       proc.returncode == 0 and m is not None and int(m.group(1)) == int(m.group(2))
       and int(m.group(2)) >= 26,
       f"rc={proc.returncode} {sweep[0][-200:]}")
    proc = run("-c",
               "import flash.decide as d\n"
               "try:\n"
               "    d.decide(None, None, 'ctx', ['a', 'b'])\n"
               "    print('no error')\n"
               "except RuntimeError as e:\n"
               "    print('RUNTIMEERROR:' + str(e)[:100])\n"
               "except Exception as e:\n"
               "    print('OTHER:' + type(e).__name__ + ':' + str(e)[:100])\n",
               env=env)
    ck("...and the CALL that needs the backend is the one that says so, as a "
       "RuntimeError naming MLX rather than a ModuleNotFoundError or a tokenizer "
       "crash — a caller can print that sentence to a user",
       "RUNTIMEERROR:" in proc.stdout and "MLX" in proc.stdout,
       f"rc={proc.returncode} out={proc.stdout[-200:]} err={proc.stderr[-120:]}")

    # ------------------------------------------- R-7.10: a selftest that cannot
    # run here must SAY so. Measured on a wheel installed in a clean venv: three
    # of the first four vectors raised `FileNotFoundError` for a path inside
    # site-packages, and the fourth printed a FAIL that was an absent directory.
    # The loop is over the table, so the sixth and seventh entry join it without
    # this file being edited; R-7.10c is the gate that finds them in the first place.
    import importlib
    mods = {m: importlib.import_module(f"flash.{m}")
            for m in sorted(doctor.VECTOR_DATA)}
    empty = Path(tempfile.mkdtemp(prefix="flash-wheel-"))
    full = data_tree()
    keep = {m: mods[m]._DATA_ROOT for m in mods}
    try:
        for mod, m in sorted(mods.items()):
            m._DATA_ROOT = empty
            rc, text = capture(m.run_selftest)
            ck(f"`python -m flash.{mod} --selftest` on a wheel-shaped install "
               f"refuses instead of raising: exit 2, one sentence naming the "
               f"missing path, and no traceback for a stranger to read as a bug "
               f"in the package",
               rc == 2 and "cannot run here" in text
               and str(empty / doctor.VECTOR_DATA[mod]) in text
               and "Traceback" not in text and "FileNotFoundError" not in text,
               f"rc={rc} out={text[-160:]!r}")
        for mod, m in sorted(mods.items()):
            m._DATA_ROOT = full
        with contextlib.redirect_stdout(io.StringIO()):
            silent = [doctor.vector_refusal(mod, mods[mod]._DATA_ROOT)
                      for mod in sorted(mods)]
            absent = [doctor.missing_vector_data(mod, full) for mod in sorted(mods)]
        ck(f"the same {len(mods)} guards, at a root that DOES carry the "
           f"{len(mods)} paths, say nothing — the refusal above is a fact about the "
           "tree and not a constant, which is the difference between 'run this from "
           "a clone' and a vector that can never run",
           silent == [None] * len(mods) and absent == [None] * len(mods)
           and len(mods) >= 6,
           str([str(p) for p in absent if p]))
    finally:
        for mod, m in mods.items():
            m._DATA_ROOT = keep[mod]
    ck("the table is a claim about the DATA tree, not the package: every path in it "
       f"sits under `benchmarks/`, and the {len(doctor.VECTOR_DATA)} modules it names "
       "are exactly the ones carrying the `_DATA_ROOT` seam this vector repoints — a "
       "package-side path would make the guard answer 'present' forever, which is a "
       "silent version of the bug it exists to fix",
       all(str(p).startswith("benchmarks/") for p in doctor.VECTOR_DATA.values())
       and all(hasattr(mods[m], "_DATA_ROOT") and callable(mods[m].run_selftest)
               for m in mods)
       and len(doctor.VECTOR_DATA) >= 6,
       f"{len(doctor.VECTOR_DATA)} entries: {sorted(doctor.VECTOR_DATA)}")

    # ------------------------------------- R-7.10c: the table above is a list, and
    # a list is only as good as the measurement that says it is complete. The
    # package is copied to a temp directory with no `benchmarks/` beside it and every
    # module there that exposes a `run_selftest` is executed from a cwd outside the
    # repo; the ones that die are re-run in the same copy with the data symlinked
    # back. That separates "this install has no data tree" from "this box is loaded",
    # which is the difference between a refusal sentence and a skipped module.
    bare, with_data, swept, refused, died, passed_twice, dispatched, shimmed = \
        pkg_only_sweep()
    data_dependent = {m for m, (rc, _) in died.items() if rc in (0, 2)}
    untabled = sorted(data_dependent - set(doctor.VECTOR_DATA))
    state = sorted(set(died) - data_dependent - set(MACHINE_STATE))
    ck(f"the package-only sweep has a spine: it ran {len(swept)} module selftests "
       f"in a copy that carries no `benchmarks/`, {len(refused)} of them refused "
       "with the shared sentence and those are exactly the table's keys, and every "
       "module it swept has the `__main__` tail that makes `python -m` reach it at "
       "all — so 'nothing on this list dies unexplained' cannot be green because "
       "the sweep collected nothing or ran a module that cannot be run",
       len(swept) >= 16 and len(refused) >= 6
       and refused == set(doctor.VECTOR_DATA) and dispatched == set(swept),
       f"swept={len(swept)} refused={sorted(refused)} "
       f"no-main={sorted(set(swept) - dispatched)} "
       f"tabled={sorted(doctor.VECTOR_DATA)}")
    ck("...and no module dies on a package-only tree unless the table names it: a "
       "selftest that passes once the data is symlinked back in was reading the "
       "tree, and owes a stranger rc 2 with a sentence instead of a traceback or a "
       "FAIL line whose cause is an absent directory. Each untabled failure is "
       f"re-run in the bare copy first ({passed_twice} of them passed the second "
       "time and are therefore timing, not data), because a data-dependence is "
       "deterministic and a wall-clock budget is not",
       not untabled,
       "untabled data-dependents: "
       + str([(m, died[m][0], died[m][1].strip()[-90:]) for m in untabled]))
    ck("...and a failure that survives the data coming back is one of the three "
       f"named machine-state vectors ({', '.join(sorted(MACHINE_STATE))}), never a "
       "silently-skipped module: the sweep is allowed to be honest about a loaded "
       "box, and the price is that the list is checked in both directions",
       not state,
       "new unexplained failures: "
       + str([(m, died[m][0], died[m][1].strip()[-90:]) for m in state]))

    # ----------------------------- R-7.16: the same copy, with the backend blocked.
    # `mlx-lm` sits behind a platform marker in `pyproject.toml`, so every Linux and
    # Windows install has the shape this arm fakes and no Mac does. The first CI run
    # on a real runner found the one selftest that reached for the library anyway —
    # `flash.train`, whose slice-args checks were argued against the INSTALLED
    # mlx-lm — and printed it as an unexplained death, correctly, several days after
    # the same code had been re-read green on this laptop. Blocking is the only way
    # that class of bug is visible before someone else's runner pays for it.
    backend_deaths = sorted(m for m, (rc, _) in shimmed.items()
                            if rc not in (0, 2) and m not in MACHINE_STATE)
    ck(f"the same {len(swept)} selftests re-run in the same package-only copy with "
       "`mlx`, `mlx_lm` and `mlx_vlm` unimportable: every one of them answers with "
       "its fraction or with the shared refusal, and not one reaches for the "
       "backend — which is the claim a stranger's `pip install .` on Linux actually "
       "gets, measured on the machine that cannot run it rather than asserted from "
       f"the one that can ({len(backend_deaths)} deaths)",
       len(shimmed) == len(swept) and len(swept) >= 16 and not backend_deaths,
       "selftests that need a backend: "
       + str([(m, shimmed[m][0], shimmed[m][1].strip()[-90:])
              for m in backend_deaths]))
    backend_control = run("-c", "import mlx_lm.lora", env=shim_env())
    ck("...and the block is live in the interpreter that arm used: `import "
       "mlx_lm.lora` under the same environment the swept children inherited "
       "raises with the shim's own sentence, so the line above cannot be green "
       "because the shipped `BLOCKED` list lost a name and the second arm quietly "
       "became a re-run of the first",
       backend_control.returncode != 0 and "--backend-free" in backend_control.stderr,
       f"rc={backend_control.returncode} err={backend_control.stderr[-120:]}")

    # ----------------------------- R-7.16: the lane's refusal lists.
    # A row that dies because the backend is gone is not a failing test, and neither
    # is one that dies because the §34.1 governor would not offer width. The only
    # thing separating either death from a real bug is a SENTENCE, so the decoder
    # that reads it is the gate that matters most here: too loose and the lane
    # forgives a crash, too tight and the lane reports a red battery on every Linux
    # runner for a reason nobody has to fix in code.
    lane = run("benchmarks/battery_reread.py", "--backend-free", "--quick",
               "grammar", "session")
    lane_out = lane.stdout + lane.stderr
    lane_refused = {m.group(1) for line in lane_out.splitlines()
                    if (m := re.match(r"REFUSED (.+?)\s+(?:backend|machine) — ",
                                      line))}
    reasons = {l: r for arm in ("backend", "machine")
               for l, r in battery.REFUSAL[arm].items()}
    named = set(reasons)
    backend_rows = set(battery.REFUSAL["backend"])
    ck("`battery_reread --backend-free --quick grammar session` answers rc 0 with a "
       "REFUSED line per row and no BAD line, each line showing that row's OWN cause "
       "sentence after the dash — and the rows the child refused are exactly the "
       "names this process's `backend` list carries. The child reads the lists off "
       "disk and the parent reads the ones it was handed, so the two agreeing is a "
       "measurement, not a tautology: a list that names a row no lane ever saw die "
       "is a note, and a lane that forgives a row the list has no name for is a "
       "freebie",
       lane.returncode == 0 and "BAD" not in lane_out
       and lane_refused == backend_rows and bool(lane_refused)
       and "proof --backend-free" in lane_out
       and all(battery.REFUSAL["backend"][l][:40] in lane_out
               for l in lane_refused & backend_rows),
       f"rc={lane.returncode} refused={sorted(lane_refused)} "
       f"listed={sorted(backend_rows)} out={lane_out[-200:]}")

    shim_death = ("   tokenizer unavailable: ImportError mlx_lm blocked by "
                  "--backend-free: this run is proving which of the battery's "
                  "vectors need a generative backend")
    linux_death = ('Traceback (most recent call last):\n'
                   '  File "benchmarks/session_check.py", line 19, in <module>\n'
                   "    from flash.loop import solve_routed\n"
                   "ModuleNotFoundError: No module named 'mlx_lm'")
    governor_death = ("AssertionError: the tournament arm needs the governor's "
                      "width >= 2 and this machine offers 1 (on battery): put it "
                      "on AC, let it cool, re-run")
    bug_death = ('Traceback (most recent call last):\n'
                 '  File "benchmarks/x_check.py", line 88, in run_gates\n'
                 "    ck('the patch lands', n == 5, f'got {n}')\n"
                 "AssertionError: expected 5 got 4")
    healthy = ("  OK  the doctor reports mlx_lm: None on a linux install\n"
               "OK   benchmarks/ts_perception_check.py 47/47\n")
    cases = [(shim_death, "backend", True), (linux_death, "backend", True),
             (governor_death, "machine", True), (shim_death, "machine", False),
             (governor_death, "backend", False), (bug_death, "backend", False),
             (bug_death, "machine", False), (healthy, "backend", False),
             (healthy, "machine", False), ("", "backend", False)]
    misdecoded = [(t.splitlines()[0][:38] if t else "<empty>", k, want,
                   battery.refuses(k, t)[:58])
                  for t, k, want in cases
                  if bool(battery.refuses(k, t)) != want]
    shown = battery.refuses("backend", shim_death)
    ck("the decoder reads CAUSES, not words. Three deaths it must read: the shim's "
       "own sentence and a Linux `ModuleNotFoundError: No module named 'mlx_lm'` "
       "both decode as backend-bound (they are the same fact about two machines), "
       "and the governor's clamp decodes as machine-bound. Seven it must not: an "
       "unrelated `AssertionError: expected 5 got 4`, a healthy line that merely "
       "NAMES `mlx_lm`, and empty output — in either arm, so the two causes cannot "
       "cross-talk — and when it does read a cause it returns the sentence itself, "
       "because the REFUSED line's evidence is the row's own words, not a byte "
       "count. `if 'mlx' in out` would be green on every one of the ten cases above "
       "and would forgive a real bug on the one platform that has the backend",
       not misdecoded and "--backend-free" in shown
       and battery.refuses("machine", governor_death) == governor_death
       and battery.refuses("machine", shim_death) == "",
       f"{len(misdecoded)} misdecoded: {misdecoded[:2]} shown={shown[:60]!r}")

    rows = {label: (want, mut) for label, _argv, want, mut, _kind
            in battery.BATTERY}
    kinds = {label: kind for label, _argv, _w, _m, kind in battery.BATTERY}
    unrowed = sorted(named - set(rows))
    overlap = sorted(backend_rows & set(battery.REFUSAL["machine"]))
    no_reason = sorted(l for l, r in reasons.items() if len(r) < 40)
    bad_kind = sorted(l for l in named if kinds.get(l) not in battery.CLAIM)
    dropped = battery.lane_claim([("benchmarks/session_check.py", 81, 26, "checks")])
    partial = battery.lane_claim([("benchmarks/m0_bakeoff.py --dry-run", 20,
                                   None, "oracle")])
    ck(f"the lists are legal battery arithmetic: all {len(named)} named rows are "
       "real `BATTERY` labels, the two arms are disjoint, every row carries its own "
       "reason sentence rather than a placeholder, each row's kind is a key `CLAIM` "
       "actually sums, and the subtraction uses the row's own counts — "
       "`lane_claim([])` is §6 untouched, an 81-check 26-mutant row drops the claim "
       "by exactly 81 and 26, and an oracle row with no mutants moves `oracle` and "
       "still leaves `mutants` alone. A list that subtracts a number the row does "
       "not print makes the lane's subtotal mean nothing, which is the failure this "
       "whole mechanism exists to avoid",
       not unrowed and not overlap and not no_reason and not bad_kind
       and battery.lane_claim([]) == battery.CLAIM
       and dropped["checks"] == battery.CLAIM["checks"] - 81
       and dropped["mutants"] == battery.CLAIM["mutants"] - 26
       and dropped["oracle"] == battery.CLAIM["oracle"]
       and partial["oracle"] == battery.CLAIM["oracle"] - 20
       and partial["mutants"] == battery.CLAIM["mutants"],
       f"unrowed={unrowed} overlap={overlap} no-reason={no_reason} kinds={bad_kind} "
       f"dropped={dropped} partial={partial}")

    # The other half of the claim: the FLAG decides what may be forgiven, not the
    # shim's presence in the interpreter. Injecting the block into a plain-lane
    # child kills the same row with the same sentence, and the plain lane must call
    # it a failure — otherwise a Mac with MLX installed forgives a backend death and
    # still prints §6's totals as though it had measured all of them.
    plain_lane = run("benchmarks/battery_reread.py", "--quick", "grammar",
                     env=shim_env())
    plain_out = plain_lane.stdout + plain_lane.stderr
    ck("...and the same killed row is a BAD line in the plain lane: `battery_reread "
       "--quick grammar` run with the block on its PYTHONPATH but WITHOUT "
       "--backend-free exits non-zero, prints no `proof --backend-free` line and no "
       "REFUSED line, and says in its own note which lane will not refuse this one — "
       "read beside `refusal_map(False)`, which is the machine arm only, so the §6 "
       "denominator cannot drift depending on who happened to export a PYTHONPATH",
       plain_lane.returncode != 0 and "REFUSED flash.grammar" not in plain_out
       and "proof --backend-free" not in plain_out and "will not refuse" in plain_out
       and set(battery.refusal_map(False)) == set(battery.REFUSAL["machine"])
       and set(battery.refusal_map(True)) == named,
       f"rc={plain_lane.returncode} map_false="
       f"{sorted(battery.refusal_map(False))} out={plain_out[-200:]}")


# ---------------------------------------------------------------- mutation cover

BUGS = {
    "always_yes_tools": ("`vector_tools` stops probing and reports every module "
                         "present, so the report forgives an install whose "
                         "battery would fail on the first import", "gate"),
    "shape_blind": ("`install_shape` reports the tree it is running IN rather "
                    "than the one it was asked about, so a wheel install reads as "
                    "a checkout", "gate"),
    "rc_ignores_report": ("`dispatch` returns 0 whatever the report said — a red "
                          "page, a green badge", "gate"),
    "decorative_shim": ("`--backend-free` installs the shim and forgets to put it "
                        "on the environment its children inherit, so the flag "
                        "'proves' a claim it never tested", "gate"),
    "top_level_backend": ("a new submodule grows a top-level `import mlx.core`, "
                          "which is the exact R-7.7 regression the sweep exists to "
                          "catch — planted on disk, not stubbed in memory, because "
                          "the claim is about what a child process can import",
                          "gate"),
    "always_refuses": ("the wheel-install guard answers 'missing' even when the "
                       "data tree is there, which turns every clone-only vector "
                       "off with a polite sentence and a green exit-2", "gate"),
    "table_in_the_package": ("a `VECTOR_DATA` path moves inside `flash/`, where "
                             "every install has it, so the guard's predicate "
                             "silently becomes 'nothing to check'", "gate"),
    "editable_via_interpreter": ("the shape's editable answer goes back to reading "
                                 "`sys.executable`, which resolves through the venv "
                                 "symlink, so a `pip install .` reports itself as an "
                                 "editable checkout — the wheel witness's line",
                                 "gate"),
    "unguarded_data_selftest": ("a new module selftest reads the data tree beside "
                                "the package and joins no table — planted as a real "
                                "file in `flash/`, because the claim is about what a "
                                "stranger's child process does on a package-only "
                                "install, not about an attribute in this one",
                                "gate"),
    "table_drops_an_entry": ("one module leaves `VECTOR_DATA` while its guard stays "
                             "wired, so the table under-claims the package and the "
                             "sweep's refusals stop matching it", "gate"),
    "selftest_reaches_backend": ("a module's selftest imports the trainer library on "
                                 "its offline path: green on every Mac, red on every "
                                 "other install, which is the exact shape the first "
                                 "ubuntu runner reported — planted as a real file "
                                 "because the claim is about a child process",
                                 "gate"),
    "refusal_decodes_anything": ("the lane's decoder matches on the shape of an "
                                 "output instead of its cause, so every death in "
                                 "every row is forgiven as 'the backend was missing' "
                                 "and a real crash prints REFUSED with a green exit",
                                 "gate"),
    "refusal_list_loses_a_row": ("a backend-bound row leaves `REFUSAL['backend']` "
                                 "while the lane keeps refusing it on the machine "
                                 "that has the backend, so the list under-claims and "
                                 "the lane's subtotal is computed against rows that "
                                 "never died here", "gate"),
    "plain_lane_forgives_backend": ("`refusal_map` stops asking which lane it is in "
                                    "and opens the backend arm to the plain run, "
                                    "which is the one platform where a backend death "
                                    "might be a bug — §6's totals then come from a "
                                    "run that forgave a row", "gate"),
}


def mutate(one: str | None = None) -> int:
    failed = ran = 0
    for bug in BUGS:
        if one and bug != one:
            continue
        ran += 1
        import battery_reread as battery
        keep = {"vector_tools": doctor.vector_tools,
                "install_shape": doctor.install_shape,
                "dispatch": doctor.dispatch,
                "missing": doctor.missing_vector_data,
                "table": doctor.VECTOR_DATA,
                "editable": doctor._is_editable,
                "refuses": battery.refuses,
                "map": battery.refusal_map,
                "refusal": battery.REFUSAL,
                "block": None}
        try:
            if bug == "always_yes_tools":
                doctor.vector_tools = lambda: {k: True
                                               for k in doctor.VECTOR_TOOLS}
            elif bug == "shape_blind":
                doctor.install_shape = lambda pkg_dir=None: keep["install_shape"]()
            elif bug == "rc_ignores_report":
                doctor.dispatch = lambda a: 0
            elif bug == "always_refuses":
                doctor.missing_vector_data = lambda mod, root: (
                    root / doctor.VECTOR_DATA[mod]) if mod in doctor.VECTOR_DATA \
                    else None
            elif bug == "table_in_the_package":
                doctor.VECTOR_DATA = {m: "flash/__init__.py"
                                      for m in doctor.VECTOR_DATA}
            elif bug == "editable_via_interpreter":
                doctor._is_editable = lambda pkg_dir: str(
                    Path(sys.executable).resolve().parent.parent / "lib") \
                    not in str(Path(pkg_dir).resolve())
            elif bug == "decorative_shim":
                keep["block"] = battery.install_backend_block
                battery.install_backend_block = lambda: "/dev/null/sitecustomize.py"
            elif bug == "top_level_backend":
                planted = ROOT / "flash" / "_zz_backend_probe.py"
                planted.write_text("import mlx.core  # planted by this mutation\n")
            elif bug == "unguarded_data_selftest":
                planted = ROOT / "flash" / "_zz_data_probe.py"
                planted.write_text(
                    "from pathlib import Path\n"
                    "_DATA_ROOT = Path(__file__).resolve().parent.parent\n\n\n"
                    "def run_selftest(verbose: bool = True) -> int:\n"
                    "    # planted by this mutation: reads the tree, says nothing\n"
                    "    body = (_DATA_ROOT / 'benchmarks/tasks"
                    "/dbg_tasks.jsonl').read_text()\n"
                    "    return 0 if body else 1\n\n\n"
                    'if __name__ == "__main__":\n'
                    "    raise SystemExit(run_selftest())\n")
            elif bug == "table_drops_an_entry":
                doctor.VECTOR_DATA = {k: v for k, v in doctor.VECTOR_DATA.items()
                                      if k != "lsp"}
            elif bug == "selftest_reaches_backend":
                planted = ROOT / "flash" / "_zz_shim_probe.py"
                planted.write_text(
                    "def run_selftest(verbose: bool = True) -> int:\n"
                    "    # planted by this mutation: the offline path reaches for "
                    "the\n"
                    "    # trainer library, which is green here and red on Linux\n"
                    "    from mlx_lm.lora import CONFIG_DEFAULTS\n"
                    "    return 0 if CONFIG_DEFAULTS else 1\n\n\n"
                    'if __name__ == "__main__":\n'
                    "    raise SystemExit(run_selftest())\n")
            elif bug == "refusal_decodes_anything":
                # The bug the lane cannot be allowed to grow: a decoder that reads
                # any output as a cause. Planted on the module so the in-process
                # gates see it, while the child they run keeps reading the shipped
                # lists off disk — which is what makes the disagreement visible.
                battery.refuses = lambda kind, out: (
                    out.strip().splitlines()[-1].strip() if out.strip() else "")
            elif bug == "refusal_list_loses_a_row":
                battery.REFUSAL = {
                    **battery.REFUSAL,
                    "backend": {k: v for k, v in
                                battery.REFUSAL["backend"].items()
                                if k != "flash.grammar --selftest"}}
            elif bug == "plain_lane_forgives_backend":
                battery.refusal_map = lambda backend_free: {
                    **battery.REFUSAL["backend"], **battery.REFUSAL["machine"]}
            CHECKS.clear()
            run_gates()
            caught = [l for l, ok, _ in CHECKS if not ok]
        finally:
            if bug == "top_level_backend":
                for f in (ROOT / "flash" / "_zz_backend_probe.py",
                          ROOT / "flash" / "__pycache__" / "_zz_backend_probe.py"):
                    f.unlink(missing_ok=True)
            if bug == "unguarded_data_selftest":
                for f in (ROOT / "flash" / "_zz_data_probe.py",
                          ROOT / "flash" / "__pycache__" / "_zz_data_probe.py"):
                    f.unlink(missing_ok=True)
            if bug == "selftest_reaches_backend":
                for f in (ROOT / "flash" / "_zz_shim_probe.py",
                          ROOT / "flash" / "__pycache__" / "_zz_shim_probe.py"):
                    f.unlink(missing_ok=True)
            doctor.vector_tools = keep["vector_tools"]
            doctor.install_shape = keep["install_shape"]
            doctor.dispatch = keep["dispatch"]
            doctor.missing_vector_data = keep["missing"]
            doctor.VECTOR_DATA = keep["table"]
            doctor._is_editable = keep["editable"]
            battery.refuses = keep["refuses"]
            battery.refusal_map = keep["map"]
            battery.REFUSAL = keep["refusal"]
            if keep["block"]:
                battery.install_backend_block = keep["block"]
        ok = bool(caught)
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} MUTATION: {BUGS[bug][0]} "
              f"-> {len(caught)} check(s) fail "
              f"{[c[:34] for c in caught][:2]}")
    print(f"\nbackend-free mutants: {ran - failed}/{ran} gates defeated by "
          "exactly their checks")
    return failed


def main(argv: list) -> int:
    if argv and argv[0] == "--mutant":
        return 1 if mutate(argv[1] if len(argv) > 1 else None) else 0
    run_gates()
    for label, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}"
              + (f"\n  ..  {detail}" if detail and not ok else ""))
    n = len(CHECKS)
    bad = [l for l, ok, _ in CHECKS if not ok]
    print(f"\nbackend-free checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
