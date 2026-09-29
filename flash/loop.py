"""The minimal agent loop (PLAN §5): ACT -> VERIFY -> error-feedback retry.

ORIENT/PLAN arrive later (LSP + graph + decision router); even this minimal
loop should show the core plan thesis: **verification closes the gap** —
pass@3 with error feedback should beat pass@1. That delta is the value of
the loop itself, before any fancy planning exists.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from flash.harness import diagnose, extract_code
from flash.perceive import format_errors, static_check
from flash import checkpoint, trace

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

# R-1.1b: which of the two perception blocks `_perceive` appends to a retry. Both
# names is what every shipped run does; `--no-source-hint` / `--no-graph-hint` drop
# one, and because run-suite records its params in the trace, an arm is auditable
# from its own session rather than from a flag someone remembers passing. An empty
# selection also skips the AST index — see `_perceive`.
HINTS: tuple[str, ...] = ("source", "graph")


@dataclass
class SolveResult:
    task_id: str
    solved: bool
    attempts: list[Attempt] = field(default_factory=list)
    seconds: float = 0.0
    # R-3.3: the flash.tourney.Result behind this run when the tournament arm
    # replaced the small-tier chain, else None. The CLI reads it for the ledger
    # and the suite line, so the candidate table survives the return trip.
    tournament: "object | None" = None
    # R-2.3: the flash.confidence.Signals for a `confidence=True` solve — the
    # evidence table §34.2 gates on, kept so the CLI can print the reasons and
    # the resume path can re-derive an offer without re-running the probes.
    confidence: "object | None" = None
    # R-3.2's clause 3: the patch arm's verified workspace, kept so the caller
    # can tell the difference between "an edit that passes the oracle" and "an
    # edit that is on the user's disk". None for every other arm.
    workspace: "dict[str, str] | None" = None

    @property
    def n_attempts(self) -> int:
        return len(self.attempts)


def _generate(model, tokenizer, messages: list[dict], max_tokens: int,
              temp: float = 0.0, seed: int | None = None,
              task_id: str = "", attempt: int = 0,
              contract=None) -> str:
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    from flash import checkpoint
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
    # R-5.3: this is the one place a dead run's work comes back. `resume` folds
    # whatever the killed process had already decoded into the prompt, so the
    # continuation is conditioned on that text and never re-decodes it; the ids
    # are replayed into the mask so the DFA stands where the kill left it.
    cont = checkpoint.resume(task_id, attempt, prompt, temp=temp, seed=seed,
                             contract=None if contract is None else
                             {"mode": contract.mode,
                              "names": list(contract.names)})
    # (prefix to prefill, the whole answer text already decoded, its token cost)
    carried, resumed = ("", 0) if cont is None else (cont[1], cont[2])
    if cont is not None:
        prompt = cont[0]
        max_tokens = max(int(max_tokens) - cont[2], 0)
        if guard is not None:
            guard.consume(checkpoint.pending_ids(task_id, attempt))
    span = checkpoint.active(task_id, attempt) is not None
    t0 = time.perf_counter()
    # A checkpointed span streams, because a recovery that only writes its
    # checkpoint when the answer is finished has nothing to recover. Unarmed,
    # this stays on mlx's `generate` — which is a loop over `stream_generate`
    # concatenating `response.text` (mlx_lm/generate.py), so the two paths
    # cannot disagree on bytes; only the armed run pays the per-token hook.
    if not span or max_tokens == 0:
        out = (generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens,
                        verbose=False, sampler=sampler,
                        logits_processors=[guard] if guard else None)
               if max_tokens else "")
    else:
        from mlx_lm import stream_generate
        out = ""
        for r in stream_generate(model, tokenizer, prompt=prompt,
                                 max_tokens=max_tokens, sampler=sampler,
                                 logits_processors=[guard] if guard else None):
            out += r.text
            checkpoint.note(r.token, r.text, t0)
    if span:
        checkpoint.fold(task_id, attempt)
    if guard is not None:
        out = out + guard.finish()     # deterministic repair of a cut block
    out = carried + out
    trace.event("generate", task_id=task_id or None, attempt=attempt, temp=temp,
                max_tokens=max_tokens, ms=round((time.perf_counter() - t0) * 1000),
                prompt_tokens=trace.n_tokens(tokenizer, prompt),
                completion_tokens=trace.n_tokens(tokenizer, out),
                constrained=guard is not None,
                mask_steps=guard.steps if guard else None,
                mask_breaches=guard.illegal_picks if guard else None,
                resumed_tokens=resumed or None,
                checkpointed=span or None,
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



def _repo_index(task: dict):
    """The AST symbol index both perception hints read, built once per retry.

    They rank the SAME list of at-issue symbols — that agreement is deliberate,
    and so is sharing the parse that produces it: measured on this repo's own 26
    files, the hint pair costs 32-33 ms on a shared index and ~170 ms (167-169 across
    the runs taken) when each hint builds its own, so a retry would otherwise pay
    the AST parse twice for one list of names. Paying it twice is also a pure waste
    of the budget §10.5's latency gate measures. None when the task has no repo
    context, which is what keeps both hints silent.
    """
    ctx = task.get("_ctx_dir")
    if not ctx:
        return None
    try:
        from flash.lsp import SymbolIndex
        return SymbolIndex.build(ctx)
    except Exception:
        return None


def _symbol_hint(task: dict, err: str, code: str = "", index=None) -> str:
    """§33.1 ACT upgrade: when a failure turns on a repo symbol, its REAL
    source. 'property object is not callable' is fixable in one line of
    context; a retry without it is a coin flip. Returns the BLOCK, or "" — which
    is also what makes it safe to rank on `err` alone. Since R-1.1c the empty
    answer means "nothing repo-defined is at issue" and nothing else: a ranked
    symbol whose source exceeds the budget arrives clipped, with the symbols that
    did not fit counted in the block. See `_perceive`."""
    ctx = task.get("_ctx_dir")
    if not (ctx and err):
        return ""
    try:
        from flash.lsp import symbol_hint
        return symbol_hint(ctx, err, code, index=index) or ""
    except Exception:
        return ""


def _graph_hint(task: dict, err: str, code: str = "", index=None) -> str:
    """§28.2 step 3: the at-issue symbols' blast radius.

    `symbol_hint` answers "what is this symbol really"; this answers "who breaks
    if I change it", which is the question a RETRY is about — the candidate
    already wrote a call, and the fix that satisfies the oracle without breaking
    a second test is the one that knows the other call sites. Same silence
    contract: no repo context, nothing at issue, no block.
    """
    ctx = task.get("_ctx_dir")
    if not (ctx and err):
        return ""
    try:
        from flash.graph import scope_hint
        return scope_hint(ctx, err, code, index=index) or ""
    except Exception:
        return ""


def _perceive(task: dict, err: str, code: str = "",
              hints: "tuple[str, ...] | None" = None) -> str:
    """The failed verdict, with both repo-perception blocks appended.

    Both blocks rank over the BARE error, and that is a fix rather than a style
    point: assembled by nesting (the graph hint fed the error that already
    carried the source block), the source hint's own quoted source became part of
    the ranking text, and one retry showed two different answers to "what is at
    issue" — the source block quoted 1 symbol while the dependents block headed 3,
    including names lifted out of the first block's body. `graph.scope_hint`
    borrows `lsp.symbols_involved` precisely so the two cannot disagree; nesting
    defeated that through the argument.

    What this adds to a retry's context is bounded twice over: 1200 chars for the
    source block and 900 for the dependents block, so neither can crowd out the
    code being fixed, and the task's own skeleton is already in the opening turn.

    `hints` (R-1.1b) selects which of the two a retry is shown, so an A/B can ask
    "does either of these help" without deleting either. An empty selection returns
    `err` BEFORE the index is built: an OFF arm that still pays the AST parse would
    make §10.5's latency comparison measure the switch rather than the hint.
    """
    chosen = [b for name, b in (("source", _symbol_hint), ("graph", _graph_hint))
              if name in (HINTS if hints is None else hints)]
    ctx = task.get("_ctx_dir")
    if not (ctx and err) or not chosen:
        return err
    index = _repo_index(task)
    blocks = [b for b in (f(task, err, code, index) for f in chosen) if b]
    return "\n\n".join([err] + blocks)


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
                 max_tokens: int, stage: str = "small") -> SolveResult:
    """R-3.2's ACT leg: answer a change request with patches, not files.

    The workspace is state, exactly like the multi-file loop's `merged` dict:
    an accepted patch set becomes the project the next attempt sees, and a
    refused one leaves it untouched. That is what makes a retry cheap — the
    model repairs the change it asked for, not a transcription of the file.

    These attempts generate freely: R-4.2's mask covers the '# file:'
    protocol, and no grammar exists for this one (SPEC §10.4).
    """
    from flash.harness import diagnose_files
    from flash.patches import (apply_patches, edit_prompt, is_python,
                               outside_lines, parse_patches)
    t0 = time.perf_counter()
    res = SolveResult(task_id=task["id"], solved=False)
    workspace = dict(task["files"])
    messages = [{"role": "user", "content": edit_prompt(task)}]
    code = ""
    start = 0
    f = checkpoint.owns(task["id"], "edits", stage)
    if f is not None:
        # The patched workspace is the expensive part of this arm — an accepted
        # patch set is a project, and rebuilding it means re-generating the
        # attempt that made it.
        start = f.attempt
        messages = list(f.messages) or messages
        res.attempts = [Attempt(**d) for d in f.done]
        workspace = dict((f.state or {}).get("workspace") or workspace)
        code = f.state.get("code") or ""
    for attempt_i in range(start, max_attempts):
        checkpoint.begin(task["id"], "edits", stage, attempt_i,
                         messages=messages, max_tokens=max_tokens,
                         state={"workspace": workspace, "code": code},
                         done=[asdict(a) for a in res.attempts])
        out = _generate(model, tokenizer, messages, max_tokens,
                        temp=0.0 if attempt_i == 0 else 0.7, seed=attempt_i,
                        task_id=task["id"], attempt=attempt_i)

        vt0 = time.perf_counter()
        before = dict(workspace)
        patches = parse_patches(out)
        result = apply_patches(before, patches,
                               oracle=task.get("test_path") or "")
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
                if c == before.get(p) or not is_python(p):
                    # `static_check` is an `ast` pass: a `.tsx` it looked at would
                    # come back a confident false syntax error, and a false
                    # STATIC costs the retry that a refusal is supposed to be for.
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
            res.workspace = dict(workspace)
            break
        messages += [{"role": "assistant", "content": out},
                     {"role": "user", "content": err + "\n\n" + RETRY_EDITS}]
    res.seconds = round(time.perf_counter() - t0, 1)
    return res


def solve(model, tokenizer, task: dict, max_attempts: int = 3,
          max_tokens: int = 1024,
          constrain: bool | None = None,
          debug: bool | None = None,
          edit: bool | None = None,
          stage: str = "small") -> SolveResult:
    if (EDIT if edit is None else edit) and task.get("edit"):
        return _solve_edits(model, tokenizer, task, max_attempts, max_tokens,
                            stage=stage)
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
    # R-5.3: if a suite adopted this arm's frame, the dead run's attempts are
    # already paid for — their verdicts, the conversation they built and the
    # multi-file union all come back off it, and the chain restarts at the
    # attempt that was in flight instead of at attempt 0.
    start = 0
    f = checkpoint.owns(task["id"], "chain", stage)
    if f is not None:
        start = f.attempt
        messages = list(f.messages) or messages
        res.attempts = [Attempt(**d) for d in f.done]
        st = f.state or {}
        merged = dict(st.get("merged") or {})
        expected, repair = st.get("expected"), st.get("repair")
        code = st.get("code") or ""
    for attempt_i in range(start, max_attempts):
        # The boundary write is what makes a between-attempt kill cheap: it
        # names the attempt about to start, so everything before it is settled
        # work the resume reads back rather than re-decodes.
        checkpoint.begin(task["id"], "chain", stage, attempt_i,
                         messages=messages, max_tokens=max_tokens,
                         state={"merged": merged, "expected": expected,
                                "repair": repair, "code": code},
                         done=[asdict(a) for a in res.attempts])
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
        # R-1.1: the resolved source has to reach the MODEL. It used to be
        # computed inside the Attempt(...) call below, so it landed in the record
        # that `flash trace` prints while `messages` — the thing read back as
        # feedback — carried the bare error. Setting `err` first makes the record
        # and the retry prompt show the same text. R-1.3b rides the same
        # assignment for the same reason: the blast radius is computed here, from
        # the same `err`, and goes into the prompt or nowhere.
        if not ok:
            err = _perceive(task, err, code)
        res.attempts.append(Attempt(code=code, ok=ok, err=err))
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


def _tourney_arm(model, tokenizer, task: dict, requested_k: int,
                 width: int, max_tokens: int, stage: str = "small") -> SolveResult:
    """R-3.3's small tier: k independent candidates, oracle-picked, shaped like
    a SolveResult so the policy, the ledger and `resume` need no special case.

    The mask (R-4.2) is not applied here: no arm measured in SPEC combines the
    two, and a tournament whose candidates are all shaped by one DFA is not a
    tournament of independent draws (SPEC §10.4).
    """
    from flash import tourney
    tr = tourney.run(model, tokenizer, task, requested_k=requested_k,
                     max_tokens=max_tokens, width=width, stage=stage)
    res = SolveResult(task_id=tr.task_id, solved=tr.solved,
                      seconds=tr.seconds, tournament=tr.fields())
    for c in tr.candidates:
        res.attempts.append(Attempt(code=c.code, ok=c.ok, err=c.err))
    return res


def tournament_fields(r: SolveResult) -> dict:
    """The tournament's ledger/trace fields, or {} when the chain ran."""
    return {} if r.tournament is None else {"tournament": r.tournament}


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

    model, tok = load_model(small_repo)
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


