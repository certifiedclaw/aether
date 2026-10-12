#!/usr/bin/env python3
"""
stress_test.py — Aether Comprehensive Stress Test
"""

import sys
import os
import time
import json
import tempfile
import shutil
import sqlite3
from pathlib import Path
from contextlib import contextmanager
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from memory.config_manager import get_mark_config, save_config
from memory.hybrid_memory import HybridMemory
from memory.rag import RAGEngine
from security.tokens import SecurityManager
from actions.extra_tools import run_extra_tool
from actions.file_controller import create_file, read_file, delete_file
from core.safety import get_safety

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
DEFAULT_ITERATIONS = 100
VERBOSE = False

def log(msg):
    if VERBOSE:
        print(f"[STRESS] {msg}")

# -----------------------------------------------------------------------------
# Fixtures: Temporary environment
# -----------------------------------------------------------------------------
@contextmanager
def temp_env():
    """Create a temporary directory with isolated config, data, logs."""
    with tempfile.TemporaryDirectory(prefix="aether_stress_") as tmp:
        env = {
            "ROOT": Path(tmp),
            "CONFIG_DIR": Path(tmp) / "config",
            "DATA_DIR": Path(tmp) / "data",
            "LOGS_DIR": Path(tmp) / "logs",
            "DB_CONNECTIONS": [],  # Track open DB connections for cleanup
        }
        for d in env.values():
            if isinstance(d, Path) and not isinstance(d, list):
                d.mkdir(parents=True, exist_ok=True)
        # Create a minimal config
        config = {
            "stt_engine": "whisper",
            "stt_model": "base",
            "stt_language": "auto",
            "llm_provider": "ollama",
            "llm_url": "http://localhost:11434",
            "llm_model": "qwen3:8b",
            "tts_engine": "edgetts",
            "tts_voice": "en-US-GuyNeural",
            "tts_speed": "1.0",
            "elevenlabs_api_key": "",
            "memory.db_path": str(env["DATA_DIR"] / "memory.db"),
            "memory.chat_history_limit": 100,
            "memory.relevance_threshold": 0.35,
            "rag.index_path": str(env["DATA_DIR"] / "rag_index"),
            "rag.chunk_size": 500,
            "rag.top_k": 5,
            "plugins.plugin_dir": str(env["DATA_DIR"] / "plugins"),
            "hotkey.toggle": "ctrl+alt+j",
            "safety.allowed_paths": [str(env["DATA_DIR"])],
            "safety.confirm_on": ["delete", "shell", "write_file"],
            "logging.log_dir": str(env["LOGS_DIR"]),
            "mcp.enabled": False,
            "mcp.servers": [],
        }
        (env["CONFIG_DIR"] / "api_keys.json").write_text(json.dumps(config, indent=2))
        # Patch config_manager
        with patch_config_manager(env["CONFIG_DIR"]):
            yield env
        # Cleanup: close any tracked DB connections
        for conn in env.get("DB_CONNECTIONS", []):
            try:
                conn.close()
            except Exception:
                pass

@contextmanager
def patch_config_manager(config_dir):
    import memory.config_manager as cm
    original_base = cm.BASE_DIR
    original_file = cm.CONFIG_FILE
    original_get = cm.get_mark_config
    cm.BASE_DIR = config_dir.parent
    cm.CONFIG_FILE = config_dir / "api_keys.json"
    cm._mark_config_instance = None
    def patched_get():
        return cm._MarkConfigAdapter()
    cm.get_mark_config = patched_get
    try:
        yield
    finally:
        cm.BASE_DIR = original_base
        cm.CONFIG_FILE = original_file
        cm.get_mark_config = original_get
        cm._mark_config_instance = None

def track_db_conn(env, conn):
    env.setdefault("DB_CONNECTIONS", []).append(conn)

# -----------------------------------------------------------------------------
# Stress Tests
# -----------------------------------------------------------------------------

def test_config_subsystem(env, iterations):
    log("Testing config subsystem...")
    start = time.perf_counter()
    for i in range(iterations):
        cfg = get_mark_config()
        _ = cfg.get("memory.chat_history_limit")
        _ = cfg.get("llm.model")
        _ = cfg.get("nonexistent", "default")
        save_config({"stress_run": i})
    elapsed = time.perf_counter() - start
    return {"name": "Config", "iterations": iterations, "elapsed": elapsed, "ops_per_sec": iterations / elapsed}

