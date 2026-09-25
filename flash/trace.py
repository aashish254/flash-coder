"""Trace store & replay (PLAN §33.6) + the session snapshot behind `flash resume` (§33.7).

One JSONL file per session; every route decision, generation (with token
spend) and verification is one appended record. Two payoffs from the SAME
file:
  - replay: `flash trace show <sid>` reconstructs which decision went wrong,
    and `--full` prints the stored prompts so a run can be re-fed to a model;
  - resumability: a session's `task_end` records ARE its snapshot, so `flash
    resume` continues an interrupted suite from the next unfinished task
    instead of restarting it. A session with no `session_end` is interrupted.

Like the ledger, recording must never cost the agent work: every write is
swallowed, and a corrupt line is skipped rather than fatal (§33.9.7).
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "results" / "traces"

CAPTURE = False          # set by --trace-full: store prompt/output text for replay
MAX_ERR = 400

_CUR: dict = {"sid": None, "seq": 0, "path": None}

_BLOBS = ("prompt", "output", "err", "detail")


def new_id(label: str) -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{label}-{uuid.uuid4().hex[:4]}"


def active() -> bool:
    return _CUR["sid"] is not None


def current() -> str | None:
    return _CUR["sid"]


def path_for(sid: str, dir: str | Path | None = None) -> Path:
    return (Path(dir) if dir else DIR) / f"{sid}.jsonl"


def open_session(label: str = "session", **meta) -> str:
    """Start a fresh session; later event() calls append to its file."""
    sid = new_id(label)
    p = path_for(sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    _CUR.update(sid=sid, seq=0, path=p)
    event("session_start", label=label, **meta)
    return sid


def attach(sid: str, dir: str | Path | None = None, **meta) -> str:
    """Re-open an existing session for appending — the §33.7 resume path."""
    p = path_for(sid, dir)
    if not p.exists():
        raise FileNotFoundError(f"no trace session '{sid}' in {p.parent}")
    _CUR.update(sid=sid, seq=max([r.get("seq", 0) for r in read(sid, dir)] or [0]) + 1,
                path=p)
    event("session_start", resumed=True, **meta)
    return sid


def close_session(**meta) -> None:
    """Mark the session complete — an unclosed session is resumable."""
    if active():
        event("session_end", **meta)
    _CUR.update(sid=None, seq=0, path=None)


def event(type: str, task_id: str | None = None, **fields) -> dict | None:
    """Append one record. No-op (returns None) when no session is open."""
    if not active():
        return None
    rec = {"ts": round(time.time(), 3), "seq": _CUR["seq"], "type": type}
    _CUR["seq"] += 1
    if task_id:
        rec["task_id"] = task_id
    rec.update({k: v for k, v in fields.items() if v is not None})
    try:
        with open(_CUR["path"], "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass
    return rec


def n_tokens(tokenizer, text) -> int | None:
    """Real token spend for one string (free when no session is open)."""
    if not active() or tokenizer is None or text is None:
        return None
    try:
        return len(tokenizer.encode(text))
    except Exception:
        return None


def read(sid: str, dir: str | Path | None = None) -> list[dict]:
    p = path_for(sid, dir)
    if not p.exists():
        return []
    out = []
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue               # a half-written line must not lose the session
    return out


def list_sessions(dir: str | Path | None = None) -> list[str]:
    root = Path(dir) if dir else DIR
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.jsonl"))


def positions(sid: str, dir: str | Path | None = None) -> dict[str, dict]:
    """Loop position: task_id -> its LAST task_end record (the snapshot)."""
    return {r["task_id"]: r for r in read(sid, dir)
            if r.get("type") == "task_end" and r.get("task_id")}


def session_params(sid: str, dir: str | Path | None = None) -> dict | None:
    """The parameters a session ran under — enough to continue it blind."""
    recs = [r for r in read(sid, dir) if r.get("type") == "session_start"]
    return (recs[-1].get("params") or None) if recs else None


def interrupted(cmd: str | None = None, dir: str | Path | None = None) -> list[str]:
    """Sessions that never closed, newest last: everything `flash resume` can continue."""
    out = []
    for sid in list_sessions(dir):
        recs = read(sid, dir)
        starts = [r for r in recs if r.get("type") == "session_start"]
        if not starts or any(r.get("type") == "session_end" for r in recs):
            continue
        if cmd and starts[-1].get("cmd") != cmd:
            continue
        out.append(sid)
    return out


def summarize(sid: str, dir: str | Path | None = None) -> dict:
    recs = read(sid, dir)
    done = {r["task_id"] for r in recs if r.get("type") == "task_end" and r.get("task_id")}
    solved = {r["task_id"] for r in recs
              if r.get("type") == "task_end" and r.get("solved") and r.get("task_id")}
    gens = [r for r in recs if r.get("type") == "generate"]
    vers = [r for r in recs if r.get("type") == "verify"]
    starts = [r for r in recs if r.get("type") == "session_start"]
    return {
        "session": sid,
        "cmd": (starts[-1].get("cmd") if starts else None),
        "closed": any(r.get("type") == "session_end" for r in recs),
        "events": len(recs),
        "tasks": len(done),
        "solved": len(solved),
        "generations": len(gens),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in gens),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in gens),
        "gen_s": round(sum(r.get("ms") or 0 for r in gens) / 1000, 2),
        "verify_s": round(sum(r.get("ms") or 0 for r in vers) / 1000, 2),
        "wall_s": (round(recs[-1]["ts"] - recs[0]["ts"], 1) if len(recs) > 1 else 0.0),
        "seconds": round(sum(r.get("seconds") or 0 for r in recs
                             if r.get("type") == "task_end"), 1),
    }


_ORDER = ("cmd", "label", "tier", "routed", "route_p", "profile", "allow_big",
          "big_allowed", "attempt", "temp", "max_tokens", "prompt_tokens",
          "completion_tokens", "ok", "kind", "solved", "attempts", "files",
          "multi", "denied", "why", "reason", "ms", "seconds", "resumed",
          "tasks", "ran")


def _one_line(rec: dict, full: bool = False) -> str:
    tid = rec.get("task_id") or ""
    parts = []
    for k in _ORDER:
        if k in rec:
            v = rec[k]
            parts.append(f"{k}={v:.3f}" if isinstance(v, float) and k.endswith("_p")
                         else f"{k}={v}")
    blobs = [f"{k}={rec[k]!r}" for k in _BLOBS if k in rec]
    if full and blobs:
        head = " ".join(parts)
        body = "\n".join(f"           {b}" for b in blobs)
        return f"[{rec['seq']:04d}] {rec['type']:<14}{tid:<20} {head}\n{body}"
    if "err" in rec and not full:
        parts.append(f"err={rec['err'][:60]!r}")
    return f"[{rec['seq']:04d}] {rec['type']:<14}{tid:<20} {' '.join(parts)}"


def render(sid: str, task_id: str | None = None, full: bool = False,
           dir: str | Path | None = None) -> str:
    """The replay view: this session's decisions, in the order they happened."""
    recs = read(sid, dir)
    if not recs:
        return f"(no trace records for session {sid})"
    if task_id:
        recs = [r for r in recs if r.get("task_id") in (task_id, None)]
    s = summarize(sid, dir)
    head = [f"session {sid}   cmd={s['cmd']}   {'closed' if s['closed'] else 'INTERRUPTED — resumable'}",
            f"tasks {s['solved']}/{s['tasks']} solved · {s['generations']} generations · "
            f"{s['prompt_tokens']}/{s['completion_tokens']} prompt/complete tokens · "
            f"{s['gen_s']}s generating, {s['verify_s']}s verifying, {s['seconds']}s task wall"]
    tail = ("" if s["closed"] else
            f"\n\nresume with:  flash resume {sid}")
    return "\n".join(head) + "\n" + "\n".join(_one_line(r, full) for r in recs) + tail



