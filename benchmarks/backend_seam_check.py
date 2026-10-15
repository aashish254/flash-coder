"""R-7.17d: the backend seam, proven with MLX BLOCKED at the interpreter.

Why this file exists
--------------------
`backend_message_check.py` proves a box with no generation backend refuses in a
sentence. `backend_free_check.py` proves the package IMPORTS without one. Neither
proves the thing PHASE B claims, which is that a second backend can carry a whole
oracle-scored turn — that the loop, the tournament, the ledger, the power governor
and the confidence table never learn which library answered.

Two things make that more than a mock:

  * **the oracle is real.** `flash run` materialises the answer into a temp
    package, executes the task's assert file as a subprocess, reads which assert
    failed with what value, feeds that back, and grades the SECOND answer. A
    stand-in that only returns a string cannot make that turn succeed unless the
    loop's feedback path is genuinely backend-neutral — so the pass is evidence
    about the loop, not about the stub.
  * **the second arm drives the shipping class.** `LlamaCppBackend`, `gguf_path`,
    `Backend.guard` and `owner()` are the real lines, run against a stand-in
    `llama_cpp` module that implements only its documented call shape. The
    refusal to apply `--constrain`, the tier resolution and the log-prob read are
    executed, not described.

MEASURED, AND WHAT THAT DID TO THIS FILE — 2026-10-15. The paragraph this one
replaced said llama-cpp-python could not be installed here and no GGUF would ever
be pulled, so the real surface could not be probed. Both turned out to be possible
(a source build in a throwaway virtual environment; `flash models pull fast` moved
the pinned 4.68 GB), and driving the shipping class against the REAL library found
three defects this file's stand-ins had been hiding — because each stand-in was
written to match `flash/backend.py`, and a stub that agrees with its own code
cannot disagree with it:

  * `Llama.tokenize` takes `add_bos`/`special`, not `add_special`, so every llama.cpp
    token count raised `TypeError` — the answer still printed while the session billed
    itself `uncounted=1/1`, because `trace.n_tokens` catches, and the uncaught copy of
    that call shape is on `decide`'s label path;
  * asking for log-probs on a model built with `logits_all=False` (the library's own
    default, and what `load_model` leaves it at) raises `ValueError`, so the declared
    `probe: True` was a capability this backend does not have;
  * its stream chunks carry `(None, text)` — measured — and `checkpoint.note` appends
    `int(token_id)` to that, which is an armed `run-suite` dying at the first token.

Each of those is now a check below, with the stand-in recording the real library's
argument names rather than this file's guesses, and each has a mutant. The measured
numbers themselves — load time, tokens, throughput, the pull — are in
`docs/models.md` and SPEC's R-7.18/R-7.19 boxes, not here; a gate that re-runs them
would need a 4.68 GB weight and a compiled llama.cpp on every box that reads §6.

What this file therefore claims, exactly: THE WIRING is proven, and the call shapes
it drives are the installed library's. What llama.cpp computes inside `Llama(...)`
is still not this file's evidence — one real generation on one Mac with one pinned
GGUF is a fact about that pair, and the arms below are what keeps the class honest
about it. `LlamaCppBackend`'s own docstring says the same, so a reader of the code
meets the limit before a reader of the site does.

Not a §6 battery row. `benchmarks/battery_reread.py`'s `BATTERY` is a closed list
and its `CLAIM` (checks 1429, oracle 20, mutants 172) is the published total; a
new vector lives in a file absent from that list, like the two `backend_*` gates
beside it.

    python benchmarks/backend_seam_check.py
    python benchmarks/backend_seam_check.py --mutant NAME
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

import backend_free_check as free                            # noqa: E402

TB = "Traceback (most recent call last)"

#: The five modules the directive names, plus `decide`, which used to own the
#: forward pass. None of them may say the name of a model library.
AGNOSTIC = ("loop.py", "tourney.py", "ledger.py", "power.py", "confidence.py",
            "decide.py")

#: The library names a consumer is not allowed to mention any more.
LIBS = ("mlx", "mlx_lm", "mlx_vlm", "llama_cpp")

CHECKS: list[tuple[str, bool, str]] = []


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


# --------------------------------------------------------------- arm A: the turn
#
# One `flash run`, start to finish, with MLX blocked and a stand-in backend
# answering. The task is the smallest thing that can still FAIL an oracle, and the
# stand-in is tier-aware: the fast tier keeps failing the assert and the brain
# answers it. So the turn cannot end GREEN unless the loop materialised the answer,
# ran the test, read which assert failed with what value, retried, gave the small
# tier's memory back and loaded the second tier — every one of those is a seam call.
_CHILD_A = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "__ROOT__")

blocked = ""
try:
    import mlx.core                                    # noqa: F401
    blocked = "IMPORTED"
except Exception as e:
    blocked = type(e).__name__

from flash import backend, cli, ledger, trace

#: (the failing one on the small tier, the passing one on the brain): the turn
#: cannot end GREEN unless the loop both retries AND hot-swaps through the seam.
WRONG = "```python\ndef f(x):\n    return x * 2\n```\n"
RIGHT = "```python\ndef f(x):\n    return x + 2\n```\n"


class _M:
    """The stub `model`: which tier was loaded, and who loaded it."""

    def __init__(self, be, ref):
        self._flash_backend = be
        self.ref = ref


class _T:
    """The stub tokenizer: the two methods the loop and the trace actually use."""

    def encode(self, text, *a, **k):
        return list(range(max(1, len(text) // 4)))


class StandIn(backend.Backend):
    name = "standin"
    supports = {"constrain": False, "probe": True, "adapter": False,
                "stream": True, "embed": False}

    def __init__(self):
        self.prompts = []
        self.loaded = []
        self.idles = 0
        self.n = 0

    def available(self):
        return True, ""

    def load_model(self, ref, adapter=None):
        self.loaded.append([ref, adapter])
        return _M(self, ref), _T()

    def format(self, tokenizer, messages):
        return "\n".join(m["content"] for m in messages)

    def generate(self, model, tokenizer, prompt, max_tokens, sampler, guard):
        self.n += 1
        self.prompts.append(prompt)
        # the brain answers; the small tier never does, so the turn cannot end
        # GREEN unless the loop both retries and hot-swaps through the seam
        return backend.Generation(text=RIGHT if model.ref == "tier:brain"
                                  else WRONG)

    def probe(self, model, tokenizer, prompt, ids):
        return [0.0, -9.0]          # option A wins: the router says EASY, small tier

    def idle(self):
        self.idles += 1


be = StandIn()
backend.REGISTRY["standin"] = be
backend.use("standin")

rows = []
real_rec = ledger.record
ledger.record = lambda entry, path=ledger.LEDGER_FILE: rows.append(dict(entry))
tdir = Path(tempfile.mkdtemp(prefix="flash-seam-"))
trace.DIR = tdir
sys.argv = ["flash", "run", "__PROMPT__", "--test", "__TEST__",
            "--attempts", "2", "--small", "tier:fast", "--big", "tier:brain",
            "--allow-big", "always", "--max-tokens", "64"]
try:
    rc = cli.main()
finally:
    ledger.record = real_rec

sids = sorted(p.stem for p in tdir.glob("*.jsonl"))
recs = trace.read(sids[0]) if sids else []
print("SEAMJSON " + json.dumps({
    "rc": rc, "blocked": blocked, "n_gen": be.n, "loaded": be.loaded,
    "idles": be.idles, "prompts": be.prompts, "rows": rows,
    "trace": [{"type": r["type"], "ok": r.get("ok"), "attempt": r.get("attempt"),
               "tier": r.get("tier"), "routed": r.get("routed"),
               "tournament": r.get("tournament"),
               "tournament_used": r.get("tournament_used")} for r in recs]}))
'''

#: A tournament asked of the same seam, in a second child so arm A's ledger rows
#: and generate-count stay about ONE turn. `tourney.run` is called directly rather
#: than through the CLI because R-3.3's eligibility question is a power-governor
#: decision: on a hot box it declines, and a gate must not read the thermometer to
#: know whether the tournament code is backend-neutral.
_CHILD_B = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "__ROOT__")

blocked = ""
try:
    import mlx.core                                    # noqa: F401
    blocked = "IMPORTED"
except Exception as e:
    blocked = type(e).__name__

from flash import backend, tourney, trace

exec(open("__AUX__").read())          # the same StandIn/_M/_T definitions

be = StandIn()
be.wrong = 2                          # candidates 0 and 1 fail the oracle on purpose
backend.REGISTRY["standin2"] = be
backend.use("standin2")
trace.DIR = Path(tempfile.mkdtemp(prefix="flash-seam-tourney-"))
model, tok = be.load_model("tier:fast")
task = {"id": "seam-tourney", "prompt": "make f(1) return 3", "test": TESTSRC}
out = tourney.run(model, tok, task, requested_k=3, max_tokens=64)
print("SEAMJSON " + json.dumps({
    "blocked": blocked,
    "asked": out.requested_k, "k": out.k, "gens": out.n_generations,
    "adopted": out.adopted, "solved": bool(out.solved),
    "prompts": len(be.prompts), "why": out.why,
    "verdicts": [[c.ok, c.passed, c.total] for c in out.candidates],
    "err": [c.err[:70] for c in out.candidates]}))
'''

#: The single-file VERIFY shape: `harness.score` splices the answer and the test
#: into one program, so the asserts reference `f` directly and no `<TMPDIR>`
#: bootstrap is involved.
_ANSWERS = '''
WRONG = "```python\\ndef f(x):\\n    return x * 2\\n```\\n"   # f(1) is 2
RIGHT = "```python\\ndef f(x):\\n    return x + 2\\n```\\n"   # f(1) is 3
TESTSRC = "assert f(1) == 3\\n"


class _M:
    def __init__(self, be):
        self._flash_backend = be


class _T:
    def encode(self, text, *a, **k):
        return list(range(max(1, len(text) // 4)))


class StandIn(backend.Backend):
    name = "standin"
    supports = {"constrain": False, "probe": True, "adapter": False,
                "stream": True, "embed": False}

    def __init__(self):
        self.prompts = []
        self.idles = 0
        self.n = 0
        self.wrong = 0

    def available(self):
        return True, ""

    def load_model(self, ref, adapter=None):
        return _M(self), _T()

    def format(self, tokenizer, messages):
        return "\\n".join(m["content"] for m in messages)

    def generate(self, model, tokenizer, prompt, max_tokens, sampler, guard):
        self.n += 1
        self.prompts.append(prompt)
        return backend.Generation(text=WRONG if self.n <= self.wrong else RIGHT)

    def probe(self, model, tokenizer, prompt, ids):
        return [0.0, -9.0]

    def idle(self):
        self.idles += 1
'''


def oracle_file() -> Path:
    """The assert file the loop grades each answer with.

    The single-file VERIFY shape: `harness.score` splices the answer and the test
    into one program, so the oracle names the symbol rather than a module, and the
    turn needs no repo on disk to be graded.
    """
    d = Path(tempfile.mkdtemp(prefix="flash-seam-"))
    (d / "test_oracle.py").write_text("assert f(1) == 3\n")
    return d


def run_child(src: str, env: dict, timeout: int = 600) -> dict:
    """One child, one `SEAMJSON` line — or the failure evidence, shaped alike."""
    r = subprocess.run([sys.executable, "-c", src], capture_output=True,
                       text=True, env=env, cwd=ROOT, timeout=timeout)
    out = r.stdout + r.stderr
    line = [l for l in out.splitlines() if l.startswith("SEAMJSON ")]
    if not line:
        return {"_rc": r.returncode, "_out": out, "_tb": TB in out}
    d = json.loads(line[0][len("SEAMJSON "):])
    d["_rc"] = r.returncode
    d["_out"] = out
    d["_tb"] = TB in out
    return d


def arm_a(env: dict) -> dict:
    repo = oracle_file()
    src = (_CHILD_A.replace("__ROOT__", str(ROOT))
           .replace("__TEST__", str(repo / "test_oracle.py"))
           .replace("__PROMPT__", "make f(1) return 3 instead of 2"))
    return run_child(src, env)


def arm_tourney(env: dict) -> dict:
    aux = Path(tempfile.mkdtemp(prefix="flash-seam-aux-")) / "standin.py"
    aux.write_text(_ANSWERS)
    src = (_CHILD_B.replace("__ROOT__", str(ROOT))
           .replace("__AUX__", str(aux)))
    return run_child(src, env)


def mlx_named_nodes(rel: str) -> list[str]:
    """Every place a module says a model library's name, as `file:line kind`."""
    tree = ast.parse((ROOT / "flash" / rel).read_text())
    hits: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            hits += [f"{rel}:{n.lineno} import {a.name}" for a in n.names
                     if a.name.split(".")[0] in LIBS]
        elif isinstance(n, ast.ImportFrom):
            if (n.module or "").split(".")[0] in LIBS:
                hits.append(f"{rel}:{n.lineno} from {n.module}")
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                and n.value.id in LIBS:
            hits.append(f"{rel}:{n.lineno} {n.value.id}.")
    return sorted(set(hits))


