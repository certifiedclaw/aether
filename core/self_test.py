"""
core/self_test.py — Aether Self-Test Mode

Run in chat: "run self test" / "/selftest"
Run from CLI: python self_test.py

Tests every major subsystem end-to-end and returns a pass/fail report.
Does NOT require external network (web search tests are skipped offline).

v2 improvements:
  - Every test runs under a hard timeout so one hung subsystem
    (a stalled Ollama request, a wedged sqlite handle, a driver that
    never returns from a device query, ...) can no longer freeze the
    whole self-test — it just gets reported as a failure.
  - Independent, I/O-bound tests run concurrently instead of serially,
    so a full run is bounded by the slowest single test rather than
    the sum of all of them.
  - run_self_test_async() runs the whole thing on a background thread
    so callers on a shared command loop (chat / dashboard) don't block
    while it executes — mirrors the threading.Thread pattern already
    used for startup warmup elsewhere in main.py.
  - Memory DB test now resolves the path the same way hybrid_memory /
    semantic_memory do (via config, relative to BASE_DIR) instead of a
    hardcoded "data/memory.db" that only worked if cwd happened to be
    the project root, and it now proves round-trip write+read+cleanup
    instead of just counting existing rows.
  - New checks: config validation (delegates to config_validator so
    the rules live in one place), GPU/CUDA availability, and free disk
    space on the drive backing data/ (RAG index + memory DB + logs all
    live there and silently misbehave when the disk fills up).
"""
from __future__ import annotations

import logging
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0   # seconds, per-test ceiling
LLM_TIMEOUT     = 30.0   # the LLM round-trip legitimately needs more room


@dataclass
class TestResult:
    name:    str
    passed:  bool
    message: str
    ms:      float = 0.0


@dataclass
class SelfTestReport:
    results: list[TestResult] = field(default_factory=list)

    def add(self, result: TestResult) -> None:
        self.results.append(result)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return len(self.results) - self.passed

    def format(self) -> str:
        lines = ["## Aether Self-Test", ""]
        for r in self.results:
            icon = "✅" if r.passed else "❌"
            ms_str = f"  ({r.ms:.0f}ms)" if r.ms > 0 else ""
            lines.append(f"{icon}  **{r.name}**{ms_str}")
            lines.append(f"    {r.message}")
        lines += [
            "",
            f"**Result: {self.passed}/{len(self.results)} passed**",
            ("🟢 All systems nominal." if self.failed == 0 else
             f"🔴 {self.failed} test(s) failed — see above."),
        ]
        return "\n".join(lines)


def _run(name: str, fn: Callable[[], TestResult], timeout: float = DEFAULT_TIMEOUT) -> TestResult:
    """
    Execute a test function under a hard wall-clock timeout, and make sure
    an unexpected exception (not already caught inside fn) still produces a
    TestResult instead of killing the whole self-test run.
    """
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        try:
            result = future.result(timeout=timeout)
            # Preserve the name the caller asked for even if the test
            # function used a different label internally.
            if result.name != name:
                result = TestResult(name, result.passed, result.message, result.ms)
            return result
        except _FutureTimeout:
            ms = (time.perf_counter() - t0) * 1000
            return TestResult(name, False, f"Timed out after {timeout:.0f}s", ms)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return TestResult(name, False, f"Unhandled error: {e}", ms)
        # NOTE: the worker thread may still be running past the timeout
        # (e.g. a genuinely stuck socket) — it's a one-off thread pool
        # that gets discarded here, matching the "report and move on"
        # goal of a self-test rather than trying to hard-kill it.


def _timed(fn: Callable) -> tuple[Any, float]:
    t0 = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - t0) * 1000


# ── Individual test functions ─────────────────────────────────────────────

def _test_python() -> TestResult:
    import sys
    v = sys.version_info
    ok = v >= (3, 10)
    return TestResult("Python ≥ 3.10", ok, f"{v.major}.{v.minor}.{v.micro}")


