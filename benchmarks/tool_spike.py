"""M5 tool spike (real4 standing probe): can a VLM CHOOSE a mechanical edit it
cannot PERFORM itself?

Evidence chain this spike sits on:
  - real4_cern_px: the 4B keeps its Arial/unstyled-link prior through 4 rounds
    of imperative feedback ("MEASURED FACT: target links ARE blue... You MUST
    delete every `a` rule") — and the 8B escalated model failed IDENTICALLY
    (task_0001). Feedback-following failure, not scale.
  - Web spike v3: small models under-use provided context (knob in prompt,
    knob ignored). Same signature, second domain.
  => The agentic lever: TOOLS that perform the edit mechanically, so the model
     only has to CHOOSE it. `strip_css` output IS the attempt submission — no
     re-generation, nothing to follow. `css_diff` is informational (weaker
     form: visibility without mechanical action).

Arms per task (same VLM, same prompts except the tool menu):
  baseline — the canonical solve_vision loop (visual feedback only)
  tools    — every prompt adds the tool menu. `TOOL: crop <quad>` shows the
             model a 2x zoom of that target quadrant and captures its
             transcription as carried text (free, once per quadrant);
             `TOOL: make_default` strips CSS + adds placeholder hrefs and
             submits the result AS the attempt (mechanical edit-as-action).

Phase 0 (deterministic, no GPU): apply the edit tool to the BASELINE arm's
failed HTML and run the pixel oracle — if the tool wouldn't fix it, the spike
is dead before the VLM speaks. (Verify the answer exists before blaming the
agent — the web_spike knob lesson.)

v1 (2026-09-24, real4): EDIT-CLASS TOOLS PROVEN NON-LOAD-BEARING. The model
  called strip_css readily (invocation protocol works), but phase 0 showed
  the tool's own output still scored 24% vs the 10% budget. Post-mortem:
  bare `<a>` tags with no href (0% blue even unstyled), ink 76% — a
  TRANSCRIPTION gap, not styling. Calibration: the REAL info.cern.ch HTML
  renders to 0.0% diff (oracle fair; ground truth attainable).
v2 (2026-09-24, real4): tool class retargeted to PERCEPTION (crop/zoom reads)
  + make_default finishing edit. FREE tool choice broke down: greedy 4B
  emitted `TOOL: make_default` with nothing to transform, echoed the menu
  pattern line as its HTML (blank render, 15.1%/3% coverage-guard artifact),
  then looped duplicate `crop tl` calls despite explicit correction.
v3 (2026-09-24, real4): harness-owned pipeline arm (draft -> 4x zoom-read ->
  rewrite with carried transcriptions). Zoom reads usable; the REWRITE had
  0 hrefs and 0 <dl> — the model ignored its own transcriptions; identical
  24.3% plateau. VERDICT: agentic lever dead at 4B — integration is the wall
  (third under-use-of-context sighting: web2 knob, real4 feedback, crop
  reads). Remaining: distillation, or model-as-reader/code-as-writer assembly.
v4 (2026-09-24, real4, solve_assembly BAND arm): model transcribes 2-3
  full-width 2x bands with a structure protocol (blank=block, '> '=indent,
  [a]=link); harness assembles. Phase-0 ceiling 1.9% PASS (assembler
  verified — v1 line-per-<p> assembler FAILED the ceiling at 16.9%,
  paragraphs must reflow; v2 run-block assembler + perfect_reading from
  benchmarks/assets/vis/real4_cern.html). LIVE reads failed: 23.4%/24.5%
  — the 4B emits ZERO structural markers and (misdiagnosed) 'stops
  early': the 480x320 target genuinely ends mid-list, coverage was
  complete. Structure protocol is beyond 4B instruction-following.
v5 (2026-09-24, real4, GEOMETRY arm): harness detects text-line geometry
  from the target PIXELS (row darkness profile -> 13 lines; gap>8px ->
  4 blocks; line height -> h1; x0 indent -> dt/dd roles), crops each
  block at 2.5x, and asks for COUNT-ANCHORED transcription ('exactly N
  lines', crop ONLY — with the full page in context the 4B transcribes
  image 1 and ignores the crop). Model supplies words+link spans only.
  RESULT: PASS at 3.1% diff (budget 10%), 4 block reads, 9.7s, ONE
  attempt — real4_cern_px solved for the first time at this hardware
  tier. VERDICT: the agentic lever is LIVE via CODE-AS-WRITER assembly
  — the model READS, the harness WRITES. The generation gap is not a
  wall; it is a routing decision. (M5-final, 4-arm record: this run.)

Run: .venv/bin/python benchmarks/tool_spike.py [--tasks real4_cern_px]
            [--arms baseline,tools,pipeline,assembly]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash.vision import (FIX_TEMPLATE, VISUAL_FIX_TEMPLATE, check_task,
                          extract_html, generate_with_image, load_vlm,
                          pixel_diff, render_html_png)

VLM = "mlx-community/Qwen3-VL-4B-Instruct-4bit"

TOOL_MENU = """\
You may use tools BEFORE writing HTML. Write exactly one line:
  TOOL: crop XX   — where XX is ONE quadrant code: tl, tr, bl, or br
  TOOL: make_default   — no argument
