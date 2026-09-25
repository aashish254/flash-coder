"""Constrained decoding (PLAN §33.3, SPEC R-4.2): make malformed output
structurally impossible.

Two layers, both testable without a model:

1. `Contract` — a character-level DFA over the *generated text* for exactly
   the protocols `flash.harness` parses: the multi-WRITER file set (a
   `# file: <name>` header, then one fenced block per file) and the
   single-file fenced answer. A sampler restricted to pieces that keep this
   DFA alive cannot emit a fence without a header, a header without a body,
   prose or a markdown heading before the first header, or a path outside the
   required file set — the four shapes the mw suite had to learn to tolerate.
2. `Masks` — compiles the DFA into one vocabulary mask per *position* (the
   id table is built once per tokenizer, ~0.2 s, and compiled positions are
   shared by every contract over it); `ConstrainedSampler` is the
   `logits_processors`-compatible callable that applies it at each step.

Masking policy, stated exactly, because "impossible" needs a definition:
  * a token is allowed iff walking its characters leaves the DFA alive from
    the *current* position — which remembers the pending backtick count, the
    header literal's progress and which names are still unwritten — or iff it
    is a chat/role marker the template strips before parsing. The mask is
    therefore exact, not a superset: the selftest proves no offered piece is
    ever refused, and no offered position is ever a dead end;
  * end-of-turn is allowed only where stopping still parses: after the last
    required block, or inside a code body — where `complete()` closes the
    fence, so the emitted files stay well-formed and the miss is only
    completeness. Mid-header a stop is masked: it would corrupt the parse and
    buys the model nothing;
  * two shapes a logit mask cannot prevent: running out of token budget (the
    cut lands wherever it lands) and a stop inside a body, which is legal but
    may leave the file set partial. The census counts those separately.

Not sound next to a draft model: speculative decoding samples ahead and can
reject tokens the DFA has already consumed, so the contract's state and the
text would diverge. There is no draft path in `flash.loop` today, so nothing
combines them yet — when P3 adds one it has to refuse the pairing explicitly,
and SPEC P3 carries that as a condition of the arm.

    python -m flash.grammar --selftest              # offline, tokenizer only
    python -m flash.grammar --census [--n 100]      # live: violation census
    python -m flash.grammar --overhead              # live: ms/step of the mask
"""
from __future__ import annotations

import copy
import re
import time
import sys
from dataclasses import dataclass, replace

FENCE = "```"
HEADER = "# file: "
_PATH_CH = re.compile(r"[\w][\w./-]*$")
_NAMED_FILE = re.compile(r'File "([\w./-]+\.py)"')
_MARKER = re.compile(r"<\|[^|]*\|>")
_STATES = ("s0", "s1", "s2", "s3", "s4", "s5", "s6")


# --------------------------------------------------------------- the contract
@dataclass
class Contract:
    """DFA for one output protocol. Positions are mutable; `feed` clones so a
    probe never disturbs the caller's contract.

    `mode="multi"` — one or more `# file: <name>` + fenced-block pairs.
    `mode="fence"` — a single fenced block, no header.
    `names` — the required file set. The path is matched against it character
    by character, so an unintended file cannot even be *started*, and a name
    already written leaves the set. Empty = any safe relative `.py` path.
    `stops` — the marker texts this tokenizer ends a turn with. A turn
    terminator is the sampler's stop decision, never answer text, so the walk
    has to know it by name rather than try to parse it.
    """

    mode: str = "multi"
    names: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    state: str = "s0"
    lit: str = ""
    path: str = ""
    ticks: int = 0
    blocks: int = 0
    stops: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # A mask is keyed on the position, and a position carries `left`; a
        # list would make the key unhashable.
        self.names = tuple(self.names)
        self.left = tuple(self.left)
        self.stops = tuple(self.stops)
        if self.names and not self.left:
            self.left = self.names

    def clone(self) -> "Contract":
        return copy.copy(self)

    def at(self, state: str) -> "Contract":
        """A copy pinned at `state` — the entry point for mask compilation."""
        c = self.clone()
        c.state, c.lit, c.path, c.ticks = state, "", "", 0
        return c

    # ------------------------------------------------------------- DFA walk
    def feed(self, text: str) -> "Contract | None":
        """Advance over one piece's characters; None when the piece is illegal.

        This is the whole rule, applied to the text as the harness will see it:
        no piece is excused, chat markers included. A turn terminator is not
        answer text and never arrives here — `absorb` handles it — and anything
        else shaped like a marker really does land in the file, so it has to
        survive the DFA or it is not a valid answer.
        """
        c = self.clone()
        for ch in text:
            if not c._consume(ch):
                return None
        return c

    def absorb(self, text: str) -> "Contract | None":
        """Advance over one *sampled* piece, excusing the turn terminator.

        The mask is compiled for pieces; this is the walk that matches it. The
        two must agree, or a model that stops where it is allowed to stop reads
        as a contract violation — found live, when the first census reported
        every masked answer malformed for exactly that reason.
        """
        return self.clone() if self.ends_turn(text) else self.feed(text)

    def ends_turn(self, text: str) -> bool:
        """Is this piece the chat template's turn terminator?

        mlx stops the instant it samples one and decodes the rest with
        skip_special_tokens, so its text never reaches the harness. The mask
        offers it exactly where `stop_ok` holds; the walk must not then read it
        as answer text and call the answer malformed.
        """
        stops = self.stops or _STOP_TEXTS
        return text in stops or text.rstrip() in stops

    def _try(self, text: str) -> bool:
        """Probe `text` from this position and leave the contract untouched.

        Mask compilation runs this ~50k times per position, so unlike `feed`
        it allocates nothing: the mutable fields are saved and put back. One
        DFA, one transition table — `_consume` stays the single authority.
        """
        if self.ends_turn(text):            # the stop, wherever it is offered
            return True
        held = (self.state, self.lit, self.path, self.ticks, self.blocks,
                self.left)
        ok = True
        for ch in text:
            if not self._consume(ch):
                ok = False
                break
        (self.state, self.lit, self.path, self.ticks, self.blocks,
         self.left) = held
        return ok

    def _consume(self, ch: str) -> bool:
        s = self.state
        if s == "s0":                        # before the first block
            if ch.isspace():
                return True
            if self.mode == "multi":
                # only the header literal may open an answer: a bare fence
                # here is exactly the 'fence without a header' bug
                if ch != "#" or not self.can_open:
                    return False
                self.state, self.lit = "s1", "#"
                return True
            if ch == "`":
                self.state, self.ticks = "s3", 1
                return True
            return False
        if s == "s1":                        # the '# file: ' literal
            want = HEADER[len(self.lit):]
            if not want or ch != want[0]:
                return False
            self.lit += ch
            if self.lit == HEADER:
                self.state, self.path = "s2", ""
            return True
        if s == "s2":                        # path, then a newline
            if ch == "\n":
                return self._close_path()
            nxt = ch if not self.path else self.path + ch
            if not _PATH_CH.match(nxt):
                return False
            if self.names and not any(nm.startswith(nxt) for nm in self.left):
                return False
            self.path = nxt
            return True
        if s == "s3":                        # opening fence: three backticks
            if ch != "`":
                return False
            self.ticks += 1
            if self.ticks >= 3:
                self.state = "s4"
            return True
        if s == "s4":                        # optional language tag
            if ch == "\n":
                self.state, self.ticks = "s5", 0
                return True
            return ch.isalnum()
        if s == "s5":                        # the code body
            if ch == "`":
                self.ticks += 1
                if self.ticks >= 3:
                    self.state, self.ticks = "s6", 0
                    self.blocks += 1
                return True
            self.ticks = 0
            return True
        if s == "s6":                        # between blocks / trailing text
            if ch.isspace():
                return True
            if self.mode == "multi" and ch == "#" and self.can_open:
                self.state, self.lit = "s1", "#"
                return True
            return False
        return False

    def _close_path(self) -> bool:
        p = self.path
        if not p.endswith(".py") or ".." in p or "/" in p:
            return False
        if self.names and p not in self.left:
            return False                 # unknown, or already written
        self.left = tuple(n for n in self.left if n != p)
        self.state, self.path, self.ticks = "s3", "", 0
        return True

    # ------------------------------------------------------------- predicates
    @property
    def can_open(self) -> bool:
        """May another block start here? A header that cannot name a required
        file any more is a dead end: it has no legal path to finish with."""
        return bool(self.left) if self.names else True

    def can_stop(self) -> bool:
        """May the turn end here? Only after every required file has been
        written and its block closed: the harness runs the tests against the
        whole set, so a partial answer is not a finished one. With no declared
        set (`mode="fence"`) one closed block is enough."""
        return self.state == "s6" and self.blocks >= 1 and not self.left

    def stop_ok(self) -> bool:
        """May the sampler end the turn here? Either the answer is already
        complete, or it is a body that `complete()` can close — the two
        positions where stopping cannot produce a malformed file set. Anywhere
        else the end-of-turn id is masked out."""
        return self.can_stop() or self.truncated()

    def truncated(self) -> bool:
        """True when the turn ended with a block still open."""
        return self.state == "s5"

    def complete(self) -> str:
        """Deterministic remainder that makes a cut-short answer parseable."""
        return "\n" + FENCE if self.state == "s5" else ""

    def snapshot(self) -> str:
        return f"{self.state} path={self.path!r} blocks={self.blocks}"

    # ---------------------------------------------------------- constructors
    @classmethod
    def file_set(cls, names: list[str] | None = None) -> "Contract":
        got = tuple(names or ())
        return cls(mode="multi", names=got, left=got)

    @staticmethod
    def names_from_prompt(prompt: str) -> tuple[str, ...]:
        """The file set a task declares itself (`File "ring.py": ...`)."""
        return tuple(dict.fromkeys(_NAMED_FILE.findall(prompt)))

    @property
    def key(self) -> str:
        return self.mode + ":" + ",".join(self.names)


