"""R-9.2: the one explicit sandbox every execution path runs under (PLAN §21).

§21 specifies a Firecracker microVM per rollout. There is no VM layer between
this program and the kernel on the box it ships for (macOS, arm64 — and the
offline battery must run without a Docker daemon), so this module wraps the
platform's own kernel-enforced mechanism instead: a Seatbelt profile handed to
`/usr/bin/sandbox-exec`, plus POSIX resource limits the child inherits across
the exec. Candidate code cannot opt out of either, because both are applied by
the process that spawns it.

What that buys, each line measured on this box rather than assumed:

* writable root — `(deny file-write*)` with one `(allow ... (subpath root))`.
  A write to `~/.ssh` comes back as `PermissionError`.
* no network by default — `(deny network-outbound)`. To an IP address the
  refusal is `PermissionError: [Errno 1] Operation not permitted` at the
  `connect`; to a hostname it is a `socket.gaierror` at the name lookup, because
  DNS is itself outbound traffic. `urllib` re-wraps the first as `URLError`.
* cpu rlimit — `RLIMIT_CPU`, set wider than the wall clock on purpose (see
  `CPU_WIDTH`), and proven to bind: a busy loop under `(2, 3)` died at 2.01 s on
  signal 24 (SIGXCPU) rather than at its 30 s wall timeout. The wall clock is the
  bound that fires on a real candidate; this is the backstop.
* fsize rlimit — `RLIMIT_FSIZE`; a 600 MB write under a 256 MB cap came back as
  `OSError: [Errno 27] File too large`, i.e. a normal verify failure rather than
  a filled disk.
* memory rlimit — **not available here**, and said so where it cannot be
  hidden: `setrlimit` raises `ValueError: current limit exceeds maximum limit`
  for `RLIMIT_AS`, `RLIMIT_DATA` and `RLIMIT_RSS` at any finite value on this
  macOS, at any privilege. `memory_ceiling()` asks a fresh child to try it
  instead of shipping a table, so the claim tracks the platform.

Reads are NOT confined. The clause names a writable root, no network and
rlimits; a `(deny default)` read policy is a different (and much larger)
project — it breaks the interpreter's own framework and dyld lookups. So this
is a write-and-network jail with a cpu ceiling, described as that.

Two traps found by running the thing, both pinned by selftest:
`subpath` must be DOUBLE-quoted (a single-quoted path is read as a symbol and
sandbox-exec dies with "unexpected symbol argument"), and the root must be a
REALPATH (`/var/folders/...` is a symlink to `/private/var/folders/...`; the
exception silently misses and the candidate's own temp writes fail).

Offline: `python -m flash.sandbox --selftest`. That vector makes nine claims about
Seatbelt, which is a macOS mechanism, and it makes them on boxes that have no
Seatbelt — so each of those nine is asked in two arms (enforced refusal here,
named-and-absent degradation there) rather than dropping out of the denominator,
which is the only way a runner on any platform can be held to one printed fraction.
"""
from __future__ import annotations

import functools
import os
import resource
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SENTRY = "/usr/bin/sandbox-exec"

#: The cpu budget is `timeout * CPU_WIDTH + CPU_SLACK_S`, i.e. wider than the
#: wall clock by the box's own width — deliberately. RLIMIT_CPU accrues a
#: waited-for child's cpu time into its parent, so a candidate that fans out N
#: workers spends cpu N times faster than it spends seconds, and a cpu limit
#: tuned to one thread would fail a CORRECT parallel answer for our arithmetic.
#: Nothing in the 22 shipped suites parallelises (measured: 0 rows mention
#: multiprocessing / threading / subprocess / Popen) and Accelerate BLAS runs at
#: 0.66 cpu seconds per wall second here, so this ceiling never binds in practice
#: — which is the point. The wall-clock timeout is the bound that actually fires;
#: the rlimit is the backstop for a child that outlives the caller waiting on it.
CPU_WIDTH = max(os.cpu_count() or 1, 2)
CPU_SLACK_S = 2

#: MB a candidate may write per file. The suites here write files of kilobytes.
FSIZE_MB = 256

NULLDEV = "/dev/null"

#: What the hostile candidate in the vector writes, and the only content this
#: module will ever delete from a user's ~/.ssh (see `reap_own_artifact`).
SENTINEL_BYTES = "pwned"


def realpath(root: str | Path) -> str:
    """The root as Seatbelt must see it: resolved, no symlink prefix."""
    return str(Path(root).resolve())


