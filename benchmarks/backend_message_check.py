"""R-7.17, OFFLINE: does a box with no backend refuse in a sentence, everywhere?

SPEC R-7.7's clause is that a machine which cannot generate must still import
everything and must say what is missing in words a person can act on. The import
half has a gate — `backend_free_check.py` sweeps every submodule of `flash` with
the backend blocked and requires the numerator to equal the denominator. The
refusal half did not, and the gap was reproducible on Linux:

    $ flash session --context . --turns 1
    Traceback (most recent call last):
      File ".../flash/loop.py", line 875, in solve_routed
        import mlx.core as mx
    ModuleNotFoundError: No module named 'mlx.core'

`flash/loop.py:875` is a bare import at the top of the routed solver, and every
generation path had one of its own. The import sweep cannot see this: it imports
modules, and a module-level import was already made clean by R-7.7. What stayed
unclean was the call.

So this vector does for the entry points what the sweep does for imports:

- blocks `mlx`, `mlx_lm` and `mlx_vlm` with the shipped `--backend-free` shim —
  the same object `battery_reread` writes for its own lane, read from
  `backend_free_check.shim_env()` rather than restated here, because a copy of
  the shim in this file could keep passing while the real flag rotted;
- proves the block bites in a child before believing any refusal produced under
  it, exactly as `--backend-free` demands of itself;
- runs EVERY command whose job is to make a model answer through the real
  parser and the real dispatch in a child, and requires of each: the sentence, a
  non-zero exit, no `Traceback` anywhere, and the refusal printed EXACTLY ONCE —
  a message that arrives once per retry attempt is not "one clean message";
- requires the refusal to quote the sentence `flash doctor` prints on that same
  blocked box, read back from `flash doctor` itself, because a message that
  tells you to run doctor and then paraphrases it is two sources for one fact;
- requires the remedy line to be the one THIS platform can act on, and runs
  `remedy()` under each of the three platform names, since the Linux and Windows
  branches are ones this box can never take by itself. Since R-7.18 the non-Apple
  branches have a second half to print: `pip install 'flash-coder[local]'` and
  `flash models pull`, because "no install line fixes this box" was only true while
  MLX was the only thing that could answer;
- requires every `flash-coder[extra]` name the package prints to be an extra
  `pyproject.toml` declares, sweeping the names out of `flash/*.py` rather than
  keeping a list here — which is how `[mac]`, printed by `remedy()` since R-7.17,
  was found undeclared;
- requires that no `cmd_*` in `flash/cli.py` imports a model library of its own,
  read off the AST — the seam is the only door, which is the property that lets a
  command be added without remembering the guard;
- probes the library entry points (`flash.learn`, `flash.vision`) too, because a
  caller that imports them never passes through the CLI's catch;
- and calls `cmd_session` in-process with the backend blocked — the reproduction
  from the report above, asserted rather than described — requiring that the
  exception that reaches the caller is `NoBackend`, not `ModuleNotFoundError`.

Every child runs with `trace.DIR` moved into a temp directory: a session that
dies before its first turn would otherwise leave a `session_start` with no
`session_end` in the tracked trace store, which §33.7 reads as an interrupted
suite to resume forever.

The mutants are the point of the file: `selected()`'s probe deleted so the MLX loader
is a bare `from mlx_lm import load` again (the shipped defect, one notch lower now
that the probe lives in the seam), the dispatch catch pointed at the wrong exception,
a command going back to `from mlx_lm import load`, a diagnosis with no remedy, a
doctor line hardcoded instead of read, a Linux box told to `pip install` MLX, and a
remedy that prints an extra `pyproject.toml` never declared. Each of those seven is
green without this gate.

    python benchmarks/backend_message_check.py             # gates + mutants
    python benchmarks/backend_message_check.py --mutant NAME
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

import backend_free_check as free                            # noqa: E402

REFUSAL = "flash: this install has no generation backend"
TB = "Traceback (most recent call last)"

#: Every `flash` command whose job is to make a model answer text.
#: `run-vis-suite` is here because `mlx_vlm` is a backend too and its absence is
#: the same defect on the same line; `decide`/`decide-batch` because R-7.7's
#: original finding was that `flash.decide` broke at `import mlx.core`.
COMMANDS = {
    "session": (["session", "--context", "{repo}", "--turns", "1"], "fix it\n"),
    "run": (["run", "fix the off-by-one", "--test", "{test}"], None),
    "run-suite": (["run-suite", "--max-tasks", "1"], None),
    "solve": (["solve", "write a fizzbuzz"], None),
    "solve-all": (["solve-all", "--max-tasks", "1"], None),
    "escalate-test": (["escalate-test"], None),
    "run-vis-suite": (["run-vis-suite", "--max-tasks", "1"], None),
    "decide": (["decide", "refactor this", "--options", "bugfix", "feature"], None),
    "decide-batch": (["decide-batch"], None),
    "route-test": (["route-test"], None),
}

CHECKS: list[tuple[str, bool, str]] = []


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


#: A child that runs the real CLI — parser, dispatch and all — with the trace
#: store pointed at a directory the CALLER made, so a refusal cannot write into
#: the tracked tree and the record it does leave can be read back here.
_WRAPPER = '''
import sys
from pathlib import Path
sys.path.insert(0, {root!r})
from flash import trace
trace.DIR = Path({trace_dir!r})
from flash import cli
sys.argv = ["flash"] + {args!r}
raise SystemExit(cli.main())
'''


def child(args: list[str], env: dict, stdin: str | None,
          cwd: Path | None = None) -> subprocess.CompletedProcess:
    tdir = Path(tempfile.mkdtemp(prefix="flash-msg-trace-"))
    src = _WRAPPER.format(root=str(ROOT), args=args, trace_dir=str(tdir))
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, env=env, cwd=cwd or ROOT, input=stdin,
                       timeout=300)
    r.trace_dir = tdir
    return r


def last_record(r: subprocess.CompletedProcess) -> dict:
    """The final line of the trace file this child wrote, parsed — or {}."""
    files = sorted(r.trace_dir.glob("*.jsonl"))
    if not files:
        return {}
    lines = [l for l in files[0].read_text().splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else {}


def sandbox() -> Path:
    """A one-file repo and a failing oracle, so `session` and `run` get past
    their argument checks and reach the import that is the thing under test."""
    d = Path(tempfile.mkdtemp(prefix="flash-backend-msg-"))
    (d / "app.py").write_text("def f(x):\n    return x + 1\n")
    (d / "test_oracle.py").write_text(
        "from app import f\n\n\ndef test_f():\n    assert f(1) == 3\n")
    return d


def refuse(cmd: str, env: dict, repo: Path) -> subprocess.CompletedProcess:
    args, stdin = COMMANDS[cmd]
    args = [a.replace("{repo}", str(repo)).replace("{test}",
            str(repo / "test_oracle.py")) for a in args]
    return child(args, env, stdin)


def blocks_backend(env: dict) -> bool:
    """The shim has to bite in a child before any refusal under it is evidence."""
    r = subprocess.run([sys.executable, "-c", "import mlx.core"],
                       capture_output=True, text=True, env=env, cwd=ROOT,
                       timeout=120)
    return r.returncode != 0 and "--backend-free" in (r.stderr + r.stdout)


def doctor_backend_line(env: dict) -> str:
    """What `flash doctor` itself prints about the backend on the blocked box,
    taken from the report rather than from this file's idea of what it says."""
    out = child(["doctor"], env, None)
    text = out.stdout + out.stderr
    i = text.find("MLX backend")
    return text[i:].splitlines()[0].strip() if i >= 0 else ""