def constraint_of(task: dict, names: list[str] | None = None) -> Contract | None:
    """The contract a task admits, or None when none can be named — the loop
    then generates freely (I-7: a degraded signal, never a broken one)."""
    if task.get("multi"):
        got = list(names or []) or list(Contract.names_from_prompt(task["prompt"]))
        if not got:
            return None
        return Contract(mode="multi", names=tuple(got), left=tuple(got))
    if task.get("test"):
        return Contract(mode="fence")
    return None


# ----------------------------------------------------------- token-level masks
def vocab_size(tokenizer) -> int:
    """Logit width: ids in the table plus the added special ids."""
    n = int(getattr(tokenizer, "vocab_size", 0) or 0)
    special = [int(i) for i in (getattr(tokenizer, "all_special_ids", ()) or ())]
    eos = getattr(tokenizer, "eos_token_id", None)
    if isinstance(eos, int):
        special.append(eos)
    return max(n, (max(special) + 1) if special else 0)


def vocab_texts(tokenizer, n: int) -> list[str]:
    """Plain text of every vocabulary id, from the tokenizer itself."""
    rust = getattr(getattr(tokenizer, "_tokenizer", None), "_tokenizer", None)
    if rust is not None:
        try:
            return list(rust.decode_batch([[i] for i in range(n)],
                                          skip_special_tokens=False))
        except Exception:                       # pragma: no cover
            pass
    return [tokenizer.decode([i], clean_up_tokenization_spaces=False)
            for i in range(n)]


_TABLES: dict = {}


_STOP_TEXTS: list[str] = []   # terminator texts, with the table


def _table(tokenizer):
    """(width, id -> text, marker ids, end-of-turn ids, first characters,
    first character -> ids), built once per tokenizer and shared."""
    key = id(tokenizer)
    if key not in _TABLES:
        n = vocab_size(tokenizer)
        texts = vocab_texts(tokenizer, n)
        markers = {i for i, t in enumerate(texts)
                   if t and _MARKER.fullmatch(t)}
        markers.update(int(i) for i in (getattr(tokenizer, "all_special_ids", ())
                                        or ()) if int(i) < n)
        eos = getattr(tokenizer, "eos_token_id", None)
        by_first: dict[str, list[int]] = {}
        for i, t in enumerate(texts):
            if t:
                by_first.setdefault(t[0], []).append(i)
        # In a code body only a backtick can move the DFA, so the ids holding
        # one are the only ones that need a walk (a few hundred of 150k).
        ticked = [i for i, t in enumerate(texts) if "`" in t]
        # Which markers this tokenizer ends a turn with. mlx stops at the
        # end-of-turn id and decodes with skip_special_tokens, so that piece —
        # and only that piece — is absent from the text the harness parses.
        # Everything else shaped like a marker lands in the answer and has to
        # survive the DFA there.
        turns = {int(eos)} if isinstance(eos, int) else set()
        turns |= {i for i, t in enumerate(texts)
                  if _MARKER.fullmatch(t) and t.strip()
                  in ("<|" + "im_end" + "|>", "<|" + "endoftext" + "|>")}
        _STOP_TEXTS[:] = sorted({texts[i] for i in turns}
                                | {texts[i].strip() for i in turns})
        _TABLES[key] = (n, texts, markers,
                        [int(eos)] if isinstance(eos, int) else [],
                        sorted(by_first), by_first, ticked, sorted(turns))
    return _TABLES[key]

