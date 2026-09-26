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

# R-3.2: the patch arm's retry note. The refusal text already names the reason;
# this is the one rule that keeps a retry from answering a refusal with a
# whole-file dump, which is the failure mode patches exist to remove.
RETRY_EDITS = (
    "Send one patch per symbol you change, addressed at a symbol shown in the "
    "project above, with that symbol's complete new definition in the block."
)

# R-3.2's control arm: the same project and the same request, answered by
# re-typing every file. Both arms read an identical prompt (the task ships it),
# so the only difference between them is the answer format.
WHOLE_FILE_PROTOCOL = (
    "Reply with the ENTIRE project: every file again, complete, each as a "
    "fenced block whose first line is exactly \"# file: <name>\". Do not "
    "describe the change — return the files."
)


@dataclass
class Attempt:
    code: str
    ok: bool
    err: str = ""
    # R-3.2 (edit tasks only): what the patch set did, so the ACT leg is
    # auditable per attempt rather than reconstructed from the code text.
    patches: int = 0
    refused: int = 0
    whole: int = 0
    outside: int = 0


# R-4.2: when on, every generation is masked to the task's output contract
# (flash/grammar.py). The CLI's --constrain sets it; a suite A/B flips it.
CONSTRAIN = False

# R-3.2: when on, a task that ships a project (`edit`) is answered with
# symbol-addressed patches (flash/patches.py) instead of whole files. The CLI's
# --edit sets it.
EDIT = False

# R-4.3: when on, a failed verify is re-run under the tracer (flash/debug.py)
# and the execution digest joins the retry feedback. The A/B that decides the
# default is `run-suite --tasks dbg_tasks.jsonl` with and without it.
DEBUG = False


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
              task_id: str = "", attempt: int = 0,
              contract=None) -> str:
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
    # R-4.2: a contract turns the output protocol into a per-step token mask.
    # Never combine with a draft model — speculative decoding rejects tokens
    # the DFA has already consumed (flash.grammar docstring).
    guard = None
    if contract is not None:
        from flash.grammar import ConstrainedSampler
        guard = ConstrainedSampler(contract, tokenizer)
    t0 = time.perf_counter()
    out = generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                   verbose=False, sampler=sampler,
                   logits_processors=[guard] if guard else None)
    if guard is not None:
        out = out + guard.finish()     # deterministic repair of a cut block
    trace.event("generate", task_id=task_id or None, attempt=attempt, temp=temp,
                max_tokens=max_tokens, ms=round((time.perf_counter() - t0) * 1000),
                prompt_tokens=trace.n_tokens(tokenizer, prompt),
                completion_tokens=trace.n_tokens(tokenizer, out),
                constrained=guard is not None,
                mask_steps=guard.steps if guard else None,
                mask_breaches=guard.illegal_picks if guard else None,
                prompt=prompt if trace.CAPTURE else None,
                output=out if trace.CAPTURE else None)
    return out


def enrich_task(task: dict, root, max_chars: int = 4000) -> dict:
    """Inject the repo skeleton for tasks that declare a `context` dir (M2),
    and ranked doc excerpts for tasks that declare `doc_urls` (Phase-4 web
    tool: knowledge as a tool, PLAN §25a3). Both idempotent via markers."""
    from pathlib import Path
    if task.get("edit"):
        # An edit task ships its own real source in the prompt; a skeleton on
        # top of it would be pure token cost.
        return task
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


def _contract_for(task: dict, expected: list[str] | None,
                  repair: list[str] | None, constrain: bool | None = None):
    """The R-4.2 output contract for this attempt, or None to generate freely.

    A retry that was asked to fix specific files gets a contract over exactly
    those names — per-file persistence makes a narrow answer safe, and the
    mask then keeps the narrow answer well-formed. With no file set to name
    (a task that never declares one) there is nothing to constrain, and the
    loop degrades to free generation rather than inventing a protocol (I-7).
    """
    if not (CONSTRAIN if constrain is None else constrain):
        return None
    from flash.grammar import Contract
    if not task.get("multi"):
        return Contract(mode="fence")
    names = tuple(repair or expected or Contract.names_from_prompt(task["prompt"]))
    return Contract(mode="multi", names=names) if names else None


def _debug_feedback(task: dict, code: str, merged: dict[str, str],
                    err: str) -> str:
    """Append what execution actually did to a failing verdict (R-4.3).

    Losing the debugger (a tracer error, a hostile candidate, no trail) costs
    the extra signal and nothing else: the traceback feedback stays (I-7).
    """
    from flash.debug import watch, watch_files
    try:
        d = (watch_files(merged, task["test"]) if task.get("multi") and merged
             else watch(code, task["test"]))
    except Exception:
        return err
    return f"{err}\n\n{d.as_feedback()}" if (d.trail and not d.ok) else err