ADAPTER = ""


def adapter_path(name: str | None = None) -> str | None:
    """Resolve the adapter this run carries, or None for the base model.

    `name=None` means "whatever the run was told to carry" (the module's
    ADAPTER, set from --adapter the way CONSTRAIN and EDIT are set from the same
    parameters); `name=""` means base model explicitly, which is what the brain
    is loaded as.

    A name that holds no weights raises rather than falling back. An "after" arm
    that quietly loaded the base model would print the most convincing wrong
    number this project can generate: a before/after where both sides are the
    before.
    """
    p = (ADAPTER if name is None else name).strip()
    if not p:
        return None
    if not (Path(p) / "adapters.safetensors").exists():
        raise FileNotFoundError(
            f"adapter {p} holds no adapters.safetensors — refusing to run the "
            f"base model under this name")
    return p


def load_model(repo: str, adapter: str | None = None):
    """mlx load for one tier, with that tier's adapter on top when it has one.

    Only the small tier is ever adapter-carrying: these weights were fit on the
    small model's own verified outcomes, and a 7B's LoRA over the 30B brain is a
    different claim from the one R-6.4 makes.
    """
    from mlx_lm import load

    ad = adapter_path(adapter)
    return load(repo, adapter_path=ad) if ad else load(repo)


def model_label(repo: str, adapter: str | None = None) -> str:
    """The name a ledger row or trace event carries for this model."""
    from flash.train import label_for

    return label_for(repo, adapter_path(adapter))


