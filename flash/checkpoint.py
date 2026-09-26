"""R-5.3 task-granular recovery: the in-flight task survives ``kill -9``.

§33.7 gave the SUITE a snapshot — the trace session is the record of which
tasks settled, so `flash resume` continues at the next unfinished one. That
snapshot has a hole exactly one task wide: the task that was generating when
the process died has no `task_end` record, so a resume re-runs it from the
first token. On the small tier that re-bills a whole generation per kill; for a
multi-file or edit task it also throws away the file set the earlier attempts
built up, which is the state the loop exists to accumulate.

This module keeps the other kind of record: one small JSON file per session,
rewritten as the in-flight task advances, holding

* the prompt and the tokens already decoded (`prompt`, `partial`, `ids`) — so a
  resume continues the SAME generation instead of restarting it,
* the conversation (`messages`) and the finished attempts (`done`) — so an
  attempt that already cost a generation is not paid for twice,
* the caller's sandbox state (`state`) — the multi-file `merged` set, the edit
  arm's `workspace`, the tournament's scored candidates,
* which model was speaking (`stage`) — a kill during the brain's pass must not
  re-run the fast tier's chain.

Two properties are load-bearing, and both are tested with a REAL `kill -9`
rather than a simulation of one. **Atomicity**: a save writes to a temp name and
`os.replace`s it, so a reader never sees a half-written frame — a process killed
between the write and the rename leaves the previous frame, older but complete.
**Inertness**: with no session armed, every entry point returns None and writes
nothing, because `flash run` (one task, one process) must not start touching a
recovery file it will never read.

What a resumed generation is NOT: the model's KV cache died with the process, so
the continuation is conditioned on the same *text* prefix, re-prefilled. It can
differ from what the killed run would have produced, and the boundary token may
be re-cut by the tokenizer. What it never does is re-decode the tokens already
on disk — which is the claim R-5.3's vector actually makes.

Offline: `python -m flash.checkpoint --selftest`.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from flash import trace

EVERY = 16             # decoded tokens between flushes
LIMIT = 20000          # characters of partial text a frame is allowed to hold

ERRORS = 0             # saves that failed — recovery must not fail silently
_CUR: dict = {"sid": None, "frame": None}


def path_for(sid: str) -> Path:
    """Sibling of the session's trace file, so one `sid` names both halves."""
    return Path(trace.DIR) / f"{sid}.ckpt.json"


# ---------------------------------------------------------------- the frame
@dataclass
class Frame:
    """Everything needed to continue the task that was running."""

    sid: str = ""
    task_id: str = ""
    kind: str = "chain"              # chain | edits | tourney — which loop owns it
    stage: str = "small"             # small | big — whose model is generating
    attempt: int = 0                 # index of the generation in flight
    max_tokens: int = 1024
    temp: float = 0.0
    seed: int | None = None
    messages: list = field(default_factory=list)
    prompt: str = ""                 # chat-template-applied prompt, as generated
    contract: dict | None = None     # {mode, names} — R-4.2's mask, rebuilt by replay
    partial: str = ""                # text decoded since the last fold into `prompt`
    answer: str = ""                 # every character this generation has decoded
    ids: list = field(default_factory=list)     # every token id this generation, folded or not
    carried: int = 0                 # how many of them are already in `prompt`
    state: dict = field(default_factory=dict)   # caller-owned sandbox state
    done: list = field(default_factory=list)    # finished Attempt dicts
    ms: int = 0                      # generation ms already spent
    seq: int = 0                     # saves of this frame
    ts: float = 0.0

    @property
    def decoded(self) -> int:
        """Tokens this generation has produced, folded into the prompt or not."""
        return len(self.ids)

    def fields(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def parse(cls, raw: dict) -> "Frame":
        """From JSON, keeping only the fields this build knows: a frame written
        by a newer loop must still be readable by an older one."""
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})


