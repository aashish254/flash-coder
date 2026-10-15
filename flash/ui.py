"""`flash ui` — the same three verbs in one local page.

The terminal is not everyone's interface. This serves one self-contained HTML page
from a loopback-only stdlib server — no npm, no build step, nothing that leaves the
machine — and gives the four things a person actually wants to see a shape for:
the chat, the diff it proposes, the oracle's verdict, and the weights.

Four rules hold:

* **127.0.0.1 only.** The server binds loopback and refuses any other address: this
  page can write files in your project, and a page reachable from a café network
  is not a local tool.
* **Nothing lands without Apply.** Every turn produces a *proposal*; the DIFF panel
  shows the real diff and only a per-file Apply reaches the disk, through
  `flash.patches.land` with the same escape and protected-oracle guards the CLI
  uses.
* **Honest with no model.** With no backend installed the page says so, names the
  remedy, and offers the pull — it never renders an invented answer.
* **One parse path.** A turn's text goes through `flash.patches` here exactly as it
  does in `flash chat`, so a demo run exercises the same diff machinery a real one
  does rather than a prettier copy of it.

The canned backend behind `--demo-backend` exists for one reason: so
`benchmarks/ui_check.py` can drive every panel, including a failed oracle, without a
4.68 GB weight download. It refuses to answer unless that flag marked the run, and
every turn it produces is labelled `DEMO` on the page, so a screenshot of it can
never be mistaken for a measured run.

Run it: `flash ui` → prints `http://127.0.0.1:<port>/`.
"""
from __future__ import annotations

import argparse
import difflib
import json
import sys
import tempfile
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from flash import backend, models

#: The verbs this page exists for; the header prints them so the page explains
#: itself without a README open next to it.
PANELS = ("chat", "diff", "verify", "models")

#: The canned turns, in order, looping. They are written in the model's own output
#: shapes — prose, and `# edit:` patch headers — because the point of the demo is to
#: drive `parse_patches`/`apply_patches`, not to skip them. The third turn is a
#: change that FAILS the oracle on purpose: a UI gate that only ever sees green
#: cannot tell a broken VERIFY panel from an empty one. Turn 2 is the one that
#: PASSES it, so the same gate proves both halves.
DEMO_TURNS = (
    {"prompt": "what does this project do?",
     "text": "Two files: `notifier.py` sends mail through a `send_email` "
             "function, and `test_notifier.py` asserts what it must do. Ask me "
             "to change either."},
    {"prompt": "add a retry with backoff to send_email",
     "text": "I added a retry: `send_email` now tries three times with a "
             "doubling wait.\n"
             "# edit: notifier.py :: send_email\n"
             "```python\n"
             "def send_email(to, body):\n"
             "    for attempt in range(3):\n"
             "        try:\n"
             "            return _smtp.send(to, body)\n"
             "        except OSError as e:\n"
             "            if attempt == 2:\n"
             "                raise\n"
             "            time.sleep(2 ** attempt)\n"
             "```\n"},
    {"prompt": "now make send_email swallow every error and return None",
     "text": "Done — every failure is now ignored.\n"
             "# edit: notifier.py :: send_email\n"
             "```python\n"
             "def send_email(to, body):\n"
             "    try:\n"
             "        return _smtp.send(to, body)\n"
             "    except Exception:\n"
             "        return None\n"
             "```\n"},
)

#: The demo workspace, written to a temporary directory so a demo Apply can never
#: touch a real project. `test_notifier.py` is the oracle the VERIFY panel runs, and
#: demo turn 3 is the change that makes it fail.
DEMO_FILES = {
    "notifier.py": "import time\n"
                   "\n"
                   "import smtp_stub as _smtp\n"
                   "\n"
                   "\n"
                   "def send_email(to, body):\n"
                   "    return _smtp.send(to, body)\n",
    # VERIFY runs the oracle under `python -I`, which puts neither the temp copy
    # nor the cwd on `sys.path`, so the oracle bootstraps to its own directory the
    # way every multi-file oracle in this repo does (see flash.harness). Without
    # the two lines below the panel reports an import error and the demo looks
    # broken for the wrong reason.
    "test_notifier.py": "import sys\n"
                        "sys.path.insert(0, '<TMPDIR>')\n"
                        "\n"
                        "import notifier\n"
                        "\n"
                        "assert notifier.send_email('a@b.c', 'hi') == 'sent'\n"
                        "try:\n"
                        "    notifier.send_email('bad', 'boom')\n"
                        "    raise AssertionError('the failure was swallowed')\n"
                        "except OSError:\n"
                        "    pass\n"
                        "print('ok')\n",
    # The oracle imports a name rather than a module, so the demo proves the VERIFY
    # panel runs the asserts without needing a mail server.
    "smtp_stub.py": "def send(to, body):\n"
                    "    if to == 'bad':\n"
                    "        raise OSError('no route to host')\n"
                    "    return 'sent'\n",
}


