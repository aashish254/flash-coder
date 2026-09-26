"""The agent's own verified outcomes, turned into weights (§27.3 layer 3, R-6.4).

R-6.4 asks for one component that improves BY LEARNING, not by editing. The
ledger already says which tasks the small tier solved, and `--trace-full`
sessions keep the exact conversation and the exact answer for every generation —
so the material for a supervised fine-tune is already on disk. This module
selects it, and nothing here invents a label:

  verified-only   a row enters the dataset only when the session's own
                  `task_end` record says that task was SOLVED, and only from the
                  generate whose attempt index closed it. An answer the oracle
                  never accepted is not a demonstration, and an early attempt
                  that was later repaired is a negative example wearing a
                  positive label.
  leakage rule    the eval suite's task ids are excluded from training before
                  anything is written, and the exclusion is counted. A LoRA
                  trained on the very tasks it is then scored on measures
                  memory, not learning — the one result that would make the
                  whole requirement meaningless.
  text, not guess a row carries the templated prompt exactly as the run handed
                  it to mlx. It is parsed back into messages and re-rendered,
                  and the re-render must equal the original; a row that fails
                  that round-trip is dropped and counted, not repaired.

Training is mlx-lm's own (`mlx_lm.lora.train_model`), wrapped twice over: it
runs in iters-slices, each resuming from the previous slice's saved weights, so
an idle window that ends mid-job is a pause rather than a loss; and it stops
early, on validation loss, because on a few dozen rows the model keeps
improving its own training loss well past the point where it has begun to
memorise. The slice is the unit of progress a kill cannot take back; the
validation checkpoint is the unit the shipped adapter is chosen on.

    python -m flash.train --dataset --dry-run          # mine and count, write nothing
    python -m flash.train --dataset --held-out benchmarks/tasks/p6_tasks.jsonl
    flash learn --lora --steps 256                     # the gated training entry
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE_DIR = ROOT / "benchmarks" / "results" / "traces"
DATASET_DIR = ROOT / "benchmarks" / "results" / "datasets"
ADAPTER_DIR = ROOT / "benchmarks" / "results" / "adapters"
TASK_DIR = ROOT / "benchmarks" / "tasks"

OPEN = "<" + "|im_start|" + ">"
CLOSE = "<" + "|im_end|" + ">"
GEN = OPEN + "assistant\n"
_TURN = re.compile(re.escape(OPEN) + r"(\w+)\n(.*?)" + re.escape(CLOSE) + "\n",
                   re.S)


def parse_messages(prompt: str) -> list[dict]:
    """ChatML text back to a message list; [] when it is not ChatML at all."""
    return [{"role": r, "content": c} for r, c in _TURN.findall(prompt or "")]


def body_of(prompt: str) -> str:
    """The prompt with its dangling assistant header removed.

    A generate record's prompt ends mid-turn: the header is there to be
    completed, so the closed turns are the prompt minus that header. Compared
    whole, every real row would read as a template failure.
    """
    p = prompt or ""
    return p[:-len(GEN)] if p.endswith(GEN) else p


def strip_stop(text: str) -> tuple[str, bool]:
    """(content, did it write its own end-of-turn).

    `stream_generate` decodes the turn terminator into the text it yields, while
    `generate` stops at the template's stop strings and does not, so the trace
    store holds both shapes. The renderer closes every turn, so the trailing one
    is removed here — dropping those rows would discard most of the store over a
    formatting difference, and keeping them would double-close the turn.
    """
    t = text or ""
    tail = t.rstrip(" \n")
    if tail.endswith(CLOSE):
        return tail[:-len(CLOSE)], True
    return t, False


def render(messages: list[dict]) -> str:
    """What mlx's template produces for a list of closed turns."""
    out = []
    for m in messages:
        out.append(f"{OPEN}{m.get('role', '')}\n{m.get('content', '')}{CLOSE}\n")
    return "".join(out)


@dataclass
class Row:
    messages: list = field(default_factory=list)
    task_id: str = ""
    session: str = ""
    attempt: int = 0
    closed: bool = False          # this attempt is the one task_end credits
    repair: bool = False          # attempt > 0: it fixed a failure it was shown
    terminated: bool = False      # the answer wrote its own end-of-turn
    source: str = "chain"         # chain|tournament|big


def task_ids_in_suite(path) -> set:
    p = Path(path)
    if not p.exists():
        return set()
    ids = set()
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                ids.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return ids


def _sessions(traces_dir) -> list[Path]:
    return sorted(Path(traces_dir).glob("*.jsonl"))


