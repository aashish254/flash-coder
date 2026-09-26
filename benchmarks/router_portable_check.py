"""The learned router is not model-portable — OFFLINE proof, no model loaded.

Why this vector exists: a live arm that only changed the tier
(`run-suite --small …-1.5B-4bit`) died inside `learn.score()` with
`operands could not be broadcast together with shapes (1536,) (3584,)`. Three
defects were behind that one traceback:

  the router bundle held one model's PCA basis and said nothing about which;
  the embedding cache was keyed by prompt text ALONE, so a second model reads
    the first model's vectors and gets plausible numbers of the wrong width;
  the serve path pooled its probe `mean` while both fit sites pooled `last`,
    which scores every prompt against a differently-shaped vector than the one
    it was trained on and raises nothing at all.

The contract these checks pin: a cache path names the model that produced the
vectors; a bundle records its fit model and its pooling and reads them back;
`route_score` refuses a foreign-dim embedding as a value plus a reason, so the
loop degrades to the static route instead of losing the suite; `load_cache`
cannot be called without a path, so the shared file cannot come back by
accident; and the probe is pooled the way the bundle was fit.

    python benchmarks/router_portable_check.py
"""
from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flash import learn                                        # noqa: E402

BIG = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
SMALL = "mlx-community/Qwen2.5-Coder-1.5B-4bit"
DIM, OTHER_DIM, K = 12, 8, 3


def rows(n: int = 8) -> list[dict]:
    """Honest ledger rows: tier small/failed, nothing big-routed (trainable())."""
    return [{"task_id": f"r{i}", "prompt": f"prompt number {i} with words",
             "tier": "small" if i % 3 else "failed", "ctx": False}
            for i in range(n)]


def bundle(small_repo: str = BIG, pool: str = learn.POOL, dim: int = DIM) -> dict:
    X = np.stack([np.arange(dim, dtype=float) + i for i in range(8)])
    return learn.fit_router(rows(len(X)), X, k=K, small_repo=small_repo, pool=pool)


def cache_checks(root: Path) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def ck(name, cond, detail=""):
        out.append((name, bool(cond), detail))

    p_big = learn.emb_cache_path(BIG)
    p_small = learn.emb_cache_path(SMALL)
    ck("the cache path names the model that produced the vectors",
       p_big != p_small and BIG.split("/")[-1] in p_big.name
       and SMALL.split("/")[-1] in p_small.name,
       f"{p_big.name} vs {p_small.name}")
    ck("pooling is part of the name, so a re-pooled fit cannot reuse vectors",
       learn.emb_cache_path(BIG, "mean") != p_big, "")

    # the pre-keying artifact is adopted exactly once, and only by its own model
    legacy = root / "prompt_embeddings_last.npz"
    legacy.write_bytes(b"x")
    learn.RESULTS, learn.LEGACY_EMB_CACHE = root, legacy
    got = learn.ensure_cache_path(BIG)
    ck("the model the legacy file was made with adopts it by renaming it",
       got == root / p_big.name and got.exists() and not legacy.exists(),
       f"{got.name}")
    other = learn.ensure_cache_path(SMALL)
    ck("a different model gets its OWN path and takes nothing",
       other == root / p_small.name and not other.exists(),
       "the file is created by the first write, not by guessing it exists")
    ck("adoption is idempotent: a second call cannot lose the adopted file",
       learn.ensure_cache_path(BIG) == root / p_big.name
       and (root / p_big.name).exists(), "")
    p = root / p_big.name
    learn.save_cache({"prompt number 0 with words": np.ones(DIM)}, p)
    learn.save_cache({}, other)
    ck("a second model starts with an EMPTY cache, not the first model's vectors",
       learn.load_cache(other) == {} and len(learn.load_cache(p)) == 1,
       f"{p.name} holds 1 vector, {other.name} holds 0")
    return out


def score_checks() -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def ck(name, cond, detail=""):
        out.append((name, bool(cond), detail))

    b = bundle()
    ok, why = learn.route_score(b, np.zeros(DIM))
    ck("a same-dim probe scores", ok is not None and why == "",
       f"P(need-big)={ok:.3f}" if ok is not None else "None")
    bad, why = learn.route_score(b, np.zeros(OTHER_DIM))
    ck("a foreign-dim probe refuses as a VALUE, not an exception",
       bad is None and why != "", str(why))
    ck("the refusal names both widths and the model the fit came from",
       why.count("12") and why.count("8") and BIG.split("/")[-1] in why, "")
    ck("the bundle records the model it was fit for",
       learn.bundle_labels(b) == (BIG, learn.POOL), str(learn.bundle_labels(b)))
    unlabeled = {k: v for k, v in b.items() if k not in ("small_repo", "pool")}
    ck("a bundle predating the labels still gets the dimension check",
       learn.route_score(unlabeled, np.zeros(OTHER_DIM))[0] is None, "")
    ck("its pooling is read as the one the fit sites have always used",
       learn.probe_pool(unlabeled) == "last"
       and learn.probe_pool(bundle(pool="mean")) == "mean",
       f"unlabeled -> {learn.probe_pool(unlabeled)}, labeled-mean -> "
       f"{learn.probe_pool(bundle(pool='mean'))}")
    ck("load_cache refuses to guess a path", _raises(TypeError, learn.load_cache),
       "the shared prompt-keyed file must not be reachable by default")
    ck("embed_backfill refuses to guess a path",
       _raises(TypeError, lambda: learn.embed_backfill(["p"], lambda p: p)),
       "")
    src = (ROOT / "flash" / "loop.py").read_text()
    ck("the loop asks the bundle how it was pooled",
       "probe_pool(router)" in src, "")
    ck("and carries that answer into the embedding", "pool=pool" in src, "")
    ck("no serve-time embedding is left with the caller's default pool",
       not re.search(r"embed_text\(model, tok, task\[.prompt.\]\)", src),
       "the old call site pooled 'mean' under a 'last' fit")
    return out


