"""R-1.3b, OFFLINE: does the graph's subgraph reach the MODEL's retry prompt?

§28.2 step 3 says "inject only that subgraph into context (small context = fast =
= cool)". Two halves are under test here:

* `graph.scope_hint` — which symbols it picks, whose callers it reports, what it
  refuses to guess, what it costs in characters and milliseconds.
* `loop.solve`'s retry prompt — because R-1.1 is the lesson of this seam. A hint
  computed for the RECORD and never appended to `err` passes every unit check and
  tells the model nothing, and did, for a month. So the checks that matter here
  drive `solve` with a stubbed generator and read the message back.

No model loads. The repo graphs are built over tempdirs and the real fixtures
tree, both deterministic AST passes.

    python benchmarks/graph_perceive_check.py            # the checks
    python benchmarks/graph_perceive_check.py --mutants  # put each bug back
    python benchmarks/graph_perceive_check.py --sweep    # one fresh process each
    python benchmarks/graph_perceive_check.py --mutant 6 # one bug, for a bisection

`--sweep` is the form the mutation claim is quoted from. Several of these bugs
live in the graph CACHE, so how many checks a mutant fails depends on whether the
process has already built a graph: run all nine together the counts are
2/2/7/2/14/2/7/2/2, run each alone they are 2/1/6/1/13/2/6/1/1. All nine are
caught either way, by the same named check, and it is that identity — not the
count — which is claimed.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import flash.loop as loop                               # noqa: E402
from flash import graph                                 # noqa: E402
from flash.graph import (_SHOP, SCOPE_CACHE, SCOPE_HEADER, SCOPE_HITS,  # noqa: E402
                         _write_repo)
from flash import lsp                                   # noqa: E402
from flash.lsp import Symbol                            # noqa: E402

FIXTURES = ROOT / "benchmarks" / "fixtures" / "minishop"
CTX_ROOT = FIXTURES.parent                # so context="minishop" resolves

# A four-deep call chain, so "which hops does the block carry" is answerable from
# the text alone: d <- c <- b <- a, read as "a is reached by b at depth 1".
_CHAIN = {
    "chain.py": (
        '"""A chain, for a depth question."""\n\n\n'
        'def d(x):\n    return x + 1\n\n\n'
        'def c(x):\n    return d(x)\n\n\n'
        'def b(x):\n    return c(x)\n\n\n'
        'def a(x):\n    return b(x)\n'),
}
# One hot symbol, many honest callers: the budget's test subject.
_HUB = {"hub.py": '"""The hub."""\n\n\ndef shared(x):\n    return x + 1\n'}
_SPOKES = {f"s{i:02d}.py": f'"""Spoke {i}."""\nfrom hub import shared\n\n\n'
                           f"def go{i}(x):\n    return shared(x)\n"
           for i in range(30)}

SUB_AT_ISSUE = ("Traceback (most recent call last):\n"
                '  File "tests/test_cart.py", line 12, in test_total\n'
                "    assert cart.subtotal_cents() == 900\n"
                "AssertionError: 950 != 900")
SUB_CODE = "def fix(cart):\n    return cart.subtotal_cents() - 50\n"

CHECKS: list[tuple[str, bool, str]] = []


def ck(name: str, cond, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))


def _call_site_honest(hint: str) -> bool:
    """Does every quoted line in the block match the file:line it cites?"""
    for m in re.finditer(r"it: (.+)  \[([^\]]+?):(\d+) via ", hint):
        text, f, ln = m.group(1), m.group(2), int(m.group(3))
        src = (FIXTURES / f).read_text(encoding="utf-8").split("\n")
        if ln > len(src) or src[ln - 1].strip() != text.strip():
            return False
    return True


# ------------------------------------------------------------------ the units

def _unit_checks() -> dict[str, float]:
    timing: dict[str, float] = {}

    # --- what it says
    import time
    t = time.perf_counter()
    hint = graph.scope_hint(FIXTURES, SUB_AT_ISSUE, SUB_CODE)
    first_ms = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    again = graph.scope_hint(FIXTURES, SUB_AT_ISSUE, SUB_CODE)
    second_ms = (time.perf_counter() - t) * 1000
    timing["first_ms"], timing["second_ms"] = first_ms, second_ms
    ck("R-1.3b's clause shape: a failure that turns on `Cart.subtotal_cents` "
       "gets the symbol that would BREAK if it changed — `Cart.total_cents`, with "
       "the line that calls it",
       hint.startswith(SCOPE_HEADER)
       and "cart.py::Cart.total_cents" in hint
       and "self.subtotal_cents()" in hint, repr(hint))
    ck("...and the provenance rides along AND IS TRUE: the `via` rule is printed, "
       "and the file:line plus the quoted call-site text match the repo's own "
       "source read off disk — an injected hint that misquotes the file it cites "
       "is worse than none, because the model has no other way to check it",
       bool(re.search(r"\[cart\.py:\d+ via enclosing-class\]", hint))
       and _call_site_honest(hint), hint)
    ck("...and the merge pass is not a second opinion: the same failure against "
       "the cached-and-merged graph yields a byte-identical block, so `merge()` "
       "cannot quietly rebuild a different graph on every retry",
       again == hint, f"{len(hint)} vs {len(again)} chars")
    g = graph.scope_graph(FIXTURES)
    ids = hint.split("cart.py::Cart.subtotal_cents", 1)[-1]
    own = [h.node.id for h in g.blast("Cart.subtotal_cents",
                                       graph.SCOPE_DEPTH).hits]
    ck("what the block injects IS what `flash graph` answers for the same symbol "
       "— the same `blast()` call, not a prose summary of it",
       own and all(n in ids for n in own[:SCOPE_HITS]),
       f"blast={own} block={ids[:200]}")
    ck("the block is only as long as it says it is: under `max_chars`, always",
       len(hint) <= graph.SCOPE_MAX_CHARS, f"{len(hint)} chars")

    # --- what it refuses to say
    ck("nothing repo-defined is at issue — a stdlib failure gains no block, "
       "rather than a header over an empty body",
       graph.scope_hint(FIXTURES, "NameError: name 'json' is not defined",
                  "import json\nprint(json.dumps({}))\n") == "", "")
    empty = Path(tempfile.mkdtemp(prefix="flash-perceive-empty-"))
    ck("an empty repo gains no block either — the graph is real and says nobody is "
       "at issue",
       graph.scope_hint(empty, SUB_AT_ISSUE, SUB_CODE) == "", "")

    dup = _write_repo({
        "pkg/a.py": '"""A."""\ndef helper(x):\n    return x + 1\n\n\n'
                    'def use_a(x):\n    return helper(x)\n',
        "pkg/b.py": '"""B."""\ndef helper(x):\n    return x * 2\n\n\n'
                    'def use_b(x):\n    return helper(x)\n'}, "dup")
    dup_hint = graph.scope_hint(dup, "helper() raised TypeError",
                                "y = helper(3)\n")
    ck("two same-named symbols in two files never BLEND into one caller list: the "
       "block carries one file's dependents and says which file it resolved to — "
       "the `Cart.total_cents` vs `CartLine.total_cents` confusion App. A bills as "
       "the costliest hallucination in the suite",
       ("pkg/a.py::use_a" in dup_hint or "pkg/b.py::use_b" in dup_hint)
       and not ("pkg/a.py::use_a" in dup_hint and "pkg/b.py::use_b" in dup_hint)
       and ("[pkg/a.py:" in dup_hint or "[pkg/b.py:" in dup_hint), repr(dup_hint))
    foreign = Symbol(name="helper", kind="function",
                     path=Path("/not-in-the-graph/helper.py"), line=1, end_line=2)
    ck("...and the tie-break is the FILE, not the name: a symbol the AST index "
       "read from a file the graph never scanned names no node, so the block "
       "drops it instead of borrowing another file's callers",
       graph._one_node(graph.scope_graph(dup), dup, foreign) is None, "")

    chain = _write_repo(_CHAIN, "chain")
    # `d` is the leaf: c reaches it at 1, b at 2, a at 3.
    depth2 = graph.scope_hint(chain, "d() divided by zero", "q = d(2)\n")
    depth3 = graph.scope_hint(chain, "d() divided by zero", "q = d(2)\n", depth=3)
    ck("§28.2's 'small context' is a number, not a mood: at depth 2 the block "
       "carries c and b but NOT the third hop a, which a depth-3 ask does get — "
       "and the depth is printed per line, so a truncated list cannot read as a "
       "complete one",
       "chain.py::c" in depth2 and "chain.py::b" in depth2
       and "chain.py::a" not in depth2 and "chain.py::a" in depth3
       and depth2.count("d1 ") == 1 and depth2.count("d2 ") == 1,
       f"d2={depth2!r}\nd3={depth3!r}")

    # --- the budget
    hub = _write_repo({**_HUB, **_SPOKES}, "hub")
    wide = graph.scope_hint(hub, "shared() blew up", "v = shared(1)\n",
                      max_chars=440)
    ck("a hub with 30 callers and a 440-char budget says so: it trims the list AND "
       "prints how many it left out, because a silently short answer reads as "
       "'those are all the callers'",
       len(wide) <= 460 and "not shown (context budget)" in wide
       and 0 < wide.count("      d1 ") < SCOPE_HITS
       and "      d1 s00.py" in wide, repr(wide))
    tight = graph.scope_hint(hub, "shared() blew up", "v = shared(1)\n",
                       max_chars=len(SCOPE_HEADER) + 12)
    ck("a budget too small for the first symbol's own header yields nothing at "
       "all, rather than a header with no answer under it",
       tight == "", repr(tight))

    # --- the cache, which is the injection's cost AND its risk
    live = _write_repo(_SHOP, "fresh")
    base = graph.scope_hint(live, "gross() is wrong", "g = gross(120)\n")
    ck("first contact builds the graph, and the block heads the symbol with its "
       "own file and line before listing who reaches it",
       "gross [function] shop/pricing.py:9" in base
       and "shop/cart.py::Cart.total" in base, repr(base))
    (live / "shop/fmt.py").write_text(
        '"""Money."""\nfrom pricing import gross\n\n\n'
        'def coins(cents):\n    return gross(cents)\n', encoding="utf-8")
    after = graph.scope_hint(live, "gross() is wrong", "g = gross(120)\n")
    ck("FRESHNESS, which is what makes a cached graph dangerous: add a caller and "
       "the next block names it — the reuse path runs `merge()`, not a re-serve "
       "of a stale index",
       "shop/fmt.py::coins" in after and "shop/fmt.py::coins" not in base,
       f"before={base!r}\nafter={after!r}")
    (live / "shop/fmt.py").write_text(
        '"""Money."""\n\n\ndef coins(cents):\n    return f"${cents}"\n',
        encoding="utf-8")
    ck("...and it un-names one too: delete the import and the same block loses "
       "that caller, so a merge that only ADDS cannot pass this",
       "shop/fmt.py::coins" not in graph.scope_hint(
           live, "gross() is wrong", "g = gross(120)\n"), "")
    ck(f"the per-process cache is bounded ({SCOPE_CACHE} repos): a 9th root "
       f"evicts the least-recently-used graph instead of holding one per task "
       f"forever in a long-running run",
       _eviction_check() <= SCOPE_CACHE, f"{len(graph._scope_cache)} roots held")
    timing.update(_shared_index_cost())
    return timing


def _shared_index_cost() -> dict[str, float]:
    """The parse both hints read, measured on a real tree and printed, not gated.

    Gating a millisecond ratio on this box would be gating the load average: the
    deterministic claim is the build COUNT in `_wiring_checks`, and this is the
    size of what that count saves.
    """
    import time
    pkg = ROOT / "flash"
    graph.scope_graph(pkg)                  # warm the graph: only the index varies
    idx = lsp.SymbolIndex.build(pkg)
    # `python_files` and not `symbol_source`, which is the name this probe first
    # tried: symbol_source's own source is 1631 chars, `symbol_hint`'s budget check
    # was a `break`, and so the whole block vanished — R-1.1's hint silently absent
    # for a failure about a long symbol. That is shipped as TODO **R-1.1c** (the
    # block now clips at a line boundary and counts what it left out; `flash
    # lsp-selftest` section 6c), so the pair below only has to be non-empty to
    # compare — and `python_files` (402 chars, no tail note) keeps the timing
    # baseline free of the overflow path it no longer has to avoid.
    err, code = "python_files() raised", "fs = python_files(r)\n"
    t = time.perf_counter()
    src1 = lsp.symbol_hint(pkg, err, code, index=idx)
    dep1 = graph.scope_hint(pkg, err, code, index=idx)
    shared = (time.perf_counter() - t) * 1000
    t = time.perf_counter()
    src2 = lsp.symbol_hint(pkg, err, code)
    dep2 = graph.scope_hint(pkg, err, code)
    twice = (time.perf_counter() - t) * 1000
    ck("sharing the index changes no answer: the hint pair built on a passed-in "
       "ranking is byte-identical to the pair that builds its own",
       (src1, dep1) == (src2, dep2) and bool(src1) and bool(dep1),
       f"{len(src1)}+{len(dep1)} chars")
    return {"shared_ms": shared, "twice_ms": twice}


def _eviction_check() -> int:
    """Roots held after nine more repos are visited."""
    for _ in range(SCOPE_CACHE + 1):
        graph.scope_graph(_write_repo(_CHAIN, "evict"))
    return len(graph._scope_cache)


# ---------------------------------------------------------------- the wiring

def _retry_prompt(task: dict, err: str, code: str) -> tuple[str, list]:
    """Drive `loop.solve` for real and hand back what the model was SHOWN.

    `_generate` is the generation seam, so the message list the stub receives is
    the same object `messages` carried into the next turn — not the `Attempt`
    record `flash trace` prints. That difference is the whole reason this
    function exists: it is where R-1.1's bug hid.
    """
    seen: list[list[dict]] = []

    def fake_generate(model, tokenizer, messages, max_tokens, **kw):
        seen.append([dict(m) for m in messages])
        body = code if len(seen) == 1 else "answer = 0"
        return f"```python\n{body}\n```\n"

    real = (loop._generate, loop.diagnose)
    loop._generate = fake_generate
    loop.diagnose = lambda c, t: (False, err)
    try:
        loop.solve(None, None, task, max_attempts=2, max_tokens=64,
                   debug=False)
    finally:
        loop._generate, loop.diagnose = real
    retry = seen[1][-1]["content"] if len(seen) > 1 else ""
    return retry, seen


def _wiring_checks() -> None:
    task = loop.enrich_task({"id": "gp1",
                             "prompt": "make subtotal_cents honour the qty",
                             "test": "assert False", "context": "minishop"},
                            CTX_ROOT)
    retry, seen = _retry_prompt(task, SUB_AT_ISSUE, SUB_CODE)
    ck("R-1.3b's clause, at the seam that names it: the retry message "
       "`loop.solve` sends carries the dependents block. A hint computed for the "
       "record and not appended to `err` passes every unit check and tells the "
       "model nothing — that is the bug corrected in R-1.1, one line from here",
       len(seen) == 2 and SCOPE_HEADER in retry, repr(retry)[-400:])
    ck("...and it arrives verbatim and exactly once, not once per retry path",
       retry.count(SCOPE_HEADER) == 1
       and retry.count("cart.py::Cart.total_cents") >= 1, "")
    ck("the block `solve` injects IS the block `scope_hint` returns for the same "
       "failure — the loop adds nothing to it and truncates none of it",
       bool(hint := graph.scope_hint(task["_ctx_dir"], SUB_AT_ISSUE, SUB_CODE))
       and retry.count(hint) == 1, f"{len(hint)} chars of hint")
    ck("both perception blocks coexist: the LSP's source hint ('Symbols in play') "
       "and the graph's dependents hint, each once — one answers 'what is this', "
       "the other 'what depends on it'",
       "Symbols in play" in retry and SCOPE_HEADER in retry, "")
    idx = lsp.SymbolIndex.build(FIXTURES)
    quoted = set(re.findall(r"^# \S+:\d+  (\S+) \[",
                            lsp.symbol_hint(FIXTURES, SUB_AT_ISSUE, SUB_CODE,
                                            index=idx), re.M))
    heads = set(re.findall(r"^  (\S+) \[", retry.partition(SCOPE_HEADER)[2],
                            re.M))
    ck("one ranking, two readers: every symbol the dependents block heads is one "
       "the source block quoted in the same retry — the loop cannot be shown two "
       "different notions of 'what is at issue' about one failure",
       bool(heads) and heads <= quoted, f"heads={sorted(heads)} quoted={sorted(quoted)}")
    builds: list[str] = []
    real_build = lsp.SymbolIndex.build

    def counted(cls, root, max_files=400):
        builds.append(Path(root).name)
        # __func__ because `real_build` is already bound to the class: passing
        # cls to it again is a TypeError, and a counter that raises measures
        # nothing while looking like it measured six somethings.
        return real_build.__func__(cls, root, max_files)

    lsp.SymbolIndex.build = classmethod(counted)
    try:
        _retry_prompt(task, SUB_AT_ISSUE, SUB_CODE)
    finally:
        lsp.SymbolIndex.build = real_build
    ck(f"one parse per retry, two readers: two failing attempts built the symbol "
       f"index {len(builds)} time(s) — the graph hint rides the source hint's "
       f"parse instead of repeating it",
       len(builds) == 2, str(builds))
    ck("a first attempt that passes pays nothing extra: the opening prompt — the "
       "task and the repo skeleton — carries neither hint",
       seen and "Symbols in play" not in seen[0][-1]["content"]
       and SCOPE_HEADER not in seen[0][-1]["content"], "")
    bare = {"id": "gp2", "prompt": "write a fizzbuzz", "test": "assert False"}
    retry2, seen2 = _retry_prompt(bare, SUB_AT_ISSUE, SUB_CODE)
    ck("control: a task with no repo context gains no block — the injection is "
       "silent by the same contract `_symbol_hint` is silent, and the bare error "
       "still reaches the model exactly once",
       len(seen2) == 2 and SCOPE_HEADER not in retry2
       and retry2.count(SUB_AT_ISSUE) == 1, repr(retry2)[-300:])
    def dead(root, *a, **kw):
        raise RuntimeError("no graph on this box")

    # save/restore rather than capture-and-reinstall: a stub installed here that
    # leaks turns the NEXT section's prompt into one with no hint at all, and a
    # leaked stub can only be caught by looking.
    saved = graph.scope_hint
    graph.scope_hint = dead
    try:
        retry3, seen3 = _retry_prompt(task, SUB_AT_ISSUE, SUB_CODE)
    finally:
        graph.scope_hint = saved
    ck("and the seam is clean on the way out: with the graph restored, a later "
       "retry still carries the block",
       SCOPE_HEADER in _retry_prompt(task, SUB_AT_ISSUE, SUB_CODE)[0], "")
    ck("a graph that raises costs the run nothing but the block: the retry still "
       "goes out, with the bare error and the source hint intact "
       "(§33.9 invariant 7)",
       len(seen3) == 2 and SCOPE_HEADER not in retry3
       and "Symbols in play" in retry3 and SUB_AT_ISSUE in retry3,
       repr(retry3)[-300:])


def run_checks() -> dict[str, float]:
    CHECKS.clear()
    timing = _unit_checks()
    _wiring_checks()
    return timing


# ------------------------------------------------------------- mutation cover

NUM_BUGS = 9        # what --sweep spawns; mutate() fails if `bugs` disagrees


def mutate(verbose: bool = True, one: int | None = None) -> int:
    """Put each plausible bug back, and require that ITS OWN check fails.

    The set is chosen for the two ways this feature can be wrong in ways that
    look right: it can lie about scope (a guessed symbol, a stale graph), or it
    can be right and never arrive. The last mutant is R-1.1's own shape — the
    hint computed and dropped instead of appended — put back at runtime rather
    than by editing `loop.py`, because the observable signature is identical and
    the repo's working tree stays honest.
    """
    import flash.loop as _loop
    g = graph
    real_blast = g.Graph.blast
    real_scope = g.scope_hint        # what the dropped-hint mutant computes

    def stale(root):
        """The cache is a cache: hand back what it holds, never re-merge."""
        key = str(Path(root).resolve())
        got = g._scope_cache.get(key)
        if got is None:
            got = g.build(key)
            g._scope_cache[key] = got
        return got

    def guess(gh, root, sym):
        nodes = gh.get(sym.name)
        return nodes[0] if nodes else None

    def uncapped(root, err="", code="", limit=g.SCOPE_LIMIT, **kw):
        nodes = graph.scope_targets(root, err, code, limit=limit)
        if not nodes:
            return ""
        lines = [SCOPE_HEADER]
        for n in nodes:
            rad = real_blast(graph.scope_graph(root), n.id, 99)
            lines.append(f"  {n.symbol} ({n.file}:{n.start})")
            lines += [f"      d{h.depth} {h.node.id} {h.edge.kind} it: "
                      f"{h.edge.text}  [{h.edge.file}:{h.edge.line} "
                      f"via {h.edge.via}]" for h in rad.hits]
        return "\n".join(lines)

    def flat_depth(self, needle, depth=g.DEFAULT_DEPTH):
        return real_blast(self, needle, 1)     # depth accepted, ignored

    def always_header(root, err="", code="", **kw):
        return SCOPE_HEADER        # a header over nothing, dressed as an answer

    def no_evict(root):
        key = str(Path(root).resolve())
        g._scope_cache[key] = g._scope_cache.get(key) or g.build(key)
        return g._scope_cache[key]

    def record_only(task, err, code="", index=None):
        """R-1.1's bug shape: compute the block, return nothing for the prompt."""
        ctx = task.get("_ctx_dir")
        if ctx and err:
            try:
                real_scope(ctx, err, code, index=index)
            except Exception:                       # the record never sees it either
                pass
        return ""

    def no_share(task):
        """The sharing dropped: each hint parses the repo for itself."""
        return None

    def nested(task, err, code=""):
        """The assembly this shipped with first: the second hint ranked over the
        first hint's own quoted source, so one retry showed two answers to
        'what is at issue'."""
        ctx = task.get("_ctx_dir")
        if not (ctx and err):
            return err
        i = _loop._repo_index(task)
        src = _loop._symbol_hint(task, err, code, i)
        ranked = f"{err}\n\n{src}" if src else err
        try:                                        # the seam guards; so must this
            dep = g.scope_hint(ctx, ranked, code, index=i)
        except Exception:
            dep = ""
        return "\n\n".join(p for p in (ranked, dep) if p)

    bugs: list[tuple[str, object, object, str]] = [        ("serves the cached graph without merging, so a caller added after the "
         "first retry is invisible", (g, "scope_graph"), stale, "FRESHNESS"),
        ("binds an at-issue symbol by NAME alone, so it can borrow another file's "
         "callers", (g, "_one_node"), guess, "the tie-break is the FILE"),
        ("ignores the character budget", (g, "scope_hint"), uncapped,
         "too small for the first symbol's own header"),
        ("accepts `depth` and injects one hop", (g.Graph, "blast"), flat_depth,
         "'small context' is a number"),
        ("prints its header even when it has no answer", (g, "scope_hint"),
         always_header, "gains no block"),
        ("keeps one graph per repo root forever", (g, "scope_graph"), no_evict,
         "bounded"),
        ("computes the block for the record and never appends it — R-1.1's bug, "
         "put back", (_loop, "_graph_hint"), record_only, "at the seam"),
        ("parses the repo once per hint instead of once per retry",
         (_loop, "_repo_index"), no_share, "one parse per retry"),
        ("feeds the second hint the text the first one quoted, so the two rank "
         "different symbols for one failure", (_loop, "_perceive"), nested,
         "one ranking, two readers"),
    ]
    escaped = 0
    if len(bugs) != NUM_BUGS:
        raise SystemExit(f"NUM_BUGS is stale: {len(bugs)} bugs listed, "
                         f"{NUM_BUGS} promised to --sweep")
    for i, (label, (holder, attr), bug, catcher) in enumerate(bugs):
        if one is not None and i != one:
            continue
        real = getattr(holder, attr)
        try:
            setattr(holder, attr, bug)
            run_checks()
            fails = [nm for nm, ok, _ in CHECKS if not ok]
        finally:
            setattr(holder, attr, real)
        hit = any(catcher in f for f in fails)
        escaped += not hit
        if verbose or not hit:
            print(f"  {'ok  ' if hit else 'MISS'} MUTATION: {label} -> "
                  f"{len(fails)} check(s) fail"
                  + ("" if hit else f", none of them the one that catches it: "
                                    f"{fails[:2]}"))
    print(f"perceive mutants: {len(bugs) - escaped}/{len(bugs)} caught"
          + ("" if one is None else f"  [{one}]"))
    return escaped