# ------------------------------------------------------------------ arming
def sweep(sid: str) -> int:
    """Delete this session's orphan temp saves, returning how many went.

    A kill can land between the write and the rename: the frame under its real
    name stays whole, and garbage is left under `.tmp<pid>`. Nothing reads that
    garbage, but a 24h chaos run leaves a piece per unlucky kill, and a recovery
    path that slowly fills a disk is its own outage.
    """
    n = 0
    try:
        for stale in Path(trace.DIR).glob(f"{sid}.ckpt.json.tmp*"):
            try:
                stale.unlink()
                n += 1
            except OSError:
                pass
    except Exception:
        pass
    return n


def arm(sid: str) -> None:
    """Only a suite has work worth recovering; `flash run` never arms this.

    Arming sweeps first: whoever holds the `sid` now is the only process that
    can still write it, so any temp file naming it belongs to a dead one.
    """
    _CUR["sid"] = sid
    sweep(sid)


def disarm() -> None:
    """Drop the session *and* the span it owned.

    Both, because a stale in-memory frame would keep a later, unrelated
    generation folding and carrying text that no disk file is recording — the
    resume would then continue a span nobody paid for.
    """
    _CUR["sid"] = None
    _CUR["frame"] = None


def armed() -> bool:
    return _CUR["sid"] is not None


def session() -> str | None:
    """The session whose frames this process owns, or None when unarmed."""
    return _CUR["sid"]


def current() -> Frame | None:
    return _CUR["frame"]


def adopt(frame: Frame) -> Frame:
    """Take a frame read off disk as the one in flight — the resume entry point.

    Without this the reader of a dead session's file has the state and nowhere
    to put it; with it, `carry()` and `note()` work on the resumed run exactly
    as they did on the killed one.
    """
    _CUR["frame"] = frame
    if not _CUR["sid"]:
        _CUR["sid"] = frame.sid
    return frame


def active(task_id: str, attempt: int) -> Frame | None:
    """The frame that owns THIS generation, or None.

    Identity is (task, attempt) rather than "whatever is running", so a
    generator called outside the armed loop — the router's probe, a confidence
    re-run — cannot append its tokens to a task's checkpoint.
    """
    f = _CUR["frame"]
    if f is not None and f.task_id == task_id and f.attempt == attempt:
        return f
    return None


def owns(task_id: str, kind: str, stage: str) -> Frame | None:
    """The adopted frame, if THIS arm is the one that was running it.

    An armed suite adopts whatever the dead session left, and then every arm is
    asked the same question — is this mine? The chain, the patch loop and the
    tournament each answer for themselves, so a frame a killed tournament left
    is never mistaken for a killed chain's attempt 0, which would resume the
    wrong state machine over the right text.
    """
    f = _CUR["frame"]
    if (f is not None and f.task_id == task_id and f.kind == kind
            and f.stage == stage):
        return f
    return None