def test_hybrid_memory(env, iterations):
    log("Testing HybridMemory...")
    db_path = env["DATA_DIR"] / "stress_memory.db"
    class TempConfig:
        def get(self, key, default=None):
            if key == "memory.db_path":
                return str(db_path)
            return get_mark_config().get(key, default)
    hm = HybridMemory(config=TempConfig())
    track_db_conn(env, hm._conn)  # track for cleanup
    try:
        start = time.perf_counter()
        for i in range(iterations):
            hm.store(f"User input {i}", f"Assistant response {i}")
        for i in range(min(iterations, 50)):
            hm.inject_context(f"User input {i}", "")
        for i in range(min(iterations, 20)):
            hm._retrieve(f"query {i}", top_k=3)
        elapsed = time.perf_counter() - start
        stats = hm.stats()
        return {"name": "HybridMemory", "iterations": iterations, "elapsed": elapsed,
                "ops_per_sec": iterations / elapsed, "stats": stats}
    finally:
        hm._conn.close()

def test_rag_engine(env, iterations):
    log("Testing RAGEngine...")
    docs_dir = env["DATA_DIR"] / "docs"
    docs_dir.mkdir(exist_ok=True)
    for i in range(min(iterations, 50)):
        (docs_dir / f"doc_{i}.txt").write_text(f"This is document {i} with some content. " * 100)
    index_path = env["DATA_DIR"] / "rag_index"
    class TempConfig:
        def get(self, key, default=None):
            if key == "rag.index_path":
                return str(index_path)
            return get_mark_config().get(key, default)
    rag = RAGEngine(config=TempConfig())
    start = time.perf_counter()
    rag.index_directory(str(docs_dir))
    for i in range(min(iterations, 20)):
        rag.query(f"query {i}")
    elapsed = time.perf_counter() - start
    stats = rag.stats()
    return {"name": "RAG", "iterations": iterations, "elapsed": elapsed,
            "ops_per_sec": iterations / elapsed, "stats": stats}

def test_security_manager(env, iterations):
    log("Testing SecurityManager...")
    mgr = SecurityManager(data_dir=env["DATA_DIR"] / "security")
    start = time.perf_counter()
    # Issue many PINs
    pins = []
    for i in range(min(iterations, 200)):
        pin = mgr.issue_pin()
        pins.append(pin)
    for pin in pins[:50]:
        mgr.consume_pin(pin)
    # Pair devices – stay under cap of 5
    devices = []
    for i in range(min(iterations, 5)):
        d = mgr.pair_device(f"device_{i}")
        devices.append(d)
    # Revoke 2 devices
    for i in range(2):
        if i < len(devices):
            mgr.revoke_device(devices[i]["device_id"])
    # Pair 2 more
    for i in range(2):
        d = mgr.pair_device(f"device_{i+5}")
        devices.append(d)
    # Validate sessions
    for d in devices:
        sess = mgr.validate_session(d["session_token"])
        if sess:
            mgr.revoke_session(d["session_token"])
    # Rate limiting
    ip = "192.168.1.100"
    for _ in range(10):
        mgr.record_attempt(ip, success=False)
    for _ in range(5):
        mgr.record_attempt(ip, success=True)
    elapsed = time.perf_counter() - start
    return {"name": "Security", "iterations": iterations, "elapsed": elapsed,
            "ops_per_sec": iterations / elapsed}

def test_tool_dispatch(env, iterations):
    log("Testing Tool Dispatch...")
    start = time.perf_counter()
    for i in range(iterations):
        if i % 5 == 0:
            run_extra_tool("wikipedia", {"query": "Python"})
        elif i % 5 == 1:
            run_extra_tool("battery", {})
        elif i % 5 == 2:
            run_extra_tool("list_notes", {})
        elif i % 5 == 3:
            run_extra_tool("calculate", {"expression": "2+2"})
        else:
            run_extra_tool("convert_units", {"value": 10, "from_unit": "km", "to_unit": "m"})
    elapsed = time.perf_counter() - start
    return {"name": "ToolDispatch", "iterations": iterations, "elapsed": elapsed,
            "ops_per_sec": iterations / elapsed}

