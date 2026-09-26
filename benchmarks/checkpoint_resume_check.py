"""R-5.3's vector: `kill -9` a suite BETWEEN TWO TOKENS, resume it in a new
process, and prove the in-flight task CONTINUES rather than restarts.

§33.7 made an interrupted suite resumable at task granularity, which left a gap
exactly one task wide: the generation in flight was thrown away and the task
restarted from attempt 0. This closes it the only way that counts — a real
SIGKILL landed on a real `flash run-suite` mid-decode, resumed by a fresh
process so nothing in memory survives the transition.

What is scripted is the MODEL and nothing else. A fake `mlx_lm` "decodes" a
fixed text four characters at a time at 12ms a token, which is what gives a
signal room to land inside a span. The loop, the checkpoint, the power
governor, the assert oracle, the trace session and the CLI's resume path all run
for real. The fake model is a witness rather than a stub: every generation
reports the exact prompt it was handed and how much of the answer that prompt
already contained, so "it did not re-decode them" is MEASURED — a continuation
must start at the dead run's last durable character and finish at the byte where
the cold answer ends.

Seven scenarios:

1. mid-generation — kill inside attempt 0; the resume continues attempt 0.
1b. repeat kill   — kill the SAME span twice, resuming between the kills, so the
                    last process must carry the text of two dead ones. A machine
                    that crashes once can be forgiven; one that crashes twice
                    must not be wrong.
2. mid-chain      — kill inside attempt 1; attempt 0 is never generated again.
3. multi-file     — kill inside attempt 1 of a task that only PASSES if the
                    dead run's `merged` file set came back, so sandbox-state
                    recovery is proven by the oracle, not by a field read back.
4. tournament     — kill inside candidate 1; candidate 0 is not re-drawn.
5. big tier       — kill inside the brain's attempt 0; the resume skips the
                    small tier the dead run already exhausted.
6. inertness      — the same solve with no session armed produces identical
                    bytes and writes no frame, which is what lets the streaming
                    path exist without invalidating a stored pass rate.

Not covered, stated so: the patch arm (R-3.2) shares this module's
begin/owns/restore shape but is not killed separately, and R-4.2's mask replay
runs only under `--constrain`, which needs a real HF vocabulary to mean anything
(`flash.grammar --selftest` covers the DFA; this file covers the span). R-5.4's
24h chaos gate stays open — SPEC §9.

    python benchmarks/checkpoint_resume_check.py
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _p in (str(ROOT), str(ROOT / "benchmarks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TICK = 0.012                 # wall time per decoded "token"
CHUNK = 4                    # characters per token: 16 tokens = one checkpoint
BIG_REPO = "fake/big"
_PAD = ("# padded past one flush boundary so a real signal can land between two\n"
        "# tokens rather than between two attempts, which is the whole point\n")
CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), str(detail)))


# --------------------------------------------------------------- the scripts
def _fence(src: str) -> str:
    return "Answer:\n\n```python\n" + _PAD + src + "```\n"


def _files(**blocks: str) -> str:
    return ("Answer:\n\n" + "".join(f"```python\n# file: {n}\n{_PAD}{c}```\n\n"
                                    for n, c in blocks.items())).rstrip() + "\n"


TASKS = {
    # greedy answers it correctly on the first try: one span to resume inside
    "ck_midgen": dict(
        prompt="Task ck_midgen: write f(x) returning x squared.",
        test="assert f(2) == 4\nassert f(3) == 9\nassert f(4) == 16\n",
        greedy=_fence("def f(x):\n    return x * x\n"),
        sampled=_fence("def f(x):\n    return x * 2\n"),
        big=_fence("def f(x):\n    return x * x\n")),
    # greedy is wrong and the sampled retry is right: attempt 1 is in flight
    "ck_chain": dict(
        prompt="Task ck_chain: write dbl(x) returning x doubled twice.",
        test="assert dbl(3) == 12\nassert dbl(0) == 0\n",
        greedy=_fence("def dbl(x):\n    return x * 2\n"),
        sampled=_fence("def dbl(x):\n    return x * 4\n"),
        big=_fence("def dbl(x):\n    return x * 4\n")),
    # attempt 0 ships both files with one wrong; attempt 1 ships ONLY the fix,
    # so the union only passes if `merged` survived the kill
    "ck_multi": dict(
        multi=True,
        prompt=("Task ck_multi: write a two-file project, a.py with f() and "
                "b.py with g(). Each file as a fenced block whose first line is "
                'exactly "# file: <name>.py".'),
        test=('import sys; sys.path.insert(0, "<TMPDIR>")\n'
              "import a, b\nassert a.f() == 1\nassert b.g() == 2\n"),
        greedy=_files(**{"a.py": "def f():\n    return 1\n",
                         "b.py": "def g():\n    return 0\n"}),
        sampled=_files(**{"b.py": "def g():\n    return 2\n"}),
        big=_files(**{"a.py": "def f():\n    return 1\n",
                      "b.py": "def g():\n    return 2\n"})),
    # the tournament's greedy candidate loses and the sampled one wins
    "ck_tourney": dict(
        prompt="Task ck_tourney: write cube(x) returning x cubed.",
        test="assert cube(2) == 8\nassert cube(3) == 27\n",
        greedy=_fence("def cube(x):\n    return x * x * x + 1\n"),
        sampled=_fence("def cube(x):\n    return x ** 3\n"),
        big=_fence("def cube(x):\n    return x ** 3\n")),
    # neither small attempt solves it; only the brain does
    "ck_big": dict(
        prompt="Task ck_big: write carry(n) returning n plus two.",
        test="assert carry(9) == 11\nassert carry(0) == 2\n",
        greedy=_fence("def carry(n):\n    return n + 1\n"),
        sampled=_fence("def carry(n):\n    return n\n"),
        big=_fence("def carry(n):\n    return n + 2\n")),
}
ORDER = list(TASKS)


# ------------------------------------------------------------ the fake model
class _Resp:
    def __init__(self, text: str, token: int):
        self.text, self.token = text, token


class _FakeModel:
    def __init__(self, repo: str):
        self.repo = repo


class _FakeTok:
    bos_token = None

    def apply_chat_template(self, messages, tokenize=False,
                            add_generation_prompt=True):
        return "".join(f"<|{m['role']}|>\n{m['content']}\n"
                       for m in messages) + (
            "<|assistant|>\n" if add_generation_prompt else "")

    def encode(self, text, add_special_tokens=True):
        return list(range(len(text)))


def _task_of(prompt: str) -> str | None:
    for tid in ORDER:
        if tid in prompt:
            return tid
    return None


def _script(model, prompt: str, sampler) -> str:
    """The whole answer this generation would produce, from its own prompt."""
    tid = _task_of(prompt)
    if tid is None:
        return "Answer:\n\n```python\npass\n```\n"
    t = TASKS[tid]
    if getattr(model, "repo", "") == BIG_REPO:
        return t["big"]
    return t["greedy"] if sampler is None else t["sampled"]


def _matched(prompt: str, answer: str) -> int:
    """How many characters of `answer` the prompt already ends with.

    A fresh generation matches none: its prompt stops at the assistant header. A
    continued one matches exactly the text a dead process had already decoded —
    which is the quantity this whole file exists to measure.
    """
    for k in range(len(answer), 0, -1):
        if prompt.endswith(answer[:k]):
            return k
    return 0


def install(dir: Path) -> None:
    """Replace mlx_lm with a scripted decoder and re-point the ledger.

    Every generation is written down twice — at its start, with the prompt it was
    given, and at its end, with what it emitted — to an append-only log the
    parent reads after a SIGKILL. A start with no end IS the event under test.
    """
    gens = dir / "gens.jsonl"

    def log(rec):
        with open(gens, "a") as fh:
            fh.write(json.dumps({**rec, "pid": os.getpid()}) + "\n")

    def stream_generate(model, tokenizer, prompt, max_tokens=1024,
                        sampler=None, logits_processors=None, **kw):
        answer = _script(model, prompt, sampler)
        k = _matched(prompt, answer)
        body = answer[k:]
        log({"phase": "start", "model": getattr(model, "repo", "?"),
             "prompt": prompt, "matched": k, "answer": answer})
        n, out = 0, ""
        for i in range(0, len(body), CHUNK):
            if n >= int(max_tokens):
                break
            time.sleep(TICK)
            piece = body[i:i + CHUNK]
            n += 1
            out += piece
            yield _Resp(piece, n)
        log({"phase": "end", "matched": k, "emitted": len(out), "tokens": n,
             "answer": answer})

    def generate(model, tokenizer, prompt, max_tokens=1024, verbose=False,
                 sampler=None, logits_processors=None, **kw):
        # what mlx_lm.generate itself does with stream_generate. Logged as its
        # own phase so a test can see WHICH of the two decode paths ran.
        answer = _script(model, prompt, sampler)
        log({"phase": "generate", "model": getattr(model, "repo", "?"),
             "prompt": prompt, "matched": _matched(prompt, answer),
             "answer": answer})
        return "".join(r.text for r in stream_generate(
            model, tokenizer, prompt, max_tokens=max_tokens, sampler=sampler,
            logits_processors=logits_processors))

    def load(repo, **kw):
        return _FakeModel(repo), _FakeTok()

    mlx_lm = types.ModuleType("mlx_lm")
    mlx_lm.load, mlx_lm.generate = load, generate
    mlx_lm.stream_generate = stream_generate
    su = types.ModuleType("mlx_lm.sample_utils")
    su.make_sampler = lambda temp=0.0, **kw: (lambda logits: logits)
    core = types.ModuleType("mlx.core")
    core.random = types.SimpleNamespace(seed=lambda *a: None)
    core.clear_cache = lambda: None
    mlx = types.ModuleType("mlx")
    mlx.core = core
    sys.modules.update({"mlx_lm": mlx_lm, "mlx_lm.sample_utils": su,
                        "mlx": mlx, "mlx.core": core})

    from flash import ledger, loop, route
    from flash.decide import Decision
    ledger_path = dir / "ledger.jsonl"
    ledger.record = lambda entry, path=None: ledger_path.open("a").write(
        json.dumps({**entry, "ts": round(time.time(), 1)}) + "\n")
    # the router's decision is one forward pass over real logits, and which tier
    # a task is ROUTED to is not what this file tests: pin it to the cheap tier
    # so escalation below is always the reactive kind.
    route.route_task = lambda model, tok, task, ctx="": Decision(
        choice="easy", index=0, confidence=0.9, probs={}, ms=1.0)
    # The learned-router bundle this repo carries is a real .npz plus a real
    # embedding pass over mx — a second model this file is not about. With it
    # out, `routed` comes from the pinned decision above and nothing else.
    loop._load_router = lambda: None


# ------------------------------------------------------------- child process
def child(dir: Path, tail: list[str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-u", str(Path(__file__)),
                             "--child", str(dir)] + tail,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True)


def child_main(dir: Path, tail: list[str]) -> int:
    from flash import checkpoint, trace
    install(dir)
    trace.DIR = dir / "traces"
    if tail[:1] == ["inert"]:                       # scenario 6: no CLI involved
        return _inert(dir)
    import flash.cli as cli
    sys.argv = ["flash"] + tail
    rc = cli.main()
    checkpoint.disarm()
    return rc or 0


def _frames(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("*.ckpt*"))


def _inert(dir: Path) -> int:
    """Scenario 6: the same solve armed and unarmed, byte for byte."""
    from flash import checkpoint, loop, trace
    from flash.harness import load_tasks
    model, tok = _FakeModel("fake/small"), _FakeTok()
    t = load_tasks(dir / "tasks.jsonl")[0]
    traces = dir / "traces"
    sid = trace.open_session("inert", cmd="inert", params={})
    checkpoint.arm(sid)
    armed = loop.solve(model, tok, dict(t), max_attempts=1)
    during = _frames(traces)
    checkpoint.disarm()
    checkpoint.clear(sid)                # as the suite engine would on settle
    after_clear = _frames(traces)
    plain = loop.solve(model, tok, dict(t), max_attempts=1)
    trace.close_session()
    (dir / "inert.json").write_text(json.dumps({
        "armed": armed.attempts[0].code, "unarmed": plain.attempts[0].code,
        "ok": bool(armed.solved and plain.solved), "during": during,
        "afterClear": after_clear, "afterUnarmed": _frames(traces)}))
    return 0


# ------------------------------------------------------------------- reading
def gens_of(dir: Path, pid: int) -> list[dict]:
    p = dir / "gens.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return [r for r in rows if r["pid"] == pid]


def ledger_rows(dir: Path) -> list[dict]:
    p = dir / "ledger.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()
            if l.strip()] if p.exists() else []


def wait_for(cond, timeout=180.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(0.004)
    return None


def first_session(out) -> str | None:
    import re
    while True:
        line = out.readline()
        if not line:
            return None
        m = re.search(r"session (\S+) —", line)
        if m:
            return m.group(1)


def tasks_file(dir: Path, ids: list[str]) -> Path:
    p = dir / "tasks.jsonl"
    p.write_text("".join(json.dumps({"id": t, **TASKS[t]}) + "\n" for t in ids))
    return p


# ------------------------------------------------------------- one kill cycle
def kill_cycle(name: str, base: Path, ids: list[str], target: str, *,
               attempt: int = 0, stage: str = "small",
               extra: list[str] | None = None, kills: int = 1) -> dict:
    """Run `ids`, SIGKILL inside `target`'s generation `kills` times (resuming
    between the kills, as a crash-prone machine would), then let one resume
    finish. `run1` is the first killed process, `mids` any further killed
    resumes, `run2` the process that was allowed to complete."""
    from flash import checkpoint, trace
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    checkpoint.disarm()
    trace.DIR = d / "traces"
    argv = (["run-suite", "--tasks", str(tasks_file(d, ids)), "--max-tasks",
             str(len(ids)), "--threshold", "1.1", "--small", "fake/small",
             "--big", BIG_REPO, "--allow-big", "never", "--attempts", "2"]
            + (extra or []))
    p = child(d, argv)
    sid = wait_for(lambda: first_session(p.stdout))
    if not sid:
        raise AssertionError(f"{name}: the killed run opened no session:\n"
                             f"{p.stdout.read()[-500:]}")

    # The floor is what makes the SECOND kill mean anything: a durable frame
    # left by a dead process already looks mid-flight, so waiting on "partial is
    # non-empty" would SIGKILL the resume before it decoded a single token. The
    # live process must get PAST the last dead one's token count.
    def midflight(floor_ids: int):
        def check():
            f = checkpoint.load(sid)
            if (f is None or f.task_id != target or f.attempt != attempt
                    or f.stage != stage or not f.partial
                    or f.decoded <= floor_ids or p.poll() is not None):
                return None
            return f
        return check

    runs, mids, dead = [], [], None
    for i in range(kills):
        if wait_for(midflight(0 if dead is None else dead.decoded)) is None:
            rc, tail = p.poll(), p.stdout.read()[-500:]
            p.kill()
            p.wait(timeout=10)
            raise AssertionError(
                f"{name}: never caught {target} attempt {attempt} of {stage} "
                f"mid-span (child exit={rc})\n{tail}")
        p.send_signal(signal.SIGKILL)
        p.wait(timeout=10)
        dead = checkpoint.load(sid)              # exactly what a resume will see
        runs.append(gens_of(d, p.pid))
        if i:
            mids.append(runs[-1])
        if i + 1 < kills:
            p = child(d, ["resume", sid])        # this resume gets killed too

    checkpoint.disarm()
    p = child(d, ["resume", sid])
    rc2 = p.wait(timeout=600)
    out2 = p.stdout.read()
    trace.DIR = d / "traces"
    return {"dir": d, "sid": sid, "dead": dead, "run1": runs[0], "mids": mids,
            "run2": gens_of(d, p.pid), "rc2": rc2, "out2": out2,
            "target": target,
            "rows": [e for e in trace.read(sid) if e["type"] == "task_end"]}


def drew(r: dict, run: str = "run2") -> list[dict]:
    """Generations a process STARTED (a start with no end died in flight)."""
    return [g for g in r[run] if g["phase"] == "start"]


def last_end(r: dict) -> dict:
    e = [g for g in r["run2"] if g["phase"] == "end"]
    return e[-1] if e else {}


def continued(r: dict) -> dict | None:
    """The resumed run's generation that carried text over, if any."""
    return next((g for g in drew(r) if g["matched"]), None)


