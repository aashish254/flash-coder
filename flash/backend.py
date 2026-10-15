"""The generation seam: the one module that knows which model library answers.

Before this file, every generation path said `import mlx.core` / `from mlx_lm
import load` at the top of its own function. That is correct for a project whose
only backend is MLX, and it produced one defect a user actually walks into: on a
machine with no MLX — which is every Linux and Windows box, since MLX is
Apple-Silicon-only — `flash session` died with a raw `ModuleNotFoundError`
traceback out of `flash/loop.py`, while README.md and SPEC R-7.7 both promise a
human sentence naming MLX instead of a stack trace.

So this module owns two things:

1. **The refusal.** `require()` and `load()` raise `NoBackend` — a RuntimeError
   whose text is the whole answer: what is missing, the exact `flash doctor` line
   that says so, and the remedy for THIS platform. The CLI catches it once, in
   `main()`, so a command cannot be added that forgets to handle it, and a library
   caller still gets a named exception rather than a traceback from somebody
   else's import.
2. **The backends.** `Backend` is the interface the loop talks to — `load_model`,
   `generate`, `stream`, `probe`, `idle` (the unload), `profile`. `MLXBackend`
   holds the Metal path (its methods are the statements `flash/loop.py` used to
   run inline, moved rather than rewritten, so the measured hot-swap choreography
   is the same code in a new file); `LlamaCppBackend` holds a local GGUF through
   `llama-cpp-python`, which is what makes generation possible on the platforms MLX
   cannot reach. Which one answers is resolved here — `selected()`, from `--backend`
   or from what the box can actually import — and the handle carries who loaded it
   where that is safe to do so, so the oracle loop, the tournament, the ledger, the
   power governor and the confidence table never learn. They take a `(model,
   tokenizer)` pair and call the same three things on it that they always did:
   `apply_chat_template`, `encode`, and whatever the pair's backend generates.

What the seam does NOT claim: `--constrain`'s per-step token mask is MLX-only,
because the mask is a logits processor and llama-cpp-python's sampler takes no
per-step hook. That is declared in `profile()["supports"]` and refused in words
(`NoFeature`) rather than by a silent fallback — a run that quietly stopped
applying a contract the user asked for would be a measurement error, not a port.
The router's one-token forward pass is a `probe`, which MLX implements; on
llama.cpp it REFUSES, because the installed library returns no distribution for a
model built without its logits buffer and the buffer costs about 5.0 GB at this
backend's context — the measured reasons are in `LlamaCppBackend.supports`, and a
GGUF run pins a tier instead of routing. Training and the learned router's
embedding stay MLX-only (`adapter`, `embed`) because a LoRA is an MLX concept.

Nothing at module scope imports a third-party model library. That is a hard rule,
not a style choice: `benchmarks/backend_free_check.py` sweeps every submodule of
`flash` with the backend blocked and requires the numerator to equal the
denominator, so one top-level `import mlx.core` here would turn a passing gate
into a traceback.

What gates this file: `benchmarks/backend_message_check.py` runs every generating
CLI command under the shipped `--backend-free` shim and requires the sentence,
the exit code and the remedy; `benchmarks/backend_free_check.py` keeps the import
half; `benchmarks/backend_seam_check.py` runs a whole oracle-scored turn with MLX
BLOCKED at the interpreter and a stand-in backend serving it, which is the way to
prove the five consumers above are actually backend-agnostic without a second
library in the interpreter that answers. Its llama.cpp arm drives the shipping
class against a stand-in `llama_cpp` whose argument names were read off the
installed library — after the installed library rejected a name this file had
guessed, which is the lesson that arm now carries.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


class NoBackend(RuntimeError):
    """No model library can answer here. The text is the user-facing answer."""


class NoFeature(RuntimeError):
    """This backend cannot do the thing asked, said in the user's terms.

    Raised rather than degraded: `--constrain` with a backend that has no logits
    hook would otherwise report a run whose output contract was never enforced.
    """


def reason(exc: BaseException, name: str) -> str:
    """The one-line diagnosis of a failed import, shaped like doctor's.

    A `ModuleNotFoundError` says `No module named 'mlx_lm'`, which is true and
    useless; the sentence below names the package the remedy installs.
    """
    return f"import {name}: {type(exc).__name__}: {exc}"


def remedy() -> str:
    """What to type, for the platform actually running.

    Two answers because there are two different problems. On Apple Silicon MLX is
    installable and simply absent, so the remedy is an install line. Anywhere else no
    MLX wheel exists at all — but since R-7.18 that is no longer the end of the
    sentence, because there is a second backend to install. The old text said "no
    install line fixes this box", which was true when MLX was the only thing that
    could answer and is a lie about llama.cpp; so the non-Apple branch now prints the
    route that works and names the one thing it costs (a C++ compile, since the
    measured PyPI release is an sdist with no wheels).
    """
    if sys.platform == "darwin":
        return ("pip install 'flash-coder[mac]'          "
                "(in a checkout: pip install -e '.[mac]')")
    return ("MLX does not build outside Apple Silicon, so no install line fixes THAT "
            "route. The route that does work here: pip install 'flash-coder[local]' "
            "(it compiles llama.cpp, so it needs a C++ toolchain), then `flash "
            "models pull fast`, then any generating command with `--backend "
            "llama.cpp --small fast`. Until then the offline half — the whole "
            "verification battery, the graph, the oracle loop's grading — runs "
            "anyway")


def doctor_line() -> str:
    """`flash doctor`'s own backend sentence, read off this interpreter.

    Imported rather than reworded: a refusal that tells the reader to run doctor
    and then prints a paraphrase of what doctor says is two sources for one fact.
    """
    from flash import doctor
    return doctor.backend_line(doctor.backend())


def message(why: str) -> str:
    """The full refusal a user reads. One message, built here, printed by the CLI."""
    return "\n".join([
        "flash: this install has no generation backend, so it cannot answer.",
        "",
        f"  what is missing  {why}",
        f"  doctor says      {doctor_line()}",
        f"  remedy           {remedy()}",
        "",
        "  nothing left this machine, and no test was run. What this install can "
        "still",
        "  do — including re-running every published number offline:  flash doctor",
    ])


def require(*names: str) -> None:
    """Prove these import, or refuse the way doctor would say it.

    Called on the line above a generation path's own `import mlx.core`, which is
    what lets the hot-swap choreography in `flash.loop` stay exactly as measured
    while still being unable to print a traceback. The probe and the import are
    the same interpreter, so a pass here cannot be followed by a failure there.
    """
    import importlib
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as e:                              # noqa: BLE001
            raise NoBackend(message(reason(e, name))) from None


def load(repo: str, adapter: str | None = None):
    """`(model, tokenizer)` for one tier, from whichever backend answers.

    Every CLI model-loading line goes through here, which is what makes
    `--small`/`--big` backend-neutral: the argument is a repo id on MLX and a GGUF
    path, file name or tier word on llama.cpp, and each backend resolves its own
    shape. The pair it returns carries who loaded it, so a later `generate` cannot
    be handed to the wrong library even inside one process.
    """
    return selected().load_model(repo, adapter)


# --------------------------------------------------------------------- seam
#
# Everything below is the part that decides WHICH library answers. A `tokenizer`
# coming out of `load_model` is only required to behave like the HuggingFace one
# the loop already assumes — `apply_chat_template(messages, tokenize=False,
# add_generation_prompt=True)` and `encode(text, add_special_tokens=False)` — so
# `flash.trace.n_tokens` and `flash.decide._label_token_ids` keep working without
# knowing who wrote them. That shared shape is the whole reason the tournament,
# the ledger, the power governor and the confidence table can stay as they are.

@dataclass
class Generation:
    """One answer and the contract numbers that go with it, in neutral fields.

    The trace record for a generation (§33.6) is the same shape whichever library
    ran, because the loop reads these fields rather than reaching into a model or
    into a guard object it cannot type: a llama.cpp run and an MLX run differ in
    the numbers, never in the keys. The clock stays with the caller — `flash.loop`
    stamps the record, so one run has one timing source instead of a backend's and
    a loop's disagreeing about the same span.
    """
    text: str = ""
    constrained: bool = False
    mask_steps: int | None = None
    mask_breaches: int | None = None


class Backend:
    """What the loop is allowed to ask a model library.

    Seven verbs, and the loop knows nothing else about the library: is it usable
    here (`available`), what it can do (`profile`), put weights on the machine
    (`load_model`), turn messages into a prompt (`format`), answer one decode step
    cheaply for the router (`probe`), generate (`generate`, `stream`), and give the
    memory back between tiers (`idle`).

    `sampler` and `guard` are the two objects the backend owns and the loop only
    passes back: `sampler(temp)` returns whatever this library samples with (a
    callable for MLX, the temperature number itself for llama.cpp, whose API takes
    a float, `None` in both when the attempt is greedy), and `guard(contract, tok)`
    is R-4.2's per-step mask or `None`. Neither is inspected outside its own
    backend.
    """

    name = "abstract"
    #: What this backend can enforce. A capability the loop asks for that is not
    #: here is refused by name (`NoFeature`), never quietly dropped.
    supports: dict = {}

    def available(self) -> tuple[bool, str]:
        """(can run here, why not in the user's words)."""
        raise NotImplementedError

    def profile(self) -> dict:
        """Identity and capabilities, for `flash doctor` and the turn report.

        `why` is carried because a report that lists only what works cannot answer
        the question a Linux user is asking, which is what this box COULD run if it
        installed something — and which of the two candidates is the one to install.
        """
        ok, why = self.available()
        return {"backend": self.name, "available": ok, "why": why or None,
                "supports": dict(self.supports)}

    def load_model(self, ref: str, adapter: str | None = None):
        """`(model, tokenizer)` for one tier, this library's shape of `ref`."""
        raise NotImplementedError

    def format(self, tokenizer, messages: list[dict]) -> str:
        """Render a chat turn as the prompt string this model was trained on."""
        raise NotImplementedError

    def sampler(self, temp: float):
        """The library's own sampling object for this temperature, or None."""
        return None

    def guard(self, contract, tokenizer):
        """The R-4.2 per-step token mask, or None when no contract was asked for."""
        if contract is None:
            return None
        if not self.supports.get("constrain"):
            raise NoFeature(
                f"{self.name}: --constrain needs a logits hook at every decode "
                "step and this backend does not expose one. Run without "
                "--constrain, or with --backend mlx on Apple Silicon.")
        raise NotImplementedError

    def seed(self, seed: int) -> None:
        """Fix the RNG so a retry draws a DIFFERENT stream (see loop._generate)."""

    def generate(self, model, tokenizer, prompt: str, max_tokens: int,
                 sampler, guard) -> Generation:
        raise NotImplementedError

    def stream(self, model, tokenizer, prompt: str, max_tokens: int,
               sampler, guard):
        """Yield `(token_id, text_piece)` so a killed span can be resumed."""
        raise NotImplementedError

    def result(self, text: str, guard) -> Generation:
        """Close an answer the way a contract requires, and record what it cost.

        R-4.2's mask is finished by the guard rather than by the model: a block cut
        off at `max_tokens` is repaired deterministically, and the two counters the
        trace carries belong to the guard. Both generate and stream end here, so a
        checkpointed run and a one-shot run report the same keys.
        """
        if guard is None:
            return Generation(text=text)
        return Generation(text=text + guard.finish(), constrained=True,
                          mask_steps=guard.steps,
                          mask_breaches=guard.illegal_picks)

    def probe(self, model, tokenizer, prompt: str,
              ids: list[int]) -> list[float]:
        """Log-probabilities of these next-token ids after `prompt`, one prefill."""
        if not self.supports.get("probe"):
            raise NoFeature(
                f"{self.name}: the router needs a single forward pass over the "
                "prompt and this backend does not expose one.")
        raise NotImplementedError

    def idle(self) -> None:
        """Give everything back: the hot-swap between tiers runs through here."""


# ------------------------------------------------------- the GGUF cache location
#
# `flash models pull` (R-7.18) writes the bytes and the manifest; llama.cpp reads
# both. It lives here rather than in `flash.models` because the refusal a missing
# weight prints is a backend sentence, and because a download module that imports
# this one is the dependency direction that keeps `flash doctor` answerable with
# neither library installed.

#: Deliberately NOT the Hugging Face cache: a GGUF is one file the user chose, and
#: a download that lands next to four repo snapshots nobody can read is not a
#: cache anybody can manage. `FLASH_GGUF_DIR` moves it for a box whose home is
#: small — the same reason `HF_HOME` exists.
GGUF_DIR = Path(os.environ.get("FLASH_GGUF_DIR")
               or str(Path.home() / ".cache" / "flash" / "gguf"))

#: tier word -> the file `flash models pull` verified against its SHA256. A tier
#: name resolves through this and ONLY through this, so `--small fast` can never
#: quietly run a same-named file somebody dropped into the cache by hand.
MANIFEST = "flash-gguf.json"


def manifest() -> dict[str, str]:
    """What has actually been pulled, or {} when nothing has.

    Absent, unreadable and empty are the same answer on purpose: a manifest that
    cannot be parsed says "nothing verified here" and the caller refuses, rather
    than this module deciding that a half-read JSON is close enough to a weight.
    """
    import json
    try:
        raw = json.loads((GGUF_DIR / MANIFEST).read_text())
    except Exception:                                     # noqa: BLE001
        return {}
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def gguf_path(ref: str) -> str:
    """The GGUF behind a llama.cpp `--small`/`--big`: a path, a tier, or a name.

    Three shapes because a user has three things to say: "run this file I already
    have", "run the fast tier", "run the file I pulled and remember by name". A
    path that was given and does not exist is refused rather than searched for,
    because the reader of a typo'd `--small` deserves to hear about the typo.
    """
    p = Path(ref).expanduser()
    if p.is_file():
        return str(p)
    have = manifest()
    if ref in have:
        f = GGUF_DIR / have[ref]
        if f.is_file():
            return str(f)
        raise NoBackend(f"flash: the manifest says tier `{ref}` is "
                        f"{f}, and that file is not on disk. Re-pull it: "
                        f"`flash models pull {ref}`.")
    if not (p.is_absolute() or "/" in ref):
        f = GGUF_DIR / ref
        if f.is_file():
            return str(f)
    raise NoBackend(
        "\n".join([
            f"flash: no GGUF behind `{ref}`, so --backend llama.cpp has no model "
            "to run.",
            "",
            f"  looked in    {GGUF_DIR}   (FLASH_GGUF_DIR moves it)",
            f"  pulled       {', '.join(sorted(have)) or 'nothing yet'}",
            "",
            "  remedy       flash models pull fast     (or name a file: "
            "--small /path/to/model.gguf)",
        ]))


class MLXBackend(Backend):
    """The Metal path. These bodies are `flash/loop.py`'s own statements, moved.

    The choreography they belong to — greedy first attempt, sampled retries with a
    per-attempt seed, the checkpointed span that streams because a recovery has
    to write its checkpoint per token, the `clear_cache()` between tiers that
    keeps 7B + 30B off a 32GB machine at once — is all measured and cited, so it
    was relocated verbatim rather than rethought. What changed is only WHO may say
    `import mlx.core`: this class, `flash/learn.py`'s embedding probe, and
    `flash/train.py`.

    `mlx_lm.generate`/`stream_generate` are imported inside each call rather than
    cached on the instance, which is what lets `checkpoint_resume_check.py` patch
    `mlx_lm.stream_generate` on the module and have that patch land here. The loop
    is the same length; one of its lines just moved into a method.
    """

    name = "mlx"
    supports = {"constrain": True, "probe": True, "adapter": True,
                "stream": True, "embed": True}

    def available(self) -> tuple[bool, str]:
        import importlib
        for mod in ("mlx.core", "mlx_lm"):
            try:
                importlib.import_module(mod)
            except Exception as e:                          # noqa: BLE001
                return False, reason(e, mod)
        return True, ""

    def profile(self) -> dict:
        out = Backend.profile(self)
        try:
            from flash import doctor
            out["library"] = doctor.backend()
        except Exception:                                   # noqa: BLE001
            pass
        return out

    def load_model(self, ref: str, adapter: str | None = None):
        require("mlx.core", "mlx_lm")
        from mlx_lm import load as _load
        return _load(ref) if not adapter else _load(ref, adapter_path=adapter)

    def format(self, tokenizer, messages: list[dict]) -> str:
        return tokenizer.apply_chat_template(messages, tokenize=False,
                                             add_generation_prompt=True)

    def guard(self, contract, tokenizer):
        if contract is None:
            return None
        from flash.grammar import ConstrainedSampler
        return ConstrainedSampler(contract, tokenizer)

    def seed(self, seed: int) -> None:
        require("mlx.core")
        import mlx.core as mx
        mx.random.seed(seed)

    def sampler(self, temp: float):
        if temp <= 0:
            return None
        require("mlx_lm.sample_utils")
        from mlx_lm.sample_utils import make_sampler
        return make_sampler(temp=temp)

    def generate(self, model, tokenizer, prompt: str, max_tokens: int,
                 sampler, guard) -> Generation:
        if not max_tokens:
            return self.result("", guard)
        require("mlx_lm")
        from mlx_lm import generate
        out = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                       verbose=False, sampler=sampler,
                       logits_processors=[guard] if guard else None)
        return self.result(out, guard)

    def stream(self, model, tokenizer, prompt: str, max_tokens: int,
               sampler, guard):
        require("mlx_lm")
        from mlx_lm import stream_generate
        for r in stream_generate(model, tokenizer, prompt=prompt,
                                 max_tokens=max_tokens, sampler=sampler,
                                 logits_processors=[guard] if guard else None):
            yield r.token, r.text

    def probe(self, model, tokenizer, prompt: str,
              ids: list[int]) -> list[float]:
        """One prefill, the last position's log-probs, restricted to `ids`.

        This is `flash.decide`'s forward pass, which used to be the one place in
        the decision path that said `import mlx.core` itself; the arithmetic it
        feeds (the restricted softmax over the option labels) stayed in `decide`,
        because it is a decision, not a library call.
        """
        require("mlx.core")
        import mlx.core as mx
        tokens = mx.array([tokenizer.encode(prompt, add_special_tokens=False)])
        logits = model(tokens)[:, -1, :]                    # [1, vocab] — one pass
        logits = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        mx.eval(logits)
        return [float(logits[0, i]) for i in ids]

    def idle(self) -> None:
        require("mlx.core")
        import mlx.core as mx
        mx.clear_cache()


