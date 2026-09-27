"""Tournament mode (PLAN §33.4, SPEC R-3.3): k candidates, oracle-scored, best wins.

A hard task is not one guess — it is a few guesses and a verifier that can tell
them apart. The fast tier's recorded pass@1 on the hard family is 44% and its
eventual pass rate with feedback retries is 57% (ledger, 145 small-first h-task
runs), so most of what the loop loses it loses on the FIRST sample, not on the
repair. This fires k samples at the same task and lets the oracle pick.

Three decisions worth their evidence:

* **Sequential, one resident model.** §33.4's inspiration is snapshot-and-fork
  parallelism, but here the candidates share one MLX model on one Metal device:
  decode is serialised by the GPU already, and k processes would cost k x 4.4 GB
  against a governor cap the machine cannot pay. So the tournament buys *variance*,
  not concurrency — and it stops early, which is what makes it cheaper than the
  retry chain it replaces (see `plan`).
* **Candidate 0 is the greedy answer**, always. Then pass@k contains pass@1 by
  construction: a tournament can only add a solve, never trade one away. The
  remaining candidates sample (§33.9 standing finding: greedy retries come back
  byte-identical, so a same-seed second candidate is a wasted generation).
* **Adoption is first-pass, and ranking is only for the report.** When nothing
  passes, `score().passed` picks which diagnostic the run surfaces — but on this
  suite's real failures that signal is nearly flat: 6 of 7 recorded hard-task
  failures die on the FIRST assert (`benchmarks/fail_position_check.py`), so
  almost every failing candidate scores 0. The mechanism's value is pass@k, and
  this module does not claim more than that.

Offline: `python -m flash.tourney --selftest` drives the whole thing with a
scripted generator, so the wiring is proven without charging a model.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from flash import checkpoint, trace
from flash.harness import Score, extract_code, score
from flash.perceive import format_errors, static_check

TEMP = 0.7                      # candidates 1..k-1; candidate 0 stays greedy


def describe(t: dict) -> str:
    """One line for a tournament record — the same text whether it came from a
    live Result or from a resumed run's trace, so a report cannot describe the
    two differently."""
    verdicts = ",".join("P" if s["ok"] else f"{s['passed']}/{s['total']}"
                        for s in t["scores"])
    return (f"k={t['k']}/{t['requested_k']} width={t['width']} gens={t['gens']} "
            f"[{verdicts}] adopted={t['adopted']}"
            + (f" ({t['clamp']})" if t.get("clamp") else ""))


@dataclass
class Candidate:
    code: str
    ok: bool
    passed: int
    total: int
    err: str = ""
    ms: int = 0
    gen_ms: int = 0


@dataclass
class Result:
    task_id: str
    solved: bool
    adopted: int                        # index into candidates; -1 when none passed
    candidates: list[Candidate] = field(default_factory=list)
    requested_k: int = 1
    k: int = 1                          # width actually used
    width: int = 1                      # the governor's cap
    why: str = ""                       # why k < requested (or why the arm declined)
    seconds: float = 0.0

    @property
    def n_generations(self) -> int:
        return len(self.candidates)

    @property
    def best(self) -> Candidate | None:
        """The passing candidate, or the one that got furthest (ties: earliest)."""
        if not self.candidates:
            return None
        return self.candidates[self.surfaced]

    @property
    def surfaced(self) -> int:
        """Which candidate the run reports. First-pass adoption means a solve
        has exactly one candidate that passed; otherwise the ranking decides,
        and the ranking is only ever used for this choice."""
        if self.solved:
            return self.adopted
        return max(range(len(self.candidates)),
                   key=lambda i: (self.candidates[i].passed, -i))

    def summary(self) -> str:
        return describe(self.fields())

    def fields(self) -> dict:
        """The ledger shape of this tournament. Owned here so the loop's record
        and the CLI's report cannot mean different things by `adopted`."""
        return {"k": self.k, "requested_k": self.requested_k, "width": self.width,
                "adopted": self.adopted, "gens": self.n_generations,
                "surfaced": self.surfaced,
                "scores": [{"passed": c.passed, "total": c.total, "ok": c.ok,
                            "gen_ms": c.gen_ms, "ms": c.ms}
                           for c in self.candidates],
                **({"clamp": self.why} if self.why else {})}


