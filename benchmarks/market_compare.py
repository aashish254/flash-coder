#!/usr/bin/env python3
"""Compare Flash Coder against another coding agent on the same weights.

Every performance number this repo published until now was about a MODEL
(`m0_bakeoff.py`) or about our own loop against itself. None of them answered
"is it better and by how much" against a tool a stranger could download
instead. This does, on the one axis where the comparison is honest: the same
served weights behind an OpenAI-compatible endpoint, the same frozen 20-task
suite, the same oracle grading, and a different agent loop on each side.

The instrument is a counting proxy, because "tokens used" is only a measurement
if something on the server side adds it up. Pointing aider at the model directly
would produce a pass rate and no denominator. Arms run through the proxy; the
proxy refuses to let an arm report zero requests, which is the shape of a
borrowed number.

    python -m mlx_lm.server --model mlx-community/Qwen2.5-Coder-7B-Instruct-4bit --port 8124
    python benchmarks/market_compare.py --arms oneshot,aider --serve-port 8124 \
        --out benchmarks/results/market_compare_$(date +%Y%m%d).log

Usage note on the aider flags: `--edit-format whole` is what this task shape
asks for (each task writes a new file from an empty one), and `--no-stream` is
what the proxy needs to see a `usage` block. Neither is a handicap; both are
stated in the witness so a reader can rerun with other choices.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from benchmarks.m0_bakeoff import extract_code  # noqa: E402
from flash import trace  # noqa: E402
from flash.harness import run_test  # noqa: E402

DEFAULT_MODEL = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
MAX_TOKENS = 1024
ARM_TIMEOUT = 420


class Count:
    """Server-side token accounting, shared by every request the proxy sees."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.prompt = 0
        self.completion = 0

    def snapshot(self) -> tuple[int, int, int]:
        with self.lock:
            return self.requests, self.prompt, self.completion

    def add(self, prompt: int, completion: int) -> None:
        with self.lock:
            self.requests += 1
            self.prompt += prompt
            self.completion += completion

    def delta(self, since: tuple[int, int, int]) -> tuple[int, int, int]:
        now = self.snapshot()
        return tuple(n - o for n, o in zip(now, since))  # type: ignore[return-value]


COUNT = Count()


class Handler(http.server.BaseHTTPRequestHandler):
    upstream = ""

    @staticmethod
    def _root() -> str:
        return Handler.upstream[: -len("/chat/completions")]

    def _forward(self, body: bytes) -> None:
        req = urllib.request.Request(self.upstream, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=ARM_TIMEOUT) as res:
                payload = res.read()
                code = res.status
        except urllib.error.HTTPError as exc:
            payload, code = exc.read(), exc.code
        except OSError as exc:
            payload = json.dumps({"error": str(exc)}).encode()
            code = 502
        if code == 200:
            try:
                usage = json.loads(payload).get("usage") or {}
                COUNT.add(int(usage.get("prompt_tokens") or 0),
                          int(usage.get("completion_tokens") or 0))
            except (ValueError, TypeError):
                pass
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802
        self._forward(self.rfile.read(int(self.headers.get("Content-Length", 0))))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        leaf = path.rsplit("/", 1)[-1]
        with urllib.request.urlopen(f"{self._root()}/{leaf}", timeout=30) as res:
            payload = res.read()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_a: str) -> None:
        pass


def read_tasks(path: Path) -> list[dict]:
    """`m0_bakeoff.load_tasks()` has no argument: it always reads the frozen m0
    suite. Handing this driver `--tasks` and then calling it anyway would run the
    suite a reader did not ask for and print its totals under the name they did —
    which is exactly the shape this project keeps having to catch in itself."""
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def serve(proxy_port: int, upstream: str) -> None:
    Handler.upstream = upstream

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = Server(("127.0.0.1", proxy_port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def completions(base: str, model: str, prompt: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=ARM_TIMEOUT) as res:
        data = json.loads(res.read())
    return data["choices"][0]["message"]["content"]


def aider_dir(work: Path, task: dict) -> Path:
    d = work / task["id"]
    d.mkdir(parents=True, exist_ok=True)
    (d / "answer.py").write_text("")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "bench@example.invalid"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "bench"], cwd=d, check=True)
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "start"], cwd=d, check=True)
    return d


