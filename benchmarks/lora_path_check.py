"""R-6.4's offline vector: the LoRA arm's gate, its resume position, the
leakage rule and the adapter's identity — each one mutation-checked.

`flash.train --selftest` covers the data law and the slice loop; `flash.jobs
--selftest` covers the gate's decision. Neither covers the path BETWEEN them,
which is where R-6.4's claim lives: a job that is gated, resumable,
leakage-free and loadable under a name that means what it says. Each of those
four properties is inverted by one line, and every inversion produces a
plausible-looking number rather than an error:

  gate      a training run that starts while the user is typing costs battery
            and warm memory, and the machine's idle state decides — not the
            caller's intention;
  resume    a `kill -9` lands between two slices, so the banked step must be
            where the next idle window continues, and a refusal must not erase
            the position a paused job already paid for;
  leakage   an adapter scored on a task it trained on measures memory. The
            exclusion is proven by reading the written artifact back, not by
            trusting a counter the writer printed;
  identity  an "after" arm that quietly loaded the base model prints the most
            convincing wrong number this project can generate — a before/after
            where both sides are the before. So a named adapter with no weights
            raises, the resolved directory must reach `mlx_lm.load`, and the
            label must name the adapter that actually ran.

Nine of these guarantees are then broken on purpose: the same group run against
a mutated copy of `jobs.py`, `train.py` or `loop.py` must fail exactly the
checks that mutation defeats. A gate no mutation trips is not a gate; where a
mutation legitimately breaks a neighbour (opening the gate also removes the
refusal a resume check depends on), the cascade is named in the expectation so
it is stated rather than hidden.

Not covered, stated so: the real mlx calls (`default_slice`/`valid_loss`) need
Metal and belong to the live arm, and the shipped-dataset checks require
benchmarks/results/datasets/ledger-verified — a clone without it fails loudly,
because R-6.4 cannot be scored without it.

    python benchmarks/lora_path_check.py
"""
from __future__ import annotations

import importlib.util
import json
import os
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

from flash import cli, jobs, loop, power, train as tr                 # noqa: E402

DATASET = ROOT / "benchmarks" / "results" / "datasets" / "ledger-verified"
# The suites R-6.4 may score on: SPEC §6.2's frozen list. None of their ids may
# appear in a training row.
EVAL_SUITES = ("m0_tasks.jsonl", "m2_tasks.jsonl", "mw_tasks.jsonl",
               "m3_hard_tasks.jsonl", "m3b_hard_tasks.jsonl",
               "m4_heldout_tasks.jsonl", "m5_heldout_tasks.jsonl",
               "m6_heldout_tasks.jsonl", "m7_heldout_tasks.jsonl",
               "vis_tasks.jsonl", "visp_tasks.jsonl", "visr_tasks.jsonl")

IDLE = dict(on_ac=True, battery_pct=100.0, mem_free_pct=80.0, mem_total_gb=32.0,
            load_per_core=0.3, idle_seconds=900.0, cores=10)


def sysstate(**kw):
    s = dict(IDLE)
    s.update(kw)
    return power.SystemState(**s)


def suite_ids(name) -> set:
    p = ROOT / "benchmarks" / "tasks" / name
    if not p.exists():
        return set()
    return {json.loads(l)["id"] for l in p.read_text().splitlines() if l.strip()}


def load_mod(name: str, path):
    """Import a file as a module under a synthetic name (mutant copies)."""
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def ck(res, name, cond, detail=""):
    res.append((name, bool(cond), str(detail)))


# ------------------------------------------------- the gate, in one process