def eligible(tournament: int, width: int, task: dict) -> tuple[bool, str]:
    """Whether the §33.4 arm replaces this task's feedback chain.

    Two refusals, both for reasons the mechanism cannot make up:

    * width 1 is not a tournament. With one candidate there is nothing to pick
      between, and the same generation buys more as a repair that is told which
      assert failed than as a second draw from the same distribution.
    * a multi-file or edit candidate is a whole project, not an answer — k of
      them is k× the output for a task the file-set state machine already
      repairs better.
    """
    if int(tournament) < 2:
        return False, "tournament off"
    if task.get("multi") or task.get("edit"):
        return False, "multi-file/edit task"
    if int(width) < 2:
        return False, f"governor width {width} cannot run 2+ candidates"
    return True, ""


def plan(requested_k: int, width: int, multi: bool = False) -> tuple[int, str]:
    """The width this machine may run, and the reason it is smaller than asked.

    §33.4's own rule — tournament when cool and plugged in, single-track on
    battery — is already the governor's `tournament_width` table (4 / 2 / 1 by
    profile), so clamping to it IS the AC-only enforcement: a shed profile
    returns width 1 and the tournament degenerates into the plain single attempt.
    """
    if multi:
        return 1, "multi-file task: tournament is single-file"
    k = max(1, min(int(requested_k), int(width)))
    if k < requested_k:
        return k, f"clamped to the governor's width {width}"
    return k, ""


def score_candidate(code: str, test: str, timeout: int = 15) -> Score:
    """VERIFY one candidate: static first (a doomed subprocess is slower than a
    line-precise diagnostic), then the assert probes."""
    static_err = format_errors(static_check(code))
    if static_err:
        return Score(False, 0, 0, f"STATIC: {static_err}")
    s = score(code, test, timeout)
    return Score(s.ok, s.passed, s.total, s.err)


def run(model, tokenizer, task: dict, requested_k: int = 3,
        max_tokens: int = 1024, width: int = 4, temp: float = TEMP,
        stage: str = "small") -> Result:
    """Fire up to `k` candidates at one task and let the oracle pick.

    Stops at the first candidate that passes — a task the greedy answer already
    solves costs one generation, exactly what the single attempt cost, so the
    arm is only ever more expensive on tasks the plain attempt would have lost.

    R-5.3: the candidate list IS this arm's state, so a resumed run picks up at
    the candidate that was in flight with every already-scored candidate in
    hand — a kill after candidate 1 does not buy candidate 0's generation again.
    """
    from flash import loop as loop_mod    # late: loop imports us in its policy

    k, why = plan(requested_k, width, multi=bool(task.get("multi")))
    t0 = time.perf_counter()
    res = Result(task_id=task["id"], solved=False, adopted=-1,
                 requested_k=requested_k, k=k, width=width, why=why)
    messages = [{"role": "user", "content": task["prompt"]}]
    start = 0
    f = checkpoint.owns(task["id"], "tourney", stage)
    if f is not None:
        start = f.attempt
        res.candidates = [Candidate(**d) for d in f.done]
    for i in range(start, k):
        # The candidate index is this arm's attempt index: `_generate` keys its
        # span on (task, attempt), and a candidate IS a tournament attempt.
        checkpoint.begin(task["id"], "tourney", stage, i, messages=messages,
                         max_tokens=max_tokens,
                         done=[asdict(c) for c in res.candidates])
        g0 = time.perf_counter()
        # candidate 0 greedy, the rest sampled with a per-candidate seed: the
        # seeds are what makes them different ANSWERS rather than one answer
        out = loop_mod._generate(model, tokenizer, messages, max_tokens,
                                 temp=0.0 if i == 0 else temp, seed=i,
                                 task_id=task["id"], attempt=i)
        gen_ms = round((time.perf_counter() - g0) * 1000)
        code = extract_code(out)
        v0 = time.perf_counter()
        s = score_candidate(code, task["test"])
        res.candidates.append(Candidate(code=code, ok=s.ok, passed=s.passed,
                                       total=s.total, err=s.err,
                                       ms=round((time.perf_counter() - v0) * 1000),
                                       gen_ms=gen_ms))
        if s.ok:
            res.solved, res.adopted = True, i
            break
    # Nothing passing leaves adopted=-1, which is what `surfaced` then ranks.
    res.seconds = round(time.perf_counter() - t0, 1)
    trace.event("tournament", task_id=task["id"], requested_k=requested_k,
                k=k, width=width, solved=res.solved, adopted=res.adopted,
                gens=res.n_generations, why=why or None,
                verdicts=[{"ok": c.ok, "passed": c.passed, "total": c.total,
                           "gen_ms": c.gen_ms, "ms": c.ms} for c in res.candidates],
                seconds=res.seconds)
    return res