class _LlamaModel:
    """The loop's `model` for a GGUF: the handle, and who loaded it.

    A facade rather than the raw `Llama` object for one reason and one only: this
    is the handle that carries `_flash_backend`, so a later `generate` cannot be
    handed to the wrong library. MLX does not get the same treatment on purpose —
    an `mlx.nn.Module` is a pytree node, and putting a non-array object in its
    `__dict__` is a change to somebody else's data structure that `mx.eval` would
    pay for. `owner()` below covers that case with the process's own choice, which
    on an MLX box is the same answer.

    `handle()` is deliberately thin: the completion dict llama-cpp-python returns
    is a documented API shape, not a private one, so `flash` reads it rather than
    pretending it is backend-neutral. `trace.n_tokens` and `decide._label_token_ids`
    are the two places that need a real token id out of it, and both get it through
    `_GgufTokenizer`.
    """

    def __init__(self, ll, path: str, be: "Backend"):
        self.ll = ll
        self.path = path
        self._flash_backend = be

    def handle(self, prompt: str, **kw):
        return self.ll(prompt, **kw)


class _GgufTokenizer:
    """The two methods flash asks of a tokenizer, over llama-cpp-python's API.

    `encode`/`decode` are the byte-level surface llama.cpp actually is: its
    tokenizer takes bytes and returns bytes, and a Chinese or emoji-containing
    prompt round-trips through `encode` only if the caller passes bytes. The
    loop's own calls pass a `str` and get ids, which is the shape
    `flash.trace.n_tokens` counts and `decide._label_token_ids` asks for.

    `apply_chat_template` uses the template the GGUF itself carries
    (`tokenizer.chat_template`, Jinja) — the same one `create_chat_completion`
    would have used — because a router or a ledger that scored a prompt rendered
    by a different template than the one the model was fine-tuned on is a wrong
    measurement, not a cheap one. A GGUF with no template refuses rather than
    falling back to a hand-written role layout.
    """

    def __init__(self, model: _LlamaModel):
        self._m = model
        self._template = (model.ll.metadata or {}).get("tokenizer.chat_template")

    @property
    def name(self) -> str:
        return self._m.path

    def encode(self, text: str, add_special_tokens: bool = False, **ignored) -> list[int]:
        # MEASURED against the installed library, not against this file's own guess:
        # `inspect.signature(Llama.tokenize)` on llama-cpp-python 0.3.36 is
        # `(text: bytes, add_bos: bool = True, special: bool = False)`. There is no
        # `add_special` keyword, so calling with one raised `TypeError` out of every
        # token count. Observed twice on the real library: `flash.trace.n_tokens` takes
        # that path inside a `try`, so a session answered `Mars` and billed itself
        # `uncounted=1/1 generations` instead of 81+2 tokens; and the same shape is what
        # `flash.decide._label_token_ids` calls with no handler at all — the pre-fix
        # probe run showed `TypeError` coming out of that path, and the fixed code gets
        # past the labels to the refusal `probe` raises below. `add_bos` is the real
        # knob and it DEFAULTS TO True, so the loop's `add_special_tokens=False` has to
        # be said out loud: flash counts and compares prompt strings, and a BOS the
        # caller did not ask for would make
        # every count one token too high. `special=True` is what lets a chat template's
        # own markers tokenize as single tokens instead of as literal text.
        return list(self._m.ll.tokenize(text.encode("utf-8"),
                                        add_bos=bool(add_special_tokens),
                                        special=True))

    def decode(self, ids: list[int], skip_special_tokens: bool = False) -> str:
        raw = self._m.ll.detokenize(list(ids))
        return (raw.decode("utf-8", "replace")
                if isinstance(raw, (bytes, bytearray)) else str(raw))

    def apply_chat_template(self, messages: list[dict], tokenize: bool = False,
                            add_generation_prompt: bool = True, **ignored) -> str:
        if not self._template:
            raise NoFeature(
                f"llama.cpp: {self._m.path} carries no tokenizer.chat_template, "
                "so flash cannot render a prompt the way this model was trained. "
                "Pull a chat-tuned GGUF, or use --backend mlx.")
        import jinja2
        out = jinja2.Environment().from_string(self._template).render(
            messages=messages, add_generation_prompt=add_generation_prompt)
        return out if not tokenize else self.encode(out)


