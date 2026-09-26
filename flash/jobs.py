"""Background learning that knows when it is allowed to run (§34.3).

The headline promise — the agent improves itself from its own outcomes — has a
user-facing cost: a refit loads the 7B and burns CPU/GPU while the human is
typing. §34.3's acceptance test is explicit: *zero training work while the
user is active or on battery, measurable progress overnight, and a run that
survives being killed mid-flight.*

Three mechanisms deliver it:

  gate      `eligibility()` requires AC **and** an idle machine (the §34.1
            governor's `allow_background_work`, i.e. also not throttled, not
            memory-pressured, not load-shed) **and** enough new outcomes.
  caps      lower process priority + a hard wall-clock budget per invocation,
            so an idle window that ends mid-job is a pause, not a hang.
  resume    every stage checkpoints to `benchmarks/results/jobs/<kind>.json`
            and embeddings flush per chunk (flash.learn.embed_backfill), so a
            kill costs at most one chunk of work.

`autofit_if_stale()` keeps its old meaning for the interactive path (a suite
run is by definition foreground); this module is the unattended path.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from flash import ledger, power

ROOT = Path(__file__).resolve().parent.parent
JOBS_DIR = ROOT / "benchmarks" / "results" / "jobs"

IDLE_GATE_S = 300.0        # §34.3: "user input within the last 5 min" = busy
MIN_NEW_ROWS = 10          # same staleness rule the interactive autofit uses
BUDGET_S = 900.0           # one idle window's worth of work
CHUNK = 8                  # prompts per embedding flush


@dataclass
class JobState:
    kind: str = "router-fit"
    stage: str = "queued"          # queued|embedding|training|paused|fitting|done|refused
    eligible: bool = False
    embedded: int = 0
    total: int = 0
    rows: int = 0
    trained_n: int = 0
    iters_done: int = 0            # lora: optimizer steps banked
    iters_total: int = 0           # lora: the step budget asked for
    adapter: str = ""              # lora: the directory the weights live in
    detail: str = ""               # lora: the last slice, in words
    adapter: str = ""              # lora: which directory holds the weights
    wall_s: float = 0.0
    reason: str = ""
    updated: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def state_path(kind: str, jobs_dir: Path | None = None) -> Path:
    return (jobs_dir or JOBS_DIR) / f"{kind}.json"


def load_state(kind: str, jobs_dir: Path | None = None) -> JobState | None:
    p = state_path(kind, jobs_dir)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    known = {k: raw[k] for k in raw if k in JobState.__dataclass_fields__}
    return JobState(**known)


def save_state(st: JobState, jobs_dir: Path | None = None) -> None:
    """Atomic checkpoint — a kill during a write must not lose the last one."""
    p = state_path(st.kind, jobs_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    st.updated = time.time()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st.to_dict(), indent=2))
    os.replace(tmp, p)


# ------------------------------------------------------------------- gating

def eligibility(force: bool = False, need_gb: float = 4.4,
                min_new: int = MIN_NEW_ROWS, idle_gate: float = IDLE_GATE_S,
                state: power.SystemState | None = None,
                rows: list[dict] | None = None,
                trained_n: int | None = None) -> tuple[bool, list[str]]:
    """May background learning run right now? (ok, why-not list).

    Denials are always explained with the numbers that produced them — the
    acceptance test is audited by reading a log line, not by trusting a flag.
    """
    if force:
        return True, ["forced (--force): idle/AC gate bypassed by an explicit call"]
    st = state if state is not None else power.read_state()
    caps = power.profile_for(st, need_gb)
    why: list[str] = []
    if not caps.allow_background_work:
        idle = st.idle_seconds
        idle_txt = "unknown" if idle is None else f"{idle:.0f}s"
        on_ac = "unknown" if st.on_ac is None else ("yes" if st.on_ac else "no")
        why.append(f"machine busy or unplugged: profile={caps.profile}, "
                   f"on_ac={on_ac}, idle={idle_txt} (need >={idle_gate:.0f}s)"
                   + (f" [{'; '.join(caps.reasons)}]" if caps.reasons else ""))
    if rows is None:
        rows = [r for r in ledger.load()
                if r.get("tier") not in ("vision", "shed")]
    if trained_n is None:
        from flash.learn import load_router
        bundle = load_router()
        trained_n = int(bundle["n_rows"]) if bundle is not None and "n_rows" in bundle else 0
    new = len(rows) - trained_n
    if new < min_new:
        why.append(f"nothing new: {new} ledger row(s) since the last fit "
                   f"(need {min_new})")
    return (not why), why


def lower_priority(amount: int = 10) -> str:
    """Give the human's processes the CPU first. Best-effort, never fatal."""
    try:
        os.nice(amount)
        return f"priority lowered (nice +{amount})"
    except Exception as e:              # pragma: no cover - platform policy
        return f"priority unchanged ({type(e).__name__})"


