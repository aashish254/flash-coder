"""The minimal agent loop (PLAN §5): ACT -> VERIFY -> error-feedback retry.

ORIENT/PLAN arrive later (LSP + graph + decision router); even this minimal
loop should show the core plan thesis: **verification closes the gap** —
pass@3 with error feedback should beat pass@1. That delta is the value of
the loop itself, before any fancy planning exists.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from flash.harness import diagnose, extract_code
from flash.perceive import format_errors, static_check
from flash import trace

FIX_TEMPLATE = (
    "Your previous code failed its tests.\n\n```python\n{code}\n```\n\n"
    "Error:\n{err}\n\nFix it. Return only the corrected code."
)

TARGETED_FIX_TEMPLATE = (
    "These files have syntax/compile errors:\n{detail}\n\n"
    "Return ONLY the corrected version of each listed file, as a fenced block "
    "starting with '# file: <name>'. The other files are fine — do not repeat them."
)

MULTI_FIX_TEMPLATE = (
    "Your previous files failed their tests.\n\n```python\n{code}\n```\n\n"
    "Error:\n{err}\n\nFix it. The required files are: {files}. Return ALL "
    "of them, each as a fenced block starting with '# file: <name>'."
)


@dataclass
class Attempt:
    code: str
    ok: bool
    err: str = ""


@dataclass
class SolveResult:
    task_id: str
    solved: bool
    attempts: list[Attempt] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def n_attempts(self) -> int:
        return len(self.attempts)


def _generate(model, tokenizer, messages: list[dict], max_tokens: int,
              temp: float = 0.0, seed: int | None = None,
              task_id: str = "", attempt: int = 0) -> str:
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    prompt = tokenizer.apply_chat_template(messages, tokenize=False,
                                           add_generation_prompt=True)
    # attempt 1 greedy (deterministic); retries sample — otherwise feedback
    # retries produce the IDENTICAL wrong code (discovered live on M5, App. A)
    sampler = make_sampler(temp=temp) if temp > 0 else None
    if temp > 0 and seed is not None:
        # Per-attempt RNG seed (live find, run7 mw3/mw4): unseeded temp>0
        # retries came back byte-identical to attempt 1, so the feedback loop
        # burned attempts re-sampling the same wrong code. Seeding by attempt
        # index makes each retry draw from a different stream.
        import mlx.core as mx
        mx.random.seed(seed)
    t0 = time.perf_counter()
    out = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                   verbose=False, sampler=sampler)
    trace.event("generate", task_id=task_id or None, attempt=attempt, temp=temp,
                max_tokens=max_tokens, ms=round((time.perf_counter() - t0) * 1000),
                prompt_tokens=trace.n_tokens(tokenizer, prompt),
                completion_tokens=trace.n_tokens(tokenizer, out),
                prompt=prompt if trace.CAPTURE else None,
                output=out if trace.CAPTURE else None)
    return out


def enrich_task(task: dict, root, max_chars: int = 4000) -> dict:
    """Inject the repo skeleton for tasks that declare a `context` dir (M2),
    and ranked doc excerpts for tasks that declare `doc_urls` (Phase-4 web
    tool: knowledge as a tool, PLAN §25a3). Both idempotent via markers."""
    from pathlib import Path
    prompt = task["prompt"]
    query = prompt                                  # rank vs the RAW prompt
    if task.get("context"):
        task = dict(task, _ctx_dir=str(Path(root) / task["context"]))
    if task.get("context") and "Project context (real API" not in prompt:
        from flash.context import digest
        skel = digest(str(Path(root) / task["context"]), max_chars)
        prompt = (
            "Project context (real API signatures — use exactly these; prefer "
            "calling existing high-level methods over re-implementing logic):\n"
            f"{skel}\n\nTask: {prompt}")
    if task.get("doc_urls") and "Library documentation (fetched" not in prompt:
        from flash import web
        block = web.format_for_prompt(web.lookup(query, task["doc_urls"]))
        if block:
            prompt = f"{block}\n\nTask: {prompt}"
    return dict(task, prompt=prompt)



def _symbol_hint(task: dict, err: str, code: str = "") -> str:
    """§33.1 ACT upgrade: when a failure turns on a repo symbol, its REAL
    source goes into the feedback. 'property object is not callable' is
    fixable in one line of context; a retry without it is a coin flip.
    Silent (and harmless) when the task has no repo context or nothing the
    repo defines is at issue."""
    ctx = task.get("_ctx_dir")
    if not (ctx and err):
        return err
    try:
        from flash.lsp import symbol_hint
        hint = symbol_hint(ctx, err, code)
    except Exception:
        return err
    return f"{err}\n\n{hint}" if hint else err


def solve(model, tokenizer, task: dict, max_attempts: int = 3,
          max_tokens: int = 1024) -> SolveResult:
    if task.get("multi"):               # multi-file answers are 2x+ longer;
        max_tokens = max(max_tokens, 2048)   # 1024 truncates mid-file (live: mw4)
    t0 = time.perf_counter()
    res = SolveResult(task_id=task["id"], solved=False)
    messages = [{"role": "user", "content": task["prompt"]}]
    code = ""
    expected: list[str] | None = None            # multi: file set from attempt 1
    merged: dict[str, str] = {}                  # multi: per-file persistent state
    for attempt_i in range(max_attempts):
        out = _generate(model, tokenizer, messages, max_tokens,
                        temp=0.0 if attempt_i == 0 else 0.7, seed=attempt_i,
                        task_id=task["id"], attempt=attempt_i)
        vt0 = time.perf_counter()
        kind = "test"
        if task.get("multi"):                       # M2: coordinated file set
            from flash.harness import diagnose_files, extract_files
            files = extract_files(out, expected=expected)
            # Per-file persistence (live find, mw suite): models often return
            # only the file they fixed, dropping the rest -> treat the file set
            # as STATE: latest valid version of each file wins, union across
            # attempts. A syntactically broken revision never overwrites a
            # previously-valid file (no regress-to-invalid).
            # Severity-filtered rejection (live find, run7 mw3): reject a
            # revision only on severity=='error' diagnostics — a pyflakes
            # WARNING (unused import) must not reject a file whose tests pass.
            # format_errors IS the error-only predicate, so the reject label
            # and the rejection rule cannot disagree.
            def _detail(p, src):
                # Error diagnostics with the OFFENDING SOURCE LINE quoted —
                # mw4 live find: bare 'L15: unterminated string' didn't break
                # the 30B's blind spot; showing it the exact broken text does.
                out = []
                lines = src.splitlines()
                for d in static_check(src):
                    if d.severity == 'error':
                        bad = lines[d.line - 1].strip() if 0 < d.line <= len(lines) else '?'
                        out.append(f"L{d.line}: {d.message} | line reads: {bad!r}")
                return "; ".join(out)
            errors = {p: _detail(p, src) for p, src in files.items()}
            valid = {p: src for p, src in files.items() if not errors[p]}
            rejected = sorted(set(files) - set(valid))
            merged.update(valid)
            if expected is None:
                expected = list(merged)
            code = "\n\n".join(f"# file: {p}\n{c}" for p, c in merged.items())
            targeted = None
            if rejected:
                # Embed the real diagnostic text per file — the bare 'syntax
                # error' label is what misclassified mw3's warning as fatal.
                detail = "; ".join(f"{p}: {errors[p]}" for p in rejected)
                ok, err = False, f"STATIC: {detail} (kept previous version)"
                kind = "static"
                targeted = detail        # next retry: repair only these files
            else:
                ok, err = diagnose_files(merged, task["test"])
        else:
            code = extract_code(out)
            # PERCEIVE (§33.1): static errors skip the test run entirely —
            # cheaper than a doomed subprocess and line-precise
            static_err = format_errors(static_check(code))
            if static_err:
                ok, err = False, f"STATIC: {static_err}"
                kind = "static"
            else:
                ok, err = diagnose(code, task["test"])  # GOT/WANT feedback
        trace.event("verify", task_id=task["id"], attempt=attempt_i, ok=ok,
                    kind=kind, ms=round((time.perf_counter() - vt0) * 1000),
                    files=len(merged) if task.get("multi") else None,
                    err=None if ok else err[:trace.MAX_ERR])
        res.attempts.append(Attempt(
            code=code, ok=ok,
            err=err if ok else _symbol_hint(task, err, code)))
        if ok:
            res.solved = True
            break
        if task.get("multi") and targeted:
            # Targeted repair (mw4): ask only for the broken files — smaller,
            # focused output; per-file persistence makes partial answers safe.
            feedback = TARGETED_FIX_TEMPLATE.format(detail=targeted)
        elif task.get("multi"):
            feedback = MULTI_FIX_TEMPLATE.format(code=code, err=err,
                                                 files=", ".join(expected or []))
        else:
            feedback = FIX_TEMPLATE.format(code=code, err=err)
        messages += [{"role": "assistant", "content": out},
                     {"role": "user", "content": feedback}]
    res.seconds = round(time.perf_counter() - t0, 1)
    return res


def solve_with_escalation(small_repo: str, big_repo: str, task: dict,
                          small_attempts: int = 2, big_attempts: int = 2,
                          max_tokens: int = 1024) -> tuple[SolveResult, str]:
    """The hardware-proven policy (App. A): small tries -> escalate to big.

    Hot-swap: the small model is fully freed BEFORE the big one loads —
    on a 32GB machine 7B (4.4GB) + 30B (17.3GB) would squeeze macOS;
    serialized, we never hold both. Later (§32 D2) a decision model picks
    *when* to escalate instead of a fixed attempt count.
    """
    import mlx.core as mx
    from mlx_lm import load

    model, tok = load(small_repo)
    r_small = solve(model, tok, task, max_attempts=small_attempts,
                    max_tokens=max_tokens)
    if r_small.solved:
        return r_small, "small"
    del model, tok
    mx.clear_cache()                     # hot-swap: free before loading big

    model, tok = load(big_repo)
    r_big = solve(model, tok, task, max_attempts=big_attempts,
                  max_tokens=max_tokens)
    del model, tok
    mx.clear_cache()
    r_big.attempts = r_small.attempts + r_big.attempts   # full audit trail
    r_big.seconds = round(r_small.seconds + r_big.seconds, 1)
    return r_big, ("big" if r_big.solved else "failed")


_ROUTER = "unset"


def _load_router():
    """Learned router bundle (flash/learn.py); None until `router-fit` saves one."""
    global _ROUTER
    if _ROUTER == "unset":
        from flash.learn import load_router
        _ROUTER = load_router()
    return _ROUTER


def solve_routed(small_repo: str, big_repo: str, task: dict, root,
                 small_attempts: int = 2, big_attempts: int = 2,
                 max_tokens: int = 1024, max_chars: int = 4000,
                 threshold: float = 0.5, allow_big: str = "auto"
                 ) -> tuple[SolveResult, str, str]:
    """The full policy: PERCEIVE(repo skeleton) -> ROUTE -> small -> reactive ESC.

    Router output is telemetry + a high-precision 'big' shortcut ONLY:
    prospective difficulty self-assessment is systematically overconfident
    (App. A, 2026-09-23: 3 configs, 0/6 hard-task detections), so a 'small'
    verdict never blocks reactive escalation. Returns (result, tier, routed_as).

    `allow_big` gates escalation against the power governor (§34.1): "auto"
    sheds the brain when the machine is hot, on battery or under memory
    pressure — the task then stays unsolved and the ledger says why; "always"
    is the benchmark override; "never" is battery-only single-track.
    """
    import mlx.core as mx
    from mlx_lm import load
    from flash import ledger, power
    from flash.route import route_task, tier_name

    task = enrich_task(task, root, max_chars)
    entry = {"task_id": task["id"], "prompt": task["prompt"][:300],
             "ctx": bool(task.get("context")),
             "small": small_repo.split("/")[-1], "big": big_repo.split("/")[-1]}
    st, caps = power.governor(power.peak_gb(small_repo))
    entry["profile"] = caps.profile
    model, tok = load(small_repo)
    routed = tier_name(route_task(model, tok, task))

    # Learned router (M3): score from outcomes-trained bundle. Zero extra model
    # load — the small tier is already resident, so the embedding is ~free.
    route_p = None
    router = _load_router()
    if router is not None:
        from flash.learn import embed_text, score
        route_p = score(router, embed_text(model, tok, task["prompt"]))
        entry["route_p"] = round(route_p, 3)
    if route_p is not None and route_p >= threshold:
        routed = "big(learned)"                # outcome-trained big shortcut

    big_ok, big_why = power.allow_model(big_repo, caps)
    if allow_big == "always":
        big_ok, big_why = True, ""
    elif allow_big == "never":
        big_ok, big_why = False, "allow-big=never"

    # §33.6: the whole routing decision is one replayable record — what the
    # router said, what the governor allowed, and which tier actually ran.
    trace.event("route", task_id=task["id"], routed=routed,
                route_p=None if route_p is None else round(route_p, 4),
                profile=caps.profile, allow_big=allow_big,
                big_allowed=big_ok, multi=bool(task.get("multi")),
                why=None if big_ok else big_why)

    if routed.startswith("big"):             # rare, high-precision: go direct
        if not big_ok:                       # the governor outranks the router
            entry["shed"] = big_why
            routed = f"small(shed:{caps.profile})"
            trace.event("escalation", task_id=task["id"], denied=True,
                        tier="big", why=big_why)
        else:
            del model, tok
            mx.clear_cache()
            model, tok = load(big_repo)
            # Multi-file outputs are larger and riskier; give big-direct the same
            # repair budget a reactive path would still have left (mw4 needed a
            # 3rd attempt: syntax fixed on 2, one-char semantic bug on 3).
            extra = 1 if task.get("multi") else 0
            r = solve(model, tok, task, big_attempts + extra, max_tokens)
            del model, tok
            mx.clear_cache()
            tier = "big" if r.solved else "failed"
            if not r.solved:                 # diagnose big-direct failures too
                entry["fail_output"] = r.attempts[-1].err[-200:]
            ledger.record({**entry, "tier": tier, "routed": routed,
                           "solved": r.solved, "attempts": r.n_attempts,
                           "seconds": r.seconds})
            return r, tier, routed

    r_small = solve(model, tok, task, small_attempts, max_tokens)
    if r_small.solved:
        ledger.record({**entry, "tier": "small", "routed": routed,
                       "solved": True, "attempts": r_small.n_attempts,
                       "seconds": r_small.seconds})
        return r_small, "small", routed
    if not big_ok:        # would escalate, cannot: shed and say so in the ledger
        entry["shed"] = big_why
        trace.event("escalation", task_id=task["id"], denied=True, tier="big",
                    why=big_why)
        ledger.record({**entry, "tier": "shed", "routed": routed,
                       "solved": False, "attempts": r_small.n_attempts,
                       "seconds": r_small.seconds})
        del model, tok
        mx.clear_cache()
        return r_small, "shed", routed
    entry["fail_output"] = r_small.attempts[-1].err[-200:]   # feature: WHY small failed
    del model, tok
    mx.clear_cache()                         # hot-swap: free before big loads

    trace.event("escalation", task_id=task["id"], denied=False, tier="big",
                reason=f"small failed after {r_small.n_attempts} attempt(s)")
    model, tok = load(big_repo)
    r_big = solve(model, tok, task, big_attempts, max_tokens)
    del model, tok
    mx.clear_cache()
    r_big.attempts = r_small.attempts + r_big.attempts   # full audit trail
    r_big.seconds = round(r_small.seconds + r_big.seconds, 1)
    tier = "big" if r_big.solved else "failed"
    ledger.record({**entry, "tier": tier, "routed": routed,
                   "solved": r_big.solved, "attempts": r_big.n_attempts,
                   "seconds": r_big.seconds})
    return r_big, tier, routed