@functools.lru_cache(maxsize=1)
def seatbelt() -> bool:
    """True only when the profile is PROVEN to bind on this box.

    Not `Path(SENTRY).exists()`: a `sandbox-exec` that accepts a profile it then
    ignores (or a macOS that stops honouring it) would otherwise leave every
    check below reading as green. This asks it to enforce one denial on a real
    file and requires the write to fail.
    """
    if not Path(SENTRY).exists():
        return False
    with tempfile.TemporaryDirectory() as d:
        root = realpath(d)
        victim = os.path.join(root, "enforcement-probe")
        prog = f"open({victim!r},'w').write('x')"
        try:
            r = subprocess.run(
                [SENTRY, "-p", '(version 1)\n(allow default)\n(deny file-write*)\n',
                 sys.executable, "-I", "-c", prog],
                capture_output=True, text=True, timeout=30)
        except Exception:
            return False
        return r.returncode != 0 and not Path(victim).exists()


CPU_EXCEPTIONS = (os.path.dirname(NULLDEV),)   # the null device: a child that
# redirects a subprocess's stderr to DEVNULL opens it for writing, and without
# this the sandbox would fail candidates for our reason, not theirs (measured).


def profile(root: str | Path) -> str:
    """The Seatbelt profile for one execution: default-open, write- and
    network-closed, with the candidate's own root as the only write exception.

    What makes the root writable is the FILTERED allow, not its position: Seatbelt
    resolves this pair by specificity, and moving the blanket `(deny file-write*)`
    after the allow still leaves the root writable (measured both ways). So the
    order below is convention, and the claim that the exception binds is carried
    by the write vector, not by the shape of this string.
    """
    out = ["(version 1)", "(allow default)", "(deny network-outbound)",
           "(deny file-write*)", f'(allow file-write* (subpath "{realpath(root)}"))']
    out += [f'(allow file-write* (subpath "{d}"))' for d in CPU_EXCEPTIONS]
    return "\n".join(out) + "\n"


def prefix(root: str | Path) -> list[str]:
    """What goes in front of the argv, or nothing when there is no sandbox."""
    if not seatbelt():
        return []
    return [SENTRY, "-p", profile(root)]


def _clamp(resource_id: int, want_soft: int) -> tuple[int, int]:
    """(soft, hard) we can actually set: a hard limit may only be lowered."""
    soft, hard = resource.getrlimit(resource_id)
    h = want_soft + 1 if hard == resource.RLIM_INFINITY else min(want_soft + 1, hard)
    s = min(want_soft, h)
    if soft != resource.RLIM_INFINITY:
        s = min(s, soft)
        h = min(h, max(s, soft))
    return s, h


def limits(cpu: int | None = None, fsize_mb: int = FSIZE_MB):
    """A `preexec_fn` that lowers cpu and file size before the exec.

    Applied to the wrapper process, which `exec`s into the interpreter, so the
    limits are the candidate's own and are inherited by anything it forks.
    Returns None when nothing can be set (a platform without these rlimits), so
    a caller never has to know which ones bound here.
    """
    wants = []
    if cpu is not None:
        wants.append((resource.RLIMIT_CPU, int(cpu)))
    if fsize_mb:
        wants.append((resource.RLIMIT_FSIZE, int(fsize_mb) * 1024 * 1024))
    if not wants:
        return None

    def _apply() -> None:
        for rid, value in wants:
            try:
                resource.setrlimit(rid, _clamp(rid, value))
            except Exception:
                pass                    # a limit this box refuses is `status`'s problem

    return _apply


@functools.lru_cache(maxsize=1)
def memory_ceiling() -> tuple[bool, str]:
    """(enforced, detail) for a process memory rlimit — measured, not declared."""
    return _measure_memory_ceiling()


_PROBE_MEMORY = """
import json, resource
tried, ok, why = [], [], ''
for n in ('RLIMIT_AS', 'RLIMIT_DATA', 'RLIMIT_RSS'):
    try:
        resource.setrlimit(getattr(resource, n), (512 * 1024 * 1024,) * 2)
        ok.append(n)
    except Exception as e:
        why = f'{n}: {type(e).__name__}: {e}'
    tried.append(n)
print(json.dumps({'ok': ok, 'why': why}))
"""


