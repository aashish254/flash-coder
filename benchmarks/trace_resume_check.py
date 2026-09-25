"""§33.7 end-to-end, OFFLINE: interrupt a suite mid-task, resume it, and prove
nothing already-settled is re-run or re-billed.

No model loads — flash.loop.solve_routed is stubbed with a deterministic
outcome table plus one armed KeyboardInterrupt, and the trace directory is
redirected to a tempdir, so this runs in milliseconds and keeps
benchmarks/results/traces clean.

    python benchmarks/trace_resume_check.py
"""
from __future__ import annotations

import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import trace                                # noqa: E402
from flash import cli                                  # noqa: E402
from flash.loop import Attempt, SolveResult            # noqa: E402

IDS = [f"r{i:02d}" for i in range(6)]
CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


class Stub:
    """Stand-in for solve_routed: fixed outcomes, one armed interrupt."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.interrupt_at: str | None = "r04"

    def __call__(self, small, big, task, root, **kw):
        tid = task["id"]
        self.calls.append(tid)
        if tid == self.interrupt_at:
            self.interrupt_at = None        # fire once: the lid closes here
            raise KeyboardInterrupt
        r = SolveResult(task_id=tid, solved=True,
                        attempts=[Attempt(code="pass", ok=True)], seconds=2.0)
        return r, "small", "small"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tasks_file = Path(d) / "suite.jsonl"
        tasks_file.write_text("".join(
            json.dumps({"id": t, "prompt": f"task {t}", "test": "assert True"}) + "\n"
            for t in IDS))
        params = {"small": "stub-small", "big": "stub-big",
                  "tasks": str(tasks_file), "with_context": False,
                  "attempts": 2, "max_tasks": 99, "max_tokens": 1024,
                  "max_chars": 4000, "threshold": 1.1, "allow_big": "auto"}

        old_dir, trace.DIR = trace.DIR, Path(d) / "traces"
        import flash.loop as loop
        real, loop.solve_routed = loop.solve_routed, Stub()
        stub = loop.solve_routed
        buf = io.StringIO()
        try:
            argv, sys.argv = sys.argv, ["flash", "run-suite", "--tasks",
                                        params["tasks"], "--max-tasks", "99",
                                        "--threshold", "1.1", "--small", "stub-small",
                                        "--big", "stub-big", "--allow-big", "auto"]
            try:
                with redirect_stdout(buf):
                    rc1 = cli.main()
            finally:
                sys.argv = argv
            out1 = buf.getvalue()
            sid = re.search(r"session (\S+) —", out1).group(1)
            ck("interrupt: exits 130 and prints the exact resume command",
               rc1 == 130 and f"flash resume {sid}" in out1, out1.strip()[-70:])
            ck("interrupt: the task killed mid-flight is not settled",
               stub.calls == IDS[:5] and set(trace.positions(sid)) == set(IDS[:4]),
               f"calls={stub.calls}")
            ck("interrupt: 4 settled tasks are on disk before anything resumes",
               trace.summarize(sid)["tasks"] == 4 and trace.interrupted("run-suite") == [sid])

            stub.calls.clear()
            argv, sys.argv = sys.argv, ["flash", "resume", sid]
            try:
                buf2 = io.StringIO()
                with redirect_stdout(buf2):
                    rc2 = cli.main()
            finally:
                sys.argv = argv
            out2 = buf2.getvalue()
            ck("resume: only the unfinished tasks are generated again",
               stub.calls == ["r04", "r05"], str(stub.calls))
            ck("resume: settled tasks are reported as skipped, not re-run",
               out2.count("not re-run") == 4 and "CACHED" in out2, out2[:70])
            ck("resume: the suite's parameters came out of the session, not argv",
               "small=stub-small" in out2 and "allow_big=auto" in out2)
            ck("resume: totals cover the whole suite and the run is clean",
               "solved 6/6" in out2 and "total 12s" in out2 and rc2 == 0,
               out2.strip()[-90:])
            ck("resume: a finished session stops being resumable",
               trace.interrupted("run-suite") == []
               and trace.summarize(sid)["closed"] is True)
            ck("snapshot: one task_end per task, in execution order",
               [r.get("task_id") for r in trace.read(sid)
                if r["type"] == "task_end"] == IDS)
            ck("snapshot: the resumed records appended to the SAME session file",
               len(trace.read(sid)) > 8 and trace.positions(sid)[IDS[5]]["tier"] == "small")

            stub.calls.clear()
            buf3 = io.StringIO()
            with redirect_stdout(buf3):
                rc3 = cli._run_suite(params, sid)
            ck("re-resume: an already-closed suite generates nothing at all",
               stub.calls == [] and rc3 == 0 and buf3.getvalue().count("not re-run") == 6,
               str(stub.calls))
        finally:
            loop.solve_routed = real
            trace.DIR = old_dir

    width = max(len(n) for n, _, _ in CHECKS)
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name.ljust(width)}  {detail}")
    n = sum(ok for _, ok, _ in CHECKS)
    print(f"\ntrace/resume end-to-end: {n}/{len(CHECKS)} checks passed")
    return 0 if n == len(CHECKS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