def mine(traces_dir=TRACE_DIR, exclude=(), require_verified: bool = True,
         stats: dict | None = None) -> list[Row]:
    """Every verified answer the trace store holds, minus the excluded suite."""
    exclude = set(exclude or ())
    rows: list[Row] = []
    s = stats if stats is not None else {}

    def bump(k, n=1):
        s[k] = s.get(k, 0) + n

    for path in _sessions(traces_dir):
        events = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                bump("torn_lines")     # a truncated write is data, not an error
        ends = {e["task_id"]: e for e in events
                if e.get("type") == "task_end" and "task_id" in e}
        tour = {e["task_id"]: e for e in events
                if e.get("type") == "tournament" and "task_id" in e}
        for e in events:
            if e.get("type") != "generate" or "task_id" not in e:
                continue
            tid = e["task_id"]
            out, prompt = e.get("output"), e.get("prompt")
            if not out or not prompt:
                bump("no_stored_text")     # a session run without --trace-full
                continue
            comp, cap = e.get("completion_tokens"), e.get("max_tokens")
            if comp is not None and cap and int(comp) >= int(cap):
                # The span ended at its token budget, not at an answer: what
                # follows is whatever the mask or the model could fit. The oracle
                # may still have taken it — a cut answer that passes is a
                # demonstration about the verifier, not about writing code.
                bump("answer_hit_token_cap")
                continue
            if e.get("resumed_tokens"):
                # the span was stitched across a kill; its prompt already holds
                # half the answer, which is not a prompt a cold run would see.
                bump("resumed_span")
                continue
            if tid in exclude:
                bump("excluded_leakage")
                continue
            end = ends.get(tid)
            if require_verified and not (end and end.get("solved")):
                bump("unverified_task")
                continue
            attempt = int(e.get("attempt") or 0)
            closed = end is not None and attempt == int(end.get("attempts") or 1) - 1
            if require_verified and not closed:
                # an accepted task can contain rejected attempts; only the one
                # the ledger credits was ever executed and passed as-is.
                bump("not_the_closing_attempt")
                continue
            msgs = parse_messages(prompt)
            if not msgs or render(msgs) != body_of(prompt):
                bump("no_roundtrip")
                continue
            if not prompt.endswith(GEN):
                # Without the header there is no boundary between the condition
                # and the completion, so nothing here can be masked or trusted.
                bump("no_generation_header")
                continue
            body, stop = strip_stop(out)
            if CLOSE in body:
                # A stop token in the MIDDLE of an answer is not a terminator:
                # that turn really did contain template text, and training on it
                # teaches the model to write its own framing as content.
                bump("stop_token_inside")
                continue
            msgs = msgs + [{"role": "assistant", "content": body}]
            if stop:
                # mlx wrote the end-of-turn into the text it returned; the
                # renderer adds it back, so it is stripped here rather than
                # dropping the row. Which of the two happened is counted, not
                # guessed at — the store holds both shapes.
                bump("closed_by_stop_token")
            src = "chain"
            if str(tid) in tour and attempt == 0:
                src = "tournament"
            rows.append(Row(messages=msgs, task_id=tid, session=path.stem,
                            attempt=attempt, closed=closed,
                            repair=attempt > 0, terminated=stop, source=src))
            bump("kept")
    if stats is not None:
        s["sessions_read"] = s.get("sessions_read", 0) + len(_sessions(traces_dir))
    return rows


def split(rows: list[Row], valid_frac: float = 0.15, seed: int = 0):
    """Deterministic split, by whole task. Two attempts of one task are the same
    conversation; sending one to train and one to valid leaks the answer."""
    by_task: dict[str, list[Row]] = {}
    for r in rows:
        by_task.setdefault(r.task_id, []).append(r)
    tasks = sorted(by_task)
    random.Random(seed).shuffle(tasks)
    n_valid = int(len(tasks) * valid_frac)
    valid_ids = set(tasks[:n_valid])
    train = [r for t in tasks if t not in valid_ids for r in by_task[t]]
    valid = [r for t in tasks if t in valid_ids for r in by_task[t]]
    return train, valid


def write_dataset(rows: list[Row], out_dir, valid_frac: float = 0.15,
                  seed: int = 0, meta: dict | None = None) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train, valid = split(rows, valid_frac=valid_frac, seed=seed)
    for name, part in (("train.jsonl", train), ("valid.jsonl", valid)):
        with (out / name).open("w") as fid:
            for r in part:
                # task_id rides along with the row: mlx's ChatDataset reads
                # `messages` and ignores the rest, so the written set stays
                # checkable — a reader can prove the frozen suite's ids are not
                # in here instead of trusting the manifest's count.
                fid.write(json.dumps({"messages": r.messages,
                                      "task_id": r.task_id}) + "\n")
    info = dict(meta or {})
    info.update(train_rows=len(train), valid_rows=len(valid),
                train_tasks=len({r.task_id for r in train}),
                valid_tasks=len({r.task_id for r in valid}),
                train_task_ids=sorted({r.task_id for r in train}),
                valid_task_ids=sorted({r.task_id for r in valid}),
                repairs=sum(1 for r in rows if r.repair))
    (out / "manifest.json").write_text(json.dumps(info, indent=2, sort_keys=True))
    return out


