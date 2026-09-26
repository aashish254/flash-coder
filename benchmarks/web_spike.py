"""Web-tool spike (PLAN.md §25 action 3, §12.1): does doc lookup close the
knowledge gap for a local 7B coder?

Thesis under test: "general knowledge as a tool, not as weights" — a local
model need not memorize every library if it can FETCH the docs. This spike
A/B-tests that claim: the same 7B (mlx-community/Qwen2.5-Coder-7B-Instruct-
4bit) attempts niche-API tasks twice — blind (no docs) vs informed (fetched
doc excerpt injected into the prompt). Pass/fail is EXECUTION-scored.

Design:
  fetch(url) -> strip HTML to text -> cache under benchmarks/cache/ (sha1 of
  url) -> excerpt -> inject into prompt. Local-first: cache hit = zero
  network; the tool degrades to cache-only offline (§33.9).

  v2: TWO excerpters A/B'd per task —
    docs-kw:  first ~2000 chars + keyword-relevance lines (v1, cheap)
    docs-emb: ~800-char chunks cosine-ranked against the prompt using the
              RESIDENT model's pooled hidden state (flash.learn.embed_text —
              zero extra model load), budget 2000 chars
  Retrieval quality is scored DETERMINISTICALLY per task: does the excerpt
  contain the load-bearing knob (web1 train_dictionary / web2 coercion /
  web3 read=)? Generation is stochastic; knob-containment is not.

  v3: --embedder BAAI/bge-small-en-v1.5 swaps the ranker's features from
      resident-7B hidden states (v2 finding: weak — compressed cosine range,
      near-miss budget cuts) to a purpose-built retrieval embedder. Runs via
      transformers+torch (both already in the venv) because mlx-lm 0.31.3 has
      no bert/modernbert backbone; task prefixes per embedder family.
      --knob-only runs just the deterministic metric — a fast ranker A/B
      with no generation.

Task choice: APIs that are real, pip-installable, and hallucination-prone
(exact kwarg names / call signatures a model guesses wrong without docs):
  web1 zstandard  — dictionary training: train_dictionary + ZstdCompressionDict
  web2 msgspec    — Struct options + msgspec.convert(from_attributes=...)
  web3 sqlglot    — MySQL "LIMIT a, b" -> DuckDB "LIMIT b OFFSET a" transpile

Honesty notes: n=3 tasks is a SPIKE, not a study — it validates the mechanism
(docs-in-prompt change outcomes), not the effect size. Also the 7B may know
some of these from training; a task the model passes blind is uninformative,
not a counterexample.

Run: .venv/bin/python benchmarks/web_spike.py --fetch-only   # no GPU
     .venv/bin/python benchmarks/web_spike.py                # full A/B
"""

import argparse
import hashlib
import html
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CACHE = Path(__file__).parent / "cache"
VLM_FREE_CHECK = "run-vis-suite"   # pgrep target: don't contend for the GPU

