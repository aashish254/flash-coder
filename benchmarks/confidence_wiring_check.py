"""R-2.3 end-to-end, OFFLINE: does an evidence offer survive the loop -> trace
-> suite report chain, and does the reported number MOVE when the signal is
muted?

No model loads. `flash.loop.solve_routed` is stubbed with the same scripted
answers the confidence selftest uses, and it calls the REAL
`loop.assess_confidence` the way the real policy does; the trace directory goes
to a tempdir. So what is under test here is the wiring, not the signals — the
signals have their own battery in `python -m flash.confidence --selftest`.

    python benchmarks/confidence_wiring_check.py
"""
from __future__ import annotations

import inspect
import io
import json
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import cli, confidence, trace              # noqa: E402
import flash.loop as loop                             # noqa: E402
# The same four scripted answers the signal's own battery runs on: if the
# wiring test invented its own, the two batteries could pass on different code.
from flash.confidence import (                        # noqa: E402
    _ANS_CLEAN as ANS_CLEAN, _ANS_NO_GUARD as ANS_NO_GUARD,
    _ANS_UNCOVERED as ANS_UNCOVERED, _T_CLEAN as T_CLEAN,
    _T_NO_GUARD as T_NO_GUARD, _T_UNCOVERED as T_UNCOVERED)
from flash.loop import Attempt, SolveResult           # noqa: E402

# id -> (answer the run surfaces, visible test, hidden test, expected offer)
FIXTURES = {
    "w01_clean": (ANS_CLEAN, T_CLEAN, "assert total_cents(4) == 100\n", False),
    "w02_uncovered": (ANS_UNCOVERED, T_UNCOVERED,
                      "assert slugify('A B C') == 'a-b-c'\n", True),
    "w03_noguard": (ANS_NO_GUARD, T_NO_GUARD,
                    "try:\n    mean([])\n    assert False, 'empty input accepted'\n"
                    "except ValueError:\n    pass\n", True),
    # The same three answers with §34.2's populations declared, arranged so the
    # blended figure and the routine-only figure CANNOT agree: one false offer
    # lives on a seeded-subtle row, one recall on a routine row.
    "w04_subtle_false": (ANS_UNCOVERED, T_UNCOVERED,
                         "assert slugify('A B C') == 'a-b-c'\n", True),
    "w05_routine_caught": (ANS_NO_GUARD, T_NO_GUARD,
                           "assert mean([2, 4]) == 99\n", True),
    "w06_routine_clean": (ANS_CLEAN, T_CLEAN, "assert total_cents(4) == 100\n", False),
}
GATES = {"w04_subtle_false": "subtle", "w05_routine_caught": "routine",
         "w06_routine_clean": "routine"}
IDS = list(FIXTURES)[:3]
POP_IDS = list(FIXTURES)[3:]
CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


