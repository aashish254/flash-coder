"""R-7.8, OFFLINE: does every command a document tells a stranger to type exist?

This repo has shipped the same defect twice in different clothes: `CONTRIBUTING.md`
put `flash doctor` and `flash selftest --all` inside a copy-pasteable setup block
while neither command existed, and the CHANGELOG's own release notes listed
`flash --version` as shipped. Both are invisible to every other vector here —
nothing executes a README — and both are the first thing a person who downloads
this project does: paste the block, get an argparse error, and stop trusting the
numbers on the page.

So the claim is checked against the parser rather than against prose:

- collect every command a tracked document tells a reader to run, from the places
  a command is written as a command (a code span, a fenced block, a CI `run:`),
  not from sentences that merely mention "flash";
- require each one to resolve in `flash.cli.build_parser()`;
- require each file a document points at (`benchmarks/x.py`, `docs/y.md`) to
  exist;
- and gate the collector itself, because a scanner that scans nothing is the same
  failure one level up: it is fed a temp document with a command nobody wrote and
  must report finding it and rejecting it.

    python benchmarks/documented_commands_check.py            # gates + mutants
    python benchmarks/documented_commands_check.py --print    # the table
"""
from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import cli                                            # noqa: E402

DOCS = ("README.md", "SPEC.md", "TODO.md", "CHANGELOG.md", "CONTRIBUTING.md",
        "SECURITY.md", "CODE_OF_CONDUCT.md",
        # The two files whose only prose is comments. A stranger reads
        # `pyproject.toml` to decide which extra to install, and one of its
        # comments named a command that has never existed.
        "pyproject.toml", "MANIFEST.in")
DOC_GLOBS = ("docs/*.md", ".github/workflows/*.yml", ".github/**/*.md")
# A command is written where a command is typed: inside `code`, inside a fenced
# block, or on a CI `run:` line. Sentences about "flash" are not commands, and
# matching them would let prose decide what the parser has to contain. The
# `python` arm is only the module form: `python benchmarks/x.py` runs a script,
# and its path is checked as a file, not as a subcommand.
SPAN = re.compile(r"`([^`\n]+)`")
FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)
CMD = re.compile(r"(?:^|[;&|]\s*)(?:\$ )?(?:python3?(?:\.\d+)? -m flash\.cli|flash)"
                 r"\s+([a-z][a-z0-9-]*)")
FLASH_ONLY = re.compile(r"(?:^|[;&|]\s*)(?:\$ )?flash\s+([a-z][a-z0-9-]*)")
FILE_REF = re.compile(r"\b((?:benchmarks|docs|flash|\.github)/[\w./-]*\.(?:py|md|jsonl|"
                      r"yml|toml|txt|log))\b")
# Records a run writes, cited by documents that describe the run. A doc pointing
# at `benchmarks/results/ci/nightly-live.log` is naming an output — the workflow
# `mkdir -p`s that directory and `tee`s into it — so requiring the file to be in
# the tree would gate the wrong thing. Source files a reader is told to open or
# run are not exempt.
REF_EXEMPT = ("benchmarks/results/",)
# `python -m flash.cli <cmd>` and `python benchmarks/m0_bakeoff.py`: the module
# form's own word is "flash", which is not a subcommand.
NOT_A_COMMAND = {"flash", "cli", "coder", "help", "the", "and", "is", "of", "to",
                 "for", "with", "on", "in", "as", "by", "it", "runs", "version"}


COMMENT_CMD = re.compile(r"^\s*[#;]\s*(flash\b.*)$")