# ------------------------------------------------------------- arm C: llama.cpp
#
# The shipping `LlamaCppBackend`, driven against a stand-in `llama_cpp`. Nothing
# here claims llama.cpp computes what the stand-in does — the point is which LINES
# OF OURS RUN and what they refuse.
_CHILD_CPP = r'''
import json, sys, tempfile, types
from pathlib import Path
sys.path.insert(0, "__ROOT__")

blocked = ""
try:
    import mlx.core                                    # noqa: F401
    blocked = "IMPORTED"
except Exception as e:
    blocked = type(e).__name__

CALLS = []
LOADS = []


class _Llama:
    """llama-cpp-python 0.3.36's documented surface, and nothing behind it.

    The chat template is the shape a real GGUF carries — a loop over `messages` —
    because `apply_chat_template` renders whatever the FILE says, which is the
    whole reason this backend refuses to hand-write a role layout.

    Every argument name below was read off the INSTALLED library, not guessed from
    `flash/backend.py`, and each one is recorded and asserted:

      * `tokenize(text: bytes, add_bos: bool = True, special: bool = False)` — there
        is no `add_special` keyword. The stand-in used to spell it that way, which
        matched this repo's code rather than the library, and that is how a wrong
        call survived: the stand-in answered, the real `Llama` raised
        `TypeError: Llama.tokenize() got an unexpected keyword argument 'add_special'`.
      * `__init__(…, logits_all=False)` and `self._logits_all` — the library's own
        default, which is what flash's load line leaves it at, and which is why a
        `logprobs=…` request raises `ValueError` rather than routing.
      * a streaming chunk from the completion API carries `text` and NO token id —
        measured as `(None, piece)` through `LlamaCppBackend.stream`, which is the
        reason `supports["ids"]` is False and the checkpointed path must not be
        asked to `int(None)`.
    """

    def __init__(self, model_path, n_ctx=512, verbose=True, logits_all=False,
                 draft_model=None, **kw):
        LOADS.append({"model_path": model_path, "n_ctx": n_ctx,
                      "logits_all": logits_all, "kw": sorted(kw)})
        self.model_path, self.n_ctx = model_path, n_ctx
        # llama.py:351 — the buffer is on unless a draft model forces it
        self._logits_all = logits_all if draft_model is None else True
        self.tokenize_args = []
        self.metadata = {"tokenizer.chat_template":
                         "{% for message in messages %}{{ message['content'] }}"
                         "{% endfor %}"}

    def __call__(self, prompt, max_tokens=16, temperature=0.0, stream=False,
                 logprobs=None, echo=True, seed=None, **kw):
        CALLS.append({"prompt": prompt[:24], "max_tokens": max_tokens,
                      "temperature": temperature, "stream": stream,
                      "logprobs": logprobs, "seed": seed, "echo": echo})
        # the library's own refusal, read at llama.py:1365 and hit live on 2026-10-15
        if logprobs is not None and self._logits_all is False:
            raise ValueError("logprobs is not supported for models created with "
                             "logits_all=False")
        if stream:
            return self._stream()
        return {"choices": [{"text": "ANSWER",
                             "logprobs": {"top_logprobs": [{" C": -0.2}]}}]}

    def _stream(self):
        for piece in ("AN", "SW"):
            yield {"choices": [{"text": piece}]}

    def tokenize(self, text, add_bos=True, special=False):
        self.tokenize_args.append({"add_bos": add_bos, "special": special,
                                   "bytes": isinstance(text, bytes)})
        return [65 + b for b in text[:8]]

    def detokenize(self, ids):
        return {10: b"A", 11: b"S", 3: b" C", 4: b" D"}.get(ids[-1], b"~")


class _NoTpl(_Llama):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.metadata = {}


mod = types.ModuleType("llama_cpp")
mod.Llama = _Llama
sys.modules["llama_cpp"] = mod

from flash import backend

out = {"blocked": blocked}
be = backend.LlamaCppBackend()
out["available"] = list(be.available())
gg = Path(tempfile.mkdtemp(prefix="flash-seam-gguf-"))
backend.GGUF_DIR = gg
f = gg / "x.gguf"
f.write_text("not a gguf; the stand-in never reads the bytes\n")

model, tok = be.load_model(str(f), None)
out["marker"] = backend.owner(model) is be
out["n_ctx"] = model.ll.n_ctx
out["profile"] = be.profile()
out["format"] = be.format(tok, [{"role": "user", "content": "hi"}])
out["encode"] = tok.encode("abcd", add_special_tokens=False)
out["tokenize_args"] = list(model.ll.tokenize_args)
from flash import decide
try:
    out["label_ids"] = decide._label_token_ids(tok, 3)
except Exception as e:                                          # noqa: BLE001
    out["label_ids"] = f"{type(e).__name__}: {e}"
g = be.generate(model, tok, "PROMPT", 8, be.sampler(0.7), None)
out["generate"] = [g.text, g.constrained, CALLS[-1]]
be.seed(11)
be.generate(model, tok, "PROMPT", 8, None, None)
out["seeded"] = CALLS[-1]["seed"]
out["stream"] = list(be.stream(model, tok, "PROMPT", 8, None, None))
out["load_args"] = dict(LOADS[-1])
out["caps"] = {"mlx": dict(backend.MLXBackend.supports),
               "cpp": dict(be.supports)}

# The router's probe on THE SHIPPED LOAD — `Llama(model_path, n_ctx, verbose)`,
# which leaves `logits_all` at the library's own False — refuses. Measured live on
# 2026-10-15: llama-cpp-python raised `ValueError: logprobs is not supported for
# models created with logits_all=False`, and the fix is a 5.0 GB (n_ctx x vocab)
# buffer at this backend's 8192 x 152,064, which flash will not pay to route a
# question. So the refusal is the shipped behaviour and must say why.
try:
    be.probe(model, tok, "PROMPT", [3, 4])
    out["probe_refused"] = "returned"
except backend.NoFeature as e:
    out["probe_refused"] = "NoFeature:" + str(e)[:330]
except BaseException as e:                                      # noqa: BLE001
    out["probe_refused"] = type(e).__name__ + ": " + str(e)[:80]

# ...and the arithmetic under that guard stays under test. This handle is built the
# way a caller that DID ask for the buffer would build one — `flash`'s own load line
# above does not, which is what `load_args` proves.
# label id 3 is in the table (" C"), label id 4 is not: the missing one must not
# read as 0.0, or `max(raw)` lets the least-likely option win the route.
model.ll = _Llama(str(f), n_ctx=be.n_ctx, verbose=False, logits_all=True)
out["probe"] = be.probe(model, tok, "PROMPT", [3, 4])
out["probe_call"] = CALLS[-1]

try:
    be.guard(object(), tok)
    out["constrain"] = "returned"
except backend.NoFeature as e:
    out["constrain"] = "NoFeature:" + str(e)[:90]
except BaseException as e:
    out["constrain"] = type(e).__name__

try:
    be.load_model(str(f), "some/adapter")
    out["adapter"] = "returned"
except backend.NoFeature as e:
    out["adapter"] = "NoFeature:" + str(e)[:60]

# --- gguf_path: the three shapes, and the two refusals
out["path_is_file"] = backend.gguf_path(str(f)) == str(f)
(gg / "named.gguf").write_text("x\n")
out["path_by_name"] = Path(backend.gguf_path("named.gguf")).name
(gg / backend.MANIFEST).write_text(json.dumps({"fast": "named.gguf"}))
out["tier"] = Path(backend.gguf_path("fast")).name
(gg / backend.MANIFEST).write_text(json.dumps({"brain": "absent.gguf"}))
try:
    backend.gguf_path("brain")
    out["stale_manifest"] = "returned"
except backend.NoBackend as e:
    out["stale_manifest"] = [str("re-pull" in str(e).lower()),
                             "absent.gguf" in str(e), "models pull" in str(e)]
try:
    backend.gguf_path("/no/such/dir/model.gguf")
    out["missing"] = "returned"
except backend.NoBackend as e:
    out["missing"] = ("FLASH_GGUF" in str(e)) and ("models pull" in str(e))

# --- a GGUF with no chat template refuses rather than inventing a layout
mod.Llama = _NoTpl
try:
    be.load_model(str(f), None)[1].apply_chat_template(
        [{"role": "user", "content": "hi"}])
    out["no_template"] = "returned"
except backend.NoFeature as e:
    out["no_template"] = "NoFeature:" + str(e)[:60]
mod.Llama = _Llama

# --- `--backend` naming a library the box cannot run
del sys.modules["llama_cpp"]
ok, why = be.available()
out["unavailable"] = [ok, why[:50]]
try:
    backend.use("llama.cpp")
    out["forced_unavailable"] = "returned"
except backend.NoBackend as e:
    out["forced_unavailable"] = str(e)[:200]
try:
    backend.use("not-a-backend")
    out["unknown"] = "returned"
except backend.NoBackend as e:
    out["unknown"] = str(e)[:120]
out["report"] = backend.report()
print("SEAMJSON " + json.dumps(out))
'''