class DemoRefused(RuntimeError):
    """The canned backend was reached on a run that did not mark itself a demo."""


def demo_text(n: int, marked: bool) -> str:
    """The n-th canned turn — and it will not answer unless this run is marked.

    `--demo-backend` is the mark. Without it this function raises, because a
    generation path that silently substitutes scripted text is the one failure mode
    a user of this tool cannot afford: an answer that looks like the model's and was
    written by nobody.
    """
    if not marked:
        raise DemoRefused(
            "flash ui: the canned demo backend only runs under `--demo-backend`. "
            "Without that flag this command loads a real model, and it will not "
            "quietly answer with scripted text instead.")
    return DEMO_TURNS[n % len(DEMO_TURNS)]["text"]


# ---------------------------------------------------------------- the session
class Session:
    """One browser against one project directory.

    The proposals live here rather than in the request handler because a turn and
    the Apply that follows it are two requests, and the second has to be able to say
    honestly whether it is writing bytes the first one produced.
    """

    def __init__(self, context: str, test: str = "", demo: bool = False):
        self.context = str(Path(context).resolve())
        self.demo = demo
        self.test_path = test
        self.turns = 0
        #: path -> proposed text, not yet on disk
        self.proposed: dict[str, str] = {}
        #: every turn the page has been shown, so a reload can re-render them
        self.history: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    # -- weights ---------------------------------------------------------
    def load(self):
        """The real model, loaded once per session and kept.

        A page that re-loaded a 4.68 GB weight for every turn would spend the user's
        afternoon on disk I/O, and `flash doctor` already says which library answers.
        """
        if self._model is None:
            from flash import cli
            ref = cli.DEFAULT_MODEL if backend.selected() == "mlx" else "fast"
            self._model, self._tokenizer = backend.load(ref)
        return self._model, self._tokenizer

    def files(self) -> dict[str, str]:
        from flash.patches import workspace_from_dir
        return workspace_from_dir(self.context)

    # -- one turn --------------------------------------------------------
    def run_turn(self, prompt: str) -> dict:
        """Ask, get text, and turn that text into a proposal with the CLI's parser.

        Both arms land in `propose`, which is the whole point: the DIFF panel shows
        what `flash.patches.apply_patches` produced, whether the words came from a
        GGUF or from `DEMO_TURNS`.
        """
        text = demo_text(self.turns, self.demo) if self.demo else self._real_text(prompt)
        workspace, ok, err, n = propose(self.files(), text, self.test_path)
        self.turns += 1
        # Only the files that actually differ are proposals; a turn that re-emitted
        # a file unchanged would otherwise offer an Apply button for nothing.
        before = self.files()
        self.proposed = {k: v for k, v in workspace.items() if before.get(k) != v}
        # The verdict counts what the DIFF panel can show, not what the parser
        # accepted — a line promising a change with no change under it is the
        # sentence this project's first-run rule exists to stop.
        row = {"i": self.turns, "prompt": prompt, "answer": text,
               "verdict": _verdict(ok, len(self.proposed) if ok else n, err,
                                   self.demo),
               "ok": ok, "err": err, "files": sorted(self.proposed)}
        self.history.append(row)
        return row

    def _real_text(self, prompt: str) -> str:
        from flash import loop
        model, tok = self.load()
        task = {"id": f"ui{self.turns}", "prompt": prompt, "test": "",
                "chat": True, "context": self.context, "files": self.files(),
                "edit": True, "multi": True}
        loop.ADAPTER = ""
        # One attempt per turn: the retry-with-feedback chain belongs to a terminal
        # that can show each try, and the second try of a wrong answer is a wrong
        # answer the page would have to explain.
        r = loop.solve_chat(model, tok, task, max_attempts=1)
        return r.attempts[-1].code if r.attempts else ""

    # -- apply / reject --------------------------------------------------
    def apply(self, path: str) -> dict:
        """Write ONE proposed file, through the CLI's own write-back guard."""
        from flash.patches import LandError, land
        if path not in self.proposed:
            return {"ok": False,
                    "err": f"flash ui: `{path}` has no proposal to apply — the "
                           "DIFF panel lists what this session proposed"}
        before = self.files()
        try:
            changed = land(self.context, before, {path: self.proposed[path]},
                           protected=(self.test_path,) if self.test_path else ())
        except LandError as e:
            return {"ok": False, "err": f"flash ui: nothing was written — {e}"}
        self.proposed.pop(path)
        return {"ok": True, "path": path,
                "changed": [list(c) for c in changed]}

    def reject(self, path: str) -> dict:
        if path not in self.proposed:
            return {"ok": False,
                    "err": f"flash ui: `{path}` has no proposal to reject"}
        self.proposed.pop(path)
        return {"ok": True, "path": path}

    # -- verify ----------------------------------------------------------
    def verify(self, test: str = "") -> dict:
        """Run the oracle over the tree AS IT WOULD BE with the proposals in it.

        Grading the disk only would be dishonest in the other direction from a
        proposal that was never tested: the page is asking "if I Apply these, does
        the test pass", and the answer must be about those bytes.
        """
        from flash import harness
        body = test or (self._oracle_text() or "")
        if not body.strip():
            err = ("no oracle: type asserts in the box or start "
                   "`flash ui --test path/to/test_x.py` — nothing was run"
                   if not self.test_path else
                   f"no oracle: `--test {self.test_path}` names a file with nothing "
                   "runnable in it — type asserts in the box or point --test at a "
                   "file that exists")
            return {"ok": False, "ran": False, "err": err}
        files = {**self.files(), **self.proposed}
        s = harness.score_files(files, body)
        return {"ok": s.ok, "ran": True, "passed": s.passed, "total": s.total,
                "err": s.err, "graded": sorted(files)}

    def _oracle_text(self) -> str:
        """The file `--test` named, read from disk.

        Not `self.files()`: that view is capped at twenty files and is the tree the
        patches address, so an oracle outside the cap would leave the panel claiming
        nothing was named. An empty return here is the honest "no oracle" line.
        """
        if not self.test_path:
            return ""
        try:
            return Path(self.context, self.test_path).read_text()
        except OSError:
            return ""

    def state(self) -> dict:
        """Everything the page paints from, in one round trip."""
        profiles = backend.report()
        # `selected()` raises on a box with neither library installed, and a page
        # refresh must not be the thing that hits that — the MODELS panel is where
        # the user learns what is missing. The name is for the header, so ask for
        # `.name` rather than let an object reach JSON as its repr.
        try:
            answering = backend.selected().name
        except backend.NoBackend:
            answering = ""
        return {"demo": self.demo, "context": self.context,
                "backend": answering,
                "available": any(p["available"] for p in profiles),
                "profiles": [{k: v for k, v in p.items() if k != "supports"}
                             for p in profiles],
                "supports": [p.get("supports") for p in profiles],
                "cache": models.status(),
                "files": sorted(self.files()),
                "proposed": {k: _diff(self.files().get(k, ""), v, k)
                             for k, v in self.proposed.items()},
                "history": self.history,
                "test_path": self.test_path or ""}


