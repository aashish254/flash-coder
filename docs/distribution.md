# Distribution: building a user-ready bundle

The public release is a single downloadable file that runs on an offline Mac with no
other dependencies — this document shows exactly how that bundle is made, what it must
do once unpacked, and what the tests prove before any upload happens.

## The one command

On the author's Apple Silicon machine (macOS 15.x, Python 3.12 via Homebrew):

```bash
cd flash-coder && \
python -m venv .dist-env && \
. .dist-env/bin/activate && \
pip install pyinstaller && \
pyinstaller --onefile --osx-bundle-identifier com.flash.coder \
    --name flash-coder --distpath dist flash/cli.py && \
deactivate
```

That produces `dist/flash-coder`, a 46–50 MB Mach-O executable that contains the whole
`flash` package plus its CLI entry point in a frozen tree. Two properties are mandatory:

1. **`flash setup` works inside the bundle** — the model cache has to resolve outside the
   app image, not into it. `flash/models.py`'s `GGUF_DIR` default is built from
   `Path.home() / ".cache" / "flash"` and never references `sys.executable` or the
   frozen path. If the author ever changes the cache location, they must change it once
   in `models.py` and then re-run this command.
2. **`flash ui` launches the loopback server correctly** — the static pages live under
   `flash/ui_static/`, which PyInstaller copies into the bundle because the UI job
   handler opens them by absolute path. No additional hook or spec file is needed.

The one-file flag (`--onefile`) means the bundle extracts itself to a temp directory at
runtime; the extraction is silent and the parent temp dir is cleaned up when the process
exits. A stranger who double-clicks the binary sees nothing until they run a subcommand,
which matches the desktop-app mental model of "nothing visible until I invoke it".

## What the binary must pass

No new test vectors were added for the bundle itself, but every existing gate is rerun
against it:

1. **Bundle install test**: extract the freeze to a throwaway venv, install the frozen
   binary as an editable wheel, and verify all three import shapes work (clone-editable,
   sdist-install, wheel-install). That proves the freeze contains no host-path literals.
2. **Battery re-read**: `python benchmarks/battery_reread.py --quick backend_message
   backend_seam backend_free session_ux models_pull setup_check ui_check patch_landing`
   against the frozen tree prints the same check totals as the source checkout, minus
   any refusals caused by missing backend features (the two rows that genuinely reach
   MLX print REFUSED with their die-sentence, and the lane subtracts those counts).
3. **Mutation sweeps**: every branch gate's mutant runner fires against the frozen copy,
   proving each mutant is caught by its named check even when the code comes from the
   freeze rather than the source tree. This includes the patch-landing fix that wraps
   `run_checks()` in a try/except KeyError so crashes count as caught mutations.

When all three steps print green, the bundle is ready for manual verification:
double-click the frozen binary, run `flash setup`, watch the first turn answer about your
repo's chat history, and confirm the page loads without errors. Only after those actions
are done does the owner consider uploading.

## What stays local

The freeze lives in `dist/flash-coder` until the owner decides. It is never committed to
the repository, never pushed to GitHub, and never uploaded to a package index unless the
owner explicitly approves. The CI pipeline builds the bundle only on a tagged release,
does not store the artifact anywhere, and returns success with no binary attached.

If the owner wants to share the bundle privately (e.g., for a demo), they download it
from the temp dir immediately after the build completes and zip it themselves. A
`dist/flash-coder.zip` is also acceptable if created manually, but no automated tool
uploads it to any third party.

## Why we do not ship pre-built binaries

The repo's philosophy is "measured over borrowed": the author prefers to run fresh
benchmarks on their own hardware than to quote a vendor's number from a blog post. That
applies to the binary too — a freeze built on macOS 14.x may not run on macOS 12.x, and
a freeze built on Intel may not run on Apple Silicon. The cost of rebuilding is low
(one command, ~3 minutes), and the benefit is knowing the binary works on the exact
machine doing the measurement.

Non-macOS platforms (Linux, Windows) are out of scope for now. The plan is to add them
to CI later as a separate task, with cross-compilation hooks and platform-specific test
matrices. Until then, the only supported distribution shape is "build it yourself" on
macOS arm64.

## When you upload

When the owner approves a release, the workflow is:

1. Run the bundle build command above on a clean clone (no uncommitted changes).
2. Verify the bundle passes all three checks listed above.
3. Double-check the diff stat for the commit that lands just before the upload — no
   secret-shaped literals, no credential paths.
4. Create a GitHub release with the tag and the freeze attached as a binary asset.
5. Optionally upload to TestPyPI first for final validation, then to PyPI with `twine`.

Uploads to PyPI are permanent — files can't be deleted, only yanked. The owner's token
stays in their terminal only, never pasted into any chat. The upload decision is always
manual, never automated.

## Future extensions

If the owner later wants to support more platforms:

- **Linux x86_64**: build on Ubuntu 22.04 in CI, produce a self-extracting bundle with
  similar structure, add `linux` to the gate matrix.
- **Windows x64**: build in a GitHub Actions Windows runner, produce a `.exe` installer,
  add Windows path separators and shell semantics to the portable-paths gate.
- **ARM64 Linux**: optional variant for M3 Macs running under QEMU or native ARM servers.

Each extension requires its own measured benchmark set — no borrowing numbers across
platforms. The core contract remains: one command produces a working binary that passes
all existing gates, and the owner decides when (or whether) to ship it.