def _solve_edits(model, tokenizer, task: dict, max_attempts: int,
                 max_tokens: int) -> SolveResult:
    """R-3.2's ACT leg: answer a change request with patches, not files.

    The workspace is state, exactly like the multi-file loop's `merged` dict:
    an accepted patch set becomes the project the next attempt sees, and a
    refused one leaves it untouched. That is what makes a retry cheap — the
    model repairs the change it asked for, not a transcription of the file.

    These attempts generate freely: R-4.2's mask covers the '# file:'
    protocol, and no grammar exists for this one (SPEC §10.4).
    """
    from flash.harness import diagnose_files
    from flash.patches import apply_patches, edit_prompt, outside_lines, parse_patches
    t0 = time.perf_counter()
    res = SolveResult(task_id=task["id"], solved=False)
    workspace = dict(task["files"])
    messages = [{"role": "user", "content": edit_prompt(task)}]
    code = ""
    for attempt_i in range(max_attempts):
        out = _generate(model, tokenizer, messages, max_tokens,
                        temp=0.0 if attempt_i == 0 else 0.7, seed=attempt_i,
                        task_id=task["id"], attempt=attempt_i)
        vt0 = time.perf_counter()
        before = dict(workspace)
        patches = parse_patches(out)
        result = apply_patches(before, patches)
        kind = "patch"
        if not patches:
            ok, err = False, ("PATCH MISSING: no "
                              "'# edit: <file> :: <symbol>' patch in the response")
        elif not result.ok:
            ok, err = False, "PATCH REFUSED: " + "; ".join(
                f"{p.file}:{p.address} — {w}" for p, w in result.refusals)
        elif not result.applied:
            ok, err = False, "PATCH EMPTY: the response addressed nothing"
        else:
            workspace = result.files
            code = "\n\n".join(f"# file: {p}\n{c}" for p, c in workspace.items())
            # PERCEIVE still runs first — a patched file that does not compile
            # is line-precise for free, and the splice already guarantees the
            # rest of the file is byte-identical.
            static_err = []
            for p, c in result.files.items():
                if c == before.get(p):
                    continue
                e = format_errors(static_check(c))
                if e:
                    static_err.append(f"{p}: {e}")
            if static_err:
                ok, err = False, "STATIC: " + "; ".join(static_err)
                kind = "static"
            else:
                ok, err = diagnose_files(workspace, task["test"])
                kind = "test"
        outside = outside_lines(before, result, task.get("target") or {})
        trace.event("patch", task_id=task["id"], attempt=attempt_i, ok=ok,
                    kind=kind,
                    applied=len(result.applied), refused=len(result.refusals),
                    whole=result.whole_rewrites, outside=outside,
                    addresses=[f"{a.patch.file}:{a.patch.address}"
                               for a in result.applied],
                    why=None if ok else err[:trace.MAX_ERR],
                    ms=round((time.perf_counter() - vt0) * 1000))
        res.attempts.append(Attempt(code=code or out, ok=ok, err=err,
                                    patches=len(patches),
                                    refused=len(result.refusals),
                                    whole=result.whole_rewrites, outside=outside))
        if ok:
            res.solved = True
            break
        messages += [{"role": "assistant", "content": out},
                     {"role": "user", "content": err + "\n\n" + RETRY_EDITS}]
    res.seconds = round(time.perf_counter() - t0, 1)
    return res


def solve(model, tokenizer, task: dict, max_attempts: int = 3,
          max_tokens: int = 1024,
          constrain: bool | None = None,
          debug: bool | None = None,
          edit: bool | None = None) -> SolveResult:
    if (EDIT if edit is None else edit) and task.get("edit"):
        return _solve_edits(model, tokenizer, task, max_attempts, max_tokens)
    if task.get("edit"):
        task = dict(task, prompt=task["prompt"] + "\n\n" + WHOLE_FILE_PROTOCOL)
    if task.get("multi"):               # multi-file answers are 2x+ longer;
        max_tokens = max(max_tokens, 2048)   # 1024 truncates mid-file (live: mw4)
    t0 = time.perf_counter()
    res = SolveResult(task_id=task["id"], solved=False)
    messages = [{"role": "user", "content": task["prompt"]}]
    code = ""
    expected: list[str] | None = None            # multi: file set from attempt 1
    merged: dict[str, str] = {}                  # multi: per-file persistent state
    repair: list[str] | None = None              # multi: files a retry must fix
    for attempt_i in range(max_attempts):
        out = _generate(model, tokenizer, messages, max_tokens,
                        temp=0.0 if attempt_i == 0 else 0.7, seed=attempt_i,
                        task_id=task["id"], attempt=attempt_i,
                        contract=_contract_for(task, expected, repair,
                                               constrain))
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
        # R-4.3: a failed run is re-executed under a line tracer, so the retry
        # sees where the value was MADE, not only where the assert noticed it.
        # Static failures are skipped — broken syntax has no execution to watch.
        if not ok and (DEBUG if debug is None else debug) and kind != "static":
            err, kind = _debug_feedback(task, code, merged, err), "debug"
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
