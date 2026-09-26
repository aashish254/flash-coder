"""flash.web — Phase-4 'knowledge as a tool' (PLAN §25a3, invariants §33.9).

The web spike (benchmarks/web_spike.py, App. A 2026-09-24) proved the pieces
this module productionizes:

  * docs-in-prompt flips hallucinated-API failures (web1 blind-FAIL -> docs-PASS)
  * retrieval quality IS the product: BAAI/bge-small-en-v1.5 ranks 3/3
    load-bearing knobs at a 2500-char/doc budget; keyword grep only 2/3
    (v3 controlled A/B). bge runs via transformers+torch (both already in the
    venv) — mlx-lm 0.31.3 has no bert/modernbert backbone.
  * local-first: sha1-cached docs under benchmarks/cache/; cache hit = zero
    network (spike cache is reused, so already-fetched docs stay free)

Degradation ladder (§33.9 #4 offline-first, #7 fallbacks everywhere):
  no network      -> cache-only docs (fetch never fatal: per-doc error record)
  embedder absent -> keyword excerpt (the spike v1 ranker — worse, not dead)
  a doc fails     -> dropped with an error note; the rest still ship

Bounded resources (§33.9 #3): the 133MB embedder loads lazily, once per
process, and never at all when force_kw/--offline-without-weights applies.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "benchmarks" / "cache"
DEFAULT_EMBEDDER = "BAAI/bge-small-en-v1.5"
DEFAULT_BUDGET = 2500          # chars/doc — v3 A/B: bge needs ~2300 for the
                               # hardest knob; 2000 cut it (near-miss lesson)


# ---------------------------------------------------------------- fetch layer
def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"[ \t]*\n[ \t]*", "\n",
                  re.sub(r"[ \t]+", " ", raw)).strip()


def _cache_key(url: str, cache: Path) -> Path:
    return cache / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")


def fetch(url: str, cache: Path = CACHE, offline: bool = False,
          timeout: int = 20) -> tuple[str, bool]:
    """Local-first fetch. Returns (text, from_cache).

    Degrades per §33.9: cache hit = no network; offline or network failure
    with no cache raises RuntimeError the caller turns into a DocExcerpt
    error record — never a crash.
    """
    cache.mkdir(parents=True, exist_ok=True)
    key = _cache_key(url, cache)
    if key.exists():
        return key.read_text(), True
    if offline:
        raise RuntimeError(f"offline and no cache for {url}")
    try:
        import requests
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "flash-coder-web"})
        r.raise_for_status()
    except Exception as e:                       # network down, DNS, TLS...
        raise RuntimeError(f"fetch failed and no cache for {url}: {e}") from e
    text = strip_html(r.text) if "<html" in r.text[:2000].lower() else r.text
    key.write_text(text)
    return text, False


# --------------------------------------------------------------- rank layers
def chunk(text: str, target: int = 800) -> list[str]:
    """Paragraph-ish chunks of ~target chars: merge small lines, hard-split
    oversized paragraphs. Deterministic — retrieval quality must be measurable
    independently of generation stochasticity (spike design rule)."""
    chunks, cur = [], ""
    for p in re.split(r"\n\s*\n|\n", text):
        p = p.strip()
        if not p:
            continue
        if len(cur) + len(p) + 1 <= target:
            cur = (cur + "\n" + p).strip()
        else:
            if cur:
                chunks.append(cur)
            while len(p) > target:
                chunks.append(p[:target])
                p = p[target:]
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def excerpt_kw(text: str, query: str, budget: int = DEFAULT_BUDGET) -> str:
    """Keyword-relevance excerpt (spike v1 ranker): doc start + lines sharing
    long lowercase terms with the query. The no-embedder fallback — 2/3 knobs
    vs bge's 3/3, so it ships only when bge can't load."""
    terms = {w.lower().strip("()`:.",) for w in query.split()
             if len(w) > 5 and w[0].islower()}
    hits = [ln for ln in text.splitlines()
            if terms & {w.lower().strip("()`:.,'") for w in ln.split()}]
    extra = "\n".join(hits[:40])
    return (text[:budget] + "\n...\n" + extra)[: budget * 2]


class TorchEmbedder:
    """Dedicated retrieval embedder via transformers+torch — both already in
    the venv (torch came with misaki, transformers with mlx-lm): zero new
    deps. Attention-masked mean-pool + L2 normalize. mlx-lm 0.31.3 has no
    bert/modernbert backbone, hence torch for encoder-class embedders."""

    def __init__(self, repo: str, local_only: bool = False):
        from transformers import AutoModel, AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(
            repo, local_files_only=local_only)
        self.model = AutoModel.from_pretrained(
            repo, local_files_only=local_only).eval()

    def embed(self, text: str):
        import torch
        enc = self.tok(text, truncation=True, max_length=512,
                       return_tensors="pt")
        with torch.no_grad():
            h = self.model(**enc).last_hidden_state[0]
        mask = enc["attention_mask"][0].unsqueeze(-1)
        v = (h * mask).sum(0) / mask.sum().clamp(min=1)
        return torch.nn.functional.normalize(v, p=2, dim=0).numpy()


_EMBEDDER: TorchEmbedder | None = None
_EMBEDDER_TRIED = False


def get_embedder(repo: str = DEFAULT_EMBEDDER,
                 offline: bool = False) -> TorchEmbedder | None:
    """Lazy process-wide singleton (load once — the M2 hot-swap lesson).
    Returns None on ANY failure so callers degrade to excerpt_kw: missing
    deps, uncached weights while offline, OOM. A tool that sometimes ranks
    worse beats a tool that sometimes crashes."""
    global _EMBEDDER, _EMBEDDER_TRIED
    if _EMBEDDER_TRIED:
        return _EMBEDDER
    _EMBEDDER_TRIED = True
    try:
        _EMBEDDER = TorchEmbedder(repo, local_only=offline)
    except Exception:
        _EMBEDDER = None
    return _EMBEDDER


def _prefixes(name: str) -> tuple[str, str]:
    """(query, document) prefixes by embedder family convention."""
    if "nomic" in name:
        return "search_query: ", "search_document: "
    if "bge" in name.lower():
        return ("Represent this sentence for searching relevant passages: ",
                "")
    return "", ""


def excerpt_embed(text: str, query: str, embed_fn,
                  budget: int = DEFAULT_BUDGET,
                  qpfx: str = "", dpfx: str = "") -> tuple[str, float]:
    """Cosine-rank ~800-char chunks against the query, fill the budget
    best-first (skipping oversized chunks — smaller ones may still fit), then
    restore doc order for readability. Returns (excerpt, top1_sim)."""
    import numpy as np
    chunks = chunk(text)
    if not chunks:
        return "", 0.0
    q = embed_fn(qpfx + query)
    X = np.stack([embed_fn(dpfx + c) for c in chunks])
    sims = (X @ q) / (np.linalg.norm(X, axis=1) * np.linalg.norm(q) + 1e-9)
    picked, used = [], 0
    for i in np.argsort(-sims):
        if used + len(chunks[i]) <= budget:
            picked.append(int(i))
            used += len(chunks[i])
    picked.sort()
    return "\n...\n".join(chunks[i] for i in picked), float(sims.max())


# ------------------------------------------------------------------- lookup
@dataclass
class DocExcerpt:
    url: str
    text: str = ""
    ranker: str = "none"       # "bge" | "kw" | "none"
    top1_sim: float = 0.0
    cached: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error


def lookup(query: str, urls: list[str], budget: int = DEFAULT_BUDGET,
           offline: bool = False, force_kw: bool = False,
           cache: Path = CACHE) -> list[DocExcerpt]:
    """fetch -> chunk -> rank per doc; per-doc failures degrade, never kill."""
    emb = None if force_kw else get_embedder(offline=offline)
    qpfx, dpfx = _prefixes(DEFAULT_EMBEDDER) if emb else ("", "")
    out: list[DocExcerpt] = []
    for url in urls:
        rec = DocExcerpt(url=url)
        try:
            text, rec.cached = fetch(url, cache=cache, offline=offline)
            if emb is not None:
                rec.text, rec.top1_sim = excerpt_embed(
                    text, query, emb.embed, budget, qpfx, dpfx)
                rec.ranker = "bge"
            else:
                rec.text = excerpt_kw(text, query, budget)
                rec.ranker = "kw"
        except Exception as e:
            rec.error = str(e)
        out.append(rec)
    return out


def format_for_prompt(excerpts: list[DocExcerpt]) -> str:
    """Prompt-injection block. Under-use-of-context lesson (App. A: web2's
    knob was IN CONTEXT and still unused): the header states these excerpts
    are ground truth and names the failure mode to avoid."""
    parts = []
    for e in excerpts:
        if e.ok:
            parts.append(f"--- {e.url} ---\n{e.text}")
    if not parts:
        return ""
    return ("Library documentation (fetched excerpts — this is ground truth "
            "for these APIs: use the exact function names and keyword "
            "arguments shown below, even if you think you know them):\n\n"
            + "\n\n".join(parts))


# ------------------------------------------------------------------ selftest
def selftest() -> list[tuple[str, bool, str]]:
    """Fully offline deterministic checks — no network, no model weights.
    Repo convention: self-checks print and set an exit code (decide-batch)."""
    import tempfile

    checks: list[tuple[str, bool, str]] = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))

    # strip_html: boilerplate gone, entities unescaped, text kept
    t = strip_html("<html><head><style>x{}</style></head><body>"
                   "<script>evil()</script><p>Hello &amp; goodbye</p></body>")
    check("strip_html", "Hello & goodbye" in t and "evil" not in t
          and "<p>" not in t, t[:40])

    # chunk: all chunks <= target or a hard-split of an oversized paragraph
    doc = "\n\n".join(f"para {i} " + "x" * 100 for i in range(10))
    cs = chunk(doc, target=300)
    check("chunk", cs and all(len(c) <= 300 for c in cs)
          and "para 0" in cs[0] and "para 9" in "".join(cs),
          f"{len(cs)} chunks")

    # fetch: cache round-trip, offline hit, offline miss -> RuntimeError
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td)
        _cache_key("u://a", cp).write_text("cached-doc")
        txt, hit = fetch("u://a", cache=cp, offline=True)
        check("fetch-cache-hit", hit and txt == "cached-doc")
        try:
            fetch("u://missing", cache=cp, offline=True)
            check("fetch-offline-miss", False, "no raise")
        except RuntimeError:
            check("fetch-offline-miss", True)

    # excerpt_embed with a STUB embedder: knob containment, budget fill,
    # doc-order restore — the retrieval contract, deterministic
    import numpy as np
    chunks_doc = "\n\n".join(["filler " + "f" * 700,
                              "KNOB the answer " + "k" * 680,
                              "tail " + "t" * 700])

    def stub(s):                            # knob+query aligned, rest not
        v = np.full(16, 1.0) if ("KNOB" in s or s == "query") \
            else np.eye(16)[0]
        return v / np.linalg.norm(v)

    ex, sim = excerpt_embed(chunks_doc, "query", stub, budget=800)
    check("embed-rank-knob", "KNOB" in ex and len(ex) <= 800,
          f"{len(ex)}c sim={sim:.2f}")
    ex2, _ = excerpt_embed(chunks_doc, "query", stub, budget=1600)
    check("embed-rank-order", ex2.find("filler") < ex2.find("KNOB")
          or "filler" not in ex2, "doc order restored")

    # kw fallback surfaces a matching line past the doc-start budget
    knob_line = "strict : bool — type coercion rules apply"
    far_doc = "intro " + "i" * 6000 + "\n" + knob_line + "\nend"
    check("kw-fallback", knob_line in excerpt_kw(far_doc,
                                                 "convert strict coercion"))

    # lookup end-to-end offline: cached doc ranks, missing doc errors not fatal
    with tempfile.TemporaryDirectory() as td:
        cp = Path(td)
        _cache_key("u://doc", cp).write_text(far_doc)
        recs = lookup("strict coercion", ["u://doc", "u://gone"],
                      offline=True, force_kw=True, cache=cp)
        check("lookup-degrades", len(recs) == 2 and recs[0].ok
              and recs[0].ranker == "kw" and not recs[1].ok and recs[1].error)
        block = format_for_prompt(recs)
        check("format-skips-errors", "u://doc" in block and "u://gone"
              not in block and "ground truth" in block)

    return checks


def run_selftest() -> int:
    checks = selftest()
    for name, ok, detail in checks:
        print(f"  {'OK ' if ok else 'FAIL'} {name:<22} {detail}")
    n_bad = sum(not ok for _, ok, _ in checks)
    print(f"\nweb selftest: {len(checks) - n_bad}/{len(checks)} checks pass")
    return 1 if n_bad else 0


if __name__ == "__main__":                       # pragma: no cover
    import sys
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        raise SystemExit(run_selftest())
    print(__doc__)

