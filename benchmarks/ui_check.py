"""R-7.20, OFFLINE: does `flash ui` do the four things the four panels claim?

The UI is the product surface for anybody who would rather read a diff than
squint at a terminal, and a page is easy to demo and hard to gate: the HTML can
say "APPLIED" while nothing touched the disk, or say "FAIL" for an oracle that
was never run. So this vector does what a user does — it starts the server, drives
the HTTP API with the demo backend, and reads the files on disk afterwards:

- **one chat turn, end to end**: the prompt is echoed, the answer is the canned
  text, and the verdict line under it says what the turn did in the user's words.
- **nothing lands without Apply**: after a turn that proposes a change, every byte
  of the project is where it was. The page's central promise is that the DIFF
  panel decides, not the CHAT panel, and that is checked against `stat` and
  `read_bytes`, not against a badge.
- **one Apply**: the file on disk becomes EXACTLY the text that was proposed, the
  line counts it reports are the counts the DIFF panel rendered, the proposal is
  gone from the panel, and the oracle file the run was started with comes back
  byte-identical because `patches.land` protected it.
- **one Reject**: the proposal disappears and the disk does not move.
- **one failed oracle, shown honestly**: the demo's third turn swallows every
  error, and VERIFY prints the oracle's own words about it —
  `the failure was swallowed`. A UI gate that only ever sees green cannot tell a
  broken VERIFY panel from an empty one, so the same file also requires the
  opposite: an oracle that was never handed anything must NOT print a FAIL.
- **VERIFY grades the proposal, not the disk**: the oracle for that arm asserts
  something only the patched file contains, so a panel that read the disk would
  report it as a failure of the user's code.
- **the MODELS panel pulls with the real bar**: a job's lines must carry the
  per-chunk progress line (`MB/s` is only ever in `_stream`'s bar, never in the
  plan `fetch` prints), and the page's own JavaScript must paint the lines of a
  RUNNING job — a panel that shows the plan and then goes quiet is a screenshot of
  a download, not a download.
- **a turn with no backend answers**: on a session that is NOT the demo, with the
  backend seam made to refuse, the ask still comes back over HTTP as a finished job
  carrying the shipping `flash: this install has no generation backend` sentence.
  The turn runs on a job thread, so `NoBackend` cannot die in the request handler and
  leave the browser staring at a closed socket — which is the difference between a
  user running `pip install 'flash-coder[local]'` and a user refreshing a dead page.
- **the two backends are asked for different things**: `backend.selected()` hands
  back a Backend object, and MLX wants a repo id while llama.cpp wants a tier word.
  Both are proven against a stub `selected()`/`load`, so a `== "mlx"` string
  comparison — which is always false, and was in this file's first draft — cannot
  come back.

NOTHING HERE LOADS A MODEL and nothing downloads a real weight: `--demo-backend`
exists so this is possible, and the two arms that could spend bytes (`/api/pull`)
point `flash models` at the same closed loopback port and the same 1 MiB fixture
`benchmarks/models_pull_check.py` uses, then at a port with nothing behind it. The
server binds 127.0.0.1 on port 0 in this process, plus one child process running
the real `flash ui --demo-backend` so the command itself — not just the object
behind it — is proven to start and to stop. At the end every arm proves its port is
FREE again: a gate that leaves a listener behind is the "the port is still in use"
first-run bug this repo keeps having to fix.

    python benchmarks/ui_check.py            # gates + mutants
    python benchmarks/ui_check.py --mutant   # mutants only
    python benchmarks/ui_check.py --mutant apply_writes_without_the_guard
"""
from __future__ import annotations

import importlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from flash import backend, cli, models, ui                         # noqa: E402
from models_pull_check import PAYLOAD, pinned, serve               # noqa: E402

PY = sys.executable
CHECKS: list[tuple[str, bool, str]] = []

#: An oracle only the PROPOSAL can satisfy: `attempt` is the retry loop's variable,
#: which exists in turn 2's patched `notifier.py` and nowhere in the file on disk.
#: This is what makes "VERIFY grades the tree as it would be" a check instead of a
#: claim — an unpatched disk fails it, so a PASS proves the proposal was graded.
#: The two bootstrap lines are the shape every multi-file oracle in this repo has:
#: the harness runs `python -I`, which puts neither the temp copy nor the cwd on
#: `sys.path` (see flash/harness.py), so without them this arm would fail for the
#: wrong reason.
PROPOSAL_ORACLE = ("import sys\n"
                   "sys.path.insert(0, '<TMPDIR>')\n"
                   "\n"
                   "import notifier\n"
                   "\n"
                   "assert 'attempt' in open('notifier.py').read()\n"
                   "assert notifier.send_email('a@b.c', 'hi') == 'sent'\n")