def _position(c: Contract):
    """The DFA's full position — what a mask must be keyed on.

    `state` alone is not enough: mid-header and mid-path pieces depend on how
    much literal has already gone by and on which files remain, and a piece in
    a code body depends on how many backticks are already pending — from a
    body one character short of a fence, '```' closes the block *and* leaves a
    stray backtick, which is illegal. The body never depends on the file set.
    """
    if c.state == "s5":
        return (c.mode, c.state, c.ticks)
    if c.state == "s2":
        return (c.mode, c.state, c.path, c.left)
    if c.state == "s3":
        return (c.mode, c.state, c.ticks)
    return (c.mode, c.state, c.lit, c.left)


# Compiled positions, shared by every mask over the same vocabulary: the tight
# states are re-derived task after task, and a task must not pay for a scan a
# previous task already did. Keyed on the identity of the table's own text
# list, so the cache cannot outlive (or precede) the vocabulary it describes.
_CACHE: dict[int, dict] = {}


class Masks:
    """Vocabulary ids admitted at each DFA position, compiled lazily.

    Enumeration is indexed by first character: a position admits only pieces
    whose first character survives there, which turns a 150k scan into a scan
    of a few hundred candidates for the tight positions (header, path, fence)
    and, for the loose ones, a scan that skips every token the DFA cannot
    disturb. `allowed` is the single source of truth for what the sampler may
    emit: the DFA's set, plus the end-of-turn id exactly where stopping is
    legal or repairable.
    """

    def __init__(self, contract: Contract, tokenizer):
        import mlx.core as mx
        self.tokenizer = tokenizer
        (n, self.texts, self.all_markers, self.eos, self.firsts,
         self.by_first, self.ticked, turns) = _table(tokenizer)
        # The mask-immune family is EXACTLY the pieces the decoder never shows
        # the harness: the turn terminator (and its vocabulary twins). A role
        # header, a vision block or any other marker is ordinary vocabulary that
        # can be sampled into the answer and would then sit in the file the
        # harness parses — so it stays under the DFA. Claiming all of them as
        # "template control" was the first build's mistake: it let the mask and
        # the walk disagree about the same bytes.
        self.turns = turns
        self.markers = turns
        contract.stops = tuple(sorted({self.texts[i] for i in turns}
                                     | {self.texts[i].strip() for i in turns}))
        self.n = n
        self._cache = _CACHE.setdefault(id(self.texts), {})
        self._arr: dict = {}
        self._free: dict = {}
        self._mx = mx

    def ids_for(self, c: Contract) -> list[int]:
        key = _position(c)
        got = self._cache.get(key)
        if got is None:
            got = self._cache[key] = self._enumerate(c)
        return got

    def unrestricted(self, c: Contract) -> bool:
        """True when this position admits the whole vocabulary: the sampler
        can then hand the logits over untouched. Decided from the compiled
        mask, never from the state name — a body one backtick short of a
        fence does *not* admit everything."""
        key = _position(c)
        if key not in self._free:
            self._free[key] = len(self.allowed(c)) >= self.n
        return self._free[key]

    def _enumerate(self, c: Contract) -> list[int]:
        if c.state == "s5":
            # Body: everything that carries no backtick survives by
            # construction; only the ticked ids can reach a closing fence.
            ids = (set(range(self.n)) - set(self.ticked)
                   - {i for i in self.all_markers
                      if i not in set(self.turns or ())
                      and not c.ends_turn(self.texts[i])})
            ids.update(i for i in self.ticked if c._try(self.texts[i]))
        else:
            ids = {i for ch in self.firsts if c._try(ch)
                   for i in self.by_first.get(ch, ()) if c._try(self.texts[i])}
        return sorted(ids)

    def allowed(self, c: Contract) -> list[int]:
        """Ids the sampler may emit at this position, end-of-turn policy
        applied. `stop_ok` is a function of the position, so one cache serves
        both the list and the array."""
        ids = set(self.ids_for(c)) | set(self.turns)
        if c.stop_ok():
            return sorted(ids)
        # A stop that would not parse is not offered: mid-header, mid-path,
        # mid-fence. Anywhere else the model has to finish the structure it
        # started, because a truncated one is the answer the harness cannot run.
        return sorted(ids - set(self.eos) - set(self.turns))

    def allow(self, c: Contract, width: int | None = None):
        """Boolean logits mask for the position.

        `width` is the model's logit width. Some checkpoints pad it past the
        vocabulary (live find on the census: 152064 against a 151657 token
        table), and a mask narrower than the logits does not broadcast — it
        just kills the run. The extra columns address no token at all, so they
        stay illegal.
        """
        if width is None:
            width = self.n
        key = (_position(c), width)
        if key not in self._arr:
            arr = self._mx.zeros((width,), dtype=self._mx.bool_)
            ids = self.allowed(c)
            ids = [i for i in ids if i < width] if width < self.n else ids
            if ids:
                arr[ids] = True
            self._arr[key] = arr
        return self._arr[key]