TASKS = [
    {
        "id": "web1_zstd_dict",
        "pip": "zstandard",
        # load-bearing doc line: retrieval quality is measured by whether the
        # excerpt contains this knob (deterministic — generation is not).
        "knob": "train_dictionary",
        "doc_urls": [
            "https://python-zstandard.readthedocs.io/en/latest/dictionaries.html",
        ],
        "prompt": (
            "Write a Python function `roundtrip(samples: list[bytes], "
            "payload: bytes) -> bytes` using the `zstandard` library that: "
            "trains a 4096-byte compression dictionary from `samples`, "
            "compresses `payload` with a ZstdCompressor USING that "
            "dictionary, then decompresses with a ZstdDecompressor using the "
            "same dictionary and returns the decompressed bytes. "
            "Return ONLY one fenced python code block."),
        "test": (
            "import zstandard\n"
            "{code}\n"
            "samples = [(b'row-%04d,' % i) * 8 for i in range(64)]\n"
            "payload = samples[7] + b'extra'\n"
            "assert roundtrip(samples, payload) == payload, 'mismatch'\n"
            "print('PASS')\n"),
    },
    {
        "id": "web2_msgspec_convert",
        "pip": "msgspec",
        "knob": "coercion",   # "strict : ... type coercion rules" @ char 3239
                              # of 4914 in converters.html — v1 kw excerpt
                              # structurally cannot reach it (past doc-start
                              # budget, zero keyword overlap with the prompt)
        # jcristharif.com is dead (2026) — wayback captures. structs.html covers
        # the Struct options; converters.html documents convert(strict=False) —
        # the str->float coercion knob the task actually hinges on.
        "doc_urls": [
            "https://web.archive.org/web/2024/https://jcristharif.com/msgspec/structs.html",
            "https://web.archive.org/web/2024/https://jcristharif.com/msgspec/converters.html",
        ],
        "prompt": (
            "Using the `msgspec` library: define a frozen Struct "
            "`Point` with fields x: float and y: float. Then write "
            "`def to_point(attrs: dict) -> Point` that uses `msgspec.convert`"
            " to build a Point from a dict of string keys ('x','y' holding "
            "numeric strings like '1.5'), and "
            "`def roundtrip(p: Point) -> Point` that msgpack-encodes and "
            "decodes it back into a Point. Return ONLY one fenced python "
            "code block."),
        "test": (
            "import msgspec\n"
            "{code}\n"
            "p = to_point({'x': '1.5', 'y': '-2.25'})\n"
            "assert isinstance(p, Point) and p.x == 1.5 and p.y == -2.25\n"
            "q = roundtrip(p)\n"
            "assert q == p and isinstance(q, Point)\n"
            "try:\n    q.x = 9\n    raise SystemExit('not frozen')\n"
            "except AttributeError:\n    pass\n"
            "print('PASS')\n"),
    },
    {
        "id": "web3_sqlglot_transpile",
        "pip": "sqlglot",
        "knob": "read=",
        "doc_urls": [
            "https://raw.githubusercontent.com/tobymao/sqlglot/main/README.md",
        ],
        "prompt": (
            "Using the `sqlglot` library: write "
            "`def transpile(sql: str) -> str` that transpiles MySQL SQL to "
            "DuckDB SQL with pretty-printing enabled. The input "
            "\"SELECT * FROM t LIMIT 5, 10\" must come out using DuckDB's "
            "'LIMIT 10 OFFSET 5' form (MySQL's LIMIT offset, count is not "
            "valid DuckDB). Return ONLY one fenced python code block."),
        "test": (
            "import re\n"
            "{code}\n"
            "out = transpile('SELECT * FROM t LIMIT 5, 10')\n"
            "flat = re.sub(r'\\s+', ' ', out).upper()\n"
            "assert 'OFFSET 5' in flat, out\n"
            "assert 'LIMIT 10' in flat, out\n"
            "print('PASS')\n"),
    },
]


# ---------------------------------------------------------------- fetch layer
def strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|nav|footer|header).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    return re.sub(r"[ \t]*\n[ \t]*", "\n",
                  re.sub(r"[ \t]+", " ", raw)).strip()


def fetch(url: str, offline: bool = False) -> str:
    """Local-first fetch: cache hit = no network. Offline mode = cache only."""
    CACHE.mkdir(exist_ok=True)
    key = CACHE / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")
    if key.exists():
        return key.read_text()
    if offline:
        raise RuntimeError(f"offline and no cache for {url}")
    import requests
    r = requests.get(url, timeout=20,
                     headers={"User-Agent": "flash-coder-spike"})
    r.raise_for_status()
    text = strip_html(r.text) if "<html" in r.text[:2000].lower() else r.text
    key.write_text(text)
    return text


def excerpt(text: str, task: dict, budget: int = 2000) -> str:
    """First `budget` chars of the doc, plus any line mentioning a term from
    the task prompt (cheap relevance pass — no embeddings needed at n=3)."""
    terms = {w.lower().strip("()`:.",) for w in task["prompt"].split()
             if len(w) > 5 and w[0].islower()}
    hits = [ln for ln in text.splitlines()
            if terms & {w.lower().strip("()`:.,'") for w in ln.split()}]
    extra = "\n".join(hits[:40])
    return (text[:budget] + "\n...\n" + extra)[: budget * 2]


def chunk(text: str, target: int = 800) -> list[str]:
    """Paragraph-ish chunks of ~target chars: merge small lines, hard-split
    oversized paragraphs. Deterministic — retrieval quality must be measurable
    independently of generation stochasticity."""
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


def _embed(model, tok, text: str, max_len: int = 512):
    """Mean-pooled last hidden state — same technique as flash.learn.embed_text
    but robust to BERT-family backbones (tuple returns, batch dim present or
    absent), so dedicated embedder repos work unchanged."""
    import mlx.core as mx
    import numpy as np
    ids = mx.array(tok.encode(text)[:max_len], dtype=mx.int32)[None]
    h = model.model(ids)
    if isinstance(h, tuple):
        h = h[0]
    if h.ndim == 3:
        h = h[0]
    return np.array(mx.mean(h, axis=0).astype(mx.float32))


