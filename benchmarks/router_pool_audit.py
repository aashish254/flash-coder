"""How much the router's train/serve pooling skew actually moved its numbers.

`flash/learn.py` has always fit the bundle on LAST-pooled hidden states
(`embed_prompts`, and `jobs.run_fit`'s embed_fn) while `flash/loop.py` scored
the live probe with `embed_text`'s DEFAULT, which is mean-pooled. Every
`route_p` in the ledger therefore came from a differently-pooled vector than
the one the logistic fit was trained on.

This measures that, and then asks the sharper question — does the bundle on
disk REPRODUCE the ledger's own recorded column, and if not, is that the skew
or a later refit? Both pools are cached by model, so the first run costs one
7B load of forward passes (no generation, no outcomes, nothing written to the
ledger) and every later run costs no load at all.

    python benchmarks/router_pool_audit.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import learn, ledger                                  # noqa: E402

REPO = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
CUTOFFS = (0.5, 0.85)
# route_p is stored rounded to 3 decimals, so an exact replay differs by <=0.0005.
TOL = 0.001


def main() -> int:
    bundle = learn.load_router()
    if bundle is None:
        print("no router bundle — run `flash router-fit --small <repo>` first")
        return 1
    cache = learn.load_cache(learn.ensure_cache_path(REPO))
    all_rows = ledger.load()
    rows = [r for r in all_rows if learn.trainable(r) and r.get("prompt")]
    prompts = [r["prompt"] for r in rows if r["prompt"] in cache]
    prompts = list(dict.fromkeys(prompts))
    if not prompts:
        print("no trainable ledger row is in the cached embeddings — nothing to "
              "compare; this audit refuses to score a pool it cannot pair")
        return 1
    print(f"{len(prompts)} prompt(s) of {len(rows)} trainable row(s) have a cached "
          f"last-pool vector; bundle dim {int(np.asarray(bundle['xmu']).shape[0])}, "
          f"labels {learn.bundle_labels(bundle)}")

    x_last = np.stack([cache[p] for p in prompts])
    mean_path = learn.ensure_cache_path(REPO, "mean")
    meancache = learn.load_cache(mean_path)
    need = [p for p in prompts if p not in meancache]
    if need:
        from mlx_lm import load
        import mlx.core as mx
        model, tok = load(REPO)
        for p in need:
            meancache[p] = learn.embed_text(model, tok, p, pool="mean")
        del model, tok
        mx.clear_cache()
        learn.save_cache(meancache, mean_path)
        print(f"computed {len(need)} mean-pooled vector(s) with forward passes "
              f"only, cached at {mean_path.name}")
    else:
        print("both pools already cached — no model load needed")
    x_mean = np.stack([meancache[p] for p in prompts])

    p_last = np.array([learn.score(bundle, v) for v in x_last])
    p_mean = np.array([learn.score(bundle, v) for v in x_mean])
    d = np.abs(p_last - p_mean)
    print(f"\nP(need-big) with the pool the fit used : median {np.median(p_last):.3f} "
          f"mean {p_last.mean():.3f}")
    print(f"P(need-big) with the old serve-time pool: median {np.median(p_mean):.3f} "
          f"mean {p_mean.mean():.3f}")
    print(f"|difference| : median {np.median(d):.3f}  mean {d.mean():.3f}  "
          f"max {d.max():.3f}")
    for cut in CUTOFFS:
        up = int(((p_mean >= cut) & (p_last < cut)).sum())
        down = int(((p_last >= cut) & (p_mean < cut)).sum())
        print(f"decisions at cutoff {cut}: {up} would have big-directed that the "
              f"fix would not, {down} the other way "
              f"(of {len(prompts)} prompt(s))")

    # Does the bundle on disk even reproduce the ledger's own route_p column? If
    # a row reproduces from one pool and not the other, the skew explains that
    # row. If it matches NEITHER pool, the row was scored by an earlier fit, and
    # a silent refit — not pooling — is what makes it unattributable. Classifying
    # every row that way keeps the audit from claiming the skew explains a column
    # a refit already overwrote.
    pos = {p: i for i, p in enumerate(prompts)}
    scored = [(pos[r["prompt"]], float(r["route_p"]), float(r.get("ts", 0)))
              for r in all_rows
              if r.get("route_p") is not None and r["prompt"] in pos]
    rec: dict[str, tuple[float, float]] = {}
    for r in all_rows:
        if r.get("route_p") is not None and r.get("prompt") in pos:
            rec[r["prompt"]] = (float(r["route_p"]), float(r.get("ts", 0)))
    hit = [i for i, p in enumerate(prompts) if p in rec]

    def repro(sub: list[int]) -> str:
        if not sub:
            return "no recorded route_p for this population"
        rr = np.array([rec[prompts[i]][0] for i in sub])
        dl = np.abs(rr - p_last[sub])
        dm = np.abs(rr - p_mean[sub])
        return (f"n={len(sub)} latest recorded route_p vs this bundle: |Δ| last-pool "
                f"median {np.median(dl):.3f} (within {TOL}: {int((dl <= TOL).sum())}"
                f"/{len(sub)}), mean-pool median {np.median(dm):.3f} "
                f"(within {TOL}: {int((dm <= TOL).sum())}/{len(sub)})")

    print(f"\n{repro(hit)}")
    buckets: dict[str, list[tuple[int, float, float]]] = {"mean": [], "last": [],
                                                          "both": [], "neither": []}
    for i, v, ts in scored:
        m, l = abs(v - float(p_mean[i])), abs(v - float(p_last[i]))
        key = ("both" if m <= TOL and l <= TOL
               else "mean" if m <= TOL else "last" if l <= TOL else "neither")
        buckets[key].append((i, v, ts))
    print(f"every ledger row whose prompt is cached, classified by which pool of "
          f"this bundle reproduces its route_p (tolerance {TOL}):")
    for key in ("mean", "last", "both", "neither"):
        b = buckets[key]
        if not b:
            print(f"  {key:<8} 0 rows")
            continue
        span = (datetime.fromtimestamp(min(t for _, _, t in b)).strftime("%m-%d")
                + "…+"
                + datetime.fromtimestamp(max(t for _, _, t in b)).strftime("%m-%d"))
        print(f"  {key:<8} {len(b):>4} row(s), recorded {span}")
    n_both = len(buckets["both"]) + len(buckets["mean"])
    print(f"  -> {n_both}/{len(scored)} cached route_p row(s) are reproduced by the "
          "MEAN pool of this bundle — the wrong pool, which is the skew this fix "
          "removes; "
          f"{len(buckets['neither'])} match neither pool, so they predate the fit "
          "on disk and pooling cannot be blamed for them")

    # Per suite, because the finding this is aimed at is a named one: PLAN's
    # m7 held-out note (2026-09-24) recorded "EVERY m7 prompt scored P>=0.83
    # and the gate big-directed 8/8 tasks the 7B one-shots", and attributed it
    # to the ledger's composition. If the m7 prompts' 0.83s are what mean
    # pooling produces under a last-pooled fit, that attribution is wrong.
    #
    # Suite comes from the task files, not the task id: an id like
    # h06_log_error_windows is carried by two suites, so it is counted in each
    # file that has it. Column totals can therefore exceed the population — that
    # is the double count, stated rather than hidden.
    id_of = {}
    for r in rows:
        id_of.setdefault(r["prompt"], str(r.get("task_id", "?")))
    by_id: dict[str, set[str]] = {}
    for f in sorted((ROOT / "benchmarks" / "tasks").glob("*.jsonl")):
        for line in f.read_text().splitlines():
            try:
                tid = json.loads(line).get("id")
            except json.JSONDecodeError:
                continue
            if tid:
                by_id.setdefault(str(tid), set()).add(f.stem)
    per_suite: dict[str, list[int]] = {}
    for i, p in enumerate(prompts):
        for s in by_id.get(id_of[p], ()):
            per_suite.setdefault(s, []).append(i)
    orphans = [i for i, p in enumerate(prompts) if not by_id.get(id_of[p])]
    print("\nper suite file  (>=0.83 counts under each pool; flips@0.5 = up/down):")
    for name in sorted(per_suite, key=lambda k: (-len(per_suite[k]), k)):
        idx = per_suite[name]
        pl, pm = p_last[idx], p_mean[idx]
        print(f"  {name:<22} n={len(idx):<4} last {np.median(pl):.3f} -> "
              f"mean {np.median(pm):.3f}   >=0.83 "
              f"{int((pl >= 0.83).sum())}->{int((pm >= 0.83).sum())}/{len(idx)}   "
              f"flips {int(((pm >= 0.5) & (pl < 0.5)).sum())}/"
              f"{int(((pl >= 0.5) & (pm < 0.5)).sum())}")
    if orphans:
        print(f"  ({len(orphans)} prompt(s) have a task_id that is in no suite file)")
    m7i = [i for i, p in enumerate(prompts)
           if "m7_heldout_tasks" in by_id.get(id_of[p], ())]
    print(f"\nm7_heldout_tasks rows in this cached population: {len(m7i)}"
          + (f" ({', '.join(id_of[prompts[i]] for i in m7i)})" if m7i else
             " — so this run CANNOT re-attribute the m7 >=0.83 finding: it "
             "contains no m7 prompt"))
    if m7i:
        vals = sorted({round(rec[prompts[i]][0], 3) for i in m7i
                       if prompts[i] in rec})
        pairs = int((p_last[m7i] >= 0.83).sum()) + int((p_mean[m7i] >= 0.83).sum())
        print(f"  m7 scored now: last-pool median {np.median(p_last[m7i]):.3f}, "
              f"mean-pool median {np.median(p_mean[m7i]):.3f}; the ledger's own "
              f"recorded route_p for those prompts: {vals}")
        print("  " + repro([i for i in m7i if prompts[i] in rec]))
        print(f"  read: >=0.83 on this bundle is {pairs}/{2 * len(m7i)} "
              "pool-prompt pairs, so the recorded 0.83s are not this bundle's "
              "output at all. The m7 note predates the current fit, and pooling "
              "cannot re-attribute it in either direction.")
    print("The gate is DISARMED at run-suite's default --threshold 1.1, so no "
          "recorded pass rate moved; what the skew produced is a wrong column in "
          "the ledger (route_p) and a wrong answer for anyone who passed "
          "--threshold 0.5.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