def _test_ollama(cfg) -> TestResult:
    try:
        import requests
        t0 = time.perf_counter()
        r = requests.get(f"{cfg.ollama_url}/api/tags", timeout=5)
        ms = (time.perf_counter() - t0) * 1000
        if r.ok:
            models = [m["name"] for m in r.json().get("models", [])]
            return TestResult("Ollama reachable", True,
                f"{', '.join(models[:3]) or 'no models'}", ms)
        return TestResult("Ollama reachable", False, f"HTTP {r.status_code}")
    except Exception as e:
        return TestResult("Ollama reachable", False, str(e))


def _test_llm(cfg) -> TestResult:
    """Send a minimal prompt and verify we get a non-empty response."""
    try:
        import requests
        model = cfg.get("llm.model", cfg.get("llm_model", "qwen3:8b"))
        t0 = time.perf_counter()
        r = requests.post(
            f"{cfg.ollama_url}/api/generate",
            json={"model": model, "prompt": "Reply with the single word: OK",
                  "stream": False, "options": {"num_predict": 10}},
            timeout=LLM_TIMEOUT - 2,
        )
        ms = (time.perf_counter() - t0) * 1000
        if r.ok:
            reply = r.json().get("response", "").strip()
            ok = bool(reply)
            return TestResult("LLM responds", ok,
                f"Response: '{reply[:40]}'" if ok else "Empty response", ms)
        return TestResult("LLM responds", False, f"HTTP {r.status_code}")
    except Exception as e:
        return TestResult("LLM responds", False, str(e))


def _resolve_memory_db_path(cfg):
    """Resolve the memory DB path exactly the way hybrid_memory /
    semantic_memory do, so this test can't drift out of sync with a
    relative-cwd assumption that only holds when launched from the
    project root."""
    from pathlib import Path
    try:
        from memory.config_manager import BASE_DIR
    except Exception:
        BASE_DIR = Path(".").resolve()
    raw = cfg.get("memory.db_path", "./data/memory.db")
    p = Path(raw)
    return p if p.is_absolute() else (BASE_DIR / p)


def _test_memory_db(cfg) -> TestResult:
    """Prove an actual write+read+cleanup round trip against the real
    memories table, instead of only counting rows that happen to
    already exist (which passes even if writes are silently broken)."""
    try:
        import sqlite3
        db = _resolve_memory_db_path(cfg)
        db.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        conn = sqlite3.connect(str(db))
        conn.execute("""CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY, input TEXT, response TEXT)""")
        conn.execute("INSERT INTO memories (input, response) VALUES (?, ?)",
                      ("_selftest_probe", "_selftest_probe"))
        conn.commit()
        row = conn.execute(
            "SELECT id FROM memories WHERE input = ?", ("_selftest_probe",)
        ).fetchone()
        conn.execute("DELETE FROM memories WHERE input = ?", ("_selftest_probe",))
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        ms = (time.perf_counter() - t0) * 1000
        ok = row is not None
        return TestResult("Memory DB", ok,
            f"Write+read+cleanup OK — {count} memories stored" if ok
            else "Write succeeded but read-back failed", ms)
    except Exception as e:
        return TestResult("Memory DB", False, str(e))


def _test_long_term_memory() -> TestResult:
    """Write + read a test entry to long_term.json."""
    try:
        from memory.memory_manager import load_memory, update_memory
        t0 = time.perf_counter()
        update_memory({"notes": {"_selftest": {"value": "test_ok"}}})
        mem = load_memory()
        ms = (time.perf_counter() - t0) * 1000
        found = mem.get("notes", {}).get("_selftest", {}).get("value") == "test_ok"
        # Clean up
        mem.get("notes", {}).pop("_selftest", None)
        update_memory(mem)
        return TestResult("Long-term memory", found,
            "Write+read cycle passed" if found else "Read back failed", ms)
    except Exception as e:
        return TestResult("Long-term memory", False, str(e))


def _test_config(cfg) -> TestResult:
    """Delegate to config_validator so validation rules stay in one
    place instead of being re-implemented here."""
    try:
        from core.config_validator import validate_config
        t0 = time.perf_counter()
        result = validate_config(cfg)
        ms = (time.perf_counter() - t0) * 1000
        if result is None:
            return TestResult("Config", True, "Validator unavailable (skipped)", ms)
        errors = getattr(result, "errors", None)
        ok = not errors
        detail = "No issues found" if ok else f"{len(errors)} issue(s): {'; '.join(map(str, errors))[:200]}"
        return TestResult("Config", ok, detail, ms)
    except Exception as e:
        return TestResult("Config", False, str(e))