class TorchEmbedder:
    """Dedicated retrieval embedder via transformers+torch — BOTH already in
    the venv (torch came with misaki, transformers with mlx-lm), so this adds
    zero deps. Attention-masked mean-pool + L2 normalize. mlx-lm 0.31.3 has no
    bert/modernbert backbone, hence torch for encoder-class embedders."""

    def __init__(self, repo: str):
        from transformers import AutoModel, AutoTokenizer
        self.tok = AutoTokenizer.from_pretrained(repo)
        self.model = AutoModel.from_pretrained(repo).eval()

    def embed(self, text: str):
        import torch
        enc = self.tok(text, truncation=True, max_length=512,
                       return_tensors="pt")
        with torch.no_grad():
            h = self.model(**enc).last_hidden_state[0]
        mask = enc["attention_mask"][0].unsqueeze(-1)
        v = (h * mask).sum(0) / mask.sum().clamp(min=1)
        return torch.nn.functional.normalize(v, p=2, dim=0).numpy()


def _prefixes(name: str) -> tuple[str, str]:
    """(query, document) prefixes by embedder family convention."""
    if "nomic" in name:
        return "search_query: ", "search_document: "
    if "bge" in name.lower():
        return ("Represent this sentence for searching relevant passages: ",
                "")
    return "", ""


def excerpt_embed(text: str, task: dict, embed_fn, budget: int = 2000,
                  qpfx: str = "", dpfx: str = ""):
    """Embed-ranked excerpt: cosine-rank ~800-char chunks against the task
    prompt and fill the budget best-first, then restore doc order.

    embed_fn is either the resident coder model (v2: weak ranking features —
    compressed cosine range, near-miss cuts) or a dedicated retrieval
    embedder (v3: --embedder BAAI/bge-small-en-v1.5 et al. via TorchEmbedder,
    or an mlx-community repo via _embed). Returns (excerpt, top1_sim).
    """
    import numpy as np
    chunks = chunk(text)
    q = embed_fn(qpfx + task["prompt"])
    X = np.stack([embed_fn(dpfx + c) for c in chunks])
    sims = (X @ q) / (np.linalg.norm(X, axis=1) * np.linalg.norm(q) + 1e-9)
    picked, used = [], 0
    for i in np.argsort(-sims):
        if used + len(chunks[i]) <= budget:   # skip oversized; smaller later
            picked.append(int(i))             # chunks may still fit
            used += len(chunks[i])
    picked.sort()                             # doc order for readability
    return "\n...\n".join(chunks[i] for i in picked), float(sims.max())


# ------------------------------------------------------------------ A/B eval
def generate(loaded, prompt: str, max_tokens: int = 768) -> str:
    from mlx_lm import generate as mlx_generate
    model, tok = loaded
    msgs = [{"role": "user", "content": prompt}]
    text = tok.apply_chat_template(msgs, tokenize=False,
                                   add_generation_prompt=True)
    return mlx_generate(model, tok, prompt=text, max_tokens=max_tokens,
                        verbose=False)


