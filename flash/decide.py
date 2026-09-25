"""System One decision fabric (PLAN §32, jevmlx-pattern).

A decision = context + typed options -> ONE forward pass -> choice + per-option
probabilities. No text generation, no parsing: we read the next-token logits
once and take a restricted softmax over single-token option labels (A/B/C/...).

This is the cheap layer that sits in front of every generative call:
routing, tool selection, escalation, cache-worthiness, safety gates.
~10 decisions cost less than one generated sentence.
"""
from __future__ import annotations

import math
import string
from dataclasses import dataclass

import mlx.core as mx


@dataclass
class Decision:
    choice: str                    # winning option text
    index: int                     # winning option index
    confidence: float              # softmax probability of the winner
    probs: dict[str, float]        # option -> probability (restricted softmax)
    ms: float                      # wall time of the single forward pass


def _label_token_ids(tokenizer, n: int) -> list[int]:
    """Single token id for each label A, B, C... as written after 'Answer:'."""
    ids = []
    for letter in string.ascii_uppercase[:n]:
        toks = tokenizer.encode(f" {letter}", add_special_tokens=False)
        if len(toks) != 1:
            # fall back to bare letter; still must be one token
            toks = tokenizer.encode(letter, add_special_tokens=False)
        ids.append(toks[-1])
    return ids


def decide(model, tokenizer, context: str, options: list[str],
           question: str = "Which option best fits the context?") -> Decision:
    """One prefill, restricted softmax over option labels. Never generates."""
    if not 2 <= len(options) <= 26:
        raise ValueError("need 2..26 options")

    labels = string.ascii_uppercase[: len(options)]
    listing = "\n".join(f"{l}) {o}" for l, o in zip(labels, options))
    user = f"{context}\n\n{question}\n{listing}\nAnswer with a single letter."
    prompt = tokenizer.apply_chat_template(
        [{"role": "user", "content": user}], tokenize=False, add_generation_prompt=True
    )

    import time
    t0 = time.perf_counter()
    tokens = mx.array([tokenizer.encode(prompt, add_special_tokens=False)])
    logits = model(tokens)[:, -1, :]                 # [1, vocab] — one pass
    logits = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
    mx.eval(logits)
    ms = (time.perf_counter() - t0) * 1000

    label_ids = _label_token_ids(tokenizer, len(options))
    raw = [float(logits[0, tid]) for tid in label_ids]   # log-probs, softmaxed over vocab
    # restricted softmax over just the options (renormalize):
    m = max(raw)
    exps = [math.exp(x - m) for x in raw]
    z = sum(exps)
    probs = {opt: e / z for opt, e in zip(options, exps)}

    best_i = max(range(len(options)), key=lambda i: exps[i])
    return Decision(choice=options[best_i], index=best_i,
                    confidence=probs[options[best_i]], probs=probs, ms=round(ms, 1))
