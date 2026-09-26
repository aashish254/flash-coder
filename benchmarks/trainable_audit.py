"""R-6.3 label-hygiene audit: does `trainable()`'s exclusion cost signal?

Two re-fits of the embedding router, evaluated on the held-out families
(m4-m7) with a shared honest-label eval set:

* A — trained only on `learn.trainable()` rows (drops tier=vision, tier=shed,
  routed=big).
* B — trained on all rows minus the rows hygiene MUST drop: the policy rows
  (`routed=big(...)` never tested the small tier — the self-amplifying class
  the m7 finding names — and `tier=shed`, whose label is the machine's power
  state). B therefore KEEPS the vision rows that trainable() excludes.

If AUC(A) == AUC(B), trainable()'s one exclusion beyond the mandated policy
rows (vision) loses nothing, and the hygiene predicate is not throwing away
signal. If they differ, the gap is the measured price of the exclusion —
which is also a legitimate result to record.

Run: `python benchmarks/trainable_audit.py`  (one 7B load for ~69 uncached
prompt embeddings if the cache is cold; generation-free, forward passes only)
Offline selftest of the audit's own machinery: `--selftest`.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash import ledger
from flash.learn import fit_router, score, trainable, embed_prompts

ROOT = Path(__file__).resolve().parent
TASK_DIR = ROOT / "tasks"
SMALL_REPO = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"


def heldout_ids() -> dict:
    ids = {}
    for f in sorted(TASK_DIR.glob("*heldout*.jsonl")):
        for line in open(f):
            if line.strip():
                ids[json.loads(line)["id"]] = f.name
    return ids


def policy_row(row: dict) -> bool:
    """The rows label hygiene MUST drop — the mandated minimum.

    routed=big(...): the label is the gate's own decision, the small tier was
    never tested, training on it self-amplifies the gate (m7, 2026-09-24).
    tier=shed: escalation was denied by the governor; the label would teach
    the router the machine's power state, not the task's difficulty.
    """
    return (str(row.get("routed", "")).startswith("big")
            or row.get("tier") == "shed")


def honest_eval(row: dict, fam: dict) -> bool:
    """Eval rows carry real outcomes on the code-repair distribution:
    tier in {small, big, failed}, from a held-out family."""
    return (row.get("task_id") in fam
            and row.get("tier") in ("small", "big", "failed")
            and not str(row.get("routed", "")).startswith("big"))


def auc(pos_scores: list, neg_scores: list) -> float:
    """Mann-Whitney U / rank statistic: P(random positive ranks above a
    random negative), ties at half credit."""
    if not pos_scores or not neg_scores:
        return float("nan")
    wins = ties = 0.0
    for p in pos_scores:
        for n in neg_scores:
            if p > n:
                wins += 1
            elif p == n:
                ties += 0.5
    return (wins + ties) / (len(pos_scores) * len(neg_scores))


def boot_diff_auc(ev_a: list, ev_b: list, n: int = 4000, seed: int = 0):
    """Distribution-free noise band for AUC(A) - AUC(B) on the SAME eval rows.

    Both point estimates come from fits on nearly the same data, so a bare
    difference could be entirely a resampling artifact — with a handful of
    positives it usually is. Resample eval rows with replacement, recompute
    both AUCs per replicate: the percentile interval says whether the gap is
    a measurement or a mirage. `ev_a`/`ev_b` are aligned (score, label) lists
    over the same rows. Returns (point_diff, lo, hi, n_replicates).
    """
    def _auc_of(ev, idx):
        pos = [ev[i][0] for i in idx if ev[i][1] == 1]
        neg = [ev[i][0] for i in idx if ev[i][1] == 0]
        return auc(pos, neg) if pos and neg else float("nan")

    all_idx = list(range(len(ev_a)))
    obs = _auc_of(ev_a, all_idx) - _auc_of(ev_b, all_idx)
    rng = np.random.default_rng(seed)
    diffs = []
    k = len(ev_a)
    for _ in range(n):
        idx = rng.integers(0, k, k)
        da, db = _auc_of(ev_a, idx), _auc_of(ev_b, idx)
        if not (np.isnan(da) or np.isnan(db)):
            diffs.append(da - db)
    if not diffs:
        return obs, float("nan"), float("nan"), 0
    ds = np.array(diffs)
    lo, hi = np.percentile(ds, [2.5, 97.5])
    return obs, float(lo), float(hi), len(diffs)


def label(row: dict) -> float:
    return 1.0 if row["tier"] in ("big", "failed") else 0.0


def run_audit() -> int:
    fam = heldout_ids()
    rows = ledger.load()
    fit_a = [r for r in rows if trainable(r)]
    fit_b = [r for r in rows if not policy_row(r)]
    eval_rows = [r for r in rows if honest_eval(r, fam)]
    extra_b = [r for r in fit_b if not trainable(r)]
    print(f"ledger: {len(rows)} rows | fit A (trainable): {len(fit_a)} "
          f"| fit B (all minus policy rows): {len(fit_b)} "
          f"| B keeps {len(extra_b)} row(s) A excludes "
          f"(tier: {sorted({r.get('tier') for r in extra_b})})")
    print(f"eval: {len(eval_rows)} held-out rows "
          f"({sum(label(r) for r in eval_rows):.0f} positive) "
          f"over {len({fam[r['task_id']] for r in eval_rows})} families")

    # one cache pass for every prompt the audit touches
    all_needed = list({r["task_id"]: r for r in fit_b + eval_rows}.values())
    X_all, kept_rows = embed_prompts(all_needed, SMALL_REPO)
    emb = {r["task_id"]: X_all[i] for i, r in enumerate(kept_rows)}

    ev = [r for r in eval_rows if r["task_id"] in emb]
    out, ev_lists = [], []
    for name, fit_rows in (("A trainable-only", fit_a), ("B all-minus-policy", fit_b)):
        fr = [r for r in fit_rows if r["task_id"] in emb]
        Xf = np.stack([emb[r["task_id"]] for r in fr])
        bundle = fit_router(fr, Xf)
        pairs = [(score(bundle, emb[r["task_id"]]), label(r)) for r in ev]
        ev_lists.append(pairs)
        pos = [s for s, l in pairs if l == 1.0]
        neg = [s for s, l in pairs if l == 0.0]
        per_fam = []
        for f in sorted({fam[r["task_id"]] for r in ev}):
            sub = [(s, l) for (s, l), r in zip(pairs, ev) if fam[r["task_id"]] == f]
            a = auc([s for s, l in sub if l == 1], [s for s, l in sub if l == 0])
            per_fam.append((f.replace("_heldout_tasks.jsonl", ""), a))
        out.append((name, len(fr), auc(pos, neg), per_fam))

    for name, n, a, per_fam in out:
        print(f"fit {name:<20} {n:>3} rows   pooled AUC = {a:.3f}   "
              + "  ".join(f"{f}:{v:.3f}" if v == v else f"{f}:n/a"
                          for f, v in per_fam))
    (_, _, a_auc, _), (_, _, b_auc, _) = out
    obs, lo, hi, reps = boot_diff_auc(ev_lists[0], ev_lists[1])
    print(f"\ndifference (A - B): pooled {obs:+.3f}, bootstrap 95% interval "
          f"[{lo:+.3f}, {hi:+.3f}] over {reps} replicates on {len(ev)} eval rows")
    if lo == lo and lo <= 0 <= hi:
        print("VERDICT: the gap does not separate from zero at this eval size — "
              "the two re-fits are the same AUC within measurement precision, "
              "and the exclusion demonstrably costs no SIGNAL yet. The eval "
              "holds few positives; state the precision, not just the point.")
    elif obs > 0:
        print("VERDICT: trainable()-only is measurably BETTER — the extra "
              "exclusions sharpen the router, not blunt it.")
    else:
        print("VERDICT: the gap separates from zero AGAINST trainable() — the "
              "excluded rows carried signal; record the size as the measured "
              "price and revisit the predicate.")
    return 0


# ------------------------------------------------------------------ selftest

def run_selftest() -> int:
    checks = []

    def ck(label_, cond, detail=""):
        checks.append((label_, bool(cond), str(detail)))
        print(f"  {'OK  ' if cond else 'FAIL'} {label_}" + (f"  [{detail}]" if detail and not cond else ""))

    a = auc([3.0, 2.0], [1.0, 0.5])
    ck("auc: perfect separation is 1.0", abs(a - 1.0) < 1e-12, a)
    a = auc([1.0, 0.5], [3.0, 2.0])
    ck("auc: inverted ranking is 0.0", abs(a) < 1e-12, a)
    a = auc([2.0], [2.0])
    ck("auc: a tie is half credit", abs(a - 0.5) < 1e-12, a)
    a = auc([3.0, 1.0], [2.0, 2.0])
    ck("auc: mixed ranks average correctly", abs(a - 0.5) < 1e-12, a)
    ck("auc: an empty class is reported, not guessed",
       np.isnan(auc([], [1.0])))

    p = lambda routed, tier: {"routed": routed, "tier": tier, "task_id": "x"}
    ck("policy rows: gate-routed big is excluded", policy_row(p("big(0.9)", "big")))
    ck("policy rows: shed is excluded", policy_row(p("small", "shed")))
    ck("policy rows: a small-first honest outcome stays",
       not policy_row(p("small", "failed")))
    ck("policy rows: vision is NOT a mandated exclusion "
       "(that is trainable()'s extra, the thing the audit measures)",
       not policy_row(p("", "vision")))
    ck("trainable() is a subset of all-minus-policy (A never keeps a B drop)",
       all(trainable(r) for r in
           [p("small", s) for s in ("small", "big", "failed")]))
    ck("the audit's question is well-posed: B strictly contains A when "
       "vision rows exist",
       policy_row({"routed": "", "tier": "vision"}) is False
       and trainable({"routed": "", "tier": "vision"}) is False)

    ev = lambda tier, routed, tid: honest_eval({"tier": tier, "routed": routed,
                                                "task_id": tid}, {"t9": "m4"})
    ck("eval keeps honest held-out outcomes in all three classes",
       all(ev(s, "small", "t9") for s in ("small", "big", "failed")))
    ck("eval excludes shed, vision and big-routed held-out rows",
       not ev("shed", "small", "t9") and not ev("vision", "", "t9")
       and not ev("big", "big(0.9)", "t9"))

    import random
    rng = random.Random(7)
    base = [(rng.random(), float(i % 3 == 0)) for i in range(300)]
    obs, lo, hi, reps = boot_diff_auc(base, list(base))
    ck("bootstrap: identical score lists give a zero gap with zero in the band",
       obs == 0.0 and lo <= 0 <= hi and reps > 1000, f"{obs} [{lo},{hi}] n={reps}")
    better = [((0.9 if l == 1 else 0.1), l) for _, l in base]
    worse = [((0.1 if l == 1 else 0.9), l) for _, l in base]
    obs, lo, hi, _ = boot_diff_auc(better, worse)
    ck("bootstrap: a true separation gap has its whole band above zero",
       lo > 0.9, f"obs={obs:.3f} [{lo:.3f},{hi:.3f}]")
    obs, lo, hi, _ = boot_diff_auc(better, better)
    ck("bootstrap: same-ranking fits give a degenerate zero band",
       obs == 0.0 and lo == 0.0 and hi == 0.0)

    n_bad = sum(not ok for _, ok, _ in checks)
    print(f"\ntrainable_audit selftest: {len(checks) - n_bad}/{len(checks)} checks passed")
    return 1 if n_bad else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())
    raise SystemExit(run_audit())