class ConstrainedSampler:
    """A `logits_processors` callable that keeps generation inside `contract`.

    mlx calls it once per decode step with (tokens so far, logits); the first
    call's array still holds the last *prompt* token, so that one only sets
    the baseline and is never fed to the DFA.

    End-of-turn is offered exactly where stopping keeps the answer
    well-formed (`Masks.allowed`): after the last required block, or inside a
    body that `finish` can close. Everywhere else the model is pushed to
    finish the structure it started — `max_tokens` remains the one way an
    answer can still be cut short, and the census reports those separately.
    """

    def __init__(self, contract: Contract, tokenizer):
        import mlx.core as mx
        self.tokenizer = tokenizer
        self.contract = contract.clone()
        self.masks = Masks(contract, tokenizer)
        self._penalty = mx.array(-1e4, dtype=mx.float32)
        self._turns = frozenset(self.masks.turns)
        self._cast: dict = {}          # penalty, materialised once per dtype
        self._base: int | None = None
        self._fed = 0
        self.steps = 0
        self.hook_ms = 0.0            # wall time spent inside the hook itself
        self.illegal_picks = 0
        self.first_breach = ""        # ids/text/position of the first refusal

    # ---------------------------------------------------------- bookkeeping
    def allowed_ids(self) -> list[int]:
        """Vocabulary ids admissible right now, end-of-turn policy included."""
        return self.masks.allowed(self.contract)

    def consume(self, ids: list[int]) -> bool:
        """Move the DFA over sampled ids. False = refused (the mask's job).

        One id per step is the normal case and takes a path that allocates
        nothing. The piece is `texts[i]` — the very string the mask was
        compiled over — so mask and walk cannot disagree on a byte-level token
        the way a re-decode would. A turn terminator is not walked at all: it
        ends the turn, `Masks.allowed` offered it because `stop_ok` held, and
        the decoder never shows it to the harness.
        """
        if not ids:
            return True
        if len(ids) == 1:
            i = ids[0]
            nxt = True if i in self._turns else self.contract.absorb(
                self.masks.texts[i])
            if nxt is True:
                return True
        elif self._turns & set(ids):
            return True
        else:
            nxt = self.contract
            for k in ids:
                nxt = nxt.absorb(self.masks.texts[k])
                if nxt is None:
                    break
        if nxt is None:
            self.illegal_picks += 1
            if not self.first_breach:
                # Named, not counted: "99 breaches" is a statistic, this is a
                # diagnosis, and a count without an instance invites a confident
                # wrong explanation of it.
                self.first_breach = (" ".join(f"{k}:{self.masks.texts[k]!r}"
                                              for k in ids)
                                     + " at " + self.contract.snapshot())
            return False
        self.contract = nxt
        return True

    def __call__(self, tokens, logits):
        import mlx.core as mx
        t0 = time.perf_counter()
        try:
            return self._mask(tokens, logits)
        finally:
            # Attributed to the hook, and reported by the census as a share of
            # the generation. The end-to-end tok/s gap between the arms is the
            # number SPEC R-4.2 gates on; this is the number that says whether
            # the gap is the hook's doing at all — the arithmetic on a
            # pre-materialised array is not, and only a real decode step knows
            # what reading the sampled id back costs.
            self.hook_ms += (time.perf_counter() - t0) * 1000.0

    def _mask(self, tokens, logits):
        import mlx.core as mx
        if self._base is None:
            # The first call's array is the whole prompt and nothing else: the
            # baseline is its length, not its last index. Reading the last
            # prompt token as generated text was the live census's 49 mask
            # breaches — a prompt ending in '.' fed the DFA a piece no answer
            # may start with, and the refusal was counted against the mask.
            self._base = len(tokens)
        new = tokens[self._base + self._fed:]
        k = len(new)
        if k:
            self.consume(new.tolist())
            self._fed += k
        if (logits.shape[-1] == self.masks.n
                and self.masks.unrestricted(self.contract)):   # nothing to forbid
            self.steps += 1
            return logits
        self.steps += 1
        pen = self._cast.get(logits.dtype)
        if pen is None:
            pen = self._cast[logits.dtype] = self._penalty.astype(logits.dtype)
        return mx.where(self.masks.allow(self.contract, logits.shape[-1]),
                        logits, pen)

    def finish(self) -> str:
        """Companion text to append once the turn has ended."""
        return self.contract.complete()


    def finish(self) -> str:
        """Companion text to append once the turn has ended."""
        return self.contract.complete()


# ----------------------------------------------------------------- live census
def _sample(model, tokenizer, prompt: str, max_tokens: int, seed: int,
            contract: Contract | None):
    """One sampled generation: (text, seconds, tokens, sampler or None)."""
    import time
    import mlx.core as mx
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    mx.random.seed(seed)
    sampler = ConstrainedSampler(contract, tokenizer) if contract else None
    t0 = time.perf_counter()
    out = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                   verbose=False, sampler=make_sampler(temp=0.7),
                   logits_processors=[sampler] if sampler else None)
    dt = time.perf_counter() - t0
    if sampler is not None:
        out = out + sampler.finish()
    return out, dt, len(tokenizer.encode(out, add_special_tokens=False)), sampler


def conformance(text: str, names: list[str]) -> tuple[bool, str]:
    """Did the answer itself obey the protocol, or did the tolerant parser in
    `extract_files` have to rescue it? That distinction is the whole point of
    R-4.2, so the vector measures the shape, not just the recovery."""
    from flash.harness import extract_files
    for stop in _STOP_TEXTS:               # what the decoder leaves out
        text = text.replace(stop, "")
    c = Contract(mode="multi", names=tuple(names)).feed(text)
    if c is None:
        return False, "malformed"
    if not c.can_stop():
        return False, "unclosed"
    files = extract_files(text, expected=list(names))
    if set(files) != set(names) or not all(v.strip() for v in files.values()):
        return False, "file set"
    return True, "ok"


def run_overhead(model: str | None = None, iters: int = 300,
                 step_ms: float | None = None) -> int:
    """The mask's per-step cost, measured against the decode step it joins.

    The census cannot answer the latency question on its own: the constraint
    also changes what the model writes (a masked answer goes straight into the
    protocol instead of narrating around it), so end-to-end tok/s compares two
    different workloads. This measures the arithmetic the sampler adds to a
    step, and evaluates it — an un-evaluated mlx expression costs nothing to
    build and everything to run, which is how the first version of this
    reported 0.002 ms/step.

    `step_ms` is the model's own decode step; it defaults to this box's
    measured free-arm rate. No weights are loaded: the tokenizer and the
    arithmetic are the whole cost.
    """
    import mlx.core as mx

    tok = _load_tokenizer()
    if tok is None:
        print("[overhead] no tokenizer in the HF cache — nothing to measure")
        return 1
    c = Contract(mode="multi", names=("ring.py", "stats.py"))
    samp = ConstrainedSampler(c, tok)
    width = 152064                             # this checkpoint's padded width
    logits = mx.zeros((1, width), dtype=mx.float32)
    penalty = mx.array(-1e4, dtype=mx.float32)
    positions = (("a header position", c.at("s0")),
                 ("a path position", c.feed(HEADER)),
                 ("a clean body position", c.at("s5")))
    out = []
    for label, pos in positions:
        arr = samp.masks.allow(pos, width)     # compiled once, then cached
        mx.eval(arr)
        t0 = time.perf_counter()
        for _ in range(iters):
            mx.eval(mx.where(arr, logits, penalty))
        apply_ms = (time.perf_counter() - t0) / iters * 1000
        # Compile cost, measured by evicting it: the shared position cache
        # would otherwise report the ~0 ms of a lookup.
        pk = _position(pos)
        samp.masks._cache.pop(pk, None)
        samp.masks._arr.pop((pk, width), None)
        t0 = time.perf_counter()
        samp.masks.allow(pos, width)
        compile_ms = (time.perf_counter() - t0) * 1000
        out.append((label, pos.state, apply_ms, compile_ms))
    base = step_ms or 1000.0 / 23.5            # measured free-arm rate, this box
    print(f"[overhead] {iters} evaluated applications per position, "
          f"logits {width} wide, model step ~{base:.1f} ms")
    worst = 0.0
    for label, state, apply_ms, compile_ms in out:
        worst = max(worst, apply_ms)
        print(f"[overhead] {label:22s} ({state}) apply {apply_ms:6.3f} ms/step"
              f"  = {apply_ms / base * 100:4.1f}%   "
              f"compile {compile_ms:6.1f} ms once")
    print(f"[overhead] worst position: {worst:.3f} ms/step = "
          f"{worst / base * 100:.1f}% of the decode step (budget 5%)")
    return 0 if worst / base * 100 <= 5.0 else 1