def in_process_session(env: dict, repo: Path) -> str:
    """`cmd_session` called directly, with the backend blocked — the Linux
    report's own call path, run here instead of described."""
    src = f'''
import sys, tempfile
from argparse import Namespace
from pathlib import Path
sys.path.insert(0, {str(ROOT)!r})
from flash import cli, trace
from flash.backend import NoBackend
trace.DIR = Path(tempfile.mkdtemp(prefix="flash-msg-trace-"))
a = Namespace(test={str(repo / "test_oracle.py")!r}, context={str(repo)!r},
              turns=1, adapter="", constrain=False, debug=False,
              no_source_hint=[], no_graph_hint=[], trace_full=False,
              small="x/y", big="x/y", allow_big="auto", attempts=1,
              max_tokens=8, tournament=1, confidence=False, apply=False)
try:
    rc = cli.cmd_session(a)
    print(f"OUTCOME=returned-{{rc}}")
except NoBackend as e:
    print("OUTCOME=NoBackend")
    print(str(e))
except BaseException as e:
    print(f"OUTCOME={{type(e).__name__}}")
    import traceback; traceback.print_exc()
'''
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, env=env, cwd=ROOT, input="fix it\n",
                       timeout=300)
    return r.stdout + r.stderr


def direct_model_imports() -> list[str]:
    """`cmd_<name>:<line> <module>` for a CLI command that imports a model itself.

    Read off `flash/cli.py`'s AST rather than from a list in this file, because
    the defect this vector closes is precisely a command saying
    `from mlx_lm import load` on its own — which is what all five of the old load
    sites did, and what a sixth would do again. Going through `flash.backend` is
    not an import of a model library; that module IS the seam, and it is the one
    place the probe lives.
    """
    import ast
    tree = ast.parse((ROOT / "flash" / "cli.py").read_text())
    out: list[str] = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name.startswith("cmd_")]:
        for n in ast.walk(fn):
            mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                    else [n.module or ""] if isinstance(n, ast.ImportFrom) else [])
            for m in mods:
                if m.split(".")[0] in ("mlx", "mlx_lm", "mlx_vlm"):
                    out.append(f"{fn.name}:{n.lineno} {m}")
    return sorted(out)