class LlamaCppBackend(Backend):
    """A local GGUF through llama-cpp-python: the path MLX cannot reach.

    MLX is Apple-Silicon-only, so on Linux and Windows — and on any AMD/Intel box
    — this is the only way `flash` generates at all. The weights are a file, not a
    repo id: `--small/--big` name a tier (`fast` / `brain`), a cached file, or a
    path, and `gguf_path` above resolves all three, which is what keeps the oracle
    loop, the ledger and the power governor addressing the same two tiers they
    always did.

    MEASURED HERE, AND WHAT WAS FOUND — 2026-10-15. The paragraph this one replaced
    said not one token had been decoded through this class on this machine. That is
    no longer true: `flash models pull fast` moved the pinned 4.68 GB GGUF, and a
    `flash session --backend llama.cpp` turn on it answered in prose — 1.55 s to
    load the weights at n_ctx 8192, 81 prompt tokens and 2 completion tokens
    counted by the tokenizer above, 0.92 s of generation, and no API key anywhere in
    the path. llama-cpp-python itself ships no wheel for this interpreter, so it was
    built from source in a throwaway virtual environment (the blocker is `cmake`,
    which this box did not have either, and `pip install cmake ninja` inside that
    environment is what made the build possible).

    Driving the real library found three defects that this class's own fixtures had
    been hiding, and each one is now a check in `backend_seam_check.py` with the
    library's real argument names: `Llama.tokenize` takes `add_bos`, not
    `add_special`, so every token COUNT raised `TypeError` — the answer still arrived
    while the bill for it read `uncounted=1/1 generations`, because the count is not on
    the generation path and `trace.n_tokens` turns a raising tokenizer into `None`
    rather than a zero; a `logprobs` request on a model built without `logits_all`
    raises `ValueError`, so `probe` was a capability this backend does not have, and on
    the installed library `flash decide --backend llama.cpp` answers that by printing
    the refusal below and exiting 2; and its stream chunks carry no token id — measured
    as `(None, 'M'), (None, 'ars')` — which is what an armed checkpoint appends with
    `int()`. What stays unproven is the library's arithmetic — one Mac, one
    pinned file, one quantisation is a fact about that pair, and `profile()` below
    keeps `verified: false` because `flash doctor` decodes nothing and so cannot
    earn that word.
    """

    name = "llama.cpp"
    #: MEASURED on the installed library (0.3.36) rather than declared from this
    #: file's own design: `constrain` is False because llama-cpp-python's sampler
    #: takes no per-step DFA hook; `probe` is False because the only way to ask it
    #: for a distribution is `logprobs=…`, which raises
    #: `ValueError: logprobs is not supported for models created with
    #: logits_all=False`, and turning `logits_all` on allocates an
    #: `(n_ctx, n_vocab)` float32 buffer — ~5.0 GB at this file's 8192-token context
    #: and a 152,064-token vocab, which flash will not pay to route a question;
    #: `ids` is False because its stream chunks carry no token id without that same
    #: buffer, and `flash.checkpoint.note` counts on an int.
    supports = {"constrain": False, "probe": False, "adapter": False,
                "stream": True, "embed": False, "ids": False}

    #: The context the GGUF is loaded with. flash's prompts are the whole repo
    #: skeleton plus an oracle file, so the llama.cpp default of 512 truncates the
    #: task rather than the answer; 8192 is the smallest window that holds a
    #: §22-sized prompt, and `FLASH_GGUF_N_CTX` raises it on a box with the RAM.
    DEFAULT_N_CTX = 8192

    def __init__(self, n_ctx: int | None = None):
        self.n_ctx = int(n_ctx if n_ctx is not None
                         else os.environ.get("FLASH_GGUF_N_CTX",
                                             self.DEFAULT_N_CTX))

    def available(self) -> tuple[bool, str]:
        import importlib
        try:
            importlib.import_module("llama_cpp")
        except Exception as e:                                # noqa: BLE001
            return False, reason(e, "llama_cpp")
        return True, ""

    def profile(self) -> dict:
        out = Backend.profile(self)
        try:
            import importlib.metadata as md
            out["library"] = f"llama-cpp-python {md.version('llama-cpp-python')}"
        except Exception:                                     # noqa: BLE001
            out["library"] = "llama-cpp-python: version unreadable"
        out["gguf_cache"] = str(GGUF_DIR)
        out["n_ctx"] = self.n_ctx
        out["verified"] = False
        return out

    def load_model(self, ref: str, adapter: str | None = None):
        if adapter:
            raise NoFeature(
                "llama.cpp: a LoRA adapter is applied when MLX builds the model, "
                "and llama-cpp-python loads GGUF weights as they are. Pull an "
                "adapted GGUF, or run this arm with --backend mlx.")
        require("llama_cpp")
        from llama_cpp import Llama
        path = gguf_path(ref)
        handle = Llama(model_path=path, n_ctx=self.n_ctx, verbose=False)
        model = _LlamaModel(handle, path, self)
        return model, _GgufTokenizer(model)

    def format(self, tokenizer, messages: list[dict]) -> str:
        return tokenizer.apply_chat_template(messages, tokenize=False,
                                             add_generation_prompt=True)

    def sampler(self, temp: float):
        return None if temp <= 0 else float(temp)

    def _temperature(self, sampler) -> float:
        return 0.0 if sampler is None else float(sampler)

    def generate(self, model, tokenizer, prompt: str, max_tokens: int,
                 sampler, guard) -> Generation:
        if not max_tokens:
            return self.result("", guard)
        out = model.handle(prompt, max_tokens=max_tokens,
                           temperature=self._temperature(sampler), echo=False,
                           seed=getattr(self, "_seed", None))
        return self.result(out["choices"][0]["text"], guard)

    def stream(self, model, tokenizer, prompt: str, max_tokens: int,
               sampler, guard):
        # `delta` is the chat-completions shape; the completion API this call uses
        # returns `text` per chunk. Both are read because llama-cpp-python's
        # streaming dict has carried either, and a resumed checkpoint that lost a
        # token to a field-name guess would silently re-decode it.
        for chunk in model.handle(prompt, max_tokens=max_tokens,
                                  temperature=self._temperature(sampler),
                                  stream=True, echo=False):
            choice = chunk["choices"][0]
            piece = choice.get("text") or (choice.get("delta") or {}).get(
                "content") or ""
            if not piece:
                continue
            lp = choice.get("logprobs") or {}
            ids = lp.get("tokens") or []
            yield (ids[0] if ids else None), piece

    def probe(self, model, tokenizer, prompt: str,
              ids: list[int]) -> list[float]:
        """One generated token with the top log-probs, read for the labels only.

        `max_tokens=1, echo=False` makes llama.cpp run the prefill and hand back
        the distribution over the FIRST decoded position, which is exactly the
        slice `decide` restricted-softmaxes over. Its top-k is a dict keyed by the
        token PIECE, so each label id is detokenized to find its entry; a label
        that is not in the top-k is read as NEG_INF rather than 0.0, because
        `max(raw)` downstream would otherwise pick the label llama.cpp least
        wanted to say.

        The guard above the arithmetic is MEASURED, not defensive: on the shipped
        load (`Llama(model_path, n_ctx, verbose)`, which leaves `logits_all` at the
        library's own False) any `logprobs=…` request raises
        `ValueError: logprobs is not supported for models created with
        logits_all=False`, and `logits_all=True` allocates an
        `(n_ctx, n_vocab)` float32 buffer — ~5.0 GB at 8192 x 152,064 — which is a
        price flash does not pay to route a question. A GGUF run therefore refuses
        by name; the arithmetic below stays live for a load that does ask for the
        buffer, and `benchmarks/backend_seam_check.py` covers both arms.
        """
        if not getattr(model.ll, "_logits_all", False):
            raise NoFeature(
                "llama.cpp: this backend loads a GGUF without `logits_all`, so "
                "llama-cpp-python returns no distribution to route on — and turning "
                "it on costs an (n_ctx x vocab) buffer, about 5.0 GB at this "
                "backend's 8192-token context. Route with --backend mlx, or pin the "
                "tier (--tier small / --tier big) so nothing has to be routed.")
        NEG_INF = -1e30
        out = model.handle(prompt, max_tokens=1, temperature=0.0, logprobs=26,
                           echo=False)
        table = (out["choices"][0].get("logprobs") or {}).get("top_logprobs") or [{}]
        table = table[0] if isinstance(table, list) else table
        if not table:
            raise NoFeature(
                "llama.cpp: this build returned no top-logprob table, so the "
                "router has no scores. Run with --tier big (or --tier small) to "
                "skip routing, or use --backend mlx.")
        scores = []
        for tid in ids:
            piece = tokenizer.decode([tid])
            hit = table.get(piece)
            if hit is None:
                hit = table.get(piece.lstrip())
                if hit is None:
                    hit = next((v for k, v in table.items()
                                if k.strip() == piece.strip()), NEG_INF)
            scores.append(float(hit))
        return scores

    def seed(self, seed: int) -> None:
        # llama-cpp-python takes the seed per completion, so remembering it here is
        # the whole implementation — but it is remembered per BACKEND INSTANCE,
        # which is why `generate` reads it off `self`.
        self._seed = int(seed)

    def idle(self) -> None:
        # A GGUF handle frees its own context when the object is dropped, which is
        # what `del model, tok` in the loop already does. `flash.power` still gets
        # its hot-swap point either way — the two tiers never run at once.
        pass


