"""Offline gate evaluation across ALL held-out suites (m4/m5/m6/m7).

Question (PLAN App. A, 2026-09-24 m7 entry): after the label-hygiene fix
(trainable()), does ANY global P(need-big) threshold separate true positives
from false positives on unseen task families? Isotonic recalibration is
monotone, so it cannot fix ranking overlap — the decisive measurement is
leave-suite-out scoring + best-threshold analysis.

Protocol:
  - truth = honest ledger rows only (routed == 'small': small really tried)
      tier small -> negative, tier big/failed -> positive (needs escalation)
  - for each suite: fit PCA8+logistic on clean rows of all OTHER suites
    (leave-suite-out; embeddings label-free, PCA/fit never see the suite)
  - report per-suite scores, ROC-AUC, and TP/FP at several thresholds

Run: .venv/bin/python benchmarks/eval_gate_heldout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash import ledger
from flash.harness import load_tasks
from flash.learn import embed_prompts, fit_router, score, trainable

ROOT = Path(__file__).resolve().parent
SMALL = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
SUITES = ["m4_heldout_tasks.jsonl", "m5_heldout_tasks.jsonl",
          "m6_heldout_tasks.jsonl", "m7_heldout_tasks.jsonl"]


def auc(scores: list[float], labels: list[bool]) -> float:
    """Rank-based ROC-AUC (probability a random positive outranks a negative)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return float("nan")
    wins = sum(p > n for p in pos for n in neg) + 0.5 * sum(p == n for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def main() -> None:
    rows = [r for r in ledger.load() if trainable(r)]
    X, kept = embed_prompts(rows, SMALL)          # cached; embeds any misses once
    print(f"clean rows: {len(kept)} "
          f"({sum(r['tier'] != 'small' for r in kept)} positives)")

    all_sc, all_y = [], []
    for suite in SUITES:
        tasks = {t["id"]: t for t in load_tasks(ROOT / "tasks" / suite)}
        idx = [i for i, r in enumerate(kept) if r["task_id"] in tasks]
        if not idx:
            print(f"\n[{suite}] no honest ledger rows — skipped")
            continue
        tr = [i for i in range(len(kept)) if i not in set(idx)]
        bundle = fit_router([kept[i] for i in tr], X[tr])
        print(f"\n[{suite}] leave-suite-out fit n={len(tr)}")
        for i in idx:
            r = kept[i]
            p = score(bundle, X[i])
            y = r["tier"] != "small"
            all_sc.append(p)
            all_y.append(y)
            print(f"  p={p:.3f}  {'POS' if y else 'neg':<3}  {r['task_id']}")

    print(f"\n=== global over {len(all_y)} held-out rows "
          f"({sum(all_y)} positives) ===")
    print(f"ROC-AUC: {auc(all_sc, all_y):.3f}  (0.5 = useless, 1.0 = perfect)")
    print(f"score ranges: positives {min((s for s, y in zip(all_sc, all_y) if y), default=0):.3f}"
          f"-{max((s for s, y in zip(all_sc, all_y) if y), default=0):.3f}, "
          f"negatives {min((s for s, y in zip(all_sc, all_y) if not y), default=0):.3f}"
          f"-{max((s for s, y in zip(all_sc, all_y) if not y), default=0):.3f}")
    print(f"\n{'thr':>5} {'TP':>3} {'FP':>3} {'TN':>3} {'FN':>3} {'prec':>6} {'recall':>6}")
    for thr in (0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        tp = sum(y and s >= thr for s, y in zip(all_sc, all_y))
        fp = sum((not y) and s >= thr for s, y in zip(all_sc, all_y))
        tn = sum((not y) and s < thr for s, y in zip(all_sc, all_y))
        fn = sum(y and s < thr for s, y in zip(all_sc, all_y))
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        print(f"{thr:>5.1f} {tp:>3} {fp:>3} {tn:>3} {fn:>3} {prec:>6.2f} {rec:>6.2f}")


if __name__ == "__main__":
    main()