def task_rows(r: dict) -> dict:
    return {x["task_id"]: x for x in r["rows"]}


def recovery_saves_the_tokens(r: dict, label: str, answer: str,
                              head: str | None = None) -> None:
    """The two numbers every scenario is built to produce.

    * the resumed prompt ends with the dead run's last durable text — the
      continuation is conditioned on exactly what was already decoded;
    * matched + emitted spans the answer to its last byte — so nothing that was
      already paid for was bought again, and nothing was lost either.

    `head` is that durable text: one pending span for a single kill, and the
    whole folded-and-pending text for a scenario that killed the resume too.
    """
    c, e = continued(r), last_end(r)
    want = r["dead"].partial if head is None else head
    ck(f"{label}: the dead run's last durable text is the resume's head start",
       c is not None and want and c["prompt"].endswith(want)
       and c["matched"] == len(want),
       f"head={len(want)}c matched={c and c['matched']}c")
    ck(f"{label}: zero decoded characters are decoded a second time",
       bool(e) and 0 < e["matched"] < len(answer)
       and e["matched"] + e["emitted"] == len(answer),
       f"{e.get('matched')}+{e.get('emitted')} of {len(answer)}c")


# --------------------------------------------------------------------- the run
def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        install(base)
        from flash import power

        # ---- 1. mid-generation: killed inside the ONLY attempt, and resumed
        r = kill_cycle("midgen", base, ["ck_midgen"], "ck_midgen")
        recovery_saves_the_tokens(r, "attempt 0", TASKS["ck_midgen"]["greedy"])
        ck("attempt 0: one generation died in flight and one resumed it — "
           "no third draw anywhere",
           len(drew(r, "run1")) == 1 and not [g for g in r["run1"]
                                              if g["phase"] == "end"]
           and len(drew(r)) == 1 and len([g for g in r["run2"]
                                          if g["phase"] == "end"]) == 1,
           f"run1={[(g['phase']) for g in r['run1']]} run2={[(g['phase']) for g in r['run2']]}")
        row = task_rows(r)["ck_midgen"]
        ck("attempt 0: the killed task settles on the small tier with the dead "
           "run's ONE attempt credited, not two",
           row["solved"] and row["attempts"] == 1 and row["tier"] == "small",
           json.dumps({k: row.get(k) for k in ("solved", "attempts", "tier")}))
        ck("attempt 0: the resume says which task it is continuing INSIDE",
           "mid-flight and resumes inside" in r["out2"] and "ck_midgen" in r["out2"],
           next((l for l in r["out2"].splitlines() if "resuming" in l), "")[:88])
        ck("attempt 0: the suite finishes closed and exits 0",
           r["rc2"] == 0 and list(task_rows(r)) == ["ck_midgen"],
           f"rc={r['rc2']} rows={list(task_rows(r))}")
        ck("attempt 0: one ledger row for the task across a killed run and its resume",
           [x["task_id"] for x in ledger_rows(r["dir"])] == ["ck_midgen"],
           str([x["task_id"] for x in ledger_rows(r["dir"])]))

        # ---- 1b. a machine that dies TWICE inside one generation: the second
        #          resume must carry the first kill's text as well as its own
        r = kill_cycle("twice", base, ["ck_midgen"], "ck_midgen", kills=2)
        first, middle = r["run1"], r["mids"][0]
        cold = [g for g in first if g["phase"] == "start"][0]["prompt"]
        # The frame's prompt has absorbed the first span, so what the last
        # resume is handed is everything after what the cold run began with.
        head = r["dead"].prompt[len(cold):] + r["dead"].partial
        ck("repeat kill: the head start is longer than the newest fragment — it "
           "holds every character BOTH dead processes decoded",
           r["dead"].prompt.startswith(cold)
           and len(head) > len(r["dead"].partial),
           f"newest={len(r['dead'].partial)}c dead text total={len(head)}c")
        recovery_saves_the_tokens(r, "repeat kill",
                                  TASKS["ck_midgen"]["greedy"], head=head)
        ck("repeat kill: three processes touched the span, two died inside it, "
           "and the answer was drawn exactly once",
           len([g for g in first if g["phase"] == "start"]) == 1
           and not [g for g in first if g["phase"] == "end"]
           and len([g for g in middle if g["phase"] == "start"]) == 1
           and not [g for g in middle if g["phase"] == "end"]
           and len(drew(r)) == 1
           and len([g for g in r["run2"] if g["phase"] == "end"]) == 1,
           f"run1={len(first)} mid={len(middle)} run2={len(r['run2'])}")
        row = task_rows(r)["ck_midgen"]
        ck("repeat kill: two crashes cost no extra attempt — the task settles "
           "on the one span all three processes shared",
           row["solved"] and row["attempts"] == 1 and r["rc2"] == 0,
           json.dumps({k: row.get(k) for k in ("solved", "attempts", "tier")}))

        # ---- 2. mid-chain on a two-task suite: attempt 0 already failed and
        #        stays failed, and the earlier task stays SETTLED
        r = kill_cycle("chain", base, ORDER[:2], "ck_chain", attempt=1)
        ck("attempt 1: the dead run generated three times (one settled task and "
           "both attempts of the next) and the resume generates only the span "
           "still in flight",
           len(drew(r, "run1")) == 3 and len(drew(r)) == 1
           and all(_task_of(g["prompt"]) == "ck_chain" for g in drew(r)),
           f"run1={len(drew(r, 'run1'))} run2={len(drew(r))}")
        ck("attempt 1: the task the dead run settled is skipped, not regenerated",
           "not re-run" in r["out2"] and "already settled" in r["out2"],
           next((l for l in r["out2"].splitlines() if "resuming" in l), "")[:88])
        ck("attempt 1: the retry's whole feedback conversation came back off "
           "the frame, so the model is asked to fix the code it already wrote",
           "Your previous code failed" in drew(r)[0]["prompt"]
           and "return x * 2" in drew(r)[0]["prompt"],
           str(len(drew(r)[0]["prompt"])))
        recovery_saves_the_tokens(r, "attempt 1", TASKS["ck_chain"]["sampled"])
        row = task_rows(r)["ck_chain"]
        ck("attempt 1: the chain settles with both attempts on the record",
           row["solved"] and row["attempts"] == 2,
           json.dumps({k: row.get(k) for k in ("solved", "attempts")}))
        ck("attempt 1: the suite still closes at exit 0 with every task settled "
           "exactly once in the ledger",
           r["rc2"] == 0 and sorted(task_rows(r)) == sorted(ORDER[:2])
           and [x["task_id"] for x in ledger_rows(r["dir"])] == ORDER[:2],
           f"rc={r['rc2']} rows={sorted(task_rows(r))} ledger="
           f"{[x['task_id'] for x in ledger_rows(r['dir'])]}")

        # ---- 3. multi-file: the oracle, not a field, proves the state came back
        r = kill_cycle("multi", base, ["ck_multi"], "ck_multi", attempt=1)
        row = task_rows(r)["ck_multi"]
        ck("merged files: the answer only PASSES on the union of both attempts, "
           "and it passes — so the file set survived the kill",
           row["solved"] and row["attempts"] == 2,
           json.dumps({k: row.get(k) for k in ("solved", "attempts")}))
        ck("merged files: the resumed attempt is repairing named files, not "
           "re-transcribing a project",
           all(n in drew(r)[0]["prompt"] for n in ("a.py", "b.py"))
           and "# file: a.py" in drew(r)[0]["prompt"],
           str([n for n in ("a.py", "b.py") if n in drew(r)[0]["prompt"]]))
        recovery_saves_the_tokens(r, "merged files", TASKS["ck_multi"]["sampled"])

        # ---- 4. tournament: candidate 0 was drawn, scored and lost
        _, caps = power.governor(power.peak_gb("fake/small"), refresh=True)
        assert caps.tournament_width >= 2, (
            f"the tournament arm needs the governor's width >= 2 and this "
            f"machine offers {caps.tournament_width} ({', '.join(caps.reasons)}): "
            "put it on AC, let it cool, re-run")
        r = kill_cycle("tourney", base, ["ck_tourney"], "ck_tourney", attempt=1,
                       extra=["--tournament", "3"])
        ck("tournament: candidate 0 is scored once, in the dead run, and never "
           "re-drawn", len(drew(r, "run1")) == 2 and len(drew(r)) == 1,
           f"run1={len(drew(r, 'run1'))} run2={len(drew(r))}")
        t = task_rows(r)["ck_tourney"].get("tournament") or {}
        ck("tournament: the record still shows the whole field it planned — the "
           "width the child's own governor granted, two candidates drawn, the "
           "sampled one adopted (compared INSIDE the record: the machine's width "
           "is live state and drifts between two reads of it)",
           t.get("requested_k") == 3 and t.get("width") >= 2
           and t.get("k") == min(3, t.get("width", 0))
           and t.get("gens") == 2 and t.get("adopted") == 1,
           json.dumps({k: t.get(k) for k in ("requested_k", "width", "k", "gens",
                                             "adopted")}))
        recovery_saves_the_tokens(r, "tournament", TASKS["ck_tourney"]["sampled"])

        # ---- 5. the big tier: the small one is not re-run on the way back
        r = kill_cycle("bigtier", base, ["ck_big"], "ck_big", attempt=0,
                       stage="big", extra=["--allow-big", "always"])
        ck("big tier: the resume generates on the brain and nowhere else — the "
           "small tier the dead run exhausted is skipped",
           all(g["model"] == BIG_REPO for g in drew(r))
           and all(_task_of(g["prompt"]) == "ck_big" for g in drew(r))
           and len(drew(r)) == 1,
           str([(g["model"], g["matched"]) for g in drew(r)]))
        row = task_rows(r)["ck_big"]
        led = [x for x in ledger_rows(r["dir"]) if x["task_id"] == "ck_big"]
        ck("big tier: the task settles once, on the big tier, and the ledger "
           "says the run was a resume",
           row["solved"] and row["tier"] == "big" and len(led) == 1
           and led[0].get("resumed") == "big",
           json.dumps({k: led[0].get(k) for k in ("tier", "resumed", "attempts")}
                      if led else {}))
        recovery_saves_the_tokens(r, "big tier", TASKS["ck_big"]["big"])

        # ---- 6. inertness: recovery must not change an answer
        d = base / "inert"
        d.mkdir(exist_ok=True)
        tasks_file(d, ["ck_midgen"])
        p = child(d, ["inert"])
        out = p.stdout.read()
        assert p.wait(timeout=300) == 0, out
        j = json.loads((d / "inert.json").read_text())
        ck("inertness: the armed (streamed) and unarmed (mlx generate) answers "
           "are byte-identical, so no stored pass rate is invalidated",
           j["armed"] == j["unarmed"] and j["ok"],
           f"{len(j['armed'])}c vs {len(j['unarmed'])}c")
        ck("inertness: an armed solve leaves a frame on disk and settling it "
           "clears the session",
           j["during"] and j["afterClear"] == [], str([j["during"], j["afterClear"]]))
        ck("inertness: with the session disarmed, the identical solve writes not "
           "one frame — recovery cannot leak into a normal run",
           all("ckpt" not in n for n in j["afterUnarmed"]), str(j["afterUnarmed"]))

        # ---- 7. settlement
        ck("settlement: no scenario left a frame behind for a later resume to "
           "mistake for work",
           not list(base.glob("*/traces/*.ckpt.json"))
           and not list(base.glob("*/traces/*.tmp*")),
           str([str(p.relative_to(base)) for p in
                list(base.glob("*/traces/*.ckpt*")) +
                list(base.glob("*/traces/*.tmp*"))][:3]))

    width = max(len(n) for n, _, _ in CHECKS)
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name.ljust(width)}  {detail}")
    n = sum(ok for _, ok, _ in CHECKS)
    print(f"\ntask-granular recovery: {n}/{len(CHECKS)} checks passed")
    return 0 if n == len(CHECKS) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        raise SystemExit(child_main(Path(sys.argv[2]), sys.argv[3:]))
    raise SystemExit(main())