def printed_extras() -> list[str]:
    """Every `flash-coder[extra]` name the shipping code can print, swept from the
    package rather than typed into this file.

    A hand-written list would be a second copy of the thing it checks, and the bug it
    exists for — a remedy that names an extra nobody declared, which pip answers with
    "Does not provide the extra" — is precisely the bug a hand-written list cannot
    see, because the list would be wrong in the same direction.
    """
    return sorted({m for p in sorted((ROOT / "flash").glob("*.py"))
                   for m in re.findall(r"flash-coder\[(\w+)\]",
                                       p.read_text(errors="ignore"))})


def declared_extras() -> set[str]:
    """The keys of `pyproject.toml`'s `[project.optional-dependencies]` — the table
    `pip install …` actually answers from."""
    with open(ROOT / "pyproject.toml", "rb") as f:
        return set(tomllib.load(f)["project"]["optional-dependencies"])


def remedy_by_platform(env: dict) -> dict:
    """`backend.remedy()` as each of three platforms, from one child process."""
    src = (f"import sys\nsys.path.insert(0, {str(ROOT)!r})\n"
           "from flash.backend import remedy\n"
           'for plat in ("darwin", "linux", "win32"):\n'
           "    sys.platform = plat\n"
           '    print(plat + "|" + remedy().replace("\\n", " "))\n')
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, env=env, cwd=ROOT, timeout=120)
    return {l.split("|", 1)[0]: l.split("|", 1)[1]
            for l in (r.stdout + r.stderr).splitlines() if "|" in l}


def library_refusals(env: dict) -> dict:
    """Which exception escapes each LIBRARY generation entry point, blocked.

    The CLI sweep above cannot see these: a caller that imports `flash.learn` or
    `flash.vision` directly never goes through `main()`'s catch. `flash.jobs` is
    deliberately not probed here — its load sits behind `eligibility()`, which
    refuses a gated box long before a model is wanted, so a call would test the
    gate rather than the guard.
    """
    src = f'''
import sys
sys.path.insert(0, {str(ROOT)!r})
from flash import backend, learn, vision


def probe(name, fn):
    try:
        fn()
        print(f"{{name}}=returned")
    except backend.NoBackend:
        print(f"{{name}}=NoBackend")
    except BaseException as e:
        print(f"{{name}}={{type(e).__name__}}")


probe("learn.embed_text", lambda: learn.embed_text(None, None, "hi"))
probe("learn.embed_prompts",
      lambda: learn.embed_prompts([{{"prompt": "hi", "id": "t1"}}], "x/y"))
probe("vision.load_vlm", lambda: vision.load_vlm("x/y"))
'''
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, env=env, cwd=ROOT, timeout=300)
    return dict(l.split("=", 1) for l in (r.stdout + r.stderr).splitlines()
                if "=" in l)