def small_label(repo: str) -> str:
    return model_label(repo)


def big_label(repo: str) -> str:
    """The brain's name: always base, whatever the small tier carries.

    `""` rather than None, so a run that DOES carry an adapter still records
    the 30B as itself — otherwise the label would claim an adapter mlx was never
    asked to load.
    """
    return model_label(repo, "")


def _load_router():
    """Learned router bundle (flash/learn.py); None until `router-fit` saves one."""
    global _ROUTER
    if _ROUTER == "unset":
        from flash.learn import load_router
        _ROUTER = load_router()
    return _ROUTER


def fail_output(r: SolveResult) -> str:
    """The diagnostic a loss is reported with.

    For a tournament it is the candidate that got furthest, not the last one
    drawn — that is the only thing the ranking is for (§33.4), and a record
    that names the wrong near-miss misleads whatever reads it next.
    """
    if r.tournament is not None:
        i = r.tournament["surfaced"]
        return (r.attempts[i].err if i < len(r.attempts) else "")[-200:]
    return (r.attempts[-1].err if r.attempts else "")[-200:]


def surfaced_attempt(r: SolveResult):
    """The attempt an outside reader should judge: the tournament's best
    candidate when one ran, else the last chain attempt."""
    if not r.attempts:
        return None
    if r.tournament is not None:
        i = r.tournament["surfaced"]
        return r.attempts[i] if i < len(r.attempts) else None
    return r.attempts[-1]