def group_gate(J, tmp: Path) -> list:
    """Every scenario gets its own adapter and jobs directory: a mutation that
    opens the gate must not hide behind state a neighbour left in the tree."""
    res: list = []
    ds = tmp / "ds"
    ds.mkdir(parents=True, exist_ok=True)
    row = json.dumps({"messages": [{"role": "user", "content": "q"},
                                   {"role": "assistant", "content": "a"}]})
    (ds / "train.jsonl").write_text(row + "\n")
    (ds / "valid.jsonl").write_text(row + "\n")
    calls: list = []

    def fake_train(**kw):
        calls.append(kw)
        root = Path(kw["adapter_dir"])
        root.mkdir(parents=True, exist_ok=True)
        (root / "adapters.safetensors").write_bytes(b"w")
        if kw.get("on_progress"):
            kw["on_progress"](kw["start_step"] + kw["slice_iters"], "slice")
        # one slice of work, and the best checkpoint is an EARLIER step than the
        # last one trained — which is the distinction the state must record
        last = kw["start_step"] + kw["slice_iters"]
        return {"iters_done": last, "steps_total": kw["steps_total"],
                "start_step": kw["start_step"], "promoted_step": last - 8,
                "best_step": last - 8, "best_loss": 1.25, "evals": [],
                "stopped": "steps", "wall_s": 1.0}

    def fresh(name):
        return tmp / f"ad-{name}", tmp / f"jobs-{name}"

    ad, jp = fresh("battery")
    msg = J.run_lora("", dataset_dir=ds, jobs_dir=jp, adapter_dir=ad,
                     steps_total=64, slice_iters=16, verbose=False,
                     train_fn=fake_train, rows=[{}] * 30,
                     state=sysstate(on_ac=False, battery_pct=40.0))
    ck(res, "gate: on battery the job refuses and makes zero training calls",
       "refused" in msg and not calls and not ad.exists(), f"{msg} {len(calls)}")

    ad, jp = fresh("typing")
    msg = J.run_lora("", dataset_dir=ds, jobs_dir=jp, adapter_dir=ad,
                     steps_total=64, slice_iters=16, verbose=False,
                     train_fn=fake_train, rows=[{}] * 30,
                     state=sysstate(idle_seconds=5.0))
    ck(res, "gate: five seconds after a keystroke it still refuses",
       "refused" in msg and not calls, msg)

    ad, jp = fresh("idle")
    calls.clear()
    msg = J.run_lora("", dataset_dir=ds, jobs_dir=jp, adapter_dir=ad,
                     steps_total=64, slice_iters=16, verbose=False,
                     train_fn=fake_train, rows=[{}] * 30, state=sysstate())
    ck(res, "gate: idle + AC opens it and the step budget reaches the trainer",
       "LoRA on 1" in msg and calls and calls[0]["steps_total"] == 64, msg)
    rec = json.loads((ad / "train_record.json").read_text())
    ck(res, "the record beside the weights names the promoted step, and the "
            "resume position follows it",
       rec["promoted_step"] == 8 and rec["iters_done"] == 16
       and J.load_state("lora-fit", jp).iters_done == 8,
       f"promoted={rec['promoted_step']} trained={rec['iters_done']} "
       f"state={J.load_state('lora-fit', jp).iters_done}")

    ad, jp = fresh("paused")
    J.save_state(J.JobState(kind="lora-fit", stage="paused", iters_done=48,
                            iters_total=64, adapter=str(ad), trained_n=0), jp)
    calls.clear()
    msg = J.run_lora("", dataset_dir=ds, jobs_dir=jp, adapter_dir=ad,
                     steps_total=64, slice_iters=16, verbose=False,
                     train_fn=fake_train, rows=[{}] * 30,
                     state=sysstate(idle_seconds=2.0))
    after = J.load_state("lora-fit", jp)
    ck(res, "a REFUSAL records itself without overwriting the paused step",
       "refused" in msg and after.stage == "paused" and after.iters_done == 48,
       f"{after.stage}@{after.iters_done}: {msg[:60]}")
    calls.clear()
    J.run_lora("", dataset_dir=ds, jobs_dir=jp, adapter_dir=ad, steps_total=64,
               slice_iters=16, verbose=False, train_fn=fake_train,
               rows=[{}] * 30, state=sysstate())
    ck(res, "and the next idle window continues from that step, not from 0",
       calls and calls[0]["start_step"] == 48,
       calls[0]["start_step"] if calls else "no call")

    ad, jp = fresh("nodata")
    calls.clear()
    msg = J.run_lora("", dataset_dir=tmp / "nowhere", jobs_dir=jp,
                     adapter_dir=ad, verbose=False, train_fn=fake_train,
                     rows=[{}] * 30, state=sysstate())
    ck(res, "no dataset: it says what to run instead of loading a model",
       "no dataset" in msg and "--held-out" in msg and not calls, msg)
    return res


# --------------------------------------------------- the real kill -9 cycle