# ---------------------------------------------------------------- selftest

def run_selftest() -> int:
    """Deterministic, offline, tempdir-only: no models, no live sessions."""
    import tempfile
    from flash import trace

    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, cond, detail: str = "") -> None:
        checks.append((name, bool(cond), detail))

    with tempfile.TemporaryDirectory() as d:
        old, trace.DIR = trace.DIR, Path(d)
        try:
            ck("inert: no session -> event() returns None",
               trace.event("generate", task_id="x") is None and not trace.active())
            ck("inert: token counting is free when off",
               trace.n_tokens(None, "hello") is None)

            sid = trace.open_session("run-suite", cmd="run-suite",
                                     params={"tasks": "m0", "allow_big": "always"})
            ck("open: names the file by session id and activates",
               trace.active() and trace.path_for(sid).exists() and trace.current() == sid)
            trace.event("route", task_id="t01", routed="small", route_p=0.12,
                        profile="balanced", allow_big="auto", big_allowed=False,
                        multi=False)
            trace.event("generate", task_id="t01", attempt=0, ms=3100,
                        prompt_tokens=4112, completion_tokens=184, temp=0.0)
            trace.event("verify", task_id="t01", attempt=0, ok=True, kind="test", ms=420)
            trace.event("task_end", task_id="t01", solved=True, tier="small",
                        attempts=1, seconds=3.5)
            trace.event("generate", task_id="t02", attempt=0, ms=2900,
                        prompt_tokens=900, completion_tokens=210, temp=0.7)
            trace.event("verify", task_id="t02", attempt=0, ok=False, kind="test",
                        ms=30, err="AssertionError: GOT 3 WANT 4")
            trace.event("escalation", task_id="t02", tier="big", ms=1500, denied=False)
            trace.event("verify", task_id="t02", attempt=1, ok=False, kind="static",
                        ms=5, err="L12: unterminated string")
            trace.event("task_end", task_id="t02", solved=False, tier="failed",
                        attempts=2, seconds=4.4)

            recs = trace.read(sid)
            ck("store: append-only, seq monotonic from 0",
               len(recs) == 10 and [r["seq"] for r in recs] == list(range(10)))
            ck("store: None-valued fields are dropped, False is kept",
               "detail" not in recs[1] and recs[7]["denied"] is False)
            ck("store: floats round-trip",
               [r for r in recs if r["type"] == "route"][0]["route_p"] == 0.12)

            trace.close_session(solved=1)
            ck("close: writes session_end and goes inert",
               not trace.active() and trace.read(sid)[-1]["type"] == "session_end"
               and trace.event("route", task_id="t99") is None
               and len(trace.read(sid)) == 11)

            s = trace.summarize(sid)
            ck("summary: token and time totals",
               (s["prompt_tokens"], s["completion_tokens"], s["gen_s"],
                s["verify_s"], s["tasks"], s["solved"], s["closed"])
               == (5012, 394, 6.0, 0.46, 2, 1, True), str(s))

            # ---- §33.7: task_end records ARE the snapshot ----
            sid2 = trace.open_session("run-suite", cmd="run-suite",
                                      params={"tasks": "m0"})
            for tid in ("t01", "t02"):
                trace.event("task_end", task_id=tid, solved=True, tier="small",
                            attempts=1, seconds=2.0)
            trace.event("task_end", task_id="t03", solved=False, tier="shed",
                        attempts=2, seconds=9.0)
            ck("resume: position tracks the LAST result per task",
               set(trace.positions(sid2)) == {"t01", "t02", "t03"})
            trace.event("task_end", task_id="t03", solved=True, tier="big",
                        attempts=3, seconds=40.0)
            ck("resume: a retry overwrites the position, no duplicates",
               len(trace.positions(sid2)) == 3
               and trace.positions(sid2)["t03"]["tier"] == "big")
            suite = ["t01", "t02", "t03", "t04", "t05"]
            ck("resume: skip-set leaves exactly the unfinished tasks",
               [t for t in suite if t not in trace.positions(sid2)] == ["t04", "t05"])
            ck("resume: no session_end means resumable, filtered by command",
               trace.interrupted("run-suite") == [sid2]
               and trace.interrupted("run-suite2") == [])
            ck("resume: stored params let `flash resume` run without flags",
               trace.session_params(sid2) == {"tasks": "m0"})

            trace.attach(sid2, reason="selftest")
            trace.event("task_end", task_id="t04", solved=True, tier="big",
                        attempts=3, seconds=31.0)
            trace.close_session(resumed=True)
            after = trace.read(sid2)
            ck("attach: appends to the same file and re-announces the session",
               trace.current() is None and after[5]["type"] == "session_start"
               and after[5]["resumed"] is True and after[-1]["type"] == "session_end")
            ck("attach: seq continues past the original records",
               [r["seq"] for r in after] == list(range(len(after))))
            ck("attach: a closed resumed session is no longer resumable",
               trace.interrupted("run-suite") == [])
            ck("attach: snapshot now holds the finished task too",
               set(trace.positions(sid2)) == {"t01", "t02", "t03", "t04"})
            try:
                trace.attach("nope-missing")
                ck("attach: unknown session id raises", False)
            except FileNotFoundError:
                ck("attach: unknown session id raises", not trace.active())

            sid4 = trace.open_session("run-suite", cmd="run-suite")
            trace.event("task_end", task_id="t01", solved=False, tier="failed",
                        attempts=2, seconds=1.0)

            # ---- §33.6: the replay view ----
            view = trace.render(sid)
            ck("replay: header states outcome and totals",
               view.startswith("session " + sid) and "closed" in view
               and "tasks 1/2 solved" in view and "5012/394 prompt/complete tokens" in view)
            ck("replay: the decision chain is readable",
               "routed=small" in view and "profile=balanced" in view
               and "prompt_tokens=4112" in view and "kind=static" in view)
            ck("replay: the governor's answer rides on the route line",
               "allow_big=auto" in view and "big_allowed=False" in view)
            ck("replay: failure detail is visible without --full",
               "GOT 3 WANT 4" in view)
            ck("replay: per-task filter keeps session-level and that task only",
               "t01" in trace.render(sid, task_id="t01")
               and "t02" not in trace.render(sid, task_id="t01")
               and "session_start" in trace.render(sid, task_id="t01"))
            ck("replay: an unclosed session advertises its resume command",
               "INTERRUPTED — resumable" in trace.render(sid4)
               and f"flash resume {sid4}" in trace.render(sid4))
            trace.attach(sid2)
            trace.event("generate", task_id="t05", attempt=0, ms=120,
                        prompt_tokens=10, completion_tokens=5,
                        prompt="Task: write fizzbuzz", output="def f(): ...")
            trace.close_session()
            ck("replay: the resume hint is gone once the session closes",
               "resume with" not in trace.render(sid2))
            ck("replay: --full surfaces the stored prompt for re-feeding",
               "prompt='Task: write fizzbuzz'" in trace.render(sid2, task_id="t05",
                                                               full=True)
               and "Task: write fizzbuzz" not in trace.render(sid2, task_id="t05"))

            # ---- degradation (§33.9.4/.7) ----
            p = trace.path_for(sid)
            with open(p, "a") as f:
                f.write('{"truncated-mid-write\n\n')
            ck("degrade: a torn or blank line is skipped, the rest survives",
               len(trace.read(sid)) == 11 and trace.summarize(sid)["tasks"] == 2)
            ck("degrade: reading an absent session is empty, not fatal",
               trace.read("never-ran") == [] and trace.summarize("never-ran")["events"] == 0)
            ck("degrade: list_sessions finds every recorded run",
               set(trace.list_sessions()) == {sid, sid2, sid4})

            # ---- capture flag ----
            trace.CAPTURE = True
            sid3 = trace.open_session("run")
            trace.event("generate", task_id="t01", attempt=0,
                        prompt="P" * 40 if trace.CAPTURE else None)
            trace.close_session()
            ck("capture: prompts ride along only when CAPTURE is on",
               "prompt" in trace.read(sid3)[-2])
            trace.CAPTURE = False
        finally:
            trace.DIR = old

    width = max(len(n) for n, _, _ in checks)
    for name, ok, detail in checks:
        print(f"  {'OK  ' if ok else 'FAIL'} {name.ljust(width)}  {detail}")
    n = sum(ok for _, ok, _ in checks)
    print(f"\ntrace selftest: {n}/{len(checks)} checks passed")
    return 0 if n == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(run_selftest())
