"""M0b: vision pathway — VLM load, image+prompt generation, screenshot->UI loop.

Design mirrors loop.py's discipline: attempt 1 greedy, retries temp=0.7 with
per-attempt seed (unseeded temp>0 retries are byte-identical — M2 live find),
feedback = real error text. Two oracle styles: `test` = python asserts over
the `html` string; `pixel` = render the HTML headlessly and diff against the
reference screenshot (M5's closed loop, fraction of strongly-different pixels).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VisionResult:
    solved: bool
    html: str
    attempts: list[tuple[str, str]] = field(default_factory=list)  # (html, err)
    seconds: float = 0.0

    @property
    def n_attempts(self) -> int:
        return len(self.attempts)


def load_vlm(repo: str):
    from mlx_vlm import load
    return load(repo)


def generate_with_image(model, processor, image_paths, prompt: str,
                        max_tokens: int = 1024, temp: float = 0.0,
                        seed: int | None = None) -> str:
    from mlx_vlm import generate
    from mlx_vlm.prompt_utils import apply_chat_template
    if isinstance(image_paths, str):
        image_paths = [image_paths]
    formatted = apply_chat_template(processor, model.config, prompt,
                                    num_images=len(image_paths))
    if temp > 0 and seed is not None:
        import mlx.core as mx
        mx.random.seed(seed)   # per-attempt stream, same lesson as loop.py
    out = generate(model, processor, formatted, image=image_paths,
                   max_tokens=max_tokens, temperature=temp, verbose=False)
    return out.text if hasattr(out, "text") else str(out)


def extract_html(text: str) -> str:
    """First fenced block (```html ... ``` or bare ```); else the raw output."""
    import re
    m = re.search(r"```(?:html)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def check_html(html: str, test: str) -> tuple[bool, str]:
    """Run task test with `html` bound; (ok, err) — same oracle shape as harness."""
    try:
        exec(compile(test, "<vis-test>", "exec"), {"html": html})
        return True, ""
    except AssertionError as e:
        return False, f"FAILING_ASSERT: {e}"
    except Exception as e:
        return False, f"ERROR: {type(e).__name__} {e}"


_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

#: Egress off for the renderer. R-9.2's writable-root half CANNOT be applied to
#: Chrome on this box: `--user-data-dir` inside the root makes headless Chrome
#: abort with rc=21 ("Failed to create a ProcessSingleton") before it renders, so
#: there is no screenshot to confine and a Seatbelt write-deny outside a root we
#: cannot give it fails the same way. What IS enforced here is the network half,
#: by Chrome's own switch board — measured three ways: the PNG bytes are
#: IDENTICAL with and without the flags (3959 both ways, pixel_diff 0.0), so the
#: oracle does not move, and a page that fetches costs +10 s of wall time
#: (13.8 s against 3.0 s), which is why the budget below went from 30 s to 60 s.
#: Killing DNS alone was worse: 25-29 s, inside a hair of the old timeout.
RENDER_TIMEOUT_S = 60      # was 30; the egress flags cost a page that fetches

_NO_EGRESS = ("--disable-background-networking",
              "--host-resolver-rules=MAP * ~NOTFOUND",
              "--proxy-server=http://127.0.0.1:9")


def render_html_png(html: str, out_png, size: tuple[int, int] = (480, 320)) -> bool:
    """Headless-Chrome screenshot of the HTML at the reference viewport.

    The one execution path R-9.2 does NOT sandbox — see `_NO_EGRESS` for the
    measurement that says why. Model HTML still runs its JavaScript here, which
    is what a pixel oracle needs, and the flags take away its reach.
    """
    import subprocess
    import tempfile
    from pathlib import Path
    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as f:
        f.write(html)
        src = f.name
    try:
        subprocess.run(
            [_CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
             f"--window-size={size[0]},{size[1]}",
             f"--screenshot={out_png}", f"file://{src}", *_NO_EGRESS],
            capture_output=True, timeout=RENDER_TIMEOUT_S, check=True)
        return Path(out_png).exists()
    except Exception:
        return False
    finally:
        Path(src).unlink(missing_ok=True)


def pixel_diff(png_a: str, png_b: str, thresh: int = 40) -> float:
    """Fraction of pixels whose max channel difference exceeds `thresh`."""
    import numpy as np
    from PIL import Image
    a = Image.open(png_a).convert("RGB")
    b = Image.open(png_b).convert("RGB").resize(a.size)
    d = np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16))
    return float((d.max(axis=2) > thresh).mean())