def _verdict(ok: bool, n: int, err: str, demo: bool) -> str:
    """The one line under a turn: what the turn did, in the user's words."""
    head = "DEMO turn (canned backend, not a model) — " if demo else ""
    if n == 0:
        return head + "answered in prose; this turn changes no files"
    if ok:
        return (head + f"proposes {n} file change(s) — nothing is written until "
                       "you press Apply")
    return head + (err.splitlines()[0] if err else "the turn changed nothing")


def propose(files: dict[str, str], text: str, oracle: str = "") -> tuple[dict, bool, str, int]:
    """Model text -> (workspace, ok, err, n_patches), with the chat loop's own parser.

    Prose with no patch header is an answer, not a failure, and stays green; a
    patch set the parser refuses is said out loud rather than shown as a change.
    """
    from flash.patches import apply_patches, parse_patches
    ps = parse_patches(text)
    if not ps:
        return dict(files), True, "", 0
    res = apply_patches(files, ps, oracle=oracle or "")
    if res.ok and res.applied:
        return res.files, True, "", len(res.applied)
    err = "PATCH REFUSED: " + "; ".join(f"{p.file}:{p.address} — {w}"
                                        for p, w in res.refusals)
    return (res.files if res.ok else dict(files)), bool(res.applied), err, len(ps)