def arm_cpp(env: dict) -> dict:
    return run_child(_CHILD_CPP.replace("__ROOT__", str(ROOT)), env)


# ------------------------------------------------- arm D: the armed checkpoint
#
# `supports["ids"] = False` is only a claim until the one place that reads it is
# driven: `loop._generate`'s choice between `generate` and `stream`. Both arms run
# in one child because the pair is the check — a guard that turned OFF streaming for
# everything would pass an `ids: False` arm alone, and the MLX-shaped arm is the one
# that says the measured streaming checkpoint is still the path for a backend that
# can deliver an id.
_CHILD_D = r'''
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, "__ROOT__")

blocked = ""
try:
    import mlx.core                                        # noqa: F401
    blocked = "IMPORTED"
except Exception as e:
    blocked = type(e).__name__

from flash import backend, checkpoint, loop, trace

CALLS = []


class _M:
    def __init__(self, be):
        self._flash_backend = be


class _T:
    def encode(self, text, *a, **k):
        return list(range(max(1, len(text) // 4)))


class Stub(backend.Backend):
    """A backend whose stream does, or does not, carry a token id.

    `no_ids` declares `supports["ids"] = False` the way `LlamaCppBackend` does; the
    pieces it yields are the measured llama.cpp chunk shape (text, no id). The other
    arm yields real ints and declares nothing, which is MLX's shape and every
    stub arm `BATTERY` measures.
    """

    def __init__(self, tag, no_ids):
        self.name = "stub-" + tag
        self.supports = {"constrain": False, "probe": True, "adapter": False,
                         "stream": True, "embed": False}
        if no_ids:
            self.supports["ids"] = False
        self.no_ids = no_ids

    def available(self):
        return True, ""

    def load_model(self, ref, adapter=None):
        return _M(self), _T()

    def format(self, tokenizer, messages):
        return messages[-1]["content"]

    def generate(self, model, tokenizer, prompt, max_tokens, sampler, guard):
        CALLS.append("generate")
        return backend.Generation(text="ONE-SHOT")

    def stream(self, model, tokenizer, prompt, max_tokens, sampler, guard):
        CALLS.append("stream")
        for i, piece in enumerate(("ONE", "-SHOT"), start=1):
            yield (None if self.no_ids else i), piece

    def probe(self, model, tokenizer, prompt, ids):
        return [0.0, -9.0]

    def idle(self):
        pass


def one(tag, no_ids):
    CALLS.clear()
    checkpoint.disarm()
    trace.DIR = Path(tempfile.mkdtemp(prefix="flash-seam-armed-"))
    tsid = trace.open_session("seam-" + tag)
    be = Stub(tag, no_ids)
    backend.REGISTRY[be.name] = be
    backend.use(be.name)
    # the exact arming `flash run-suite` does (cli.py arms the session, the loop
    # opens the frame per attempt), so `span` is True inside `_generate`
    checkpoint.arm("seam-" + tag)
    model, tok = be.load_model("tier:fast")
    checkpoint.begin("t-" + tag, "edits", "small", 0, max_tokens=8)
    err = None
    text = None
    try:
        text = loop._generate(model, tok, [{"role": "user", "content": "P"}],
                              8, task_id="t-" + tag, attempt=0)
    except BaseException as e:                                   # noqa: BLE001
        err = type(e).__name__ + ": " + str(e)[:90]
    fr = checkpoint.current()
    return {"calls": list(CALLS), "text": text, "err": err,
            "ids": list(fr.ids) if fr else None,
            "answer": fr.answer if fr else None,
            "carried": fr.carried if fr else None,
            "frame_on_disk": checkpoint.path_for("seam-" + tag).is_file(),
            "events": [ev.get("checkpointed") for ev in trace.read(tsid)
                       if ev.get("type") == "generate"]}


out = {"blocked": blocked,
       "cpp": one("cpp", True),
       "mlx": one("mlx", False)}
print("SEAMJSON " + json.dumps(out))
'''