- crop: I will show you a 2x ZOOM of that one quadrant of the target and ask
  you to transcribe it. Each quadrant can be cropped once. Use crops to READ
  every word and every link before rewriting — most of your errors are
  transcription errors on small text.
- make_default: I will delete ALL CSS from your last HTML, give every link a
  placeholder href, and submit the result AS your answer: browser-default
  rendering (serif text, blue underlined links). The target is plain unstyled
  HTML — your own CSS only breaks the match.
When you are done reading, return the complete corrected HTML in one fenced
code block."""

CROP_READ_TEMPLATE = (
    "Image 1 is the target screenshot. Image 2 is a 2x zoom of its "
    "{name} quadrant. Transcribe the zoomed region EXACTLY: every word, "
    "marking which words/phrases are hyperlinks (blue, underlined) by "
    "wrapping them like [a]link text[/a]. Note the structure too (heading "
    "vs plain line, indented definition-list entries). Reply with the "
    "transcription only, no commentary.")

QUADRANTS = {"tl": (0, 0), "tr": (1, 0), "bl": (0, 1), "br": (1, 1)}
QUAD_NAMES = {"tl": "top-left", "tr": "top-right",
              "bl": "bottom-left", "br": "bottom-right"}


def crop_quadrant(png: str, quad: str, out: str) -> bool:
    """2x upscaled crop of one quadrant of the target — the perception tool."""
    from PIL import Image
    try:
        im = Image.open(png).convert("RGB")
        w, h = im.size
        qx, qy = QUADRANTS[quad]
        c = im.crop((qx * w // 2, qy * h // 2, (qx + 1) * w // 2,
                     (qy + 1) * h // 2))
        c.resize((w, h), Image.LANCZOS).save(out)
        return True
    except Exception:
        return False


def tool_strip_css(html: str) -> str:
    """Mechanical edit-as-action: delete every <style> block and style= attr."""
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    html = re.sub(r"\s+style=(\"[^\"]*\"|'[^']*')", "", html, flags=re.I)
    return html


def normalize_links(html: str) -> str:
    """Bare <a>text</a> -> <a href="#">text</a> so links render as links."""
    return re.sub(r"<a(?![^>]*\bhref=)([^>]*)>", r'<a href="#"\1>', html)


def make_default(html: str) -> str:
    """Browser-default rendering in one mechanical step: strip CSS + fix links."""
    return normalize_links(tool_strip_css(html))


# Line-anchored (prose mentions of "TOOL: crop tl" mid-sentence don't fire),
# but tolerant of trailing junk after the args — 4B outputs are rarely clean.
TOOL_RE = re.compile(r"^\s*TOOL:\s*(crop|make_default)\b[ \t]*([a-z]{2})?",
                     re.M)



def solve_arm(model, processor, task: dict, tools: bool,
              max_attempts: int = 4, max_tokens: int = 1024,
              dump: Path | None = None, arm: str = "") -> dict:
    """solve_vision mirrored with the model hoisted out (model loaded once for
    the whole suite) plus the tool protocol. Same prompt/feedback flow."""
    import tempfile
    t0 = time.time()
    attempts, tool_calls, crops_done = [], [], set()
    free = 6                      # tool re-prompts per run, then we submit as-is
    images, msgs_extra = [task["image"]], ""
    for i in range(max_attempts):
        prompt = task["prompt"] + msgs_extra
        if len(images) == 2 and i > 0:
            prompt = VISUAL_FIX_TEMPLATE.format(err=attempts[-1][1])
        if tools:
            prompt += "\n\n" + TOOL_MENU
        out = generate_with_image(model, processor, images, prompt,
                                  max_tokens=max_tokens,
                                  temp=0.0 if i == 0 else 0.7, seed=i)
        m = TOOL_RE.search(out) if tools else None
        tool = ""
        if m and m.group(1) == "crop" and free > 0:
            free -= 1
            quad = m.group(2) or ""
            if quad in QUADRANTS and quad not in crops_done and crop_quadrant(
                    task["image"], quad,
                    (zq := tempfile.mktemp(suffix=".png"))):
                crops_done.add(quad)
                # stateless API: the zoom read must be captured as TEXT and
                # carried in msgs_extra, or the next generate can't see it.
                reading = generate_with_image(
                    model, processor, [task["image"], zq],
                    CROP_READ_TEMPLATE.format(name=QUAD_NAMES[quad]),
                    max_tokens=512, temp=0.0)
                tool_calls.append(f"attempt{i+1}: crop {quad} "
                                  f"({len(reading.strip())} chars read)")
                msgs_extra += (f"\n\nExact transcription of the target's "
                               f"{QUAD_NAMES[quad]} quadrant (via 2x zoom):\n"
                               f"{reading.strip()}\n")
                if dump is not None:
                    (dump / f"{task['id']}_{arm}_crop_{quad}.txt"
                     ).write_text(reading)
            else:
                # invalid or duplicate crop: correct it — never submit the
                # tool line itself as HTML (v2 live find: 4B echoed the menu
                # pattern and the loop submitted it, blank render, 15.1%/3%)
                tool_calls.append(f"attempt{i+1}: INVALID crop {quad or '?'}"
                                  f" (done: {''.join(sorted(crops_done))})")
                msgs_extra += ("\n\nThat crop was invalid or already used. "
                               "Reply with ONE unused quadrant code (tl, tr, "
                               "bl, br) as TOOL: crop XX, or TOOL: "
                               "make_default, or the complete HTML in one "
                               "fenced block.\n")
            continue                    # perception: free, same attempt
        if m and m.group(1) == "make_default" and attempts:
            cand = make_default(attempts[-1][0])
            tool_calls.append(f"attempt{i+1}: make_default "
                              f"({len(attempts[-1][0])} -> {len(cand)} chars)")
            html, tool = cand, "make_default"
        else:
            html = extract_html(out)
        ok, err = check_task(html, task)
        attempts.append((html, "" if ok else err))
        if dump is not None:
            (dump / f"{task['id']}_{arm}_att{i+1}.html").write_text(html)
            png = dump / f"{task['id']}_{arm}_att{i+1}.png"
            if render_html_png(html, png):
                d = pixel_diff(task["image"], str(png))
                tag = f"[{tool}]" if tool else ""
                print(f"{'':24s}   att{i+1}{tag} diff={d:.1%} "
                      f"{'PASS' if ok else err.splitlines()[0]}", flush=True)
        if ok:
            return {"solved": True, "attempts": len(attempts),
                    "tool_calls": tool_calls, "s": round(time.time() - t0, 1)}
        if render_html_png(html, (r := tempfile.mktemp(suffix=".png"))):
            images = [task["image"], r]
        msgs_extra = "\n\n" + FIX_TEMPLATE.format(err=err)
    return {"solved": False, "attempts": len(attempts),
            "tool_calls": tool_calls, "s": round(time.time() - t0, 1),
            "last_html": attempts[-1][0], "last_err": attempts[-1][1]}

def solve_pipeline(model, processor, task: dict, max_tokens: int = 1024,
                   dump: Path | None = None, arm: str = "pipe") -> dict:
    """Harness-owned agency: no menu, no choices. Draft -> the HARNESS crops
    and reads all four quadrants -> rewrite with the transcriptions + the
    draft's oracle error. Submissions: the rewrite, then make_default(rewrite).

    v2 menu-arm finding: the greedy 4B emits `TOOL: make_default` with nothing
    to transform, loops duplicate crops despite explicit correction — FREE
    tool choice is beyond 4B-class control. This arm tests the remaining
    question: does zoom-READING + carried transcription suffice when the
    harness owns the control flow? (M5 for models that can't drive.)
    """
    import tempfile
    t0 = time.time()

    def score(html, tag, n):
        ok, err = check_task(html, task)
        if dump is not None:
            (dump / f"{task['id']}_{arm}_att{n}.html").write_text(html)
            png = dump / f"{task['id']}_{arm}_att{n}.png"
            if render_html_png(html, png):
                d = pixel_diff(task["image"], str(png))
                print(f"{'':24s}   att{n}{tag} diff={d:.1%} "
                      f"{'PASS' if ok else err.splitlines()[0]}", flush=True)
        return ok, err

    draft = extract_html(generate_with_image(
        model, processor, [task["image"]], task["prompt"],
        max_tokens=max_tokens, temp=0.0))
    ok, err = score(draft, "[draft]", 1)
    if ok:
        return {"solved": True, "attempts": 1, "tool_calls": ["draft passed"],
                "s": round(time.time() - t0, 1)}

    readings = []
    for quad in ("tl", "tr", "bl", "br"):
        zq = tempfile.mktemp(suffix=".png")
        if not crop_quadrant(task["image"], quad, zq):
            continue
        r = generate_with_image(model, processor, [task["image"], zq],
                                CROP_READ_TEMPLATE.format(
                                    name=QUAD_NAMES[quad]),
                                max_tokens=512, temp=0.0).strip()
        readings.append(f"{QUAD_NAMES[quad]} quadrant:\n{r}")
        if dump is not None:
            (dump / f"{task['id']}_{arm}_crop_{quad}.txt").write_text(r)

    rewrite_prompt = (
        task["prompt"] + "\n\nYour first draft failed this check:\n" + err +
        "\n\nHere are exact transcriptions of the target's four quadrants, "
        "read at 2x zoom:\n\n" + "\n\n".join(readings) +
        "\n\nRewrite the complete HTML to match these transcriptions EXACTLY: "
        "every word, every link, the same structure. Return ONLY the HTML in "
        "one fenced block.")
    rewrite = extract_html(generate_with_image(
        model, processor, [task["image"]], rewrite_prompt,
        max_tokens=max_tokens, temp=0.0))
    ok2, err2 = score(rewrite, "[rewrite]", 2)
    if ok2:
        return {"solved": True, "attempts": 2,
                "tool_calls": ["4x crop + rewrite"], "s": round(time.time()-t0, 1)}
    defaulted = make_default(rewrite)
    ok3, err3 = score(defaulted, "[make_default]", 3)
    return {"solved": ok3, "attempts": 3,
            "tool_calls": ["4x crop + rewrite + make_default"],
            "s": round(time.time() - t0, 1),
            "last_html": defaulted, "last_err": err3}




# ---- v4 arm: model-as-reader, CODE-as-writer assembly -----------------------
# The v3 finding: the 4B's zoom transcriptions are GOOD, but its rewrite
# ignores them (0 hrefs, 0 <dl>) — so remove generation from the loop
# entirely. The model only transcribes; the harness parses the readings
# into markup. Bands, not quadrants: a quadrant crop splits long text
# lines across left/right and the model transcribes the two halves
# INCONSISTENTLY (v3: tl said "an [executive summary]", tr said
# "[a]executive summary[/a]" — unmergeable). Full-width bands put every
# text line whole in exactly one read; the 10% seam overlap is deduped.

BANDS = {"top": (0.0, 0.40), "mid": (0.30, 0.70),
        "bottom": (0.60, 1.0)}   # ~10% seam overlap, deduped

BAND_READ_TEMPLATE = (
    "Image 1 is the target screenshot. Image 2 is a 2x zoom of its "
    "horizontal band of it. Transcribe the zoomed band EXACTLY, as "
    "plain text: one output line per text line; a BLANK LINE wherever "
    "the page has vertical space between blocks; prefix each INDENTED "
    "line (set in from the left margin) with '> '; wrap hyperlinks "
    "(blue, underlined) like [a]link text[/a]. Transcribe EVERY line "
    "in the band from top to bottom — do not stop early, do not "
    "summarize. Transcription only, no commentary.")

_A_MARKED = re.compile(r"\[a\](.*?)\[/a\]")
_A_UNTERMINATED = re.compile(r"\[a\](.*)$")
_A_BARE = re.compile(r"\[([^\[\]/][^\[\]]*)\]")


def _linkify(ln: str) -> str:
    """[a]x[/a], unterminated [a]x, and bare [x] (the style the 4B
    actually emits) all become links — on this target every bracketed
    span IS a link."""
    ln = _A_MARKED.sub(r'<a href="#">\1</a>', ln)
    ln = _A_UNTERMINATED.sub(r'<a href="#">\1</a>', ln)
    return _A_BARE.sub(r'<a href="#">\1</a>', ln)


def crop_band(png: str, band: str, out: str) -> bool:
    """2x upscaled full-width band (top/bottom half with seam overlap)."""
    from PIL import Image
    try:
        im = Image.open(png).convert("RGB")
        w, h = im.size
        f0, f1 = BANDS[band]
        c = im.crop((0, int(f0 * h), w, int(f1 * h)))
        c.resize((w * 2, c.height * 2), Image.LANCZOS).save(out)
        return True
    except Exception:
        return False


def _wrap(text: str, width: int = 72) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def assemble_html(readings: list[str]) -> str:
    """CODE AS WRITER: parse marked transcriptions into markup — the model
    never writes HTML. Protocol: one line per text line, BLANK lines mark
    block boundaries, leading SPACE marks indented (dd) lines, [a] marks
    links. Assembly: dedupe seam-overlap lines; a block containing
    indented runs becomes a <dl> (unindented runs -> <dt>, indented ->
    <dd>); each run's lines are space-joined so the BROWSER reflows text
    to full width; plain blocks become <p>; first short line -> <h1>.
    NO CSS: the browser-default stylesheet is the target's style. (v1 of
    this assembler emitted one <p> per transcription line: ink coverage
    53%, ceiling 16.9% > budget — paragraphs must REFLOW.)"""
    lines, seen = [], set()
    for r in readings:
        for ln in r.splitlines():
            key = ln.strip().lstrip(">").strip()
            if not key:
                lines.append("")
            elif key not in seen:
                seen.add(key)
                lines.append(ln.rstrip())
    blocks, cur = [], []
    for ln in lines:
        if ln.strip():
            cur.append(ln)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)

    out, first = [], True
    for blk in blocks:
        runs, buf, ind = [], [], None
        for ln in blk:
            i = ln.startswith(">") or ln[:1] in (" ", "\t")
            if ind is not None and i != ind:
                runs.append((ind, buf))
                buf = []
            ind = i
            buf.append(ln.lstrip("> \t").rstrip() if i else ln.strip())
        if buf:
            runs.append((ind, buf))
        if any(i for i, _ in runs):          # mixed/indented block -> <dl>
            out.append("<dl>")
            for i, buf in runs:
                tag = "dd" if i else "dt"
                out.append(f"<{tag}>{_linkify(' '.join(buf))}</{tag}>")
            out.append("</dl>")
        else:
            for _, buf in runs:
                text = _linkify(" ".join(buf))
                if first and len(text) < 40:
                    out.append(f"<h1>{text}</h1>")
                else:
                    out.append(f"<p>{text}</p>")
                first = False
    return "<html><body>\n" + "\n".join(out) + "\n</body></html>"


def perfect_reading(raw_html: str) -> str:
    """Ground-truth transcription in the band-read protocol: what
    assemble_html receives from a PERFECT reader. Phase 0 for the
    assembly arm — if this ceiling fails the oracle, the arm is dead
    pre-flight (the web_spike knob lesson: verify the answer exists
    first). Block tags -> block breaks, <dt> -> line break, <dd>..</dd>
    -> indented lines; runs are reflowed and re-wrapped at 72 cols so
    [a] markers never span lines (whitespace-token wrap)."""
    import html as _html
    s = re.sub(r"<(head|header)\b.*?</\1>", "", raw_html, flags=re.S | re.I)
    s = re.sub(r"<a\s[^>]*>(.*?)</a>",
               lambda m: "[a]" + " ".join(m.group(1).split()) + "[/a]",
               s, flags=re.S | re.I)
    s = re.sub(r"</?(?:p|h1|dl|ul|li|body|html)\b[^>]*>", "\x00", s,
               flags=re.S | re.I)
    s = re.sub(r"</?dt[^>]*>", "\x03", s, flags=re.S | re.I)
    s = re.sub(r"<dd[^>]*>", "\x01", s, flags=re.S | re.I)
    s = re.sub(r"</dd\s*>", "\x02", s, flags=re.S | re.I)
    s = _html.unescape(re.sub(r"<[^>]+>", "", s))
    out, ind, parts = [], False, []

    def flush():
        if parts:
            out.extend(("> " if ind else "") + ln
                       for ln in _wrap(" ".join(parts)))
            parts.clear()

    for seg in re.split(r"([\x00\x01\x02\x03])", s):
        if seg == "\x00":
            flush()
            ind = False            # block tags implicitly close an open <dd>
            if out and out[-1] != "":
                out.append("")
        elif seg == "\x01":
            flush()
            ind = True
        elif seg == "\x02":
            flush()
            ind = False
        elif seg == "\x03":
            flush()
            ind = False            # 1993 HTML: <dt> implies </dd> — the CERN
        else:                      # page never closes its <dd>s explicitly
            parts.extend(seg.split())
    flush()
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


# ---- v5: GEOMETRY-driven assembly --------------------------------------------
# v4 post-mortem (three runs): the 4B's WORDS are mostly right but it emits
# ZERO structural markers (no blank lines, no indent prefixes) and stops
# transcriptions early. So v5 takes structure away from the model entirely:
# the harness detects text-line geometry from the target PIXELS, crops each
# block, and asks for a count-anchored transcription ("exactly N lines").
# The model's only job: words + link spans — its one demonstrated skill.

BLOCK_READ_TEMPLATE = (
    "This image is a zoomed crop of {what} from a web page. It contains "
    "exactly {n} line{s} of text. Transcribe ALL {n} lines EXACTLY, one "
    "output line per text line, in top-to-bottom order. Wrap hyperlinks "
    "(blue, underlined) like [a]link text[/a]. No commentary, no blank "
    "lines.")

MAX_LINES_PER_READ = 8        # v4: the 4B stops early past ~6 lines


def line_geometry(png: str) -> list[dict]:
    """Deterministic text-line detection from the TARGET pixels: a line is
    a run of rows with >2 dark pixels (1-row gaps bridged for thin
    glyphs); x0 = leftmost dark pixel. The harness owns STRUCTURE
    because the 4B won't emit it (v4: zero markers, three runs)."""
    from PIL import Image
    im = Image.open(png).convert("RGB")
    w, h = im.size
    px = im.load()
    dark = []
    for y in range(h):
        xs = [x for x in range(w) if sum(px[x, y]) < 500]
        dark.append((min(xs) if xs else None, len(xs)))
    lines, y = [], 0
    while y < h:
        if dark[y][1] > 2:
            y0, x0 = y, dark[y][0]
            while y < h and (dark[y][1] > 2
                             or (y + 1 < h and dark[y + 1][1] > 2)):
                if dark[y][1] > 2 and dark[y][0] is not None:
                    x0 = dark[y][0] if x0 is None else min(x0, dark[y][0])
                y += 1
            lines.append({"y0": y0, "y1": y, "x0": x0 or 0, "h": y - y0})
        else:
            y += 1
    return lines


