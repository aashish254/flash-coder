#!/usr/bin/env python3
"""Flash Coder — M0 Model Bake-Off (PLAN.md §M0 / §22).

Measures the three MoE contenders (plus a 7B smoke test) on this machine:
  - load time, time-to-first-token (TTFT), tokens/sec, peak Metal memory
  - pass rate on 20 executable coding tasks (tests run in a subprocess)

Usage:
  python benchmarks/m0_bakeoff.py --download --models qwen3-coder-30b gpt-oss-20b
  python benchmarks/m0_bakeoff.py --run --models qwen3-coder-30b
  python benchmarks/m0_bakeoff.py --dry-run          # validate harness, no downloads
  python benchmarks/m0_bakeoff.py --report           # print table from results/

NOTE: Qwen3-VL (vision path) needs `mlx-vlm` and is benchmarked separately
(M0b) — this script covers the text/coding path via mlx-lm.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASKS_FILE = ROOT / "benchmarks" / "tasks" / "m0_tasks.jsonl"
RESULTS_DIR = ROOT / "benchmarks" / "results"

# Default contenders (M0). Names must exist under mlx-community on HF.
MODELS = {
    "qwen25-coder-7b": "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",   # smoke test
    "qwen3-coder-30b": "mlx-community/Qwen3-Coder-30B-A3B-Instruct-4bit",  # contender A
    "gpt-oss-20b": "mlx-community/gpt-oss-20b-MXFP4-Q8",                 # contender B
    "qwen3-30b-instruct": "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",  # contender C
}

CODE_FENCE = re.compile(r"```(?:python)?\s*\n(.*?)```", re.DOTALL)


def load_tasks() -> list[dict]:
    tasks = []
    with open(TASKS_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def extract_code(text: str) -> str:
    m = CODE_FENCE.search(text)
    return (m.group(1) if m else text).strip()


def run_test(code: str, test: str, timeout: int = 15) -> tuple[bool, str]:
    """Execute candidate code + asserts in an isolated subprocess."""
    prog = code + "\n\n" + test + "\nprint('__PASS__')\n"
    try:
        r = subprocess.run(
            [sys.executable, "-I", "-c", prog],
            capture_output=True, text=True, timeout=timeout,
        )
        ok = "__PASS__" in r.stdout and r.returncode == 0
        return ok, ("" if ok else (r.stderr.strip()[-400:] or "no __PASS__"))
    except subprocess.TimeoutExpired:
        return False, f"timeout>{timeout}s"


def dry_run() -> int:
    """Validate the harness: the stored reference solutions must all pass."""
    tasks = load_tasks()
    print(f"[dry-run] {len(tasks)} tasks from {TASKS_FILE.name}")
    failures = 0
    for t in tasks:
        ok, err = run_test(t["solution"], t["test"])
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"  {mark}  {t['id']}" + ("" if ok else f"  -> {err.splitlines()[-1] if err else ''}"))
    print(f"[dry-run] {len(tasks) - failures}/{len(tasks)} reference solutions pass")
    return 1 if failures else 0


def download_models(names: list[str]) -> int:
    from huggingface_hub import snapshot_download
    rc = 0
    for name in names:
        repo = MODELS[name]
        print(f"[download] {repo} ...", flush=True)
        try:
            p = snapshot_download(repo_id=repo)
            print(f"[download] OK -> {p}")
        except Exception as e:  # noqa: BLE001
            print(f"[download] FAILED {repo}: {e}")
            rc = 1
    return rc


def bench_model(name: str, max_tokens: int, max_tasks: int,
                draft: str | None = None, draft_tokens: int = 4) -> dict:
    """One model through the frozen suite. `draft` turns on speculative
    decoding (R-8.1): the draft proposes `draft_tokens` tokens, the target
    verifies them in one pass, and accepted tokens are what the stream yields —
    so tok/s here already counts accepted tokens per second, which is the only
    honest unit for the comparison.

    Never combined with the output mask: `flash.grammar`'s DFA consumes a
    token as soon as it is read, and a rejected draft would leave the contract
    ahead of the text."""
    import mlx.core as mx
    from mlx_lm import load, stream_generate

    repo = MODELS.get(name, name)
    tasks = load_tasks()[:max_tasks]
    print(f"\n=== {name} ({repo})"
          + (f" + draft {draft}" if draft else "") + " ===", flush=True)

    t0 = time.perf_counter()
    model, tokenizer = load(repo)
    draft_model = None
    if draft:
        from mlx_lm import load as _load
        draft_repo = MODELS.get(draft, draft)
        # the draft shares the target's tokenizer family only if it is the same
        # architecture; mlx checks and raises, which is the useful answer here
        draft_model, _ = _load(draft_repo)
    load_s = time.perf_counter() - t0
    print(f"  load: {load_s:.1f}s")

    ttfts, tpss, passed, details = [], [], 0, []
    for t in tasks:
        msgs = [{"role": "user", "content": t["prompt"]}]
        prompt = tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=True
        )
        reset_peak = getattr(mx, "reset_peak_memory", None) or getattr(mx.metal, "reset_peak_memory", None)
        if reset_peak:
            reset_peak()
        t0, ttft, text = time.perf_counter(), None, []
        ntok = 0
        kw = {"draft_model": draft_model, "num_draft_tokens": draft_tokens} \
            if draft_model else {}
        for resp in stream_generate(model, tokenizer, prompt=prompt,
                                    max_tokens=max_tokens, **kw):
            if ttft is None and resp.text:
                ttft = time.perf_counter() - t0
            text.append(resp.text)
            ntok += 1
        elapsed = time.perf_counter() - t0
        out = "".join(text)
        ok, err = run_test(extract_code(out), t["test"])
        passed += ok
        tps = ntok / elapsed if elapsed else 0.0
        ttfts.append((ttft or elapsed) * 1000)
        tpss.append(tps)
        details.append({"id": t["id"], "pass": ok, "ms": round(elapsed * 1000),
                        "tok_s": round(tps, 1), "err": err[:120] if not ok else ""})
        print(f"  {'PASS' if ok else 'FAIL'} {t['id']:<22} {elapsed:5.1f}s  {tps:5.1f} tok/s", flush=True)

    get_peak = getattr(mx, "get_peak_memory", None) or getattr(mx.metal, "get_peak_memory", None)
    peak_gb = (get_peak() / 1e9) if get_peak else None
    result = {
        "model": repo,
        "load_s": round(load_s, 1),
        "pass": f"{passed}/{len(tasks)}",
        "pass_rate": round(passed / len(tasks), 3),
        "ttft_ms_avg": round(sum(ttfts) / len(ttfts)),
        "tok_s_avg": round(sum(tpss) / len(tpss), 1),
        "peak_mem_gb": round(peak_gb, 1) if peak_gb else None,
        "draft": MODELS.get(draft, draft) if draft else None,
        "draft_tokens": draft_tokens if draft else None,
        "details": details,
    }
    del model
    if draft_model is not None:
        del draft_model
    mx.clear_cache()
    return result


def print_report(results: list[dict]) -> None:
    hdr = f"{'model':<44} {'pass':>6} {'ttft_ms':>8} {'tok/s':>7} {'mem_gb':>7} {'load_s':>7}"
    print("\n" + hdr + "\n" + "-" * len(hdr))
    for r in results:
        label = r["model"] + (" +draft" if r.get("draft") else "")
        print(f"{label:<44} {r['pass']:>6} {r['ttft_ms_avg']:>8} "
              f"{r['tok_s_avg']:>7} {str(r['peak_mem_gb']):>7} {r['load_s']:>7}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Flash Coder M0 bake-off")
    ap.add_argument("--models", nargs="+", default=["qwen25-coder-7b"],
                    choices=list(MODELS), help="which models to use")
    ap.add_argument("--download", action="store_true", help="download selected models")
    ap.add_argument("--run", action="store_true", help="run the benchmark")
    ap.add_argument("--dry-run", action="store_true", help="validate harness only")
    ap.add_argument("--report", action="store_true", help="print saved results table")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--max-tasks", type=int, default=20)
    ap.add_argument("--draft", default=None,
                    help="R-8.1: draft model (key from --list or a repo id) used by "
                         "the target for speculative decoding. Costs extra memory: "
                         "both models stay resident")
    ap.add_argument("--draft-tokens", type=int, default=4,
                    help="tokens proposed per verification pass")
    args = ap.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        return dry_run()
    if args.report:
        files = sorted(RESULTS_DIR.glob("m0_*.json"))
        if not files:
            print("no results yet")
            return 1
        print_report(json.loads(files[-1].read_text())["results"])
        return 0
    if args.download:
        return download_models(args.models)
    if args.run:
        results = []
        for n in args.models:
            try:
                results.append(bench_model(n, args.max_tokens, args.max_tasks,
                                        draft=args.draft,
                                        draft_tokens=args.draft_tokens))
            except Exception as e:  # noqa: BLE001 — one bad model must not kill the bake-off
                print(f"  MODEL FAILED {n}: {e}")
        if not results:
            return 1
        print_report(results)
        out = RESULTS_DIR / f"m0_{time.strftime('%Y%m%d_%H%M%S')}.json"
        out.write_text(json.dumps({"results": results}, indent=2))
        print(f"\nsaved -> {out}")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