def ck(label: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((label, bool(ok), detail))


# ------------------------------------------------------------------ the hosts
def serve_dead() -> str:
    """A loopback URL with nothing listening behind it.

    Bound on port 0 and closed again, so the port is one the OS just handed out and
    is very unlikely to have been taken by something else in the meantime. This is
    how the "the host cannot be reached" sentence is produced on a metered line
    without asking a network for anything.
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def _closed(port: int) -> bool:
    """The port is free: nothing answers it, and a socket can bind it.

    Both halves are needed. A connection attempt alone says the listener is gone
    now; the bind says the address is actually available for the next `flash ui`,
    which is the thing a user retries after a Ctrl-C. `SO_REUSEADDR` is set so a
    TIME_WAIT from this gate's own closed connections is not mistaken for a live
    server — on macOS a socket that is genuinely LISTENing still refuses the bind.
    """
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        s.close()
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
        return False
    except Exception:                                     # noqa: BLE001
        return True


# ------------------------------------------------------------------ the driver
class Api:
    """One server, one demo project, and the four buttons."""

    def __init__(self, tmp: Path, test: str = "test_notifier.py",
                 demo: bool = True):
        self.srv, self.sess = ui.make_server(str(tmp), 0, test, demo)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(self.base + path, timeout=60) as r:
            return r.status, json.loads(r.read())

    def page(self) -> tuple[int, str, dict]:
        with urllib.request.urlopen(self.base + "/", timeout=60) as r:
            return r.status, r.read().decode(), dict(r.headers)

    def post(self, route: str, **body) -> dict:
        """`route` is the URL path; everything else is the JSON body.

        The body has a `path` key of its own (`/api/apply` names the file), so the
        two are not allowed to collide — a driver whose parameter shadows a payload
        field is how a gate ends up applying its own route.
        """
        req = urllib.request.Request(
            self.base + route, data=json.dumps(body).encode(),
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())

    def disk(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(Path(self.sess.context).iterdir())
                if p.is_file()}

    def wait(self, jid: str, secs: float = 120.0) -> dict:
        """Poll a job the way the page does, until it is no longer running."""
        t0 = time.time()
        while time.time() - t0 < secs:
            _, j = self.get("/api/job?id=" + jid)
            if j.get("state") != "running":
                return j
            time.sleep(0.1)
        return {"state": "timeout", "kind": "?", "lines": ["the job never finished"],
                "result": {"ok": False, "err": "the job never finished"}}

    def turn_job(self, prompt: str) -> dict:
        """Ask and return the FINISHED job — the refusal arms need the job, not
        the turn, because a turn that dies on a missing backend is the whole
        design question."""
        return self.wait(self.post("/api/turn", prompt=prompt)["job"])

    def turn(self, prompt: str) -> dict:
        """Ask and return the turn row, which is what the page paints from."""
        job = self.turn_job(prompt)
        return job.get("result") or {"i": 0, "ok": False, "answer": "",
                                     "verdict": "", "files": [], "prompt": prompt,
                                     "err": "the turn produced no result: "
                                            + " / ".join(job.get("lines", []))}

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        # A demo run's project is a temp directory `demo_workspace()` made. It is
        # this gate's litter rather than the user's, so it leaves with the server:
        # twelve mutant runs would otherwise leave twelve throwaway trees on disk.
        if self.sess.demo and "flash-ui-demo-" in self.sess.context:
            shutil.rmtree(self.sess.context, ignore_errors=True)


# ------------------------------------------------------------------ the arms
def arms_page(a: Api) -> None:
    code, page, headers = a.page()
    ck("GET / is one self-contained document with all FOUR panels on it — chat, "
       "diff, verify, models — because the product is the page, and a page missing "
       "a panel is a feature that does not exist",
       code == 200 and all(f"<h2>{n}</h2>" in page
                           for n in ("Chat", "Diff", "Verify", "Models")),
       f"code={code} bytes={len(page)}")
    ck("and it fetches NOTHING off another machine: no src= or href= to a host, no "
       "CDN, no font, no build step — the page is served from 127.0.0.1 and must "
       "keep working with the network off, which is the whole distribution promise",
       'src="http' not in page and 'href="http' not in page
       and "cdn." not in page and "<link" not in page, page[:160])
    csp = headers.get("Content-Security-Policy") or ""
    ck("the response carries a CSP that frames nothing and connects nowhere but "
       "itself — this page can write files in the project it points at, so a form "
       "or an iframe in it is not a cosmetic detail",
       "frame-ancestors 'none'" in csp and "connect-src 'self'" in csp
       and "default-src 'none'" in csp, csp)
    ck("the running job has somewhere on the page to be painted, and the poll loop "
       "writes its lines BEFORE it decides whether the job is finished — a MODELS "
       "panel that only renders at the end shows nothing during the one wait that "
       "lasts an afternoon, which is the difference between progress and a spinner",
       "id=job" in page
       and -1 < page.index("el('job').textContent=(j.lines||[]).slice(-12)")
       < page.index("if(j.state==='running')"),
       page[page.index("function poll"):][:160])
    ck("a finished job's sentence is placed by KIND, and the VERIFY panel is written "
       "only by the Run-the-oracle button: `fast: … hashes to the pinned SHA256` is "
       "a fact about a weight, and the same line landing in the panel that grades "
       "the user's asserts would read as a verdict about their code",
       "j.kind==='turn'" in page
       and "el('verify').textContent=j.result.text" not in page
       and "el('verify').textContent=j.text" in page, page[:200])


def arms_turns(a: Api) -> None:
    before = a.disk()
    t1 = a.turn("what does this project do?")
    ck("one chat turn, end to end: the prompt comes back echoed exactly as it was "
       "typed, the answer is the text the backend produced, and `ok` is true — the "
       "three things the transcript needs to paint a turn",
       t1["i"] == 1 and t1["ok"] and "Two files" in t1["answer"]
       and t1["prompt"] == "what does this project do?", str(t1)[:200])
    st = a.get("/api/state")[1]
    ck("and the history is in the state, in order, with the prompt the user typed "
       "next to the answer they got, so a reload re-paints the transcript instead of "
       "starting over",
       len(st["history"]) == 1 and st["history"][0]["prompt"]
       == "what does this project do?", str(st["history"])[:200])
    ck("a prose turn says so, in the user's words, and proposes nothing: "
       "`changes no files` is the sentence that tells a stranger this turn is safe",
       t1["files"] == [] and "changes no files" in t1["verdict"], t1["verdict"])
    ck("the DEMO badge is in the verdict line itself, not only in a corner of the "
       "page — a canned answer has to be identifiable as one from the transcript, "
       "years later, with the header scrolled away",
       "DEMO" in t1["verdict"] and "not a model" in t1["verdict"], t1["verdict"])

    t2 = a.turn("add a retry with backoff to send_email")
    st = a.get("/api/state")[1]
    diff = st["proposed"].get("notifier.py") or []
    ck("the second turn's patch becomes a proposal with a real unified diff — "
       "a/ and b/ headers, an @@ hunk, added and removed lines — because the DIFF "
       "panel shows what `flash.patches` produced, not what a UI layer formatted",
       t2["files"] == ["notifier.py"] and any(l.startswith("--- a/") for l in diff)
       and any(l.startswith("@@") for l in diff)
       and any(l.startswith("+") for l in diff), str(diff)[:220])
    ck("and NOTHING HAS BEEN WRITTEN: every byte of the project is identical to "
       "what it was before the turn. This is the promise the whole page is built on "
       "— the chat proposes, the diff decides",
       a.disk() == before, str([k for k, v in a.disk().items()
                                if before.get(k) != v])[:200])
    ck("the verdict line under that turn says a change is PROPOSED and says what "
       "would make it real, so the Apply button is not a mystery",
       "proposes 1 file change(s)" in t2["verdict"] and "Apply" in t2["verdict"],
       t2["verdict"])


def arms_verify(a: Api) -> None:
    v = a.post("/api/verify", test=PROPOSAL_ORACLE)
    ck("VERIFY grades the tree AS IT WOULD BE with the proposal in it: the oracle "
       "asserts something only the patched file contains, so this PASS is proof the "
       "panel is not reading the disk — and a user deciding whether to press Apply "
       "is asking about bytes that are not there yet",
       v["ok"] and v["ran"] and v["text"].startswith("PASS"), str(v)[:260])
    ck("the pass line carries the oracle's own count, not a bare OK, so the reader "
       "can see how much was actually reached",
       v.get("total", 0) >= 1 and f"{v['passed']}/{v['total']}" in v["text"],
       v["text"][:160])

    t3 = a.turn("now make send_email swallow every error")
    v = a.post("/api/verify", test="")
    ck("one failed oracle, shown honestly: the swallow-everything turn makes the "
       "project's own test fail, and the panel prints FAIL plus the AssertionError "
       "the oracle raised — its words, not a paraphrase",
       t3["files"] == ["notifier.py"] and not v["ok"] and v["ran"]
       and v["text"].startswith("FAIL") and "the failure was swallowed" in v["text"],
       v["text"][-260:])
    ck("and the failure did not touch the disk either: a red VERIFY is information, "
       "not a rollback or a write",
       "the failure was swallowed" not in a.disk()["notifier.py"].decode(), "")


def arms_apply_reject(a: Api) -> None:
    # The canned turns cycle modulo 3 and this Api has already spent three of them,
    # so the loop is WALKED to the patch rather than assumed: one prose turn, then the
    # retry. An arm that indexed a proposal straight after a prose turn is a KeyError
    # in a gate rather than a finding about the page.
    a.turn("what does this project do?")               # index 0: prose
    a.turn("add a retry")                              # index 1: the patch
    if "notifier.py" not in a.sess.proposed:
        ck("the demo loop hands this arm a proposal to apply", False,
           f"proposed={list(a.sess.proposed)} after {a.sess.turns} turns")
        return
    before = a.disk()
    want = a.sess.proposed["notifier.py"]
    shown = a.get("/api/state")[1]["proposed"]["notifier.py"]
    added = sum(1 for l in shown if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in shown if l.startswith("-") and not l.startswith("---"))
    r = a.post("/api/apply", path="notifier.py")
    after = a.disk()
    ck("one Apply: the file on disk is now EXACTLY the text that was proposed — "
       "compared as bytes against what the session held, because the page saying "
       "`applied` and the disk holding something else is the bug this panel exists "
       "to prevent",
       r["ok"] and after["notifier.py"] == want.encode() == Path(
           f"{a.sess.context}/notifier.py").read_bytes(), str(r)[:200])
    ck(f"and Apply reports the line counts it changed — here {added} added, "
       f"{removed} removed — which are the counts the DIFF panel just rendered, so "
       "the number next to the button and the number in the answer come from one "
       "diff and not from two places that can drift",
       r["changed"] == [["notifier.py", added, removed]] and added > 0
       and removed > 0, str(r["changed"]))
    ck("the oracle file the run was pointed at is BYTE-IDENTICAL after the Apply: "
       "`--test` protects it, and a page that let a model edit the thing scoring it "
       "would make every later PASS meaningless",
       after["test_notifier.py"] == before["test_notifier.py"], "")
    st = a.get("/api/state")[1]
    ck("and the proposal leaves the DIFF panel once it has landed, so a file cannot "
       "be Applied twice",
       "notifier.py" not in st["proposed"], str(list(st["proposed"]))[:120])

    a.turn("swallow every error")                      # index 2: the bad patch
    r = a.post("/api/reject", path="notifier.py")
    ck("one Reject: the proposal is dropped, the answer stays on screen, and the "
       "disk does not move — Reject is a decision, not an edit",
       r["ok"] and "notifier.py" not in a.get("/api/state")[1]["proposed"]
       and a.disk()["notifier.py"] == after["notifier.py"], str(r)[:160])

    r = a.post("/api/apply", path="smtp_stub.py")
    ck("an Apply for a file nobody proposed is refused with a sentence naming the "
       "panel that lists what was, rather than writing something or throwing",
       not r["ok"] and "no proposal to apply" in r["err"], str(r)[:180])
    r = a.post("/api/reject", path="smtp_stub.py")
    ck("...and so is a Reject for one: a page that answers OK to dropping a "
       "proposal that never existed teaches the user that the buttons are theatre",
       not r["ok"] and "no proposal to reject" in r["err"], str(r)[:180])


def arms_honest_no_oracle(a: Api) -> None:
    v = a.post("/api/verify", test="")
    ck("VERIFY with nothing to run says NOTHING WAS RUN and does not print a FAIL: "
       "an oracle that was never handed is not a verdict about the user's code",
       not v["ran"] and not v["ok"] and not v["text"].startswith("FAIL")
       and "--test" in v["text"], str(v)[:240])


def arms_refusals(a: Api) -> None:
    r = a.post("/api/turn", prompt="   ")
    ck("an empty ask is refused with four words, not a turn against the model that "
       "costs the user a minute and prints nothing",
       not r["ok"] and "empty ask" in r["err"], str(r)[:160])
    try:
        code, body = a.get("/api/nope")
    except urllib.error.HTTPError as e:
        code, body = e.code, json.loads(e.read())
    ck("an unknown route answers 404 with a sentence naming the route, and no "
       "traceback anywhere in the body — `flash ui` is a product surface too",
       code == 404 and "no route" in body.get("err", "")
       and "Traceback" not in json.dumps(body), str(body)[:180])
    r = a.post("/api/pull", tier="brainn")
    ck("a tier word that is not pinned is refused with the words that ARE, before "
       "any request is made — the same rule `flash models pull` follows",
       not r["ok"] and "fast" in r["err"] and "brain" in r["err"], str(r)[:200])


def arms_turn_refusal(tmp: Path) -> None:
    """A turn against a box with no backend: the sentence has to ARRIVE.

    This is the one thing the demo cannot express — `--demo-backend` exists exactly
    so a turn never reaches a library — so the seam is made to refuse here, on a
    NON-demo session, and the message is the shipping `backend.message()` text rather
    than one written for this file. Nothing loads: the refusal happens before a
    weight is opened, so this arm costs no seconds and no bytes.
    """
    proj = tmp / "no-backend"
    proj.mkdir(parents=True, exist_ok=True)
    for rel, text in ui.DEMO_FILES.items():
        (proj / rel).write_text(text, encoding="utf-8")
    why = backend.reason(ImportError("No module named 'llama_cpp'"), "llama_cpp")
    real = backend.selected

    def boom():
        raise backend.NoBackend(backend.message(why))

    backend.selected = boom
    c = None
    try:
        c = Api(proj, test="", demo=False)
        job = c.turn_job("add a retry")
        body = json.dumps(job)
        ck("a turn on a box with no backend comes back as a FINISHED job carrying "
           "the whole refusal, over a normal response: the turn runs on the job "
           "thread, so `NoBackend` is caught where a request handler would have died "
           "in the socket — the difference between `pip install 'flash-coder[local]'` "
           "and a browser spinner over a dead connection",
           job["state"] == "failed" and job["kind"] == "turn"
           and job["result"]["ok"] is False
           and "flash: this install has no generation backend"
           in job["result"]["err"]
           and "remedy" in job["result"]["err"] and "Traceback" not in body,
           body[:260])
        ck("and the dead turn leaves no half-turn in the transcript: the session's "
           "history is still empty, so a user who installs the library and asks "
           "again sees one answer rather than a dead prompt above it",
           c.sess.history == [], str(c.sess.history)[:160])
    except (OSError, ValueError) as e:
        # A request that never got an answer lands here. That IS the finding: the
        # turn was served on the handler's thread and the exception took the socket.
        ck("a turn on a box with no backend answers over HTTP instead of killing "
           "the connection", False, f"{type(e).__name__}: {e}")
        ck("…and the transcript stayed empty", False, "the request never returned")
    finally:
        backend.selected = real
        if c is not None:
            c.close()


def arms_pull_job(a: Api, tmp: Path) -> None:
    """The MODELS panel's pull, on the closed loopback host: the job machinery, not
    a real weight."""
    t = models.TIERS["fast"]
    jid = a.post("/api/pull", tier="fast")["job"]
    job = a.wait(jid)
    lines = "\n".join(job["lines"])
    ck("a pull started from the page runs on `flash models`'s own code and streams "
       "its plan into the job the page polls — one download implementation, so the "
       "UI cannot be a prettier copy of the thing that is gated elsewhere",
       job["state"] == "done" and t.filename in lines and "sha256" in lines
       and job["result"]["ok"] is True, lines[:260])
    ck("and the job carries the PROGRESS BAR, not only the plan: `MB/s` is written "
       "by `models._stream` and by nothing else in this repo, and the fixture is "
       "bigger than one CHUNK, so two lines with a percentage on them mean the same "
       "`\\r` bar `flash setup` prints reached the panel byte for byte",
       "MB/s" in lines and sum(1 for l in job["lines"] if "%" in l) >= 2,
       "\n".join([l for l in job["lines"] if "%" in l][:3])[:200])
    ck("the bytes that landed are the fixture whole, and the tier is registered to "
       "them — a page that reports success about a file it did not verify would be "
       "the same lie in HTML",
       (tmp / "cache" / t.filename).read_bytes() == PAYLOAD
       and "hashes to the pinned SHA256" in job["result"]["text"],
       job["result"]["text"][:200])

    # Now the same machinery against a host with nothing behind it.
    dead = serve_dead()
    with pinned(tmp / "cache-dead", "range", dead):
        jid = a.post("/api/pull", tier="fast")["job"]
        job = a.wait(jid)
        lines = "\n".join(job["lines"])
        ck("and when the host cannot be reached the job FINISHES rather than "
           "spinning, with the sentence the terminal would have printed and "
           "`result.ok` false — a spinner that never stops is how a first run "
           "becomes a hung install",
           job["state"] == "done" and job["result"]["ok"] is False
           and "cannot reach" in lines, lines[-260:])


def arms_marks(tmp: Path) -> None:
    srv = None
    try:
        srv, _ = ui.make_server(str(tmp), 0, "", False, host="0.0.0.0")
        refused = ""
    except ValueError as e:
        refused = str(e)
    finally:
        # Only under the mutant that drops the guard does a server exist here, and
        # it must not survive the arm that proves the guard was dropped.
        if srv is not None:
            srv.server_close()
    ck("a request to bind anything but 127.0.0.1 is refused at the one place a bind "
       "can happen, with the reason (`it can write to your project`) and what to do "
       "instead — a flag in the help text is not a guarantee",
       "127.0.0.1" in refused and "write" in refused, refused[:200])

    try:
        ui.demo_text(0, marked=False)
        msg = ""
    except ui.DemoRefused as e:
        msg = str(e)
    ck("the canned backend REFUSES to answer a run that did not mark itself a demo, "
       "and the refusal names `--demo-backend`: an answer that looks like the "
       "model's and was written by nobody is the one failure mode a user of this "
       "tool cannot afford",
       "--demo-backend" in msg and "scripted text" in msg, msg[:200])

    # The demo and the real turn must go through ONE parse path, or the gate proves
    # a copy of the diff machinery rather than the machinery.
    proj = tmp / "live"
    proj.mkdir(parents=True, exist_ok=True)
    for rel, text in ui.DEMO_FILES.items():
        (proj / rel).write_text(text, encoding="utf-8")
    live = ui.Session(str(proj), test="", demo=False)
    live._real_text = lambda prompt: ui.DEMO_TURNS[1]["text"]
    row = live.run_turn("add a retry")
    ck("a NON-demo session handed the same model text by the real loop produces the "
       "identical proposal through the identical function — the DIFF panel is one "
       "implementation with two sources of text, not a demo copy that happens to "
       "look right",
       row["files"] == ["notifier.py"]
       and live.proposed["notifier.py"] == ui.propose(
           live.files(), ui.DEMO_TURNS[1]["text"])[0]["notifier.py"], str(row)[:180])

    noop = ui.Session(str(proj), test="", demo=False)
    text = ("# edit: notifier.py :: send_email\n```python\n"
            "def send_email(to, body):\n    return _smtp.send(to, body)\n```\n")
    noop._real_text = lambda prompt: text
    row = noop.run_turn("rewrite send_email as itself")
    ck("a turn whose patch changes nothing says `changes no files` and offers no "
       "Apply — the verdict counts what the DIFF panel can show, not what the parser "
       "accepted, because a line promising a change with no change under it is the "
       "sentence this project's first-run rule exists to stop",
       row["files"] == [] and "changes no files" in row["verdict"], row["verdict"])


def arms_load_ref(tmp: Path) -> None:
    """Which weight the page asks for, on each backend, without loading anything.

    `backend.selected()` returns a Backend, not a name, and the two backends take
    different SHAPES for that argument: a tier word resolves to a GGUF in the cache,
    a repo id resolves to an MLX snapshot. A `selected() == "mlx"` comparison is
    simply always false, so the one line that decides what a run loads would hand an
    MLX Mac a tier name and a llama.cpp box a repo id — and neither failure prints
    anything until the model is already being opened.
    """
    proj = tmp / "refs"
    proj.mkdir(parents=True, exist_ok=True)
    real_load, real_selected = backend.load, backend.selected
    asked: dict[str, list[str]] = {}

    class _Be:
        name = "?"

    def fake_load(ref, adapter=None):
        asked[_Be.name].append(ref)
        return object(), object()

    backend.load = fake_load
    try:
        for name in ("llama.cpp", "mlx"):
            asked[name] = []
            _Be.name = name
            backend.selected = lambda: _Be()
            ui.Session(str(proj), test="", demo=False).load()
    finally:
        backend.load, backend.selected = real_load, real_selected

    ck("a `llama.cpp` box is asked for the pinned GGUF TIER WORD — `fast` is what "
       "`flash models pull` registered, and a repo id there would be opened as a "
       "file name",
       asked["llama.cpp"] == ["fast"], str(asked["llama.cpp"])[:120])
    ck("and an `mlx` box is asked for the MLX repo id the CLI itself defaults to, "
       "read off `cli.DEFAULT_MODEL` rather than copied: the page proves the same "
       "weights `flash setup` just proved, on whichever library answers",
       asked["mlx"] == [cli.DEFAULT_MODEL], str(asked["mlx"])[:120])
    ck("the two answers are DIFFERENT STRINGS: this is the check that makes the "
       "pair above mean something, because a loader that compared the Backend object "
       "to a name (`selected() == \"mlx\"`, which is never true) handed every box "
       "the tier word and printed nothing wrong until the model was already open",
       asked["llama.cpp"] != asked["mlx"], str(asked)[:160])


def arms_child(tmp: Path) -> None:
    """The command itself: `flash ui --demo-backend`, started and stopped for real."""
    p = subprocess.Popen([PY, "-m", "flash.cli", "ui", "--demo-backend", "--port",
                          "0"], cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True)
    # A thread feeds the lines and the deadline is checked in THIS thread, because
    # `readline()` on a pipe has no timeout of its own. `dispatch` flushes its four
    # notice lines precisely so this arrives; a version that did not would sit in
    # `serve_forever()` with the URL still in a full pipe buffer, and a loop of
    # `while time.time() < t0: readline()` would block here forever.
    lines: list[str] = []

    def feed():
        for line in p.stdout:
            lines.append(line)

    threading.Thread(target=feed, daemon=True).start()
    url, t0 = "", time.time()
    i = 0
    while time.time() - t0 < 30:
        while i < len(lines):
            if "http://127.0.0.1:" in lines[i]:
                url = lines[i].strip().split("open ")[-1]
                break
            i += 1
        if url:
            break
        time.sleep(0.2)
    if url:
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        with urllib.request.urlopen(url, timeout=30) as r:
            page = r.read().decode()
        req = urllib.request.Request(url.rstrip("/") + "/api/turn",
                                     data=json.dumps({"prompt": "hi"}).encode(),
                                     headers={"content-type": "application/json"})
        jid = json.loads(urllib.request.urlopen(req, timeout=60).read())["job"]
        turn = {}
        t1 = time.time()
        while time.time() - t1 < 60:
            job = json.loads(urllib.request.urlopen(url.rstrip("/")
                                                    + f"/api/job?id={jid}",
                                                    timeout=30).read())
            if job.get("state") != "running":
                turn = job.get("result") or {}
                break
            time.sleep(0.2)
        ck("`flash ui --demo-backend` as a real child process prints the URL it is "
           "serving and the project it points at, answers one HTTP turn, and hands "
           "back the page — the command a user types, not the object behind it",
           "Flash Coder" in page and turn.get("i") == 1
           and "DEMO" in turn.get("verdict", ""),
           f"{url} {str(turn)[:120]}")
        p.send_signal(subprocess.signal.SIGINT)
        rc = p.wait(timeout=30)
        ck("and Ctrl-C stops it cleanly, with a line saying so", rc == 0,
           f"rc={rc}")
        ck("no port left behind: the listener is gone and the address binds again, "
           "so the next `flash ui` — or this one again — starts without a fight",
           _closed(port), f"port {port} still answering")
    else:
        p.kill()
        p.wait(timeout=30)
        ck("flash ui --demo-backend printed its URL within 30s, flushed, so a "
           "piped first run tells the user where to go", False,
           f"stdout lines: {lines}")


def arms_state_shape(a: Api) -> None:
    st = a.get("/api/state")[1]
    ck("one round trip gives the page everything it paints — the demo mark, the "
       "project, the files, the proposals, the history, the oracle it was handed, "
       "and BOTH backends with their reasons — so the page needs no second request "
       "and no guess about this machine",
       all(k in st for k in ("demo", "context", "files", "proposed", "history",
                             "profiles", "cache", "test_path", "available",
                             "supports", "backend")),
       str(sorted(st))[:200])
    mlx = [p for p in st["profiles"] if p["backend"] == "mlx"]
    ck("the MODELS panel's rows are the seam's own profiles, keyed `backend` — the "
       "answer is which library could run and what stops the other, which is what "
       "`flash doctor` says, not a paraphrase the UI wrote",
       len(mlx) == 1 and "available" in mlx[0] and len(st["profiles"]) == 2,
       str(st["profiles"])[:220])
    ck("and `backend` is a NAME, never an object's repr: a page that shows "
       "`<flash.backend.MLXBackend object at 0x1060ad050>` has leaked a memory "
       "address into the one panel that is supposed to explain the machine",
       st["backend"] in ("mlx", "llama.cpp", ""), repr(st["backend"])[:120])
    ck("the cache block is `models.status()`'s own state, tiers and bytes and all, "
       "so the panel's idea of what is on disk is the same idea `flash models list` "
       "prints — the two surfaces this repo has twice had disagree",
       st["cache"]["tiers"] == models.status()["tiers"]
       and "dir" in st["cache"], str(st["cache"])[:200])


# ------------------------------------------------------------------ the gates
def arm(fn, *args) -> None:
    """Run one arm, and turn a crash inside it into a FAILED check.

    Every arm here talks HTTP to a server in a thread, so an `Api` whose handler
    raised is a socket that closes mid-request, and the exception arrives two calls
    later inside whichever arm happened to be running. Without this wrapper that
    single stack frame ends the sweep: the remaining arms never run, the mutation
    half never starts, and the output is a traceback rather than a line saying which
    promise broke — which is exactly the thing this product is not allowed to do to
    a user.
    """
    try:
        fn(*args)
    except Exception as e:                              # noqa: BLE001
        ck(f"{getattr(fn, '__name__', 'arm')} ran without raising", False,
           f"{type(e).__name__}: {e}")


def run_gates() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ui-check-"))
    srv, base = serve()
    a = b = None
    try:
        with pinned(tmp / "cache", "range", base):
            a = Api(tmp / "project")
            for fn in (arms_page, arms_turns, arms_verify, arms_apply_reject,
                       arms_state_shape):
                arm(fn, a)
            arm(arms_pull_job, a, tmp)
        if a is not None:
            a.close()
        ck("no port left behind after the server is shut down — it binds again and "
           "nothing answers the old listener", a is not None and _closed(a.port),
           f"port {getattr(a, 'port', '?')}")

        b = Api(tmp / "nooracles", test="")
        arm(arms_honest_no_oracle, b)
        arm(arms_refusals, b)
        b.close()
        ck("and the same for the second server: a gate that leaves a listener "
           "behind cannot be run twice", _closed(b.port), f"port {b.port}")

        # Its own server, on a project with no demo mark: `--demo-backend` is the
        # only thing that keeps a turn from reaching for a model, so this is the arm
        # that proves a missing backend becomes a sentence on the page.
        arm(arms_turn_refusal, tmp)
        arm(arms_marks, tmp / "marks")
        arm(arms_load_ref, tmp)
        arm(arms_child, tmp)
    finally:
        for s in (a, b):
            if s is not None:
                s.close()
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ mutants
#: (label, [(path, old, new)]) — each restores a page that looks finished.
MUTANTS = {
    "apply_writes_without_the_guard": ("Apply puts the bytes there itself, so the "
                                       "oracle file is no longer protected and the "
                                       "line counts the page prints are made up",
                                       [("flash/ui.py",
                                         "            changed = land(self.context, "
                                         "before, {path: self.proposed[path]},\n"
                                         "                           protected=(self"
                                         ".test_path,) if self.test_path else ())\n",
                                         "            (Path(self.context) / path)"
                                         ".write_text(self.proposed[path])\n"
                                         "            changed = [[path, 0, 0]]\n")]),
    "verify_grades_the_disk": ("the panel runs the oracle against the files on disk, "
                               "so a user is told their un-edited project fails the "
                               "test their edit would have passed",
                               [("flash/ui.py",
                                 "        files = {**self.files(), **self.proposed}"
                                 "\n",
                                 "        files = self.files()\n")]),
    "reject_lies_when_there_is_nothing": ("Reject answers OK for a path nobody "
                                          "proposed, so the button is a gesture "
                                          "rather than a decision",
                                          [("flash/ui.py",
                                            '                    "err": f"flash ui: '
                                            '`{path}` has no proposal to reject"}\n',
                                            '                    "ok": True, '
                                            '"path": path}\n')]),
    "demo_needs_no_mark": ("the canned backend answers whatever the run was started "
                           "with, so a scripted reply can reach a user who never "
                           "asked for a demo",
                           [("flash/ui.py", "    if not marked:\n", "    if False:\n")]),
    "binds_any_interface": ("the loopback guard moves into the docstring, and a page "
                            "that can write to a project starts listening on every "
                            "interface the machine has",
                            [("flash/ui.py", '    if host != "127.0.0.1":\n',
                              "    if False:\n")]),
    "verdict_counts_the_parser": ("the turn line counts the patches the parser "
                                  "accepted instead of the proposals the panel can "
                                  "show, so a no-op change advertises an Apply "
                                  "button for a file that is already like that",
                                  [("flash/ui.py",
                                    '"verdict": _verdict(ok, len(self.proposed) if '
                                    'ok else n, err,\n',
                                    '"verdict": _verdict(ok, n, err,\n')]),
    "page_fetches_a_cdn": ("the page gains one remote script, which is one dependency "
                           "the offline first run does not have and one origin that "
                           "can now execute against this server",
                           [("flash/ui.py", "<title>Flash Coder</title>\n",
                             "<title>Flash Coder</title>\n"
                             '<script src="https://cdn.example/flash.js"></script>\n')]),
    "fail_printed_for_an_unrun_oracle": ("a VERIFY with no oracle prints FAIL, which "
                                         "is the difference between an honest 'I did "
                                         "not run' and a red panel about code nobody "
                                         "tested",
                                         [("flash/ui.py",
                                           '            v["text"] = v["err"] if not '
                                           'v["ran"] else (\n',
                                           '            v["text"] = "FAIL " + v["err"]'
                                           ' if not v["ran"] else (\n')]),
    "pull_has_no_bar": ("the pull keeps printing its plan to the job and sends the "
                        "progress line to the terminal's stderr, so the panel that "
                        "promised `the same bar as setup` shows a list that stops "
                        "moving for an hour",
                        [("flash/ui.py",
                          "        rc = models.fetch(t, yes=True, progress=True, "
                          "out=sink, bar=sink)\n",
                          "        rc = models.fetch(t, yes=True, progress=True, "
                          "out=sink)\n")]),
    "progress_never_renders": ("the poll loop still asks for the job and then throws "
                               "the lines away, so the page is correct about the "
                               "download and silent while it happens",
                               [("flash/ui.py",
                                 "  el('job').textContent=(j.lines||[]).slice(-12)"
                                 ".join('\\n');\n", "")]),
    "ref_is_a_string_compare": ("the loader compares the Backend object to a name, "
                                "which is never true, so every box is handed the "
                                "other library's idea of a model",
                                [("flash/ui.py",
                                  '            ref = ("fast" if '
                                  'backend.selected().name == "llama.cpp"\n'
                                  '                   else cli.DEFAULT_MODEL)\n',
                                  '            ref = cli.DEFAULT_MODEL if '
                                  'backend.selected() == "mlx" else "fast"\n')]),
    "turn_holds_the_socket": ("the turn goes back to running on the request "
                              "handler's thread, so a box with no generation backend "
                              "answers NOTHING — the browser sees a connection that "
                              "closed and the terminal prints the stack frame this "
                              "product promises never to show",
                              [("flash/ui.py",
                                '            prompt = body["prompt"].strip()\n'
                                "\n"
                                "            def work(job, p=prompt):\n"
                                "                row = self.session.run_turn(p)\n"
                                '                self._log(f"turn {row[\'i\']}: '
                                '{row[\'verdict\']}")\n'
                                "                return row\n"
                                "            return self._json({\"ok\": True,\n"
                                '                               "job": _job(self'
                                '.session, work, "turn")})\n',
                                '            row = self.session.run_turn(body'
                                '["prompt"].strip())\n'
                                '            self._log(f"turn {row[\'i\']}: '
                                '{row[\'verdict\']}")\n'
                                '            return self._json({"ok": True, "turn": '
                                'row})\n')]),
}


def _apply(patches):
    saved: dict[Path, str] = {}
    for rel, old, new in patches:
        p = ROOT / rel
        text = p.read_text()
        if text.count(old) != 1:
            for rp, rt in saved.items():
                rp.write_text(rt)
            raise AssertionError(f"mutant anchor is not unique in {rel}")
        saved.setdefault(p, text)
        p.write_text(text.replace(old, new))
    return list(saved.items())


def _repatched() -> None:
    """Re-execute `flash/ui.py` in THIS interpreter: the arms drive `ui.make_server`
    and `ui.propose` as objects, so a file on disk that no reload touches would let
    every mutant pass silently. Only `ui` is reloaded — `backend` and `models` hold
    this run's pinned cache and tier table, and a reload would reset them mid-arm."""
    importlib.reload(ui)


def mutate(one: str | None = None) -> int:
    #: Every file any mutant touches, captured before the first patch — a run that
    #: left half a mutant in the tree would certify code nobody tested.
    pristine = {(ROOT / rel): (ROOT / rel).read_bytes()
                for _, patches in MUTANTS.values() for rel, _, _ in patches}
    ran = failed = 0
    for name in ([one] if one else sorted(MUTANTS)):
        label, patches = MUTANTS[name]
        CHECKS.clear()
        saved = _apply(patches)
        try:
            _repatched()
            run_gates()
        finally:
            for p, text in saved:
                p.write_text(text)
            _repatched()
        left = [str(p.relative_to(ROOT)) for p, b in pristine.items()
                if p.read_bytes() != b]
        bad = [l for l, ok, _ in CHECKS if not ok]
        ran += 1
        print(f"  {'ok  ' if bad and not left else 'FAIL'} MUTATION: {label} -> "
              f"{len(bad)} check(s) fail" + (f"  {bad[0]}" if bad else "")
              + (f"  MUTANT LEFT IN TREE: {left}" if left else ""))
        failed += not bad or bool(left)
    print(f"\nui mutants: {ran - failed}/{ran} gates defeated by exactly their checks")
    return failed


def main(argv: list) -> int:
    if argv and argv[0] == "--mutant":
        return 1 if mutate(argv[1] if len(argv) > 1 else None) else 0
    run_gates()
    n = len(CHECKS)
    bad = [c for c in CHECKS if not c[1]]
    for label, ok, detail in CHECKS:
        print(f"  {'OK  ' if ok else 'FAIL'} {label}"
              + (f"\n       [{detail}]" if detail and not ok else ""))
    print(f"\nui checks: {n - len(bad)}/{n} passed")
    if bad:
        return 1
    return 1 if mutate() else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