def group_blocks(lines: list[dict]) -> list[list[dict]]:
    """Inter-line gap > 8px = a block break (paragraph margins ~1em;
    intra-paragraph leading is a few px)."""
    blocks, cur, prev_y1 = [], [], None
    for ln in lines:
        if prev_y1 is not None and ln["y0"] - prev_y1 > 8:
            blocks.append(cur)
            cur = []
        cur.append(ln)
        prev_y1 = ln["y1"]
    if cur:
        blocks.append(cur)
    return blocks


def block_role(blk: list[dict], median_h: float) -> str:
    if any(ln["h"] > 1.5 * median_h for ln in blk):
        return "h1"
    x0s = [ln["x0"] for ln in blk]
    if max(x0s) - min(x0s) > 20:
        return "dl"
    return "p"


def crop_block(png: str, y0: int, y1: int, out: str,
               zoom: float = 2.5) -> bool:
    from PIL import Image
    try:
        im = Image.open(png).convert("RGB")
        w, _ = im.size
        c = im.crop((0, max(0, y0), w, y1))
        c.resize((int(w * zoom), int(c.height * zoom)),
                 Image.LANCZOS).save(out)
        return True
    except Exception:
        return False


def solve_assembly(model, processor, task: dict, max_tokens: int = 1024,
                   dump: Path | None = None, arm: str = "asm") -> dict:
    """v5 arm: geometry -> count-anchored block reads -> v4 assembler."""
    import tempfile
    t0 = time.time()

    # Phase 0: the assembly ceiling — a PERFECT reading, built from the
    # reference page source stored as a sibling of the target screenshot
    # (real4_cern.png -> real4_cern.html). No asset -> proceed unverified.
    ref = Path(task["image"]).with_suffix(".html")
    if ref.exists():
        ceiling = assemble_html([perfect_reading(ref.read_text())])
        ok0, err0 = check_task(ceiling, task)
        if dump is not None:
            (dump / f"{task['id']}_{arm}_phase0.html").write_text(ceiling)
            png = dump / f"{task['id']}_{arm}_phase0.png"
            if render_html_png(ceiling, png):
                d = pixel_diff(task["image"], str(png))
                print(f"{'':24s}   assembly phase0 ceiling diff={d:.1%} "
                      f"{'PASS' if ok0 else err0.splitlines()[0]}",
                      flush=True)
        if not ok0:
            return {"solved": False, "attempts": 0,
                    "tool_calls": ["phase0 ceiling FAILED — dead pre-flight"],
                    "s": round(time.time() - t0, 1)}
    else:
        print(f"{'':24s}   assembly phase0 SKIPPED — no {ref}", flush=True)

    lines = line_geometry(task["image"])
    blocks = group_blocks(lines)
    hs = sorted(ln["h"] for ln in lines)
    med = hs[len(hs) // 2]
    roles = [block_role(b, med) for b in blocks]
    print(f"{'':24s}   geometry: {len(lines)} lines, {len(blocks)} blocks "
          f"{[(r, len(b)) for r, b in zip(roles, blocks)]}", flush=True)

    what = {"h1": "a heading", "p": "a paragraph",
            "dl": "a definition list"}
    proto_blocks, nreads = [], 0
    for bi, (blk, role) in enumerate(zip(blocks, roles)):
        tlines = []
        for c0 in range(0, len(blk), MAX_LINES_PER_READ):
            chunk = blk[c0:c0 + MAX_LINES_PER_READ]
            zb = tempfile.mktemp(suffix=".png")
            if not crop_block(task["image"], chunk[0]["y0"] - 2,
                              chunk[-1]["y1"] + 2, zb):
                continue
            n = len(chunk)
            r = generate_with_image(
                model, processor, [zb],   # CROP ONLY: with the full page
                # in context the 4B transcribes image 1 and ignores the
                # crop (v5 run 1: h1 text zipped into <dt> roles)
                BLOCK_READ_TEMPLATE.format(what=what[role], n=n,
                                           s="s" if n > 1 else ""),
                max_tokens=min(max_tokens, 70 * n + 40), temp=0.0).strip()
            nreads += 1
            tlines.extend(ln for ln in r.splitlines() if ln.strip())
            if dump is not None:
                (dump / f"{task['id']}_{arm}_blk{bi}_{c0}.txt").write_text(r)
        if role == "dl":
            x0min = min(l["x0"] for l in blk)
            proto = [("> " if l["x0"] - x0min > 20 else "") + t
                     for l, t in zip(blk, tlines)]
        else:
            proto = tlines
        proto_blocks.append("\n".join(proto))

    html = assemble_html(["\n\n".join(proto_blocks)])
    ok, err = check_task(html, task)
    if dump is not None:
        (dump / f"{task['id']}_{arm}_final.html").write_text(html)
        png = dump / f"{task['id']}_{arm}_final.png"
        if render_html_png(html, png):
            d = pixel_diff(task["image"], str(png))
            print(f"{'':24s}   assembly final diff={d:.1%} "
                  f"{'PASS' if ok else err.splitlines()[0]}", flush=True)
    return {"solved": ok, "attempts": 1,
            "tool_calls": [f"geometry + {nreads} block reads + assembly"],
            "s": round(time.time() - t0, 1),
            "last_html": html, "last_err": err}



def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=None, help="comma-separated ids (all 5 "
                    "real tasks by default)")
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--arms", default="baseline,tools,pipeline,assembly",
                    help="comma list: baseline,tools,pipeline,assembly")
    args = ap.parse_args()

    tasks = [json.loads(x) for x in
             Path("benchmarks/tasks/visr_tasks.jsonl").read_text().splitlines()
             if x.strip()]
    if args.tasks:
        want = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in want]
    if subprocess.run(["pgrep", "-f", "run-vis-suite"],
                      capture_output=True).returncode == 0:
        raise SystemExit("run-vis-suite active — wait for the GPU to free up")

    model, processor = load_vlm(VLM)   # ONE load for the whole spike
    arms = {a.strip() for a in args.arms.split(",") if a.strip()}
    if args.skip_baseline:
        arms.discard("baseline")
    dump = Path("/tmp/tool_spike_artifacts")
    dump.mkdir(exist_ok=True)
    rows = []
    for t in tasks:
        row = {"id": t["id"]}
        if "baseline" in arms:
            row["baseline"] = solve_arm(model, processor, t, tools=False,
                                        dump=dump, arm="base")
            b = row["baseline"]
            print(f"{t['id']:24s} baseline -> "
                  f"{'PASS' if b['solved'] else 'FAIL'} "
                  f"({b['attempts']} attempts, {b['s']}s)", flush=True)
            # Phase 0: would the edit tool have fixed the baseline's last HTML?
            if not b["solved"] and "last_html" in b:
                stripped = make_default(b["last_html"])
                (dump / f"{t['id']}_phase0.html").write_text(stripped)
                ok, err = check_task(stripped, t)
                row["phase0_strip_fixes"] = ok
                first = err.splitlines()[0] if err else "within budget"
                print(f"{'':24s} phase0: make_default on failed baseline HTML "
                      f"-> oracle {'PASS' if ok else 'FAIL'} ({first})",
                      flush=True)
                if err:
                    (dump / f"{t['id']}_phase0_err.txt").write_text(err)
        if "tools" in arms:
            row["tools"] = solve_arm(model, processor, t, tools=True,
                                     dump=dump, arm="tool")
            tr = row["tools"]
            print(f"{t['id']:24s} tools    -> "
                  f"{'PASS' if tr['solved'] else 'FAIL'} "
                  f"({tr['attempts']} attempts, {tr['s']}s)  calls: "
                  f"{tr['tool_calls'] or 'none'}", flush=True)
        if "pipeline" in arms:
            row["pipeline"] = solve_pipeline(model, processor, t, dump=dump)
            p = row["pipeline"]
            print(f"{t['id']:24s} pipeline -> "
                  f"{'PASS' if p['solved'] else 'FAIL'} "
                  f"({p['attempts']} attempts, {p['s']}s)", flush=True)
        if "assembly" in arms:
            row["assembly"] = solve_assembly(model, processor, t, dump=dump)
            a = row["assembly"]
            print(f"{t['id']:24s} assembly -> "
                  f"{'PASS' if a['solved'] else 'FAIL'} "
                  f"({a['attempts']} attempts, {a['s']}s)  "
                  f"{a['tool_calls'][0]}", flush=True)
        rows.append(row)

    print("\n== VERDICT ==")
    ARM_ORDER = ("baseline", "tools", "pipeline", "assembly")
    for row in rows:
        parts, swung = [], []
        for name in ARM_ORDER:
            r = row.get(name)
            if r is None:
                continue
            parts.append(f"{name} {'PASS' if r['solved'] else 'FAIL'}")
            if name != "baseline" and r["solved"]:
                swung.append(name.upper())
        p0 = ""
        if "phase0_strip_fixes" in row:
            p0 = ("  phase0-edit=fixes" if row["phase0_strip_fixes"]
                  else "  phase0-edit=no-fix")
        swing = ""
        if swung and row.get("baseline") and not row["baseline"]["solved"]:
            swing = f"  <-- {'/'.join(swung)} SWUNG IT"
        print(f"  {row['id']}: " + "  ".join(parts) + p0 + swing)

    def swung_any(arm: str) -> bool:
        return any(r.get("baseline") and not r["baseline"]["solved"]
                   and r.get(arm, {}).get("solved") for r in rows)

    if swung_any("assembly"):
        lev = ("LIVE via CODE-AS-WRITER assembly — the model READS, the "
               "harness WRITES; the generation gap is bypassed entirely")
    elif swung_any("pipeline"):
        lev = ("LIVE via HARNESS-OWNED agency — crop-read+rewrite pipeline "
               "passed where feedback and free tool choice failed")
    elif swung_any("tools"):
        lev = "LIVE via free tool choice"
    else:
        lev = "not demonstrated"
    print("  agentic lever: " + lev)


if __name__ == "__main__":
    main()