CHILD = r'''
import json, os, sys, time, importlib.util
from pathlib import Path

ROOT = Path(os.environ["LORA_ROOT"])
sys.path.insert(0, str(ROOT))


def resolve(real, env):
    """The real module, or a mutant copy of it when the environment names one."""
    p = os.environ.get(env)
    if not p:
        return __import__(real, fromlist=["x"])
    spec = importlib.util.spec_from_file_location(real + "_mut", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[real + "_mut"] = mod
    spec.loader.exec_module(mod)
    return mod


J = resolve("flash.jobs", "LORA_JOBS")
TR = resolve("flash.train", "LORA_TRAIN")
from flash import power                                              # noqa: E402

TMP = Path(os.environ["LORA_TMP"])
LOG = Path(os.environ["LORA_LOG"])
SLICE_S = float(os.environ.get("LORA_SLICE_S", "0.8"))
CURVE = json.loads(os.environ.get("LORA_CURVE",
                                  '{"16":1.9,"32":1.5,"48":1.7,"64":1.8}'))


def log(line):
    with LOG.open("a") as fid:
        fid.write(line + "\n")
        fid.flush()
        os.fsync(fid.fileno())


def fake_slice(*, repo, train_file, valid_file, adapter_dir, iters, resume_from,
               seed=0, **kw):
    """mlx's train_model, replaced by a step span that costs real wall time.

    The span is derived from the weights already on disk, the way mlx derives it
    from `resume_adapter_file`, so the log says which optimizer steps this
    process ran. `begin` is durable BEFORE the sleep, which is what lets the
    parent prove the kill landed inside a slice rather than between two.
    """
    ad = Path(adapter_dir)
    w = ad / "adapters.safetensors"
    prev = int(w.read_text().lstrip("w")) if w.exists() else 0
    log(f"begin {prev + 1}..{prev + iters} "
        f"resume={'warm' if resume_from else 'cold'}")
    time.sleep(SLICE_S)
    w.write_text(f"w{prev + iters}")
    (ad / "adapter_config.json").write_text(json.dumps({"rank": 8}))
    log(f"end {prev + 1}..{prev + iters}")
    return SLICE_S


def fake_loss(*, repo, adapter_dir, valid_file, **kw):
    step = int(Path(adapter_dir).name.split("-")[1])
    return float(CURVE.get(str(step), 2.0))


def train_fn(**kw):
    return TR.train_lora(run=fake_slice, loss_fn=fake_loss, **kw)


state = power.SystemState(**json.loads(os.environ["LORA_STATE"]))
print(J.run_lora("mlx-community/fake-7B-Instruct", dataset_dir=TMP / "ds",
                 adapter_dir=TMP / "ad", jobs_dir=TMP / "jobs",
                 steps_total=int(os.environ.get("LORA_STEPS", "64")),
                 slice_iters=int(os.environ.get("LORA_SLICE", "16")),
                 patience=int(os.environ.get("LORA_PATIENCE", "2")), budget_s=float(os.environ.get("LORA_BUDGET", "600")),
                 verbose=False, train_fn=train_fn, rows=[{}] * 30, state=state))
'''


def spawn(tmp: Path, log: Path, jobs_mut=None, train_mut=None):
    env = dict(os.environ, LORA_ROOT=str(ROOT), LORA_TMP=str(tmp),
               LORA_LOG=str(log), LORA_STATE=json.dumps(IDLE))
    if jobs_mut:
        env["LORA_JOBS"] = str(jobs_mut)
    if train_mut:
        env["LORA_TRAIN"] = str(train_mut)
    return subprocess.Popen([sys.executable, str(tmp / "child.py")],
                            cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)