def run_gates() -> None:
    env = free.shim_env()
    repo = sandbox()

    ck("the blocker bites in a child before any refusal under it is believed: "
       "`import mlx.core` fails with the shipped shim's own sentence",
       blocks_backend(env), "child import")

    line = doctor_backend_line(env)
    ck("`flash doctor` still answers on a box with no backend, and names which "
       "half is missing", "MLX backend: NOT available" in line, line or "no line")

    outs = {c: refuse(c, env, repo) for c in COMMANDS}
    text = {c: r.stdout + r.stderr for c, r in outs.items()}

    named = sorted(c for c, t in text.items() if REFUSAL in t)
    ck(f"all {len(COMMANDS)} generating commands refuse in a sentence: "
       + ", ".join(sorted(COMMANDS)) if len(named) == len(COMMANDS) else
       f"only {len(named)}/{len(COMMANDS)} refuse in a sentence",
       named == sorted(COMMANDS),
       "no sentence: " + ", ".join(sorted(set(COMMANDS) - set(named))))

    tb = sorted(c for c, t in text.items() if TB in t)
    ck("not one of them prints a traceback", not tb,
       "traceback from: " + ", ".join(tb))

    bad_rc = sorted(c for c, r in outs.items() if r.returncode == 0)
    ck("every refusal exits non-zero, so a script cannot mistake it for a run",
       not bad_rc, "rc 0: " + ", ".join(bad_rc))

    ends = {c: last_record(outs[c]) for c in ("session", "run", "run-suite")}
    ck("a refusal leaves no resumable session behind: §33.7 reads a trace with no "
       "`session_end` as a suite to resume, so the three commands that open one "
       "close it and record the reason",
       all(e.get("type") == "session_end" and e.get("refused")
           and e.get("solved") is False for e in ends.values()),
       str({c: e.get("type", "no record") for c, e in ends.items()}))

    silent = sorted(c for c, t in text.items() if "MLX" not in t)
    ck("every refusal names MLX, the thing the reader has to go install",
       not silent, "silent on MLX: " + ", ".join(silent))

    once = sorted(c for c, t in text.items() if t.count(REFUSAL) != 1)
    ck("the refusal is ONE message per command, not one per retry attempt or "
       "per hot-swap", not once,
       "repeated or absent: " + ", ".join(f"{c}×{text[c].count(REFUSAL)}"
                                          for c in once))

    diverged = sorted(c for c, t in text.items() if line not in t)
    ck("each refusal quotes `flash doctor`'s own backend sentence, read from "
       "`flash doctor` on this box, not a paraphrase of it",
       bool(line) and not diverged,
       "diverged: " + ", ".join(diverged) if diverged else line)

    rem = [c for c, t in text.items() if "remedy" not in t]
    ck("the refusal carries a remedy line, so it is not only a diagnosis",
       not rem, "no remedy: " + ", ".join(rem))

    miss = {}
    for c in COMMANDS:
        why_line = next((l for l in text[c].splitlines()
                         if "what is missing" in l), "")
        found = re.search(r"import ([\w.]+):", why_line)
        miss[c] = found.group(1) if found else ""
    ck("each refusal names the import that actually failed: the vision command "
       "is the only one that says mlx_vlm, and no command refuses on a package "
       "it never imports",
       miss["run-vis-suite"] == "mlx_vlm"
       and {m for c, m in miss.items() if c != "run-vis-suite"} <= {"mlx.core",
                                                                    "mlx_lm"},
       str(miss))

    # remedy()'s other two branches are the ones a Linux or Windows user reads,
    # and this box cannot run them, so the function is executed once per platform
    # name rather than left as a code path no gate has ever seen. A CHILD does it:
    # `backend_free_check` imports `flash.cli`, which imports `flash.backend`, so
    # this process already holds the module and a mutant written to disk would
    # never be read. The child loads the file as it stands.
    printed, declared = printed_extras(), declared_extras()
    by_platform = remedy_by_platform(env)
    ck("remedy() answers for the three platforms separately: only Apple Silicon is "
       "handed the MLX install line, and Linux and Windows are told MLX cannot be "
       "installed there — and since R-7.18 they are ALSO handed the route that does "
       "work, because 'no install line fixes this box' became false the day the "
       "seam grew a second backend, and a refusal that names only the closed door is "
       "a dead end a reader cannot walk out of",
       len(by_platform) == 3
       and "flash-coder[mac]" in by_platform.get("darwin", "")
       and all("flash-coder[mac]" not in by_platform.get(p, "")
               and "does not build outside" in by_platform.get(p, "")
               and "flash-coder[local]" in by_platform.get(p, "")
               and "flash models pull" in by_platform.get(p, "")
               and "--backend llama.cpp" in by_platform.get(p, "")
               for p in ("linux", "win32")),
       " | ".join(f"{p}: {by_platform[p][:44]}" for p in sorted(by_platform)))

    ck("every `flash-coder[extra]` name the shipping code prints is an extra "
       "pyproject.toml actually declares — a remedy line that names an extra nobody "
       "wrote is worse than no remedy, because the reader types it and pip refuses. "
       "The names are swept out of every module that prints one rather than kept in "
       "a hand-written list, and the sweep refuses to be green because it collected "
       "nothing: `[mac]` and `[local]` must both show up",
       sorted(set(printed) - declared) == []
       and {"mac", "local"} <= set(printed),
       f"printed {sorted(printed)} declared {sorted(declared)}")

    dar = platform.system() == "Darwin"
    body = text["session"]
    ck("and the branch THIS box takes is the actionable one: "
       + ("Apple Silicon got the [mac] install line" if dar
          else "elsewhere got the sentence saying no install line fixes it"),
       ("flash-coder[mac]" in body) if dar else ("does not build outside" in body),
       next((l for l in body.splitlines() if "remedy" in l), body[:160]))

    direct = in_process_session(env, repo)
    ck("cmd_session called in-process with the backend blocked raises NoBackend "
       "where the Linux report got ModuleNotFoundError",
       "OUTCOME=NoBackend" in direct,
       next((l for l in direct.splitlines() if l.startswith("OUTCOME")),
            direct[:200]))
    ck("…and that call prints no traceback", TB not in direct)

    lib = library_refusals(env)
    ck("the library entry points a caller reaches without `main()`'s catch "
       "refuse the same way: " + ", ".join(f"{k}→{v}" for k, v in sorted(lib.items())),
       len(lib) == 3 and all(v == "NoBackend" for v in lib.values()),
       str(lib))

    d, p = child(["doctor"], env, None), child(["power"], env, None)
    ck("the guard leaves the offline half alone: `flash power` still exits 0 and "
       "`flash doctor` still prints its whole report — exiting 1 because a [no] "
       "row has to move the code, which is R-7.6's own rule",
       p.returncode == 0 and d.returncode == 1
       and d.stdout.startswith("flash doctor")
       and "selftest --all" in d.stdout and TB not in (d.stdout + d.stderr),
       f"power rc={p.returncode}, doctor rc={d.returncode}")

    sites = direct_model_imports()
    ck("and the seam is the only way a command reaches a model: no `cmd_*` in "
       "`flash/cli.py` imports `mlx`, `mlx_lm` or `mlx_vlm` itself, which is "
       "exactly what the load sites this vector re-routed used to do",
       not sites, "; ".join(sites))