# ---------------------------------------------------------------------- run

def run_fit(small_repo: str, budget_s: float = BUDGET_S, chunk: int = CHUNK,
            force: bool = False, kind: str = "router-fit", verbose: bool = True,
            jobs_dir: Path | None = None, cache_path: Path | None = None,
            router_path: Path | None = None, embed_fn=None,
            rows: list[dict] | None = None,
            state: power.SystemState | None = None) -> str:
    """One gated, resumable router refit. Returns a status line.

    `embed_fn`/`rows`/`state`/paths are injectable so the gate and the
    kill-and-resume behaviour are testable offline; production leaves them
    None and gets the 7B, the ledger and a live sample of the machine.
    """
    from flash import learn

    cache_path = Path(cache_path or learn.EMB_CACHE)
    router_path = Path(router_path or learn.ROUTER_FILE)
    t0 = time.monotonic()
    if rows is None:
        rows = [r for r in ledger.load() if ledger_trainable(r)]
    prompts = [r.get("prompt") or "" for r in rows]
    prompts = [p for p in prompts if p]
    prev = load_state(kind, jobs_dir)
    st = JobState(kind=kind, rows=len(rows), total=len(prompts),
                  embedded=prev.embedded if prev else 0)

    ok, why = eligibility(force=force, rows=rows, state=state,
                          trained_n=(prev.trained_n if prev else None))
    st.eligible = ok
    st.reason = "; ".join(why)
    if not ok:
        st.stage = "refused"
        st.wall_s = round(time.monotonic() - t0, 2)
        save_state(st, jobs_dir)
        return f"[learn] refused: {st.reason}"
    if verbose:
        print(f"[learn] eligible ({st.reason or 'gate open'}); "
              f"{len(prompts)} prompt(s), budget {budget_s:.0f}s")
        print(f"[learn] {lower_priority()}")

    own_embed = embed_fn is None
    if own_embed:
        from mlx_lm import load
        import mlx.core as mx
        model, tok = load(small_repo)
        embed_fn = lambda p: learn.embed_text(model, tok, p, pool="last")  # noqa: E731

    st.stage = "embedding"
    save_state(st, jobs_dir)

    def _embed(p):
        return embed_fn(p)

    try:
        cache, remaining = learn.embed_backfill(
            prompts, _embed, cache_path=cache_path, chunk=chunk,
            budget_s=budget_s,
            on_progress=lambda done, total: (setattr(st, "embedded", done),
                                             save_state(st, jobs_dir)))
    finally:
        if own_embed:
            del model, tok
            mx.clear_cache()
    if remaining:
        st.stage = "paused"
        st.trained_n = prev.trained_n if prev else 0
        st.wall_s = round(time.monotonic() - t0, 2)
        st.reason = f"{len(remaining)} prompt(s) left unembedded at budget"
        save_state(st, jobs_dir)
        return (f"[learn] paused after {st.wall_s}s: {st.reason} "
                f"— rerun to resume (checkpoint: {state_path(kind, jobs_dir)})")

    st.stage = "fitting"
    save_state(st, jobs_dir)
    X = np.stack([cache[p] for p in prompts])
    bundle = {**learn.fit_router(rows, X), "n_rows": np.array(len(rows))}
    learn.save_router(bundle, router_path)
    st.stage = "done"
    st.trained_n = len(rows)
    st.wall_s = round(time.monotonic() - t0, 2)
    st.reason = ""
    save_state(st, jobs_dir)
    return (f"[learn] router refit on {len(rows)} honest outcomes in "
            f"{st.wall_s}s -> {router_path.name}")