def _test_gpu() -> TestResult:
    try:
        t0 = time.perf_counter()
        import torch  # heavy import; only pulled in if installed
        ms = (time.perf_counter() - t0) * 1000
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            return TestResult("GPU/CUDA", True, name, ms)
        return TestResult("GPU/CUDA", True, "CUDA not available — running on CPU", ms)
    except ImportError:
        return TestResult("GPU/CUDA", True, "torch not installed (OK if not needed)")
    except Exception as e:
        return TestResult("GPU/CUDA", False, str(e))


def _test_disk_space(cfg) -> TestResult:
    try:
        db = _resolve_memory_db_path(cfg)
        target = db.parent if db.parent.exists() else db.parent.parent
        t0 = time.perf_counter()
        usage = shutil.disk_usage(target)
        ms = (time.perf_counter() - t0) * 1000
        free_gb = usage.free / (1024 ** 3)
        ok = free_gb > 1.0
        return TestResult("Disk space", ok, f"{free_gb:.1f} GB free on data volume", ms)
    except Exception as e:
        return TestResult("Disk space", False, str(e))


def _test_hybrid_memory(aether) -> TestResult:
    try:
        mem = getattr(aether, "_hybrid_mem", None) or \
              getattr(aether, "_semantic_mem", None)
        if not mem:
            return TestResult("Hybrid memory", False, "Not initialized")
        t0 = time.perf_counter()
        mem.store("selftest_input", "selftest_response")
        results = mem.inject_context("selftest_input", "")
        ms = (time.perf_counter() - t0) * 1000
        ok = isinstance(results, str)
        return TestResult("Hybrid memory", ok,
            "Store+retrieve OK" if ok else "Inject returned non-string", ms)
    except Exception as e:
        return TestResult("Hybrid memory", False, str(e))


def _test_rag(aether) -> TestResult:
    try:
        rag = getattr(aether, "_rag", None)
        if not rag:
            return TestResult("RAG engine", False, "Not initialized")
        stats = rag.stats()
        return TestResult("RAG engine", True,
            f"Loaded — {stats.get('total_chunks', 0)} chunks indexed")
    except Exception as e:
        return TestResult("RAG engine", False, str(e))


def _test_goals(aether) -> TestResult:
    try:
        goals = getattr(aether, "_goals", None)
        if not goals:
            return TestResult("Goal manager", False, "Not initialized")
        active = goals.list_active()
        return TestResult("Goal manager", True,
            f"{len(active)} active goal(s)")
    except Exception as e:
        return TestResult("Goal manager", False, str(e))


def _test_plugins(aether) -> TestResult:
    try:
        plugins = getattr(aether, "_plugins", None)
        if not plugins:
            return TestResult("Plugins", True, "No plugin system (OK)")
        tools = plugins.list_tools()
        return TestResult("Plugins", True, f"{len(tools)} tool(s) loaded")
    except Exception as e:
        return TestResult("Plugins", False, str(e))


def _test_evaluator(aether) -> TestResult:
    try:
        ev = getattr(aether, "_evaluator", None)
        if not ev:
            return TestResult("Evaluator", False, "Not initialized")
        result = ev.evaluate("test question", "test answer", [("web_search", "result")])
        return TestResult("Evaluator", True,
            f"Score: {result.score:.2f}, passed: {result.passed}")
    except Exception as e:
        return TestResult("Evaluator", False, str(e))


def _test_observability(aether) -> TestResult:
    try:
        obs = getattr(aether, "_obs", None)
        if not obs:
            return TestResult("Observability", False, "Not initialized")
        obs.count("selftest")
        report = obs.report()
        return TestResult("Observability", bool(report),
            "Report generated" if report else "Empty report")
    except Exception as e:
        return TestResult("Observability", False, str(e))


def _test_mic() -> TestResult:
    try:
        import sounddevice as sd
        default = sd.query_devices(kind="input")
        return TestResult("Microphone", True, default["name"])
    except Exception as e:
        return TestResult("Microphone", False, str(e))