# ---------------------------------------------------------------- mutants

#: (label, [(path, old, new)]) — each restores a plausible-looking way to ship
#: the original defect.
MUTANTS = {
    "guard_removed": ("the load site imports mlx with no probe in front of it — "
                      "the shipped defect, restored one notch lower now that the "
                      "probe lives in the seam: `selected()` stops asking the box "
                      "what it can run, and the MLX loader goes back to a bare "
                      "`from mlx_lm import load`",
                      [("flash/backend.py",
                        '    if _FORCED:\n        return REGISTRY[_FORCED]\n',
                        '    return REGISTRY["mlx"]\n'),
                       ("flash/backend.py",
                        '        require("mlx.core", "mlx_lm")\n'
                        '        from mlx_lm import load as _load\n',
                        '        from mlx_lm import load as _load\n')]),
    "catch_removed": ("main() catches the wrong exception, so the user sees a "
                      "stack trace again",
                      [("flash/cli.py", "    except NoBackend as e:",
                        "    except KeyboardInterrupt as e:")]),
    "message_without_remedy": ("the refusal diagnoses and never says what to do",
                               [("flash/backend.py",
                                 '        f"  remedy           {remedy()}",\n',
                                 "")]),
    "doctor_line_paraphrased": ("the message invents what doctor should say "
                                "instead of reading doctor",
                                [("flash/backend.py",
                                  "    from flash import doctor\n"
                                  "    return doctor.backend_line(doctor.backend())",
                                  "    return 'MLX backend: NOT available — "
                                  "probably fine'")]),
    "wrong_platform_remedy": ("a Linux box is told to pip-install MLX",
                              [("flash/backend.py",
                                '    if sys.platform == "darwin":',
                                '    if True:')]),
    "command_imports_the_model_itself": ("a CLI command goes back to "
                                         "`from mlx_lm import load` instead of "
                                         "asking the seam — the original shape "
                                         "of the defect, in the file that "
                                         "forgets the guard",
                                         [("flash/cli.py",
                                           "def cmd_decide(args) -> int:\n"
                                           "    from flash.backend import load",
                                           "def cmd_decide(args) -> int:\n"
                                           "    from mlx_lm import load")]),
    "remedy_names_an_undeclared_extra": ("the Apple-Silicon remedy tells the reader "
                                         "to install an extra that is not in "
                                         "pyproject.toml — which is the state this "
                                         "repo shipped in, with `[mac]` printed by "
                                         "`remedy()` and only `[local]` declared",
                                         [("pyproject.toml",
                                           'mac = ["mlx-lm',
                                           'mack = ["mlx-lm')]),
}