def ink_fraction(png: str, thresh: int = 20) -> float:
    """Fraction of pixels differing from the image's own background (corner).

    thresh=20 (not 40): pale-but-visible content like #fff9e6-on-white (Δ=25)
    must count as ink, or the coverage guard rejects faithful renders — vis5_px
    live find: 3.4% pixel diff, '14% coverage', pure metric artifact. Uniform
    blank pages still score 0 at any threshold.
    """
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)
    bg = a[2, 2]
    return float((np.abs(a - bg).max(axis=2) > thresh).mean())


def check_task(html: str, task: dict) -> tuple[bool, str]:
    """Dispatch: pixel oracle if the task declares `pixel`, else assert tests."""
    import tempfile
    if "pixel" not in task:
        return check_html(html, task["test"])
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        out = f.name
    if not render_html_png(html, out):
        return False, "RENDER: headless Chrome failed to screenshot the HTML"
    d = pixel_diff(task["image"], out)
    cov = ink_fraction(out) / max(ink_fraction(task["image"]), 1e-6)
    if d <= task["pixel"] and cov >= 0.5:
        return True, ""
    # M2 lesson, applied visually: bare numbers fail, directional detail works.
    # Per-quadrant ink comparison tells the model WHERE it went wrong.
    report, layout_mismatch = quadrant_report(task["image"], out)
    err = (f"PIXEL_DIFF: {d:.1%} of pixels differ (budget "
           f"{task['pixel']:.0%}), ink coverage {cov:.0%} of reference\n"
           + report)
    # real4 lesson: when coverage is high, the diff is mostly typographic
    # (font family/size/link styling makes the same words land on different
    # pixels) — even if a quadrant still mismatches, because a bigger font
    # also pushes content out of a quadrant. Say so explicitly and give a
    # checklist; bare diffs can't express any of this (the 4B keeps its
    # Arial + no-underline-link prior through 4 attempts otherwise).
    if cov >= 0.7 and d > task["pixel"]:
        lead = ("Your layout densities match the target everywhere, so the "
                "difference is TYPOGRAPHIC, not structural:" if not layout_mismatch
                else "Your layout is close (minor quadrant mismatches aside), "
                "and the diff is largely TYPOGRAPHIC, not structural:")
        tb, cb = blue_frac(task["image"]), blue_frac(out)
        err += (f"\n{lead} the same text is "
                f"landing on different pixels because your font family, font "
                f"size, or link styling differs from the target.\n"
                f"Blue-pixel share (link/border blue): target "
                f"{tb:.1%} vs yours {cb:.1%}.\n")
        # Measured, unconditional instruction beats conditional advice: the
        # 4B keeps restyling links to black/unstyled even when TOLD to check
        # (real4, 2026-09-24). Evidence-backed imperative is the last lever
        # before calling it a model-capability ceiling.
        if tb - cb > 0.02:
            err += (f"MEASURED FACT: the target has {tb:.1%} blue pixels and "
                    f"your render has only {cb:.1%} — the target's links ARE "
                    f"blue and underlined and yours are NOT. You MUST delete "
                    f"every `a` rule from your CSS (no color, no "
                    f"text-decoration overrides) so links render blue and "
                    f"underlined by default.\n")
        err += ("Typography checklist — inspect the target image and match it "
                "EXACTLY: (1) FONT: if the target's text has serifs (small "
                "strokes at letter ends, like Times), use `font-family: serif` "
                "or no font CSS at all — NOT Arial/sans-serif. Default browser "
                "HTML IS serif. (2) LINKS: browser-default links are blue "
                "(#0000ee) and UNDERLINED — if the target's links look like "
                "that, do not restyle `a` in any way (no color, no "
                "text-decoration overrides). (3) SIZES: a default <h1> is 2em "
                "serif bold, not 24px sans. When the target looks like plain "
                "unstyled HTML, the correct stylesheet is NO stylesheet.")
    return False, err