def ledger_trainable(row: dict) -> bool:
    from flash.learn import trainable
    return trainable(row)


# --------------------------------------------------------------- LoRA job

LORA_STEPS = 256           # one overnight experiment's worth of steps
LORA_SLICE = 32            # steps between checkpoints: what a kill can cost
LORA_PATIENCE = 3          # non-improving checkpoints before the run stops


def run_lora(small_repo: str, dataset_dir=None, adapter_name: str = "self-improve",
             steps_total: int = LORA_STEPS, slice_iters: int = LORA_SLICE,
             patience: int = LORA_PATIENCE, budget_s: float = BUDGET_S,
             force: bool = False, kind: str = "lora-fit", verbose: bool = True,
             jobs_dir: Path | None = None, adapter_dir=None, train_fn=None,
             rows: list[dict] | None = None,
             state: power.SystemState | None = None) -> str:
    """One gated, resumable LoRA fit on the agent's own verified outcomes (R-6.4).

    The gate is the same `eligibility()` the router refit uses, and it runs
    BEFORE anything is imported or written: refused here means zero training
    calls and an untouched adapter directory. §34.3's acceptance test is that
    the machine's idle state decides, not the caller's intention.

    What it does then is hand the whole step budget to `flash.train.train_lora`,
    which owns the slice loop, the validation measurement and the early stop —
    this function owns only the gate, the priority, the wall clock and the
    position the next idle window resumes from. `on_progress` fires once per
    slice, so a `kill -9` costs at most `slice_iters` steps of work.

    `train_fn` is injectable so the gate, the resume position and the pause are
    checkable offline without a model; production leaves it None.
    """
    from flash import train as tr

    t0 = time.monotonic()
    ds = Path(dataset_dir or (tr.DATASET_DIR / "ledger-verified"))
    n_train = len(tr.read_jsonl(ds / "train.jsonl"))
    if not n_train:
        return (f"[learn] no dataset at {ds} — build it first: "
                f"python -m flash.train --dataset --held-out <frozen suite>")
    ad = Path(adapter_dir or (tr.ADAPTER_DIR / adapter_name))

    ledger_rows = ([r for r in ledger.load()
                    if r.get("tier") not in ("vision", "shed")]
                   if rows is None else rows)
    prev = load_state(kind, jobs_dir)
    start = prev.iters_done if prev and prev.stage in ("training", "paused") else 0
    st = JobState(kind=kind, rows=len(ledger_rows), iters_done=start,
                  iters_total=steps_total, total=steps_total, adapter=str(ad))

    ok, why = eligibility(force=force, rows=ledger_rows, state=state,
                          trained_n=(prev.trained_n if prev else 0))
    st.eligible = ok
    st.reason = "; ".join(why)
    if not ok:
        # A refusal records the refusal; it does not erase the position. A job
        # that was PAUSED stays paused — its weights are still on disk at that
        # step — and only says that this attempt was refused. Rewriting the
        # stage would make the next idle window start from cold while believing
        # it had never begun, which is how an overnight run silently becomes two.
        if prev and prev.kind == st.kind and prev.stage in ("paused", "training"):
            st.stage = prev.stage
            st.iters_done, st.iters_total = prev.iters_done, prev.iters_total
            st.adapter, st.trained_n = prev.adapter, prev.trained_n
        else:
            st.stage = "refused"
        st.wall_s = round(time.monotonic() - t0, 2)
        save_state(st, jobs_dir)
        return f"[learn] refused: {st.reason}"
    if verbose:
        print(f"[learn] LoRA eligible ({st.reason or 'gate open'}); "
              f"{steps_total} step(s) from step {start}, budget {budget_s:.0f}s")
        print(f"[learn] {lower_priority()}")

    def _progress(done, detail):
        st.stage = "training"
        st.iters_done = done
        st.detail = detail
        save_state(st, jobs_dir)

    rec = (train_fn or tr.train_lora)(
        repo=small_repo, dataset_dir=ds, adapter_dir=ad, steps_total=steps_total,
        start_step=start, slice_iters=slice_iters, eval_every=slice_iters,
        patience=patience, budget_s=budget_s, verbose=verbose,
        on_progress=_progress)
    # `iters_done` is the position a resume loads, so it is the step whose
    # weights are at the adapter root — usually the best-measured one, which is
    # behind the last step trained. Naming them apart is what stops a resumed
    # job from claiming a position its shipped weights never reached.
    st.iters_done = rec["promoted_step"]
    st.detail = (f"root weights = step {rec['promoted_step']} "
                 f"({rec['iters_done']} trained), best loss {rec['best_loss']}, "
                 f"stopped: {rec['stopped']}")
    st.wall_s = round(time.monotonic() - t0, 2)
    # the record belongs next to the weights it describes: an arm that loads this
    # adapter later must be able to see which step was promoted, and why
    (ad / "train_record.json").write_text(json.dumps(
        {**rec, "dataset": str(ds), "adapter": str(ad), "train_rows": n_train,
         "slice_iters": slice_iters, "patience": patience,
         "gate": st.reason or "gate open"},
        indent=2, sort_keys=True))
    if rec["stopped"] == "budget" and rec["iters_done"] < steps_total:
        st.stage = "paused"
        save_state(st, jobs_dir)
        return (f"[learn] LoRA paused after {st.wall_s}s: {rec['iters_done']}/"
                f"{steps_total} step(s) — rerun to resume "
                f"(checkpoint: {state_path(kind, jobs_dir)})")
    st.stage = "done"
    st.trained_n = len(ledger_rows)
    st.reason = ""
    save_state(st, jobs_dir)
    return (f"[learn] LoRA on {n_train} verified row(s): {rec['iters_done']} "
            f"step(s) in {st.wall_s}s, best valid loss {rec['best_loss']} at step "
            f"{rec['best_step']} ({rec['stopped']}) -> {ad}")