def run_census(n: int = 100, model: str | None = None,
               tasks_file: str | None = None, max_tokens: int = 1500,
               seed0: int = 1000, attempts: str = "both") -> int:
    """Sample the same prompts with and without the mask: violation counts per
    arm and the throughput cost.

    Both arms are measured by the same yardstick — `conformance` on the emitted
    text — so the numbers are comparable. The gate is the structural half:
    `malformed` (a DFA-dead answer) and mask breaches MUST be 0 for the
    constrained arm. `unclosed`/`file set` are completeness misses: a stop
    inside a body or an exhausted budget, which no logit mask can forbid.
    """
    from flash import harness
    from mlx_lm import load

    repo = model or "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
    tasks = harness.load_tasks(tasks_file)
    jobs = [t for t in tasks if t.get("multi")]
    if not jobs:
        print("no multi-file tasks — nothing to census")
        return 1
    arms = ("free", "constrained") if attempts == "both" else (attempts,)
    print(f"[census] {repo}\n[census] {n} generations over {len(jobs)} "
          f"prompt(s), max_tokens={max_tokens}, temp=0.7, arms={arms}")
    stats: dict[str, dict] = {}
    loaded = None
    for arm in arms:
        if loaded is None:
            loaded = load(repo)
        model, tok = loaded
        viol = trunc = illegal = rec = 0
        hook_ms = gen_s = 0.0
        breach_example = ""
        why: dict[str, int] = {}
        toks = secs = 0.0
        for i in range(n):
            t = jobs[i % len(jobs)]
            names = list(Contract.names_from_prompt(t["prompt"]))
            c = (Contract(mode="multi", names=tuple(names))
                 if arm == "constrained" else None)
            text, dt, nt, samp = _sample(model, tok, t["prompt"], max_tokens,
                                         seed0 + i, c)
            secs += dt
            toks += nt
            if samp:
                hook_ms += samp.hook_ms
            gen_s += dt
            cut = bool(samp and samp.contract.truncated())
            trunc += cut
            if samp and samp.illegal_picks > illegal:
                illegal = samp.illegal_picks
                breach_example = breach_example or samp.first_breach
            ok, reason = conformance(text, names)
            if not ok:
                viol += 1
                why[reason] = why.get(reason, 0) + 1
            # The number that actually broke the mw suite: could the tolerant
            # parser recover exactly the file set the task asked for?
            from flash.harness import extract_files
            got = extract_files(text, expected=names)
            rec += int(set(got) == set(names) and all(v.strip()
                                                     for v in got.values()))
            print(f"  [{arm:11s}] {i + 1:3d}/{n} {t['id']:16s} "
                  f"{'ok ' if ok else 'BAD'} {reason:9s} rec={rec}/{i + 1} "
                  f"{nt / max(dt, 1e-9):5.1f} tok/s {nt:4d} tok")
        stats[arm] = dict(viol=viol, rec=rec, trunc=trunc, illegal=illegal,
                          hook_pct=100.0 * (hook_ms / 1000.0) / max(gen_s, 1e-9),
                          breach_example=breach_example, why=why,
                          tps=toks / max(secs, 1e-9))
    for arm in arms:
        s = stats[arm]
        print(f"\n[census] {arm:11s}: {s['viol']}/{n} contract violations "
              f"{s['why']}  |  {s['trunc']} ended inside a block  |  "
              f"{s['illegal']} mask breaches  |  "
              f"{s['rec']}/{n} recoverable by the parser  |  {s['tps']:.1f} tok/s  |  "
              f"hook {s['hook_pct']:.1f}% of wall (incl. the model step "
              "the read-back waits for)")
        if s.get("breach_example"):
            print(f"[census] first breach: {s['breach_example']}")
    free, con = stats.get("free"), stats.get("constrained")
    if free and con and free["tps"]:
        print(f"[census] mask cost : {(1 - con['tps'] / free['tps']) * 100:+.1f}% "
              f"throughput (budget 5%)")
    if not con:
        return 0 if free and free["viol"] == 0 else 1
    return 0 if (con["why"].get("malformed", 0) == 0
                 and con["illegal"] == 0) else 1


# -------------------------------------------------------------------- selftest
def _reference(task: dict) -> str:
    """A task's stored solution, rendered in contract form."""
    return "\n\n".join(f"{HEADER}{p}\n{FENCE}python\n{s}{FENCE}"
                       for p, s in task["solution"].items())


def _load_tokenizer():
    """The fast tier's tokenizer, weights never touched (offline battery)."""
    import glob
    from pathlib import Path
    try:
        from mlx_lm.tokenizer_utils import load as load_tok
        snaps = glob.glob(str(Path.home() / ".cache/huggingface/hub"
                              "/models--mlx-community--Qwen2.5-Coder-7B-"
                              "Instruct-4bit/snapshots/*"))
        return load_tok(Path(snaps[0])) if snaps else None
    except Exception as e:                       # pragma: no cover
        print("   tokenizer unavailable:", type(e).__name__, e)
        return None