def _apply(patches):
    """Patch every file this mutant touches, and return what to write back.

    Keyed by PATH, first text wins: a mutant with two patches on one file reads
    that file a second time AFTER the first patch landed, so saving per patch
    would restore the mid-mutant copy and leave the other half of the mutation in
    the tree — which is exactly how `selected()` ended up returning MLX
    unconditionally in a committed tree.
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


def mutate(one: str | None = None) -> int:
    # Every file any mutant touches, captured before the first patch: a run that
    # ends with a mutation still in the checkout is a run whose fractions describe
    # code nobody can re-read. `guard_removed` is a TWO-patch mutant, which is the
    # shape that left half a mutation in a committed tree before (`62d547f`).
    pristine = {(ROOT / rel): (ROOT / rel).read_bytes()
                for _, patches in MUTANTS.values() for rel, _, _ in patches}
    ran = failed = 0
    for name in ([one] if one else sorted(MUTANTS)):
        label, patches = MUTANTS[name]
        CHECKS.clear()
        saved = _apply(patches)
        try:
            run_gates()
        finally:
            for p, text in saved:
                p.write_text(text)
        left = [str(p.relative_to(ROOT)) for p, b in pristine.items()
                if p.read_bytes() != b]
        bad = [l for l, ok, _ in CHECKS if not ok]
        ran += 1
        print(f"  {'ok  ' if bad and not left else 'FAIL'} MUTATION: {label} -> "
              f"{len(bad)} check(s) fail" + (f"  {bad[0]}" if bad else "")
              + (f"  MUTANT LEFT IN TREE: {left}" if left else ""))
        failed += not bad or bool(left)
    print(f"\nbackend-message mutants: {ran - failed}/{ran} gates defeated by "
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
    print(f"\nbackend-message checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