def arm_armed(env: dict) -> dict:
    return run_child(_CHILD_D.replace("__ROOT__", str(ROOT)), env)


# ------------------------------------------------------------------ the gates

def run_gates() -> None:
    env = free.shim_env()

    blocked = subprocess.run([sys.executable, "-c", "import mlx.core"],
                             capture_output=True, text=True, env=env, cwd=ROOT,
                             timeout=120)
    ck("the blocker bites in a child before any seam claim under it is believed: "
       "`import mlx.core` fails with the shipped shim's own sentence",
       blocked.returncode != 0 and "--backend-free" in (blocked.stderr
                                                        + blocked.stdout),
       "child import")

    a = arm_a(env)
    vs = [t for t in a.get("trace", []) if t["type"] == "verify"]
    row = a.get("rows", [{}])[0] if a.get("rows") else {}
    prompts = a.get("prompts", [])

    ck("a WHOLE oracle-scored turn runs on a stand-in backend with MLX blocked at "
       "the interpreter: `flash run` exits 0, prints solved=True, and named no "
       "library of its own",
       a.get("blocked") != "IMPORTED" and a.get("rc") == 0 and not a.get("_tb")
       and "solved=True" in a.get("_out", ""),
       f"blocked={a.get('blocked')} rc={a.get('rc')} tb={a.get('_tb')} "
       f"out={a.get('_out', '')[-260:]}")

    ck("the loop asked the seam to load BOTH tiers BY THE NAMES THE CLI WAS GIVEN, "
       "in the order the policy decided, and only the seam decided what a tier is",
       [l[0] for l in a.get("loaded", [])] == ["tier:fast", "tier:brain"],
       str(a.get("loaded")))

    ck("the answer that came back is the LIBRARY's, not the loop's: every prompt "
       "went out through the seam and the second one is the first one plus the "
       "failing assert",
       len(prompts) >= 3 and "assert f(1) == 3" in prompts[1]
       and prompts[0] != prompts[1],
       f"{len(prompts)} prompt(s); tail of the second: "
       f"{(prompts[1][-200:] if len(prompts) > 1 else '')!r}")

    ck("the VERIFY step really ran the oracle: the two small-tier attempts failed "
       "and the brain's answer passed, which is the loop grading a backend rather "
       "than trusting one",
       [(v["attempt"], v["ok"]) for v in vs] == [(0, False), (1, False), (0, True)],
       str(vs))

    ck("the failing assert's VALUE reached the backend, so the retry is feedback "
       "and not a re-roll: the second prompt quotes what the oracle saw",
       len(prompts) >= 2 and "GOT" in prompts[1]
       and "assert f(1) == 3" in prompts[1],
       f"tail={(prompts[1][-200:] if len(prompts) > 1 else '')!r}")

    ck("the routing decision went through the seam too — the router's forward pass "
       "is a `probe`, and the record says the small tier was chosen",
       any(t["type"] == "route" and t["routed"] == "small" for t in a.get("trace", []))
       and a.get("rc") == 0,
       str([t for t in a.get("trace", []) if t["type"] == "route"]))

    ck("the hot-swap free is a backend verb: the loop called `idle()` and never "
       "said `mx.clear_cache` — the 32GB machine still never holds both tiers",
       a.get("idles", 0) >= 1, f"idles={a.get('idles')}")

    ck("the LEDGER row the turn wrote names a tier and a route and no library: "
       "an outcome record that could not say which backend answered is the "
       "agnosticism claim, read off the data",
       bool(row) and row.get("tier") == "big" and row.get("solved") is True
       and row.get("routed") == "small"
       and not [k for k in list(row) + [str(v) for v in row.values()]
                if any(l in str(k).lower() for l in LIBS)],
       str(sorted(row))[:220])

    ck("one turn's ledger row, and one turn's worth of generations: the seam did "
       "not double-run the loop to find a backend",
       len(a.get("rows", [])) == 1 and len(a.get("prompts", [])) == 3,
       f"rows={len(a.get('rows', []))} prompts={len(a.get('prompts', []))}")

    t = arm_tourney(env)
    ck("the TOURNAMENT is the same story: `tourney.run` fired three candidates at "
       "one task through the stand-in with MLX blocked, and each verdict came from "
       "the oracle rather than from what the backend said",
       t.get("_rc") == 0 and not t.get("_tb") and t.get("blocked") != "IMPORTED"
       and t.get("k") == 3 and t.get("gens") == 3 and t.get("prompts") == 3
       and [v[0] for v in t.get("verdicts", [])] == [False, False, True],
       str({k: t.get(k) for k in ("blocked", "k", "gens", "prompts", "verdicts",
                                  "why")})[:260])

    ck("...and the candidate that PASSED is the one adopted, which is the "
       "selection logic reading the same verdict the loop reads",
       t.get("solved") is True and t.get("adopted") == 2,
       str({k: t.get(k) for k in ("solved", "adopted", "err")})[:260])

    for rel in AGNOSTIC:
        hits = mlx_named_nodes(rel)
        ck(f"`flash/{rel}` says no model library's name anywhere — not one import, "
           f"not one attribute: this is the file the {rel[:-3]} relies on to stay "
           "backend-neutral",
           not hits, "; ".join(hits))

    c = arm_cpp(env)
    ck("the SHIPPING LlamaCppBackend class loads a GGUF through the seam and hands "
       "back a pair the loop can address, with the handle carrying who loaded it",
       c.get("blocked") != "IMPORTED" and c.get("available") == [True, ""]
       and c.get("marker") is True,
       str({k: c.get(k) for k in ("blocked", "available", "marker")}))

    ck("...and a generation through the real class is loaded with the context "
       "flash's prompts need rather than llama.cpp's default 512, because a flash "
       "prompt is a repo skeleton plus an oracle and 512 would truncate the TASK",
       c.get("n_ctx") == 8192, str(c.get("n_ctx")))

    ck("the chat template the model CARRIES is the one rendered — a prompt built "
       "by a different template than the weights were fine-tuned on is a wrong "
       "measurement, not a cheap one",
       c.get("format") == "hi", str(c.get("format")))

    ck("`_GgufTokenizer.encode` speaks the library's REAL tokenize surface: the "
       "keyword is `add_bos`, llama-cpp-python 0.3.36 defaults it to True, so a BOS "
       "flash never asked for is refused, special tokens are read as tokens, and "
       "what reaches the library is BYTES. This stand-in used to spell the keyword "
       "`add_special` — matching this repo's code rather than the installed "
       "library — and so it hid a `TypeError: Llama.tokenize() got an unexpected "
       "keyword argument 'add_special'` that every real llama.cpp run hits",
       c.get("encode") == [162, 163, 164, 165]
       and c.get("tokenize_args") == [{"add_bos": False, "special": True,
                                       "bytes": True}],
       f"encode={c.get('encode')} args={c.get('tokenize_args')}")

    ck("...and the ROUTER gets its label ids out of the same tokenizer instead of "
       "dying on the way: `flash.decide._label_token_ids` is the line a routed GGUF "
       "run runs before it samples anything, and under the wrong keyword a routed "
       "run crashed here rather than generating",
       isinstance(c.get("label_ids"), list)
       and len(c.get("label_ids")) == 3
       and all(isinstance(i, int) for i in c.get("label_ids", [])),
       str(c.get("label_ids"))[:160])

    ck("a generation through the real class returns the text and passes the loop's "
       "own knobs (max_tokens, the temperature the sampler made, no seed until one "
       "is set)",
       c.get("generate", [None])[0] == "ANSWER"
       and c.get("generate", [None, None, {}])[2]["max_tokens"] == 8
       and c.get("generate", [None, None, {}])[2]["temperature"] == 0.7
       and c.get("seeded") == 11,
       str(c.get("generate"))[:220])

    ck("streaming through the real class yields (None, piece) — llama-cpp-python's "
       "completion chunks carry no token id on this call shape, measured on the "
       "installed library — and `flash.checkpoint.note` does `int(token_id)`, so an "
       "armed span that streamed here would die at its FIRST token. This pair is the "
       "evidence behind `supports[\"ids\"] = False`, not a decoration under it",
       c.get("stream") == [[None, "AN"], [None, "SW"]], str(c.get("stream")))

    ck("the capability table says what the library refused: `probe` False, `ids` "
       "False, `constrain` False — while MLX declares neither refusal, so every "
       "measured MLX and stub arm keeps the streaming checkpoint path it was scored "
       "on. The loop's guard asks `is not False` for exactly that reason",
       c.get("caps", {}).get("cpp", {}).get("probe") is False
       and c["caps"]["cpp"].get("ids") is False
       and c["caps"]["cpp"].get("constrain") is False
       and c["caps"]["mlx"].get("probe") is True
       and "ids" not in c["caps"]["mlx"],
       str(c.get("caps")))

    ck("`flash` asks for the buffer it refuses to pay for: the load line passes only "
       "model_path, n_ctx and verbose, leaving `logits_all` at the library's own "
       "False — so the probe refusal below is the shipped behaviour rather than a "
       "limit this file invented for its own test",
       c.get("load_args", {}).get("logits_all") is False
       and "logits_all" not in c.get("load_args", {}).get("kw", [])
       and c.get("load_args", {}).get("n_ctx") == 8192,
       str(c.get("load_args")))

    ck("the router's probe on that load REFUSES BY NAME, with the reason and the "
       "alternative in the sentence: no distribution exists without an "
       "(n_ctx x vocab) buffer of about 5.0 GB here, so a GGUF run is told to route "
       "with --backend mlx or pin the tier rather than dying on a ValueError three "
       "calls into the run",
       str(c.get("probe_refused", "")).startswith("NoFeature:")
       and "llama.cpp:" in c.get("probe_refused", "")
       and "logits_all" in c.get("probe_refused", "")
       and "5.0 GB" in c.get("probe_refused", "")
       and "--tier" in c.get("probe_refused", ""),
       str(c.get("probe_refused"))[:220])

    ck("...and the arithmetic under that guard is still real, on a handle built WITH "
       "the buffer: the probe reads the label ids out of the top-logprob table it "
       "was handed, in ONE generated token, and a label that is NOT in it scores "
       "below one that is — a missing label read as 0.0 would let the least-likely "
       "option win the route",
       len(c.get("probe", [])) == 2 and c["probe"][0] > c["probe"][1]
       and c["probe"][1] < -1e29 and c["probe"][0] == -0.2
       and c.get("probe_call", {}).get("max_tokens") == 1
       and c.get("probe_call", {}).get("logprobs") == 26,
       f"probe={c.get('probe')} call={c.get('probe_call')}")

    ck("--constrain on a backend with no per-step logits hook refuses BY NAME "
       "instead of generating without the contract the run asked for",
       str(c.get("constrain", "")).startswith("NoFeature:")
       and "--constrain" in c.get("constrain", ""), str(c.get("constrain"))[:160])

    ck("a LoRA adapter on a GGUF refuses too, because `--backend llama.cpp` with "
       "`--adapter` would otherwise run the base weights under the adapted arm's "
       "name",
       str(c.get("adapter", "")).startswith("NoFeature:"),
       str(c.get("adapter"))[:120])

    ck("a GGUF with no chat template refuses rather than falling back to a "
       "hand-written role layout",
       str(c.get("no_template", "")).startswith("NoFeature:"),
       str(c.get("no_template"))[:120])

    ck("a tier, a cached file name and an explicit path all resolve, and each "
       "through one function",
       c.get("path_is_file") is True and c.get("path_by_name") == "named.gguf"
       and c.get("tier") == "named.gguf",
       str({k: c.get(k) for k in ("path_is_file", "path_by_name", "tier")}))

    ck("a manifest that names a file no longer on disk says RE-PULL and names the "
       "missing file, rather than running a same-named file somebody dropped in by "
       "hand",
       c.get("stale_manifest") == ["True", True, True],
       str(c.get("stale_manifest"))[:200])

    ck("a `--small` path that does not exist is refused with the cache location "
       "and the pull command in it, not searched for",
       c.get("missing") is True, str(c.get("missing")))

    ck("`--backend llama.cpp` on a box that cannot import it refuses BEFORE the "
       "run, naming the backend that was asked for rather than the import that "
       "failed three calls later",
       str(c.get("forced_unavailable", "")).startswith("flash: this install")
       and "llama.cpp: import llama_cpp" in c.get("forced_unavailable", ""),
       str(c.get("forced_unavailable"))[:220])

    ck("...and a name that is not a backend at all says which ones exist",
       "is not a backend this build knows" in c.get("unknown", "")
       and "llama.cpp" in c.get("unknown", ""), str(c.get("unknown")))

    ck("`flash doctor`'s report lists BOTH backends with their availability and "
       "capabilities even with neither importable — the question a Linux user asks "
       "is what this box COULD run",
       [p["backend"] for p in c.get("report", [])] == ["mlx", "llama.cpp"]
       and all(p["available"] is False for p in c.get("report", []))
       and c["report"][1]["supports"]["constrain"] is False
       and c["report"][1]["supports"]["probe"] is False
       and c["report"][1]["supports"]["ids"] is False,
       str(c.get("report"))[:260])

    d = arm_armed(env)
    cpp, mlx = d.get("cpp", {}), d.get("mlx", {})

    ck("an armed checkpoint span on a backend that declares `ids: False` takes the "
       "ONE-SHOT call and never streams. On the installed library a streamed chunk "
       "carries no id and `flash.checkpoint.note` does `int(token_id)`, so the "
       "un-guarded line was a TypeError at the FIRST decoded token of every "
       "checkpointed `flash run-suite --backend llama.cpp` — a crash no fixture "
       "could see, because the fixture handed an id back",
       d.get("blocked") != "IMPORTED" and cpp.get("err") is None
       and cpp.get("calls") == ["generate"] and cpp.get("text") == "ONE-SHOT",
       str({k: cpp.get(k) for k in ("blocked", "calls", "text", "err")}))

    ck("...and the guarded run still answers and still writes its frame, so the "
       "guard is a path change and not a refusal. What it gives up is the "
       "token-by-token span, which is the declared limit of this backend: `--resume` "
       "restarts that task instead of continuing inside it",
       cpp.get("ids") == [] and cpp.get("frame_on_disk") is True
       and cpp.get("events") == [True],
       str({k: cpp.get(k) for k in ("ids", "frame_on_disk", "events")}))

    ck("...and the same guard leaves a backend that CAN deliver an id on the "
       "streaming path it was measured on: the MLX-shaped arm streams, notes both "
       "token ids and folds them into its checkpoint. Without this arm the guard "
       "would pass by turning streaming off for everybody",
       mlx.get("calls") == ["stream"] and mlx.get("ids") == [1, 2]
       and mlx.get("answer") == "ONE-SHOT" and mlx.get("carried") == 2,
       str({k: mlx.get(k) for k in ("calls", "ids", "answer", "carried", "err")}))