def _diff(before: str, after: str, path: str) -> list[str]:
    """A unified diff with real line numbers, for the DIFF panel to render."""
    return [l.rstrip("\n") for l in difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="")]


# ---------------------------------------------------------------- the jobs
def _job(sess: Session, work) -> str:
    """Run `work` on a thread and hand back an id the page polls.

    A turn is a minute of model time and a pull is an afternoon of bytes. A request
    that holds the socket open for either would look like a hung server, which is
    the exact first-run failure this project keeps having to fix.
    """
    jid = uuid.uuid4().hex[:8]
    job = {"state": "running", "lines": ["started"], "result": None}
    sess.jobs[jid] = job

    def go():
        try:
            job["result"] = work(job)
            job["state"] = "done"
        except (backend.NoBackend, backend.NoFeature) as e:
            # The house refusal: several lines, each a fact, the last one the
            # command. Shown whole rather than as a stack frame.
            job["lines"].extend(str(e).splitlines())
            job["state"] = "failed"
            job["result"] = {"ok": False, "err": str(e)}
        except Exception as e:                              # noqa: BLE001
            job["lines"].append(f"{type(e).__name__}: {e}")
            job["state"] = "failed"
            job["result"] = {"ok": False, "err": f"{type(e).__name__}: {e}"}
    threading.Thread(target=go, daemon=True).start()
    return jid


class _Bar:
    """A write-only stream that keeps the pull's own progress line.

    `flash models` prints its bar with `\\r`; the page can only show what it is sent,
    so the carriage returns become one line each and the latest is what renders.
    Reusing the download rather than reimplementing it is the point.
    """

    def __init__(self, job: dict):
        self.job = job
        self._tail = ""

    def write(self, s: str) -> int:
        for part in s.replace("\r", "\n").split("\n"):
            part = part.strip()
            if part and part != self._tail:
                self._tail = part
                self.job["lines"].append(part)
        return len(s)

    def flush(self) -> None:
        pass


