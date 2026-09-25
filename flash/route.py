"""Task routing: fast tier or brain? (PLAN §32 decision fabric, M2 wiring)

One restricted-softmax decision per task — ~300ms, no generation. The router
sees the task AND the repo skeleton, so it can price in repo-knowledge needs.
Policy: pick the CHEAPEST tier that can solve it (cost-aware, not max-quality).
"""
from __future__ import annotations

from flash.decide import Decision, decide

TIERS = [
    "easy: one well-known pattern, a single function, no edge-case traps",
    "tricky: multi-step reasoning, stateful edge cases, or unusual precision requirements",
]

QUESTION = ("A fast 7B coder solves EASY tasks reliably but fails TRICKY ones. "
            "Judge the TASK, not your own confidence: which is this?")


def route_task(model, tok, task: dict, ctx: str = "") -> Decision:
    """Route one task dict ({prompt, ...}) to a tier. ctx = repo skeleton."""
    context = f"Task: {task['prompt']}"
    if ctx:
        context = f"Repo context (API the task must use):\n{ctx}\n\n{context}"
    return decide(model, tok, context, TIERS, question=QUESTION)


def tier_name(d: Decision) -> str:
    return "small" if d.index == 0 else "big"
