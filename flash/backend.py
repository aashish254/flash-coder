"""The generation seam: the one module that knows which model library answers.

Before this file, every generation path said `import mlx.core` / `from mlx_lm
import load` at the top of its own function. That is correct for a project whose
only backend is MLX, and it produced one defect a user actually walks into: on a
machine with no MLX — which is every Linux and Windows box, since MLX is
Apple-Silicon-only — `flash session` died with a raw `ModuleNotFoundError`
traceback out of `flash/loop.py`, while README.md and SPEC R-7.7 both promise a
human sentence naming MLX instead of a stack trace.

So this module owns two things:

1. **The refusal.** `mlx()`, `mlx_lm()` and `load()` raise `NoBackend` — a
   RuntimeError whose text is the whole answer: what is missing, the exact
   `flash doctor` line that says so, and the remedy for THIS platform. The CLI
   catches it once, in `main()`, so a command cannot be added that forgets to
   handle it, and a library caller still gets a named exception rather than a
   traceback from somebody else's import.
2. **The backend.** `Backend` is the interface the loop talks to — load,
   generate, unload, profile. `MLXBackend` wraps the Metal path;
   `LlamaCppBackend` wraps a local GGUF through `llama-cpp-python`, which is what
   makes generation possible on the platforms MLX cannot reach. Which one answers
   is resolved here from the installed packages and the `--backend` flag; the
   oracle loop, the tournament, the ledger, the power governor and the confidence
   table never learn.

Nothing at module scope imports a third-party model library. That is a hard rule,
not a style choice: `benchmarks/backend_free_check.py` sweeps every submodule of
`flash` with the backend blocked and requires the numerator to equal the
denominator, so one top-level `import mlx.core` here would turn a passing gate
into a traceback.

What gates this file: `benchmarks/backend_message_check.py` runs every generating
CLI command under the shipped `--backend-free` shim and requires the sentence,
the exit code and the remedy; `benchmarks/backend_free_check.py` keeps the import
half.
"""
from __future__ import annotations

import sys


class NoBackend(RuntimeError):
    """No model library can answer here. The text is the user-facing answer."""


def reason(exc: BaseException, name: str) -> str:
    """The one-line diagnosis of a failed import, shaped like doctor's.

    A `ModuleNotFoundError` says `No module named 'mlx_lm'`, which is true and
    useless; the sentence below names the package the remedy installs.
    """
    return f"import {name}: {type(exc).__name__}: {exc}"


def remedy() -> str:
    """What to type, for the platform actually running.

    Two answers because there are two different problems. On Apple Silicon MLX is
    installable and simply absent, so the remedy is an install line. Anywhere else
    no MLX wheel exists at all, and saying `pip install` something would be a
    promise this project cannot keep.
    """
    if sys.platform == "darwin":
        return ("pip install 'flash-coder[mac]'          "
                "(in a checkout: pip install -e '.[mac]')")
    return ("MLX does not build outside Apple Silicon, so no install line fixes "
            "this box. The offline half — the whole verification battery, the "
            "graph, the oracle loop's grading — runs anyway")


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


def mlx():
    """The `mlx.core` module, or `NoBackend`."""
    try:
        import mlx.core as mx                               # noqa: F401
    except Exception as e:                                  # noqa: BLE001
        raise NoBackend(message(reason(e, "mlx.core"))) from None
    return mx


def mlx_lm():
    """The `mlx_lm` module, or `NoBackend`."""
    try:
        import mlx_lm                                       # noqa: F401
    except Exception as e:                                  # noqa: BLE001
        raise NoBackend(message(reason(e, "mlx_lm"))) from None
    return mlx_lm


def load(repo: str, adapter: str | None = None):
    """`(model, tokenizer)` for a repo this backend can serve."""
    return mlx_lm().load(repo) if not adapter else \
        mlx_lm().load(repo, adapter_path=adapter)