def test_llm_client(env, iterations, skip_if_no_ollama=True):
    try:
        import requests
        requests.get("http://localhost:11434/api/tags", timeout=2)
    except Exception:
        if skip_if_no_ollama:
            log("Ollama not running – skipping LLM tests.")
            return {"name": "LLM", "iterations": 0, "elapsed": 0, "skipped": True, "reason": "Ollama unavailable"}
        else:
            raise
    from core.llm_client import call_llm_text, call_llm_stream
    log("Testing LLM client (real Ollama)...")
    start = time.perf_counter()
    for i in range(min(iterations, 10)):
        try:
            call_llm_text("Reply with OK", timeout=10)
        except Exception:
            pass
    for i in range(min(iterations, 5)):
        try:
            gen = call_llm_stream([{"role": "user", "content": "Say hello"}])
            for _ in gen:
                pass
        except Exception:
            pass
    elapsed = time.perf_counter() - start
    return {"name": "LLM", "iterations": iterations, "elapsed": elapsed,
            "ops_per_sec": iterations / elapsed}

def test_file_operations(env, iterations):
    log("Testing File Operations...")
    test_dir = env["DATA_DIR"] / "file_stress"
    test_dir.mkdir(exist_ok=True)
    start = time.perf_counter()
    for i in range(iterations):
        fname = f"file_{i}.txt"
        create_file(str(test_dir), name=fname, content=f"Content {i}")
        read_file(str(test_dir), name=fname)
        if i % 10 == 0:
            delete_file(str(test_dir), name=fname)
    elapsed = time.perf_counter() - start
    return {"name": "FileOps", "iterations": iterations, "elapsed": elapsed,
            "ops_per_sec": iterations / elapsed}

def test_concurrency(env, iterations):
    log("Testing Concurrency...")
    start = time.perf_counter()
    def worker(i):
        if i % 3 == 0:
            hm = HybridMemory()
            hm.store(f"thread_{i}", f"resp_{i}")
            hm._conn.close()
            return True
        elif i % 3 == 1:
            run_extra_tool("calculate", {"expression": f"{i}+{i}"})
            return True
        else:
            safety = get_safety()
            safety.is_path_allowed("/tmp")
            return True
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, i) for i in range(iterations)]
        results = [f.result() for f in as_completed(futures)]
    elapsed = time.perf_counter() - start
    return {"name": "Concurrency", "iterations": iterations, "elapsed": elapsed,
            "ops_per_sec": iterations / elapsed}

# -----------------------------------------------------------------------------
# Main runner
# -----------------------------------------------------------------------------

def run_all_tests(iterations, skip_llm=False, skip_ui=False):
    results = []
    with temp_env() as env:
        results.append(test_config_subsystem(env, iterations))
        results.append(test_hybrid_memory(env, iterations))
        results.append(test_rag_engine(env, iterations))
        results.append(test_security_manager(env, iterations))
        results.append(test_tool_dispatch(env, iterations))
        if not skip_llm:
            results.append(test_llm_client(env, iterations))
        else:
            results.append({"name": "LLM", "skipped": True, "reason": "skipped by flag"})
        results.append(test_file_operations(env, iterations))
        results.append(test_concurrency(env, iterations))
    return results

def print_report(results):
    print("\n" + "="*80)
    print(" AETHER STRESS TEST REPORT")
    print("="*80)
    print(f"{'Test':<20} {'Iterations':<12} {'Elapsed (s)':<12} {'Ops/s':<10} {'Notes'}")
    print("-"*80)
    total_ops = 0
    total_time = 0
    for r in results:
        if r.get("skipped"):
            print(f"{r['name']:<20} {'SKIPPED':<12} {'-':<12} {'-':<10} {r.get('reason', '')}")
            continue
        ops = r.get("iterations", 0)
        elapsed = r.get("elapsed", 0)
        ops_per_sec = r.get("ops_per_sec", 0)
        notes = ""
        if "stats" in r:
            notes = f"stats: {r['stats']}"
        print(f"{r['name']:<20} {ops:<12} {elapsed:<12.3f} {ops_per_sec:<10.2f} {notes}")
        total_ops += ops
        total_time += elapsed
    print("-"*80)
    print(f"TOTAL OPS: {total_ops} in {total_time:.2f}s  (avg {total_ops/total_time:.2f} ops/s)")
    print("="*80)

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aether Stress Test")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS,
                        help=f"Number of iterations per test (default: {DEFAULT_ITERATIONS})")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip tests that require Ollama")
    parser.add_argument("--skip-ui", action="store_true",
                        help="Skip UI-related tests (not implemented yet)")
    parser.add_argument("--verbose", action="store_true",
                        help="Print detailed progress")
    args = parser.parse_args()
    global VERBOSE
    VERBOSE = args.verbose

    print(f"Starting stress test with {args.iterations} iterations per test...")
    results = run_all_tests(args.iterations, skip_llm=args.skip_llm, skip_ui=args.skip_ui)
    print_report(results)

if __name__ == "__main__":
    main()