def _measure_memory_ceiling() -> tuple[bool, str]:
    """Ask a fresh interpreter to set each memory rlimit and report back.

    Deliberately a CHILD: the answer must come from the platform, not from a
    value this module could have written down.
    """
    try:
        r = subprocess.run([sys.executable, "-I", "-c", _PROBE_MEMORY],
                           capture_output=True, text=True, timeout=30)
        d = __import__("json").loads(r.stdout.strip())
    except Exception as e:
        return False, f"probe failed: {type(e).__name__}"
    if d["ok"]:
        return True, "enforced by " + ", ".join(d["ok"])
    return False, f"no memory rlimit can be set ({d['why']})"


def run(argv: list, root: str | Path, timeout: float, *,
        cwd: str | Path | None = None, env: dict | None = None,
        input: str | bytes | None = None, cpu: int | None = None,
        fsize_mb: int = FSIZE_MB) -> subprocess.CompletedProcess:
    """`subprocess.run` with the sandbox on it, raising the same exceptions.

    Every candidate-execution seam goes through here so that "every execution
    path runs under an explicit sandbox" is one call site, not a convention.
    The argv itself is untouched — a seam's `-I`/`-s` stays its own business,
    and `env` is copied, not rebuilt, so a caller's PYTHONHASHSEED survives.
    """
    root = realpath(root)
    e = dict(env if env is not None else os.environ)
    # A child's own temp files must land inside the writable root, or every
    # candidate that calls tempfile fails for our reason and not its own.
    e["TMPDIR"] = root
    cmd = prefix(root) + [str(a) for a in argv]
    return subprocess.run(cmd, cwd=str(cwd) if cwd else root, env=e, input=input,
                          capture_output=True, text=True, timeout=timeout,
                          preexec_fn=limits(
                              cpu if cpu is not None
                              else int(timeout) * CPU_WIDTH + CPU_SLACK_S, fsize_mb))


def status() -> dict:
    """What is actually enforced right now, for the report and the selftest."""
    mem_ok, mem_why = memory_ceiling()
    return {
        "seatbelt": ("enforced" if seatbelt() else "unavailable"),
        "writes": f"root-only, {NULLDEV} excepted" if seatbelt() else "unconfined",
        "network": "denied by default" if seatbelt() else "unconfined",
        "cpu": f"RLIMIT_CPU ({CPU_WIDTH}x wall + {CPU_SLACK_S}s), wall clock binds",
        "fsize": f"RLIMIT_FSIZE {FSIZE_MB}MB",
        "memory": mem_why if mem_ok else f"UNAVAILABLE: {mem_why}",
    }


# Three shapes of the same two hazards the vector names. `touch`/`dial` are
# callable so a check can put the hostile statement AFTER a passing assert; the
# module-level forms are what the vector's "candidate" really is.
_HOSTILE = """
import os, socket, urllib.request
def touch(p):
    open(os.path.expanduser(p), 'w').write('pwned')
    return 'wrote'
def dial(host, port):
    s = socket.socket(); s.settimeout(2); s.connect((host, port))
    return s
def get(url):
    return urllib.request.urlopen(url, timeout=2).read()
def f(x): return x
"""
_WRITE_AT_IMPORT = ("import os\n"
                    "open(os.path.expanduser({p!r}), 'w').write({b!r})\n"
                    "def f(x): return x\n")
# TEST-NET-3 (RFC 5737): documentation space, never routable, so the check
# proves the REFUSAL and not an accidental timeout on somebody's host.
_NET_AT_IMPORT = ("import socket\ns = socket.socket(); s.settimeout(2)\n"
                  "s.connect(('203.0.113.1', 80))\n"
                  "def f(x): return x\n")
_FETCH_AT_IMPORT = ("import urllib.request\n"
                    "b = urllib.request.urlopen('http://203.0.113.1/', timeout=2).read()\n"
                    "def f(x): return x\n")
_DNS_AT_IMPORT = ("import socket\nsa = socket.getaddrinfo('example.com', 80)\n"
                  "def f(x): return x\n")
# The degradation arm's two network candidates. A documentation IP's fate on a box
# with no jail is that box's own business — it can time out, refuse, or be answered by
# a proxy the runner happens to have — and a check may not depend on which. Port 1
# (tcpmux) on loopback answers the same way everywhere with nothing leaving the machine:
# refused. So the arm keeps the claim it can actually make — the harness turns a
# candidate's error into a verdict, and no PermissionError is credited to a jail that
# is not here — on a target that cannot vary with the runner's egress.
_NET_REFUSED_PORT = ("import socket\ns = socket.socket(); s.settimeout(2)\n"
                     "s.connect(('127.0.0.1', 1))\n"
                     "def f(x): return x\n")