def blue_frac(png: str) -> float:
    """Fraction of pixels that read as link/border blue (#0000ee-like).

    Objective typography signal: browser-default links and focus borders are
    blue; a VLM that restyles links to black (real4 attempt-1 failure) shows a
    blue-fraction collapse that quadrant ink density cannot see.
    """
    import numpy as np
    from PIL import Image
    a = np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)
    blue = (a[..., 2] - np.maximum(a[..., 0], a[..., 1])) > 40
    return float(blue.mean())


def quadrant_report(target_png: str, cand_png: str) -> tuple[str, bool]:
    """Ink density per quadrant, candidate vs target — directional feedback.
    Returns (report, any_mismatch)."""
    import numpy as np
    from PIL import Image
    lines = ["Layout comparison (ink density per region, YOURS vs target):"]
    names = [("top-left", (0, 0)), ("top-right", (1, 0)),
             ("bottom-left", (0, 1)), ("bottom-right", (1, 1))]
    ta = np.asarray(Image.open(target_png).convert("RGB").resize((480, 320)),
                    dtype=int)
    ca = np.asarray(Image.open(cand_png).convert("RGB").resize((480, 320)),
                    dtype=int)

    def ink(arr, qx, qy):
        h, w = arr.shape[:2]
        cell = arr[qy * h // 2:(qy + 1) * h // 2, qx * w // 2:(qx + 1) * w // 2]
        # robust bg: the cell's MOST COMMON color (exact mode). cell[0,0]
        # breaks when the quadrant's corner pixel lands on a glyph (real4
        # bottom-right read 99% ink); per-channel median breaks on ~50/50
        # two-color quadrants (HN orange header: the median lands BETWEEN
        # the colors and everything reads as ink).
        flat = cell.reshape(-1, cell.shape[-1])
        colors, counts = np.unique(flat, axis=0, return_counts=True)
        bg = colors[np.argmax(counts)]
        return float((np.abs(cell - bg).max(axis=-1) > 20).mean())

    flagged = False
    for name, (qx, qy) in names:
        c, t = ink(ca, qx, qy), ink(ta, qx, qy)
        if abs(c - t) > 0.08:
            flagged = True
        flag = "   <-- MISMATCH" if abs(c - t) > 0.08 else ""
        lines.append(f"  {name}: yours {c:.0%} vs target {t:.0%}{flag}")
    return "\n".join(lines), flagged


FIX_TEMPLATE = ("Your HTML failed this check:\n{err}\n\n"
                "Look at the screenshot again and return ONLY the corrected, "
                "complete HTML in one fenced block.")

VISUAL_FIX_TEMPLATE = (
    "Image 1 is the target screenshot. Image 2 is how YOUR previous HTML "
    "actually rendered. It failed this check:\n{err}\n\n"
    "Compare the two images carefully — fix the layout, colors, and text so "
    "your render matches the target. Return ONLY the corrected, complete "
    "HTML in one fenced code block.")


def solve_vision(repo: str, task: dict, max_attempts: int = 2,
                 max_tokens: int = 1024) -> VisionResult:
    """Screenshot -> HTML with test-feedback retries. Same arc as solve().

    Pixel-oracle retries are a TRUE closed loop (M5): the attempt's own render
    is fed back to the VLM side-by-side with the target — the agent sees its
    output, not just an error string.
    """
    import tempfile
    import time
    model, processor = load_vlm(repo)
    msgs_extra = ""
    t0 = time.time()
    attempts: list[tuple[str, str]] = []
    images = [task["image"]]
    for i in range(max_attempts):
        prompt = task["prompt"] + msgs_extra
        if len(images) == 2 and i > 0:
            prompt = (VISUAL_FIX_TEMPLATE.format(err=attempts[-1][1])
                      if attempts else prompt)
        out = generate_with_image(model, processor, images, prompt,
                                  max_tokens=max_tokens,
                                  temp=0.0 if i == 0 else 0.7, seed=i)
        html = extract_html(out)
        ok, err = check_task(html, task)
        attempts.append((html, "" if ok else err))
        if ok:
            return VisionResult(True, html, attempts, time.time() - t0)
        if "pixel" in task and render_html_png(
                html, (render := tempfile.mktemp(suffix=".png"))):
            images = [task["image"], render]   # show the model its own render
        msgs_extra = "\n\n" + FIX_TEMPLATE.format(err=err)
    return VisionResult(False, attempts[-1][0], attempts, time.time() - t0)