# --------------------------------------------------------------------- mutants

#: (label, [(path, old, new)]) — each restores a way of shipping the seam that
#: looks finished and is not.
MUTANTS = {
    "consumer_re_acquires_mlx": ("a module that is supposed to be backend-neutral "
                                 "learns MLX again — the drift the AST scan exists "
                                 "to catch, since nothing else in the run would "
                                 "notice",
                                 [("flash/tourney.py",
                                   "from __future__ import annotations\n",
                                   "from __future__ import annotations\n"
                                   "import mlx.core  # noqa: F401\n")]),
    "owner_ignores_the_handle": ("`owner()` forgets who loaded a model and asks the "
                                 "process instead, so a second backend in the same "
                                 "interpreter generates with the wrong library",
                                 [("flash/backend.py",
                                   "    return be if isinstance(be, Backend) else "
                                   "selected()\n",
                                   "    return selected()\n")]),
    "constrain_dropped_silently": ("a backend with no logits hook accepts "
                                   "--constrain and ignores it, so the run reports "
                                   "a contract it never enforced",
                                   [("flash/backend.py",
                                     "        if not self.supports.get(\"constrain\"):\n",
                                     "        if False:\n")]),
    "missing_label_scores_zero": ("a label absent from the top-logprob table reads "
                                  "as 0.0, which is the highest score a missing "
                                  "token can get and wins the route",
                                  [("flash/backend.py",
                                    "        NEG_INF = -1e30\n",
                                    "        NEG_INF = 0.0\n")]),
    "tier_ignores_the_manifest": ("a pulled tier resolves to any same-named file in "
                                  "the cache instead of the one that passed its "
                                  "SHA256",
                                  [("flash/backend.py",
                                    "    if ref in have:\n",
                                    "    if False:\n")]),
    "forced_backend_unprobed": ("`--backend` is trusted instead of checked, so the "
                                "refusal a user reads names an import rather than "
                                "the library they asked for",
                                [("flash/backend.py",
                                  "    ok, why = REGISTRY[key].available()\n"
                                  "    if not ok:\n"
                                  "        raise NoBackend(message(f\"{key}: "
                                  "{why}\"))\n",
                                  "    ok, why = REGISTRY[key].available(), \"\"\n")]),
    "gguf_tokenize_guessed_keyword": ("the GGUF tokenizer asks the library for a "
                                      "keyword it does not take — the defect a "
                                      "stand-in written to match this file could not "
                                      "see, and which turned every llama.cpp token "
                                      "count into `uncounted` and every routed GGUF "
                                      "run into a crash",
                                      [("flash/backend.py",
                                        "add_bos=bool(add_special_tokens),\n"
                                        + " " * 40 + "special=True))\n",
                                        "add_special=False))\n")]),
    "probe_guard_removed": ("the router's probe asks a model built without the logits "
                            "buffer for a distribution — the shipped load goes back to "
                            "raising the library's own ValueError out of a run that "
                            "should have been told to pin a tier",
                            [("flash/backend.py",
                              "        if not getattr(model.ll, \"_logits_all\", "
                              "False):\n",
                              "        if False:\n")]),
    "supports_reclaims_the_measured_refusals": ("the capability table claims a probe "
                                               "and streamed ids again, which is a "
                                               "declaration the installed library "
                                               "contradicts — and doctor prints it to "
                                               "the Linux user deciding what to "
                                               "install",
                                               [("flash/backend.py",
                                                 "    supports = {\"constrain\": "
                                                 "False, \"probe\": False, "
                                                 "\"adapter\": False,\n"
                                                 "                \"stream\": True, "
                                                 "\"embed\": False, \"ids\": "
                                                 "False}\n",
                                                 "    supports = {\"constrain\": "
                                                 "False, \"probe\": True, "
                                                 "\"adapter\": False,\n"
                                                 "                \"stream\": True, "
                                                 "\"embed\": False}\n")]),
    "armed_span_streams_without_ids": ("the loop streams whatever the backend gives, "
                                       "so an armed GGUF run calls `int(None)` in "
                                       "`checkpoint.note` and dies at the first token "
                                       "— which is the behaviour this repo shipped "
                                       "before the library was measured",
                                       [("flash/loop.py",
                                         "    streamable = be.supports.get(\"ids\") "
                                         "is not False\n",
                                         "    streamable = True\n")]),
    "streaming_off_for_everybody": ("the guard asks for an `ids` key instead of "
                                    "asking that it not be False, so MLX and every "
                                    "measured stub stop streaming: an armed run "
                                    "recovers nothing between tokens because nothing "
                                    "is written per token any more",
                                    [("flash/loop.py",
                                      "    streamable = be.supports.get(\"ids\") "
                                      "is not False\n",
                                      "    streamable = be.supports.get(\"ids\") "
                                      "is True\n")]),
}