_FETCH_REFUSED_PORT = ("import urllib.request\n"
                       "b = urllib.request.urlopen('http://127.0.0.1:1/', timeout=2).read()\n"
                       "def f(x): return x\n")


def reap_own_artifact(path: str | Path) -> bool:
    """Delete the sentinel a MUTATED run of the vector may have left in ~/.ssh.

    Only ever our own file: the name is ours and the content must be exactly the
    bytes the hostile candidate writes. Anything else stays, because a routine
    that sweeps a user's `~/.ssh` is a worse bug than the one it hides.
    """
    p = Path(path)
    try:
        if p.read_text() != SENTINEL_BYTES:
            return False
        p.unlink()
        return True
    except OSError:
        return False


def run_selftest() -> int:
    """The R-9.2 vector: a hostile candidate fails, honestly reported, and the
    honest ones never notice the sandbox is there.

    Nine of its thirty-four claims belong to one macOS mechanism. On a box that has
    no `/usr/bin/sandbox-exec` — which is every Linux and Windows host this package
    installs on — those claims cannot be earned by asking the kernel to refuse
    something it was never told to refuse, and they cannot be quietly dropped either,
    because a denominator that moves with the platform is a number no runner can be
    held to. So each is asked twice, in the two arms below: where Seatbelt binds the
    vector measures the refusal, and where it does not the vector measures the
    DEGRADATION — the jail absent, the module saying so, the hostile write landing in
    a throwaway HOME rather than a real `~/.ssh`, and no check claiming a denial this
    box did not produce. `arm()` picks the label, the same branch picks the condition,
    and the line printed before either run says which arm the box took.
    """
    checks = []

    def ck(name, cond, note=""):
        checks.append((name, bool(cond), str(note)))
        print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f"  [{note}]" if note and not cond else ""))

    confined = seatbelt()
    shipped_sentry = SENTRY

    def arm(has, lacks):
        return has if confined else lacks

    print("sandbox selftest, " + arm(
        "Seatbelt ENFORCED here: the claims below are measured against the kernel",
        f"no {SENTRY} on {sys.platform}: the same claims are measured against the "
        "degradation — an absent jail named as absent"))

    # Never aim at the real id_rsa: a leaking sandbox would destroy a key, and
    # the denial being tested is the same rule over any path in ~/.ssh.
    real_sentinel = str(Path.home() / ".ssh" / "flash-sandbox-probe-must-not-exist")
    # A run against a MUTANT (the blanket deny removed) lets the write land, so
    # the clean run reaps its own leftover before it measures anything.
    reap_own_artifact(real_sentinel)
    pre = Path(real_sentinel).exists()
    # `~/.ssh/id_rsa` spelled literally is NOT the hazard: open() does not expand
    # a tilde, so that candidate makes '<root>/~/.ssh/id_rsa' inside its own
    # writable root. Only the expanded form reaches the real home directory, so
    # the vector is run that way and the distinction is stated, not glossed.
    ck("the tilde case is not the confinement case: open('~/.ssh/x') writes into "
       "the root, because open() never expands a tilde",
       not Path(real_sentinel).exists())

    with tempfile.TemporaryDirectory() as td:
        root = realpath(td)
        py = [sys.executable, "-I"]
        from flash.harness import diagnose, score, score_files

        # the reaper only ever deletes what the vector itself wrote
        leak = Path(root) / "reap-me"
        ck("reap_own_artifact deletes our own leftover...",
           (leak.write_text(SENTINEL_BYTES), reap_own_artifact(leak))[1]
           and not leak.exists())
        foreign = Path(root) / "not-mine"
        foreign.write_text("an unrelated file")
        ck("...and refuses anything else: a sweep of ~/.ssh that deleted a real "
           "key would be the worse bug",
           not reap_own_artifact(foreign) and foreign.read_text() == "an unrelated file")
        ck("...and is quiet about a file that was never there",
           not reap_own_artifact(Path(root) / "absent"))

        def probe(src, **kw):
            try:
                return run(py + ["-c", src], root, kw.pop("timeout", 30), **kw)
            except subprocess.TimeoutExpired:
                # A candidate the cpu rlimit would have stopped now runs to the
                # wall clock: report that as a failed check, not a crashed run.
                return subprocess.CompletedProcess(["-c", src], -99, "", "timeout")

        # --- 1. the wrapper's own shape -------------------------------------
        prof = profile(root)
        ck("profile double-quotes the root: a single-quoted path is read as a "
           "SYMBOL and sandbox-exec dies with 'unexpected symbol argument'",
           f'(subpath "{root}")' in prof and "'" not in prof, prof.replace("\n", " "))
        ck("profile is given the REALPATH: /var/folders is a symlink to "
           "/private/var/folders and the exception silently misses the symlink",
           root == realpath(root) and not root.startswith("/var/"), root)
        ck("profile shape: one blanket write-deny AND one filtered allow naming "
           "this root — the two lines the clause's 'writable root' rests on "
           "(specificity, not order, is what binds: see `profile`'s docstring)",
           "(deny file-write*)" in prof and f'(subpath "{root}")' in prof,
           prof.replace("\n", " "))
        ck("(deny network-outbound) is in the profile every seam receives",
           "(deny network-outbound)" in prof)
        _saved = SENTRY
        try:
            globals()["SENTRY"] = "/nonexistent/sandbox-exec"
            seatbelt.cache_clear()
            r = probe("print('__PASS__')")
            ck("a box with no wrapper degrades to no prefix and still verifies "
               "(a Linux rollout host is not broken by this module)",
               prefix(root) == [] and r.stdout.strip() == "__PASS__", r.stdout[:60])
        finally:
            globals()["SENTRY"] = _saved
            seatbelt.cache_clear()
        ck(arm("seatbelt() is an enforcement probe, not a Path.exists(): this box "
               "has the wrapper AND a deny-only profile really refuses a write",
               "seatbelt() is False on this box and the prefix is empty, so nothing "
               "below can read as a jail: the probe asked the platform and the "
               "platform said it has no Seatbelt"),
           (seatbelt() is True and Path(SENTRY).exists()) if confined
           else (seatbelt() is False and prefix(root) == []))
        # The mutation this exists to kill: a seatbelt() that only asked
        # `Path(SENTRY).exists()` would report a sandbox on a box whose wrapper
        # ignores profiles. So hand it one that does, and require the answer to
        # be False — i.e. the module degrades loudly instead of lying quietly.
        fake = Path(root) / "fake-sandbox-exec"
        fake.write_text("#!/bin/sh\nif [ \"$1\" = \"-p\" ]; then shift 2; fi\nexec \"$@\"\n")
        fake.chmod(0o755)
        _saved = SENTRY
        try:
            globals()["SENTRY"] = str(fake)
            seatbelt.cache_clear()
            ck("a wrapper that IGNORES its profile yields seatbelt()=False and an "
               "empty prefix — no check here can then pass unearned, which is the "
               "only honest failure mode left",
               seatbelt() is False and prefix(root) == [])
        finally:
            globals()["SENTRY"] = _saved
            seatbelt.cache_clear()
        # The invariant the two SENTRY swaps above exist to protect is their own
        # restoration, stated as that. This line used to be `assert seatbelt() is
        # True`, which a box with no Seatbelt can never satisfy: on Linux the vector
        # died here with a bare AssertionError, printed no fraction at all, and the
        # runner read that as a red test it had no code to fix.
        assert SENTRY == shipped_sentry and seatbelt() is confined, \
            "the SENTRY swaps must leave the shipped wrapper path and the box's own answer in place"
        # Where the hostile candidates aim. Confined, that is the user's real ~/.ssh,
        # because the jail is both the thing under test and what makes aiming there
        # safe. Unconfined it is a throwaway HOME inside this run's temp root, because
        # on that box the write LANDS — and a vector that litters a real home directory
        # with a probe file, then reports the file as proof of a refusal it never got,
        # is the worse bug. The aim changes with the arm; the claim each check makes is
        # still about this module.
        if confined:
            sentinel = real_sentinel
        else:
            fake_home = Path(root) / "fake-home"
            (fake_home / ".ssh").mkdir(parents=True, exist_ok=True)
            sentinel = str(fake_home / ".ssh" / "flash-sandbox-probe-must-not-exist")

        # --- 2. the vector: two hostile candidates, normally reported -------
        s = score(_WRITE_AT_IMPORT.format(p=sentinel, b=SENTINEL_BYTES),
                  "assert f(1) == 1\nassert f(2) == 2\n", timeout=20)
        ck(arm("VECTOR: a candidate that writes to ~/.ssh is refused by the kernel, "
               "not by us pattern-matching the path",
               "VECTOR: with no jail the same candidate's write LANDS — which is how "
               "this run knows the prefix really is empty instead of quietly passing "
               "a confinement claim it never made"),
           (not s.ok and "PermissionError" in s.err) if confined
           else (s.ok and Path(sentinel).exists() and not Path(real_sentinel).exists()),
           s.err[:90])
        ck("...and the file it tried to create still does not exist afterwards" if confined
           else "...and what it wrote sits in that throwaway HOME, never in the real ~/.ssh",
           (not Path(sentinel).exists()) if confined
           else (Path(sentinel).exists() and not Path(real_sentinel).exists()),
           f"existed before this run: {pre}")
        s = score(_NET_AT_IMPORT if confined else _NET_REFUSED_PORT,
                  "assert f(1) == 1\nassert f(2) == 2\n", timeout=20)
        ck(arm("VECTOR: a candidate that opens a socket is refused by the kernel "
               "before a packet leaves",
               "VECTOR: no jail here, and the vector names that instead of borrowing a "
               "refusal it did not earn — a connect to a closed local port fails as the "
               "box's own ConnectionRefusedError, never as a PermissionError, while "
               "status() calls the network unconfined"),
           (not s.ok and "PermissionError" in s.err) if confined
           else (not s.ok and "ConnectionRefusedError" in s.err
                 and "PermissionError" not in s.err
                 and status()["network"] == "unconfined"),
           s.err[:90])
        s = score(_FETCH_AT_IMPORT if confined else _FETCH_REFUSED_PORT,
                  "assert f(1) == 1\nassert f(2) == 2\n", timeout=20)
        ck(arm("the refusal is not only the raw syscall: urllib raises too, and the "
               "harness reports it as a verdict (a candidate cannot hide behind its "
               "own try/except because the ERROR line is what the retry sees)",
               "urllib raises here too, on a local refused connect rather than on "
               "egress this box cannot be asked about — the arm keeps the harness's "
               "job, which is to turn the candidate's error into a verdict instead of "
               "a hang or a bare traceback"),
           (not s.ok and "URLError" in s.err and "not permitted" in s.err) if confined
           else (not s.ok and "URLError" in s.err and "PermissionError" not in s.err),
           s.err[:100])
        s = score(_DNS_AT_IMPORT, "assert f(1) == 1\nassert f(2) == 2\n", timeout=20)
        ck(arm("and a HOSTNAME never resolves either — name service is itself outbound, "
               "so the refusal arrives early as gaierror, not as a long timeout",
               "a hostname is NOT shown confined here: the lookup either resolves or "
               "fails as a plain gaierror, and either way the module says unconfined "
               "rather than claiming the early refusal this box did not produce"),
           (not s.ok and "gaierror" in s.err) if confined
           else ((s.ok or "gaierror" in s.err) and "PermissionError" not in s.err
                 and status()["network"] == "unconfined"), s.err[:90])
        s = score(_HOSTILE, f"assert f(1) == 1\nassert touch({sentinel!r}) == 'wrote'\n",
                  timeout=20)
        ck(arm("VECTOR: the suite still RANKS a hostile candidate like any partial "
               "answer — the assert before the refusal passed, so passed/total say 1/2",
               "VECTOR: with no jail the hostile candidate passes 2/2 — the only "
               "honest reading on this box, because a 1/2 here would be a refusal "
               "this kernel never applied"),
           (not s.ok and (s.passed, s.total) == (1, 2)) if confined
           else (s.ok and (s.passed, s.total) == (2, 2)),
           f"{s.passed}/{s.total} {s.err[:60]}")
        if confined:
            ok2, err2 = diagnose(_HOSTILE, f"assert touch({sentinel!r}) == 'wrote'\n",
                                 timeout=20)
        else:
            # The write candidate cannot be the error vector on an unconfined box (it
            # just succeeds), so the same module's socket call stands in for it, aimed
            # at the closed local port for the same reason as above. It is raised
            # INSIDE the assert, which is where the GOT/WANT/ERROR probe lives: an
            # import-time failure would print a bare traceback and report nothing
            # actionable, and that distinction is the claim below.
            ok2, err2 = diagnose(_HOSTILE,
                                 "assert f(1) == 1\nassert dial('127.0.0.1', 1)\n",
                                 timeout=20)
        ck(arm("...and the retry loop still gets actionable feedback about it, in the "
               "same GOT/WANT/ERROR shape every other failure uses",
               "...and the retry loop still gets a GOT/WANT/ERROR verdict for the "
               "candidate that does fail here, in the same shape every other failure "
               "uses — no hang, no empty report"),
           (not ok2 and "ERROR: PermissionError" in err2) if confined
           else (not ok2 and "ERROR:" in err2), err2[:90])
        ck("no hostile candidate hung or crashed the harness: the verdicts above "
           "came back in one run each" if confined else
           "no hostile candidate hung, and none of them reached the real ~/.ssh: the "
           "unconfined arm aimed every write at the throwaway HOME",
           not Path(real_sentinel).exists())

        # --- 3. no collateral damage -----------------------------------------
        ck("a benign candidate verifies exactly as it did without the sandbox",
           score("def f(x):\n    return x * 2\n", "assert f(2) == 4\nassert f(3) == 6\n",
                 timeout=20).ok)
        r = probe("import tempfile, os; p = os.path.join(tempfile.mkdtemp(), 'f');"
                  "open(p, 'w').write('x'); print(os.environ['TMPDIR'] == {r!r},"
                  " tempfile.gettempdir() == {r!r})".format(r=root))
        ck("TMPDIR is retargeted into the root, so a candidate's own temp files "
           "land in the directory we hand it and delete after it",
           r.stdout.strip() == "True True", (r.stdout + r.stderr)[-90:])
        # macOS's own /var -> /private/var. This is the symlink form that
        # actually bites: a profile naming `/var/folders/...' is compared
        # LITERALLY against the kernel's resolved write path, so the exception
        # misses and the candidate cannot write in the directory it was given.
        # (Measured all four ways: only a pattern naming `/tmp' fails, because a
        # symlink at the ROOT of the path is resolved by a different rule.)
        var_form = root.replace("/private/", "/", 1)
        r = run(py + ["-c", "import os; open('via-symlink.txt','w').write('1');"
                            "print(os.path.exists('via-symlink.txt'))"], var_form, 30)
        ck("the root may reach us in the /var symlink form and the write "
           "exception still binds, because profile() realpaths it",
           r.stdout.strip() == "True" and Path(root, "via-symlink.txt").exists(),
           (r.stdout + r.stderr)[-90:])
        r = probe("import os, sys; open('rel.txt','w').write('1'); sys.stdout.write(os.getcwd())")
        ck("a relative write lands in the root and the candidate's cwd IS the root"
           " — without that, 'writable root' would not confine the common case",
           r.stdout.strip() == root and Path(root, "rel.txt").exists(), r.stdout[:80])
        r = probe("import subprocess, sys; p = subprocess.run([sys.executable, '-I', '-c',"
                  " \"open('c.txt','w').write('1'); print('child-ok')\"],"
                  " stdout=subprocess.PIPE, text=True, stderr=subprocess.DEVNULL);"
                  " print(p.stdout.strip(), p.returncode)")
        ck("a candidate that shells out keeps working, and its child inherits the "
           "same confinement (the null device is the profile's one exception)",
           "child-ok 0" in r.stdout, (r.stdout + r.stderr)[-90:])
        r = probe("import os, json; json.dumps({'p': sorted(os.listdir('/usr'))[:2]});"
                  "print('__READS-ALLOWED__')")
        ck("reads stay open, and that is a STATED limit of this sandbox, not an "
           "accident: the clause asks for a writable root, no network and rlimits",
           r.stdout.strip() == "__READS-ALLOWED__", (r.stdout + r.stderr)[-60:])

        # --- 4. the rlimits --------------------------------------------------
        t = time.monotonic()
        r = probe("while True: pass", cpu=2, timeout=30)
        wall = time.monotonic() - t
        ck(arm("RLIMIT_CPU binds THROUGH the wrapper: a busy loop died on the signal in"
               " about its cpu seconds, not at the 30s wall timeout",
               "RLIMIT_CPU binds with no wrapper in front of it here: the busy loop died"
               " on the signal in about its cpu seconds, so the limit is the child's own"
               " and not something the prefix was doing"),
           r.returncode == -24 and 1.5 < wall < 12, f"rc={r.returncode} wall={wall:.2f}s")
        r = probe("import resource; print(resource.getrlimit(resource.RLIMIT_CPU))",
                  cpu=3, timeout=30)
        ck("the child reports the limit it was given (witness from inside the"
           " sandbox, not a number printed from outside it)",
           r.stdout.strip().startswith("(3,"), r.stdout.strip())
        r = probe("import resource; print(resource.getrlimit(resource.RLIMIT_CPU))",
                  timeout=30)
        ck("the default cpu budget is wider than the wall clock by the box's own "
           "width, so a candidate that fans out workers is not killed for our "
           "arithmetic (measured: the wall timeout is what actually fires)",
           r.stdout.strip().startswith(f"({30 * CPU_WIDTH + CPU_SLACK_S},"),
           f"{r.stdout.strip()} vs {30 * CPU_WIDTH + CPU_SLACK_S}")
        big = Path(root, "big.bin")
        r = probe(f"open({str(big)!r},'wb').write(b'x' * 600 * 1024 * 1024);"
                  "print('__PASS__')", timeout=60)
        ck("RLIMIT_FSIZE turns a disk-filling write into an ordinary failure: the"
           " kernel stops the writer — whether this platform surfaces EFBIG to the"
           " interpreter (measured here on macOS) or kills it with SIGXFSZ (Linux) —"
           " and either way the file stays under the cap",
           r.returncode != 0
           and (not big.exists() or big.stat().st_size < 300 * 1024 * 1024)
           and ("File too large" in r.stderr or r.returncode == -signal.SIGXFSZ.value),
           f"rc={r.returncode} {r.stderr.strip()[-70:]}")
        mem = memory_ceiling()
        ck("memory ceiling is what a fresh child answers after TRYING setrlimit,"
           " so the claim tracks the platform instead of a table here",
           mem == _measure_memory_ceiling() and (mem[0] or "ValueError" in mem[1]),
           mem[1])
        st = status()
        ck("status names every layer the clause asks for, including the one this"
           " box cannot do",
           set(st) == {"seatbelt", "writes", "network", "cpu", "fsize", "memory"}
           and (st["memory"].startswith("UNAVAILABLE") or "enforced" in st["memory"]),
           str(st))

        # --- 5. every execution seam goes through here -----------------------
        # `python -m flash.sandbox` runs this file as __main__ AND the seams
        # import it as flash.sandbox — two module objects. Patching this file's
        # globals would spy on a copy nobody calls, so the target is the import.
        import flash.sandbox as SB
        _repo = realpath(Path.cwd())
        seen: dict[str, str] = {}
        real = SB.run

        def spy(argv, r, timeout, **kw):
            seen[sys._getframe(1).f_code.co_name] = realpath(r)
            return real(argv, r, timeout, **kw)

        SB.run = spy
        try:
            import flash.confidence as C
            import flash.debug as D
            from flash.harness import run_test
            ms = score_files({"m.py": "X = 1\n"},
                             "import sys; sys.path.insert(0, '<TMPDIR>')\n"
                             "from m import X\nassert X == 1\nassert X == 2\n",
                             timeout=20)
            run_test("def f(): return 1\n", "assert f() == 1\nassert True\n", timeout=20)
            D._run({"solution.py": "def f():\n    return 1\n"}, "assert f() == 1\n", 20)
            C._seeded_run("def f(): return 1\n", "assert f() == 1\n", 0, 20)
            C.edge_probe("def f(x):\n    return len(x)\n", "assert f('abc') == 3\n")
        finally:
            SB.run = real
        seams = {"run_test", "_probes", "_run", "_seeded_run", "edge_probe"}
        ck("the five candidate-execution seams — harness run_test and _probes,"
           " debug _run, confidence _seeded_run and edge_probe — spawn through"
           " sandbox.run, so the clause is one call site and not a convention",
           seams <= set(seen), f"saw {sorted(seen)}")
        ck("...and each one is given a root of its own, not the caller's cwd",
           all(not v.startswith(_repo) for v in seen.values())
           and len(set(seen.values())) >= 4, str(sorted(set(seen.values()))))
        ck("a multi-file set still imports its sibling module from inside the "
           "sandbox: the <TMPDIR> bootstrap and cwd=root agree, so ranking "
           "survives the move (1/2, not an error-out)",
           (ms.ok, ms.passed, ms.total) == (False, 1, 2),
           f"{ms.passed}/{ms.total} {ms.err[:60]}")

    n_bad = sum(not ok for _, ok, _ in checks)
    st = status()
    print(f"\nsandbox selftest: {len(checks) - n_bad}/{len(checks)} checks passed"
          f"  [seatbelt={st['seatbelt']}, memory={st['memory'][:34]}]")
    return 1 if n_bad else 0


if __name__ == "__main__":                       # pragma: no cover
    if "--selftest" in sys.argv:
        raise SystemExit(run_selftest())
    print(__doc__)
    for k, v in status().items():
        print(f"  {k}: {v}")