def group_kill(J, tmp: Path, jobs_mut=None, train_mut=None) -> list:
    """SIGKILL a real training process inside a slice, resume it in a new
    process, and prove the banked step is where it continues."""
    res: list = []
    work = tmp / "kill"
    (work / "ds").mkdir(parents=True, exist_ok=True)
    row = json.dumps({"messages": [{"role": "user", "content": "q"},
                                   {"role": "assistant", "content": "a"}]})
    (work / "ds" / "train.jsonl").write_text(row + "\n")
    (work / "ds" / "valid.jsonl").write_text(row + "\n")
    (work / "child.py").write_text(CHILD)
    log = work / "slice.log"

    p = spawn(work, log, jobs_mut, train_mut)
    reached = False
    for _ in range(1500):
        try:
            if log.read_text().count("begin") >= 2:
                reached = True
                break
        except FileNotFoundError:
            pass
        if p.poll() is not None:
            break
        time.sleep(0.02)
    p.kill()
    if not reached:
        raise AssertionError("the run never reached its second slice: "
                             f"{p.communicate(timeout=30)[0][:300]}")
    out1 = p.communicate(timeout=30)[0]
    lines1 = log.read_text().splitlines()
    begins1 = [l for l in lines1 if l.startswith("begin")]
    state1 = J.load_state("lora-fit", work / "jobs")
    w1 = (work / "ad" / "adapters.safetensors").read_text()
    ck(res, "the SIGKILL landed inside a slice: begun, never ended",
       len(begins1) == 2 and sum(l.startswith("end") for l in lines1) == 1,
       str(lines1))
    ck(res, "what the dead run banked is the last slice it finished",
       state1 is not None and state1.stage == "training"
       and state1.iters_done == 16 and w1 == "w16",
       f"{state1 and (state1.stage + '@' + str(state1.iters_done))} "
       f"weights={w1} out={out1[:50]}")

    p = spawn(work, log, jobs_mut, train_mut)
    out2, _ = p.communicate(timeout=180)
    spans = [l.split()[1] for l in log.read_text().splitlines()
             if l.startswith("begin")]
    resumed = spans[len(begins1):]
    ck(res, "the resume re-ran no slice the dead process had completed",
       bool(resumed) and resumed[0] == "17..32" and spans.count("1..16") == 1
       and len(resumed) == 3, f"all={spans} resumed={resumed}")
    begins = log.read_text().splitlines()
    ck(res, "every resumed slice loaded the weights its predecessor saved",
       all("warm" in l for l in begins if l.startswith("begin")
           and not l.split()[1].startswith("1..")), str(spans))
    root = (work / "ad" / "adapters.safetensors").read_text()
    rec = json.loads((work / "ad" / "train_record.json").read_text())
    ck(res, "the adapter root holds the best-measured checkpoint, not the last",
       root == "w32" and rec["promoted_step"] == 32 and rec["iters_done"] == 64
       and (work / "ad" / "step-64" / "adapters.safetensors").read_text() == "w64",
       f"root={root} promoted/done={rec.get('promoted_step')}/"
       f"{rec.get('iters_done')} out={out2[:50]}")
    ck(res, "and the record says it stopped early on loss, not on steps",
       rec["best_step"] == 32 and "overfit" in rec["stopped"],
       rec["stopped"][:60])
    return res


# ------------------------------------------------- the leakage rule, on disk

TRACE_PROMPT = ("<" + "|im_start|" + ">user\nWrite f().\n"
                "<" + "|im_end|" + ">\n<" + "|im_start|" + ">assistant\n")