def confidence_eligible(confidence: bool, task: dict) -> tuple[bool, str]:
    """Whether R-2.3's evidence streams can run on this task's answer.

    The four signals are single-answer shaped: coverage and the edge probe both
    execute one `solution.py`. A multi-file or edit task's output is a file set
    or a patch series, so the honest answer is a refusal with a reason, not a
    number.
    """
    if not confidence:
        return False, "confidence off"
    if task.get("multi") or task.get("edit"):
        return False, "multi-file/edit task: signals are single-answer shaped"
    return True, ""


def assess_confidence(task: dict, r: SolveResult) -> dict:
    """Fill `r.confidence` with what verification actually covered and return
    its ledger fields. §34.2's point in one call: the model's own probability
    is not evidence, four real runs are."""
    from flash import confidence
    a = surfaced_attempt(r)
    if a is None or not a.code:
        return {}
    r.confidence = confidence.evaluate(a.code, task["test"])
    return r.confidence.fields()


def solve_routed(small_repo: str, big_repo: str, task: dict, root,
                 small_attempts: int = 2, big_attempts: int = 2,
                 max_tokens: int = 1024, max_chars: int = 4000,
                 threshold: float = 0.5, allow_big: str = "auto",
                 tournament: int = 1, confidence: bool = False
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

    `tournament` (R-3.3) replaces the small tier's chain with k independent,
    oracle-scored candidates — but only where a tournament is actually a
    tournament. With the governor at width 1 there are no independent draws to
    pick between, and the attempt budget buys more as a feedback repair that
    names a failing assert than as a second sample of the same distribution;
    so width 1 keeps the chain. Multi-file and edit tasks keep the chain too
    (a candidate is a whole project there, not an answer).

    `confidence` (R-2.3) adds §34.2's prospective evidence table on the answer
    this run surfaces — on BOTH tiers, and on a failure too, because "what did
    you actually verify" is the same question either way. It never changes the
    tier or the verdict: the offer is for a human, or for a later policy.
    """
    import mlx.core as mx
    from mlx_lm import load
    from flash import ledger, power
    from flash.route import route_task, tier_name

    task = enrich_task(task, root, max_chars)
    # R-5.3: adopt whatever the dead session left on THIS task before any arm
    # runs, so the chain/patch/tournament below sees a resumed span instead of
    # starting a cold one. A frame naming another task is left on disk alone:
    # its own task will pick it up when the loop reaches it.
    if checkpoint.armed():
        checkpoint.handoff(checkpoint.session(), task["id"])
    entry = {"task_id": task["id"], "prompt": task["prompt"][:300],
             "ctx": bool(task.get("context")),
             # the label is the model that made the decision, adapter included:
             # a ledger row that says "7B" for a 7B+lora is a wrong attribution,
             # not a shorthand one.
             "small": small_label(small_repo), "big": big_label(big_repo)}
    st, caps = power.governor(power.peak_gb(small_repo))
    entry["profile"] = caps.profile
    model, tok = load_model(small_repo)
    routed = tier_name(route_task(model, tok, task))

    # Learned router (M3): score from outcomes-trained bundle. Zero extra model
    # load — the small tier is already resident, so the embedding is ~free.
    # The bundle is model-specific: it holds one model's PCA basis, pooled one
    # particular way, so the probe is embedded the way the fit embedded (a
    # mean-pooled probe against a last-pooled fit scores every prompt wrong in
    # silence) and a dimension mismatch refuses instead of raising.
    route_p = None
    route_why = None
    router = _load_router()
    if router is not None:
        from flash.learn import bundle_labels, embed_text, probe_pool, route_score
        fit_repo, _ = bundle_labels(router)
        pool = probe_pool(router)
        route_p, why = route_score(router, embed_text(model, tok, task["prompt"],
                                                      pool=pool))
        route_why = why or None
        if route_p is not None:
            entry["route_p"] = round(route_p, 3)
            entry["route_by"] = f"{fit_repo}/{pool}"
    if route_p is not None and route_p >= threshold:
        routed = "big(learned)"                # outcome-trained big shortcut

    big_ok, big_why = power.allow_model(big_repo, caps)
    if allow_big == "always":
        big_ok, big_why = True, ""
    elif allow_big == "never":
        big_ok, big_why = False, "allow-big=never"

    # §33.6: the whole routing decision is one replayable record — what the
    # router said, what the governor allowed, and which tier actually ran.
    # R-3.3's eligibility is part of that decision, and its refusal reason
    # goes in the record: a reader must be able to tell "tournament off" from
    # "tournament asked for and declined by the width cap".
    from flash import tourney
    tour_ok, tour_why = tourney.eligible(tournament, caps.tournament_width, task)
    conf_ok, conf_why = confidence_eligible(confidence, task)
    trace.event("route", task_id=task["id"], routed=routed,
                route_p=None if route_p is None else round(route_p, 4),
                route_why=route_why,
                profile=caps.profile, allow_big=allow_big,
                big_allowed=big_ok, multi=bool(task.get("multi")),
                tournament=tournament, tournament_used=tour_ok,
                conf_on=confidence, conf_used=conf_ok,
                why=None if big_ok else big_why,
                tour_why=tour_why or None, conf_why=conf_why or None)

    def with_conf(r: SolveResult) -> None:
        """Merge §34.2's evidence fields into the record this run ends in.
        Called at every return path, so a shed or a big answer carries the
        same evidence as a small one — the gate counts offers, not tiers."""
        if conf_ok:
            entry.update(assess_confidence(task, r))

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
            r = solve(model, tok, task, big_attempts + extra, max_tokens,
                      stage="big")
            del model, tok
            mx.clear_cache()
            tier = "big" if r.solved else "failed"
            if not r.solved:                 # diagnose big-direct failures too
                entry["fail_output"] = fail_output(r)
            with_conf(r)
            ledger.record({**entry, "tier": tier, "routed": routed,
                           "solved": r.solved, "attempts": r.n_attempts,
                           "seconds": r.seconds})
            return r, tier, routed

    # R-5.3, the part that is not about tokens: a frame that names the big tier
    # means the dead run had ALREADY exhausted the small one. Re-running the
    # small tier would spend the generations the checkpoint exists to avoid —
    # and, since one session holds one frame, it would overwrite the very state
    # the big tier resumes from. So the escalation is taken as settled.
    f = checkpoint.current()
    if f is not None and f.task_id == task["id"] and f.stage == "big":
        entry["resumed"] = "big"
        r_small = SolveResult(task_id=task["id"], solved=False)
        trace.event("resume", task_id=task["id"], stage="big",
                    reason="the dead run had already failed the small tier")
    else:
        r_small = (_tourney_arm(model, tok, task, tournament,
                                caps.tournament_width, max_tokens, stage="small")
                   if tour_ok else
                   solve(model, tok, task, small_attempts, max_tokens,
                         stage="small"))
    entry.update(tournament_fields(r_small))
    if r_small.solved:
        with_conf(r_small)
        ledger.record({**entry, "tier": "small", "routed": routed,
                       "solved": True, "attempts": r_small.n_attempts,
                       "seconds": r_small.seconds})
        return r_small, "small", routed
    if not big_ok:        # would escalate, cannot: shed and say so in the ledger
        entry["shed"] = big_why
        trace.event("escalation", task_id=task["id"], denied=True, tier="big",
                    why=big_why)
        with_conf(r_small)
        ledger.record({**entry, "tier": "shed", "routed": routed,
                       "solved": False, "attempts": r_small.n_attempts,
                       "seconds": r_small.seconds})
        del model, tok
        mx.clear_cache()
        return r_small, "shed", routed
    entry["fail_output"] = fail_output(r_small)   # feature: WHY small failed
    del model, tok
    mx.clear_cache()                         # hot-swap: free before big loads

    trace.event("escalation", task_id=task["id"], denied=False, tier="big",
                reason=f"small failed after {r_small.n_attempts} attempt(s)")
    model, tok = load(big_repo)
    # stage="big" is not decoration: the frame identity is (task, arm, stage,
    # attempt), and a big tier that labels itself small both clobbers the small
    # tier's frame and makes the `resumed == "big"` skip above unreachable.
    r_big = solve(model, tok, task, big_attempts, max_tokens, stage="big")
    del model, tok
    mx.clear_cache()
    r_big.attempts = r_small.attempts + r_big.attempts   # full audit trail
    r_big.seconds = round(r_small.seconds + r_big.seconds, 1)
    tier = "big" if r_big.solved else "failed"
    with_conf(r_big)
    ledger.record({**entry, "tier": tier, "routed": routed,
                   "solved": r_big.solved, "attempts": r_big.n_attempts,
                   "seconds": r_big.seconds})
    return r_big, tier, routed
