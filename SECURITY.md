# Security Policy

## Reporting

Open a **private security advisory**: [github.com/aashish254/flash-coder/security/advisories/new](https://github.com/aashish254/flash-coder/security/advisories/new). If you can't, DM the maintainer through GitHub and I'll give you a direct address. Please don't file a public issue for a vulnerability. I triage within 72 hours and credit you in the release notes unless you ask not to be.

That URL is where this repository is *going*, not where it is: no remote is
configured and nothing is published yet, so until the first push the direct route
is the maintainer's GitHub DM.

## What this tool actually does on your machine

The whole threat model, stated as measured behavior rather than as a promise.
`python -m flash.sandbox` prints these same lines on your own machine, so you can
check them instead of trusting this table:

```
seatbelt  enforced
writes    root-only, /dev/null excepted
network   denied by default
cpu       RLIMIT_CPU (10x wall + 2s), wall clock binds
fsize     RLIMIT_FSIZE 256MB
memory    UNAVAILABLE: no memory rlimit can be set (RLIMIT_RSS: ValueError: ...)
```

- **It runs model-written code.** `flash solve`, `flash run`, `flash run-suite` and the hidden-test probe execute the candidate program in a **subprocess** that every call site routes through `sandbox.run()` — one seam, not a convention. Under macOS that subprocess is wrapped in a Seatbelt profile via `/usr/bin/sandbox-exec`: file writes outside the designated root come back as `PermissionError`, outbound network is denied (a `connect` fails with `Operation not permitted`, a hostname lookup fails as `socket.gaierror` because DNS is itself outbound), and `RLIMIT_CPU` / `RLIMIT_FSIZE` apply. There is no filesystem or network boundary *inside* the root, and no memory ceiling: `setrlimit` for `RLIMIT_AS`/`RLIMIT_DATA`/`RLIMIT_RSS` raises on macOS at any finite value, so the module says `UNAVAILABLE` rather than claiming a bound it can't set.
- **On Linux and Windows the kernel mechanism is different, and the tool knows it.** `sandbox-exec` is macOS-only, so off Apple Silicon `status()` reports `writes: unconfined` and `network: unconfined` and only the POSIX rlimits bind. Treat "point it at a repository you would run `pytest` in" as the rule on every platform until a Linux profile exists.
- **It reads your repository.** PERCEIVE (`flash context`, `flash perceive`, `flash find`, `flash refs`, `flash graph`, `flash symbols`, `flash lsp-selftest`) parses source files locally to build a symbol skeleton and an AST call graph. Nothing is uploaded.
- **It can fetch web pages.** `flash web` retrieves URLs you name, caches the text under `benchmarks/cache/`, and ranks excerpts with a local embedder. It does not crawl, and it sends no information about your repo.
- **It can write drafts.** `flash ambient` is draft-only by design (R-7.2): it lints and annotates and refuses to commit or push.
- **Model weights are downloaded from Hugging Face** on first use by `mlx-lm`, into the standard HF cache — not by this code. Expect roughly 4 GB for the 7B fast tier and considerably more for the 30B brain. Each model carries its own upstream license; see [docs/models.md](docs/models.md).
- **There is no telemetry.** Nothing in `flash/` opens a socket except the explicit `flash web` path you invoke.

## Traces are the sensitive artifact

`--trace-full` stores the **exact prompt text** of every generation so a run can be replayed (invariant I-6). In practice that means your project's real source, formatted into a prompt, is written as plaintext to `benchmarks/results/traces/<session>.jsonl`.

- Traces are local and never uploaded. Add `benchmarks/results/traces/` to your own `.gitignore` before committing a project you ran with `--trace-full`.
- `flash trace <session>` renders decisions; `--full` includes the stored prompts, `--task <id>` narrows to one task. Read a trace the way you'd read a log file that contains source code.
- The traces committed to *this* repository are generated from the fixtures under `benchmarks/fixtures/` and from Flash Coder's own source, not from anyone's private code.

## Dependencies

`mlx-lm`, `mlx-vlm` and `python-lsp-server` do the heavy lifting, all locally. Dependabot is enabled for pip requirements and GitHub Actions. The offline battery is what catches a dependency upgrade that silently changes a measured claim, so CI runs it on every pull request.

## Supported versions

| Version | Supported |
| --- | --- |
| latest tagged release | yes |
| `main` | yes, best effort — run `flash selftest --all` and quote the printed counts |
| older tags | no |