# ---------------------------------------------------------------- the page
#: One self-contained document: no fetch of any asset, no CDN, no build step. Text
#: is inserted with `textContent` everywhere, so a model answer that contains HTML
#: cannot become markup on the page that shows it.
PAGE = """<!doctype html>
<html lang=en><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Flash Coder</title>
<style>
:root{--bg:#12100e;--panel:#1b1815;--ink:#efe9e0;--dim:#9c9284;--line:#2e2a25;
--ok:#7ac74f;--no:#e05c5c;--accent:#e8a33d;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 var(--mono)}
header{display:flex;gap:14px;align-items:baseline;padding:14px 18px;border-bottom:1px solid var(--line)}
header b{font-size:17px;letter-spacing:.04em}
header span{color:var(--dim);font-size:13px}
.badge{background:var(--accent);color:#1b1815;padding:1px 7px;border-radius:3px;font-size:12px;font-weight:700}
main{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line)}
section{background:var(--panel);padding:14px 16px;min-height:46vh}
h2{margin:0 0 10px;font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--dim)}
p,pre{margin:0 0 8px;white-space:pre-wrap;word-break:break-word}
pre{font:13px/1.45 var(--mono);color:var(--ink)}
input,textarea,button{font:inherit;background:#0f0d0b;color:var(--ink);border:1px solid var(--line);border-radius:3px;padding:6px 8px}
textarea{width:100%;min-height:74px;resize:vertical}
button{cursor:pointer;background:var(--accent);color:#1b1815;border:0;font-weight:700}
button.ghost{background:transparent;color:var(--dim);border:1px solid var(--line);font-weight:400}
button:disabled{opacity:.4;cursor:not-allowed}
.turn{border-top:1px solid var(--line);padding-top:8px;margin-top:8px}
.verdict{color:var(--accent);font-size:13px}
.file{border:1px solid var(--line);border-radius:3px;padding:10px;margin-bottom:10px}
.file h3{margin:0 0 8px;font-size:14px}
.add{color:var(--ok)}.del{color:var(--no)}.hunk{color:var(--dim)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.muted{color:var(--dim);font-size:13px}
@media(max-width:900px){main{grid-template-columns:1fr}}
</style>
<header>
  <b>Flash Coder</b><span id=state>reading this machine&hellip;</span>
  <span id=demo style="display:none"><span class=badge>DEMO</span> canned backend &mdash; no model ran</span>
  <span id=note class=muted></span>
</header>
<main>
<section><h2>Chat</h2>
  <div class=row><input id=ask size=44 placeholder="ask about this project">
  <button id=send>Ask</button></div>
  <p class=muted id=chatnote>Nothing is written to your project by a turn &mdash; the Diff panel decides that.</p>
  <div id=turns></div>
</section>
<section><h2>Diff</h2>
  <p class=muted>Every proposed change, per file. Apply writes that one file; Reject drops the proposal.</p>
  <div id=diffs></div>
</section>
<section><h2>Verify</h2>
  <div class=row><textarea id=test placeholder="asserts to run against the tree, or name one with --test"></textarea></div>
  <div class=row><button id=runverify>Run the oracle</button><span class=muted>runs the proposals too, so you see the verdict before you Apply</span></div>
  <pre id=verify></pre>
</section>
<section><h2>Models</h2>
  <div id=models></div>
</section>
</main>
<script>
let S={}, jobs={};
const el=id=>document.getElementById(id);
function put(node,text,cls){const p=document.createElement(cls||'pre');p.textContent=text;node.appendChild(p);return p;}
async function refresh(){S=await(await fetch('/api/state')).json();paint();}
function paint(){
  el('state').textContent=[(S.available?'ready':'no model on this machine'),S.backend,S.context].filter(Boolean).join(' · ');
  el('demo').style.display=S.demo?'':'none';
  if(!S.available&&!S.demo){el('chatnote').textContent='No model library answers here: '+
    (S.profiles.map(p=>p.why).filter(Boolean)[0]||'see Models')+' — pull a weight in the Models panel, or run `flash setup` in a terminal.';}
  const t=el('turns');t.innerHTML='';
  S.history.forEach(r=>{const d=document.createElement('div');d.className='turn';
    put(d,'you › '+r.prompt);put(d,'flash › '+r.answer);
    put(d,r.verdict).className='verdict';t.appendChild(d);});
  const f=el('diffs');f.innerHTML='';
  const paths=Object.keys(S.proposed);
  if(!paths.length)put(f,'no proposals yet','p').className='muted';
  paths.forEach(p=>{const box=document.createElement('div');box.className='file';
    const h=document.createElement('h3');h.textContent=p;box.appendChild(h);
    const body=document.createElement('div');box.appendChild(body);
    S.proposed[p].forEach(l=>{const c=document.createElement('div');c.textContent=l;
      c.className=l.startsWith('+')?'add':l.startsWith('-')?'del':l.startsWith('@')?'hunk':'muted';body.appendChild(c);});
    const row=document.createElement('div');row.className='row';
    const a=document.createElement('button');a.textContent='Apply';a.onclick=()=>post('/api/apply',{path:p});
    const r=document.createElement('button');r.textContent='Reject';r.className='ghost';r.onclick=()=>post('/api/reject',{path:p});
    row.append(a,r);box.appendChild(row);f.appendChild(box);});
  const m=el('models');m.innerHTML='';
  (S.profiles||[]).forEach(p=>{const line=(p.available?'ready  ':'absent ')+p.backend+'  '+(p.library||p.why||'');
    put(m,line,'p').className=p.available?'':'muted';});
  const c=S.cache||{};put(m,'weights: '+c.bytes_on_disk+' bytes in '+(c.dir||'?')+' · '+c.pulled_gb+' GB pulled · '+(c.free_bytes||0)+' bytes free','p').className='muted';
  (c.tiers||[]).forEach(t=>{const row=document.createElement('div');row.className='row';
    put(row,t.tier+': '+(t.on_disk?'on disk':'not pulled')+(t.resumable?' (part '+t.part_bytes+' bytes)':''),'p');
    if(!t.on_disk){const b=document.createElement('button');b.textContent='Pull '+t.tier+' ('+Math.round(t.size/1e9)+' GB)';
      b.onclick=()=>post('/api/pull',{tier:t.tier});row.appendChild(b);}m.appendChild(row);});
  if(!S.available&&!S.demo)put(m,'nothing can answer yet: install a model library (`pip install flash-coder[local]` for llama.cpp) or run `flash setup`','p').className='muted';
}
async function post(u,body){const r=await fetch(u,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body||{})});
  const j=await r.json();if(j.job)watch(j.job);el('note').textContent=j.ok?'':(j.err||'');refresh();return j;}
function watch(id){jobs[id]=true;poll(id);}
async function poll(id){const j=await(await fetch('/api/job?id='+id)).json();
  if(j.state==='running'){setTimeout(()=>poll(id),400);return;}
  delete jobs[id];if(j.result&&j.result.text)el('verify').textContent=j.result.text;refresh();}
el('send').onclick=async()=>{const q=el('ask').value.trim();if(!q)return;el('ask').value='';
  el('send').disabled=true;el('note').textContent='thinking — a turn is a minute of model time';
  await post('/api/turn',{prompt:q});el('send').disabled=false;el('note').textContent='';};
el('runverify').onclick=async()=>{const j=await post('/api/verify',{test:el('test').value});
  el('verify').textContent=j.text||(j.err||'');};
refresh();
</script>
"""