def _apply(patches):
    """Patch every file this mutant touches, and return what to write back.

    Keyed by PATH, first text wins: a mutant with two patches on one file reads
    that file a second time AFTER the first patch landed, so saving per patch
    would restore the mid-mutant copy and leave the other half of the mutation in
    the tree — which is exactly how `selected()` ended up returning MLX
    unconditionally in a committed tree.
    """
    saved: dict[Path, str] = {}
    for rel, old, new in patches:
        p = ROOT / rel
        text = p.read_text()
        if text.count(old) != 1:
            for rp, rt in saved.items():
                rp.write_text(rt)
            raise AssertionError(f"mutant anchor is not unique in {rel}")
        saved.setdefault(p, text)
        p.write_text(text.replace(old, new))
    return list(saved.items())


def mutate(one: str | None = None) -> int:
    ran = failed = 0
    for name in ([one] if one else sorted(MUTANTS)):
        label, patches = MUTANTS[name]
        CHECKS.clear()
        saved = _apply(patches)
        try:
            run_gates()
        finally:
            for p, text in saved:
                p.write_text(text)
        bad = [l for l, ok, _ in CHECKS if not ok]
        ran += 1
        print(f"  {'ok  ' if bad else 'FAIL'} MUTATION: {label} -> "
              f"{len(bad)} check(s) fail" + (f"  {bad[0]}" if bad else ""))
        failed += not bad
    print(f"\nbackend-seam mutants: {ran - failed}/{ran} gates defeated by "
          "exactly their checks")
    return failed


def main(argv: list) -> int:
    if argv and argv[0] == "--mutant":
        return 1 if mutate(argv[1] if len(argv) > 1 else None) else 0
    run_gates()
    n = len(CHECKS)
    bad = [c for c in CHECKS if not c[1]]
    for label, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}"
              + (f"\n       [{detail}]" if detail and not ok else ""))
    print(f"\nbackend-seam checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