def group_leak(TR, tmp: Path) -> list:
    """Mine synthetic traces with a suite held out, then read the files back."""
    res: list = []
    work = tmp / "leak"
    traces = work / "traces"
    traces.mkdir(parents=True, exist_ok=True)
    events: list = []
    for tid in ("train_me_1", "train_me_2", "held_out_1", "held_out_2"):
        events += [dict(ts=0.0, seq=0, type="generate", task_id=tid, attempt=0,
                        prompt=TRACE_PROMPT, output="42"),
                   dict(ts=0.0, seq=1, type="task_end", task_id=tid,
                        solved=True, attempts=1, tier="small", seconds=1.0,
                        routed="small")]
    (traces / "s1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n")
    suite = work / "eval_tasks.jsonl"
    suite.write_text("\n".join(json.dumps({"id": t, "prompt": "x"})
                               for t in ("held_out_1", "held_out_2")) + "\n")

    real_ds = TR.DATASET_DIR
    try:
        TR.DATASET_DIR = work / "datasets"
        out_dir = TR.build_dataset(held_out=[suite], out_name="v",
                                   traces_dir=traces, valid_frac=0.5,
                                   verbose=False)[2]
    finally:
        TR.DATASET_DIR = real_ds
    rows = [json.loads(l) for l in
            (out_dir / "train.jsonl").read_text().splitlines()]
    rows += [json.loads(l) for l in
             (out_dir / "valid.jsonl").read_text().splitlines()]
    written = {r["task_id"] for r in rows}
    man = json.loads((out_dir / "manifest.json").read_text())
    ck(res, "leakage: a held-out suite's ids are absent from every written row",
       written == {"train_me_1", "train_me_2"}, str(sorted(written)))
    ck(res, "leakage: what the manifest claims to exclude really is not in the "
            "rows",
       man["excluded_count"] == 2
       and set(man["excluded_task_ids"]) == {"held_out_1", "held_out_2"}
       and not (set(man["excluded_task_ids"]) & written)
       and set(man["train_task_ids"]) | set(man["valid_task_ids"]) == written,
       json.dumps({k: man.get(k) for k in
                   ("excluded_count", "excluded_task_ids")}))
    ck(res, "leakage: the drop is counted, so the exclusion has a denominator",
       man["drops"].get("excluded_leakage") == 2, str(man["drops"]))

    if not (DATASET / "manifest.json").exists():
        ck(res, "the shipped dataset is absent, so R-6.4 cannot be scored", False,
           f"build it: python -m flash.train --dataset --held-out <frozen suite> "
           f"(expected {DATASET})")
        return res
    sm = json.loads((DATASET / "manifest.json").read_text())
    ids = set(sm["train_task_ids"]) | set(sm["valid_task_ids"])
    ev: set = set()
    for name in EVAL_SUITES:
        ev |= suite_ids(name)
    ck(res, "the shipped adapter was trained on zero ids in any frozen suite",
       bool(ids) and not (ids & ev),
       f"{len(ids)} trained ids, overlap={sorted(ids & ev)}")
    srows = [json.loads(l) for l in
             (DATASET / "train.jsonl").read_text().splitlines()]
    srows += [json.loads(l) for l in
              (DATASET / "valid.jsonl").read_text().splitlines()]
    ck(res, "every shipped row carries the task id it was mined from",
       all(r.get("task_id") for r in srows)
       and {r["task_id"] for r in srows} == ids,
       f"{len(srows)} rows, {len({r.get('task_id') for r in srows})} ids")
    hosts = sorted(n.name for n in (ROOT / "benchmarks" / "tasks").glob("*.jsonl")
                   if ids & suite_ids(n.name))
    ck(res, "the suites that DO hold trained ids are named, and no scoring suite "
            "is among them", bool(hosts) and not (set(hosts) & set(EVAL_SUITES)),
       str(hosts))
    return res


# ------------------------------------------------------ the adapter's identity

class FakeMlx:
    """A stand-in for `mlx_lm.load` that reports the adapter it was handed."""

    def __init__(self):
        self.calls: list = []

    def load(self, repo, adapter_path=None):
        self.calls.append((repo, adapter_path))
        return ("model", "tokenizer")


def group_identity(L, tmp: Path) -> list:
    res: list = []
    ad = tmp / "adapters" / "self-improve"
    ad.mkdir(parents=True, exist_ok=True)
    (ad / "adapters.safetensors").write_bytes(b"\x00")
    empty = tmp / "adapters" / "half-written"
    empty.mkdir(parents=True, exist_ok=True)

    spy = FakeMlx()
    real = sys.modules.pop("mlx_lm", None)
    mod = types.ModuleType("mlx_lm")
    mod.load = spy.load
    sys.modules["mlx_lm"] = mod
    prev_adapter = L.ADAPTER
    try:
        L.ADAPTER = ""
        bare = L.small_label("mlx/Qwen2.5-Coder-7B")
        L.load_model("repo-7B")
        ck(res, "no adapter named: the base model loads with no adapter argument",
           spy.calls[-1] == ("repo-7B", None) and bare == "Qwen2.5-Coder-7B",
           f"{spy.calls[-1]} label={bare}")
        L.ADAPTER = str(ad)
        L.load_model("repo-7B")
        ck(res, "a named adapter reaches mlx_lm.load as adapter_path",
           spy.calls[-1] == ("repo-7B", str(ad)), str(spy.calls[-1]))
        small, big = (L.small_label("mlx/Qwen2.5-Coder-7B"),
                      L.big_label("mlx/Qwen3-30B"))
        ck(res, "the small tier is labelled with the adapter it ran, the brain "
                "with none",
           small == "Qwen2.5-Coder-7B+lora:self-improve" and big == "Qwen3-30B",
           f"{small} | {big}")
        L.ADAPTER = str(empty)
        raised = None
        n_calls = len(spy.calls)
        try:
            L.load_model("repo-7B")
        except FileNotFoundError as exc:
            raised = str(exc)
        ck(res, "an adapter with no weights raises instead of loading the base "
                "model",
           raised is not None and "adapters.safetensors" in raised
           and len(spy.calls) == n_calls, raised)
    finally:
        L.ADAPTER = prev_adapter
        if real is not None:
            sys.modules["mlx_lm"] = real
        else:
            sys.modules.pop("mlx_lm", None)
    return res


