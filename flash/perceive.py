"""PERCEIVE — §33.1: static-analysis oracle via LSP (pylsp over stdio).

Gives the agent compiler-grade eyes BEFORE executing: syntax errors,
undefined names, unused imports — with exact line numbers. In the loop,
static errors skip the test run entirely and feed straight into the fix
prompt: cheaper and more precise than a failed subprocess.

Design: one short-lived pylsp process per check, virtual document (nothing
touches disk), Content-Length-framed JSON-RPC per the LSP base protocol.
Falls back to stdlib compile() when pylsp isn't installed.
"""
from __future__ import annotations

import json
import select
import subprocess
import sys
import time
from dataclasses import dataclass

_SEV = {1: "error", 2: "warning", 3: "info", 4: "hint"}


@dataclass
class Diagnostic:
    line: int          # 1-based
    severity: str      # error | warning | info | hint
    message: str
    source: str = "lsp"


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode()
    return b"Content-Length: %d\r\n\r\n" % len(body) + body


def _read_msg(stream) -> dict | None:
    headers: dict[bytes, bytes] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        k, _, v = line.partition(b":")
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get(b"content-length", b"0"))
    return json.loads(stream.read(n)) if n else None


def lsp_diagnostics(code: str, timeout: float = 10.0) -> list[Diagnostic]:
    """Open `code` as a virtual doc in pylsp; return its diagnostics."""
    proc = subprocess.Popen([sys.executable, "-m", "pylsp"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)
    uri = "file:///tmp/flash_perceive_virtual.py"

    def send(method: str, params: dict, msg_id: int | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if msg_id is not None:
            msg["id"] = msg_id
        proc.stdin.write(_frame(msg))
        proc.stdin.flush()

    try:
        send("initialize", {"processId": None, "rootUri": None,
                            "capabilities": {}}, msg_id=1)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:               # await init response
            if not select.select([proc.stdout], [], [], 0.2)[0]:
                continue
            msg = _read_msg(proc.stdout)
            if msg and msg.get("id") == 1:
                break
        send("initialized", {})
        send("textDocument/didOpen", {"textDocument": {
            "uri": uri, "languageId": "python", "version": 1, "text": code}})

        diags: list[Diagnostic] | None = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:               # await diagnostics
            if not select.select([proc.stdout], [], [], 0.2)[0]:
                continue
            msg = _read_msg(proc.stdout)
            if msg is None:
                break
            if msg.get("method") == "textDocument/publishDiagnostics":
                diags = [Diagnostic(line=d["range"]["start"]["line"] + 1,
                                    severity=_SEV.get(d.get("severity", 3), "info"),
                                    message=d["message"].splitlines()[0],
                                    source=d.get("source", "lsp"))
                         for d in msg["params"]["diagnostics"]]
                break
        send("shutdown", {}, msg_id=2)
        send("exit", {})
        return diags if diags is not None else _syntax_fallback(code)
    except Exception:
        return _syntax_fallback(code)
    finally:
        proc.kill()


def _syntax_fallback(code: str) -> list[Diagnostic]:
    try:
        compile(code, "<generated>", "exec")
        return []
    except SyntaxError as e:
        return [Diagnostic(line=e.lineno or 1, severity="error",
                           message=e.msg, source="compile")]


def static_check(code: str, use_lsp: bool = True) -> list[Diagnostic]:
    """Public entry: errors+ from LSP if available, else syntax-only."""
    if use_lsp:
        try:
            return lsp_diagnostics(code)
        except Exception:
            pass
    return _syntax_fallback(code)


def format_errors(diags: list[Diagnostic], limit: int = 3) -> str:
    """One-line feedback string for the fix prompt."""
    errs = [d for d in diags if d.severity == "error"]
    return " | ".join(f"L{d.line}: {d.message}" for d in errs[:limit])