def sweep() -> int:
    """One FRESH process per mutant, and the two lists must agree.

    `--mutants` runs all nine in the interpreter that wrote them, where mutant 0's
    cache warm-up is mutant 1's starting state — but a real run's retries do not
    arrive in a bug's wake. Eviction, the graph cache and the merge path are each
    order-sensitive, so a count seen only in one shared process could be the
    previous mutant's leftover. This spawns one process per bug, re-runs them all
    in-process, and fails unless both catch 9/9.
    """
    caught = 0
    for i in range(NUM_BUGS):
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                               "--mutant", str(i)],
                              capture_output=True, text=True, cwd=str(ROOT))
        lines = [ln for ln in proc.stdout.splitlines() if "MUTATION:" in ln]
        if len(lines) != 1 or proc.returncode not in (0, 1):
            print(f"  MISS MUTATION [{i}]: no clean verdict "
                  f"(rc={proc.returncode})\n{proc.stdout[-400:]}{proc.stderr[-400:]}")
            continue
        print(lines[0].rstrip())
        caught += proc.returncode == 0
    print(f"perceive mutants, fresh process each: {caught}/{NUM_BUGS} caught")
    here = mutate(verbose=False)
    print(f"perceive mutants, one process:        {NUM_BUGS - here}/{NUM_BUGS} "
          "caught")
    ok = caught == NUM_BUGS and here == 0
    print(("OK  each mutant is caught by its named check in a fresh process too, "
           "so no count here is a leftover from the previous bug"
           if ok else
           "FAIL  the two sweeps disagree or one escaped — the mutation claim is "
           "process-order-dependent"))
    return 0 if ok else 1


