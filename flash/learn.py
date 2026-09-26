"""Learned router v0 (PLAN §M3): fit P(needs-big) from the outcome ledger.

Prospective self-assessment is provably overconfident (App. A, 2026-09-23),
so routing signal must come from *outcomes*. This fits a tiny L2-regularized
logistic regression on route-time-available features (pure numpy — no new
deps) and evaluates with leave-one-out CV. With few positives this is a
pipeline proof, not a deployable model; the ledger makes n grow for free.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import numpy as np


def features(row: dict) -> list[float]:
    """Route-time-available features only (no outcome leakage)."""
    p = row.get("prompt", "")
    words = p.split()
    return [
        np.log1p(len(p)),                              # prompt chars
        np.log1p(len(words)),                          # prompt words
        sum(c.isdigit() for c in p) / max(len(p), 1),  # digit density
        sum(c in ":'{}[]()" for c in p) / max(len(p), 1),  # structure density
        len(re.findall(r"`[^`]+`", p)),                # inline-code spans
        float(bool(row.get("ctx"))),                   # repo context attached
    ]


EMB_CACHE = Path(__file__).resolve().parent.parent / "benchmarks" / "results" / "prompt_embeddings_last.npz"


def trainable(row: dict) -> bool:
    """Honest outcome labels only.

    - tier="vision" rows are a different task distribution (screenshot
      replication, not code repair) and would pollute the small/big router.
    - tier="shed" rows (§34.1 governor) never got their escalation: the small
      tier failed and the brain stayed unloaded for thermal/battery reasons.
      Labelling those 'needs-big' would teach the router the machine's power
      state, not the task's difficulty.
    - routed="big(...)" rows never tested the small tier: their 'big' label
      is the gate's own decision, not an outcome. Training on them
      self-amplifies the gate (m7 held-out finding, 2026-09-24: after the
      big-heavy mw/label-noise window, EVERY m7 prompt scored P>=0.83 and
      the gate big-directed 8/8 tasks the 7B one-shots).
    """
    return (row.get("tier") not in ("vision", "shed")
            and not str(row.get("routed", "")).startswith("big"))

def embed_text(model, tok, text: str, max_len: int = 512, pool: str = "mean") -> np.ndarray:
    """Single-prompt embedding: pooled last hidden state ('mean' or 'last' token)."""
    import mlx.core as mx
    ids = mx.array(tok.encode(text)[:max_len], dtype=mx.int32)[None]
    h = model.model(ids)[0]
    v = mx.mean(h, axis=0) if pool == "mean" else h[-1]
    return np.array(v.astype(mx.float32))




def load_cache(path: Path = EMB_CACHE) -> dict:
    if Path(path).exists():
        z = np.load(path)
        return {k: z[k] for k in z.files}
    return {}


def save_cache(cache: dict, path: Path = EMB_CACHE) -> None:
    """Atomic flush: a kill mid-write must not corrupt the shared embedding
    cache (the ledger rule applies to artifacts too — §33.9 invariant 1).

    Written through a file handle on purpose: np.savez appends '.npz' to a
    *path* argument, which silently breaks the tmp+rename atomic swap.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez(fh, **cache)
    os.replace(tmp, path)


def embed_backfill(prompts: list[str], embed_fn, cache_path: Path = EMB_CACHE,
                   chunk: int = 8, budget_s: float | None = None,
                   clock=time.monotonic, on_progress=None) -> tuple[dict, list[str]]:
    """Embed the missing prompts in CHUNKS, flushing the cache after each one.

    §34.3's acceptance test is 'resume verified by killing a run mid-flight':
    chunking + a checkpoint after every chunk means an interruption costs at
    most `chunk` prompts, and `budget_s` lets a background job stop politely
    when its window closes and finish on the next idle spell.

    Returns (cache, still_missing). Pure w.r.t. embed_fn — testable offline.
    At least ONE chunk always runs, so every call makes measurable progress
    even when the budget is already spent.
    """
    cache = load_cache(cache_path)
    missing = [p for p in dict.fromkeys(prompts) if p not in cache]
    t0 = clock()
    for i in range(0, len(missing), chunk):
        for p in missing[i:i + chunk]:
            cache[p] = embed_fn(p)
        save_cache(cache, cache_path)
        if on_progress:
            on_progress(min(i + chunk, len(missing)), len(missing))
        if budget_s is not None and clock() - t0 >= budget_s:
            return cache, missing[i + chunk:]
    return cache, []


def embed_prompts(rows: list[dict], small_repo: str, max_len: int = 512,
                  budget_s: float | None = None):
    """Pooled last-hidden-state embeddings from the small tier, cached by prompt.

    Pre-prompt-telemetry ledger rows get their prompt backfilled from
    benchmarks/tasks/*.jsonl by task_id. Returns (X, kept_rows).
    """
    from mlx_lm import load
    import mlx.core as mx

    task_dir = Path(__file__).resolve().parent.parent / "benchmarks" / "tasks"
    id2prompt = {}
    for f in sorted(task_dir.glob("*.jsonl")):
        with open(f) as fh:
            for line in fh:
                if line.strip():
                    d = json.loads(line)
                    id2prompt.setdefault(d["id"], d["prompt"])
    kept = []
    for r in rows:
        p = r.get("prompt") or id2prompt.get(r.get("task_id"), "")
        if p:
            r["prompt"] = p
            kept.append(r)

    prompts = [r["prompt"] for r in kept]
    already = load_cache()
    need = [p for p in dict.fromkeys(prompts) if p not in already]
    if need:
        model, tok = load(small_repo)         # load once, reuse for every prompt

        def embed_fn(p):
            return embed_text(model, tok, p, max_len, pool="last")

        cache, remaining = embed_backfill(prompts, embed_fn, budget_s=budget_s)
        del model, tok
        mx.clear_cache()
        if remaining:
            raise RuntimeError(f"embedding budget spent: {len(remaining)} prompt(s) "
                               f"still unembedded — rerun to resume")
    return np.stack([load_cache()[p] for p in prompts]), kept


