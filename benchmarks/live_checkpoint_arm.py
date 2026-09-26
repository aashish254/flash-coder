"""R-5.3's LIVE arm: `kill -9` a real 7B `run-suite` between two real tokens.

`checkpoint_resume_check.py` proves the mechanism with the model scripted and
everything else real. This is the arm that box also demands with a MODEL real: a
live `flash run-suite` over a frozen suite, SIGKILLed by the parent the moment
the checkpoint holds durable decoded text, then resumed by a fresh process.

What it measures, from the system under test rather than from the code:

* the dead run's durable frame — task, attempt, tokens decoded, characters kept;
* the resume's `generate` record for THAT attempt, whose stored prompt (the
  session runs `--trace-full`, so prompts are verbatim) must be EXACTLY the cold
  prompt plus the dead process's durable text. That is the claim itself, read
  back off the record: what mlx was handed already contained the answer's head,
  so those tokens were not decoded again;
* `resumed_tokens` against `completion_tokens` — how much of the answer the
  resume actually had to produce, and what a cold restart of that attempt would
  have re-decoded;
* that the in-flight task settles with the attempts the dead run already earned,
  and that no other task's work was re-billed.

The token arithmetic is stated as approximate on purpose: BPE re-tokenisation of
prompt+answer is not guaranteed to equal the sum of its halves.

After the gate, unless `--no-control`, the script runs the SAME suite with the
same flags and no kill, and prints the two sessions side by side per task: the
sha1 of each answer (so a resumed answer must equal a cold one), the attempt
count, the task's own wall seconds and the settling span's decode milliseconds.
`--no-control` leaves "456ms" as a number with nothing beside it.

    python benchmarks/live_checkpoint_arm.py                 # m0, 3 tasks, 7B + control
    python benchmarks/live_checkpoint_arm.py --tasks 5 --suite m2_tasks.jsonl
    python benchmarks/live_checkpoint_arm.py --no-control    # arm only, no comparison run
"""
from __future__ import annotations

import argparse
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SMALL = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"


def wait_for(cond, timeout=600.0):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    return None


def settled_from_trace(sid) -> dict:
    """Per task: the answer's sha1, its attempts, the settling span's decode cost
    and the task's own wall seconds — all read back off the session's records, so
    the comparison is the system under test talking, not this script's arithmetic.
    Needs `--trace-full`; without it no answer is stored and the sha1 column is
    None rather than a guess."""
    import hashlib

    from flash import trace
    ev = trace.read(sid)
    gen = {}
    for e in ev:
        if e["type"] == "generate":
            # the attempt that settled the task is the last one drawn
            gen[(e.get("task_id"), e.get("attempt"))] = e
    out = {}
    for e in ev:
        if e["type"] != "task_end":
            continue
        g = gen.get((e["task_id"], (e["attempts"] or 1) - 1), {})
        ans = g.get("output")
        out[e["task_id"]] = dict(
            sha1=(None if ans is None else hashlib.sha1(ans.encode()).hexdigest()[:12]),
            attempts=e["attempts"], ms=g.get("ms"), seconds=e.get("seconds"),
            completion=g.get("completion_tokens"), prompt=g.get("prompt_tokens"),
            resumed=g.get("resumed_tokens"))
    return out


def session_of(out) -> str | None:
    while True:
        line = out.readline()
        if not line:
            return None
        m = re.search(r"session (\S+) —", line)
        if m:
            return m.group(1)


