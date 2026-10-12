"""tools/verify_local.py -- one-shot health check for a real Aether install.

Run from the aether folder:   python tools\\verify_local.py
Writes logs/verify_report.txt -- upload that single file to get it reviewed.

Does four things, no model needed:
  1. environment (Python/OS, key package versions, Ollama reachable + models)
  2. py_compile on every .py file
  3. pytest (if installed)
  4. headless replay of tests/live_test_cases.json (23 prompts) through the real
     pipeline, appends logs/live_report.md, then GRADES every reply (PASS/FAIL per
     case, plus new crash reports and stderr tracebacks). Falls back to the old
     tests/live_test_prompts_v2.txt if the cases file is missing.

Secrets: API keys are never read into the report; only whether they are set.
Options: --skip-replay   --replay-timeout SECONDS (default 900)   --keep-memory (don't isolate memory)
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools import live_grader, req_check  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "logs" / "verify_report.txt"
PROMPTS = ROOT / "tests" / "live_test_prompts_v2.txt"
LIVE_REPORT = ROOT / "logs" / "live_report.md"
PACKAGES = [
    "PyQt6", "faster-whisper", "sounddevice", "numpy", "requests", "pyautogui",
    "pycaw", "comtypes", "pygetwindow", "psutil", "send2trash", "edge-tts",
    "kokoro", "openwakeword", "ddgs", "duckduckgo-search", "pytest", "fastapi",
]
SAFE_CONFIG_KEYS = ["llm_provider", "echo_guard", "wake_word_enabled", "gemini_for_tools"]
MAX_TAIL = 6000

out: list[str] = []


def section(title: str) -> None:
    out.append("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def line(s: str = "") -> None:
    out.append(s)
    print(s)


def tail(text: str, n: int = MAX_TAIL) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "...[cut]...\n" + text[-n:]


def run(cmd: list[str], timeout: int, env: dict | None = None, stdin_path: Path | None = None):
    t0 = time.time()
    try:
        fh = open(stdin_path, "rb") if stdin_path else None
        try:
            p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                               env=env, stdin=fh, encoding="utf-8", errors="replace")
        finally:
            if fh:
                fh.close()
        return p.returncode, p.stdout, p.stderr, time.time() - t0
    except subprocess.TimeoutExpired as e:
        so = (e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else e.stdout) or ""
        se = (e.stderr.decode("utf-8", "replace") if isinstance(e.stderr, bytes) else e.stderr) or ""
        return -999, so, se + f"\n[TIMEOUT after {timeout}s]", time.time() - t0
    except Exception as e:  # noqa: BLE001
        return -1, "", f"{type(e).__name__}: {e}", time.time() - t0


def _other_aether_processes() -> list[int]:
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return []
    me = {os.getpid(), os.getppid()}
    found = []
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if p.info["pid"] not in me and "python" in (p.info.get("name") or "").lower() \
                    and ("main.py" in cmd or "verify_local" in cmd):
                found.append(p.info["pid"])
        except Exception:  # noqa: BLE001
            continue
    return found


def _resource_snapshot() -> None:
    """RAM, models Ollama has loaded (and how much sits in VRAM), and the
    heaviest processes. A slow or timed-out replay is almost always the machine
    being busy, and this makes that visible in the report."""
    line("Load      :")
    try:
        import psutil
        vm = psutil.virtual_memory()
        line(f"  RAM {vm.percent:.0f}% used ({vm.available / 2**30:.1f} GB free of {vm.total / 2**30:.1f} GB)")
        if vm.percent >= 85:
            line("  !! RAM is nearly full -- Ollama will spill to CPU/disk and stall. Close games/browsers first.")
        procs = []
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                procs.append((p.info["memory_info"].rss, p.info["name"] or "?"))
            except Exception:  # noqa: BLE001
                continue
        top = sorted(procs, reverse=True)[:5]
        line("  top memory: " + ", ".join(f"{n} {r / 2**30:.1f}GB" for r, n in top))
    except Exception as e:  # noqa: BLE001
        line(f"  psutil unavailable ({type(e).__name__})")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=4) as r:
            loaded = json.load(r).get("models", [])
        if not loaded:
            line("  ollama: no model loaded yet")
        for m in loaded:
            size, vram = m.get("size", 0), m.get("size_vram", 0)
            pct = f"{100 * vram / size:.0f}%" if size else "?"
            line(f"  ollama loaded: {m.get('name')}  {size / 2**30:.1f} GB, {pct} on GPU")
            if size and vram < size * 0.9:
                line("  !! model is partly on CPU -- expect slow replies (free VRAM/RAM or use a smaller model)")
        if len(loaded) > 1:
            line("  !! more than one model loaded at once -- they compete for VRAM; run: ollama stop <name>")
    except Exception:  # noqa: BLE001
        pass


ENV_BLOCKER: str | None = None   # set by check_env(): a problem that makes the live replay pointless


def check_env() -> None:
    section("1. ENVIRONMENT")
    line(f"Python   : {sys.version.split()[0]}  ({sys.executable})")
    line(f"Platform : {platform.platform()}")
    line(f"Folder   : {ROOT}")
    line("Packages :")
    for name in PACKAGES:
        try:
            line(f"  {name:20s} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            line(f"  {name:20s} -- not installed")
    line("Requirements :")
    for _l in req_check.render(req_check.missing_requirements(ROOT / "requirements.txt")):
        line(_l)
    global ENV_BLOCKER
    ENV_BLOCKER = None
    cfg_path = ROOT / "config" / "api_keys.json"
    line("Config    :")
    cfg: dict = {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("not a JSON object")
        for k in SAFE_CONFIG_KEYS:
            line(f"  {k:20s} {cfg.get(k, '(default)')}")
    except FileNotFoundError:
        cfg = {}
        ENV_BLOCKER = (f"config/{cfg_path.name} is MISSING. Zips leave it out on purpose (it holds your keys): "
                       "copy it over from your previous Aether folder. Aether is running on built-in defaults.")
        line(f"  !! {ENV_BLOCKER}")
    except Exception as e:  # noqa: BLE001
        cfg = {}
        line(f"  could not read {cfg_path.name}: {type(e).__name__}")
    try:                               # where each key comes from -- shown even with no config file
        from core import credentials as _cred
        for _n in _cred.SECRET_NAMES:
            _src = _cred.source_of(_n, cfg)
            line(f"  {_n:20s} {'set (' + _src + ')' if _src else 'not set'}")
    except Exception as e:  # noqa: BLE001
        line(f"  (could not check keys: {type(e).__name__})")
    line("Ollama   :")
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=4) as r:
            models = [m.get("name") for m in json.load(r).get("models", [])]
        line(f"  reachable, {len(models)} model(s): {', '.join(models) or 'none'}")
        if str(cfg.get("llm_provider", "ollama")).lower() == "ollama":
            try:
                from core.llm_client import _DEFAULTS as _D
                default_model = _D.get("llm_model", "")
            except Exception:  # noqa: BLE001
                default_model = ""
            want = cfg.get("llm_model") or default_model
            if want and want not in models and f"{want}:latest" not in models:
                close = [m for m in models if m and m.split(":")[0] == want.split(":")[0]]
                hint = (f"installed variants: {', '.join(close)} -- set llm_model to one of them" if close
                        else f"run: ollama pull {want}")
                msg = f"the model Aether will use, '{want}', is NOT installed ({hint})"
                line(f"  !! {msg}")
                ENV_BLOCKER = ENV_BLOCKER or msg
    except Exception as e:  # noqa: BLE001
        line(f"  NOT reachable ({type(e).__name__}) -- start Ollama before the replay")
        ENV_BLOCKER = ENV_BLOCKER or "Ollama is not reachable"
    _resource_snapshot()
    others = _other_aether_processes()
    if others:
        line(f"  !! {len(others)} other Aether/main.py process(es) already running (PIDs {others}). "
             "Close them first: concurrent runs share memory and logs and scramble the replay.")


def check_compile() -> None:
    section("2. COMPILE")
    files = [p for p in ROOT.rglob("*.py")
             if not any(part in {".git", "__pycache__", "venv", ".venv", "node_modules"} for part in p.parts)]
    bad = []
    for p in files:
        rc, _, err, _ = run([sys.executable, "-m", "py_compile", str(p)], 60)
        if rc != 0:
            bad.append((p.relative_to(ROOT), tail(err, 500)))
    line(f"{len(files)} files checked, {len(bad)} failed")
    for p, err in bad:
        line(f"  FAIL {p}\n    {err}")


def check_pytest() -> None:
    section("3. PYTEST")
    rc, so, se, dt = run([sys.executable, "-m", "pytest", "tests", "-q", "--no-header",
                          "-p", "no:cacheprovider"], 600)
    if "No module named pytest" in se:
        line("pytest not installed (pip install pytest) -- skipped")
        return
    line(f"exit code {rc}, {dt:.0f}s")
    line(tail(so + "\n" + se, 5000))


class _MemoryIsolation:
    """Run the replay against EMPTY memory, then put the user's real memory back.

    Without this, the replay's fixed prompts retrieve earlier (possibly bad)
    answers from memory and the model copies them, so results depend on past
    runs instead of the current code. Snapshots live in logs/.verify_snapshot/
    and are restored in __exit__ even if the replay times out or crashes.
    """
    FILES = ["data/memory.db", "memory/long_term.json"]

    def __enter__(self):
        import shutil
        self.snap = ROOT / "logs" / ".verify_snapshot"
        self.snap.mkdir(parents=True, exist_ok=True)
        self.saved = []
        for rel in self.FILES:
            src = ROOT / rel
            if src.exists():
                dst = self.snap / rel.replace("/", "__")
                shutil.copy2(src, dst)
                self.saved.append((src, dst))
        db = ROOT / "data" / "memory.db"
        if db.exists():
            import sqlite3
            con = sqlite3.connect(db)
            try:
                con.execute("delete from memories")
                con.commit()
            except Exception:  # noqa: BLE001
                pass
            finally:
                con.close()
        lt = ROOT / "memory" / "long_term.json"
        if lt.exists():
            lt.write_text("{}", encoding="utf-8")
        line("memory isolated for the replay (your real memory is restored afterwards)")
        return self

    def __exit__(self, *exc):
        import shutil
        for src, dst in self.saved:
            try:
                shutil.copy2(dst, src)
            except Exception as e:  # noqa: BLE001
                line(f"!! could not restore {src}: {e} -- copy it back from {dst}")
        line("real memory restored")
        return False


def check_replay(timeout: int, isolate: bool = True, force: bool = False) -> bool:
    """Run the headless replay and grade it. Returns True if every check passed."""
    cases = None
    prompts = PROMPTS
    if live_grader.CASES.exists():
        cases = live_grader.load_cases()
        prompts = live_grader.write_prompts(cases, ROOT / "logs" / "live_prompts_v3.txt")
    section(f"4. HEADLESS REPLAY ({len(cases) if cases else 15} live prompts)")
    if ENV_BLOCKER and not force:
        line(f"SKIPPED: {ENV_BLOCKER}")
        line("Fix that and re-run (or pass --force-replay to run anyway). Without it every turn fails and "
             "the run just waits for the timeout.")
        return False
    if not prompts.exists():
        line(f"missing {prompts} -- skipped")
        return True
    env = dict(os.environ, AETHER_HEADLESS="1", AETHER_HEADLESS_STDIN="1",
               AETHER_HEADLESS_STDIN_TIMEOUT="120", PYTHONIOENCODING="utf-8",
               AETHER_SKIP_MAINTENANCE="1")  # a test run must not commit/push report files
    old = LIVE_REPORT.stat().st_mtime if LIVE_REPORT.exists() else 0
    crashes_before = live_grader.count_crashes()
    started = time.time()
    line(f"running (up to {timeout}s)...")
    if isolate:
        with _MemoryIsolation():
            rc, so, se, dt = run([sys.executable, "main.py"], timeout, env=env, stdin_path=prompts)
    else:
        rc, so, se, dt = run([sys.executable, "main.py"], timeout, env=env, stdin_path=prompts)
    line(f"exit code {rc}, {dt:.0f}s")
    line("--- console tail ---\n" + tail(so))
    if se.strip():
        line("--- stderr tail ---\n" + tail(se, 3000))
    if LIVE_REPORT.exists() and LIVE_REPORT.stat().st_mtime > old:
        line("--- logs/live_report.md ---\n" + tail(LIVE_REPORT.read_text(encoding="utf-8", errors="replace"), 30000))
    else:
        line("logs/live_report.md was NOT updated by this run")
    if not cases:
        return True
    section("5. GRADING (tests/live_test_cases.json)")
    turns = live_grader.load_turns(started - 1)
    text, ok = live_grader.render(
        live_grader.grade(cases, turns),
        new_crashes=max(0, live_grader.count_crashes() - crashes_before),
        stderr_tracebacks=(se or "").count("Traceback (most recent call last)"),
    )
    line(text)
    section("6. WHOLE-RUN CHECKS (all turns, stderr, tiers)")
    text2, ok2 = live_grader.render_overall(live_grader.grade_overall(cases, turns, stderr=se or ""))
    line(text2)
    return ok and ok2


def main() -> int:
    args = sys.argv[1:]
    timeout = 900
    if "--replay-timeout" in args:
        timeout = int(args[args.index("--replay-timeout") + 1])
    check_env()
    check_compile()
    check_pytest()
    ok = True
    if "--skip-replay" not in args:
        ok = check_replay(timeout, isolate="--keep-memory" not in args,
                          force="--force-replay" in args)
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(out), encoding="utf-8")
    print(f"\nDone. Upload this file: {REPORT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