# ---------------------------------------------------------------- the server
class Handler(BaseHTTPRequestHandler):
    """Nine routes, all JSON but the page. No cookies, no auth, no cache."""

    server_version = "flash-ui"
    session: Session                                  # set on the subclass below

    def do_GET(self) -> None:                         # noqa: N802
        if self.path.startswith("/api/job"):
            jid = (parse_qs(self.path.partition("?")[2]).get("id") or [""])[0]
            return self._json(self.session.jobs.get(jid) or
                              {"state": "failed", "lines": ["no such job"],
                               "result": None})
        if self.path.startswith("/api/state"):
            return self._json(self.session.state())
        if self.path in ("/", "/index.html"):
            return self._bytes(PAGE.encode(), "text/html; charset=utf-8")
        self._json({"ok": False, "err": f"flash ui: no route {self.path}"}, 404)

    def do_POST(self) -> None:                        # noqa: N802
        body = self._body()
        if self.path == "/api/turn":
            if not body.get("prompt", "").strip():
                return self._json({"ok": False, "err": "flash ui: an empty ask"})
            row = self.session.run_turn(body["prompt"].strip())
            self._log(f"turn {row['i']}: {row['verdict']}")
            return self._json({"ok": row["ok"], "turn": row,
                               "err": "" if row["ok"] else row["err"]})
        if self.path in ("/api/apply", "/api/reject"):
            r = (self.session.apply(body.get("path", ""))
                 if self.path.endswith("apply")
                 else self.session.reject(body.get("path", "")))
            if self.path.endswith("apply") and r["ok"]:
                self._log(f"applied {r['path']}")
            return self._json(r)
        if self.path == "/api/verify":
            v = self.session.verify(body.get("test", ""))
            # `ran` is the difference between an oracle that said no and an oracle
            # that was never asked: the second must not print a FAIL.
            v["text"] = v["err"] if not v["ran"] else (
                ("PASS " if v["ok"] else "FAIL ")
                + (f"{v['passed']}/{v['total']} asserts reached\n"
                   if v.get("total") else "")
                + ("" if v["ok"] else "\n" + v["err"]))
            return self._json(v)
        if self.path == "/api/pull":
            tier = body.get("tier", "")
            if tier not in models.TIERS:
                return self._json({"ok": False,
                                   "err": f"flash ui: no tier named `{tier}` is "
                                          f"pinned. Pinned: {', '.join(models.TIERS)}"})
            jid = _job(self.session, lambda job, t=tier: self._pull(job, t))
            return self._json({"ok": True, "job": jid})
        self._json({"ok": False, "err": f"flash ui: no route {self.path}"}, 404)

    def _pull(self, job, tier: str) -> dict:
        """The download, on `flash models`'s own code path, bar lines into the job."""
        t = models.tier(tier)
        rc = models.fetch(t, yes=True, progress=True, out=_Bar(job))
        ok, line = models.verify(tier)
        return {"ok": rc == 0 and ok, "rc": rc, "text": line, "verified": ok}

    # -- plumbing --------------------------------------------------------
    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:                                 # one page, one box each
            raise ValueError("flash ui: request body too large")
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw or b"{}")

    def _json(self, obj, code: int = 200) -> None:
        self._bytes(json.dumps(obj, default=str).encode(), "application/json", code)

    def _bytes(self, data: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        # This page can write files in the project it is pointed at, so it is served
        # as its own origin and nothing else may frame or script it.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; script-src 'unsafe-inline'; "
                         "style-src 'unsafe-inline'; connect-src 'self'; "
                         "img-src 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def _log(self, line: str) -> None:
        print(f"flash ui: {line}", file=sys.stderr, flush=True)

    def log_message(self, fmt: str, *a) -> None:
        pass                           # the two lines above are the whole record


def make_server(context: str = ".", port: int = 0, test: str = "",
                demo: bool = False, host: str = "127.0.0.1") -> tuple[ThreadingHTTPServer, Session]:
    """The server and its session, not started.

    `host` is not a free parameter: anything but loopback is refused here, at the
    one place a bind can happen, rather than by a docstring asking.
    """
    if host != "127.0.0.1":
        raise ValueError("flash ui: this server can write to your project, so it "
                         "only binds 127.0.0.1. Put a real reverse proxy in front "
                         "if you meant to share it.")
    sess = Session(demo_workspace() if demo else context, test=test, demo=demo)
    handler = type("Bound", (Handler,), {"session": sess})
    return ThreadingHTTPServer((host, port), handler), sess


def demo_workspace() -> str:
    """A temporary project for `--demo-backend`, so no demo Apply can reach a repo.

    The temp directory is the mark's other half: a run that carries canned text must
    also carry canned bytes, or the gate's proof and the user's files share a disk.
    """
    d = Path(tempfile.mkdtemp(prefix="flash-ui-demo-"))
    for rel, text in DEMO_FILES.items():
        (d / rel).write_text(text, encoding="utf-8")
    return str(d)


def add_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--context", default=".",
                   help="the project the page reads and may write "
                        "(default: this folder)")
    p.add_argument("--test", default="",
                   help="an oracle file the VERIFY panel pre-loads and protects "
                        "from edits")
    p.add_argument("--port", type=int, default=0,
                   help="the port to listen on (default: any free one)")
    p.add_argument("--demo-backend", action="store_true",
                   help="answer from a canned script and edit a throwaway project "
                        "instead of a real one — for `benchmarks/ui_check.py` and "
                        "for seeing the page before any weight is downloaded")


def dispatch(a) -> int:
    """Serve until Ctrl-C. The URL is printed before the first request is possible."""
    if a.demo_backend and a.context != ".":
        # A demo that points at a real tree could still write to it, and the one
        # promise `--demo-backend` makes is that it cannot.
        print("flash ui: --demo-backend edits its own throwaway project, so "
              "--context is ignored on that run")
    srv, sess = make_server(a.context, a.port, a.test, a.demo_backend)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"flash ui — {sess.turns} turns, four panels, nothing leaves this machine")
    print(f"  open {url}")
    print(f"  project: {sess.context}")
    print("  Ctrl-C stops it")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nflash ui: stopped", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(prog="flash ui",
                                 description=__doc__.split("\n")[0])
    add_flags(ap)
    return dispatch(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