def aider_run(binary: str, base: str, model: str, d: Path, prompt: str) -> tuple[int, str]:
    cmd = [
        binary,
        "--model", f"openai/{model}",
        "--openai-api-base", base,
        "--no-stream", "--yes-always", "--no-check-update", "--no-analytics",
        "--no-pretty", "--no-dirty-commits",
        "--edit-format", "whole",
        "--file", "answer.py",
        "--message", prompt,
    ]
    env = dict(os.environ, OPENAI_API_KEY="proxy-no-auth",
               # aider calls `webbrowser.open` for its release notes on a version
               # it has not shown before, and this arm starts one aider per task.
               # Twenty launches is twenty tabs in the user's browser, which is
               # not a measurement of anything and is not their machine to use.
               BROWSER="/usr/bin/true",
               AIDER_ANALYTICS_PROVIDER_LOG="", PYTHONUNBUFFERED="1")
    try:
        p = subprocess.run(cmd, cwd=d, env=env, capture_output=True, text=True,
                           timeout=ARM_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, "aider timed out"
    return p.returncode, (p.stdout + p.stderr)[-4000:]


def grade(tasks: list[dict], produce, label: str) -> dict:
    """Run one arm. `produce(task) -> code` is the only thing an arm supplies,
    so the prompt, the oracle and the denominator are identical across arms."""
    res = {"arm": label, "passed": 0, "seconds": [], "requests": 0,
           "prompt": 0, "completion": 0, "lines": []}
    for t in tasks:
        before = COUNT.snapshot()
        t0 = time.perf_counter()
        code, note = produce(t)
        secs = time.perf_counter() - t0
        req, pr, co = COUNT.delta(before)
        ok, err = run_test(code, t["test"]) if code.strip() else (False, "no code produced")
        res["passed"] += bool(ok)
        res["seconds"].append(secs)
        res["requests"] += req
        res["prompt"] += pr
        res["completion"] += co
        res["lines"].append(
            f"  {'PASS' if ok else 'FAIL'} {t['id']:<22} {secs:5.1f}s  "
            f"req={req} tok={pr}+{co}"
            + ("" if ok else f"  -> {(err or '').splitlines()[-1][:70] if err else note[:70]}"))
    return res


def flash_arm(python: str, tasks_file: Path, trace_dir: Path) -> dict:
    """Our own loop, run as a subprocess so it loads the weights in-process and
    writes a trace session. Tokens come from that trace, not from a guess — and
    if the session is missing, the token line prints 0 and the gate below calls
    that out rather than letting it pass as a measurement.

    `--allow-big never` is deliberate: the server has one 7B on it, so a second
    model in the loop would make the arms compare different weights."""
    cmd = [python, "-m", "flash.cli", "run-suite", "--tasks", str(tasks_file),
           "--attempts", "2", "--max-tokens", str(MAX_TOKENS), "--allow-big", "never"]
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                       timeout=ARM_TIMEOUT * 10)
    wall = time.perf_counter() - t0
    files = sorted(trace_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime)
    sums = trace.summarize(files[-1].stem, trace_dir) if files else {}
    n = sums.get("tasks", 0)
    return {"arm": "flash run-suite", "passed": sums.get("solved", 0), "n": n,
            "seconds": [sums.get("seconds", wall) / max(n, 1)],
            "requests": sums.get("generations", 0),
            "prompt": sums.get("prompt_tokens", 0),
            "completion": sums.get("completion_tokens", 0),
            "lines": [f"  trace session {files[-1].stem if files else 'NONE'}: "
                      f"{sums.get('solved', 0)}/{n} solved, "
                      f"{sums.get('generations', 0)} generations, "
                      f"{sums.get('gen_s', 0)}s generating + {sums.get('verify_s', 0)}s "
                      f"verifying of {sums.get('seconds', 0)}s task time, rc {p.returncode}"],
            "total_seconds": wall}


# The witness is published, so it cannot carry this machine's disk. The aider arm's
# failure detail is a child process's own log, which quotes whatever paths it was
# launched with, so the driver substitutes rather than trusting a clean run.
def host_markers() -> list[tuple[str, str]]:
    """This machine's prefixes, borrowed from the gate that measures them.

    Spelling a home-directory prefix into this file would itself be the leak
    `portable_paths_check.py` exists to catch, and the only exemption it grants is the
    line in that file which declares the list. So the list is imported instead of
    copied, and the checkout and the home directory go in as values rather than as
    literals."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from portable_paths_check import HOST_PATHS
    home = str(Path.home())
    out = [(str(ROOT), "<checkout>"), (home, "<home>")]
    out += [(m, "<host>") for m in HOST_PATHS if m.rstrip("/") != home]
    return out


def scrub(text: str) -> tuple[str, int]:
    """Replace every host prefix with a token and count what was replaced.

    Longest marker first: the checkout path *starts with* the home directory, so
    substituting the shorter one first would leave `<home>/…/flash-coder` behind and
    a witness that names the author's tree in pieces."""
    replaced = 0
    for marker, token in sorted(host_markers(), key=lambda p: -len(p[0])):
        if not marker:
            continue
        replaced += text.count(marker)
        text = text.replace(marker, token)
    return text, replaced


