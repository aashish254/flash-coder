# site/

The landing page for Flash Coder. Vite + React + TypeScript + Tailwind v4.

This directory holds no numbers. That is the whole design.

## Where the figures come from

Nothing on the page is typed by hand. The chain runs one way:

```
python benchmarks/dashboard_data.py --repeats 3   # measures
python benchmarks/export_site_data.py             # converts
```

- `dashboard_data.py` parses the committed §6 battery witness for the 33 printed
  fractions and times `python -m flash.<module> --selftest` three times per module,
  publishing the median with its min and max beside it.
- `export_site_data.py` writes `src/data/benchmarks.json`, `graph.json` and
  `transcripts.json`. It is also the only place that redacts: the checkout path
  becomes `<checkout>`, the home directory becomes `~`, and each captured
  transcript prints the substitutions it carries.

`src/lib/data.ts` reads those three files and nothing else. A figure that is not in
that JSON cannot render, and there is no hand-edited copy of any of them to drift.

The hero's graph is the same rule applied to code rather than to counts:
`export_site_data.py` runs `flash.graph.build(ROOT)` and keeps the 150
most-connected symbols under `flash/`, joined on `calls` and `imports` edges only.
`reads` edges from module-level constants dominate the real graph, and a slice that
kept them is a 2,552-edge hairball rather than a picture.

## Develop and build

```bash
npm install
npm run dev      # http://localhost:5173
npm run build    # static export in dist/, safe under a subpath
```

`vite.config.ts` sets `base: './'` so `dist/` can be served from any path.

## Two things this page got wrong before it got right

**It rendered blank on a machine without a GPU.** three.js throws rather than
degrading when no WebGL context can be made, an uncaught error in any child
unmounts the whole React tree, and the result was an empty `#root`. `src/lib/webgl.ts`
now asks three questions before mounting the canvas — is there a context, is it a
*software* one, and has the reader asked for reduced motion — and on any "no" draws
`GraphFlat.tsx`, the same 150 symbols as an interactive SVG. An error boundary sits
behind the probe for a context lost after mount.

**An earlier version of this page's numbers were invented.** Four dashboard PNGs
with competitor latencies and a cost column that no run had produced were committed
as benchmarks and reverted; the commit pair is in the history, and the page says so
in its own "Panel 3.5 does not exist" box. The generated-data pipeline above is the
control, not the promise.

## Verifying a change here

A screenshot taken with `chrome --headless --screenshot` is not evidence for this
page. That flag captures one viewport inside a virtual-time budget, and a section
whose entrance is an `IntersectionObserver` paints blank — which is indistinguishable
from broken. Drive a real scroll over the DevTools Protocol, wait for the reveal to
run, then capture. Check both render paths: the canvas at a GPU context and the SVG
with `--disable-gpu --disable-software-rasterizer`.