def extract_code(out: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", out, re.S)
    return m.group(1) if m else out


def run_test(task: dict, code: str) -> tuple[bool, str]:
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
        f.write(task["test"].replace("{code}", code))
        path = f.name
    try:
        r = subprocess.run([sys.executable, path], capture_output=True,
                           text=True, timeout=60)
        return "PASS" in r.stdout, (r.stderr or r.stdout)[-400:]
    except subprocess.TimeoutExpired:
        return False, "timeout"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch-only", action="store_true",
                    help="exercise fetch/cache layer only (no GPU)")
    ap.add_argument("--model",
                    default="mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")
    ap.add_argument("--embedder", default="resident",
                    help="'resident' = rank chunks with the coder model's own "
                         "hidden states; an mlx-community/ repo = MLX embedder; "
                         "any other HF repo = torch encoder via TorchEmbedder "
                         "(v3 default test: BAAI/bge-small-en-v1.5)")
    ap.add_argument("--emb-budget", type=int, default=2500,
                    help="char budget for the embed-ranked excerpt (2500 = v3 "
                         "validated: web2's knob needs 2299; 2000 cuts it)")
    ap.add_argument("--knob-only", action="store_true",
                    help="deterministic knob-retrieval metric only — no "
                         "generation arms (fast ranker A/B)")
    args = ap.parse_args()

    if args.fetch_only:
        for t in TASKS:
            for url in t["doc_urls"]:
                text = fetch(url)
                ex = excerpt(text, t)
                print(f"{t['id']}: {len(text)} chars fetched+cached, "
                      f"excerpt {len(ex)} chars")
        print("\nfetch layer OK — rerun without --fetch-only for the A/B eval")
        return

    # GPU contention guard: a vision suite run shares this machine.
    if subprocess.run(["pgrep", "-f", VLM_FREE_CHECK],
                      capture_output=True).returncode == 0:
        raise SystemExit("run-vis-suite active — wait for the GPU to free up")

    for t in TASKS:
        subprocess.run([sys.executable, "-m", "pip", "install", "-q",
                        t["pip"]], check=True)

    from mlx_lm import load
    dedicated = args.embedder != "resident"
    use_torch = dedicated and not args.embedder.startswith("mlx-community/")
    # ONE load per model — reloading per arm OOM-kills (M2 hot-swap lesson).
    # knob-only + dedicated embedder: the 7B is never used — don't load it.
    loaded = None if (args.knob_only and dedicated) else load(args.model)
    if dedicated and use_torch:
        emb_fn = TorchEmbedder(args.embedder).embed
    elif dedicated:
        emb_loaded = load(args.embedder)
        emb_fn = lambda s: _embed(*emb_loaded, s)
    else:
        emb_fn = lambda s: _embed(*loaded, s)
    qpfx, dpfx = _prefixes(args.embedder) if dedicated else ("", "")
    print(f"embedder: {args.embedder}  (emb budget {args.emb_budget})")

    results = []
    for t in TASKS:
        texts = [fetch(u) for u in t["doc_urls"]]
        kw = "\n\n".join(excerpt(x, t) for x in texts)
        t0 = time.time()
        ranked = [excerpt_embed(x, t, emb_fn, args.emb_budget, qpfx, dpfx)
                  for x in texts]
        em = "\n\n".join(r[0] for r in ranked)
        print(f"{t['id']}: knob '{t['knob']}' in excerpt? "
              f"kw={t['knob'] in kw}  emb={t['knob'] in em}  "
              f"(embed rank {time.time() - t0:.1f}s, top1 sim "
              f"{max(r[1] for r in ranked):.3f})")
        row = {"id": t["id"], "knob_kw": t["knob"] in kw,
               "knob_emb": t["knob"] in em}
        for arm, prompt in ([] if args.knob_only else [
            ("blind", t["prompt"]),
            ("docs-kw", f"Relevant documentation:\n{kw}\n\n{t['prompt']}"),
            ("docs-emb", f"Relevant documentation:\n{em}\n\n{t['prompt']}"),
        ]):
            t0 = time.time()
            code = extract_code(generate(loaded, prompt))
            ok, tail = run_test(t, code)
            row[arm] = {"pass": ok, "s": round(time.time() - t0, 1)}
            print(f"{t['id']:24s} {arm:8s} -> {'PASS' if ok else 'FAIL'} "
                  f"({row[arm]['s']}s)" + ("" if ok else f"  | {tail[-120:]}"))
        results.append(row)

    print("\n== VERDICT ==")
    print(f"  knob retrieval (deterministic):  kw "
          f"{sum(r['knob_kw'] for r in results)}/3   emb "
          f"{sum(r['knob_emb'] for r in results)}/3")
    if args.knob_only:
        return
    known = [r["id"] for r in results if r["blind"]["pass"]]
    print(f"  blind-pass (model already knew): {known or 'none'}")
    for arm in ("docs-kw", "docs-emb"):
        swung = [r["id"] for r in results
                 if not r["blind"]["pass"] and r[arm]["pass"]]
        lost = [r["id"] for r in results
                if r["blind"]["pass"] and not r[arm]["pass"]]
        print(f"  {arm}: swung FAIL->PASS {swung or 'none'}; "
              f"lost PASS->FAIL {lost or 'none'}")
    any_swung = any(not r["blind"]["pass"]
                    and (r["docs-kw"]["pass"] or r["docs-emb"]["pass"])
                    for r in results)
    print("  thesis 'knowledge as a tool': "
          + ("SUPPORTED at spike scale" if any_swung else
             "NOT demonstrated — tasks too easy or docs insufficient"))


if __name__ == "__main__":
    main()