class Stub:
    """Stand-in for solve_routed: a scripted answer per task, and the SAME
    §34.2 call the real policy makes at its return paths."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, small, big, task, root, **kw):
        code, _test, _hidden, _offer = FIXTURES[task["id"]]
        self.calls.append(task["id"])
        r = SolveResult(task_id=task["id"], solved=True,
                        attempts=[Attempt(code=code, ok=True)], seconds=1.0)
        ok, _why = loop.confidence_eligible(bool(kw.get("confidence")), task)
        if ok:
            loop.assess_confidence(task, r)
        return r, "small", "small"


def params_for(tasks_file: Path, confidence_on: bool) -> dict:
    return {"small": "stub-small", "big": "stub-big", "tasks": str(tasks_file),
            "with_context": False, "attempts": 2, "max_tasks": 99,
            "max_tokens": 1024, "max_chars": 4000, "threshold": 1.1,
            "allow_big": "auto", "confidence": confidence_on}


def main() -> int:
    # ---------------------------------------------------------------- units
    ck("eligibility: off refuses, with the reason in the record",
       loop.confidence_eligible(False, {"id": "x"}) == (False, "confidence off"))
    ck("eligibility: a multi-file or edit answer is not one answer",
       all("single-answer shaped" in loop.confidence_eligible(True, t)[1]
           for t in ({"multi": True}, {"edit": True})))
    ck("eligibility: a plain single-file task runs the streams",
       loop.confidence_eligible(True, {"id": "x"})[0])

    a = SolveResult(task_id="u1", solved=True,
                    attempts=[Attempt(code=ANS_UNCOVERED, ok=True)], seconds=1.0)
    fields = loop.assess_confidence({"id": "u1", "prompt": "p", "test": T_UNCOVERED}, a)
    ck("assess_confidence fills exactly the six conf_* ledger fields",
       set(fields) == {"conf_static", "conf_cov", "conf_seeds", "conf_edges",
                       "conf_offer", "conf_reasons"}, str(sorted(fields)))
    ck("assess_confidence attaches the Signals to the result for the CLI to read",
       isinstance(a.confidence, confidence.Signals)
       and a.confidence.fields() == fields)
    ck("an answer with nothing to surface assesses to nothing, not to a zero",
       loop.assess_confidence({"id": "u", "test": "assert True"},
                              SolveResult(task_id="u", solved=False)) == {})

    b = SolveResult(task_id="u2", solved=True,
                    attempts=[Attempt(code=ANS_CLEAN, ok=True),
                              Attempt(code=ANS_UNCOVERED, ok=True)], seconds=1.0)
    b.tournament = {"surfaced": 0, "scores": [{"ok": True}, {"ok": True}]}
    ck("surfaced_attempt judges the tournament's best candidate, not the last draw",
       loop.surfaced_attempt(b) is b.attempts[0]
       and loop.assess_confidence({"id": "u2", "test": T_CLEAN}, b)["conf_offer"] is False)

    empty = SolveResult(task_id="u3", solved=False)
    ck("_hidden_verdict is None when nothing surfaced (no answer key, no row)",
       cli._hidden_verdict(empty, "assert True") is None)
    ck("_hidden_verdict accepts what the hidden tests pass",
       cli._hidden_verdict(b, "assert total_cents(4) == 100\n") is True)
    ck("_hidden_verdict sinks what the hidden tests reject — that is the answer key",
       cli._hidden_verdict(
           SolveResult(task_id="u4", solved=True,
                       attempts=[Attempt(code=ANS_NO_GUARD, ok=True)]),
           "try:\n    mean([])\n    assert False\nexcept ValueError:\n    pass\n") is False)
    ck("_conf_note prints R-2.3 only where there is evidence to print",
       "[R-2.3]" in cli._conf_note(a)
       and cli._conf_note(SolveResult(task_id="z", solved=False)) == "")

    # ------------------------------------------------------- suite, signal on
    with tempfile.TemporaryDirectory() as d:
        tasks_file = Path(d) / "wiring.jsonl"
        tasks_file.write_text("".join(
            json.dumps({"id": t, "prompt": f"task {t}", "test": FIXTURES[t][1],
                        "hidden": FIXTURES[t][2]}) + "\n" for t in IDS))
        old_dir, trace.DIR = trace.DIR, Path(d) / "traces"
        real = loop.solve_routed
        stub = Stub()
        loop.solve_routed = stub
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli._run_suite(params_for(tasks_file, True), None)
            out = buf.getvalue()
            sid = re.search(r"session (\S+) —", out).group(1)
            pos = trace.positions(sid)
            ck("suite: every answer's evidence reaches its task_end record",
               all("conf_offer" in pos[t] for t in IDS), str(pos[IDS[0]])[:90])
            ck("suite: the offer matches what the signals say per answer",
               [bool(pos[t]["conf_offer"]) for t in IDS]
               == [FIXTURES[t][3] for t in IDS],
               str([pos[t].get("conf_offer") for t in IDS]))
            ck("suite: the hidden verdict rides in the same record",
               [pos[t].get("hidden_ok") for t in IDS] == [True, True, False],
               str([pos[t].get("hidden_ok") for t in IDS]))
            line = [l for l in out.splitlines() if l.startswith("[R-2.3]")]
            ck("report: recall is counted over would-fail-hidden answers only",
               len(line) == 1 and "recall on would-fail-hidden 1/1" in line[0],
               line[0] if line else out.strip()[-90:])
            ck("report: false offers are counted over hidden-accepted ones, "
               "and 1-in-3 is NOT hidden as a percentage",
               "1 false offer(s) over 2 hidden-accepted" in (line[0] if line else ""),
               line[0] if line else "")
            ck("report: the offers themselves are on the line",
               "2/3 answer(s) carried evidence" in (line[0] if line else ""))
            ck("suite rc: the report never changes the pass/fail verdict", rc == 0)
            render = trace.render(sid)
            ck("trace replay: conf_offer is a first-class column, not a buried blob",
               "conf_offer=" in render, render.splitlines()[0][:80])
            ck("trace replay: the confidence event itself is replayable",
               "confidence" in render and "cov=" in cli._conf_note(a))

            # --------------------------------------------- resume keeps numbers
            stub.calls.clear()
            buf2 = io.StringIO()
            with redirect_stdout(buf2):
                cli._run_suite(params_for(tasks_file, True), sid)
            out2 = buf2.getvalue()
            line2 = [l for l in out2.splitlines() if l.startswith("[R-2.3]")]
            ck("resume: the numbers re-derive from the session, nothing re-runs",
               stub.calls == [] and line2 == line, str(stub.calls))

            # --------------------------------------------- mutation: mute it
            stub.calls.clear()
            buf3 = io.StringIO()
            with redirect_stdout(buf3):
                cli._run_suite(params_for(tasks_file, False), None)
            out3 = buf3.getvalue()
            sid3 = re.search(r"session (\S+) —", out3).group(1)
            ck("mutation: muted, no answer carries evidence fields at all",
               not any("conf_offer" in r for r in trace.positions(sid3).values()),
               str(sorted(trace.positions(sid3)[IDS[0]])))
            ck("mutation: muted, the gate line disappears — so the line IS the signal",
               "[R-2.3]" not in out3 and len(stub.calls) == 3)
            ck("report: an untagged suite prints one population, not two",
               "gate's own population" not in out3 and "gate's own population" not in out)

            # --------------------------------- the gate's own population split
            pop_file = Path(d) / "populations.jsonl"
            pop_file.write_text("".join(
                json.dumps({"id": t, "prompt": f"task {t}", "test": FIXTURES[t][1],
                            "hidden": FIXTURES[t][2],
                            **({"gate": GATES[t]} if t in GATES else {})}) + "\n"
                for t in POP_IDS + IDS))
            stub.calls.clear()
            buf4 = io.StringIO()
            with redirect_stdout(buf4):
                cli._run_suite(params_for(pop_file, True), None)
            out4 = buf4.getvalue()
            blend = [l for l in out4.splitlines() if l.startswith("[R-2.3]")][0]
            own = [l for l in out4.splitlines() if "gate's own population" in l]
            ck("report: the blended line counts every keyed answer, tagged or not",
               "4/6 answer(s) carried evidence" in blend
               and "2 false offer(s) over 4 hidden-accepted" in blend, blend)
            ck("report: the routine-only line counts ONLY rows tagged routine — the "
               "subtle row's false offer is named, not folded into the clause",
               len(own) == 1 and "recall 1/1" in own[0]
               and "0 false offer(s) over 1 hidden-accepted of 2 task(s)" in own[0],
               own[0] if own else "")
            ck("mutation: the split does not move the goalposts — same run, the "
               "blended figure is the stricter one",
               own and "2 false" in blend and "0 false" in own[0])
        finally:
            loop.solve_routed = real
            trace.DIR = old_dir

    # ------------------------------------ anti-drift: no record path skips it
    src = inspect.getsource(loop.solve_routed)
    lines = src.splitlines()
    recs = sum(1 for l in lines if "ledger.record(" in l)
    calls = sum(1 for l in lines if l.strip().startswith("with_conf("))
    ck("policy: every ledger.record in solve_routed is fed by a with_conf",
       recs == calls and recs >= 2, f"{recs} record line(s), {calls} with_conf call(s)")
    ck("policy: each with_conf sits immediately above the record it feeds",
       all("ledger.record(" in "\n".join(lines[i:i + 4])
           for i, l in enumerate(lines) if l.strip().startswith("with_conf(")))
    ck("policy: the route record carries the offer's own on/off and refusal",
       {"conf_on", "conf_used", "conf_why"} <= set(re.findall(r"\b(conf_\w+|tour_\w+)=",
                                                              src)))

    width = max(len(n) for n, _, _ in CHECKS)
    bad = 0
    for name, ok, detail in CHECKS:
        bad += not ok
        print(f"  {'OK  ' if ok else 'FAIL'} {name.ljust(width)}"
              + (f"   [{detail}]" if detail and not ok else ""))
    print(f"\nR-2.3 wiring end-to-end: {len(CHECKS) - bad}/{len(CHECKS)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