def report(res: dict, n: int) -> None:
    avg = sum(res["seconds"]) / max(len(res["seconds"]), 1)
    print(f"\n=== {res['arm']}: {res['passed']}/{n} "
          f"({res['passed'] / n:.0%}) · {avg:.1f}s/task · "
          f"{res['requests']} requests · {res['prompt']} prompt + "
          f"{res['completion']} completion tokens")
    for ln in res["lines"]:
        print(ln)
    if res["requests"] == 0:
        print(f"BAD  {res['arm']} made 0 proxied requests, so its token line is "
              "not a measurement of anything")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="oneshot,aider")
    ap.add_argument("--serve-port", type=int, default=8124)
    ap.add_argument("--proxy-port", type=int, default=8125)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--aider-bin", default="/tmp/aider-venv/bin/aider")
    ap.add_argument("--flash-python", default=str(ROOT / ".venv" / "bin" / "python"))
    ap.add_argument("--tasks", default=str(ROOT / "benchmarks" / "tasks" / "m0_tasks.jsonl"))
    ap.add_argument("--max-tasks", type=int, default=0)
    ap.add_argument("--trace-dir", default=str(ROOT / "benchmarks" / "results" / "traces"))
    ap.add_argument("--out", default="")
    ap.add_argument("--selftest", action="store_true",
                    help="run the gates that do not need a server")
    args = ap.parse_args(argv)

    witness: list[str] = []

    def say(line: str = "") -> None:
        print(line)
        witness.append(line)

    tasks = read_tasks(Path(args.tasks))
    if args.max_tasks:
        tasks = tasks[: args.max_tasks]
    say(f"suite: {Path(args.tasks).name} — {len(tasks)} tasks, "
        f"ids {tasks[0]['id']}…{tasks[-1]['id']}")

    # Gate 1: the grader. Twenty stored reference solutions must pass the oracle
    # before a tool's failure means anything.
    bad = [t["id"] for t in tasks if not run_test(t["solution"], t["test"])[0]]
    say(f"grader check: {len(tasks) - len(bad)}/{len(tasks)} stored reference "
        f"solutions pass the harness oracle" + (f" BAD {bad}" if bad else ""))
    if bad or args.selftest:
        return 1 if bad else 0

    tasks_file = Path(args.tasks)
    base = f"http://127.0.0.1:{args.proxy_port}/v1"
    upstream = f"http://127.0.0.1:{args.serve_port}/v1/chat/completions"
    serve(args.proxy_port, upstream)

    # Gate 2: every arm talks to the SAME weights. Ask the server what it is
    # serving rather than trusting the flag this script was handed.
    try:
        with urllib.request.urlopen(f"{base}/models", timeout=20) as res:
            served = [m.get("id") for m in json.loads(res.read()).get("data", [])]
    except OSError as exc:
        say(f"BAD  cannot reach the proxy or the server: {exc}")
        return 2
    say(f"model check: server answers /v1/models with {served}")
    if args.model not in served:
        say(f"BAD  asked for {args.model!r}, server is serving {served}")
        return 2

    work = Path("/tmp/market_compare_work")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    results = []
    for arm in [a for a in args.arms.split(",") if a]:
        if arm == "oneshot":
            def produce(t: dict, base=base) -> tuple[str, str]:
                try:
                    out = completions(base, args.model, t["prompt"])
                except OSError as exc:
                    return "", f"request failed: {exc}"
                return extract_code(out), ""
            results.append(grade(tasks, produce, "single-shot (no agent loop)"))
        elif arm == "aider":
            def produce(t: dict, work=work) -> tuple[str, str]:  # noqa: B008
                d = aider_dir(work, t)
                rc, log = aider_run(args.aider_bin, base, args.model, d, t["prompt"])
                code = extract_code((d / "answer.py").read_text(errors="ignore"))
                return (code, f"aider rc {rc}") if code else ("", f"aider rc {rc}: {log[-200:]}")
            results.append(grade(tasks, produce, "aider"))
        elif arm == "flash":
            results.append(flash_arm(args.flash_python, tasks_file, Path(args.trace_dir)))
        else:
            say(f"BAD  unknown arm {arm!r}")
            return 2
        report(results[-1], len(tasks))

    say("\n" + "=" * 72)
    say(f"{'arm':<34}{'pass':>7}{'s/task':>9}{'requests':>10}{'tokens':>10}")
    for r in results:
        avg = sum(r["seconds"]) / max(len(r["seconds"]), 1)
        say(f"{r['arm']:<34}{r['passed']:>4}/{len(tasks)}{avg:>9.1f}"
            f"{r['requests']:>10}{r['prompt'] + r['completion']:>10}")
    say("\nMeasured on this machine, one Mac, 4-bit local weights, no API keys. "
        "Watts/task is not in this table: `powermetrics` needs sudo (SPEC §9, G6).")

    if args.out:
        text, replaced = scrub("\n".join(witness) + "\n")
        left = sum(text.count(marker) for marker, _ in host_markers() if marker)
        line = (f"provenance  host paths left in this witness: {left} "
                f"(substitutions: {replaced})")
        Path(args.out).write_text(text + line + "\n")
        print(line)
        print(f"witness -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