# ------------------------------------------------------------------ the CLI

def group_cli(CLI, tmp: Path) -> list:
    """`learn --lora` and the router refit share one checkpoint directory, so the
    job's NAME is the only thing that keeps the two apart — and a shared name
    means a LoRA run reads the router's step count as its resume position."""
    res: list = []
    from flash import jobs as J
    ap = CLI.build_parser()
    calls: list = []
    real = (J.run_lora, J.run_fit)

    def spy_lora(small, **kw):
        calls.append(("lora", kw))
        return "[learn] LoRA spy ran"

    def spy_fit(small, **kw):
        calls.append(("fit", kw))
        return "[learn] refit spy ran"

    J.run_lora, J.run_fit = spy_lora, spy_fit
    try:
        rc = CLI.cmd_learn(ap.parse_args(
            ["learn", "--lora", "--adapter", "probe", "--steps", "8"]))
        kinds = [kw.get("kind") for _, kw in calls]
        ck(res, "`flash learn --lora` writes its checkpoint under its own job name",
           rc == 0 and [t for t, _ in calls] == ["lora"] and kinds == ["lora-fit"],
           f"{[t for t, _ in calls]} kinds={kinds}")
        ck(res, "and the experiment flags reach the trainer, not just the name",
           calls[0][1]["adapter_name"] == "probe" and calls[0][1]["steps_total"] == 8,
           str({k: calls[0][1][k] for k in ("adapter_name", "steps_total")}))
        calls.clear()
        CLI.cmd_learn(ap.parse_args(["learn"]))
        ck(res, "`flash learn` alone still refits the router under ITS name",
           [t for t, _ in calls] == ["fit"]
           and calls[0][1]["kind"] == "router-fit", str(calls))
        calls.clear()
        CLI.cmd_learn(ap.parse_args(["learn", "--lora", "--kind", "custom"]))
        ck(res, "--kind still overrides the derived name",
           calls and calls[0][1]["kind"] == "custom", str(calls))
    finally:
        J.run_lora, J.run_fit = real
    return res


# ---------------------------------------------------------------- mutations

