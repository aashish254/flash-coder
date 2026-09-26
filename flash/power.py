"""The power governor (§34.1) — system profile defaults, not user settings.

§33.9 invariant 3 promises *hard* caps on RAM/thermals and that the agent
sheds load BEFORE the user notices it. That is only true if something reads
the machine. This module is that something: it samples the OS (battery,
thermal, memory pressure, load, user idle), picks a profile, and hands the
loop a set of caps it must respect.

Design rules:
  * Pure policy. `profile_for(state, need_gb)` is a function of its input —
    the decision table is unit-testable without touching the machine, and
    the same table decides on every host (§33.9 invariant 7: losing any
    single reading degrades, never kills — every probe is optional).
  * Expensive models are the expensive decision. The 30B brain peaks at
    17.3GB on a 32GB box (App. A), so escalation is what gets gated first;
    the 7B fast tier keeps working when everything else is shed.
  * The OS already knows when to be quiet (low-power mode, thermal
    warnings, memory pressure). We read its mind instead of guessing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field

IS_MAC = sys.platform == "darwin"

# Peak Metal footprints measured on the M5 (App. A) — the numbers the
# governor budgets against, in GB.
MODEL_PEAK_GB = {
    "Qwen2.5-Coder-7B-Instruct-4bit": 4.4,
    "Qwen3-30B-A3B-Instruct-2507-4bit": 17.3,
    "Qwen3-Coder-30B-A3B-Instruct-4bit": 17.3,
    "Qwen3-VL-4B-Instruct-4bit": 9.0,
    "gpt-oss-20b-MXFP4-Q8": 12.2,
}
DEFAULT_PEAK_GB = 6.0


def peak_gb(repo: str) -> float:
    """Measured peak memory for a model repo key (fallback: conservative)."""
    key = repo.rstrip("/").split("/")[-1]
    return MODEL_PEAK_GB.get(key, DEFAULT_PEAK_GB)


# ------------------------------------------------------------------ readings

@dataclass
class SystemState:
    """One sample of the machine. Every field may be None = not measurable."""
    on_ac: bool | None = None
    battery_pct: float | None = None
    low_power_mode: bool | None = None        # macOS Low Power Mode is on
    thermal_limit_pct: float | None = None    # <100 == being throttled
    thermal_warning: bool | None = None
    mem_free_pct: float | None = None         # macOS memorystatus_level
    mem_total_gb: float | None = None
    swap_used_gb: float | None = None
    load_per_core: float | None = None
    idle_seconds: float | None = None         # since last keyboard/mouse
    cores: int | None = None
    ts: float = field(default_factory=time.time)


def _run(cmd: list[str], timeout: float = 2.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def _sysctl_int(name: str) -> int | None:
    try:
        out = _run(["sysctl", "-n", name]).strip()
        return int(out) if out.lstrip("-").isdigit() else None
    except Exception:
        return None


def parse_batt(out: str) -> tuple[bool | None, float | None]:
    """(on_ac, battery_percent) from `pmset -g batt`; (None, None) if unreadable."""
    if not out:
        return None, None
    on_ac = True if "AC Power" in out else (False if "Battery Power" in out else None)
    m = re.search(r"(\d{1,3})%", out)
    return on_ac, (float(m.group(1)) if m else None)


def parse_therm(out: str) -> tuple[float | None, bool | None]:
    """(cpu_speed_limit_pct, thermal_warning) from `pmset -g therm`.

    Nominal macOS prints the three 'No ... has been recorded' notes — and the
    positive substring of each is INSIDE the negative sentence, so the negated
    forms are matched FIRST (live bug caught by the selftest: reading the
    nominal note as a warning shed the brain on every run).

    Any recorded warning, or a CPU speed limit below 100%, means the package
    is already giving up performance — the wrong moment to load a 17GB MoE
    (§27.2: cool for hours beats fast for minutes).
    """
    if not out:
        return None, None
    limit = None
    m = re.search(r"CPU_Speed_Limit\s*=\s*(\d+)", out)
    if m:
        limit = float(m.group(1))
    if "No thermal warning level has been recorded" in out:
        warning = False
    elif "No CPU speed limit" in out and "thermal" not in out.lower():
        warning = False
    else:
        warning = bool(re.search(r"thermal warning", out, re.I)) or (limit is not None)
    return limit, warning


def read_power() -> tuple[bool | None, float | None]:
    return parse_batt(_run(["pmset", "-g", "batt"]))


def read_thermal() -> tuple[float | None, bool | None]:
    return parse_therm(_run(["pmset", "-g", "therm"]))


def read_memory() -> tuple[float | None, float | None, float | None]:
    """(mem_free_pct, mem_total_gb, swap_used_gb)."""
    free = _sysctl_int("kern.memorystatus_level")
    total = _sysctl_int("hw.memsize")
    swap_gb = None
    out = _run(["sysctl", "-n", "vm.swapusage"])
    m = re.search(r"used\s*=\s*([\d.]+)M", out)
    if m:
        swap_gb = float(m.group(1)) / 1024.0
    return (float(free) if free is not None else None,
            round(total / 2**30, 1) if total else None,
            swap_gb)


def read_idle() -> float | None:
    """Seconds since the last human input event (§34.3's gate variable)."""
    out = _run(["ioreg", "-l", "-c", "IOHIDSystem"])
    m = re.search(r'"HIDIdleTime"\s*=\s*(\d+)', out)
    return int(m.group(1)) / 1e9 if m else None


def read_state(use_low_power_mode: bool | None = None) -> SystemState:
    """Sample everything. Cheap enough to call per task (~5 subprocesses)."""
    on_ac, pct = read_power()
    limit, warn = read_thermal()
    free, total, swap = read_memory()
    cores = _sysctl_int("hw.logicalcpu") or os.cpu_count()
    try:
        load = os.getloadavg()[0] / max(cores or 1, 1)
    except OSError:
        load = None
    lpm = use_low_power_mode
    if lpm is None and IS_MAC:
        lpm = re.search(r"lowpowermode\s+1", _run(["pmset", "-g"])) is not None
    return SystemState(on_ac=on_ac, battery_pct=pct, low_power_mode=lpm,
                       thermal_limit_pct=limit, thermal_warning=warn,
                       mem_free_pct=free, mem_total_gb=total, swap_used_gb=swap,
                       load_per_core=load, idle_seconds=read_idle(), cores=cores)


# -------------------------------------------------------------- the decision

@dataclass
class Caps:
    """What the loop is allowed to do right now. Read-only contract."""
    profile: str
    max_model_gb: float
    allow_big_tier: bool
    tournament_width: int
    max_tokens_cap: int
    allow_background_work: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def brief(self) -> str:
        return (f"{self.profile}: big-tier={'yes' if self.allow_big_tier else 'NO'} "
                f"max-model={self.max_model_gb}GB width={self.tournament_width} "
                f"background={'yes' if self.allow_background_work else 'no'}")


# Product budgets (§26–§27): the fast tier always fits; the brain does not
# always fit *alongside* the user's workload.
SMALL_TIER_GB = 4.4
HEADROOM = 1.15                    # free must exceed need by 15%


def profile_for(s: SystemState, need_gb: float = SMALL_TIER_GB,
                idle_gate: float = 300.0) -> Caps:
    """The decision table. Ordered by severity: the first hard stop wins.

    Inputs the machine cannot report are skipped, never guessed at — a
    missing reading may cost a capability (no big tier) but must not cost
    correctness.
    """
    reasons: list[str] = []
    shed = False                                  # a hard stop fired
    free_gb = (s.mem_total_gb * s.mem_free_pct / 100.0) if (
        s.mem_free_pct is not None and s.mem_total_gb is not None) else None

    if s.thermal_warning or (s.thermal_limit_pct is not None and s.thermal_limit_pct < 100):
        shed = True
        reasons.append(f"throttled (cpu limit={s.thermal_limit_pct}%, warning={s.thermal_warning})")
    if s.low_power_mode:
        shed = True
        reasons.append("macOS Low Power Mode is on — respect it")
    if not s.on_ac and s.on_ac is not None:
        reasons.append(f"on battery ({s.battery_pct}%)")
        if s.battery_pct is not None and s.battery_pct < 25:
            shed = True
            reasons.append("battery below 25%")
    if free_gb is not None and free_gb < need_gb * HEADROOM:
        reasons.append(f"free memory {free_gb:.1f}GB < {need_gb * HEADROOM:.1f}GB needed")
        shed = True
    if s.load_per_core is not None and s.load_per_core > 2.0:
        shed = True
        reasons.append(f"load {s.load_per_core:.1f}/core — user is busy")

    big_ok = not shed
    if big_ok:
        profile = "maximum-performance" if (s.on_ac and not reasons) else "balanced"
    else:
        profile = "low-power"

    # The memory ceiling is absolute, not just a big-tier veto: if the box
    # cannot hold the FAST tier with headroom, the honest answer is that the
    # agent should not load a model at all right now (§33.9 invariant 3).
    ceiling = 17.3 if big_ok else SMALL_TIER_GB
    if free_gb is not None:
        ceiling = min(ceiling, max(0.0, round(free_gb / HEADROOM, 1)))

    idle = s.idle_seconds if s.idle_seconds is not None else 0.0
    background = bool(s.on_ac) and idle >= idle_gate and not shed
    if s.on_ac is None:
        background = False                        # cannot prove AC -> never train
    width = 4 if profile == "maximum-performance" else (2 if profile == "balanced" else 1)
    observed = ", ".join(f"{k}={v}" for k, v in (
        ("ac", s.on_ac), ("battery", s.battery_pct), ("mem-free", s.mem_free_pct),
        ("load/core", s.load_per_core and round(s.load_per_core, 2)),
        ("cpu-limit", s.thermal_limit_pct)) if v is not None)
    return Caps(profile=profile,
                max_model_gb=ceiling,
                allow_big_tier=big_ok,
                tournament_width=width,
                max_tokens_cap=4096 if big_ok else 2048,
                allow_background_work=background,
                reasons=reasons or [f"no shed signal ({observed or 'no readings'})"])


def allow_model(repo: str, caps: Caps) -> tuple[bool, str]:
    """Can this model be loaded under the current caps? ('', reason)."""
    need = peak_gb(repo)
    if need <= caps.max_model_gb:
        return True, ""
    return False, (f"{caps.profile} profile: {repo.split('/')[-1]} needs "
                   f"~{need}GB peak, cap is {caps.max_model_gb}GB "
                   f"({'; '.join(caps.reasons)})")


_CACHE: tuple[float, SystemState, Caps] | None = None


def governor(need_gb: float = SMALL_TIER_GB, ttl: float = 5.0,
             refresh: bool = False) -> tuple[SystemState, Caps]:
    """Sampled state + caps, cached for `ttl` seconds (cheap in hot loops)."""
    global _CACHE
    now = time.monotonic()
    if not refresh and _CACHE and now - _CACHE[0] < ttl:
        return _CACHE[1], _CACHE[2]
    st = read_state()
    caps = profile_for(st, need_gb)
    _CACHE = (now, st, caps)
    return st, caps


# ------------------------------------------------------------------ CLI view

def report(need_gb: float = SMALL_TIER_GB) -> str:
    st, caps = governor(need_gb, refresh=True)
    lines = [f"profile: {caps.profile}", ""]
    fields = [("drawing from", "AC" if st.on_ac else "battery" if st.on_ac is not None else "?"),
              ("battery", f"{st.battery_pct}%" if st.battery_pct is not None else "?"),
              ("low-power mode", st.low_power_mode),
              ("cpu speed limit", f"{st.thermal_limit_pct}%" if st.thermal_limit_pct is not None else "none"),
              ("thermal warning", st.thermal_warning),
              ("memory free", f"{st.mem_free_pct}% of {st.mem_total_gb}GB"),
              ("swap used", f"{st.swap_used_gb}GB" if st.swap_used_gb is not None else "?"),
              ("load per core", f"{st.load_per_core:.2f}" if st.load_per_core is not None else "?"),
              ("idle", f"{st.idle_seconds:.0f}s" if st.idle_seconds is not None else "?"),
              ("cores", st.cores)]
    for k, v in fields:
        lines.append(f"  {k:<18} {v}")
    lines += ["", f"caps: {caps.brief()}", "why:"]
    lines += [f"  - {r}" for r in caps.reasons]
    for name, repo in (("fast tier", "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"),
                       ("brain", "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit")):
        ok, why = allow_model(repo, caps)
        lines.append(f"  {name:<10} {'LOAD OK' if ok else 'SHED':8} (~{peak_gb(repo)}GB)"
                     + ("" if ok else f"  {why}"))
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest

_S = SystemState


def _table() -> list[tuple[str, SystemState, bool, str]]:
    """(case, state, expect big-tier allowed, expect profile)."""
    ac = dict(on_ac=True, battery_pct=100.0, mem_free_pct=80.0, mem_total_gb=32.0,
              load_per_core=0.4, idle_seconds=5.0, cores=10)
    return [
        ("plugged in, cool, headroom", _S(**ac), True, "maximum-performance"),
        ("battery 60%, plenty of RAM",
         _S(**{**ac, "on_ac": False, "battery_pct": 60.0}), True, "balanced"),
        ("battery 18%",
         _S(**{**ac, "on_ac": False, "battery_pct": 18.0}), False, "low-power"),
        ("macOS low-power mode on",
         _S(**{**ac, "low_power_mode": True}), False, "low-power"),
        ("thermally throttled to 70%",
         _S(**{**ac, "thermal_limit_pct": 70.0}), False, "low-power"),
        ("thermal warning recorded",
         _S(**{**ac, "thermal_warning": True}), False, "low-power"),
        ("memory pressure: 12% free of 32GB",
         _S(**{**ac, "mem_free_pct": 12.0}), False, "low-power"),
        ("user hammering the machine (load 3/core)",
         _S(**{**ac, "load_per_core": 3.0}), False, "low-power"),
        ("no readings at all (non-mac host)", _S(), True, "balanced"),
    ]


def run_selftest(verbose: bool = True) -> int:
    """The decision table + peak-memory budgets. Pure, offline, deterministic."""
    checks: list[tuple[str, bool, str]] = []

    def check(label, ok, detail=""):
        checks.append((label, bool(ok), detail))
        if verbose:
            print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"  {detail}" if detail else ""))

    # 0. the probes parse REAL captured output, including the negated notes
    nominal = ("Note: No thermal warning level has been recorded\n"
               "Note: No performance warning level has been recorded\n"
               "Note: No CPU power status has been recorded")
    cases = [
        ("therm nominal (all 'No ... recorded')", parse_therm(nominal), (None, False)),
        ("therm silent / non-mac", parse_therm(""), (None, None)),
        ("therm warning recorded",
         parse_therm("Thermal warning level recorded\nCPU_Speed_Limit  =  78"),
         (78.0, True)),
        ("cpu speed limit alone counts as throttling",
         parse_therm("CPU_Speed_Limit  =  62"), (62.0, True)),
        ("batt on AC", parse_batt("Now drawing from 'AC Power'\n"
                                  " -InternalBattery-0\t100%; charged; present: true"),
         (True, 100.0)),
        ("batt draining", parse_batt("Now drawing from 'Battery Power'\n"
                                     " -InternalBattery-0\t55%; discharging; 3:36 remaining"),
         (False, 55.0)),
    ]
    for label, got, want in cases:
        check(f"parse: {label}", got == want, f"{got} (want {want})")

    for case, st, want_big, want_profile in _table():
        caps = profile_for(st)
        check(f"table: {case}", caps.allow_big_tier == want_big and caps.profile == want_profile,
              f"-> {caps.profile}, big={caps.allow_big_tier}, why={caps.reasons}")

    # shedding must cost the expensive option only: the fast tier survives
    cold = profile_for(_S(on_ac=False, battery_pct=10.0, mem_free_pct=80.0,
                          mem_total_gb=32.0, load_per_core=0.5, idle_seconds=0,
                          cores=10, thermal_warning=True))
    check("shed keeps the fast tier alive",
          allow_model("mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", cold)[0]
          and not allow_model("mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit", cold)[0],
          cold.brief())
    ok, why = allow_model("mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit", cold)
    check("shed reason is stated in numbers", "17.3GB" in why and "cap is" in why, why)

    # ... but a genuinely full machine stops the agent entirely (invariant 3)
    tight = profile_for(_S(on_ac=True, battery_pct=100.0, mem_free_pct=6.0,
                           mem_total_gb=32.0, load_per_core=0.3, idle_seconds=1.0, cores=10))
    fast_ok, fast_why = allow_model("mlx-community/Qwen2.5-Coder-7B-Instruct-4bit", tight)
    check("1.9GB free vetoes even the 4.4GB fast tier", not fast_ok, fast_why or tight.brief())

    # §34.3's precondition lives in the same table: no AC -> no background work
    check("background work needs AC + idle user",
          profile_for(_S(on_ac=True, idle_seconds=600.0, mem_free_pct=80.0,
                         mem_total_gb=32.0, load_per_core=0.2, battery_pct=100.0,
                         cores=10)).allow_background_work
          and not profile_for(_S(on_ac=True, idle_seconds=12.0, mem_free_pct=80.0,
                                 mem_total_gb=32.0, load_per_core=0.2,
                                 battery_pct=100.0, cores=10)).allow_background_work
          and not profile_for(_S(on_ac=False, idle_seconds=600.0, battery_pct=90.0,
                                 mem_free_pct=80.0, mem_total_gb=32.0,
                                 load_per_core=0.2, cores=10)).allow_background_work)

    # tournament width tracks the profile (§33.4 runs only when cool)
    widths = [profile_for(_S(**{"on_ac": True, "battery_pct": 100.0, "mem_free_pct": 80.0,
                                "mem_total_gb": 32.0, "load_per_core": 0.3,
                                "idle_seconds": 1.0, "cores": 10})).tournament_width,
              profile_for(_S(on_ac=False, battery_pct=70.0)).tournament_width,
              profile_for(_S(on_ac=False, battery_pct=70.0, thermal_warning=True)).tournament_width]
    check("tournament width degrades with the profile", widths == [4, 2, 1], str(widths))

    # live probe: every reading must parse or be None, never crash
    st = read_state()
    check("probe: live sample returns a usable state",
          st.cores is not None and st.mem_total_gb is not None,
          f"cores={st.cores} mem={st.mem_total_gb}GB free={st.mem_free_pct}% "
          f"on_ac={st.on_ac} idle={st.idle_seconds and round(st.idle_seconds)}s")
    st2, caps = governor(refresh=True)
    check("probe: governor() agrees with the table",
          caps.profile == profile_for(st2).profile, caps.brief())

    n_ok = sum(ok for _, ok, _ in checks)
    if verbose:
        print(f"\npower selftest: {n_ok}/{len(checks)} checks passed")
    return 0 if n_ok == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(run_selftest())
