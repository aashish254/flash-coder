"""Budget calibration probe for visr tasks (recipe from PLAN App. A 2026-09-24).

For each task: blank-page diff+ink (exploit check), and a hand-written
'mediocre approximation' diff+ink. An honest budget sits just above the
mediocre diff; a budget BELOW the mediocre diff is unpassable, and a budget
ABOVE the blank diff is exploitable.

Run: .venv/bin/python benchmarks/calibrate_visr.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash.harness import load_tasks
from flash.vision import ink_fraction, pixel_diff, render_html_png

ROOT = Path(__file__).resolve().parent

BLANK = "<html><body></body></html>"

APPROX = {
    # real2: orange header + 6 story rows, approximate fonts/colors
    "real2_hn_px": """<html><body style="margin:0;background:#f6f6ef;font-family:Verdana,sans-serif;font-size:10pt">
<div style="background:#ff6600;padding:2px 4px">
<span style="border:1px solid #fff;color:#fff;padding:0 3px;margin-right:4px">Y</span>
<b>Hacker News</b>
<a style="margin-left:6px;text-decoration:none;color:#000">new</a> |
<a style="text-decoration:none;color:#000">past</a> |
<a style="text-decoration:none;color:#000">comments</a> |
<a style="text-decoration:none;color:#000">ask</a> |
<a style="text-decoration:none;color:#000">show</a> |
<a style="text-decoration:none;color:#000">jobs</a> |
<a style="text-decoration:none;color:#000">submit</a>
<span style="float:right"><a style="text-decoration:none;color:#000">login</a></span>
</div>
<ol style="margin:6px 0 0 0;padding-left:26px;color:#828282;font-size:10pt">
<li><span style="color:#000">Linux support is coming to Snapdragon X2 Series</span>
<span style="font-size:8pt">(qualcomm.com)</span><br>
<span style="font-size:7pt">161 points by aaronday 3 hours ago | hide | 69 comments</span></li>
<li><span style="color:#000">Claude discovers a novel enzyme system with CRISPR-like repeats</span>
<span style="font-size:8pt">(anthropic.com)</span><br>
<span style="font-size:7pt">509 points by raahelb 8 hours ago | hide | 534 comments</span></li>
<li><span style="color:#000">Meta VR Glasses</span>
<span style="font-size:8pt">(meta.com)</span><br>
<span style="font-size:7pt">228 points by polymorph1sm 2 hours ago | hide | 185 comments</span></li>
<li><span style="color:#000">VSCode's SSH Agent Is Bananas (2025)</span>
<span style="font-size:8pt">(fly.io)</span><br>
<span style="font-size:7pt">133 points by Rapzid 5 hours ago | hide | 86 comments</span></li>
<li><span style="color:#000">ArXiv receives multiyear commitments to support it as an independent nonprofit</span>
<span style="font-size:8pt">(arxiv.org)</span><br>
<span style="font-size:7pt">60 points by JohnHammersley 3 hours ago | hide | 10 comments</span></li>
<li><span style="color:#000">The Twilight of VR (2025)</span>
<span style="font-size:8pt">(example.com)</span><br>
<span style="font-size:7pt">42 points by someone 1 hour ago | hide | 3 comments</span></li>
</ol></body></html>""",

    # real4: unstyled HTML with the same text content and blue links
    "real4_cern_px": """<html><body>
<h1>World Wide Web</h1>
<p>The WorldWideWeb (W3) is a wide-area <a href="#">hypermedia</a> information
retrieval initiative aiming to give universal access to a large universe of
documents.</p>
<p>Everything there is online about W3 is linked directly or indirectly to
this document, including an <a href="#">executive summary</a> of the project,
<a href="#">Mailing lists</a> , <a href="#">Policy</a> , November's
<a href="#">W3 news</a> , <a href="#">Frequently Asked Questions</a> .</p>
<dl>
<dt><a href="#">What's out there?</a></dt>
<dd>Pointers to the world's online information, <a href="#">subjects</a> ,
<a href="#">W3 servers</a> , etc.</dd>
<dt><a href="#">Help</a></dt>
<dd>on the browser you are using</dd>
<dt><a href="#">Software Products</a></dt>
<dd>A list of W3 project components and their current state. (e.g.
<a href="#">Line Mode</a> ,X11 <a href="#">Viola</a> ,
<a href="#">NeXTStep</a> , <a href="#">Servers</a> , <a href="#">Tools</a> ,
<a href="#">Mail robot</a> , <a href="#">Library</a> )</dd>
</dl></body></html>""",

    # real5: white page, red wordmark, bordered input + button
    "real5_ddg_px": """<html><body style="margin:0;background:#fff;font-family:sans-serif">
<h1 style="text-align:center;color:#d43d1f;font-size:24px;margin-top:28px">DuckDuckGo</h1>
<div style="text-align:center;margin-top:18px">
<input style="width:295px;height:24px;border:2px solid #4a80b6;border-radius:4px;padding:0 4px">
<button style="height:32px;margin-left:4px;background:#f2f2f2;border:1px solid #999;border-radius:4px;font-size:14px;padding:0 12px">Search</button>
</div></body></html>""",
}


def probe(html: str, target: str) -> tuple[float, float]:
    out = tempfile.mktemp(suffix=".png")
    render_html_png(html, out)
    return pixel_diff(target, out), ink_fraction(out)


def main() -> None:
    tasks = {t["id"]: t for t in load_tasks(ROOT / "tasks" / "visr_tasks.jsonl")}
    for tid, html in APPROX.items():
        t = tasks[tid]
        target = str(ROOT.parent / t["image"]) if not Path(t["image"]).is_absolute() else t["image"]
        bd, bi = probe(BLANK, target)
        ad, ai = probe(html, target)
        ti = ink_fraction(target)
        print(f"[{tid}] budget={t['pixel']:.0%}  target ink={ti:.1%}")
        print(f"  blank:    diff={bd:.1%} ink={bi:.1%}"
              f"{'  <-- EXPLOITABLE (blank under budget)' if bd <= t['pixel'] else ''}")
        print(f"  mediocre: diff={ad:.1%} ink={ai:.1%} coverage={ai / max(ti, 1e-9):.0%}"
              f"{'  <-- UNPASSABLE (mediocre over budget)' if ad > t['pixel'] else ''}")


if __name__ == "__main__":
    main()
