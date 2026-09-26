"""Why did the four live ambient windows produce no accepted additive draft?

The windows failed on this project's real package map with THREE unmapped modules
(~45 lines to echo). The offline fixture passes with ONE unmapped module in a
4-line map. Those two differ in two variables at once, so neither run isolates a
cause — and the first two arms of this probe, measured on 2026-09-26, refuted the
obvious guess: a 52-line map with one module to add produced a verified draft on
the model's first try in 29.8s.

So this is a 2×2 over the two candidate causes:

    echo length  = how many lines the `# file:` fence makes the model re-type
    insert count = how many separate map entries it must place correctly

A 3x1, B 52x1, C 3x3, D 52x3. Every arm is a fresh git repo whose only red check
is the same `drift:...:unmapped` finding, under the same oracle and the same
additive clause; one model load serves all four arms.

    python benchmarks/ambient_echo_probe.py [--dry]

Measured with mlx-community/Qwen2.5-Coder-7B-Instruct-4bit, temp 0.0 on attempt 0
then 0.8, max_tokens 1024, the gate opened by a synthetic idle+AC profile state.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flash import ambient, harness, power  # noqa: E402

MODEL = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"

SMALL_MAP = ('"""Demo package:\n'
             '  M1: flash.known    (the module the map does name)\n'
             '"""\n')
NAMES = {1: ("zeta",), 3: ("zeta", "eta", "theta")}


def commit_all(root: Path) -> None:
    for cmd in (["init", "-q", "-b", "main", str(root)],
                ["-C", str(root), "config", "user.email", "a@b.c"],
                ["-C", str(root), "config", "user.name", "probe"],
                ["-C", str(root), "add", "-A"],
                ["-C", str(root), "commit", "-q", "-m", "baseline"]):
        subprocess.run(["git", *cmd], check=True, capture_output=True)


def make_repo(root: Path, big: bool, inserts: int) -> Path:
    """A git repo whose only drift finding covers `inserts` unmapped module(s).
    `big` copies this project's real `flash/` (map and every module) so the echo is
    the real one; otherwise the map is 3 lines. The extra module files are trivial
    and never imported — the check reads only their NAMES."""
    (root / "flash").mkdir(parents=True)
    if big:
        for p in (ambient.ROOT / "flash").glob("*.py"):
            shutil.copy(p, root / "flash" / p.name)
    else:
        (root / "flash" / "__init__.py").write_text(SMALL_MAP)
        (root / "flash" / "known.py").write_text("KNOWN = 1\n")
    for m in NAMES[inserts]:
        (root / "flash" / f"{m}.py").write_text(f"{m.upper()}_MARKER = 1\n")
    commit_all(root)
    return root


def idle():
    return power.SystemState(on_ac=True, battery_pct=100.0, low_power_mode=False,
                             thermal_limit_pct=100.0, thermal_warning=False,
                             mem_free_pct=60.0, mem_total_gb=24.0, swap_used_gb=0.0,
                             load_per_core=0.2, idle_seconds=600.0, cores=8)


def inspect(repo: Path, tmp: Path, generate) -> None:
    """One generation per unmapped name, analysed as text: WHERE did the model put
    it? The 2x2 says the tier can do this edit; the live windows on the real repo
    say it did not. This distinguishes 'the answer is wrong' from 'the answer is
    right but written in a form the map parser does not read', which is the finding
    that decides whether R-7.2 needs a bigger model or a placement seam.
    """
    red = [f for f in ambient.scan(ambient.head_tree(repo)) if f.kind == "drift"
           and f.id.endswith(":unmapped")]
    if not red:
        print("[inspect] no unmapped-module finding at HEAD — nothing to diagnose")
        return
    f = red[0]
    wanted = f.detail.split(": ", 1)[1].split(", ")
    out = generate([{"role": "user", "content": f.prompt}], 0)
    files = harness.extract_files(out, expected=f.files)
    print(f"[inspect] {repo}\n    unmapped: {', '.join(wanted)}")
    print(f"    response: {len(out)} chars, {len(out.splitlines())} lines, "
          f"`# file:` header in {sorted(files)}")
    probe = tmp / "inspect"
    probe.mkdir(parents=True, exist_ok=True)
    (probe / "flash").mkdir(parents=True, exist_ok=True)
    (probe / "flash" / "__init__.py").write_text(
        files.get("flash/__init__.py", ""))
    still = [x.id for x in ambient.check_drift(probe)]
    print(f"    re-run of the drift check on the extracted text alone: {still or 'clean'}")
    doc_lines = (files.get("flash/__init__.py", "").splitlines())
    for name in wanted:
        hits = [(i + 1, l.strip()) for i, l in enumerate(doc_lines)
                if name in l and l.strip() != original_line(name, repo)]
        forms = [("flash." + name in l, "`" + name + "`" in l, name + ".py" in l)
                 for _, l in hits]
        print(f"    {name}: {len(hits)} line(s) mention it; "
              f"recognized form (flash.x or backticked x) = "
              f"{any(a or b for a, b, _ in forms)}")
        for ln, l in hits[:3]:
            print(f"        line {ln}: {l[:120]}")


def original_line(name: str, repo: Path) -> str:
    """A line that already existed is not evidence of placement; return one exact
    existing line mentioning `name` so the caller can exclude the echo."""
    src = (repo / "flash" / "__init__.py").read_text().splitlines()
    return next((l.strip() for l in src if name in l), "\0nomatch")


tmp = Path(tempfile.mkdtemp())
dry = "--dry" in sys.argv
gen = None if dry else ambient.make_generator(MODEL)
rows = []

if "--inspect" in sys.argv:
    for target in (Path(ambient.ROOT),
                   make_repo(tmp / "repo-D", True, 3)):
        inspect(target, tmp, gen)
    sys.exit(0)

for label, big, inserts in [("A", False, 1), ("B", True, 1),
                            ("C", False, 3), ("D", True, 3)]:
    repo = make_repo(tmp / f"repo-{label}", big, inserts)
    echo = len((repo / "flash" / "__init__.py").read_text().splitlines())
    red = [f for f in ambient.scan(ambient.head_tree(repo)) if f.kind == "drift"]
    print(f"\n=== {label}: echo {echo} lines x {inserts} insert(s) "
          f"-> {len(red)} drift finding(s)")
    if len(red) != 1:
        print("    probe premise broken: expected exactly one drift finding")
        rows.append((label, echo, inserts, None, None, "premise broken"))
        continue
    f = red[0]
    src = f.prompt.split("```python\n", 1)[1].rsplit("\n```", 1)[0]
    print(f"    finding: {f.id} | echo length in prompt: {len(src.splitlines())} "
          f"lines | additive={f.additive}")
    rec = ambient.run(repo=repo, home=tmp / f"home-{label}", generate=gen,
                      limit=1, max_attempts=3, verbose=True, dry=dry, state=idle())
    for d in rec["drafts"]:
        diff = Path(d["diff"]).read_text() if d["diff"] else ""
        rows.append((label, echo, inserts, d["verified"], d["attempts"], d["note"]))
        damage = "yes" if "No newline at end of file" in diff else "no"
        print(f"    -> verified={d['verified']} attempts={d['attempts']} "
              f"seconds={d['seconds']:.1f} diff_lines={len(diff.splitlines())} "
              f"de_newlined={damage} | {d['note'][:160]}")

print("\n=== matrix (verified drafts; None = no draft reached the oracle)")
for label, echo, inserts, verified, attempts, note in rows:
    print(f"  {label}  echo={echo:>2}  inserts={inserts}  verified={verified}  "
          f"attempts={attempts}  {note[:90]}")