MUTATIONS = (
    ("the busy/unplugged denial stops firing", "gate", "flash/jobs.py",
     '    if not caps.allow_background_work:\n', '    if False:\n',
     ("gate: on battery the job refuses and makes zero training calls",
      "gate: five seconds after a keystroke it still refuses",
      "a REFUSAL records itself without overwriting the paused step",
      # opening the gate also removes the refusal the resume-position check is
      # about: with no refusal there is no paused job left behind
      "and the next idle window continues from that step, not from 0")),
    ("the banked step is thrown away", "gate", "flash/jobs.py",
     '    start = prev.iters_done if prev and prev.stage in ("training", "paused") else 0\n',
     '    start = 0\n',
     ("and the next idle window continues from that step, not from 0",)),
    ("a refusal overwrites the paused position", "gate", "flash/jobs.py",
     '        if prev and prev.kind == st.kind and prev.stage in ("paused", "training"):\n',
     '        if False:\n',
     ("a REFUSAL records itself without overwriting the paused step",
      # a refused rerun that rewrites the stage also erases the resume position
      "and the next idle window continues from that step, not from 0")),
    ("a finished slice banks nothing", "kill", "flash/train.py",
     '        if on_progress:\n', '        if False:\n',
     ("what the dead run banked is the last slice it finished",
      "the resume re-ran no slice the dead process had completed",
      "every resumed slice loaded the weights its predecessor saved",
      "the adapter root holds the best-measured checkpoint, not the last")),
    ("the root adapter is the last step, not the best", "kill", "flash/train.py",
     '    promoted = best_step if best_step is not None else done\n',
     '    promoted = done\n',
     ("the adapter root holds the best-measured checkpoint, not the last",)),
    ("the held-out exclusion stops firing", "leak", "flash/train.py",
     '            if tid in exclude:\n', '            if False:\n',
     ("leakage: a held-out suite's ids are absent from every written row",
      "leakage: what the manifest claims to exclude really is not in the rows",
      "leakage: the drop is counted, so the exclusion has a denominator")),
    ("a missing weights file falls back to the base model", "identity",
     "flash/loop.py",
     '    if not (Path(p) / "adapters.safetensors").exists():\n', '    if False:\n',
     ("an adapter with no weights raises instead of loading the base model",)),
    ("the adapter never reaches the loader", "identity", "flash/loop.py",
     '    return load(repo, adapter_path=ad) if ad else load(repo)\n',
     '    return load(repo)\n',
     ("a named adapter reaches mlx_lm.load as adapter_path",)),
    ("the brain is labelled as if it carried the adapter", "identity",
     "flash/loop.py", '    return model_label(repo, "")\n',
     '    return model_label(repo)\n',
     ("the small tier is labelled with the adapter it ran, the brain with none",)),
    ("learn --lora writes under the router's job name", "cli", "flash/cli.py",
     '    kind = args.kind or ("lora-fit" if args.lora else "router-fit")\n',
     '    kind = args.kind or "router-fit"\n',
     ("`flash learn --lora` writes its checkpoint under its own job name",)),
)


def run_group(name: str, mods: dict, tmp: Path, jobs_mut=None, train_mut=None):
    if name == "gate":
        return group_gate(mods["jobs"], tmp)
    if name == "kill":
        return group_kill(mods["jobs"], tmp, jobs_mut, train_mut)
    if name == "leak":
        return group_leak(mods["train"], tmp)
    if name == "cli":
        return group_cli(mods["cli"], tmp)
    return group_identity(mods["loop"], tmp)


def mutant(tmp: Path, tag: str, rel: str, anchor: str, repl: str) -> Path:
    src = (ROOT / rel).read_text()
    if src.count(anchor) != 1:
        raise AssertionError(f"{tag}: anchor appears {src.count(anchor)} times "
                             f"in {rel}: {anchor[:50]!r}")
    out = tmp / f"_mut_{tag}.py"
    out.write_text(src.replace(anchor, repl))
    return out


def main() -> int:
    mods = {"jobs": jobs, "train": tr, "loop": loop, "cli": cli}
    results: list = []
    with tempfile.TemporaryDirectory() as d:
        for g in ("gate", "kill", "leak", "identity", "cli"):
            results += run_group(g, mods, Path(d) / g)
    w = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"  {'OK  ' if ok else 'FAIL'} {name:<{w}}  {detail}")
    n_ok = sum(1 for _, ok, _ in results if ok)
    print(f"\nlora path: {n_ok}/{len(results)} checks passed")

    bad = 0
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for i, (label, group, rel, anchor, repl, expect) in enumerate(MUTATIONS):
            tag = f"{i}_{Path(rel).stem}"
            path = mutant(tmp, tag, rel, anchor, repl)
            m = dict(mods)
            m[Path(rel).stem] = load_mod(f"mut_{tag}", path)
            try:
                res = run_group(group, m, tmp / f"run-{i}",
                                path if rel == "flash/jobs.py" else None,
                                path if rel == "flash/train.py" else None)
                got = sorted({n for n, ok, _ in res if not ok})
            except Exception as exc:                        # noqa: BLE001
                got = [f"<raised {type(exc).__name__}: {exc}>"]
            want = sorted(set(expect))
            ok = got == want
            bad += not ok
            print(f"  {'OK  ' if ok else 'FAIL'} break {label}"
                  f"  ->  {got if got else 'NOTHING failed'}"
                  + ("" if ok else f"   EXPECTED {want}"))
    print(f"\nmutations: {len(MUTATIONS) - bad}/{len(MUTATIONS)} gates defeated "
          f"by exactly their checks")
    return 0 if n_ok == len(results) and not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