def pca(X: np.ndarray, k: int) -> np.ndarray:
    """n is tiny; compress embeddings to k orthogonal components before fitting."""
    Xc = X - X.mean(0)
    _, _, vt = np.linalg.svd(Xc, full_matrices=False)
    return Xc @ vt[:k].T


def fit_matrix(X: np.ndarray, y: np.ndarray, l2: float = 1.0,
               steps: int = 3000, lr: float = 0.1):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.zeros(Z.shape[1] + 1)                       # + bias
    Zw = np.hstack([Z, np.ones((len(Z), 1))])
    for _ in range(steps):
        p = 1 / (1 + np.exp(-Zw @ w))
        g = Zw.T @ (p - y) / len(y) + l2 * np.r_[w[:-1], 0] / len(y)
        w -= lr * g
    return w, mu, sd


def predict_vec(w, mu, sd, x: np.ndarray) -> float:
    z = (x - mu) / sd
    return float(1 / (1 + np.exp(-(np.r_[z, 1.0] @ w))))


def loo_report(X: np.ndarray, rows: list[dict], title: str) -> str:
    """Leave-one-out: does P(needs-big) rank the truly-escalated tasks highest?"""
    y = np.array([1.0 if r["tier"] != "small" else 0.0 for r in rows])
    scores = []
    for i in range(len(rows)):
        tr = [j for j in range(len(rows)) if j != i]
        if len(set(y[tr])) < 2:
            continue                                   # need both classes
        w, mu, sd = fit_matrix(X[tr], y[tr])
        scores.append((rows[i]["task_id"], predict_vec(w, mu, sd, X[i]), bool(y[i])))
    scores.sort(key=lambda s: -s[1])
    n_pos = sum(s[2] for s in scores)
    lines = [f"[{title}] leave-one-out over {len(scores)} runs ({n_pos} positives):",
             f"{'task':<22} {'P(need-big)':<12} truth"]
    for tid, p, truth in scores[:8]:
        lines.append(f"{tid:<22} {p:<12.3f} {'ESC/FAIL' if truth else ''}")
    k = min(3, len(scores))
    lines.append(f"precision@{k} = {sum(s[2] for s in scores[:k])}/{k}   "
                 f"precision@{n_pos} = {sum(s[2] for s in scores[:n_pos])}/{n_pos}")
    return "\n".join(lines)


ROUTER_FILE = Path(__file__).resolve().parent.parent / "benchmarks" / "results" / "router.npz"


def fit_router(rows: list[dict], X: np.ndarray, k: int = 8) -> dict:
    """Full fit on all rows: PCA(k) + logistic regression. Returns the bundle."""
    y = np.array([1.0 if r["tier"] != "small" else 0.0 for r in rows])
    xmu = X.mean(0)
    _, _, vt = np.linalg.svd(X - xmu, full_matrices=False)
    Xp = (X - xmu) @ vt[:k].T
    w, mu, sd = fit_matrix(Xp, y)
    return {"w": w, "mu": mu, "sd": sd, "vt": vt[:k], "xmu": xmu}


def save_router(bundle: dict, path: Path = ROUTER_FILE) -> None:
    """Atomic swap: the live gate reads router.npz mid-run, never a half file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez(fh, **bundle)
    os.replace(tmp, path)


def load_router(path: Path = ROUTER_FILE):
    if not path.exists():
        return None
    return {k: v for k, v in np.load(path).items()}


def score(bundle: dict, emb: np.ndarray) -> float:
    """P(needs-big) for one embedding."""
    xp = (emb - bundle["xmu"]) @ bundle["vt"].T
    z = (xp - bundle["mu"]) / bundle["sd"]
    return float(1 / (1 + np.exp(-(np.r_[z, 1.0] @ bundle["w"]))))



def autofit_if_stale(small_repo: str, min_new: int = 10) -> str | None:
    """Refit the router when the ledger has >= min_new rows since the last fit.

    Self-improvement hook: run-suite calls this so routing keeps learning from
    fresh outcomes with no manual step. Returns a status line, or None if fresh.
    """
    from flash import ledger
    # Staleness counts all router-eligible rows (any activity), but the fit uses
    # only trainable() rows — honest labels, see trainable() docstring.
    rows = [r for r in ledger.load() if r.get("tier") not in ("vision", "shed")]
    bundle = load_router()
    trained_n = int(bundle["n_rows"]) if bundle is not None and "n_rows" in bundle else 0
    if len(rows) - trained_n < min_new:
        return None
    fit_rows = [r for r in rows if trainable(r)]
    X, kept = embed_prompts(fit_rows, small_repo)
    save_router({**fit_router(kept, X), "n_rows": np.array(len(rows))})
    return (f"[autofit] router refit on {len(kept)} labeled outcomes "
            f"(+{len(rows) - trained_n} new ledger rows)")