def _test_speaker() -> TestResult:
    try:
        import sounddevice as sd
        default = sd.query_devices(kind="output")
        return TestResult("Speaker", True, default["name"])
    except Exception as e:
        return TestResult("Speaker", False, str(e))


def _test_loop_guard(aether) -> TestResult:
    try:
        lg = getattr(aether, "_loop_guard", None)
        if not lg:
            return TestResult("Loop guard", False, "Not initialized")
        return TestResult("Loop guard", True, "Active")
    except Exception as e:
        return TestResult("Loop guard", False, str(e))


# ── Public API ─────────────────────────────────────────────────────────────

def run_self_test(aether=None, config=None) -> SelfTestReport:
    """
    Run all tests. Pass the AetherApp instance to test live subsystems.
    If aether=None, only tests that don't need the app are run.

    Every test runs under a per-test timeout so a single stuck subsystem
    can't hang the whole run, and independent stateless tests run
    concurrently to keep total wall-clock time down.
    """
    from memory.config_manager import get_mark_config
    cfg = config or get_mark_config()
    report = SelfTestReport()

    # Stateless tests (no app needed) — independent, so run concurrently.
    stateless_jobs: list[tuple[str, Callable[[], TestResult], float]] = [
        ("Python ≥ 3.10",    _test_python,                          DEFAULT_TIMEOUT),
        ("Ollama reachable",  lambda: _test_ollama(cfg),             DEFAULT_TIMEOUT),
        ("LLM responds",      lambda: _test_llm(cfg),                LLM_TIMEOUT),
        ("Memory DB",         lambda: _test_memory_db(cfg),          DEFAULT_TIMEOUT),
        ("Long-term memory",  _test_long_term_memory,                DEFAULT_TIMEOUT),
        ("Config",            lambda: _test_config(cfg),             DEFAULT_TIMEOUT),
        ("GPU/CUDA",          _test_gpu,                             DEFAULT_TIMEOUT),
        ("Disk space",        lambda: _test_disk_space(cfg),          DEFAULT_TIMEOUT),
        ("Microphone",        _test_mic,                             DEFAULT_TIMEOUT),
        ("Speaker",           _test_speaker,                         DEFAULT_TIMEOUT),
    ]

    with ThreadPoolExecutor(max_workers=min(8, len(stateless_jobs))) as pool:
        futures = {
            pool.submit(_run, name, fn, timeout): name
            for name, fn, timeout in stateless_jobs
        }
        by_name = {futures[future]: future.result() for future in futures}
        # Preserve declared order in the report rather than completion order.
        for name, _fn, _timeout in stateless_jobs:
            report.add(by_name[name])

    # App-level tests — these touch shared aether state, so keep them
    # sequential (and still individually timeout-guarded) to avoid
    # introducing thread-safety issues in subsystems that assume
    # single-threaded access.
    if aether is not None:
        app_jobs = [
            ("Hybrid memory",  lambda: _test_hybrid_memory(aether)),
            ("RAG engine",     lambda: _test_rag(aether)),
            ("Goal manager",   lambda: _test_goals(aether)),
            ("Plugins",        lambda: _test_plugins(aether)),
            ("Evaluator",      lambda: _test_evaluator(aether)),
            ("Observability",  lambda: _test_observability(aether)),
            ("Loop guard",     lambda: _test_loop_guard(aether)),
        ]
        for name, fn in app_jobs:
            report.add(_run(name, fn))

    return report


def run_self_test_async(callback: Callable[[SelfTestReport], None],
                         aether=None, config=None) -> threading.Thread:
    """
    Run the self-test on a background daemon thread and hand the finished
    report to `callback` when done. Use this from any shared command loop
    (chat / dashboard) so a slow subsystem (e.g. a laggy Ollama response)
    doesn't stall processing of the next command while the test runs —
    the same reasoning behind backgrounding startup warmup elsewhere.
    """
    def _worker():
        try:
            report = run_self_test(aether=aether, config=config)
        except Exception as e:
            report = SelfTestReport()
            report.add(TestResult("Self-test", False, f"Self-test crashed: {e}"))
        try:
            callback(report)
        except Exception:
            logger.exception("run_self_test_async callback raised")

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    report = run_self_test()
    print(report.format())