# ----------------------------------------------------------------- writing
def save(frame: Frame) -> None:
    """Rewrite the session's frame atomically. No armed session, no file.

    fsync before the rename: a chaos run that loses power — not only a signal —
    must not find a frame whose bytes the disk never got.
    """
    global ERRORS
    if _CUR["sid"] is None:
        return
    frame.ts = round(time.time(), 3)
    frame.seq += 1
    p = path_for(_CUR["sid"])
    tmp = p.with_name(f"{p.name}.tmp{os.getpid()}")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w") as fh:
            json.dump(frame.fields(), fh, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        ERRORS += 1
        try:
            tmp.unlink()
        except OSError:
            pass


def begin(task_id: str, kind: str, stage: str, attempt: int, *,
          messages: list | None = None, max_tokens: int = 1024,
          state: dict | None = None, done: list | None = None) -> Frame | None:
    """Open the checkpoint for a task, or for the next attempt of it.

    Called at the top of an attempt, so a kill *between* attempts still finds a
    frame naming the attempt that had not started — `attempt` plus `done` is
    what tells a resume what has already been paid for.

    When the frame already names this very generation it is the one a dead
    process left behind, and its decoded span is the reason it exists: the
    boundary fields advance, the span survives. Opening over it would delete
    the only bytes this task has been billed for.
    """
    if _CUR["sid"] is None:
        return None
    f = _CUR["frame"]
    if live(f, task_id, kind, stage, attempt):
        for k, v in (("messages", list(messages or f.messages)),
                     ("max_tokens", max_tokens),
                     ("state", dict(state or f.state)),
                     ("done", list(done or f.done))):
            setattr(f, k, v)
        save(f)
        return f
    f = Frame(sid=_CUR["sid"], task_id=task_id, kind=kind, stage=stage,
              attempt=attempt, messages=list(messages or []),
              max_tokens=max_tokens, state=dict(state or {}),
              done=list(done or []))
    save(f)
    _CUR["frame"] = f
    return f


def live(f: Frame | None, task_id: str, kind: str, stage: str,
         attempt: int) -> bool:
    """Whether `f` is the span THIS generation is continuing, not replacing.

    Four parts, because a task has four nested identities and a resume that
    matches on fewer would continue the wrong text: the same attempt index at
    the same tier of the same arm of the same task.
    """
    return (f is not None and f.task_id == task_id and f.kind == kind
            and f.stage == stage and f.attempt == int(attempt))


def update(**fields) -> Frame | None:
    """Advance the in-flight frame at a boundary and flush it."""
    f = _CUR["frame"]
    if f is None:
        return None
    for k, v in fields.items():
        setattr(f, k, v)
    save(f)
    return f


def started(task_id: str, attempt: int, prompt: str, *, temp: float,
            seed: int | None, contract: dict | None) -> None:
    """Record the generation about to stream: its prompt, its sampler settings,
    and the contract needed to rebuild a mask by replay.

    A fresh span, so the ids of a previous attempt never leak into this one's
    budget or its mask position.
    """
    f = active(task_id, attempt)
    if f is None:
        return
    f.prompt, f.temp, f.seed = prompt, temp, seed
    f.contract = contract
    f.partial, f.answer, f.ids, f.carried = "", "", [], 0
    save(f)


def note(token_id: int, text: str, started_at: float) -> None:
    """One decoded token. Flushes every `EVERY`, not every token: the frame is
    worth a few hundred microseconds per 16 steps, not per step.

    Past `LIMIT` characters the text stops being kept (the ids still are, for
    the budget) and the span is no longer resumable — `carry()` refuses rather
    than continuing a truncated answer, because continuing half a file would
    produce a wrong answer that looks like a recovered one.
    """
    f = _CUR["frame"]
    if f is None:
        return
    f.ids.append(int(token_id))
    if len(f.partial) <= LIMIT:
        f.partial += text
    else:
        f.partial = f.partial[:LIMIT + 1] + "…"      # the marker carry() looks for
    if not f.partial.endswith("…"):
        f.answer += text
    f.ms = round((time.perf_counter() - started_at) * 1000)
    if len(f.ids) % EVERY == 0:
        save(f)


def carry(task_id: str, attempt: int) -> tuple[str, str, int] | None:
    """What this generation must continue from: the prefix to prefill, the WHOLE
    answer text produced so far, and how many tokens that cost.

    The text is the cumulative `answer`, not the pending `partial`: a generation
    can be killed more than once, and a caller that prefilled only the newest
    segment would hand the model half an answer and get back the other half
    prepended to it.

    Consuming it folds the pending text into `prompt` and clears the pending
    span, so a second call returns None by construction — and if THIS run dies
    as well, the next resume carries the longer prefix. None means "continue
    nothing": no frame, no text, or a span too large to keep exactly.

    A spent budget still carries its text. The caller has to decide what to do
    with an answer that is finished but never verified, and silently discarding
    it would make the ceiling a data-loss path.
    """
    f = active(task_id, attempt)
    if f is None or not f.partial or f.partial.endswith("…"):
        return None
    prefix, head, n = f.prompt + f.partial, f.answer, f.decoded
    f.prompt, f.partial = prefix, ""
    f.carried = n                       # `ids` stays whole: a mask replays all of it
    save(f)
    return prefix, head, n


def pending_ids(task_id: str, attempt: int) -> list:
    """Token ids this generation has already produced — R-4.2's mask is rebuilt
    by walking them, which is the same walk the killed run performed."""
    f = active(task_id, attempt)
    return [] if f is None else list(f.ids)


def resume(task_id: str, attempt: int, prompt: str, *, temp: float,
           seed: int | None, contract: dict | None) -> tuple | None:
    """The generation-facing seam: what to continue from, or None.

    Called at the top of every `_generate`, and it answers one of three
    questions:

    * *not our generation* (no frame, or one naming another task or attempt) —
      None, and the caller decodes exactly as it did before recovery existed.
      This is why `flash run` and an unarmed suite are untouched by this module.
    * *a dead process left text here* — `(prefix to prefill, text already
      decoded, tokens it cost)`. The span stays open, so a second kill carries
      from the new place rather than the old one.
    * *our generation, nothing decoded* — None, having opened the span fresh, so
      what this run produces is being written down from token one.
    """
    f = active(task_id, attempt)
    if f is None:
        return None
    c = carry(task_id, attempt)
    if c is None:
        started(task_id, attempt, prompt, temp=temp, seed=seed, contract=contract)
    return c


def fold(task_id: str, attempt: int) -> None:
    """Hand the whole span back: the caller has the finished text, so nothing
    here may be mistaken for an interrupted answer by the next resume.

    Without this, a kill during VERIFICATION — after the answer was decoded but
    before its verdict landed — would leave a completed generation looking like
    a half one, and the resume would prefill an answer and keep writing past the
    end of it.
    """
    f = active(task_id, attempt)
    if f is None or not f.partial:
        return
    f.prompt, f.partial = f.prompt + f.partial, ""
    f.carried = f.decoded
    save(f)


# ----------------------------------------------------------------- reading
def load(sid: str) -> Frame | None:
    """The frame a session died with, or None.

    A torn or unreadable file reads as no frame rather than raising: recovery
    is the bonus, and the suite-level snapshot in the trace still stands on its
    own. The atomic rename is what makes "torn" here mean "an old frame", not
    "a half one".
    """
    p = path_for(sid)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw.get("task_id"):
        return None
    return Frame.parse(raw)


def handoff(sid: str, task_id: str) -> Frame | None:
    """Adopt the frame a dead session left on THIS task, or find nothing.

    The resume entry point a suite calls once per task. Naming the task is the
    guard: a session that died on `t07` has nothing to hand to `t08`, and a
    frame carried into the wrong task would prefill one problem's half-answer
    into another's prompt.
    """
    f = load(sid)
    if f is None or f.task_id != task_id:
        return None
    return adopt(f)


def clear(sid: str) -> None:
    try:
        path_for(sid).unlink()
    except OSError:
        pass                # nothing to clear, or already gone: both are the goal


def finish() -> None:
    """The task settled — its checkpoint is history, and history lives in the
    trace. Leaving the file would offer a resume into a finished task."""
    sid = _CUR["sid"]
    _CUR["frame"] = None
    if sid:
        clear(sid)


def pending(sid: str) -> str | None:
    """The task a session has to finish, if any — for the interrupt notice."""
    f = load(sid)
    return None if f is None else f.task_id


# ------------------------------------------------------------------ selftest
_CHILD = '''
import sys, time
sys.path.insert(0, %(root)s)
from flash import checkpoint as ck, trace
trace.DIR = %(dir)s
ck.arm(%(sid)s)
i = 0
while True:
    i += 1
    ck.begin("killed_task", "chain", "small", 0, state={"i": i},
             messages=[{"role": "user", "content": "n=%%d %%s" %% (i, "x" * (i %% 233))}])
    ck.started("killed_task", 0, "P", temp=0.0, seed=None, contract=None)
    for j in range(16):
        ck.note(i * 16 + j, "tok", time.perf_counter())
'''


def run_selftest(verbose: bool = True) -> int:
    """Deterministic and offline: a temp dir, a stubbed decode span, and two
    real SIGKILLs of a child that is mid-write."""
    import signal
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    checks: list[tuple[str, bool, str]] = []
    root = str(Path(__file__).resolve().parent.parent)

    def ck(label, cond, detail=""):
        checks.append((label, bool(cond), str(detail)))
        if verbose:
            print(f"  {'OK  ' if cond else 'FAIL'} {label}"
                  + (f"  [{detail}]" if detail else ""))

    with tempfile.TemporaryDirectory() as d:
        old_dir, trace.DIR = trace.DIR, Path(d)
        try:
            # ------------------------------------------------------------ inert
            disarm()
            ck("inert: with no session armed, begin() answers None and writes nothing",
               begin("t1", "chain", "small", 0) is None
               and active("t1", 0) is None and note(1, "x", time.perf_counter()) is None
               and carry("t1", 0) is None and load("nope") is None
               and list(Path(d).glob("*")) == [],
               str([p.name for p in Path(d).glob("*")]))

            arm("s1")
            ck("naming: the frame is a sibling of the session's own trace file",
               path_for("s1").name == "s1.ckpt.json")

            # -------------------------------------------------------- lifecycle
            f = begin("t1", "chain", "small", 0,
                      messages=[{"role": "user", "content": "p"}], max_tokens=64)
            ck("begin: the frame lands on disk at once, before any token",
               f is not None and load("s1").attempt == 0 and f.decoded == 0)
            started("t1", 0, "PROMPT", temp=0.7, seed=3,
                    contract={"mode": "fence", "names": []})
            t0 = time.perf_counter()
            for i in range(EVERY - 1):
                note(i, "a", t0)
            ck("flush: nothing lands between boundaries — the frame still says 0",
               load("s1").decoded == 0, str(load("s1").decoded))
            note(EVERY - 1, "a", t0)
            g = load("s1")
            ck("flush: at the EVERY-th token the text and its ids are durable",
               g.decoded == EVERY and g.partial == "a" * EVERY
               and g.ids == list(range(EVERY)) and g.temp == 0.7 and g.seed == 3
               and g.contract == {"mode": "fence", "names": []},
               str([g.decoded, g.temp, g.seed]))
            ck("flush: until the first fold, the whole generation is the pending span",
               g.prompt == "PROMPT" and g.carried == 0)

            update(attempt=1, state={"merged": {"a.py": "x = 1\n"}},
                   done=[{"ok": False, "err": "STATIC: x"}])
            g = load("s1")
            ck("boundary: attempt index, sandbox state and finished attempts survive",
               g.attempt == 1 and g.state["merged"]["a.py"] == "x = 1\n"
               and g.done[0]["err"].startswith("STATIC"), str(g.attempt))
            ck("identity: only the (task, attempt) that owns the frame may write it",
               active("t1", 1) is not None and active("t1", 0) is None
               and active("other", 1) is None)

            # ----------------------------------------------------- continuation
            # A new attempt opens a fresh span, so what a resume carries is that
            # attempt's own text and never its predecessor's.
            started("t1", 1, "PROMPT", temp=0.0, seed=None, contract=None)
            ck("span: a new attempt carries no text over from the previous one",
               load("s1").decoded == 0 and load("s1").partial == "")
            t0 = time.perf_counter()
            for i in range(EVERY):
                note(i, "b", t0)
            c = carry("t1", 1)
            g = load("s1")
            ck("carry: the prefix to prefill, the answer text so far and its token count",
               c is not None and c[0] == "PROMPT" + "b" * EVERY and c[1] == "b" * EVERY
               and c[2] == EVERY, str(c)[:34])
            ck("carry: the ids of the whole generation stay for a mask to replay, "
               "even after the text they belong to has been folded",
               g.carried == EVERY and pending_ids("t1", 1) == list(range(EVERY))
               and g.partial == "" and g.ids == list(range(EVERY)), str(g.carried))
            ck("carry: consumed once — a second call continues nothing",
               carry("t1", 1) is None)
            t0 = time.perf_counter()
            for i in range(EVERY):
                note(100 + i, "c", t0)
            c = carry("t1", 1)      # the SECOND kill, after one carry already folded
            ck("composition: a second kill carries the LONGER prefix, and its "
               "text is the WHOLE answer so far, not just the newest segment — a "
               "caller prefilled with half an answer would finish with the wrong one",
               c[0] == "PROMPT" + "b" * EVERY + "c" * EVERY
               and c[1] == "b" * EVERY + "c" * EVERY
               and c[2] == 2 * EVERY, str([c[1][:8], c[2]]))
            # Text pending AND budget spent: the answer is finished but was never
            # verified, so the ceiling must carry its text forward, not drop it.
            t0 = time.perf_counter()
            for i in range(EVERY):
                note(200 + i, "d", t0)
            g = update(max_tokens=3 * EVERY)
            # `update` returns the live frame and `carry` folds it in place, so
            # what it held must be read BEFORE the call — checking the same
            # object afterwards tests a copy that was never made.
            was_partial, was_decoded = g.partial, g.decoded
            c = carry("t1", 1)
            ck("budget: a generation that reached its ceiling still carries its answer — "
               "the limit is not allowed to become a data-loss path",
               bool(was_partial) and was_decoded == g.max_tokens == 3 * EVERY
               and c is not None and c[1] == "b" * EVERY + "c" * EVERY + "d" * EVERY
               and c[2] == 3 * EVERY,
               f"decoded={was_decoded} max={g.max_tokens}")
            ck("truncation: a span past the size limit is refused, not half-resumed",
               update(max_tokens=64, partial="y" * (LIMIT + 1)) is not None
               and note(999, "z", time.perf_counter()) is None
               and current().partial.endswith("…") and carry("t1", 1) is None,
               f"{len(current().partial)} chars")

            # -------------------------------------------------------- lifecycle2
            ck("clear: settling a task deletes the frame — a finished task must not "
               "look resumable", finish() is None and load("s1") is None)
            ck("clear: and a disarmed session deletes nothing by accident",
               (arm("s1"), begin("t1b", "chain", "small", 0), disarm(),
                load("s1") is not None and begin("x", "chain", "small", 0) is None))
            disarm()
            p = path_for("s2")
            arm("s2")
            p.write_text("{ this is not json")
            ck("a corrupt frame reads as no frame, not as an exception",
               load("s2") is None)
            ck("unknown fields are ignored, so a newer frame is still readable",
               load("s2") is None and Frame.parse({"task_id": "z", "sid": "s2",
                                                   "invented_future_field": 1}).task_id == "z")
            p.unlink()
            ck("no file at all is also no frame", load("s2") is None)
            disarm()
            # Arming is where the sweep actually runs, and it must not reach
            # across sessions — two suites can be recorded in one directory.
            (Path(d) / "s9.ckpt.json.tmp1").write_text("orphan")
            (Path(d) / "s8.ckpt.json.tmp2").write_text("not mine")
            arm("s9")
            ck("arm: taking a session sweeps its dead processes' temps as it arms",
               not (Path(d) / "s9.ckpt.json.tmp1").exists()
               and (Path(d) / "s8.ckpt.json.tmp2").exists())
            (Path(d) / "s8.ckpt.json.tmp2").unlink()
            disarm()

            # --------------------------------- real SIGKILL, twice, racing writes
            def killed(kill_at: float):
                """Run the child for `kill_at` seconds, SIGKILL it, and return
                (reads the parent made, whole frames among them, the final frame,
                the exit status, the child's stderr).

                The parent reads WHILE the child writes: that is the race a
                write-in-place loop would lose, leaving the name pointing at a
                half-written object. The kill lands at an arbitrary point in the
                span, so what survives is asserted ACTIONABLE at any position
                rather than pinned to one.
                """
                kp = path_for("sk")
                prog = _CHILD % {"root": json.dumps(root), "dir": json.dumps(d),
                                 "sid": json.dumps("sk")}
                kid = subprocess.Popen([sys.executable, "-u", "-c", prog],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, text=True)
                reads, good = [], 0
                t_end = time.perf_counter() + kill_at
                while time.perf_counter() < t_end and kid.poll() is None:
                    try:
                        raw = json.loads(kp.read_text()) if kp.exists() else None
                    except Exception:
                        raw = "TORN"
                    reads.append(raw)
                    if isinstance(raw, dict) and raw.get("task_id") == "killed_task":
                        good += 1
                os.kill(kid.pid, signal.SIGKILL)
                kid.wait(timeout=10)
                err = (kid.stderr.read() or "")[-160:]
                try:
                    final = json.loads(kp.read_text())
                except Exception:
                    final = None
                return reads, good, final, kid.returncode, err

            def actionable(r):
                """Any point in the span is legal; a half object is not. Accepts
                a frame read off disk or one still in memory."""
                if r is not None and not isinstance(r, dict):
                    r = r.fields()
                return (isinstance(r, dict) and r.get("task_id") == "killed_task"
                        and r.get("attempt") == 0 and isinstance(r.get("ids"), list)
                        and isinstance(r.get("state", {}).get("i"), int)
                        and 0 <= len(r["ids"]) <= EVERY)

            reads, good, final, rc, cerr = killed(0.8)
            torn = sum(1 for r in reads if r == "TORN")
            ck("kill -9 during writes: not one read the parent made was a half frame",
               torn == 0 and good > 0 and rc == -signal.SIGKILL,
               f"{len(reads)} reads, {good} whole, {torn} torn {cerr}")
            ck("kill -9: whatever survives is a frame a resume can act on — task, "
               "attempt, span and sandbox state all present",
               actionable(final), str(final)[:78] if final else "")
            tmps = sorted(q.name for q in Path(d).glob("*.tmp*"))
            ck("kill -9: the frame is never torn, but a kill between the write and "
               "the rename can orphan a temp — which is what the sweep is for",
               len(tmps) <= 1, str(tmps))
            (Path(d) / "sk.ckpt.json.tmp99999").write_text("orphan")
            n_swept = sweep("sk")
            ck("sweep: taking a session deletes its dead processes' temps and keeps "
               "the frame", n_swept == len(tmps) + 1
               and not list(Path(d).glob("sk.ckpt.json.tmp*")) and actionable(load("sk")),
               f"tmps={tmps} swept={n_swept}")
            # The torn-read count above is a result, not a tautology: cut the
            # frame in half by hand and `load` stops recognising it.
            kp = path_for("sk")
            whole = kp.read_text()
            kp.write_text(whole[:len(whole) // 2])
            ck("mutation: a half-written frame reads as nothing — so zero torn reads "
               "is something the atomic rename earns", load("sk") is None)
            kp.write_text(whole)
            ck("mutation restored: the same bytes read as a frame again",
               actionable(json.loads(kp.read_text())))

            reads2, good2, final2, rc2, _ = killed(2.0)
            ck("durability is not a once-only trick: a longer race, still zero torn",
               all(r != "TORN" for r in reads2) and good2 > 10,
               f"{len(reads2)} reads, {good2} whole")
            ck("the surviving frame is a LATER one, so saves really land",
               actionable(final2) and final2["state"]["i"] > final["state"]["i"],
               f"i={final['state']['i']} then i={final2['state']['i']}")

            # ------------------------------------------- a save that cannot land
            err_before = ERRORS
            blocker = Path(d) / "blocker"
            blocker.write_text("")           # a file where a directory must be
            trace.DIR = blocker / "sub"
            arm("s3")
            got = begin("t3", "chain", "small", 0)
            ck("policy: a save that cannot land is counted, not swallowed — recovery "
               "that quietly stopped working is worse than none",
               got is not None and ERRORS == err_before + 1 and current() is got,
               f"ERRORS={ERRORS}")
            ck("policy: the failure costs the run nothing, and every failed save is "
               "counted rather than only the first",
               note(1, "x", time.perf_counter()) is None
               and update(attempt=3) is not None and ERRORS == err_before + 2,
               f"ERRORS={ERRORS}")
        finally:
            trace.DIR = old_dir
            disarm()

    width = max(len(n) for n, _, _ in checks)
    for name, ok, detail in checks:
        print(f"  {'OK  ' if ok else 'FAIL'} {name.ljust(width)}  {detail}")
    n = sum(ok for _, ok, _ in checks)
    print(f"\ncheckpoint selftest: {n}/{len(checks)} checks passed")
    return 0 if n == len(checks) else 1


if __name__ == "__main__":                       # pragma: no cover
    import sys
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        raise SystemExit(run_selftest())
    print(__doc__)