def dataset_stats(rows: list[Row]) -> dict:
    seq = [len(render(r.messages)) for r in rows]
    return {"rows": len(rows), "tasks": len({r.task_id for r in rows}),
            "repairs": sum(1 for r in rows if r.repair),
            "chars_min": min(seq) if seq else 0,
            "chars_median": sorted(seq)[len(seq) // 2] if seq else 0,
            "chars_max": max(seq) if seq else 0}


# --------------------------------------------------------------- training


def slice_args(repo: str, adapter_dir, iters: int, resume_from=None,
               num_layers: int = 16, rank: int = 8,
               learning_rate: float = 1e-4, batch_size: int = 1,
               max_seq_length: int = 2048, seed: int = 0):
    """mlx-lm's training arguments for one slice, as the object it expects.

    Split out of `default_slice` because mlx reads these as *attributes*
    (`args.seed`, then `vars(args)`), so a dict — the natural thing to hand a
    function — raises at the first step of the first live training run. Building
    it apart from the model load is what lets the offline check catch that.

    `iters` is mlx's step count, not an epoch count: one iter is one batch. So
    `save_every=iters` writes `adapters.safetensors` exactly once, at the end of
    the slice, which is the boundary a kill cannot cross.
    """
    from types import SimpleNamespace

    from mlx_lm.lora import CONFIG_DEFAULTS

    args = dict(CONFIG_DEFAULTS)
    args.update(model=repo, train=True, fine_tune_type="lora", seed=seed,
                num_layers=num_layers, batch_size=batch_size, iters=iters,
                learning_rate=learning_rate, max_seq_length=max_seq_length,
                adapter_path=str(adapter_dir),
                resume_adapter_file=(str(resume_from) if resume_from else None),
                lora_parameters={"rank": rank, "dropout": 0.0, "scale": 20.0},
                mask_prompt=True, save_every=iters,
                steps_per_report=max(iters // 5, 1), steps_per_eval=10 ** 9,
                val_batches=4, test=False, report_to=None, project_name=None,
                grad_checkpoint=True, optimizer="adamw",
                optimizer_config=CONFIG_DEFAULTS["optimizer_config"],
                lr_schedule=None, clear_cache_threshold=0)
    return SimpleNamespace(**args)


def read_jsonl(path) -> list:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def default_slice(*, repo: str, train_file: Path, valid_file: Path,
                  adapter_dir: Path, iters: int, resume_from: Path | None,
                  seed: int = 0, verbose: bool = True, **kw) -> float:
    """One mlx-lm LoRA slice of `iters` steps. Returns the wall seconds it took.

    Imports live inside so that mining a dataset — which is what the offline
    checks do — never reaches for Metal.
    """
    import time

    from mlx_lm import load
    from mlx_lm.lora import train_model
    from mlx_lm.tuner.datasets import ChatDataset

    data = read_jsonl(train_file)
    if not data:
        raise ValueError(f"no training rows in {train_file}")
    vdata = read_jsonl(valid_file)
    model, tok = load(repo)
    train_set = ChatDataset(data, tok, mask_prompt=True)
    valid_set = ChatDataset(vdata, tok, mask_prompt=True) if vdata else train_set
    args = slice_args(repo, Path(adapter_dir), iters, resume_from, seed=seed, **kw)
    t0 = time.monotonic()
    train_model(args, model, train_set, valid_set)
    del model, tok
    return round(time.monotonic() - t0, 2)


# ------------------------------------------------------------- artifact reads


def valid_loss(repo: str, adapter_dir, valid_file, batch_size: int = 1,
               max_seq_length: int = 2048) -> float:
    """Mean next-token loss on the valid split, with this adapter loaded.

    Measured outside mlx's own validation pass because the number has to be
    attributable to a checkpoint on disk — which is also the thing the early
    stop keeps and the thing an arm later re-loads.
    """
    from mlx_lm import load
    from mlx_lm.tuner.datasets import ChatDataset
    from mlx_lm.tuner.trainer import CacheDataset, evaluate

    model, tok = load(repo, adapter_path=str(adapter_dir))
    data = read_jsonl(valid_file)
    if not data:
        raise ValueError(f"no validation rows in {valid_file}")
    ds = CacheDataset(ChatDataset(data, tok, mask_prompt=True))
    loss = evaluate(model=model, dataset=ds, batch_size=batch_size,
                    num_batches=max(len(data) // batch_size, 1),
                    max_seq_length=max_seq_length)
    del model, tok
    return float(loss)


def _checkpoint(adapter_dir, step) -> Path:
    """Snapshot the slice's weights under their own step number.

    A copy, not a move: mlx always writes the next slice into the same root file,
    and the resume path has to keep existing. The step number is what the record
    names, so a later reading of the run knows which weights scored what.
    """
    root = Path(adapter_dir)
    ck = root / f"step-{step}"
    ck.mkdir(parents=True, exist_ok=True)
    for name in ("adapters.safetensors", "adapter_config.json"):
        if (root / name).exists():
            shutil.copy2(root / name, ck / name)
    return ck


def train_lora(*, repo: str, dataset_dir, adapter_dir, steps_total: int = 256,
               start_step: int = 0, slice_iters: int = 32, eval_every: int = 32,
               patience: int = 3, budget_s: float | None = None, seed: int = 0,
               verbose: bool = True, run=None, loss_fn=None,
               on_progress=None) -> dict:
    """Train, measure, and keep the least-overfit checkpoint. Returns the record.

    Three rules the numbers depend on:

    * **A slice is the resumable unit.** Each one re-loads the weights the
      previous one saved (`resume_adapter_file`) and appends `slice_iters`
      steps; `start_step` is where a resumed job picks up. mlx restores weights,
      not the Adam moments, so a resumed slice starts its optimiser fresh — the
      honest cost of making a kill cheap.
    * **Training longer is not the same as training better.** On a few dozen
      rows of verified answers the model keeps driving its own training loss
      down after the validation loss has turned around; a fixed step count would
      then ship the adapter that memorises the condition and repeats the answer.
      So every `eval_every` steps the checkpoint is measured and the best kept.
    * **`patience` stops the run, it does not rescue it.** After `patience`
      checkpoints that fail to improve, the loop stops and says so.

    `run`/`loss_fn` are injectable so the loop, the early stop and the promote
    are checkable offline without a model; production leaves them None.
    """
    import time

    run = run or default_slice
    loss_fn = loss_fn or valid_loss
    ds = Path(dataset_dir)
    tr, va = ds / "train.jsonl", ds / "valid.jsonl"
    if not read_jsonl(tr):
        raise ValueError(f"no training rows in {tr} — run "
                         f"`python -m flash.train --dataset --held-out ...`")
    root = Path(adapter_dir)
    root.mkdir(parents=True, exist_ok=True)
    weights = root / "adapters.safetensors"
    if start_step and not weights.exists():
        # resuming into a directory with no weights would train the BASE model
        # while the checkpoint claimed these steps were already banked
        raise ValueError(f"resume at step {start_step} but {weights} is missing")

    t0 = time.monotonic()
    done, stalls, evals = start_step, 0, []
    best_step, best_loss = None, float("inf")
    stopped = "steps"
    while done < steps_total:
        if budget_s is not None and (time.monotonic() - t0) >= budget_s:
            stopped = "budget"
            break
        n = min(slice_iters, steps_total - done)
        sec = run(repo=repo, train_file=tr, valid_file=va, adapter_dir=root,
                  iters=n, resume_from=(weights if done else None), seed=seed)
        done += n
        if not weights.exists():
            raise RuntimeError(f"slice of {n} step(s) saved no weights to {weights}")
        if on_progress:
            on_progress(done, f"slice {n} step(s) in {sec}s")
        if done % eval_every == 0 or done == steps_total:
            ck = _checkpoint(root, done)
            loss = round(loss_fn(repo=repo, adapter_dir=ck, valid_file=va), 4)
            evals.append((done, loss))
            if verbose:
                print(f"[lora] step {done}: valid loss {loss}")
            if loss < best_loss - 1e-4:
                best_step, best_loss, stalls = done, loss, 0
            else:
                stalls += 1
                if stalls >= patience:
                    stopped = (f"overfit — {stalls} checkpoint(s) worse than "
                               f"step {best_step} (its loss {best_loss})")
                    break
    # The root file is what a loader reads, so the record names the step it
    # actually holds. A resume loads THIS, not `iters_done`: the promoted
    # checkpoint is usually an earlier one, and continuing from a step the
    # shipped weights never trained would be a fabricated position.
    promoted = best_step if best_step is not None else done
    if promoted != done:
        src = root / f"step-{promoted}"
        for name in ("adapters.safetensors", "adapter_config.json"):
            if (src / name).exists():
                shutil.copy2(src / name, root / name)
    return {"iters_done": done, "steps_total": steps_total,
            "start_step": start_step, "promoted_step": promoted,
            "best_step": best_step, "best_loss": (None if best_loss == float("inf")
                                                  else best_loss),
            "evals": evals, "stopped": stopped,
            "wall_s": round(time.monotonic() - t0, 2)}


def adapter_files(adapter_dir) -> dict:
    p = Path(adapter_dir)
    return {"dir": str(p), "exists": (p / "adapters.safetensors").exists(),
            "config": (p / "adapter_config.json").exists()}


def read_config(adapter_dir) -> dict:
    p = Path(adapter_dir) / "adapter_config.json"
    return json.loads(p.read_text()) if p.exists() else {}


def label_for(repo: str, adapter_dir=None) -> str:
    """The model label a ledger row carries: the brain, plus the adapter on top."""
    name = str(repo).split("/")[-1]
    if adapter_dir:
        name += f"+lora:{Path(adapter_dir).name}"
    return name


# ------------------------------------------------------------- entry point


def build_dataset(held_out=None, out_name: str = "ledger-verified",
                  dry_run: bool = False, traces_dir=TRACE_DIR,
                  valid_frac: float = 0.15, seed: int = 0, verbose: bool = True):
    """Mine, split and (unless asked not to) write the SFT pair files."""
    exclude: set[str] = set()
    suites: list[str] = []
    for spec in (held_out or []):
        ids = task_ids_in_suite(spec)
        exclude |= ids
        suites.append(f"{Path(str(spec)).name}:{len(ids)}")
    stats: dict = {}
    rows = mine(traces_dir=traces_dir, exclude=exclude, stats=stats)
    ds = dataset_stats(rows)
    if verbose:
        print(f"[train] read {stats.get('sessions_read', 0)} trace session(s) "
              f"from {Path(traces_dir).parent}")
        for k in sorted(stats):
            if k != "kept":
                print(f"[train]   dropped {k}: {stats[k]}")
        held = ("excluded " + ", ".join(suites)) if suites else "NOTHING excluded"
        print(f"[train] leakage rule: {held} -> {ds['rows']} rows over "
              f"{ds['tasks']} task(s); repairs {ds['repairs']}; "
              f"chars median {ds['chars_median']} max {ds['chars_max']}")
    if dry_run:
        return rows, stats, None
    out = write_dataset(rows, DATASET_DIR / out_name, valid_frac=valid_frac,
                        seed=seed, meta={"held_out_suites": suites,
                                         "excluded_count": len(exclude),
                                         "excluded_task_ids": sorted(str(i)
                                                                      for i in exclude),
                                         "drops": stats, "stats": ds})
    if verbose:
        print(f"[train] wrote {out} ({ds['rows']} rows)")
    return rows, stats, out


def suite_from_dataset(split: str = "train", dataset=None, tasks_dir=None,
                       out=None, verbose: bool = True):
    """A tasks file holding exactly the ids one side of the dataset was built from.

    R-6.4's held-out arm answers "did it transfer?". This answers the question
    before it: "did it learn at all?" — run the adapter on the tasks it was
    trained on and compare with the base model. The answer means memory, not
    generalisation, and saying so is the whole point of putting it behind a flag
    instead of a default; an in-distribution pass rate must never be reachable
    by forgetting to pass `--tasks`.

    `dataset` is the built dataset directory and `tasks_dir` the suite folder,
    each resolved from the tree at call time — the ids come from that
    directory's manifest, so a check can aim this at a temp dataset without
    touching the shipped one.
    """
    ds = Path(dataset or (DATASET_DIR / "ledger-verified"))
    tdir = Path(tasks_dir or TASK_DIR)
    man = json.loads((ds / "manifest.json").read_text())
    ids = set(man[f"{split}_task_ids"])
    if not ids:
        raise ValueError(f"manifest at {ds} names no '{split}' tasks")
    out = Path(out or (tdir / f"r64_{split}_from_dataset.jsonl"))
    rows, seen, dupes = [], {i for i in ids}, 0
    for path in sorted(tdir.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
            except json.JSONDecodeError:
                continue
            if t.get("id") in ids:
                # An id lives in more than one suite file, and a duplicated row
                # would silently double that task's weight in the pass rate — so
                # this file holds one row per id, in first-file order.
                if t["id"] not in seen:
                    dupes += 1
                    continue
                rows.append(t)
                seen.discard(t["id"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(t) for t in rows) + "\n")
    if verbose:
        print(f"[train] {split} side: {len(rows)} task(s)"
              + (f", {dupes} duplicate row(s) dropped" if dupes else "")
              + f" -> {out}"
              + (f"; {len(seen)} id(s) appear in no suite file: {sorted(seen)}"
                 if seen else ""))
    return out, len(rows), sorted(seen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", action="store_true",
                    help="mine the trace store and write train/valid jsonl")
    ap.add_argument("--dry-run", action="store_true",
                    help="mine and print the counts, write nothing")
    ap.add_argument("--held-out", action="append", default=[],
                    help="a tasks file whose ids MUST NOT be trained on "
                         "(repeatable). The frozen suite R-6.4 scores on.")
    ap.add_argument("--out", default="ledger-verified",
                    help="the dataset directory name under benchmarks/results/"
                         "datasets: written by --dataset, read by "
                         "--suite-from-dataset")
    ap.add_argument("--suite-from-dataset", action="store_true",
                    help="write the tasks file for one side of the built dataset "
                         "(the in-distribution arm: memory, not transfer)")
    ap.add_argument("--split", default="train", choices=("train", "valid"))
    ap.add_argument("--suite-out", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return run_selftest()
    if a.suite_from_dataset:
        _, n, missing = suite_from_dataset(split=a.split,
                                           dataset=DATASET_DIR / a.out,
                                           out=a.suite_out)
        return 0 if n and not missing else 1
    if a.dataset or a.dry_run:
        _, _, out = build_dataset(held_out=a.held_out, out_name=a.out,
                                  dry_run=a.dry_run)
        return 0 if (a.dry_run or out) else 1
    ap.print_help()
    return 1


# -------------------------------------------------------------- offline test

def run_selftest(verbose: bool = True) -> int:
    """Offline: no model, no Metal, no training — the data law and the slices."""
    import tempfile

    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}"
                  + (f"  {detail}" if detail else ""))

    def sess(tmp, name, events):
        p = Path(tmp) / f"{name}.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
        return p

    PROMPT = (OPEN + "user\nWrite f().\n" + CLOSE + "\n" + GEN)
    # what a retry actually looks like on disk: the failed answer and the
    # oracle's complaint are in the conversation the model was conditioned on
    REPAIR = (PROMPT[:-len(GEN)] + OPEN + "assistant\nwrong\n" + CLOSE + "\n"
              + OPEN + "user\nIt failed:\nAssertionError: 3 != 4\n"
              + CLOSE + "\n" + GEN)

    def ev(task, attempt, out, prompt=PROMPT, **kw):
        return dict(ts=0.0, seq=0, type="generate", task_id=task,
                    attempt=attempt, prompt=prompt, output=out, **kw)

    def end(task, solved=True, attempts=1):
        return dict(ts=0.0, seq=1, type="task_end", task_id=task,
                    solved=solved, attempts=attempts, tier="small",
                    seconds=1.0, routed="small")

    with tempfile.TemporaryDirectory() as d:
        tr = Path(d) / "traces"
        tr.mkdir()
        # a one-attempt success and a repaired success, both verified
        sess(tr, "s1", [
            ev("a1", 0, "42"), end("a1"),
            ev("a2", 0, "wrong"),
            dict(ts=0.0, seq=2, type="verify", task_id="a2", attempt=0, ok=False,
                 err="AssertionError"),
            ev("a2", 1, "right", prompt=REPAIR), end("a2", attempts=2),
        ])
        # a task the oracle never accepted, and one whose closing attempt differs
        sess(tr, "s2", [
            ev("b1", 0, "nope"), end("b1", solved=False),
            ev("b2", 0, "kept"), end("b2", attempts=3),
            dict(ts=0.0, seq=9, type="generate", task_id="b3", attempt=0),
        ])
        # a torn line, and a span that came back from a kill
        p = tr / "s3.jsonl"
        p.write_text(json.dumps(ev("c1", 0, "ok")) + "\n"
                     + '{"task_id": "c1", "type": "task_end"' + "\n"
                     + json.dumps(ev("c2", 0, "ok2", resumed_tokens=7)) + "\n"
                     + json.dumps(end("c1")) + "\n"
                     + json.dumps(end("c2")) + "\n")

        st: dict = {}
        rows = mine(traces_dir=tr, stats=st)
        ids = sorted(r.task_id for r in rows)
        check("verified-only: the solved tasks are kept, the unsolved one dropped",
              ids == ["a1", "a2", "c1"], f"{ids} {st}")
        check("verified-only: only the attempt the session credits becomes a row"
              " — neither a rejected draft nor an answer whose task closed later",
              st.get("not_the_closing_attempt") == 2
              and st.get("unverified_task") == 1, str(st))
        check("round-trip: a prompt that is not the template it claims is dropped",
              st.get("no_roundtrip", 0) == 0, str(st))
        check("a torn trace line is counted as data, not a crash",
              st.get("torn_lines") == 1, str(st))
        check("R-5.3: a span resumed across a kill is not training text",
              "c2" not in ids and st.get("resumed_span") == 1, str(st))
        rep = [r for r in rows if r.repair]
        check("a repair row keeps the failure conversation it was fixed from",
              len(rep) == 1 and rep[0].task_id == "a2"
              and len(rep[0].messages) == 4
              and "AssertionError" in rep[0].messages[2]["content"],
              json.dumps(rep[0].messages[2])[:60] if rep else "none")
        accepted = {"a1": "42", "a2": "right", "c1": "ok"}
        check("the assistant turn the model is trained on is the accepted answer",
              len(rows) == len(accepted)
              and all(r.messages[-1]["content"] == accepted.get(r.task_id)
                      for r in rows), str([r.messages[-1]["content"] for r in rows]))

        suite = Path(d) / "eval_tasks.jsonl"
        suite.write_text(json.dumps({"id": "a1", "prompt": "x"}) + "\n"
                         + json.dumps({"id": "b2", "prompt": "y"}) + "\n")
        st2 = {}
        kept = [r.task_id for r in mine(traces_dir=tr,
                                        exclude=task_ids_in_suite(suite),
                                        stats=st2)]
        check("leakage: the frozen suite's ids leave the training pool entirely",
              kept == ["a2", "c1"] and st2.get("excluded_leakage") == 2,
              f"{kept} {st2}")

        # the same task solved in two different sessions is two rows; a split
        # that sends them to opposite sides hands valid the answer
        twice = rows + [Row(messages=list(rows[0].messages),
                            task_id=rows[0].task_id, session="s9",
                            attempt=0, closed=True)]
        tr_rows, va_rows = split(twice, valid_frac=0.5, seed=1)
        both = {r.task_id for r in tr_rows} & {r.task_id for r in va_rows}
        check("split: a task's rows never straddle train and valid",
              not both, f"{len(tr_rows)}/{len(va_rows)} shared={sorted(both)}")
        check("split: the duplicate really did land on one side",
              sum(1 for r in tr_rows + va_rows if r.task_id == rows[0].task_id)
              == 2, rows[0].task_id)

        out = write_dataset(mine(traces_dir=tr), Path(d) / "ds")
        man = json.loads((out / "manifest.json").read_text())
        lines = (out / "train.jsonl").read_text().splitlines()
        check("the written file is mlx-lm's chat jsonl, plus the task id that "
              "makes the exclusion checkable from the artifact",
              len(lines) > 0 and set(json.loads(lines[0])) == {"messages", "task_id"}
              and json.loads(lines[0])["messages"][-1]["role"] == "assistant",
              f"{len(lines)} train row(s)")
        check("the manifest carries the counts the gates are read from",
              man["train_rows"] + man["valid_rows"] == 3
              and man["repairs"] == 1, str(man))
        check("the manifest names the tasks on each side of the split",
              sorted(man["train_task_ids"] + man["valid_task_ids"])
              == ["a1", "a2", "c1"], str(man["train_task_ids"]))

    # the in-distribution suite generator: it reads the artifact's manifest, so
    # a wrong rule hands an arm a file that silently over- or under-weights
    # tasks — and an id it cannot supply must be said out loud
    with tempfile.TemporaryDirectory() as d:
        ds = Path(d) / "ds"
        ds.mkdir()
        ids = ["a1", "a2", "c1"]
        (ds / "manifest.json").write_text(json.dumps(
            {"train_task_ids": ids, "valid_task_ids": ["v1", "v2"]}))
        td = Path(d) / "tasks"
        td.mkdir()
        # a1 lives in two suite files; c1 in none; v1 is the other side's id
        (td / "1_suite.jsonl").write_text("\n".join(
            json.dumps({"id": i, "prompt": "p"}) for i in ["a1", "a2", "v1"]) + "\n")
        (td / "2_suite.jsonl").write_text(
            json.dumps({"id": "a1", "prompt": "p"}) + "\n")
        p, n, missing = suite_from_dataset("train", dataset=ds, tasks_dir=td,
                                           verbose=False)
        got = [json.loads(l)["id"] for l in p.read_text().splitlines()]
        check("the in-distribution file holds one row per trained id, so a task "
              "that lives in two suite files cannot count twice",
              sorted(got) == ["a1", "a2"] and len(got) == len(set(got)) == n,
              f"{got} n={n}")
        check("the other side of the split stays out of the in-distribution file",
              "v1" not in got, str(got))
        check("an id the suites cannot supply is reported, not skipped",
              missing == ["c1"], str(missing))
        _, n2, _ = suite_from_dataset("train", dataset=ds, tasks_dir=td,
                                      verbose=False)
        check("writing into the directory it scans is not a feedback loop: a "
              "second run reads its own output and produces the same file",
              n2 == n and p.read_text().splitlines() == ["{\"id\": \"a1\", \"prompt\": \"p\"}",
                                                          "{\"id\": \"a2\", \"prompt\": \"p\"}"],
              f"{n2} row(s) on the second pass")
        pv, nv, missing_v = suite_from_dataset("valid", dataset=ds,
                                               tasks_dir=td, verbose=False)
        got_v = [json.loads(l)["id"] for l in pv.read_text().splitlines()]
        check("the valid side names its own tasks and nothing the train side had",
              got_v == ["v1"] and nv == 1 and not set(got_v) & set(got),
              f"{got_v} {missing_v}")
        check("a valid side with an id no suite file supplies is reported too — "
              "--suite-from-dataset exits nonzero on either gap",
              nv == 1 and missing_v == ["v2"], str(missing_v))
        (ds / "manifest.json").write_text(json.dumps(
            {"train_task_ids": [], "valid_task_ids": ["v1"]}))
        try:
            suite_from_dataset("train", dataset=ds, tasks_dir=td, verbose=False)
            raised = ""
        except ValueError as exc:
            raised = str(exc)
        check("an empty side of the split raises rather than writing a suite "
              "whose every row would be a task nobody trained on",
              "names no 'train' tasks" in raised, raised)
    # three shapes that must never become training rows, each isolated so its
    # own counter is the evidence
    with tempfile.TemporaryDirectory() as d:
        tr = Path(d) / "t"
        tr.mkdir()
        already = (PROMPT + "42" + CLOSE + "\n")     # no dangling header
        sess(tr, "s_noheader", [ev("z", 0, "42", prompt=already), end("z")])
        st3 = {}
        check("a prompt with no generation header is refused — there is no "
              "completion boundary to mask at",
              mine(traces_dir=tr, stats=st3) == []
              and st3.get("no_generation_header") == 1, str(st3))
        (tr / "s_noheader.jsonl").unlink()
        sess(tr, "s_stoptok", [ev("z", 0, "42" + CLOSE + "\n", prompt=PROMPT),
                               end("z")])
        st4 = {}
        kept_stop = mine(traces_dir=tr, stats=st4)
        check("a stored answer that wrote its own end-of-turn is kept, with that "
              "turn stripped: the renderer closes the turn anyway, so keeping "
              "the text would double-close it and dropping the row would throw "
              "away most of the store over a formatting difference",
              len(kept_stop) == 1 and st4.get("closed_by_stop_token") == 1
              and kept_stop[0].terminated
              and kept_stop[0].messages[-1]["content"] == "42",
              json.dumps(kept_stop[0].messages[-1]) if kept_stop else str(st4))
        (tr / "s_stoptok.jsonl").unlink()
        sess(tr, "s_inside", [ev("z", 0, "a" + CLOSE + "b", prompt=PROMPT),
                              end("z")])
        st_ins = {}
        check("a stop token in the MIDDLE of an answer is refused — that turn "
              "really does contain template text",
              mine(traces_dir=tr, stats=st_ins) == []
              and st_ins.get("stop_token_inside") == 1, str(st_ins))
        (tr / "s_inside.jsonl").unlink()
        sess(tr, "s_cap", [dict(ev("z", 0, "42"), completion_tokens=64,
                                max_tokens=64), end("z")])
        st_cap = {}
        check("an answer that ran out its token budget is not a demonstration",
              mine(traces_dir=tr, stats=st_cap) == []
              and st_cap.get("answer_hit_token_cap") == 1, str(st_cap))
        (tr / "s_cap.jsonl").unlink()
        sess(tr, "s_plain", [ev("z", 0, "42", prompt="plain text, no template"),
                             end("z")])
        st5 = {}
        check("an untemplated prompt yields no row and says why",
              mine(traces_dir=tr, stats=st5) == []
              and st5.get("no_roundtrip") == 1, str(st5))
    check("label: the brain's name carries the adapter on top",
          label_for("mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", "ad/x")
          == "Qwen2.5-Coder-7B-Instruct-4bit+lora:x"
          and label_for("a/b") == "b", label_for("a/b", "ad/x"))
    # ---- the training seam, argued against the INSTALLED mlx-lm (no model load)
    a = slice_args("repo/x", Path("ad/r"), 12, None)
    check("slice args are the object mlx reads them as: attributes and vars() "
          "both resolve — a dict reaches train_model and dies on args.seed",
          a.iters == 12 and vars(a)["iters"] == 12
          and a.fine_tune_type == "lora" and a.mask_prompt is True,
          type(a).__name__)
    check("a slice checkpoints exactly once, at its own end, so a kill cannot "
          "leave weights ahead of the recorded step count",
          a.save_every == a.iters, f"save_every={a.save_every}, iters={a.iters}")
    check("only a resumed slice names weights to load — a cold one starts from "
          "the base model, or the first step of a job would inherit a past run",
          a.resume_adapter_file is None
          and slice_args("repo/x", "ad/r", 12,
                         Path("ad/r/adapters.safetensors")
                         ).resume_adapter_file.endswith("adapters.safetensors"),
          str(a.resume_adapter_file))
    raised = ""
    try:
        default_slice(repo="", train_file=Path("no-such.jsonl"),
                      valid_file=Path("no-such-valid.jsonl"),
                      adapter_dir=Path("ad"), iters=1, resume_from=None)
    except ValueError as e:
        raised = str(e)
    check("an empty training file is an error, not a zero-step slice that reads "
          "as progress", "no training rows" in raised, raised or "no error raised")

    # ---- the slice loop, the early stop and the promote, against fake weights
    with tempfile.TemporaryDirectory() as d:
        ds = Path(d) / "ds"
        ds.mkdir()
        (ds / "train.jsonl").write_text(json.dumps(
            {"messages": [{"role": "user", "content": "q"},
                          {"role": "assistant", "content": "a"}]}) + "\n")
        (ds / "valid.jsonl").write_text((ds / "train.jsonl").read_text())
        ad = Path(d) / "ad"
        LOSSES = {8: 3.0, 16: 2.0, 24: 1.5, 32: 1.8, 40: 2.2, 48: 2.9}
        seen: list = []

        def fake_run(*, adapter_dir, iters, resume_from, **kw):
            n = (len(seen) + 1) * iters      # the step this slice ENDS at
            seen.append(resume_from)
            p = Path(adapter_dir)
            (p / "adapters.safetensors").write_bytes(f"w{n}".encode())
            (p / "adapter_config.json").write_text(json.dumps({"rank": 8}))
            return 1.0

        def fake_loss(*, adapter_dir, **kw):
            step = int(Path(adapter_dir).name.split("-")[1])
            return LOSSES[step]

        rec = train_lora(repo="x", dataset_dir=ds, adapter_dir=ad, steps_total=96,
                         slice_iters=8, eval_every=8, patience=2,
                         run=fake_run, loss_fn=fake_loss)
        check("the early stop fires after `patience` non-improving checkpoints, "
              "and names the step it is stopping for",
              "overfit" in rec["stopped"] and rec["iters_done"] == 40
              and rec["best_step"] == 24, f"{rec['iters_done']} {rec['stopped']}")
        check("the adapter left at the root is the best-measured checkpoint, not "
              "the last one trained — a longer run is not a better model",
              (ad / "adapters.safetensors").read_bytes() == b"w24"
              and (ad / "step-24" / "adapters.safetensors").read_bytes() == b"w24"
              and (ad / "step-40" / "adapters.safetensors").read_bytes() == b"w40",
              (ad / "adapters.safetensors").read_text(errors="ignore"))
        check("every slice after the first resumes from the weights on disk — a "
              "silently cold restart would retrain the same steps and report "
              "progress",
              seen[0] is None and all(s is not None and str(s).endswith(
                  "adapters.safetensors") for s in seen[1:]),
              f"{len(seen)} slice(s), first={seen[0]} second={seen[1]}")
        lost: list = []
        rec_b = train_lora(repo="x", dataset_dir=ds, adapter_dir=Path(d) / "ad2",
                           steps_total=96, slice_iters=8, budget_s=0.0,
                           run=lambda **kw: lost.append(kw) or 0.0,
                           loss_fn=fake_loss)
        check("a spent budget pauses at a slice boundary with zero slices run",
              rec_b["stopped"] == "budget" and rec_b["iters_done"] == 0
              and not lost, f"{rec_b['stopped']} {len(lost)} call(s)")
        bad = ""
        try:
            train_lora(repo="x", dataset_dir=ds, adapter_dir=Path(d) / "empty-ad",
                       steps_total=32, start_step=24, run=fake_run,
                       loss_fn=fake_loss)
        except ValueError as e:
            bad = str(e)
        check("resuming into a directory with no weights is refused, not trained "
              "from the base model under a claimed step count",
              "resume at step 24" in bad, bad or "no error raised")
        banked: list = []

        def warm_run(**kw):
            banked.append(kw["resume_from"])
            return fake_run(adapter_dir=ad, iters=8, resume_from=kw["resume_from"])

        rec_r = train_lora(repo="x", dataset_dir=ds, adapter_dir=ad,
                           steps_total=48, start_step=40, slice_iters=8,
                           eval_every=8, patience=2, run=warm_run,
                           loss_fn=fake_loss)
        check("a resumed job starts warm, at the step the checkpoint banked",
              banked and str(banked[0]).endswith("adapters.safetensors")
              and rec_r["iters_done"] == 48 and rec_r["start_step"] == 40,
              f"{rec_r['iters_done']} from {banked[:1]}")

    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\ntrain selftest: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
