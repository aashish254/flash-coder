"""R-7.6: `flash doctor` — is this install sane, and how do I verify what I just read?

The question this answers is the one a person on a machine nobody has seen asks
after their first `pip install`: not "does the code run" (`--help` proves that
much) but "which HALF of this project do I have, and what would it take to have
the rest". Two facts make that a real question here rather than a formality:

- The package and its verification surface are separate trees. `benchmarks/`
  sits NEXT TO `flash/` in a checkout, and a wheel install carries no
  `benchmarks/` at all, so the same `flash` on PATH can re-run every published
  number on one machine and none on another. That split was measured on a clean
  clone (SPEC R-7.5), where a selftest printed eight failures that were really
  one absent directory.
- MLX exists only on Apple Silicon, so the generative half may be missing for a
  reason no installer can fix.

So every line here is read from the running interpreter and the filesystem beside
it — never from a document, because a document is the thing under test.

    flash doctor            # the report
    flash doctor --json     # the same answers as an object
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

import flash

# What the project claims to be able to do, so the report can say which half a
# machine is in rather than listing versions and leaving the reader to infer.
SMALL_TIER_GB = 4.4
PY_FLOOR = (3, 11)                 # `requires-python` in pyproject.toml; a check
                                   # in backend_free_check.py keeps the two equal
MATRIX = ("Qwen2.5-Coder-7B-Instruct-4bit", "Qwen3-Coder-30B-A3B-Instruct-4bit",
          "Qwen3-30B-A3B-Instruct-2507-4bit", "gpt-oss-20b-MXFP4-Q8")
# What the offline verification surface imports. Measured, not guessed: every
# name here is a top-level `import` in `flash/*.py` or `benchmarks/*.py` that is
# not in the standard library, so a `[no]` answer means a vector would fail on
# this box, not that an optional extra is missing.
VECTOR_TOOLS = {"numpy": "the battery's numeric seams",
                "pyflakes": "the lint gate §6 runs as a check",
                "pylsp": "the LSP seam's server (flash perceive/find/refs)",
                "requests": "flash web's doc fetch"}

# Which module selftests read the data tree NEXT TO the package rather than only
# the package. Measured on a wheel installed in a clean venv (SPEC R-7.5): with no
# `benchmarks/` beside `flash/`, `flash.grammar` and `flash.debug` raised
# `FileNotFoundError` for a path inside site-packages and `flash.patches` printed
# one FAIL out of 46 that was an absent directory wearing a failure. `flash.graph`
# already refused, and this table is that refusal generalised to the three that
# did not, so the four share one sentence and one gate.
# The list is NOT the safety property — the gate in `benchmarks/backend_free_check.py`
# sweeps every module that exposes a `run_selftest` in a package-only copy of the
# tree, so a module that grows a data-dependence and is not tabled fails there.
# That gate exists because the second wheel run found two entries the first four
# missed. `flash.tourney --selftest` scores one real h-task and raised
# `FileNotFoundError` for a path resolved against the CALLER'S CWD; `flash.lsp
# --selftest` reads the minishop fixture and died three layers away from the absent
# directory with `ValueError: substring not found`, which names no path at all. The
# second of those is why the sweep classifies by re-running with the data PRESENT
# rather than by reading the error text. Tourney's path is now anchored at the
# package, and both are refusals rather than tracebacks.
VECTOR_DATA = {"grammar": "benchmarks/tasks/mw_tasks.jsonl",
               "debug": "benchmarks/tasks/dbg_tasks.jsonl",
               "patches": "benchmarks/fixtures/minishop",
               "graph": "benchmarks/fixtures/minishop",
               "lsp": "benchmarks/fixtures/minishop",
               "tourney": "benchmarks/tasks/m3_hard_tasks.jsonl"}


def missing_vector_data(mod: str, root: Path) -> Path | None:
    """The path `python -m flash.<mod> --selftest` needs and this install does not
    have, or `None` when it is all there. `root` is a parameter for the same reason
    `install_shape`'s is: the vector points it at synthetic wheel- and source-shaped
    trees, so the answer cannot quietly become "fine" for both."""
    rel = VECTOR_DATA.get(mod)
    if rel is None:
        return None
    path = root / rel
    return None if path.exists() else path


def vector_refusal(mod: str, root: Path) -> int | None:
    """Print the "this is not a tree this vector can run in" page and return the rc
    it commits to, or `None` when the data is present and the selftest should go
    ahead. Exit 2, not 1: a count of failures here would invite someone to diff a
    run that never happened."""
    missing = missing_vector_data(mod, root)
    if missing is None:
        return None
    print(f"flash.{mod} --selftest cannot run here: it reads {missing}, and that "
          f"path does not exist.\n"
          f"  A wheel install carries the package only, no `benchmarks/`, so part "
          f"of this vector would report a failure that is an absent directory "
          f"wearing one. Run it from a clone or an unpacked sdist (both ship "
          f"`benchmarks/`). To check the install itself: `flash doctor`, "
          f"`flash power`, `python -m flash.harness --selftest`.")
    return 2



def install_shape(pkg_dir: Path | None = None) -> dict:
    """Where this `flash` lives, and what is beside it.

    A parameter rather than a module constant on purpose: the answer is a
    property of ONE install, and `benchmarks/backend_free_check.py` gates this
    function against synthetic trees — a wheel-shaped one with no `benchmarks/`,
    a source-shaped one with it — so the report cannot learn to answer "fine"
    about both.
    """
    here = Path(pkg_dir or Path(flash.__file__).resolve().parent)
    root = here.parent
    data = root / "benchmarks"
    return {
        "package_dir": str(here),
        "root": str(root),
        "has_benchmarks": data.is_dir(),
        "has_battery": (data / "battery_reread.py").is_file(),
        "has_git": (root / ".git").exists(),
        "editable": _is_editable(here),
        "tasks": len(list((data / "tasks").glob("*.jsonl"))) if data.is_dir() else 0,
    }


def _is_editable(pkg_dir: Path) -> bool:
    """True when `import flash` resolved OUTSIDE a `site-packages` directory.

    This used to ask whether `Path(sys.executable).resolve()`'s `lib/` is a prefix
    of the package path, and on a Mac venv that symlink points at the Homebrew
    interpreter — so a wheel installed into `venv/lib/python3.11/site-packages/flash`
    answered "editable checkout" to a user who had no checkout. Measured in exactly
    that install; the answer is now read off the path it is a property of.
    """
    resolved = str(Path(pkg_dir).resolve())
    return "site-packages" not in resolved and "dist-packages" not in resolved


def backend() -> dict:
    """Can this box generate, and if not, what exactly is missing."""
    out: dict = {"mlx": None, "mlx_lm": None, "error": None}
    try:
        import mlx.core as mx                              # noqa: F401
        out["mlx"] = getattr(mx, "__version__", None) or "present"
    except Exception as e:                                 # noqa: BLE001
        out["error"] = f"import mlx.core: {type(e).__name__}: {e}"
        out["has_backend"] = False
        return out
    try:
        import mlx_lm                                      # noqa: F401
        out["mlx_lm"] = getattr(mlx_lm, "__version__", None) or "present"
    except Exception as e:                                 # noqa: BLE001
        out["error"] = f"import mlx_lm: {type(e).__name__}: {e}"
    out["has_backend"] = out["mlx_lm"] is not None
    return out


def model_cache() -> dict:
    """Which §22 candidates are already on disk, so a download is not a surprise."""
    hub = Path(os.environ.get("HF_HOME")
               or str(Path.home() / ".cache" / "huggingface" / "hub"))
    if not hub.is_dir():
        return {"dir": str(hub), "present": False, "models": [],
                "matrix_have": [], "matrix_missing": list(MATRIX)}
    names = sorted(p.name.replace("models--", "").replace("--", "/")
                   for p in hub.glob("models--*"))
    have = [m for m in MATRIX if any(m.lower() in n.lower() for n in names)]
    return {"dir": str(hub), "present": True, "models": names,
            "matrix_have": have, "matrix_missing": [m for m in MATRIX
                                                    if m not in have]}


def vector_tools() -> dict:
    """The imports the offline surface needs. Absent ones are named: `pyflakes`
    is a gate in this repo, so an install without it is a different install."""
    out = {}
    for mod in VECTOR_TOOLS:
        try:
            __import__(mod)
            out[mod] = True
        except Exception:                                  # noqa: BLE001
            out[mod] = False
    return out


def has_vector_tools(a: dict) -> bool:
    """The verdict this section feeds: every module the offline surface imports
    came back True. A separate function so a build that hardcodes the answer can
    be told apart from one that read it — `backend_free_check.py` puts an
    unimportable name into `VECTOR_TOOLS` and requires this to say no."""
    return all(a["tools"].values())


def machine() -> dict:
    """Python, platform, and what the governor says this box may load right now."""
    m = {"python": platform.python_version(), "platform": sys.platform,
         "machine": platform.machine(),
         "os": " ".join(platform.mac_ver()[:1]).strip()
         or platform.platform(terse=True),
         "may_load": None, "why": None}
    try:
        from flash import power
        st, caps = power.governor(SMALL_TIER_GB, refresh=True)
        total = st.mem_total_gb or 0.0
        free = (st.mem_free_pct or 0.0) / 100 * total
        m["may_load"] = (f"profile {caps.profile}, up to {caps.max_model_gb} GB "
                         f"right now (~{free:.1f} GB free of {total:.0f}, "
                         f"tournament width {caps.tournament_width})")
        m["why"] = "; ".join(caps.reasons) or "nothing holding the profile back"
    except Exception as e:                                 # noqa: BLE001
        m["why"] = f"governor unreadable: {type(e).__name__}: {e}"
    return m


def answers() -> dict:
    return {"version": flash.__version__, "shape": install_shape(),
            "backend": backend(), "cache": model_cache(),
            "tools": vector_tools(), "machine": machine()}


def _py_at_least(v: str, floor: tuple[int, int]) -> bool:
    """Is this interpreter at least `floor`? `pyproject.toml` declares the same
    floor, and the docs repeat it, so the report checks it against the running
    interpreter instead of trusting either."""
    try:
        parts = tuple(int(x) for x in v.split("+")[0].split(".")[:2])
    except ValueError:
        return False
    return len(parts) == 2 and parts >= floor


def verdict(a: dict) -> list[tuple[str, bool | None, str]]:
    """(section, ok, line). `None` marks an answer that is neither good nor bad —
    a fact about the machine the reader has to weigh, like a model nobody
    downloaded yet, or a governor this platform cannot read."""
    sh, be, ca, to, ma = (a["shape"], a["backend"], a["cache"], a["tools"],
                          a["machine"])
    py_ok = _py_at_least(ma["python"], PY_FLOOR)
    out: list[tuple[str, bool | None, str]] = [
        ("install", True,
         f"flash {a['version']} at {sh['package_dir']} "
         f"({'editable checkout' if sh['editable'] else 'installed copy'})"),
        ("install", sh["has_benchmarks"],
         "verification surface beside the package: benchmarks/"
         + (f" present, {sh['tasks']} task corpora" if sh["has_benchmarks"]
            else " ABSENT")),
        ("backend", be["has_backend"],
         "MLX backend: " + (f"mlx {be['mlx']}, mlx_lm {be['mlx_lm']}"
                            if be["has_backend"]
                            else f"NOT available — {be['error']}")),
        ("machine", py_ok,
         f"python {ma['python']} · {ma['platform']}/{ma['machine']} · {ma['os']}"
         + ("" if py_ok else f" — this project needs {'.'.join(map(str, PY_FLOOR))}+")),
        ("machine", None if ma["may_load"] is None else True,
         "what this box may load: "
         + (ma["may_load"] or f"unmeasurable here — {ma['why']}")),
        ("cache", None, f"model cache {ca['dir']}: {len(ca['models'])} repo(s), "
                        f"{len(ca['matrix_have'])} of {len(MATRIX)} §22 "
                        f"candidates present"),
    ]
    if ca["matrix_missing"]:
        out.append(("cache", None, "  not downloaded: "
                    + ", ".join(ca["matrix_missing"][:4])))
    missing = [k for k, v in to.items() if not v]
    out.append(("tools", has_vector_tools(a),
                "offline-surface imports: "
                + ("all four present" if not missing
                   else "missing " + ", ".join(f"{k} ({VECTOR_TOOLS[k]})"
                                               for k in missing))))
    out.append(("verify", sh["has_battery"],
                "the offline battery "
                + ("runs here: `flash selftest --all`" if sh["has_battery"]
                   else "CANNOT run from this install — no benchmarks/ beside the "
                       "package. Clone the repo, or unpack the sdist (which ships "
                       "it), then run it there")))
    out.append(("verify", be["has_backend"],
                "generation "
                + ("runs here: `flash power`, then `flash run <task>`"
                   if be["has_backend"]
                   else "CANNOT run here. Every other half of this project still "
                        "does — including the whole offline battery")))
    return out


def report(a: dict) -> str:
    rows = verdict(a)
    lines = ["flash doctor — what this install can and cannot do", ""]
    last = None
    for section, ok, line in rows:
        if section != last:
            lines.append(f" {section}")
            last = section
        mark = "yes" if ok is True else ("no " if ok is False else "   ")
        lines.append(f"  [{mark}] {line}")
    no = sum(1 for _, ok, _ in rows if ok is False)
    lines.append("")
    lines.append(f"{no} answer(s) say no: this install cannot do something the "
                 "project can do. Each of those lines names its own remedy."
                 if no else
                 "Both halves this project claims to have are present here.")
    return "\n".join(lines)


def add_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true",
                   help="the same answers as an object, for a script to read")


def dispatch(a: argparse.Namespace) -> int:
    data = answers()
    if a.json:
        print(json.dumps(data, indent=1))
        return 0
    print(report(data))
    return 1 if any(ok is False for _, ok, _ in verdict(data)) else 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="flash doctor",
                                 description=__doc__.split("\n")[0])
    add_flags(ap)
    return dispatch(ap.parse_args(argv))


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