def run_selftest() -> int:
    import json
    import random
    from pathlib import Path
    from flash.harness import extract_files

    root = Path(__file__).resolve().parent.parent
    checks: list[tuple[str, bool, str]] = []

    def ck(name: str, cond: bool, note: str = "") -> None:
        checks.append((name, bool(cond), str(note)))

    def rows(p):
        return [json.loads(l) for l in Path(p).read_text().splitlines()
                if l.strip()]

    multi = rows(root / "benchmarks/tasks/mw_tasks.jsonl")
    m0 = rows(root / "benchmarks/tasks/m0_tasks.jsonl")
    fence = Contract(mode="fence")
    t = multi[0]
    names = tuple(t["solution"])
    ref = _reference(t)

    # ---- 1-3 the contract accepts what ships as the reference answer
    ck("mw references walk their contract",
       all(Contract(mode="multi", names=tuple(x["solution"])).feed(_reference(x))
           is not None for x in multi), f"{len(multi)} tasks")
    ends = [Contract(mode="multi", names=tuple(x["solution"])).feed(_reference(x))
            for x in multi]
    ck("mw references end stoppable with every block closed",
       all(e is not None and e.can_stop() and e.blocks == len(x["solution"])
           for e, x in zip(ends, multi)))
    ck("m0 references walk the fence contract",
       all(fence.feed(f"{FENCE}python\n{x['solution']}{FENCE}") is not None
           for x in m0), f"{len(m0)} tasks")
    ck("a bare unfenced answer does not (the fence is required)",
       fence.feed(m0[0]["solution"]) is None)

    # ---- 4-9 the shapes the harness had to learn to tolerate are illegal
    bad = {
        "a heading before the fence":
            "### file: " + names[0] + "\n" + ref.replace(
                HEADER + names[0] + "\n", "", 1),
        "a fence with no header": ref.replace(HEADER + names[0] + "\n", "", 1),
        "a header with no fence": ref.replace(FENCE + "python\n", "", 1),
        "prose before the first header": "Sure, here you go:\n" + ref,
        "a path that escapes the root": ref.replace(
            HEADER + names[0], HEADER + "../evil.py", 1),
        "a file outside the required set": ref.replace(
            HEADER + names[0], HEADER + "other.py", 1),
    }
    for label, text in bad.items():
        c = Contract(mode="multi", names=names).feed(text)
        ck("rejects " + label, c is None, "" if c is None else c.snapshot())
    unknown = Contract(mode="multi", names=names).feed(HEADER + "oth")
    ck("an unknown name dies as soon as it stops matching", unknown is None,
       "" if unknown is None else unknown.snapshot())

    # ---- 10-11 truncation is open, and complete() repairs it
    cut = ref[:ref.rindex("\n" + FENCE, 10)]
    c = Contract(mode="multi", names=names).feed(cut)
    ck("truncation is legal-but-open, not illegal",
       c is not None and c.truncated() and not c.can_stop(),
       "" if c else "dead")
    ck("complete() turns a cut answer back into a closed block",
       c is not None and set(extract_files(cut + c.complete(),
                                           expected=list(names))) <= set(names),
       repr(c.complete() if c else None))

    # ---- 12-13 re-emitting a written file, and name bookkeeping
    twice = ref + "\n\n" + HEADER + names[0] + "\n" + FENCE + "\nx=1\n" + FENCE
    ck("refuses to re-emit a file already written",
       Contract(mode="multi", names=names).feed(twice) is None)
    one = Contract(mode="multi", names=names).feed(
        HEADER + names[1] + "\n" + FENCE + "\nx=1\n" + FENCE)
    ck("a partial file set is legal but cannot end the turn",
       one is not None and one.left == (names[0],)
       and not one.can_stop() and not one.stop_ok(),
       "" if one else "dead")

    # ---- 14-15 tokenization independence
    def split_walk(text: str, con: Contract, rng) -> bool:
        i = 0
        while i < len(text):
            k = min(len(text), i + rng.randint(1, 7))
            nxt = con.feed(text[i:k])
            if nxt is None:
                return False
            con, i = nxt, k
        return True

    rng = random.Random(7)
    ck("40 random splits per mw reference stay legal",
       all(split_walk(_reference(x),
                      Contract(mode="multi", names=tuple(x["solution"])), rng)
           for x in multi for _ in range(40)))
    ck("an illegal answer stays illegal under every split",
       all(split_walk(bad["prose before the first header"],
                      Contract(mode="multi", names=names), random.Random(s))
           is False for s in range(40)))

    # ---- 16-29 token masks: the real tokenizer, no weights loaded
    tok = _load_tokenizer()
    if tok is None:
        ck("a tokenizer is available for the mask checks", False,
           "HF cache miss — the mask checks are the offline half of R-4.2")
    else:
        import mlx.core as mx
        t0 = time.perf_counter()
        n, texts, markers, eos, firsts, by_first, ticked, turns = _table(tok)
        table_s = time.perf_counter() - t0
        con0 = Contract(mode="multi", names=names)
        samp = ConstrainedSampler(con0, tok)
        t1 = time.perf_counter()
        start_ids = samp.masks.ids_for(con0.at("s0"))
        body_ids = samp.masks.ids_for(con0.at("s5"))
        build_s = time.perf_counter() - t1
        ck("the id table covers the logit width", len(texts) == n,
           f"n={n}, {table_s:.2f}s")
        ck("eos is a marker the DFA itself would reject",
           bool(eos) and texts[eos[0]].startswith("<|"),
           texts[eos[0]] if eos else "no eos id")
        fence_ids = {i for i, x in enumerate(texts) if x.strip() == FENCE}
        ck("no fence token is legal before the first header",
           bool(fence_ids) and not (fence_ids & set(start_ids)),
           f"{len(fence_ids)} excluded")
        # ---- the mask, excluding template control: at a tight position the
        # only things the DFA should admit are answer-shaped pieces (markers
        # ride along everywhere by design, and the template strips them).
        def answer_shaped(ids):
            return [i for i in ids if i not in markers]

        odd = sorted(x for x in {texts[i] for i in answer_shaped(start_ids)}
                     if not x.isspace() and not HEADER.startswith(x.lstrip()))
        ck("only whitespace and a header prefix can open the answer",
           not odd, f"{len(start_ids)} ids, odd={odd[:3]}")
        # The fast body path rests on a property of the DFA: in a code body
        # only a backtick can move it. Checked against a direct walk of every
        # ticked id plus a wide stride of the unticked ones.
        body_set = set(body_ids)
        probe = list(ticked) + list(range(0, n, 37))
        # Only the terminator is excused. A role marker is answer text, and it
        # has to survive the DFA where it would be sampled — the body included.
        L, S = "im_" + "end", "user"                  # built: no chat marker here
        role = ["<|" + S + "|>", "<|" + L + "|>assistant\n"]
        body_ids = set(body_ids)
        ck("a role marker is refused even inside a code body",
           all(texts[i] not in role for i in body_ids),
           f"{len(role)} shapes checked")
        ck("the terminator is the only mask-immune piece, and it is excused",
           set(turns) <= set(samp.masks.markers)
           and con0.ends_turn(texts[turns[0]])
           and not con0.ends_turn("<|" + S + "|>")
           and con0.absorb(texts[turns[0]]) is not None
           and con0.feed(texts[turns[0]]) is None,
           f"turns={[texts[i] for i in turns]}")
        ck("the body mask matches a direct DFA walk",
           all((con0.at("s5").feed(texts[i]) is not None) == (i in body_set)
               for i in probe),
           f"{len(probe)}/{n} ids walked, {len(body_set)} admitted")

        def path_ok(pos, ids):
            pool = pos.left or names
            return all(any(nm.startswith(pos.path + texts[i]) for nm in pool)
                       for i in answer_shaped(ids))

        at_path = con0.feed(HEADER)
        p_ids = samp.masks.ids_for(at_path) if at_path else []
        ck("path pieces are restricted to the required names",
           any(p_ids) and path_ok(at_path, p_ids), f"{len(p_ids)} ids")
        deep = at_path.feed(names[0][0]) if at_path else None
        d_ids = samp.masks.ids_for(deep) if deep else []
        ck("mid-path pieces track the remaining names",
           any(d_ids) and path_ok(deep, d_ids), f"{len(d_ids)} ids")
        done = con0.feed(_reference(t))
        a_ids = samp.masks.ids_for(done) if done else []
        ck("once every file is written, no new block can open",
           not any(texts[i].lstrip().startswith("#")
                   for i in answer_shaped(a_ids)),
           f"{len(a_ids)} ids, none of them a header")
        # The exact bug this pins: a bare '#' offered after the last file leads
        # to a path position with nothing left to name — a dead end the model
        # can neither leave nor stop out of.
        ck("a finished file set offers only whitespace and the stop",
           done is not None and all(texts[i].isspace()
                                    for i in answer_shaped(a_ids))
           and done.stop_ok(), f"{len(answer_shaped(a_ids))} answer ids")
        ck("mask compilation is cheap enough to amortize", build_s < 2.0,
           f"{build_s:.2f}s for the start and body positions")
        logits = mx.ones((n,), dtype=mx.float32)
        masked = samp(mx.array([7], dtype=mx.uint32), logits)
        un = int(mx.sum(mx.where(masked > -1e3, mx.ones_like(masked),
                                 mx.zeros_like(masked))).item())
        ck("a masked step leaves exactly the allowed ids unpenalised",
           un == len(samp.allowed_ids()), f"{un} vs {len(samp.allowed_ids())}")
        # The census's own crash: this checkpoint's logits are 152064 wide for
        # a 151657-token vocabulary, and a mask sized to the table does not
        # broadcast against them.
        wide = mx.ones((n + 4096,), dtype=mx.float32)
        wmask = samp(mx.array([7], dtype=mx.uint32), wide)
        wun = int(mx.sum(mx.where(wmask > -1e3, mx.ones_like(wmask),
                                  mx.zeros_like(wmask))).item())
        ck("a logit width padded past the vocabulary is masked, not crashed",
           wmask.shape == wide.shape and wun == len(samp.allowed_ids()),
           f"{wide.shape[-1]} columns over a {n}-token table")
        ck("end-of-turn is masked where a stop would not parse",
           all(i not in samp.allowed_ids() for i in samp.masks.eos),
           f"position {samp.contract.state}, {len(samp.allowed_ids())} ids")
        closed = ConstrainedSampler(con0, tok)
        closed.contract = done
        inbody = ConstrainedSampler(con0, tok)
        inbody.contract = con0.at("s5")
        ck("end-of-turn survives after the last block and inside a live body",
           all(closed.contract.stop_ok() and inbody.contract.stop_ok()
               and set(samp.masks.eos) <= set(x.allowed_ids())
               for x in (closed, inbody)), str(samp.masks.eos))
        # The live census's 49 mask breaches, pinned offline: the first call's
        # array is the whole prompt, and its last token is not generated text.
        # Reading it as answer text fed the DFA a piece no answer may start
        # with — a prompt ending in '.' produced a refusal the mask was blamed
        # for.
        pre = ConstrainedSampler(con0, tok)
        prompt_ids = tok.encode('File "ring.py": a ring buffer.',
                                add_special_tokens=False)
        pre(mx.array(prompt_ids, dtype=mx.uint32), logits)
        after_prompt = pre.contract.snapshot()
        hash_id = [i for i in pre.allowed_ids() if texts[i] == "#"]
        pre(mx.array(prompt_ids + hash_id, dtype=mx.uint32), logits)
        ck("the prompt is never walked as answer text",
           pre.illegal_picks == 0 and after_prompt.startswith("s0")
           and pre.contract.state == "s1", f"prompt call -> {after_prompt}")

        # In a clean body the mask refuses only the template markers. They are
        # answer text as far as the harness is concerned: a vision block or a
        # second role header landing mid-file is exactly the corruption this
        # module exists to prevent, so nothing wider is excused here.
        off = set(inbody.allowed_ids())
        refused = [i for i in range(n) if i not in off]
        ck("a clean body step refuses exactly the template markers",
           refused and all(_MARKER.fullmatch(texts[i]) for i in refused)
           and set(refused) == set(inbody.masks.all_markers) - set(inbody.masks.turns),
           f"{len(refused)} refused, {len(off)} of {n} offered")

        # The bug this pins: a body that has already spent a backtick does NOT
        # admit the whole vocabulary — '```' there closes the fence and leaves
        # a stray tick, which is how the first mask breached the DFA.
        near = con0.feed(HEADER + names[0] + "\n" + FENCE + "\nx = 1\n`")
        near_ids = samp.masks.allowed(near) if near else []
        ck("a body with a backtick pending masks the overshooting pieces",
           near is not None and near.ticks == 1
           and not inbody.masks.unrestricted(near)
           and all(texts[i] != FENCE for i in near_ids),
           f"{len(near_ids)} of {n} ids still legal")

        # ---- adversarial walks: only ever pick ids the mask itself offered.
        # A loose position is biased toward the pieces that can close it, and a
        # walk ends the moment the mask offers the stop and the sampler takes
        # it — so every ending here is a choice, not a truncation.
        r = random.Random(11)
        eos_set, tick_set = set(samp.masks.eos), set(ticked)
        breach = deadend = cut = ended = stops = pieces_seen = 0
        clean = partial = illegal_end = 0
        worst: list[str] = []
        for _ in range(40):
            s = ConstrainedSampler(con0, tok)
            pieces: list[str] = []
            for _step in range(300):
                legal = s.allowed_ids()
                if not legal:
                    deadend += 1
                    break                       # nothing to say, nowhere to go
                if len(legal) > 200:                 # a loose (body) position
                    q = r.random()
                    if q < 0.25 and s.contract.stop_ok():
                        stops += 1
                        break                   # the stop the mask offered
                    if q < 0.55:
                        close = [i for i in legal if i in tick_set]
                        legal = close or legal  # aim at the closing fence
                pick = legal[r.randrange(len(legal))]
                if pick in eos_set:
                    stops += 1
                    break                       # the model ended the turn
                if not s.consume([pick]):
                    breach += 1
                    break
                pieces.append(texts[pick])       # markers included: as emitted
                pieces_seen += 1
            else:
                cut += 1                        # out of steps: the budget case
                continue                        # no mask can prevent it
            ended += 1
            text = "".join(pieces) + s.finish()
            ok, why = conformance(text, list(names))
            if ok:
                clean += 1
            elif why == "unclosed" and s.contract.stop_ok():
                # A legal stop inside a body: the emitted files are whole, the
                # SET is partial. No logit mask may forbid that stop — it is
                # the model's own budget decision, and it degrades parseably.
                partial += 1
            else:
                illegal_end += 1
                if len(worst) < 2:
                    worst.append(f"{why}@{s.contract.state}: {text[:60]!r}")
        ck("40 mask-legal walks: no refusal, no dead end",
           breach == 0 and deadend == 0,
           f"{breach} breach, {deadend} dead ends over {pieces_seen} pieces, "
           f"{cut} cut by budget")
        ck("a walk that ends by choice never yields a malformed answer",
           illegal_end == 0 and ended > 0,
           f"{clean} complete, {partial} partial-but-parseable, "
           f"{illegal_end} illegal "
           f"of {ended} ended {worst}")

        # ---- liveness: a mask that only ever says "no" is worthless. Every
        # shipped reference must pass through its own mask token by token, so
        # the constraint can never block a correct answer.
        import bisect
        blocked = []
        for x in multi:
            w = ConstrainedSampler(Contract(mode="multi",
                                            names=tuple(x["solution"])), tok)
            for i in tok.encode(_reference(x), add_special_tokens=False):
                pool = w.allowed_ids()
                k = bisect.bisect_left(pool, i)
                if k == len(pool) or pool[k] != i:
                    blocked.append((x["id"], texts[i]))
                    break
                if not w.consume([i]):
                    blocked.append((x["id"], "refused " + texts[i]))
                    break
            if not blocked and not w.contract.can_stop():
                blocked.append((x["id"], "no stop offered at the end"))
        # The live census's failure mode, pinned: a masked answer that ends on
        # the terminator has to read as the answer, not as a violation.
        stop_txt = texts[turns[0]]
        whole = _reference(t)
        ck("stopping on the terminator leaves the answer conforming",
           conformance(whole + stop_txt, list(names))[0]
           and conformance(whole + stop_txt + "\n", list(names))[0],
           repr(stop_txt))

        ck("no mw reference is blocked by its own mask", not blocked,
           str(blocked[:3]))
    # ---- 30-31 the vector's own measure on shipped answers
    ck("an answer the tolerant parser had to rescue counts as a violation",
       not conformance(bad["a fence with no header"], list(names))[0])
    ck("the reference answer conforms", conformance(ref, list(names))[0])

    # ---- 32-36 the loop's wiring: which contract each attempt is masked to
    from flash.loop import _contract_for
    mw = {"id": "mw1", "multi": True,
          "prompt": 'Write two files.\nFile "ring.py": a ring buffer.\n'
                    'File "stats.py": running stats.'}
    ck("the switch off leaves the loop unconstrained",
       _contract_for(mw, None, None, constrain=False) is None)
    first = _contract_for(mw, None, None, constrain=True)
    ck("attempt 1 is masked to the file set the prompt declares",
       first is not None and first.names == names, "" if first else "none")
    fixed = _contract_for(mw, list(names), ["stats.py"], constrain=True)
    ck("a targeted repair is masked to only the files it must fix",
       fixed is not None and fixed.names == ("stats.py",),
       "" if fixed else "none")
    ck("a single-file task is masked to a fenced block",
       _contract_for({"id": "t01", "test": "assert 1"}, None, None,
                     constrain=True).mode == "fence")
    ck("a multi task with no nameable file set degrades to free generation",
       _contract_for({"id": "x", "multi": True, "prompt": "fix it"}, None, None,
                     constrain=True) is None)
    width = max(len(name) for name, _, _ in checks)
    fails = 0
    for name, cond, note in checks:
        fails += 0 if cond else 1
        print(f"  {'ok  ' if cond else 'FAIL'} {name:<{width}}"
              + (f"  [{note}]" if note else ""))
    print(f"\nflash.grammar selftest: {len(checks) - fails}/{len(checks)}")
    return 1 if fails else 0


if __name__ == "__main__":                       # pragma: no cover
    a = sys.argv[1:]
    if a and a[0] == "--overhead":
        kw = {}
        for flag, name, cast in (("--model", "model", str),
                                 ("--iters", "iters", int),
                                 ("--step-ms", "step_ms", float)):
            if flag in a:
                kw[name] = cast(a[a.index(flag) + 1])
        raise SystemExit(run_overhead(**kw))
    if a and a[0] == "--census":
        kw = {}
        for flag, name, cast in (("--n", "n", int), ("--model", "model", str),
                                 ("--tasks", "tasks_file", str),
                                 ("--arm", "attempts", str),
                                 ("--max-tokens", "max_tokens", int)):
            if flag in a:
                kw[name] = cast(a[a.index(flag) + 1])
        raise SystemExit(run_census(**kw))
    raise SystemExit(run_selftest())