# ------------------------------------------------------------------ selftest

_TZ_TEST = "assert f(2) == 4\nassert f(3) == 9\nassert f(4) == 16\n"
_TZ = {"id": "tz_square", "prompt": "Write `def f(x)` returning x squared.",
       "test": _TZ_TEST}
_IMPS = {
    "zero": "def f(x):\n    return 0\n",        # dies on assert 1   -> 0/3
    "double": "def f(x):\n    return x * 2\n",  # passes 1, dies on 2 -> 1/3
    "square": "def f(x):\n    return x * x\n",  # the answer          -> ok
    "broken": "def f(x):\n    return x*#\n",    # never runs          -> STATIC
}


def _resp(*names: str) -> list[str]:
    return ["Answer:\n\n```python\n" + _IMPS[n] + "```\n" for n in names]


HARD_TASKS = "benchmarks/tasks/m3_hard_tasks.jsonl"
_DATA_ROOT = Path(__file__).resolve().parent.parent


def run_wiring(verbose: bool = True, into: list | None = None,
               hard_tasks: str | Path | None = None) -> int:
    """The whole tournament with no model: a scripted generator stands in for
    the samples, so what is under test is the mechanism — order, early exit,
    adoption, ranking, the governor's clamp and the trace record."""
    import tempfile

    import flash.loop as loop
    from flash.harness import load_tasks

    tasks = _DATA_ROOT / (hard_tasks or HARD_TASKS)

    checks = into if into is not None else []

    def ck(label, cond, detail=""):
        checks.append((label, bool(cond), str(detail)))
        if verbose:
            print(f"  {'OK  ' if cond else 'FAIL'} {label}"
                  + (f"  [{detail}]" if detail else ""))

    def run_with(names, k=3):
        calls: list[dict] = []
        stream = iter(_resp(*names))

        def _gen(model, tokenizer, messages, max_tokens, **kw):
            calls.append(kw)
            return next(stream)

        original = loop._generate
        loop._generate = _gen
        try:
            r = run(None, None, _TZ, requested_k=k)
        finally:
            loop._generate = original
        r._calls = calls                      # for the schedule checks below
        return r

    # --- the governor's clamp is the AC-only rule (§33.4)
    ck("plan: full width on maximum-performance", plan(4, 4) == (4, ""), str(plan(4, 4)))
    ck("plan: balanced caps the tournament at 2", plan(4, 2)[0] == 2, str(plan(4, 2)))
    ck("plan: on battery width 1 degenerates to the single attempt",
       plan(3, 1)[0] == 1 and "width" in plan(3, 1)[1], str(plan(3, 1)))
    ck("plan: a multi-file task is declined, not half-supported",
       plan(3, 4, multi=True) == (1, "multi-file task: tournament is single-file"))

    # --- the candidate schedule
    r = run_with(["zero", "double", "square"])
    ck("candidate 0 is the greedy answer (so pass@k contains pass@1)",
       r._calls[0].get("temp") == 0.0, str([c.get("temp") for c in r._calls]))
    ck("later candidates sample, each from its own seed",
       all(c.get("temp", 0) > 0 for c in r._calls[1:])
       and [c.get("seed") for c in r._calls] == [0, 1, 2],
       str([(c.get("temp"), c.get("seed")) for c in r._calls]))
    ck("one model object serves every candidate (no extra load)",
       len(r._calls) == 3)

    # --- adoption and early exit
    r = run_with(["zero", "square", "zero"])
    ck("a passing candidate ends the tournament",
       r.solved and r.adopted == 1 and r.n_generations == 2, r.summary())
    r = run_with(["square", "zero", "zero"])
    ck("the greedy answer alone costs exactly one generation",
       r.solved and r.adopted == 0 and r.n_generations == 1, r.summary())
    r = run_with(["zero", "double", "broken"])
    ck("nothing passing: unsolved, adopted=-1, all k spent",
       not r.solved and r.adopted == -1 and r.n_generations == 3, r.summary())
    ck("the surfaced diagnostic is the candidate that got furthest",
       r.best is r.candidates[1] and r.best.passed == 1,
       str([c.passed for c in r.candidates]))
    r2 = run_with(["double", "double", "zero"])
    ck("a tie in the ranking breaks toward the earlier candidate",
       r2.best is r2.candidates[0], str([c.passed for c in r2.candidates]))
    r = run_with(["broken", "square"])
    ck("an unparsable candidate is refused by PERCEIVE before any subprocess",
       r.candidates[0].passed == 0 and r.candidates[0].err.startswith("STATIC"),
       r.candidates[0].err[:70])
    ck("a broken first candidate does not sink the task",
       r.solved and r.adopted == 1, r.summary())

    # --- real suite, real oracle: the reference solution of an h-task
    t = load_tasks(tasks)[0]
    stream = iter(["```python\n" + t["solution"] + "```\n"])

    def _gen2(model, tokenizer, messages, max_tokens, **kw):
        return next(stream)
    original = loop._generate
    loop._generate = _gen2
    try:
        r = run(None, None, t, requested_k=3)
    finally:
        loop._generate = original
    ck("a real hard task's reference answer is adopted on candidate 0",
       r.solved and r.adopted == 0 and r.candidates[0].total == 2, r.summary())

    # --- the trace record (§33.6: the decision must be replayable)
    from flash import trace
    with tempfile.TemporaryDirectory() as d:
        saved, trace.DIR = trace.DIR, Path(d)
        try:
            sid = trace.open_session("tourney-selftest")
            run_with(["zero", "double", "square"])
            trace.close_session()
            evs = [e for e in trace.read(sid, dir=d) if e["type"] == "tournament"]
        finally:
            trace.DIR = saved
    ck("the tournament writes one replayable event with its candidate table",
       len(evs) == 1 and len(evs[0]["verdicts"]) == 3
       and evs[0]["verdicts"][2]["ok"] and evs[0]["adopted"] == 2,
       str(evs)[:160])

    n_bad = sum(not ok for _, ok, _ in checks)
    if verbose:
        print(f"\ntourney selftest: {len(checks) - n_bad}/{len(checks)} checks passed")
    return 1 if n_bad else 0


def run_selftest() -> int:
    from flash import doctor
    refused = doctor.vector_refusal("tourney", _DATA_ROOT)
    if refused:
        return refused
    return run_wiring()


if __name__ == "__main__":                       # pragma: no cover
    import sys
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        raise SystemExit(run_selftest())
    print(__doc__)