# ---------------------------------------------------------------- selftest

def run_selftest(verbose: bool = True) -> int:
    """Offline acceptance test for §34.3: gate, budget, kill, resume."""
    import tempfile

    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))

    def state(**kw) -> power.SystemState:
        base = dict(on_ac=True, battery_pct=100.0, mem_free_pct=80.0,
                    mem_total_gb=32.0, load_per_core=0.3, idle_seconds=900.0,
                    cores=10)
        base.update(kw)
        return power.SystemState(**base)

    # -- the gate: exactly the two denials §34.3 names, plus staleness
    ok, why = eligibility(state=state(), rows=[{"tier": "small"}] * 50, trained_n=0)
    check("gate: idle + AC + new outcomes -> allowed", ok, "; ".join(why))
    ok, why = eligibility(state=state(idle_seconds=12.0), rows=[{}] * 50, trained_n=0)
    check("gate: user active 12s ago -> refused",
          not ok and "idle=12s" in why[0], why[0])
    ok, why = eligibility(state=state(on_ac=False, battery_pct=90.0), rows=[{}] * 50,
                          trained_n=0)
    check("gate: on battery -> refused", not ok and "on_ac=no" in why[0], why[0])
    ok, why = eligibility(state=state(), rows=[{}] * 12, trained_n=10)
    check("gate: only 2 new outcomes -> refused",
          not ok and "nothing new" in why[0], why[0])
    ok, why = eligibility(force=True, state=state(idle_seconds=0.0, on_ac=False))
    check("gate: --force overrides, and says so", ok and "forced" in why[0], why[0])
    ok, why = eligibility(state=state(idle_seconds=4000.0, load_per_core=6.0),
                          rows=[{}] * 50, trained_n=0)
    check("gate: high system load sheds background work too", not ok, why[0])

    # -- kill-and-resume: a deterministic fake embedder, temp artifacts
    rows = [{"prompt": f"task {i} prompt text", "tier": "small" if i % 3 else "big"}
            for i in range(24)]
    calls: list[str] = []

    def fake_embed(p):
        calls.append(p)
        h = abs(hash(p)) % 1000
        return np.array([h / 1000.0, len(p) / 100.0, 1.0, float(h % 7)], dtype=np.float64)

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cache_p, router_p, jobs_p = d / "emb.npz", d / "router.npz", d / "jobs"
        msg1 = run_fit("", budget_s=0.0, chunk=4, kind="router-fit", verbose=False,
                       jobs_dir=jobs_p, cache_path=cache_p, router_path=router_p,
                       embed_fn=fake_embed, rows=rows, state=state())
        st1 = load_state("router-fit", jobs_p)
        check("resume: budget spent -> paused, not failed",
              st1 is not None and st1.stage == "paused" and "paused" in msg1,
              f"{st1 and st1.stage}: {msg1}")
        check("resume: partial work is on disk", cache_p.exists()
              and len(learn_keys(cache_p)) > 0,
              f"{len(learn_keys(cache_p)) if cache_p.exists() else 0} embedded")
        n_after_kill = len(calls)
        msg2 = run_fit("", budget_s=60.0, chunk=4, kind="router-fit", verbose=False,
                       jobs_dir=jobs_p, cache_path=cache_p, router_path=router_p,
                       embed_fn=fake_embed, rows=rows, state=state())
        st2 = load_state("router-fit", jobs_p)
        check("resume: second run finishes from the checkpoint",
              st2 is not None and st2.stage == "done" and "refit" in msg2, msg2)
        check("resume: the killed chunk was not recomputed",
              len(calls) - n_after_kill == 24 - n_after_kill,
              f"{len(calls)} embed calls total for 24 prompts")
        check("resume: router bundle stamped with its training n",
              router_p.exists() and int(np.load(router_p)["n_rows"]) == 24,
              f"n_rows={int(np.load(router_p)['n_rows']) if router_p.exists() else '-'}")

        # refusal must leave the previous router untouched (gated change, §27.4)
        before = router_p.read_bytes()
        msg3 = run_fit("", budget_s=60.0, chunk=4, force=False, kind="router-fit",
                       verbose=False, jobs_dir=jobs_p, cache_path=cache_p,
                       router_path=router_p, embed_fn=fake_embed, rows=rows,
                       state=state())
        check("gate: a refused rerun cannot overwrite the router",
              "refused" in msg3 and router_p.read_bytes() == before, msg3)

    # checkpoints survive a corrupt/absent state file
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "jobs"
        check("state: absent checkpoint reads as None", load_state("x", p) is None)
        p.mkdir(parents=True)
        (p / "x.json").write_text("{not json")
        check("state: corrupt checkpoint degrades to None", load_state("x", p) is None)

    # ---- the LoRA job: the same gate over the heavier arm (R-6.4)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ds = d / "ds"
        ds.mkdir()
        (ds / "train.jsonl").write_text(json.dumps(
            {"messages": [{"role": "user", "content": "q"},
                          {"role": "assistant", "content": "a"}]}) + "\n")
        (ds / "valid.jsonl").write_text((ds / "train.jsonl").read_text())
        ad, jobs_p = d / "ad", d / "jobs"
        calls: list = []

        def fake_train(**kw):
            calls.append(kw)
            root = Path(kw["adapter_dir"])
            root.mkdir(parents=True, exist_ok=True)
            (root / "adapters.safetensors").write_bytes(b"w")
            if kw.get("on_progress"):
                kw["on_progress"](kw["start_step"] + kw["slice_iters"], "one slice")
            return {"iters_done": kw["start_step"] + kw["slice_iters"],
                    "steps_total": kw["steps_total"],
                    "start_step": kw["start_step"],
                    "promoted_step": kw["start_step"] + kw["slice_iters"],
                    "best_step": kw["start_step"] + kw["slice_iters"],
                    "best_loss": 1.25, "evals": [], "stopped": "steps", "wall_s": 1.0}

        msg = run_lora("", dataset_dir=ds, adapter_name="t", steps_total=64,
                       slice_iters=16, force=False, verbose=False, jobs_dir=jobs_p,
                       adapter_dir=ad, train_fn=fake_train,
                       state=state(on_ac=False, battery_pct=40.0),
                       rows=[{}] * 30)
        check("gate: on battery the LoRA job refuses and makes zero training calls",
              "refused" in msg and not calls and not ad.exists(), f"{msg} "
              f"{len(calls)} call(s)")
        msg = run_lora("", dataset_dir=ds, steps_total=64, slice_iters=16,
                       verbose=False, jobs_dir=jobs_p, adapter_dir=ad,
                       train_fn=fake_train, state=state(), rows=[{}] * 30)
        st_l = load_state("lora-fit", jobs_p)
        check("gate: idle + AC opens it, and the slice budget reaches the trainer",
              "LoRA on 1" in msg and st_l.stage == "done"
              and calls[0]["steps_total"] == 64, msg)
        check("the record next to the weights names the step that was promoted",
              json.loads((ad / "train_record.json").read_text())["promoted_step"]
              == 16, (ad / "train_record.json").read_text()[:60])
        # a paused job resumes at its banked step, and the gate still decides
        save_state(JobState(kind="lora-fit", stage="paused", iters_done=48,
                            iters_total=64, trained_n=0), jobs_p)
        calls.clear()
        msg = run_lora("", dataset_dir=ds, steps_total=64, slice_iters=16,
                       verbose=False, jobs_dir=jobs_p, adapter_dir=ad,
                       train_fn=fake_train, state=state(idle_seconds=3.0),
                       rows=[{}] * 30)
        check("gate: a RESUMING job is still refused while the user is typing",
              "refused" in msg and not calls and "idle=3s" in msg, msg)
        calls.clear()
        msg = run_lora("", dataset_dir=ds, steps_total=64, slice_iters=16,
                       verbose=False, jobs_dir=jobs_p, adapter_dir=ad,
                       train_fn=fake_train, state=state(), rows=[{}] * 30)
        check("resume: the banked step is where training continues from",
              calls and calls[0]["start_step"] == 48
              and load_state("lora-fit", jobs_p).iters_done == 64,
              f"{calls[0]['start_step'] if calls else '-'} | {msg}")
        # no dataset is a refusal with an explanation, not a crash
        calls.clear()
        msg = run_lora("", dataset_dir=d / "nowhere", jobs_dir=jobs_p,
                       adapter_dir=ad, train_fn=fake_train, state=state(),
                       rows=[{}] * 30)
        check("no dataset: it says what to run instead of loading a model",
              "no dataset" in msg and "--held-out" in msg and not calls, msg)

    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\njobs selftest: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


def learn_keys(cache_path: Path) -> list[str]:
    import numpy as np
    return list(np.load(cache_path).files)


if __name__ == "__main__":
    raise SystemExit(run_selftest())