def code_lines(text: str, is_yaml: bool, is_cfg: bool = False) -> list[tuple[int, str]]:
    """(line number, text) for every line worth parsing as a command.

    The line number is kept because the failure this file exists to catch is a
    sentence in a document, and a report that says "`selftest` does not exist"
    without saying which page tells a reader to type it is a hunt, not an answer.
    """
    def lineno(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    out: list[tuple[int, str]] = []
    for block in FENCE.finditer(text):
        base = lineno(block.start()) + 1
        out.extend((base + i, l) for i, l in enumerate(block.group(1).splitlines()))
    for span in SPAN.finditer(text):
        out.append((lineno(span.start()), span.group(1)))
    if is_cfg:
        # In a packaging file a comment IS the prose, and an un-backticked
        # `flash something` at the start of one reads as an instruction.
        for i, raw in enumerate(text.splitlines(), 1):
            m = COMMENT_CMD.match(raw)
            if m:
                out.append((i, m.group(1)))
    if is_yaml:
        # A `run:` block is typed by a machine on every push, so it counts.
        for m in re.finditer(r"run: \|\n((?:[ \t]+.*\n?)+)", text):
            base = lineno(m.start()) + 1
            out.extend((base + i, l.strip())
                       for i, l in enumerate(m.group(1).splitlines()))
        for m in re.finditer(r"run: (.+)", text):
            out.append((lineno(m.start()), m.group(1).strip()))
    return out


def collect(paths: list[Path]) -> dict[str, list[str]]:
    """command -> every `file:line` that told a reader to type it."""
    found: dict[str, list[str]] = {}
    for p in paths:
        text = p.read_text(errors="ignore")
        yaml = p.suffix == ".yml"
        cfg = p.suffix in (".toml", ".in")
        rel = str(p.relative_to(ROOT))
        for line_no, line in code_lines(text, yaml, cfg):
            line = line.strip()
            m = (CMD.match(line) or FLASH_ONLY.match(line))
            if not m:
                continue
            cmd = m.group(1)
            if cmd in NOT_A_COMMAND:
                continue
            found.setdefault(cmd, []).append(f"{rel}:{line_no}")
    return found


def doc_files() -> list[Path]:
    out = [ROOT / d for d in DOCS if (ROOT / d).is_file()]
    for g in DOC_GLOBS:
        out.extend(sorted(ROOT.glob(g)))
    return out


def resolves(cmd: str) -> tuple[bool, str]:
    """Does the parser accept this command, and if not, what did it say?

    argparse alone decides, and nothing is ever EXECUTED on the way to that
    answer: `flash ambient` or `flash learn` dispatched here would create a
    worktree or a job checkpoint while checking a document. `parse_args([cmd])`
    raises SystemExit before any `fn` runs, and its two failure shapes mean
    different things — "invalid choice" is a command that does not exist, "the
    following arguments are required" is a command that does.
    """
    ap = cli.build_parser()
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            ap.parse_args([cmd])
        return True, ""
    except SystemExit:
        text = err.getvalue()
        return "invalid choice" not in text, text.strip().splitlines()[-1][:120]


def exists_in_tree(ref: str) -> bool:
    return (ROOT / ref).is_file()


CHECKS: list[tuple[str, bool, str]] = []


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


def run_gates(print_table: bool = False) -> None:
    files = doc_files()
    commands = collect(files)
    flat = sorted(commands)
    cited = {c.split(":")[0] for v in commands.values() for c in v}
    ck(f"the collector has a spine: it found {len(flat)} distinct commands in "
       f"{sum(len(v) for v in commands.values())} citations across {len(cited)} "
       "documents, which is wide enough that 'every documented command exists' "
       "cannot be green because nothing was read",
       len(flat) >= 20 and len(cited) >= 6, f"{len(flat)} commands: {flat}")
    scanned = {str(p.relative_to(ROOT)) for p in files}
    pack_cites = sorted({cite for lst in commands.values() for cite in lst
                         if cite.split(":")[0] in ("pyproject.toml", "MANIFEST.in")})
    ck("the two packaging files are among the documents scanned, and commands "
       "actually come out of them — `pyproject.toml` and `MANIFEST.in` are what a "
       "stranger reads to decide what to install, their only prose is comments, "
       "and that is where `flash vision --run` sat for a whole release claiming "
       "to be a command",
       {"pyproject.toml", "MANIFEST.in"} <= scanned
       and any(c.startswith("pyproject.toml") for c in pack_cites),
       f"{len(pack_cites)} citation(s) from them: {pack_cites[:4]}")
    missing = {c: sorted(set(commands[c])) for c in flat if not resolves(c)[0]}
    ck(f"every one of those {len(flat)} commands resolves in "
       "`flash.cli.build_parser()` — the parser, not a copy of its help text",
       not missing, f"{missing}")
    if print_table:
        for c in flat:
            print(f"  ..  {c:16s} {'ok' if resolves(c)[0] else 'MISSING':8s} "
                  f"{sorted(set(commands[c]))[:3]}")

    refs = sorted({r for p in files
                   for r in FILE_REF.findall(p.read_text(errors="ignore"))
                   if not r.startswith(REF_EXEMPT)})
    gone = [r for r in refs if not exists_in_tree(r)]
    ck(f"every source file a document points a reader at exists ({len(refs)} paths "
       "collected from the same spans, records under `benchmarks/results/` exempt "
       "because a run writes them) — a `python benchmarks/x.py` that is not there "
       "is the same defect wearing a .py",
       not gone and len(refs) >= 10, f"{len(gone)} missing: {gone[:6]}")

    probes = ["flash nosuchcommand doctor", "python -m flash.cli alsobogus",
              "$ flash anotherfake --json"]
    seen = set()
    for line in probes:
        m = CMD.match(line) or FLASH_ONLY.match(line)
        if m:
            seen.add(m.group(1))
    ck("the collector finds a command it has never heard of, and the existence "
       "check rejects it — this vector's own instrument is gated, so it cannot "
       "go blind and still print a pass",
       {"nosuchcommand", "alsobogus", "anotherfake"} == seen
       and all(not resolves(c)[0] for c in seen), f"{sorted(seen)}")

    # Both published pipelines, not just the one people see first: nightly calls
    # `flash run-suite` with five flags, and a dangling command there is the same
    # defect with a longer fuse.
    wf = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    calls = [(m.group(1), f.name, where, line) for f in wf
             for where, line in code_lines(f.read_text(errors="ignore"), True)
             if (m := CMD.match(line.strip()) or FLASH_ONLY.match(line.strip()))]
    ck(f"every `flash` command line in the published workflows resolves "
       f"(found {len(calls)} across {len(wf)} files: "
       f"{sorted({c for c, _, _, _ in calls})}) — the pipelines a stranger sees on "
       "the repo's front page must not call a command that does not exist",
       len(calls) >= 4 and all(resolves(c)[0] for c, _, _, _ in calls),
       f"{[(c, f, w, l[:40]) for c, f, w, l in calls if not resolves(c)[0]]}")
    ck("...and the two commands R-7.6 added are among them, so the workflow that "
       "used to be dangling is now gated by this file and by "
       "`backend_free_check.py`",
       any(c == "doctor" for c, _, _, _ in calls)
       and any(c == "selftest" for c, _, _, _ in calls),
       f"{[c for c, _, _, _ in calls]}")

    version = subprocess.run([sys.executable, "-m", "flash.cli", "--version"],
                             cwd=ROOT, capture_output=True, text=True)
    ck("`flash --version` (top level, so not a subcommand) runs and prints the "
       f"package's version: {version.stdout.strip()!r}",
       version.returncode == 0 and version.stdout.strip().startswith("flash "),
       f"rc={version.returncode} err={version.stderr[-120:]}")


def mutate(one: str | None = None) -> int:
    """Five ways this vector can be green while telling a lie.

    The patches go through `globals()` because `run_gates` looks these names up as
    module attributes: assigning them in this function's scope would patch a local
    nobody reads, and the mutant would report a check caught that never ran.
    """
    bugs = {
        "blind_collector": ("the command pattern stops matching, so the collector "
                            "reads zero documents and 'every command exists' is "
                            "vacuously true",),
        "always_resolves": ("`resolves` returns True whatever the parser says, "
                            "which is exactly how `flash doctor` shipped inside a "
                            "setup block for a commit",),
        "no_ci_lane": ("the CI `run:` lines stop being collected, so the "
                       "published pipeline can call a missing command again",),
        "parser_missing": ("SPEC's own mutation-check: delete a documented "
                           "subcommand's parser entry, and the gate must name the "
                           "documents and lines that still cite it",),
        "no_packaging": ("`pyproject.toml` and `MANIFEST.in` leave the scanned "
                         "set — which is exactly where `flash vision --run` hid "
                         "while every markdown page was being read",),
    }
    real = {k: globals()[k] for k in ("collect", "resolves", "code_lines", "DOCS")}
    real_bp = cli.build_parser
    failed = ran = 0
    for bug in bugs:
        if one and bug != one:
            continue
        ran += 1
        try:
            if bug == "blind_collector":
                globals()["collect"] = lambda paths: {}
            elif bug == "always_resolves":
                globals()["resolves"] = lambda c: (True, "")
            elif bug == "no_ci_lane":
                keep = real["code_lines"]
                globals()["code_lines"] = lambda text, is_yaml, is_cfg=False: (
                    [] if is_yaml else keep(text, is_yaml, is_cfg))
            elif bug == "no_packaging":
                globals()["DOCS"] = tuple(
                    d for d in real["DOCS"]
                    if d not in ("pyproject.toml", "MANIFEST.in"))
            elif bug == "parser_missing":
                def stripped(real=real_bp, drop=("selftest", "doctor")):
                    p = real()
                    for act in getattr(p._subparsers, "_group_actions", []):
                        for name in drop:
                            act.choices.pop(name, None)
                    return p
                cli.build_parser = stripped
            CHECKS.clear()
            run_gates()
            caught = [l for l, ok, _ in CHECKS if not ok]
            if bug == "parser_missing":
                # The remedy is only a remedy if it points somewhere: at least one
                # failing answer has to carry a `file:line` a reader can open, or
                # this file would be reporting a symptom and leaving the author to
                # hunt the sentence.
                cited = [d for _, ok, d in CHECKS
                         if not ok and re.search(r"\.(md|yml):\d+", d)]
                caught = caught if cited else ["the failure named no file and line"]
        finally:
            globals().update(real)
            cli.build_parser = real_bp
        ok = bool(caught)
        failed += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} MUTATION: {bugs[bug][0]} "
              f"-> {len(caught)} check(s) fail {[c[:30] for c in caught][:2]}")
    print(f"\ndocumented-command mutants: {ran - failed}/{ran} gates defeated by "
          "exactly their checks")
    return failed


def main(argv: list) -> int:
    if argv and argv[0] == "--mutant":
        return 1 if mutate(argv[1] if len(argv) > 1 else None) else 0
    run_gates(print_table="--print" in argv)
    for label, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}"
              + (f"\n  ..  {detail}" if detail and (not ok or "--print" in argv) else ""))
    n = len(CHECKS)
    bad = [l for l, ok, _ in CHECKS if not ok]
    print(f"\ndocumented-command checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