# ------------------------------------------------------------------- selection
#
# The order is not alphabetical and not a preference: MLX first because every
# number this repo publishes — the §6 battery, the market comparison, the router
# fit — was measured on it, so a machine that has both must not have a second
# backend quietly become the default of a re-run. `--backend` overrides it, and
# the override is checked rather than assumed.

ORDER = ("mlx", "llama.cpp")
REGISTRY: dict[str, Backend] = {"mlx": MLXBackend(), "llama.cpp": LlamaCppBackend()}

_FORCED: str | None = None


def use(name: str | None) -> "Backend | None":
    """Honor `--backend` for this process; `None`/`auto` leaves the choice automatic.

    `auto` deliberately resolves NOTHING here. The automatic choice is made at the
    first model call, not at dispatch, because `flash doctor` and the offline
    battery have no model to load and must stay answerable on a box with no backend
    at all — a dispatch-time `selected()` would turn R-7.6's report into a refusal.
    A name this build does not know, and a name the box cannot run, both refuse
    rather than falling back to what is installed: a run that silently swapped
    libraries would print the same trace keys with different arithmetic behind
    them, and no number in the output would say so.
    """
    global _FORCED
    if name in (None, "", "auto"):
        _FORCED = None
        return None
    key = {k.lower(): k for k in REGISTRY}.get(str(name).lower())
    if key is None:
        raise NoBackend(f"flash: `--backend {name}` is not a backend this build "
                        f"knows. Known: {', '.join(ORDER)}, or 'auto'.")
    ok, why = REGISTRY[key].available()
    if not ok:
        raise NoBackend(message(f"{key}: {why}"))
    _FORCED = key
    return REGISTRY[key]