def cli(*args, capture=False):
    cmd = [sys.executable, "-u", "-m", "flash.cli"] + list(args)
    if capture:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, cwd=ROOT)
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default="m0_tasks.jsonl")
    ap.add_argument("--tasks", type=int, default=3)
    ap.add_argument("--small", default=SMALL)
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--no-control", action="store_true",
                    help="skip the no-kill comparison run (the arm then reports "
                         "the resumed span with nothing beside it)")
    a = ap.parse_args()

    from flash import checkpoint, power
    from flash import trace

    st, caps = power.governor(power.peak_gb(a.small), refresh=True)
    print(f"[arm] profile={caps.profile} ac={caps.reasons[0]} "
          f"width={caps.tournament_width}", flush=True)
    tasks_file = ROOT / "benchmarks" / "tasks" / a.suite
    argv = ["run-suite", "--tasks", str(tasks_file), "--max-tasks", str(a.tasks),
            "--attempts", str(a.attempts), "--small", a.small,
            "--allow-big", "never", "--trace-full"]
    t_start = time.perf_counter()

    p1 = cli(*argv, capture=True)
    sid = wait_for(lambda: session_of(p1.stdout), timeout=300)
    if not sid:
        print("[arm] FAIL — the killed run opened no session")
        return 1
    print(f"[arm] session {sid} live", flush=True)

    def midflight():
        f = checkpoint.load(sid)
        if (f is None or not f.partial or f.decoded == 0
                or p1.poll() is not None):
            return None
        return f
    frame = wait_for(midflight, timeout=600)
    if frame is None:
        print("[arm] FAIL — never caught a generation mid-span on a real model")
        p1.kill(); p1.wait(timeout=30)
        return 1
    p1.send_signal(signal.SIGKILL)
    p1.wait(timeout=30)
    dead = checkpoint.load(sid)
    print(f"[arm] KILLED mid-generation: task={dead.task_id} "
          f"arm={dead.kind} stage={dead.stage} attempt={dead.attempt} "
          f"decoded={dead.decoded} tokens kept={len(dead.partial)} chars "
          f"(frame seq={dead.seq})", flush=True)
    print(f"[arm] kill landed {time.perf_counter() - t_start:.1f}s into the run",
          flush=True)

    checkpoint.disarm()
    p2 = cli("resume", sid, "--trace-full", capture=True)
    out2 = p2.stdout.read()
    rc2 = p2.wait(timeout=1800)
    wall = time.perf_counter() - t_start
    print("\n".join(out2.splitlines()[-12:]), flush=True)

    ev = trace.read(sid)
    gens = [e for e in ev if e["type"] == "generate"
            and e.get("task_id") == dead.task_id]
    resumed_gen = next((e for e in gens if e.get("attempt") == dead.attempt),
                       None)
    ends = [e for e in ev if e["type"] == "task_end"]
    print(f"\n[arm] === what the {dead.task_id} attempt {dead.attempt} span "
          f"cost, per process ===")
    print(f"  dead process: {dead.decoded} tokens decoded, "
          f"{len(dead.partial)} characters durable at its last flush")
    for e in gens:
        print(f"  generate (attempt {e.get('attempt')}): ms={e['ms']} "
              f"prompt_tokens={e['prompt_tokens']} "
              f"completion_tokens={e['completion_tokens']} "
              f"checkpointed={e.get('checkpointed')} "
              f"resumed_tokens={e.get('resumed_tokens')}")
    if resumed_gen is not None and resumed_gen.get("prompt"):
        # The killed process never reaches its own trace write, so the cold
        # prompt comes from the frame and the continued one from the resume's
        # record. Both are stored verbatim under --trace-full.
        given = resumed_gen["prompt"]
        exact = given == dead.prompt + dead.partial
        print(f"  the resume's prompt IS the cold prompt plus the dead text: "
              f"{exact} ({len(dead.prompt)} + {len(dead.partial)} chars → "
              f"{len(given)} chars handed to mlx)")
        carried = resumed_gen.get("resumed_tokens") or 0
        new = (resumed_gen["completion_tokens"] or 0) - carried
        print(f"  answer tokens: {resumed_gen['completion_tokens']} total, "
              f"{carried} carried in over the prompt, {new} actually decoded "
              f"this time in {resumed_gen['ms']}ms — a cold restart of this "
              f"attempt decodes all {resumed_gen['completion_tokens']} again")
    print("[arm] === the session's task_end records ===")
    for e in ends:
        print(f"  {e['task_id']:<26} solved={e['solved']} tier={e['tier']} "
              f"attempts={e['attempts']}")
    left = checkpoint.pending(sid)
    print(f"[arm] frame left behind after the resumed run: {left or 'none'}")
    print(f"[arm] resume exit={rc2} total wall {wall:.0f}s")
    ok = (rc2 == 0 and resumed_gen is not None
          and (resumed_gen.get("resumed_tokens") or 0) > 0
          and resumed_gen.get("prompt") == dead.prompt + dead.partial
          and left is None and "mid-flight and resumes inside" in out2)
    print(f"\n[arm] VERDICT: {'the in-flight task resumed INSIDE its span' if ok else 'GATE NOT MET'}")

    if not a.no_control and ok:
        # The same suite, the same flags, the same armed/streaming path — minus
        # the kill. Without it "456ms" is a number with nothing beside it.
        print("\n[arm] === control run (no kill), same argv ===", flush=True)
        cp = cli(*argv, capture=True)
        csid = wait_for(lambda: session_of(cp.stdout), timeout=300)
        if csid is None:
            print("[arm] control run printed no session id — no comparison")
            cp.kill(); cp.wait(timeout=30)
            return 1
        cp.wait(timeout=1800)
        print(f"[arm] control session {csid} closed rc={cp.returncode}", flush=True)
        mine, theirs = settled_from_trace(sid), settled_from_trace(csid)
        print("\ntask                 answer-sha1  attempts   "
              "task s killed|control   span ms killed|control   tokens "
              "completion|prompt|resumed")
        for t in theirs:
            m, c = mine.get(t, {}), theirs[t]
            same = (m.get("sha1") == c.get("sha1") and m.get("sha1") is not None)
            print(f"{t:<20} {str(same):<11} "
                  f"{m.get('attempts')}/{c.get('attempts')}       "
                  f"{m.get('seconds')}s|{c.get('seconds')}s      "
                  f"{m.get('ms')}|{c.get('ms')}   "
                  f"completion={m.get('completion')}/{c.get('completion')} "
                  f"prompt={m.get('prompt')}/{c.get('prompt')} "
                  f"resumed={m.get('resumed') or 'None'}")
        print("\n[arm] one ledger row per task across the kill and its resume "
              "(the killed process wrote no task_end): "
              f"{len(mine)} tasks in the resumed session, "
              f"{len(theirs)} in the control")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
