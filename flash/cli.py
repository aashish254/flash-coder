"""flash — CLI entry point (M1 skeleton).

  python -m flash.cli decide "refactor the auth module" --options bugfix feature refactor docs
  python -m flash.cli decide-batch            # built-in routing demo, prints accuracy
  python -m flash.cli bench ...               # wraps benchmarks/m0_bakeoff.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"

# Tiny routing eval: (task_text, expected_category) — grows into the real
# router training set from loop traces (PLAN §27, §32.4).
ROUTING_DEMO = [
    ("fix the crash when saving an empty file", "bugfix"),
    ("add OAuth login to the CLI", "feature"),
    ("rename getData to fetch_data everywhere", "refactor"),
    ("write docstrings for the parser module", "docs"),
    ("the tests fail on Windows paths", "bugfix"),
    ("support exporting results as CSV", "feature"),
    ("extract this duplicated block into a helper", "refactor"),
    ("update the README install section", "docs"),
    ("off-by-one error in pagination", "bugfix"),
    ("add a --verbose flag to the build command", "feature"),
    ("split this 300-line function up", "refactor"),
    ("document the config file format", "docs"),
]
CATEGORIES = ["bugfix", "feature", "refactor", "docs"]


def cmd_decide(args) -> int:
    from mlx_lm import load
    from flash.decide import decide

    model, tok = load(args.model)
    d = decide(model, tok, args.context, args.options)
    print(json.dumps({"choice": d.choice, "confidence": round(d.confidence, 3),
                      "ms": d.ms, "probs": {k: round(v, 3) for k, v in d.probs.items()}},
                     indent=2))
    return 0


def cmd_decide_batch(args) -> int:
    from mlx_lm import load
    from flash.decide import decide

    model, tok = load(args.model)
    correct, total_ms = 0, 0.0
    for text, expected in ROUTING_DEMO:
        d = decide(model, tok, f"Coding task: {text}", CATEGORIES,
                   question="What kind of task is this?")
        ok = d.choice == expected
        correct += ok
        total_ms += d.ms
        print(f"  {'OK ' if ok else 'MISS'} [{d.choice:>8} @ {d.confidence:4.2f}, {d.ms:5.1f}ms]  {text}")
    n = len(ROUTING_DEMO)
    print(f"\nrouting accuracy: {correct}/{n}  ({correct/n:.0%})   "
          f"avg decision: {total_ms/n:.1f}ms")
    return 0 if correct == n else 1


def cmd_router_fit(args) -> int:
    """M3: fit the learned router on ledger outcomes; leave-one-out report."""
    from flash import ledger
    from flash.learn import embed_prompts, features, loo_report, pca, trainable
    import numpy as np
    rows = [r for r in ledger.load() if trainable(r)]
    if len(rows) < 10:
        print(f"only {len(rows)} ledger rows — run more suites first")
        return 1
    print(loo_report(np.array([features(r) for r in rows]), rows, "coarse"))
    print()
    X, kept = embed_prompts(rows, args.small)
    print(loo_report(pca(X, k=8), kept, "embeddings+pca8"))
    from flash.learn import fit_router, save_router
    save_router(fit_router(kept, X))
    print("\nrouter bundle saved -> benchmarks/results/router.npz "
          "(solve_routed: big-direct when P(need-big) >= 0.5)")

    print("\ncaveat: pipeline proof with few positives; the ledger grows n for free")
    return 0



def cmd_ledger(args) -> int:
    """M3: summarize the outcome ledger (the future router's training data)."""
    from flash import ledger
    rows = ledger.load()
    print(ledger.summary(rows))
    if args.tail and rows:
        print("\nlast runs:")
        for r in rows[-args.tail:]:
            print(f"  {r['task_id']:<22} tier={r['tier']:<7} routed={r['routed']:<6} "
                  f"solved={r['solved']} attempts={r['attempts']} ({r['seconds']}s)")
    return 0



def cmd_route_test(args) -> int:
    """Route m0+m2 tasks small-vs-big, scored against hardware-proven labels."""
    from mlx_lm import load
    from flash.context import digest
    from flash.harness import load_tasks
    from flash.route import route_task, tier_name

    model, tok = load(args.model)
    m2_ctx = digest(str(ROOT / "benchmarks" / "fixtures"), args.max_chars)
    rows: list[tuple[dict, str, str]] = []
    for t in load_tasks():                      # m0: 7B solved 18/20 on hardware
        need = "big" if t["id"] in ("t14_topk_frequent", "t15_parse_log") else "small"
        rows.append((t, "", need))
    for t in load_tasks(ROOT / "benchmarks" / "tasks" / "m2_tasks.jsonl"):
        rows.append((t, m2_ctx, "small"))       # 7B solved all w/ semantic skeleton

    correct, total_ms = 0, 0.0
    for t, ctx, want in rows:
        d = route_task(model, tok, t, ctx)
        got = tier_name(d)
        ok = got == want
        correct += ok
        total_ms += d.ms
        print(f"  {'OK ' if ok else 'MISS'} [{got:>5} @ {d.confidence:4.2f} "
              f"want={want:<5} {d.ms:5.0f}ms] {t['id']}", flush=True)
    n = len(rows)
    print(f"\nrouting: {correct}/{n} ({correct / n:.0%})   "
          f"avg {total_ms / n:.0f}ms/decision")
    return 0


def cmd_solve_all(args) -> int:
    from mlx_lm import load
    from flash.harness import load_tasks
    from flash.loop import solve

    model, tok = load(args.model)
    tasks = load_tasks(args.tasks)[: args.max_tasks]
    if args.with_context:                      # M2: inject each task's repo skeleton
        from flash.loop import enrich_task
        tasks = [enrich_task(t, ROOT, args.max_chars) for t in tasks]
    pass1 = passk = 0
    for t in tasks:
        r = solve(model, tok, t, max_attempts=args.attempts, max_tokens=args.max_tokens)
        pass1 += r.attempts[0].ok
        passk += r.solved
        print(f"  {'PASS' if r.solved else 'FAIL'} {t['id']:<22} "
              f"attempts={r.n_attempts} first={'ok' if r.attempts[0].ok else 'no'} "
              f"({r.seconds}s)", flush=True)
    n = len(tasks)
    print(f"\npass@1: {pass1}/{n} ({pass1/n:.0%})   "
          f"pass@{args.attempts}: {passk}/{n} ({passk/n:.0%})   "
          f"loop delta: +{passk - pass1} tasks")
    return 0 if passk == n else 1


def cmd_escalate_test(args) -> int:
    """Run the 2 known-hard tasks through small->big escalation policy."""
    from flash.harness import load_tasks
    from flash.loop import solve_with_escalation

    hard = [t for t in load_tasks() if t["id"] in ("t14_topk_frequent", "t15_parse_log")]
    for t in hard:
        r, tier = solve_with_escalation(args.small, args.big, t)
        print(f"  [{'ESC->BIG' if tier == 'big' else tier.upper():>9}] {t['id']:<22} "
              f"solved={r.solved} attempts={r.n_attempts} ({r.seconds}s)", flush=True)
    return 0
def cmd_run(args) -> int:
    """The full agent on one task: PERCEIVE(repo) -> ROUTE -> small -> ESC."""
    from flash import trace
    from flash.loop import solve_routed

    task = {"id": "adhoc", "prompt": args.prompt,
            "test": open(args.test).read()}
    if args.context:
        task["context"] = args.context
    trace.CAPTURE = args.trace_full
    sid = trace.open_session("run", cmd="run", params={"prompt": args.prompt[:200],
                                                       "small": args.small,
                                                       "big": args.big,
                                                       "allow_big": args.allow_big})
    r, tier, routed = solve_routed(args.small, args.big, task, ROOT,
                                   small_attempts=args.attempts,
                                   big_attempts=args.attempts,
                                   max_tokens=args.max_tokens,
                                   allow_big=args.allow_big)
    trace.event("task_end", task_id=task["id"], solved=r.solved, tier=tier,
                attempts=r.n_attempts, seconds=r.seconds, routed=routed)
    trace.close_session(solved=r.solved)
    print(f"routed={routed} tier={tier}")
    print("--- code ---")
    print(r.attempts[-1].code)
    print(f"--- solved={r.solved} attempts={r.n_attempts} ({r.seconds}s) ---")
    print(f"[trace] replay this run:  flash trace show {sid}")
    return 0 if r.solved else 1


SUITE_PARAMS = ("small", "big", "tasks", "with_context", "attempts", "max_tasks",
                "max_tokens", "max_chars", "threshold", "allow_big")


def _run_suite(params: dict, sid: str | None = None) -> int:
    """The suite engine, shared by `run-suite` and `resume` (§33.7).

    The trace session IS the snapshot: every finished task appends a
    task_end record, so an interrupted run resumes at the next unfinished
    task without re-running (or re-billing) the ones already done.
    """
    from flash import trace
    from flash.harness import load_tasks
    from flash.loop import solve_routed

    if params["threshold"] <= 1.0:          # gate on -> keep the router learning
        from flash.learn import autofit_if_stale
        msg = autofit_if_stale(params["small"])
        if msg:
            print(msg, flush=True)
    tasks = load_tasks(params["tasks"])[: params["max_tasks"]]
    if params["with_context"]:
        from flash.loop import enrich_task
        tasks = [enrich_task(t, ROOT, params["max_chars"]) for t in tasks]

    done = trace.positions(sid) if sid else {}
    if sid:
        trace.attach(sid, cmd="run-suite", params=params)
        print(f"[trace] resuming {sid} — {len(done)} task(s) already settled, "
              f"{len(tasks) - len([t for t in tasks if t['id'] in done])} to go",
              flush=True)
    else:
        sid = trace.open_session("run-suite", cmd="run-suite", params=params)
        print(f"[trace] session {sid} — interrupt me anytime, then: "
              f"flash resume {sid}", flush=True)

    solved = small_n = big_n = shed_n = 0
    total = 0.0
    ran = 0
    try:
        for t in tasks:
            if t["id"] in done:
                d = done[t["id"]]
                solved += bool(d.get("solved"))
                small_n += d.get("tier") == "small"
                big_n += d.get("tier") == "big"
                shed_n += d.get("tier") == "shed"
                total += d.get("seconds") or 0.0
                print(f"  [{'CACHED' if d.get('solved') else str(d.get('tier', 'failed')).upper():>9}] "
                      f"{t['id']:<22} solved={d.get('solved')} (already in session, "
                      f"not re-run)", flush=True)
                continue
            r, tier, routed = solve_routed(params["small"], params["big"], t, ROOT,
                                           small_attempts=params["attempts"],
                                           big_attempts=params["attempts"],
                                           max_tokens=params["max_tokens"],
                                           threshold=params["threshold"],
                                           allow_big=params["allow_big"])
            ran += 1
            trace.event("task_end", task_id=t["id"], solved=r.solved, tier=tier,
                        attempts=r.n_attempts, seconds=r.seconds, routed=routed)
            solved += r.solved
            small_n += tier == "small"
            big_n += tier == "big"
            shed_n += tier == "shed"
            total += r.seconds
            print(f"  [{('SHED' if tier == 'shed' else 'ESC->BIG' if tier == 'big' else tier.upper()):>9}] "
                  f"{t['id']:<22} solved={r.solved} attempts={r.n_attempts} "
                  f"({r.seconds}s)", flush=True)
    except KeyboardInterrupt:
        print(f"\ninterrupted after {ran} task(s). {len(done) + ran} of {len(tasks)} "
              f"are settled; nothing written to the ledger is lost.\n"
              f"  continue:  flash resume {sid}", flush=True)
        return 130
    n = len(tasks)
    trace.close_session(solved=solved, tasks=n, ran=ran)
    print(f"\nsolved {solved}/{n}   small-tier {small_n}, escalated {big_n}   "
          f"total {total:.0f}s (avg {total / n:.1f}s/task)")
    print(f"[trace] flash trace show {sid}")
    if shed_n:
        from flash import power
        _, caps = power.governor(refresh=True)
        print(f"{shed_n} task(s) denied escalation by the §34.1 governor "
              f"({caps.profile}): {'; '.join(caps.reasons)}")
        print("   rerun with --allow-big always to force the brain on battery/heat")
    return 0 if solved == n else 1


def cmd_run_suite(args) -> int:
    """Full policy over a suite: PERCEIVE -> ROUTE -> small -> ESC per task."""
    from flash import trace
    params = {k: getattr(args, k) for k in SUITE_PARAMS}
    sid = None
    if args.resume is not None:
        sid = args.resume or (trace.interrupted("run-suite") or [None])[-1]
        if not sid:
            print("nothing to resume — no interrupted run-suite session")
            return 2
        if not trace.read(sid):
            print(f"no trace session '{sid}'")
            return 2
        print(f"resuming session {sid}")
    trace.CAPTURE = args.trace_full
    return _run_suite(params, sid)


def cmd_resume(args) -> int:
    """§33.7: continue the last (or a named) interrupted suite from its snapshot."""
    from flash import trace
    sid = args.session or (trace.interrupted("run-suite") or [None])[-1]
    if not sid:
        print("nothing to resume — no interrupted run-suite session "
              "(flash trace ls to see recorded runs)")
        return 0
    params = trace.session_params(sid)
    if not params:
        print(f"session {sid} stored no parameters — rerun run-suite with flags "
              f"and pass --resume {sid}")
        return 2
    for k in SUITE_PARAMS:                      # explicit flags outrank the snapshot
        v = getattr(args, k, None)
        if v is not None:
            params[k] = v
    trace.CAPTURE = bool(args.trace_full)
    print(f"continuing {sid} under: " + ", ".join(f"{k}={v}" for k, v in params.items()))
    return _run_suite(params, sid)


def cmd_trace(args) -> int:
    """§33.6: the trace store and its replay view."""
    from flash import trace
    if args.selftest:
        return trace.run_selftest()
    if args.show:
        print(trace.render(args.show, task_id=args.task, full=args.full))
        return 0
    sids = trace.list_sessions()
    if not sids:
        print("no trace sessions yet — run-suite records one")
        return 0
    for sid in sids:
        s = trace.summarize(sid)
        print(f"{sid:<44} {s['cmd'] or '-':<10} {s['solved']}/{s['tasks']:<3} solved "
              f"{'closed' if s['closed'] else 'INTERRUPTED -> resume':<24} "
              f"{s['prompt_tokens'] + s['completion_tokens']:>7} tok  {s['wall_s']:>7}s wall")
    return 0




def cmd_run_vis_suite(args) -> int:
    """M0b: screenshot->HTML suite through the VLM with test-feedback retries."""
    from flash import ledger
    from flash.harness import load_tasks
    from flash.vision import solve_vision

    tasks = load_tasks(args.tasks)[: args.max_tasks]
    solved = 0
    total = 0.0
    for t in tasks:
        r = solve_vision(args.vlm, t, max_attempts=args.attempts,
                         max_tokens=args.max_tokens)
        solved += r.solved
        total += r.seconds
        print(f"  [{'VISION' if r.solved else 'FAILED':>9}] {t['id']:<22} "
              f"solved={r.solved} attempts={r.n_attempts} ({r.seconds:.1f}s)",
              flush=True)
        if not r.solved:
            # full err: the quadrant report IS the diagnosis (truncating to 140
            # chars hid the MISMATCH lines that explain WHERE it went wrong)
            print(f"           err: {r.attempts[-1][1]}", flush=True)
        # M3 flywheel: vision outcomes go to the ledger under tier="vision" so
        # stats see them but the small/big router filter excludes them
        ledger.record({"prompt": t["prompt"], "tier": "vision",
                       "attempts": r.n_attempts, "solved": r.solved,
                       "seconds": round(r.seconds, 1), "task_id": t["id"]})
    n = len(tasks)
    print(f"\nsolved {solved}/{n}   total {total:.0f}s "
          f"(avg {total / n:.1f}s/task)")
    return 0 if solved == n else 1





def cmd_solve(args) -> int:
    """Solve one ad-hoc task, optionally with repo context injected (M2)."""
    from mlx_lm import load
    from flash.loop import solve

    prompt = args.prompt
    if args.context:
        from flash.context import digest
        prompt = (f"Project context (real API signatures — use exactly these):\n"
                  f"{digest(args.context, args.max_chars)}\n\nTask: {args.prompt}")
    if args.docs:                             # Phase-4: knowledge as a tool
        from flash import web
        urls = [u.strip() for u in args.docs.split(",") if u.strip()]
        recs = web.lookup(args.prompt, urls, budget=args.docs_budget,
                          offline=args.docs_offline)
        for r in recs:
            print(f"  [docs {r.ranker:>3} "
                  f"{'cache' if r.cached else 'FETCH':>5} {len(r.text)}c] "
                  f"{r.url}" if r.ok else f"  [docs ERR] {r.url}: {r.error}",
                  flush=True)
        block = web.format_for_prompt(recs)
        if block:
            prompt = f"{block}\n\nTask: {args.prompt}" if not args.context \
                else f"{block}\n\n{prompt}"
    task = {"id": "adhoc", "prompt": prompt,
            "test": open(args.test).read() if args.test else "print('__PASS__')"}
    model, tok = load(args.model)
    r = solve(model, tok, task, max_attempts=args.attempts)
    print("--- code ---")
    print(r.attempts[-1].code)
    print(f"--- solved={r.solved} attempts={r.n_attempts} ({r.seconds}s) ---")
    return 0 if r.solved else 1


def cmd_context(args) -> int:
    from flash.context import digest
    print(digest(args.path, max_chars=args.max_chars))
    return 0


def cmd_web(args) -> int:
    """Phase-4 web tool: fetch -> cache -> bge-rank excerpts (PLAN §25a3)."""
    from flash import web
    if args.selftest:
        return web.run_selftest()
    urls = [u.strip() for u in args.urls.split(",") if u.strip()]
    if not urls:
        print("no --urls given"); return 2
    recs = web.lookup(args.query, urls, budget=args.budget,
                      offline=args.offline, force_kw=args.kw)
    for r in recs:
        if r.ok:
            print(f"  [{r.ranker:>3} sim={r.top1_sim:.3f} "
                  f"{'cache' if r.cached else 'FETCH':>5} "
                  f"{len(r.text)}c] {r.url}")
        else:
            print(f"  [ERR] {r.url}: {r.error}")
    print()
    block = web.format_for_prompt(recs)
    print(block if block else "(no excerpts — all docs failed)")
    return 0 if any(r.ok for r in recs) else 1


def cmd_perceive(args) -> int:
    from flash.perceive import static_check
    code = open(args.file, encoding="utf-8").read()
    diags = static_check(code)
    if not diags:
        print(f"{args.file}: clean")
        return 0
    for d in diags:
        print(f"{args.file}:{d.line} [{d.severity}] {d.message} ({d.source})")
    return 1 if any(d.severity == "error" for d in diags) else 0


def cmd_find(args) -> int:
    """§33.1: where is this symbol defined? (AST discovery + live LSP ranges)"""
    from flash.lsp import find_symbol
    syms = find_symbol(args.path, args.name, use_lsp=not args.ast)
    if not syms:
        print(f"{args.name}: not defined under {args.path}")
        return 1
    for s in syms:
        print(f"{s}")
    return 0


def cmd_refs(args) -> int:
    """§33.1: every site that mentions this symbol."""
    from flash.lsp import find_references
    locs = find_references(args.path, args.name, use_lsp=not args.ast)
    for loc in locs:
        print(f"{loc.path}:{loc.line}:{loc.char}")
    print(f"\n{len(locs)} reference site(s) for {args.name}")
    return 0 if locs else 1


def cmd_symbols(args) -> int:
    """§33.1: outline one file (live ranges) or the whole tree (AST table)."""
    from flash.lsp import outline
    one = args.file or args.name
    if one:
        from flash.lsp import Lsp
        with Lsp(args.path) as lsp:
            for s in lsp.document_symbols(one):
                print(f"{s}")
        return 0
    for s in outline(args.path):
        print(f"{s}")
    return 0


def cmd_lsp_selftest(args) -> int:
    from flash.lsp import run_selftest
    return run_selftest()


def cmd_learn(args) -> int:
    """§34.3: background learning, gated on AC + idle user, resumable."""
    from flash import jobs
    if args.selftest:
        return jobs.run_selftest()
    if args.status:
        st = jobs.load_state(args.kind)
        if st is None:
            print(f"no checkpoint for job '{args.kind}' — it has never run")
            return 0
        print(json.dumps(st.to_dict(), indent=2))
        return 0
    if args.check:
        ok, why = jobs.eligibility(force=args.force, min_new=args.min_new)
        print(("eligible: " if ok else "refused: ") + ("; ".join(why) or "gate open"))
        return 0 if ok else 1
    msg = jobs.run_fit(args.small, budget_s=args.budget, chunk=args.chunk,
                       force=args.force, kind=args.kind)
    print(msg)
    return 0 if "refused" not in msg else 1


def cmd_power(args) -> int:
    """§34.1: what the machine can afford right now, and why."""
    from flash import power
    if args.selftest:
        return power.run_selftest()
    if args.json:
        from dataclasses import asdict
        st, caps = power.governor(args.need, refresh=True)
        print(json.dumps({"state": asdict(st), "caps": caps.to_dict()}, indent=2))
        return 0
    print(power.report(args.need))
    return 0


def cmd_bench(args) -> int:
    return subprocess.call([sys.executable, str(ROOT / "benchmarks" / "m0_bakeoff.py"),
                            *args.bench_args])


def main() -> int:
    ap = argparse.ArgumentParser(prog="flash", description="Flash Coder CLI (M1)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("decide", help="one typed decision, one forward pass")
    p.add_argument("context")
    p.add_argument("--options", nargs="+", required=True)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.set_defaults(fn=cmd_decide)

    p = sub.add_parser("decide-batch", help="run the built-in routing demo")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.set_defaults(fn=cmd_decide_batch)

    p = sub.add_parser("route-test", help="route m0+m2 tasks small-vs-big vs known labels")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--max-chars", type=int, default=4000)
    p.set_defaults(fn=cmd_route_test)

    p = sub.add_parser("solve-all", help="ACT->VERIFY->retry loop over all tasks")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--max-tasks", type=int, default=20)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--tasks", default=None, help="jsonl path (default: m0 suite)")
    p.add_argument("--with-context", action="store_true",
                   help="M2: inject each task's repo skeleton into the prompt")
    p.add_argument("--max-chars", type=int, default=4000)
    p.set_defaults(fn=cmd_solve_all)

    p = sub.add_parser("escalate-test", help="small->big hot-swap escalation demo")
    p.add_argument("--small", default=DEFAULT_MODEL)
    p.add_argument("--big", default="mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit")
    p.set_defaults(fn=cmd_escalate_test)

    p = sub.add_parser("router-fit", help="M3: fit learned router on the ledger (leave-one-out report)")
    p.add_argument("--small", default="mlx-community/Qwen2.5-Coder-7B-Instruct-4bit")
    p.set_defaults(fn=cmd_router_fit)


    p = sub.add_parser("ledger", help="M3: outcome ledger summary (router training data)")
    p.add_argument("--tail", type=int, default=10)
    p.set_defaults(fn=cmd_ledger)


    p = sub.add_parser("run", help="full policy on one task: PERCEIVE->ROUTE->small->ESC")
    p.add_argument("prompt")
    p.add_argument("--test", required=True, help="file with asserts verifying the solution")
    p.add_argument("--context", help="repo path whose skeleton is injected (M2)")
    p.add_argument("--small", default=DEFAULT_MODEL)
    p.add_argument("--big", default="mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit")
    p.add_argument("--attempts", type=int, default=2, help="per tier")
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--allow-big", choices=("auto", "always", "never"), default="auto",
                   help="escalation vs the §34.1 power governor: auto obeys the "
                        "profile (battery/heat/memory shed the brain), always is "
                        "the benchmark override, never is single-track")
    p.add_argument("--trace-full", action="store_true",
                   help="§33.6: also store the exact prompts and outputs, so the "
                        "run can be re-fed to a model")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("run-suite", help="full policy over a suite (cost report)")
    p.add_argument("--small", default=DEFAULT_MODEL)
    p.add_argument("--big", default="mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit")
    p.add_argument("--tasks", default=None, help="jsonl path (default: m0 suite)")
    p.add_argument("--with-context", action="store_true")
    p.add_argument("--attempts", type=int, default=2, help="per tier")
    p.add_argument("--max-tasks", type=int, default=25)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--max-chars", type=int, default=4000)
    p.add_argument("--threshold", type=float, default=1.1,
                   help="learned-router P(need-big) cutoff; >1 disables the gate "
                        "(default OFF since held-out m7 (2026-09-24): gate trained "
                        "with its own big-direct rows big-directed 8/8 easy tasks; "
                        "pass 0.5 to enable)")
    p.add_argument("--allow-big", choices=("auto", "always", "never"), default="auto",
                   help="escalation vs the §34.1 power governor (auto sheds the "
                        "brain on battery/heat/memory pressure)")
    p.add_argument("--resume", nargs="?", const="", default=None, metavar="SESSION",
                   help="§33.7: continue an interrupted trace session (blank = the "
                        "latest one) instead of starting a new run")
    p.add_argument("--trace-full", action="store_true",
                   help="§33.6: store exact prompts/outputs too, for re-feeding")
    p.set_defaults(fn=cmd_run_suite)

    p = sub.add_parser("resume", help="§33.7: continue an interrupted suite from its "
                                      "trace snapshot (parameters come from the session)")
    p.add_argument("session", nargs="?", default=None, help="trace session id "
                       "(default: the newest interrupted run-suite session)")
    p.add_argument("--small", default=None)
    p.add_argument("--big", default=None)
    p.add_argument("--tasks", default=None)
    p.add_argument("--with-context", action="store_true", default=None)
    p.add_argument("--attempts", type=int, default=None)
    p.add_argument("--max-tasks", type=int, default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--max-chars", type=int, default=None)
    p.add_argument("--threshold", type=float, default=None)
    p.add_argument("--allow-big", choices=("auto", "always", "never"), default=None)
    p.add_argument("--trace-full", action="store_true")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("trace", help="§33.6: list recorded sessions or replay one")
    p.add_argument("show", nargs="?", default=None, help="session id to replay")
    p.add_argument("--task", default=None, help="only this task's decisions")
    p.add_argument("--full", action="store_true", help="include stored prompts/outputs")
    p.add_argument("--selftest", action="store_true", help="offline deterministic checks")
    p.set_defaults(fn=cmd_trace)

    p = sub.add_parser("run-vis-suite", help="M0b: screenshot->HTML suite via VLM")
    p.add_argument("--tasks", default="benchmarks/tasks/vis_tasks.jsonl")
    p.add_argument("--vlm", default="mlx-community/Qwen3-VL-4B-Instruct-4bit")
    p.add_argument("--attempts", type=int, default=2)
    p.add_argument("--max-tokens", type=int, default=1024)
    p.add_argument("--max-tasks", type=int, default=999)
    p.set_defaults(fn=cmd_run_vis_suite)

    p = sub.add_parser("solve", help="solve one ad-hoc task (M2: --context injects repo skeleton)")
    p.add_argument("prompt")
    p.add_argument("--test", help="file with asserts verifying the solution")
    p.add_argument("--context", help="repo path whose skeleton is injected into the prompt")
    p.add_argument("--max-chars", type=int, default=4000)
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--attempts", type=int, default=3)
    p.add_argument("--docs", default=None,
                   help="Phase-4: comma-separated doc URLs to fetch+rank into "
                        "the prompt (flash.web)")
    p.add_argument("--docs-budget", type=int, default=2500)
    p.add_argument("--docs-offline", action="store_true",
                   help="docs from cache only (§33.9)")
    p.set_defaults(fn=cmd_solve)

    p = sub.add_parser("context", help="token-budgeted project skeleton (M2 PERCEIVE)")
    p.add_argument("--path", default=".")
    p.add_argument("--max-chars", type=int, default=4000)
    p.set_defaults(fn=cmd_context)

    p = sub.add_parser("web", help="Phase-4: fetch+cache+bge-rank doc excerpts "
                                   "(offline-degrading, §33.9)")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--urls", default="", help="comma-separated doc URLs")
    p.add_argument("--budget", type=int, default=2500,
                   help="chars of excerpt per doc (v3 A/B winner)")
    p.add_argument("--offline", action="store_true", help="cache-only")
    p.add_argument("--kw", action="store_true",
                   help="force keyword ranker (skip the bge embedder)")
    p.add_argument("--selftest", action="store_true",
                   help="offline deterministic checks, exit 0/1")
    p.set_defaults(fn=cmd_web)

    p = sub.add_parser("perceive", help="LSP static check of one file")
    p.add_argument("file")
    p.set_defaults(fn=cmd_perceive)

    p = sub.add_parser("find", help="§33.1: where is a symbol defined?")
    p.add_argument("name")
    p.add_argument("--path", default=".")
    p.add_argument("--ast", action="store_true", help="skip the language server")
    p.set_defaults(fn=cmd_find)

    p = sub.add_parser("refs", help="§33.1: every site that mentions a symbol")
    p.add_argument("name")
    p.add_argument("--path", default=".")
    p.add_argument("--ast", action="store_true", help="skip the language server")
    p.set_defaults(fn=cmd_refs)

    p = sub.add_parser("symbols", help="§33.1: symbol outline of a file (LSP) or tree (AST)")
    p.add_argument("name", nargs="?", default=None, help="file to outline")
    p.add_argument("--path", default=".")
    p.add_argument("--file", default=None, help="alias for the positional file")
    p.set_defaults(fn=cmd_symbols)

    p = sub.add_parser("lsp-selftest", help="§33.1: deterministic perception checks")
    p.set_defaults(fn=cmd_lsp_selftest)

    p = sub.add_parser("learn", help="§34.3: gated, resumable background router refit")
    p.add_argument("--small", default=DEFAULT_MODEL)
    p.add_argument("--budget", type=float, default=900.0,
                   help="wall-clock cap per invocation; the rest resumes next idle window")
    p.add_argument("--chunk", type=int, default=8, help="prompts per checkpoint flush")
    p.add_argument("--force", action="store_true",
                   help="override the idle/AC gate (explicit foreground use)")
    p.add_argument("--kind", default="router-fit")
    p.add_argument("--status", action="store_true", help="print the last checkpoint")
    p.add_argument("--check", action="store_true", help="only answer: may it run now?")
    p.add_argument("--min-new", type=int, default=10)
    p.add_argument("--selftest", action="store_true")
    p.set_defaults(fn=cmd_learn)

    p = sub.add_parser("power", help="§34.1: system profile + what the machine may load")
    p.add_argument("--need", type=float, default=4.4, help="GB the caller wants to load")
    p.add_argument("--json", action="store_true", help="machine-readable state + caps")
    p.add_argument("--selftest", action="store_true", help="deterministic decision table")
    p.set_defaults(fn=cmd_power)

    p = sub.add_parser("bench", help="wrap benchmarks/m0_bakeoff.py")
    p.add_argument("bench_args", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_bench)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
