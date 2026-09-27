"""R-1.1b, OFFLINE: can `hint_ab_tasks.jsonl` answer the question the A/B asks?

R-1.1b is a MEASUREMENT clause: does either perception block earn its tokens. A
measurement is only as good as its instrument, and the instrument here is a
frozen suite. Today's repo cannot run that A/B at all — of 22 task files only
`m2_tasks.jsonl` declares a repo context, and of its 5 tasks exactly ONE ever
reaches a retry across the 57 stored traces, so an A/B on that data is n=1. This
suite is the replacement: minishop tasks picked from `hint_ab_candidates.jsonl`
by `gen_hint_ab.py`, over a pilot run, for being in one difficulty band — the
tier's own greedy first answer FAILS, and a repo-defined symbol is at issue in
why. The first-try answer is therefore not a hand-written guess at a mistake but
the mistake this model actually made, stored verbatim as the code the loop
verified.

The gates are about the SHAPE of the instrument, and every one is computed from
the real thing — the real oracle (`harness.diagnose`, whose 400-char verdict is
the exact text `_perceive` ranks on), the real shared symbol index, the real hint
builders, and `loop.solve` driven with a stubbed generator so the retry MESSAGE
is what is inspected, not a hint computed for the record. That substitution is
R-1.1's lesson, applied before the fact rather than after it.

    python benchmarks/hint_ab_check.py            # the gates and the mutants
    python benchmarks/hint_ab_check.py --print    # per-task table too

What is NOT claimed here: that the hints help. Eight tampered copies of the suite
run back through every gate, each required to be caught by the gate that covers
the property it breaks, so a green run means only "this instrument can tell the
arms apart" — the most an offline vector can say about an A/B.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import flash.loop as loop                                   # noqa: E402
from flash import graph, harness                            # noqa: E402
from flash.context import digest                            # noqa: E402
from flash.harness import extract_code                      # noqa: E402
from flash.perceive import format_errors, static_check      # noqa: E402
from flash.graph import SCOPE_HEADER                        # noqa: E402
from flash.lsp import (HINT_HEADER, SymbolIndex, symbol_hint,  # noqa: E402
                       symbols_involved)

SUITE = ROOT / "benchmarks" / "tasks" / "hint_ab_tasks.jsonl"
CTX_DIR = ROOT / "benchmarks" / "fixtures"
SOURCE_BUDGET = 1200        # `lsp.symbol_hint`'s own default max_chars
MIN_CLASSES = 5             # the suite must not be one mistake ten times
ARMS = (("source", "graph"), ("graph",), ("source",), ())
API_KINDS = ("class", "function", "method", "property")


def published() -> set[str]:
    """The fixture's API surface — the names a prompt may not hand over.

    `SymbolIndex` labels an indented assignment a module-level `constant`, so
    `cents = abs(cents)` inside `money()` competes with `BULK_MIN_QTY = 5` on
    kind alone. The source line on disk tells them apart (an unindented binding
    is importable; an indented one is a local), and only the importable names are
    an answer a prompt could leak.
    """
    out: set[str] = set()
    for syms in index().by_name.values():
        for s in syms:
            if s.name.startswith("_"):
                continue
            if s.kind in API_KINDS:
                out.add(s.name)
            elif s.kind in ("constant", "field"):
                line = Path(s.path).read_text().splitlines()[s.line - 1]
                if line[:1] not in (" ", "\t"):
                    out.add(s.name)
    return out

CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


_INDEX: dict = {}


def index() -> SymbolIndex:
    if "i" not in _INDEX:
        _INDEX["i"] = SymbolIndex.build(CTX_DIR)
    return _INDEX["i"]


_ORACLE: dict[tuple[str, str], tuple[bool, str]] = {}


def oracle(code: str, test: str) -> tuple[bool, str]:
    """`harness.diagnose`, memoized: each tampered copy re-runs every gate over
    every task, and each uncached verdict is a sandboxed subprocess."""
    key = (hashlib.sha1(code.encode()).hexdigest(),
           hashlib.sha1(test.encode()).hexdigest())
    if key not in _ORACLE:
        _ORACLE[key] = harness.diagnose(code, test)
    return _ORACLE[key]


def exc_class(err: str) -> str:
    """The failure's class as the retry sees it.

    Two shapes have no exception name at all and each gets its own label rather
    than being folded into the other: a bare assert-diff carries no `Error` word,
    and a STATIC verdict never reached a test run — the latter is a third of this
    suite, and calling it "assert-diff" would hide that the loop short-circuited.
    """
    if err.startswith("STATIC:"):
        m = re.search(r"(undefined name|syntax error|before assignment)", err,
                      re.I)
        return "static:" + (m.group(1).lower().replace(" ", "-") if m
                            else "other")
    names = sorted(set(re.findall(r"\b([A-Z]\w*(?:Error|Exception))\b", err)))
    return names[0] if names else ("assert-diff" if err else "no verdict")


def shown_names(block: str) -> list[str]:
    """The symbols the SOURCE block quotes, in order, bare of their container."""
    return [m.group(2) for m in
            re.finditer(r"^# \S+:\d+\s+(?:([\w.]+\.)?(\w+)\s*\[)", block, re.M)]


def headed_names(block: str) -> list[str]:
    """The symbols the DEPENDENTS block heads — `Cart.total_cents [method]`."""
    return [m.group(1).rsplit(".", 1)[-1] for m in
            re.finditer(r"^\s+(\S+) \[\w+\] \S+:\d+", block, re.M)]


def counted(block: str) -> int:
    m = re.search(r"(\d+) symbol\(s\) at issue not shown", block)
    return int(m.group(1)) if m else 0


def measure(task: dict) -> dict:
    """Everything the gates ask of one task, from the real oracle.

    Three faithfulness rules, each learned from a gate that first failed here:

    * the hint root is the task's own `context` dir, because that is what
      `loop.enrich_task` makes `_ctx_dir` — a record without one must come back
      with silent blocks, which is a property of the suite worth gating on;
    * the verdict is the one `_perceive` RECEIVES, which for an undefined-name
      answer is `STATIC: L2: undefined name 'X'` — `solve` short-circuits the
      test run on a static finding, so a third of this suite never produces a
      traceback for the hint to rank on. Ranking on `diagnose`'s output for them
      would certify an eligibility the live arms never see;
    * `naive` is code, not an answer: `solve` calls `extract_code` before it
      diagnoses, so the raw prose-plus-fences text a model sends is one
      extraction away from what the loop reads. The first pilot's records were
      frozen at that raw layer, which made every one of them fail as
      `unterminated string literal` — a verdict about the extractor, not the
      model. That is why `compiles` is a gate and prose is one too.

    `ranked_alt` is the ranking over the text the loop does NOT use (the traceback
    for a static task, `diagnose`'s verdict for a bare assert-diff), which is what
    makes "would this task still be eligible" a number rather than a hope.
    """
    ctx = str(ROOT / task["context"]) if task.get("context") else ""
    static = format_errors(static_check(task["naive"]))
    try:
        compile(task["naive"], "<naive>", "exec")
        compiles = True
    except (SyntaxError, ValueError):
        compiles = False
    diag_ok, diag_err = oracle(task["naive"], task["test"])
    naive_ok, verdict, vkind = diag_ok, diag_err, "test"
    alt = diag_err if static else harness.run_test(task["naive"],
                                                  task["test"])[1]
    if static:
        verdict, vkind, naive_ok = f"STATIC: {static}", "static", False
    sol_ok, sol_err = oracle(task["solution"], task["test"])
    src = grp = ""
    ranked: list[str] = []
    ranked_alt: list[str] = []
    if ctx and not naive_ok:
        idx = index()
        ranked = [s.name for s in symbols_involved(idx, verdict,
                                                  task["naive"], 3)]
        ranked_alt = [s.name for s in symbols_involved(idx, alt,
                                                       task["naive"], 3)]
        src = symbol_hint(ctx, verdict, task["naive"], index=idx)
        grp = graph.scope_hint(ctx, verdict, task["naive"], index=idx)
    return {"id": task["id"], "naive_ok": naive_ok, "verdict": verdict,
            "vkind": vkind, "sol_ok": sol_ok, "sol_err": sol_err,
            "compiles": compiles,
            "ranked": ranked, "ranked_alt": ranked_alt, "src": src, "grp": grp,
            "cls": exc_class(verdict),
            "sig": (f"{exc_class(verdict)}/{vkind}",
                    ranked[0] if ranked else "")}


ARM_HITS: dict[str, str] = {}


def arm_retry(task: dict, err: str, hints: tuple[str, ...]) -> str:
    """The retry MESSAGE `loop.solve` sends for this task under one arm.

    `solve` is driven with both seams stubbed — the generator replays the frozen
    naive answer, `diagnose` returns the verdict the real oracle already
    produced — so the only live code between the failure and the prompt is
    `_perceive`, which is exactly what R-1.1b's switch selects. A STATIC-class
    answer never reaches the stub at all: `solve` short-circuits on its own
    `static_check`, recomposing the same `STATIC: …` text `measure` ranked on,
    which is why the two cannot disagree about what the model was shown. Cached
    on the record's CONTENT, not its id: two tasks with one id must not share a
    retry.
    """
    key = hashlib.sha1(json.dumps([task.get("id"), task.get("context"),
                                   task.get("prompt"), task.get("naive"),
                                   err, list(hints)],
                                  sort_keys=True).encode()).hexdigest()
    got = ARM_HITS.get(key)
    if got is not None:
        return got
    real_gen, real_diag, real_hints = loop._generate, loop.diagnose, loop.HINTS
    seen: list[list[dict]] = []

    def gen(model, tokenizer, messages, max_tokens, **kw):
        seen.append([dict(m) for m in messages])
        return f"```python\n{task['naive']}\n```\n"

    try:
        loop._generate = gen
        loop.diagnose = lambda code, test: (False, err)
        loop.HINTS = hints
        loop.solve(None, None, dict(task), max_attempts=2, max_tokens=128,
                   debug=False)
    finally:
        loop._generate, loop.diagnose, loop.HINTS = real_gen, real_diag, real_hints
    retry = seen[1][-1]["content"] if len(seen) > 1 else ""
    ARM_HITS[key] = retry
    return retry


def target_name(prompt: str) -> str:
    m = re.search(r"write (\w+)\(", prompt)
    return m.group(1) if m else ""


def gates(tasks: list[dict], keep: dict | None = None) -> None:
    """Every gate, over whatever list is handed in — the frozen suite or one of
    its tampered copies — so a mutant is caught by a gate, not a special case."""
    CHECKS.clear()
    rows = [measure(t) for t in tasks]

    missing = [t.get("id", "?") for t in tasks
               if not {"id", "context", "prompt", "test", "solution",
                       "naive"} <= set(t)
               or not (ROOT / t.get("context", "") / "minishop").is_dir()]
    ck(f"the suite is frozen and complete: {len(tasks)} records, each with "
       "prompt/test/solution/naive and a context dir that holds the package",
       not missing, ",".join(missing))

    prose = [t["id"] for t in tasks
             if extract_code(t["naive"]) != t["naive"].strip("\n")]
    ck("`naive` is already the CODE `solve` verified, not the model's prose-plus-"
       "fences answer — the loop extracts before it diagnoses, so a record "
       "holding raw text would have this vector certify a syntax verdict no "
       "live arm ever reads", not prose, ",".join(prose))

    bad = [r["id"] for r in rows if r["naive_ok"]]
    ck("every naive answer FAILS the real oracle — a task the first try solves "
       "never reaches a retry, so an arm that shows it a hint is not being "
       "asked a question", not bad, ",".join(bad))

    bad = [r["id"] for r in rows if not r["sol_ok"]]
    ck("every reference solution PASSES the real oracle — an unsolvable task "
       "lands as all four arms at zero, which reads exactly like 'the hints do "
       "not help'", not bad, ",".join(f"{r['id']}: {r['sol_err'][-90:]}"
                                      for r in rows if not r["sol_ok"]))

    thin = [r["id"] for r in rows if not r["src"]]
    ck("over each naive answer's REAL verdict the source block is non-empty — "
       "R-1.1c's contract is that silence means 'nothing repo-defined is at "
       "issue', so a silent task is not hint-eligible and must not be scored",
       not thin, ",".join(thin))

    thin = [r["id"] for r in rows if not r["grp"]]
    ck("...and so is the dependents block, over the same verdict and the same "
       "shared index — both blocks have something to show for every task in the "
       "denominator", not thin, ",".join(thin))

    cut = [r["id"] for r in rows if not r["compiles"]]
    ck("every naive COMPILES — a SyntaxError here means the answer was cut off by "
       "the token cap, and a retry over a half-written program scores the harness "
       "budget instead of the symbol the hint names", not cut, ",".join(cut))

    src_max = max((len(r["src"]) for r in rows), default=0)
    grp_max = max((len(r["grp"]) for r in rows), default=0)
    ck("the blocks stay inside their own budgets, so the A/B's added context is "
       f"a number: {SOURCE_BUDGET} chars for source, "
       f"{graph.SCOPE_MAX_CHARS} for dependents",
       src_max <= SOURCE_BUDGET and grp_max <= graph.SCOPE_MAX_CHARS,
       f"largest {src_max} / {grp_max}")

    off = []
    for r in rows:
        shown, gh = shown_names(r["src"]), headed_names(r["grp"])
        if (not r["ranked"] or r["ranked"][:len(shown)] != shown
                or r["ranked"][:len(gh)] != gh
                or len(shown) + counted(r["src"]) != len(r["ranked"])):
            off.append(f"{r['id']}: ranked={r['ranked']} src={shown} "
                       f"counted={counted(r['src'])} graph={gh}")
    ck(f"one ranking, two readers, {len(rows)} failures deep: each block's "
       "symbols are a "
       "PREFIX of the same `symbols_involved` list and every ranked symbol is "
       "either quoted or counted in the tail — the two blocks cannot tell two "
       "stories about one failure, and R-1.1c's counted tail is what keeps the "
       "prefix rule true when the budget bites",
       not off, " | ".join(off[:2]))

    enrich, texts = [], {}
    for t, r in zip(tasks, rows):
        e = loop.enrich_task(dict(t), ROOT)
        enrich.append(e)
        texts[r["id"]] = {a: arm_retry(e, r["verdict"], a) for a in ARMS}
    miss = [i for i, txt in texts.items()
            if HINT_HEADER not in txt[("source", "graph")]
            or SCOPE_HEADER not in txt[("source", "graph")]]
    ck("the suite is eligible AT THE SEAM the A/B measures: `loop.solve` over a "
       "stubbed generator shows every task's retry message carrying BOTH headers",
       not miss, ",".join(miss))

    same, wrong = [], []
    for i, txt in texts.items():
        if len(set(txt.values())) != len(ARMS):
            same.append(i)
        if (HINT_HEADER in txt[("graph",)] or SCOPE_HEADER in txt[("source",)]
                or HINT_HEADER not in txt[("source",)]
                or SCOPE_HEADER not in txt[("graph",)]
                or HINT_HEADER in txt[()] or SCOPE_HEADER in txt[()]):
            wrong.append(i)
    ck("the four arms are four DIFFERENT prompts and each carries exactly its "
       "own blocks — an OFF arm that leaks its hint measures nothing and looks "
       "like a result", not (same or wrong),
       f"indistinct={','.join(same)} mis-set={','.join(wrong)}")

    crowded = []
    for r in rows:
        for arm, txt in texts[r["id"]].items():
            if txt.count(r["verdict"]) != 1:
                crowded.append(f"{r['id']}/{'+'.join(arm) or 'off'}")
    ck("the hint never rewrites the verdict it is appended to: in all four arms "
       "of every task the retry carries the bare verdict EXACTLY ONCE, so a "
       "delta between arms is the blocks' and not a differently-worded or "
       "doubled error", not crowded, ",".join(crowded[:4]))

    sigs = [(r["sig"], r["id"]) for r in rows]
    classes = {r["cls"] for r in rows}
    dupes = {s: [i for (x, i) in sigs if s == x] for s in {y for y, _ in sigs}}
    ck(f"the suite is not one mistake ten times: {len(sigs)} distinct "
       f"(failure class, at-issue symbol) signatures spanning >= {MIN_CLASSES} "
       "exception classes",
       all(len(v) == 1 for v in dupes.values()) and len(classes) >= MIN_CLASSES,
       f"dupes={[v for v in dupes.values() if len(v) > 1]} classes="
       f"{sorted(classes)}")

    leaks = []
    names = published()
    for t in tasks:
        prompt = t.get("prompt", "")
        hit = [b for b in ("import ", "minishop.", ".py", "```") if b in prompt]
        words = {w for w in re.findall(r"[A-Za-z]\w+", prompt) if w in names}
        words.discard(target_name(prompt))
        if hit or words:
            leaks.append(f"{t.get('id')}: {hit} {sorted(words)}")
    ck("no prompt carries the package's own code or names one of its symbols — "
       "if the question leaked the answer, every arm would pass and the A/B "
       "would be scoring its own prompt", not leaks, " | ".join(leaks[:3]))

    if keep is not None:
        keep["rows"] = rows
        keep["enrich"] = enrich
        keep["texts"] = texts


def redundancy(rows: list[dict], enrich: list[dict]) -> str:
    """What the blocks add over the opening skeleton — printed, never gated.

    `--with-context` already puts real signatures in turn 0, so the honest
    question is not "hint versus nothing" but "hint versus skeleton", and the
    answer is a count of the lines the skeleton does NOT print.
    """
    skel = digest(str(CTX_DIR), 4000)
    shown = repeated = body = reach = 0
    for r in rows:
        for ln in r["src"].splitlines()[1:]:
            if not ln.strip() or ln.lstrip().startswith("…"):
                continue
            shown += 1
            if ln.strip() in skel:
                repeated += 1
            else:
                body += 1
        reach += len([x for x in r["grp"].splitlines()
                      if x.strip().startswith(("d1", "d2", "--"))])
    turn0 = sum(len(t["prompt"]) for t in enrich)
    return (f"turn 0 already carries {turn0} chars of skeleton across "
            f"{len(rows)} tasks; over their failures the source block shows "
            f"{shown} lines, {repeated} of which the skeleton prints too and "
            f"{body} of which it never does (bodies, not signatures), while the "
            f"dependents block adds {reach} reach/import lines that appear "
            f"nowhere in turn 0")


def tamper(tasks: list[dict]) -> list[tuple[str, list[dict], str]]:
    """Eight broken copies of the suite, each data-level, each with one gate."""
    out: list[tuple[str, list[dict], str]] = []
    t = tasks[0]
    out.append(("a task whose naive answer already passes — the loop never "
                "reaches a retry for it, so it dilutes both arms equally and "
                "looks like evidence",
                [dict(t, naive=t["solution"]), *tasks[1:]],
                "every naive answer FAILS"))
    t = tasks[1]
    out.append(("a task whose reference solution fails its own test — all four "
                "arms would score 0 and the nil would read as 'hints do not "
                "help'",
                [dict(x, solution=t["naive"]) if x is t else x for x in tasks],
                "every reference solution PASSES"))
    t = tasks[2]
    out.append(("a task whose failure names nothing the repo defines (a stdlib "
                "crash in a test that never touches the package) — both blocks "
                "are contractually silent, so it cannot score an arm",
                [dict(x, test="assert nope(1) == 2\n",
                      naive="def nope(x):\n    return 1 / 0",
                      solution="def nope(x):\n    return 2") if x is t else x
                 for x in tasks],
                "the source block is non-empty"))
    t = tasks[3]
    out.append(("a prompt that quotes the fixture's own source — the arms would "
                "pass because the question handed over the answer",
                [dict(x, prompt=x["prompt"] + "\n```python\nfrom minishop.utils"
                      " import money\n```") if x is t else x for x in tasks],
                "no prompt carries"))
    t = tasks[4]
    out.append(("one task duplicated under its own id — a suite that asks the "
                "same question twice, weighted",
                [*tasks, dict(t)],
                "not one mistake ten times"))
    t = tasks[5]
    out.append(("a task that lost its `context` — no repo to perceive, so both "
                "arms show it nothing and the delta is computed over silence",
                [dict(x, context="") if x is t else x for x in tasks],
                "the dependents block, over the same verdict"))
    t = tasks[6 % len(tasks)]
    out.append(("a `naive` frozen at the wrong layer — the model's prose-plus-"
                "fences answer instead of the code `solve` extracted from it, so "
                "the vector ranks on the extractor's complaint",
                [dict(x, naive="Here is the implementation:\n\n```python\n"
                      + x["naive"] + "\n```\n") if x is t else x for x in tasks],
                "naive` is already the CODE"))
    t = tasks[7 % len(tasks)]
    out.append(("a `naive` the token cap cut off mid-string — it fails, it even "
                "has symbols at issue, but its retry is about an unclosed "
                "program, which no hint can correct",
                [dict(x, naive=x["naive"] + '\nlabel = "abc') if x is t else x
                 for x in tasks],
                "every naive COMPILES"))
    return out


def main() -> int:
    show = "--print" in sys.argv
    tasks = [json.loads(l) for l in SUITE.read_text().splitlines() if l.strip()]
    keep: dict = {}
    gates(tasks, keep)
    failed = [n for n, ok, _ in CHECKS if not ok]
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}"
              + (f"\n       {detail}" if detail and (not ok or show) else ""))
    print(f"\nR-1.1b hint-eligible suite: {len(CHECKS) - len(failed)}/"
          f"{len(CHECKS)} checks passed")
    print(f"  ..  {redundancy(keep['rows'], keep['enrich'])}")
    dep = [r["id"] for r in keep["rows"] if r["ranked"][:1] != r["ranked_alt"][:1]]
    print(f"  ..  {len(dep)}/{len(keep['rows'])} tasks are hint-eligible only "
          f"because `diagnose`'s GOT/WANT upgrade names the at-issue symbol in "
          f"the verdict — on the bare traceback they rank to nothing "
          f"({', '.join(dep) or 'none'}); eligibility rides on that upgrade, so a "
          f"regression there shrinks this denominator silently")
    if show:
        for r in keep["rows"]:
            print(f"  ..  {r['id']:<26} {r['cls']:<20} "
                  f"at issue={','.join(r['ranked']):<34} "
                  f"src={len(r['src']):4d} graph={len(r['grp']):4d}")
    if failed:
        return 1

    defeated = 0
    cases = tamper(tasks)
    for label, tampered, must in cases:
        gates(tampered)
        bad = [n for n, ok, _ in CHECKS if not ok]
        hit = any(must in n for n in bad)
        defeated += hit
        print(f"  {'ok  ' if hit else 'MISS'} MUTATION: {label}"
              f" -> {len(bad)} check(s) fail"
              + ("" if hit else f", none of them {must!r}"))
    print(f"\nhint-ab mutants: {defeated}/{len(cases)} gates defeated by exactly "
          "their checks")
    return 0 if defeated == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
