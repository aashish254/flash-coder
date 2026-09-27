## What changed

One paragraph. If it closes a numbered requirement, name it (`R-1.1b`, `box 321`).

## The command, and what it printed

```shell

```

Paste real output. A count with no command attached will be asked for.

## What fails when this is reverted

The mutation check. Either:

- a vector line that goes red when the change is undone (say which), or
- "no behavior changed" (docs, comment, rename), or
- "the check cannot fail today because <reason>" — which is a legitimate thing
  to say, but then the PR should also say what would make it able to fail.

## Docs that move together

- [ ] `SPEC.md` — clause updated (status, vector, the run that printed it)
- [ ] `TODO.md` — box checked/unchecked with its Result paragraph
- [ ] `README.md` — any quoted number, plus the Known-limitations section if this changed a gate
- [ ] `flash/__init__.py` — module map, if a module appeared or its job changed
- [ ] `benchmarks/battery_reread.py` — the line for a new/changed vector, and `CLAIM` re-derived from the printed counts
- [ ] `benchmarks/results/` — the witness log a claim cites, if the claim cites one

## Two things I want flagged, because this repo asks

- Does this PR make any published number harder to check? (A yes here needs
  arguing, not just a no.)
- Is any part of this *projected* or *gated* rather than measured? Say so in one
  line.