def selected() -> Backend:
    """The backend this process generates with.

    With `--backend` given, that is the answer — `use()` already proved the box can
    run it. Otherwise the first entry in `ORDER` that imports wins, and a machine
    with neither gets the refusal with BOTH reasons in it: once there is a second
    backend to have tried, "MLX is not installable here" is only half of what the
    user needs to read.
    """
    if _FORCED:
        return REGISTRY[_FORCED]
    misses = []
    for key in ORDER:
        be = REGISTRY[key]
        ok, why = be.available()
        if ok:
            return be
        misses.append(f"{key}: {why}")
    raise NoBackend(message("; ".join(misses)))


def owner(model) -> Backend:
    """Who loaded this model, falling back to the process's own choice.

    The fallback is not laziness. `benchmarks/*_check.py` hands the loop stub
    model objects it built itself, and a stub carries no backend marker; those
    vectors are MLX vectors, so `selected()` is the right answer for them, and a
    real GGUF handle always carries its marker so the loop never asks the wrong
    library to generate.
    """
    be = getattr(model, "_flash_backend", None)
    return be if isinstance(be, Backend) else selected()


def reset() -> None:
    """Forget a forced backend — for the gates, which run many choices in one
    interpreter and must not leak one test's `--backend` into the next."""
    global _FORCED
    _FORCED = None


def report() -> list[dict]:
    """Every backend's profile, in `ORDER` — the shape `flash doctor` prints.

    Both are always reported, available or not. A report that only listed what
    works cannot answer the question a Linux user is actually asking, which is what
    this box COULD run if it installed something.
    """
    out = []
    for key in ORDER:
        be = REGISTRY[key]
        try:
            out.append(be.profile())
        except Exception as e:                              # noqa: BLE001
            out.append({"backend": be.name, "available": False,
                        "error": f"{type(e).__name__}: {e}",
                        "supports": dict(be.supports)})
    return out