def _report_checks() -> int:
    """Run the 27 and print them. Returns the number that failed."""
    timing = run_checks()
    bad = 0
    for name, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}"
              + (f"\n       {detail}" if detail and not ok else ""))
        bad += not ok
    print(f"  ..  cost of the injection on the fixtures repo: "
          f"{timing['first_ms']:.1f} ms cold (build + query), "
          f"{timing['second_ms']:.2f} ms once cached (merge + query)")
    print(f"  ..  one parse per retry, on flash/ (26 files): both hints together "
          f"{timing['shared_ms']:.0f} ms on a shared index vs "
          f"{timing['twice_ms']:.0f} ms building it twice")
    print(f"\nR-1.3b graph-into-context: {len(CHECKS) - bad}/{len(CHECKS)} "
          f"checks passed")
    return bad


def main() -> int:
    if "--mutant" in sys.argv and "--sweep" not in sys.argv:
        return 1 if mutate(verbose=True,
                           one=int(sys.argv[sys.argv.index("--mutant") + 1])) else 0
    bad = _report_checks()
    if "--sweep" in sys.argv:
        # SPEC §6's line for this vector: the checks and both mutation sweeps, in
        # the order-independent form, from one command.
        return 1 if (bad or sweep()) else 0
    if "--mutants" in sys.argv:
        return 1 if (bad or mutate(verbose=False)) else 0
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