def _raises(exc, fn) -> bool:
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def roundtrip_checks(root: Path) -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []

    def ck(name, cond, detail=""):
        out.append((name, bool(cond), detail))

    path = root / "router.npz"
    b = bundle()
    learn.save_router({**b, "n_rows": np.array(8)}, path)
    back = learn.load_router(path)
    ck("the labels survive the npz round trip",
       learn.bundle_labels(back) == (BIG, learn.POOL), str(learn.bundle_labels(back)))
    ck("a reloaded bundle still scores the same probe identically",
       abs(learn.score(back, np.zeros(DIM)) - learn.score(b, np.zeros(DIM))) < 1e-12,
       "")
    ck("an atomic swap leaves no .tmp beside the live bundle",
       list(root.glob("router.npz.tmp*")) == [] and path.exists(), "")
    return out


MUTATIONS = {
    # label -> the shipped function replaced by the shape it had before
    "cache ignores the model": ("emb_cache_path",
                                lambda repo, pool=learn.POOL: learn.RESULTS / "prompt_embeddings_last.npz"),
    "bundle keeps no labels": ("fit_router",
                               lambda r, X, k=8, small_repo="", pool=learn.POOL: {
                                   kk: v for kk, v in learn._orig_fit(r, X, k, small_repo, pool).items()
                                   if kk not in ("small_repo", "pool")}),
    "score trusts any width": ("route_score",
                               lambda b, emb: (learn.score(b, np.r_[emb, np.zeros(max(0, DIM - len(emb)))][:DIM]), "")),
    "serve pools mean": ("probe_pool", lambda b: "mean"),
    "cache path optional again": ("load_cache", lambda path=None: {}),
}


def _expected(label: str, fails: list[str]) -> bool:
    """Did THIS mutation break the check that exists to catch it?

    A mutant that breaks other checks too is still caught; a mutant that
    breaks none of them means the check is testing something else.
    """
    want = {
        "cache ignores the model": "names the model that produced the vectors",
        "bundle keeps no labels": "records the model it was fit for",
        "score trusts any width": "refuses as a VALUE",
        "serve pools mean": "read as the one the fit sites have always used",
        "cache path optional again": "refuses to guess a path",
    }[label]
    return any(want in f for f in fails)


def mutate() -> tuple[int, list[str]]:
    """Put each bug back and require that its own checks fail. A mutant that
    passes is a check that is not testing the bug it names."""
    notes, defeated = [], 0
    orig = {name: getattr(learn, name) for name in
            ("emb_cache_path", "fit_router", "route_score", "probe_pool", "load_cache")}
    learn._orig_fit = orig["fit_router"]
    for label, (attr, fn) in MUTATIONS.items():
        setattr(learn, attr, fn)
        try:
            with tempfile.TemporaryDirectory() as d:
                fails = [nm for nm, ok, _ in score_checks() if not ok]
                fails += [nm for nm, ok, _ in cache_checks(Path(d)) if not ok]
        finally:
            for name, fn in orig.items():
                setattr(learn, name, fn)
        hit = _expected(label, fails)
        defeated += hit
        notes.append(f"{'ok  ' if hit else 'MISS'} MUTATION: {label} -> "
                     f"{len(fails)} check(s) fail"
                     + ("" if hit else f", none of them the one that catches it: {fails[:2]}"))
    return defeated, notes


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        saved = (learn.RESULTS, learn.LEGACY_EMB_CACHE)
        all_checks = cache_checks(root) + score_checks() + roundtrip_checks(root)
        learn.RESULTS, learn.LEGACY_EMB_CACHE = saved
    width = max(len(n) for n, _, _ in all_checks)
    fails = [n for n, c, _ in all_checks if not c]
    for name, cond, note in all_checks:
        print(f"  {'ok  ' if cond else 'FAIL'} {name:<{width}}"
              + (f"  [{note}]" if note else ""))
    print(f"\nrouter portability: {len(all_checks) - len(fails)}/{len(all_checks)} "
          f"checks passed")
    if fails:
        return 1
    defeated, notes = mutate()
    for line in notes:
        print("  " + line)
    print(f"\nmutations: {defeated}/{len(MUTATIONS)} gates defeated by exactly "
          f"their checks")
    return 0 if defeated == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
