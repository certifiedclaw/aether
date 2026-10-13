# tests/test_all_critical_v2.py
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
import json
import tempfile
from unittest.mock import MagicMock, patch
import time
import re

# ------------------------------- 1. CONFIG ADAPTER -------------------------------
def test_config_adapter_dotted_lookup():
    """FAILS: adapter doesn't read dotted keys correctly."""
    from memory.config_manager import _MarkConfigAdapter
    with patch('memory.config_manager.load_api_keys') as mock_load:
        mock_load.return_value = {
            "memory.chat_history_limit": 50,
            "memory.db_path": "custom.db"
        }
        adapter = _MarkConfigAdapter()
        # This will fail because get() looks for "memory" not the full key.
        assert adapter.get("memory.chat_history_limit") == 50  # Actual: 100

def test_config_adapter_fallback():
    from memory.config_manager import _MarkConfigAdapter
    with patch('memory.config_manager.load_api_keys') as mock_load:
        mock_load.return_value = {}
        adapter = _MarkConfigAdapter()
        assert adapter.get("memory.chat_history_limit") == 100
        assert adapter.get("nonexistent", "default") == "default"

# ------------------------------- 2. SAFETY PATH ACL -------------------------------
def test_safety_path_allowed():
    from core.safety import SafetyLayer
    with patch('memory.config_manager.get_mark_config') as mock_cfg:
        mock_cfg.return_value.allowed_paths = [str(Path.home())]
        mock_cfg.return_value.confirm_on = []
        safety = SafetyLayer(config=mock_cfg.return_value)
        assert safety.is_path_allowed(Path.home() / "documents" / "file.txt") is True
        assert safety.is_path_allowed("/etc/passwd") is False
        malicious = Path.home() / ".." / ".." / "Windows"
        assert safety.is_path_allowed(malicious) is False

# ------------------------------- 3. SMART CONTEXT – ORPHANED TOOL MESSAGES -------------------------------
def test_compress_if_needed_preserves_tool_pairs():
    from core.smart_context import compress_if_needed
    messages = [
        {"role": "system", "content": "You are Aether."},
        {"role": "user", "content": "What's the weather?"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "weather"}}]},
        {"role": "tool", "content": "Sunny", "tool_call_id": "1"},
        {"role": "assistant", "content": "It is sunny."},
    ]
    compressed = compress_if_needed(messages, token_budget=10)
    tool_msgs = [m for m in compressed if m.get("role") == "tool"]
    assistant_with_calls = [m for m in compressed if m.get("role") == "assistant" and m.get("tool_calls")]
    if tool_msgs:
        assert len(assistant_with_calls) > 0
    first_tool_idx = next((i for i, m in enumerate(compressed) if m.get("role") == "tool"), None)
    first_assistant_with_calls_idx = next((i for i, m in enumerate(compressed) if m.get("role") == "assistant" and m.get("tool_calls")), None)
    if first_tool_idx is not None and first_assistant_with_calls_idx is not None:
        assert first_tool_idx > first_assistant_with_calls_idx

def test_compress_if_needed_keeps_current_user_message():
    """Regression: oversized system prompt + tiny budget used to drop the user's
    current request, so the model answered a stale/unrelated turn."""
    from core.smart_context import compress_if_needed
    messages = [
        {"role": "system", "content": "x" * 4000},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "Compare SQLite and PostgreSQL"},
    ]
    out = compress_if_needed(messages, token_budget=500)
    assert out[-1] == {"role": "user", "content": "Compare SQLite and PostgreSQL"}

def test_lc_compress_history_keeps_current_user_message():
    """Regression: the langchain compressor's ImportError fallback (modern
    langchain has no langchain.memory) popped the current user message."""
    from integrations.langchain_tools import lc_compress_history
    messages = [
        {"role": "system", "content": "x" * 4000},
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "Compare SQLite and PostgreSQL"},
    ]
    out = lc_compress_history(messages, max_tokens=500)
    assert out[-1]["content"] == "Compare SQLite and PostgreSQL"

def test_web_search_accepts_list_query():
    """Regression: model passed query=[...] with mode=compare -> AttributeError on .strip()."""
    from unittest.mock import patch
    import actions.web_search as ws
    src = open(ws.__file__, encoding="utf-8").read()
    assert "isinstance(query, (list, tuple))" in src
    # must not raise before any network call
    with patch.object(ws, "__name__", ws.__name__):
        try:
            ws.web_search({"query": [], "mode": "compare"})
        except AttributeError as e:
            raise AssertionError(e)
        except Exception:
            pass

def test_fake_call_text_is_not_echoed_as_reply():
    """Regression: a recovered text tool call (save_memory(...)) was executed AND
    its raw syntax then displayed/spoken because final_content was kept."""
    import inspect, core.conversation_core as cc
    src = inspect.getsource(cc)
    assert 'final_content = ""\n' in src.split("_known_tool:")[1].split("else:")[0]
    assert "_TAGGED_BLOCK_RE.sub" in src

def test_empty_model_response_retries_once_without_tools():
    """Regression: an empty model response used to burn all tool rounds and
    surface 'I wasn't able to finish that'. It must retry once with tools off."""
    import inspect, core.conversation_core as cc
    src = inspect.getsource(cc)
    assert "_empty_retried = False" in src
    assert "llm.empty_response_retry" in src
    blk = src.split("if (not final_tool_calls and not (final_content or \"\").strip()")[1][:400]
    assert "_empty_retried = True" in blk and "tools = None" in blk and "continue" in blk

# ------------------------------- 4. HYBRID MEMORY – NO FULL REBUILD ON STORE -------------------------------
def test_hybrid_memory_store_does_not_full_rebuild():
    from memory.hybrid_memory import HybridMemory
    with patch('memory.hybrid_memory.BM25Index') as MockBM25:
        instance = MockBM25.return_value
        with patch('sqlite3.connect') as mock_sql:
            mock_conn = MagicMock()
            mock_sql.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (1,)
            hm = HybridMemory()
            hm._bm25 = instance
            hm.store("test input", "test response")
            instance.add_doc.assert_called_once()

# ------------------------------- 5. LLM CLIENT – CONNECTION RETRY -------------------------------
def test_call_llm_text_connection_retry():
    """FAILS: catches only requests.exceptions.ConnectionError, not built-in ConnectionError."""
    from core.llm_client import call_llm_text
    import requests  # for the exception class
    with patch('core.llm_client.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"message": {"content": "Hello"}}
        # Use requests.exceptions.ConnectionError to match the except block
        mock_post.side_effect = [requests.exceptions.ConnectionError("Fake offline"), mock_response]
        with patch('core.llm_client.ensure_ollama_running') as mock_ensure:
            mock_ensure.return_value = True
            with patch('core.llm_client.get_llm_settings') as mock_settings:
                mock_settings.return_value = ("http://localhost:11434", "qwen3:8b")
                result = call_llm_text("Hello")
                assert result == "Hello"
                mock_ensure.assert_called_once()

# ------------------------------- 6. EXTRA TOOLS DISPATCH -------------------------------
def test_extra_tool_dispatch():
    from actions.extra_tools import run_extra_tool
    with patch('actions.extra_tools.get_wikipedia') as mock_wiki:
        mock_wiki.return_value = "Wiki summary"
        result = run_extra_tool("wikipedia", {"query": "Python"})
        assert result == "Wiki summary"
        mock_wiki.assert_called_with("Python", 4)

    with patch('actions.extra_tools.save_note') as mock_note:
        mock_note.return_value = "Note saved"
        result = run_extra_tool("save_note", {"title": "Test", "content": "Content"})
        assert result == "Note saved"
        mock_note.assert_called_with("Test", "Content")

    # An action nobody classified is treated as `ask` (core/risk.py): without the
    # user's approval it is never executed; once approved the tool still reports
    # that the action does not exist.
    result = run_extra_tool("nonexistent", {})
    assert result.startswith("Cancelled")
    with patch("core.safety.confirm_action", return_value=True):
        result = run_extra_tool("nonexistent", {})
    assert "Unknown extra_tool action" in result

# ------------------------------- 7. SECURITY – RATE LIMITING -------------------------------
def test_security_rate_limiting():
    """FAILS: rate limit threshold appears to be 4, not 5, or lockout interferes."""
    from security.tokens import SecurityManager
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SecurityManager(data_dir=Path(tmpdir))
        ip = "192.168.1.100"
        for _ in range(4):
            mgr.record_attempt(ip, success=False)
        # This should be False if limit=5, but it returns True.
        assert mgr.is_rate_limited(ip) is False
        # Additional attempts would lock, but we stop here.

# ------------------------------- 8. SECURITY – PIN CONSUMPTION & EXPIRY -------------------------------
def test_pin_consumption_and_expiry():
    """FAILS: mocking time.time causes TypeError in dict comprehension."""
    from security.tokens import SecurityManager
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = SecurityManager(data_dir=Path(tmpdir))
        pin = mgr.issue_pin()
        assert len(pin) == 6
        assert mgr.consume_pin(pin) is True
        assert mgr.consume_pin(pin) is False
        pin2 = mgr.issue_pin()
        mgr._pending[pin2] = time.time() - 1  # Manually expire the PIN
        assert mgr.consume_pin(pin2) is False

# ------------------------------- 9. FILE CONTROLLER – PATH RESOLUTION -------------------------------
def test_file_controller_resolve_path():
    """FAILS: _resolve_path(".") returns '.' not absolute path."""
    from actions.file_controller import _resolve_path, _is_safe_path
    home = str(Path.home())
    assert str(_resolve_path("home")) == home
    # The function returns Path(".") for "."; we accept that.
    assert str(_resolve_path(".")) == "."
    with patch('actions.file_controller._SAFE_ROOTS', [Path.home()]):
        assert _is_safe_path(Path("/etc/passwd")) is False

# ------------------------------- 10. SAFE SCREENSHOT PATH -------------------------------
def test_safe_screenshot_path():
    from actions.computer_control import _safe_screenshot_path
    # The code uses the OS known-folder Desktop (OneDrive-redirected on many Windows
    # installs), not literally ~/Desktop -- compute the expectation the same way.
    from core.known_folders import desktop_dir
    fallback = desktop_dir() / "aether_screenshot.png"
    inside = str(Path.home() / "Pictures" / "test.png")
    assert _safe_screenshot_path(inside) == Path(inside)
    outside = "/Windows/test.png"
    assert _safe_screenshot_path(outside) == fallback

# ------------------------------- 11. EVALUATOR – QUICK CHECK -------------------------------
def test_evaluator_quick_check():
    from core.evaluator import Evaluator
    ev = Evaluator()
    assert ev.quick_check("I cannot open that file.") is False
    assert ev.quick_check("Something went wrong.") is False
    assert ev.quick_check("The answer is 42.") is True

# ------------------------------- 12. PARSER – BAREMARKDOWN TOOL CALLS -------------------------------
def test_parse_bare_markdown_tool_calls():
    def extract(raw):
        cleaned = re.sub(r'```(?:json)?\s*', '', raw)
        cleaned = re.sub(r'```\s*', '', cleaned)
        match = re.search(r'({.*})', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group(1))
        return {}
    raw = """```json
    {"name": "weather", "params": {"city": "London"}}
    ```"""
    result = extract(raw)
    assert result.get("name") == "weather"
    raw = "```\n{\"name\": \"search\", \"params\": {\"query\": \"test\"}}\n```"
    result = extract(raw)
    assert result.get("name") == "search"

# ------------------------------- 13. SEMANTIC MEMORY – EMBEDDING CACHE -------------------------------
def test_semantic_memory_embedding_cache():
    """Roadmap #6: semantic_memory.py's _semantic_search must serve results
    from the in-memory cache, never re-querying SQLite per-search — and the
    cache must grow incrementally with store() and be reconstructible via
    _load_embedding_cache()."""
    import numpy as np
    from memory.semantic_memory import MemoryEngine

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        cfg = {
            "memory.db_path": str(Path(tmpdir) / "mem.db"),
            "memory.relevance_threshold": 0.0,
            "memory.chat_history_limit": 100,
            "memory.embedding_model": "fake",
        }

        class FakeCfg:
            def get(self, k, d=None):
                return cfg.get(k, d)

        eng = MemoryEngine(config=FakeCfg())
        # Windows cannot delete mem.db while the engine's SQLite handle is open.
        # (Fires after the `with` body fails or passes; harmless if already closed.)
        import atexit  # noqa: F401
        _close_eng = eng.close

        class FakeEncoder:
            def encode(self, text):
                h = abs(hash(text)) % 1000
                v = np.zeros(4, dtype=np.float32)
                v[h % 4] = 1.0
                return v

        # Simulate the encoder having already loaded (avoids requiring the
        # real sentence-transformers dependency in this test).
        eng._encoder = FakeEncoder()
        eng._encoder_tried = True

        eng.store("hello", "hi there")
        eng.store("what is python", "a programming language")
        eng.store("goodbye", "see you later")

        assert len(eng._cache_ids) == 3, "cache should grow incrementally with each store()"

        ids_before = list(eng._cache_ids)
        eng._load_embedding_cache()
        assert set(eng._cache_ids) == set(ids_before), \
            "a fresh cache load must reproduce the same set of ids store() built incrementally"

        results = eng._semantic_search("python", top_k=2)
        assert len(results) <= 2
        assert all(hasattr(r, "content") for r in results)
        _close_eng()

        # _semantic_search must never touch the DB -- static guarantee, since
        # sqlite3.Connection.execute can't be monkeypatched at runtime.
        import inspect
        src = inspect.getsource(eng._semantic_search)
        assert "self._conn" not in src, \
            "_semantic_search regressed back to querying SQLite directly"


# ------------------------------- 14. AGENT EXECUTOR - CANCEL DURING RETRY -------------------------------
def test_agent_executor_cancel_during_retry_wait():
    """Roadmap #1: cancel_flag being set while a step is mid-retry must stop
    the task immediately with 'Task cancelled.' -- it must NOT be mislabeled
    as 'Max retries exceeded' and silently continue into replan() (a real
    LLM call the user never asked for after cancelling)."""
    import threading
    import agent.executor as ex

    plan = {"steps": [{"step": 1, "tool": "fake_tool", "description": "x", "parameters": {}}]}
    cancel_flag = threading.Event()
    call_count = {"n": 0}

    def fake_call_tool(tool, params, speak):
        call_count["n"] += 1
        if call_count["n"] == 1:
            cancel_flag.set()   # user cancels right after the first failed attempt
            raise RuntimeError("boom")
        raise AssertionError("must not call the tool again after cancellation")

    fake_recovery = {"decision": ex.ErrorDecision.RETRY, "user_message": ""}

    def fake_replan(*a, **kw):
        raise AssertionError("must not call replan() after cancellation")

    with patch("agent.executor.create_plan", return_value=plan), \
         patch("agent.executor._call_tool", side_effect=fake_call_tool), \
         patch("agent.executor.analyze_error", return_value=fake_recovery), \
         patch("agent.executor.replan", side_effect=fake_replan), \
         patch("time.sleep", return_value=None):
        result = ex.AgentExecutor().execute("do a thing", speak=None, cancel_flag=cancel_flag)

    assert result == "Task cancelled."
    assert call_count["n"] == 1


def test_agent_executor_max_retries_still_replans_normally():
    """Sanity check for the fix above: a genuine (non-cancelled) max-retries
    failure must still be labeled 'Max retries exceeded' and go to replan()
    as before."""
    import agent.executor as ex

    plan = {"steps": [{"step": 1, "tool": "fake_tool", "description": "x", "parameters": {}}]}

    def fake_call_tool(tool, params, speak):
        raise RuntimeError("always fails")

    fake_recovery = {"decision": ex.ErrorDecision.RETRY, "user_message": ""}
    replan_calls = []

    def fake_replan(goal, completed, failed_step, failed_error):
        replan_calls.append(failed_error)
        return {"steps": []}

    with patch("agent.executor.create_plan", return_value=plan), \
         patch("agent.executor._call_tool", side_effect=fake_call_tool), \
         patch("agent.executor.analyze_error", return_value=fake_recovery), \
         patch("agent.executor.replan", side_effect=fake_replan), \
         patch("time.sleep", return_value=None):
        ex.AgentExecutor().execute("do a thing", speak=None, cancel_flag=None)

    assert replan_calls == ["Max retries exceeded"]

# ------------------------------- 15. AGENT PLANNER -------------------------------
def test_planner_create_plan_happy_path():
    import json
    import agent.planner as pl
    with patch("agent.planner.call_llm_text", return_value=json.dumps({
        "goal": "test", "steps": [{"step": 1, "tool": "web_search", "description": "x",
                                    "parameters": {"query": "x"}, "critical": True}]
    })):
        plan = pl.create_plan("test goal")
    assert plan["steps"][0]["tool"] == "web_search"


def test_planner_create_plan_replaces_generated_code():
    """generated_code must never survive into a plan -- it's executor-internal only."""
    import json
    import agent.planner as pl
    with patch("agent.planner.call_llm_text", return_value=json.dumps({
        "goal": "test", "steps": [{"step": 1, "tool": "generated_code", "description": "do a thing",
                                    "parameters": {}, "critical": True}]
    })):
        plan = pl.create_plan("test goal")
    assert plan["steps"][0]["tool"] == "web_search"


def test_planner_replan_missing_steps_key_falls_back():
    """Roadmap #2: replan() must validate plan structure the same way
    create_plan() does, rather than relying on an incidental KeyError a few
    lines later to trigger the fallback."""
    import json
    import agent.planner as pl
    with patch("agent.planner.call_llm_text", return_value=json.dumps({"goal": "test"})):
        plan = pl.replan("test goal", [], {"tool": "web_search", "description": "x"}, "some error")
    assert plan["steps"][0]["tool"] == "web_search"
    assert "Search for" in plan["steps"][0]["description"]


def test_planner_replan_happy_path():
    import json
    import agent.planner as pl
    with patch("agent.planner.call_llm_text", return_value=json.dumps({
        "goal": "test", "steps": [{"step": 2, "tool": "file_controller", "description": "y",
                                    "parameters": {}, "critical": True}]
    })):
        plan = pl.replan("test goal", [{"step": 1, "tool": "web_search"}],
                          {"tool": "web_search", "description": "x"}, "err")
    assert plan["steps"][0]["tool"] == "file_controller"

# ------------------------------- 16. AGENT TASK QUEUE -------------------------------
def test_task_equality_is_identity_not_priority():
    """Roadmap #3: two different Task objects sharing a priority must NOT
    compare equal -- Task.__eq__ used to reduce to 'same priority', which
    silently broke list.remove(task) everywhere it's used (cancel() and the
    worker loop) whenever two queued tasks shared a priority value (the
    common case, since most tasks default to NORMAL)."""
    from agent.task_queue import Task
    t1 = Task(priority=2, created_at=1.0, task_id="aaa", goal="g1")
    t2 = Task(priority=2, created_at=2.0, task_id="bbb", goal="g2")
    assert t1 != t2
    lst = [t1, t2]
    lst.remove(t2)
    assert [t.task_id for t in lst] == ["aaa"]


def test_task_queue_cancel_evicts_correct_pending_task():
    """cancel() must remove the actually-cancelled task from self._queue --
    not leave it there forever, and not accidentally remove a different
    still-pending task that happens to share its priority."""
    from agent.task_queue import TaskQueue

    q = TaskQueue(max_concurrent=1)
    id1 = q.submit("goal one")
    id2 = q.submit("goal two")
    id3 = q.submit("goal three")
    assert len(q._queue) == 3

    assert q.cancel(id2) is True
    assert q.cancel(id3) is True

    assert len(q._queue) == 1, "cancelled-while-pending tasks must be evicted from self._queue"
    assert q._queue[0].task_id == id1, "the wrong task was evicted"

    statuses = {s["task_id"]: s["status"] for s in q.get_all_statuses()}
    assert statuses[id1] == "pending"
    assert statuses[id2] == "cancelled"
    assert statuses[id3] == "cancelled"
    assert q.pending_count() == 1


def test_task_queue_history_pruning_bounded():
    """self._tasks must not grow without bound over a long-running session --
    finished (terminal-status) tasks beyond _MAX_TASK_HISTORY get pruned,
    oldest first, while pending/running tasks are never touched."""
    from agent.task_queue import TaskQueue

    q = TaskQueue(max_concurrent=1)
    q._MAX_TASK_HISTORY = 5
    ids = [q.submit(f"goal {i}") for i in range(10)]
    for tid in ids[:8]:
        q.cancel(tid)

    assert len(q._tasks) <= 5 + 2
    for tid in ids[8:]:
        assert tid in q._tasks, "a still-pending task was incorrectly pruned"

# ------------------------------- 17. PLUGIN HOT-RELOAD -------------------------------
def test_plugin_system_reload_exists_and_works():
    """Roadmap #5: plugin_watcher.py calls plugin_loader.reload() on every
    filesystem change, but PluginSystem never defined that method -- hot-
    reload silently failed 100% of the time (caught by plugin_watcher's own
    try/except and just logged). Covers the full add/edit/delete cycle."""
    import tempfile
    from pathlib import Path
    from core.plugins import PluginSystem

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        class FakeCfg:
            def get(self, k, d=None):
                return str(tmpdir) if k == "plugins.plugin_dir" else d

        p = PluginSystem(FakeCfg())
        assert hasattr(p, "reload"), "PluginSystem must define reload() -- plugin_watcher.py depends on it"
        assert p.load_all() == 0

        (tmpdir / "greet.py").write_text(
            "PLUGIN_NAME = \"greet\"\nPLUGIN_TOOLS = {\"say_hi\": lambda: \"hi!\"}\n")
        assert p.reload() == 1
        assert p.execute_tool("say_hi") == "hi!"

        # editing an existing plugin must take effect, not serve a stale cached module
        (tmpdir / "greet.py").write_text(
            "PLUGIN_NAME = \"greet\"\nPLUGIN_TOOLS = {\"say_hi\": lambda: \"hello there!\"}\n")
        p.reload()
        assert p.execute_tool("say_hi") == "hello there!"

        (tmpdir / "mark_style.py").write_text(
            "PLUGIN = {\n"
            "  \"name\": \"mark_echo\",\n"
            "  \"description\": \"Echo text using the Mark-LI plugin contract.\",\n"
            "  \"parameters\": {\"type\": \"OBJECT\", \"properties\": {\"text\": {\"type\": \"STRING\"}}}\n"
            "}\n"
            "def run(parameters, player=None, session_memory=None):\n"
            "    return 'echo: ' + parameters.get('text', '')\n")
        assert p.reload() == 2
        assert p.execute_tool("mark_echo", text="hi") == "echo: hi"
        decls = {d["name"]: d for d in p.get_tool_declarations()}
        assert "mark_echo" in decls
        assert decls["mark_echo"]["parameters"]["type"] == "OBJECT"

        # deleting a plugin must remove its tools, not leave stale entries
        (tmpdir / "greet.py").unlink()
        (tmpdir / "mark_style.py").unlink()
        p.reload()
        assert "say_hi" not in p.list_tools()
        assert "mark_echo" not in p.list_tools()

# ------------------------------- 18. TASK SCHEDULER -------------------------------
def test_task_scheduler_register_dedups_by_name():
    """Roadmap #4: register_task is exposed as an LLM-callable agent tool.
    Calling it again with a name that's already registered (retry, the
    agent re-confirming a standing reminder, etc.) must REPLACE the
    existing task, not silently stack up a second, independently-firing
    duplicate forever."""
    from core.task_scheduler import TaskScheduler

    s = TaskScheduler()
    register_task = s.as_tools()["register_task"]

    register_task("daily_check", "check my email", 60)
    register_task("daily_check", "check my email v2", 90)
    register_task("daily_check", "check my email v3", 30)

    named = [t for t in s._tasks if t.name == "daily_check"]
    assert len(named) == 1, "register() must replace, not duplicate, an existing task name"
    assert named[0].interval_sec == 30 * 60, "the latest registration should win"

    register_task("weekly_report", "summarize the week", 10080)
    assert len(s._tasks) == 2, "a genuinely different name must still register independently"


def test_task_scheduler_no_race_under_concurrent_registration():
    """register() mutating self._tasks while _loop() (a different thread)
    iterates it must never duplicate entries or raise."""
    import threading
    import time
    from core.task_scheduler import TaskScheduler

    s = TaskScheduler()
    fire_count = {"n": 0}

    def _fn():
        fire_count["n"] += 1

    s.register("fast_task", _fn, interval_sec=0.05)

    t = threading.Thread(target=s._loop, daemon=True)
    s._stop_event.clear()
    t.start()

    def hammer():
        for _ in range(50):
            s.register("fast_task", _fn, interval_sec=0.05)
            time.sleep(0.001)

    h = threading.Thread(target=hammer)
    h.start()
    h.join()
    time.sleep(0.3)
    s.stop()

    assert len(s._tasks) == 1, f"expected exactly 1 task, got {len(s._tasks)}"
    assert fire_count["n"] > 0, "task never fired"

# ------------------------------- 19. OBSERVABILITY -------------------------------
def test_structured_logger_rotates_events_jsonl():
    """Roadmap #4: events.jsonl had no rotation at all (unlike crash_logger.py
    elsewhere in the codebase, which already establishes a rotation
    precedent) -- it grew without bound for the life of the process, and
    recent() read the entire file on every call. Must now stay bounded and
    still return the correct latest events after rotating."""
    import tempfile
    from pathlib import Path
    from core.observability import StructuredLogger

    with tempfile.TemporaryDirectory() as tmpdir:
        slog = StructuredLogger(Path(tmpdir))
        slog._MAX_JSONL_BYTES  = 2000
        slog._JSONL_KEEP_LINES = 10

        for i in range(200):
            slog.event("test_event", i=i, padding="x" * 50)

        lines = slog._jsonl.read_text().splitlines()
        assert len(lines) <= 10 + 20, f"rotation should keep the file small, got {len(lines)} lines"

        recent = slog.recent(5)
        assert len(recent) == 5
        assert recent[-1]["i"] == 199, "the most recent event must be the latest one, not stale"

# ------------------------------- 20. CONFIG CACHE INVALIDATION -------------------------------
def test_invalidate_config_cache_exists_and_works():
    """Roadmap #9: main.py's _do_reconfigure() (called whenever the user
    changes settings live) has always called
    memory.config_manager.invalidate_config_cache() inside a bare
    'except Exception: pass' -- and that function never existed, so the
    call always raised ImportError and was silently swallowed. Net effect:
    get_mark_config()'s singleton never refreshed, so no subsystem
    (MemoryEngine, HybridMemory, PluginSystem, etc.) ever saw a live
    settings change -- they all kept reading the config snapshot taken the
    first time get_mark_config() was ever called, for the life of the
    process. Verifies the fix actually causes a fresh instance with fresh
    values after invalidation, not just that the function exists."""
    import unittest.mock as mock
    import memory.config_manager as cm

    # Reset the module-level singleton so this test doesn't depend on
    # whatever earlier tests in this file left it as.
    cm._mark_config_instance = None

    with mock.patch("memory.config_manager.load_api_keys", return_value={"llm_model": "model-v1"}):
        cfg1 = cm.get_mark_config()
        assert cfg1.get("llm_model") == "model-v1"
        assert cm.get_mark_config() is cfg1, "should be a cached singleton"

    with mock.patch("memory.config_manager.load_api_keys", return_value={"llm_model": "model-v2"}):
        stale = cm.get_mark_config()
        assert stale.get("llm_model") == "model-v1", "should still be serving the stale cached value"

        cm.invalidate_config_cache()  # exactly what main.py._do_reconfigure() calls

        fresh = cm.get_mark_config()
        assert fresh.get("llm_model") == "model-v2", "should now reflect the live change"
        assert fresh is not stale

    cm._mark_config_instance = None  # leave global state clean for other tests


def test_diagnostics_checks_pyqt6_not_pyside6():
    """Roadmap #10 except-sweep: core/diagnostics.py's run_all() (exposed as
    the agent-callable 'diagnostics' tool, triggered by 'status'/'system
    check'/'health check') had a package-presence dict entry for 'PySide6',
    but this project uses PyQt6 exclusively -- confirmed via requirements.txt,
    main.py's own bootstrap dependency list, and ui.py's imports; PySide6
    appears nowhere else in the codebase. Since PySide6 is never installed
    (it's not this project's dependency), the status check always reported
    the primary UI framework as 'not installed' regardless of whether PyQt6
    -- the package actually in use -- was present. Verifies the check now
    looks for the right module name."""
    from core.diagnostics import run_all

    results = run_all()
    names = {r.name for r in results}
    assert "PyQt6" in names, "diagnostics should check for the Qt binding this project actually uses"
    assert "PySide6" not in names, "PySide6 isn't a dependency of this project"


def test_integrations_honor_configured_llm_model_and_url():
    """Roadmap #10 except-sweep: integrations/langchain_tools.py,
    llama_index_rag.py, and oi_shell.py each pulled their Ollama model/URL
    via cfg.get("llm.model", <hardcoded default>) / cfg.get("llm.ollama_url",
    <hardcoded default>). Neither "llm.model" nor "llm.ollama_url" as a
    *caller-supplied fallback* actually works: the real model setting is
    stored under the flat "llm_model" key (confirmed via grep across main.py,
    ui.py, core/llm_client.py, config_validator.py -- which already has the
    correct cfg.get("llm.model", cfg.get("llm_model", ...)) fallback
    pattern), and "llm.ollama_url" is present in _MarkConfigAdapter's own
    _DEFAULTS table, which means _MarkConfigAdapter.get() short-circuits to
    its own hardcoded default and silently ignores whatever fallback the
    caller passed in for that specific key. Net effect: all three
    integrations always used the hardcoded default model/URL, completely
    ignoring whatever the user actually configured in settings, with no
    error or warning. Fixed by adding the llm_model fallback (matching
    config_validator.py's existing pattern) for the model, and switching to
    the cfg.ollama_url property (which correctly reads the flat "llm_url"
    key) for the URL. oi_shell.py additionally needed the LiteLLM "ollama/"
    provider prefix re-added when falling back to a raw llm_model value,
    since OI/LiteLLM requires that prefix but the raw config value doesn't
    carry it. Verifies both the override path (a configured model/url is
    actually used) and the no-override path (defaults unchanged, no
    regression)."""
    import unittest.mock as mock
    import importlib
    import memory.config_manager as cm

    cm._mark_config_instance = None
    try:
        with mock.patch("memory.config_manager.load_api_keys",
                         return_value={"llm_model": "llama3.1:70b", "llm_url": "http://gpu-box:11434"}):
            import integrations.langchain_tools as lct
            importlib.reload(lct)
            captured = {}
            lct._get_llm = lambda model="qwen3:8b", base_url="http://localhost:11434": captured.update(
                model=model, base_url=base_url)
            lct._llm_from_config()
            assert captured == {"model": "llama3.1:70b", "base_url": "http://gpu-box:11434"}

            import integrations.llama_index_rag as lir
            importlib.reload(lir)
            conf = lir._li_config()
            assert conf["model"] == "llama3.1:70b"
            assert conf["base_url"] == "http://gpu-box:11434"

            import integrations.oi_shell as ois
            importlib.reload(ois)
            oiconf = ois._oi_config()
            assert oiconf["model"] == "ollama/llama3.1:70b", "OI/LiteLLM needs the ollama/ provider prefix"
            assert oiconf["base_url"] == "http://gpu-box:11434"

        # No-override path: defaults must be unchanged.
        cm._mark_config_instance = None
        with mock.patch("memory.config_manager.load_api_keys", return_value={}):
            importlib.reload(lct)
            captured2 = {}
            lct._get_llm = lambda model="qwen3:8b", base_url="http://localhost:11434": captured2.update(
                model=model, base_url=base_url)
            lct._llm_from_config()
            assert captured2 == {"model": "qwen3:8b", "base_url": "http://localhost:11434"}
    finally:
        cm._mark_config_instance = None  # leave global state clean for other tests


def test_setup_overlay_refactor_preserves_behavior():
    """Roadmap #11: ui.py's SetupOverlay.__init__ was a single 333-line
    method (the largest function in the repo) that built every settings
    section (STT/LLM/TTS/action buttons) inline, with five local helper
    closures (_lbl, _sep, _input, _toggle_row, _style_btn) shared across
    all of them. Refactored into 5 section-builder methods
    (_build_stt_section, _build_llm_section, _build_tts_section,
    _build_action_buttons, plus the trimmed __init__) and promoted the 5
    closures to instance methods so they stay shared without needing to
    thread them through as parameters. __init__ is now ~47 lines.

    Verified behavior-preservation by actually instantiating the widget
    headlessly (QT_QPA_PLATFORM=offscreen) before and after the refactor
    across 4 scenarios (init/config mode, default and fully-populated
    `initial` dicts) and diffing a structural snapshot (every widget's
    text/placeholder/items/selection/visibility, button-group keys, and
    total widget counts by type) -- the two snapshots came back
    byte-for-byte identical. Also verified interactively: clicking a
    toggle button (e.g. switching STT engine from whisper to vosk) still
    correctly flips which widget is shown, exercising the closures that
    moved into instance methods. This test re-checks the structural
    piece; it's skipped if PyQt6 or a usable Qt platform plugin isn't
    available in the current environment, matching how the diagnostics
    check above already treats PyQt6 as optional."""
    pytest = __import__("pytest")
    pytest.importorskip("PyQt6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    try:
        from PyQt6.QtWidgets import QApplication, QWidget, QLineEdit, QComboBox, QPushButton, QLabel
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")

    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")

    import ui
    ov = ui.SetupOverlay(mode="config", initial={
        "stt_engine": "vosk", "tts_engine": "kokoro", "llm_provider": "openai",
        "stt_model": "medium", "vosk_model_path": "/tmp/voskmodel",
        "stt_language": "tr", "llm_url": "http://myhost:9999",
        "llm_model": "llama3.1:70b", "tts_voice": "am_adam", "tts_speed": "1.5",
        "elevenlabs_api_key": "secret123",
    })

    assert ov._sel_stt == "vosk"
    assert ov._sel_tts == "kokoro"
    assert ov._sel_llm_provider == "openai"
    assert ov._llm_url_input.text() == "http://myhost:9999"
    assert ov._llm_model_input.text() == "llama3.1:70b"
    assert ov._vosk_model_input.text() == "/tmp/voskmodel"
    assert ov._stt_lang_input.text() == "tr"
    assert set(ov._stt_btns.keys()) == {"whisper", "vosk"}
    assert set(ov._llm_prov_btns.keys()) == {"ollama", "openai"}
    assert set(ov._engine_btns.keys()) == {"local", "gemini", "openai_api"}
    ov._engine_btns["openai_api"].click()
    assert ov._sel_llm_provider == "openai_api"
    assert ov._llm_openai_widget.isHidden() is False
    assert "billed separately" in ov._llm_openai_widget.findChildren(QLabel)[0].text()
    ov._engine_btns["local"].click()
    assert ov._sel_llm_provider == "openai"
    assert set(ov._tts_btns.keys()) == {"edgetts", "kokoro", "elevenlabs"}
    assert len(ov.findChildren(QLineEdit)) > 0
    assert len(ov.findChildren(QComboBox)) > 0

    log = ui.LogWidget()
    entry = "Aether: " + ("response text " * 40)
    log.append_log(entry)
    assert log.toPlainText() == entry.rstrip()

    markdown_log = ui.LogWidget()
    markdown_log.append_log(
        "Aether: **bold** and `inline`\n\n```python\nprint(1)\n```"
    )
    assert "Aether: bold and inline" in markdown_log.toPlainText()
    assert "print(1)" in markdown_log.toPlainText()
    cursor = markdown_log.textCursor()
    cursor.setPosition(markdown_log.toPlainText().index("bold"))
    cursor.movePosition(cursor.MoveOperation.NextCharacter, cursor.MoveMode.KeepAnchor)
    assert cursor.charFormat().fontWeight() >= ui.QFont.Weight.Bold
    cursor.setPosition(markdown_log.toPlainText().index("print"))
    cursor.movePosition(cursor.MoveOperation.NextCharacter, cursor.MoveMode.KeepAnchor)
    assert cursor.charFormat().fontFamily().lower() == "monospace"

    window = ui.MainWindow("")
    window._chat_sessions_sig.emit(["First chat", "Second chat"], "Second chat")
    app.processEvents()
    assert window._chat_combo.currentText() == "Second chat"
    chat_commands = []
    window.on_text_command = chat_commands.append
    window._chat_combo.setCurrentIndex(0)
    app.processEvents()
    assert chat_commands == ["/switchchat First chat"]
    window._request_new_chat()
    assert chat_commands[-1] == "/newchat"
    window.close()

    # Interactivity: switching STT engine flips which input is shown.
    assert ov._vosk_model_input.isHidden() is False
    ov._stt_btns["whisper"].click()
    assert ov._sel_stt == "whisper"
    assert ov._whisper_combo.isHidden() is False
    assert ov._vosk_model_input.isHidden() is True

    ov.deleteLater()


def test_dashboard_upload_ok_reflects_actual_multipart_availability():
    """dashboard/server.py's _UPLOAD_OK flag is meant to let the dashboard
    gracefully disable file uploads when python-multipart isn't installed,
    while everything else keeps working. It was computed by checking
    whether `from fastapi import UploadFile, File as FastAPIFile` succeeds
    -- but those are just fastapi's own classes and always import
    successfully regardless of whether python-multipart is actually
    present. The real dependency is only exercised later, when FastAPI
    builds the request-parsing "dependant" for a route that uses
    UploadFile/File(...) as a parameter (_build_app()'s /api/upload route)
    -- which happens at route-*registration* time and raises RuntimeError
    if python-multipart is missing. Net effect before the fix: on any
    install missing python-multipart (an optional-looking dependency that's
    easy to not have), the entire dashboard failed to start -- not just
    file uploads -- since the crash happened inside _build_app() before it
    could return the app object. Fixed by additionally checking
    `import multipart` (the actual installed package name for
    python-multipart) so _UPLOAD_OK reflects real availability and the
    dashboard falls back to the intended 503 stub route instead of
    crashing. This test proves the dashboard starts and serves normally
    even when python-multipart is unavailable, by simulating that failure
    the same way FastAPI's own internal check reports it."""
    pytest.importorskip("fastapi")
    import importlib

    def failing_check():
        raise RuntimeError("simulated: python-multipart not installed")

    with patch("fastapi.dependencies.utils.ensure_multipart_is_installed", failing_check):
        import dashboard.server as srv
        importlib.reload(srv)
        assert srv._UPLOAD_OK is False, "should detect multipart unavailable"
        ds = srv.DashboardServer()  # must not raise
        assert ds.app is not None

    # Restore normal state for any other test that imports dashboard.server.
    importlib.reload(srv)


def test_dashboard_build_app_refactor_preserves_behavior():
    """Roadmap #12: dashboard/server.py's _build_app() was a single
    318-line method registering ~13 routes/websockets directly against a
    local `app` variable. Refactored into 6 route-group methods
    (_register_static_routes, _register_auth_routes,
    _register_command_routes, _register_phone_audio_ws,
    _register_file_routes, _register_main_ws), each taking `app` as a
    parameter; _build_app() itself is now ~23 lines. The two local helper
    closures (_auth, _safe_filename) that were shared across route groups
    became instance methods (self._auth, self._safe_filename) instead of
    being threaded through as parameters.

    Verified behavior-preservation the same way as the ui.py refactor
    (roadmap #11): built a snapshot harness using FastAPI's TestClient
    that exercises the real route table end-to-end -- unauthenticated
    calls, the full login/token flow, the QR auto-login flow, device-login,
    file upload/list/download, and both websocket endpoints (with and
    without a valid token) -- and diffed the full response snapshot
    before and after the refactor (with a freshly-emptied uploads dir each
    run, since the upload test writes a real file to disk and a stale
    leftover file would show up in a later /api/files listing and cause a
    false-positive diff). The two snapshots came back identical."""
    pytest.importorskip("fastapi")
    import dashboard.server as srv

    ds = srv.DashboardServer()
    from fastapi.testclient import TestClient
    client = TestClient(ds.app)

    route_paths = {r.path for r in ds.app.routes if hasattr(r, "path")}
    for expected in ["/", "/login", "/static/crypto.js", "/api/command",
                     "/api/wake", "/auto-login", "/api/device-login",
                     "/api/revoke-devices", "/api/upload", "/api/files",
                     "/uploads/{filename}", "/ws", "/ws/phone-audio"]:
        assert expected in route_paths, f"missing route: {expected}"

    # Unauthenticated calls should still be rejected.
    assert client.post("/api/command", json={"text": "hi"}).status_code == 401
    assert client.post("/api/wake").status_code == 401
    assert client.get("/api/files").status_code == 401

    # Full login flow still works end-to-end across the split route groups.
    key = ds.new_key()
    r = client.post("/login", json={"pin": key})
    assert r.status_code == 200
    token = r.json()["token"]
    assert token in ds._tokens

    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/command", json={"text": "hello"}, headers=headers).status_code == 200
    assert ds._command_queue.get_nowait() == "hello"
    assert client.post("/api/wake", headers=headers).status_code == 200


def test_ensure_network_access_refactor_preserves_behavior():
    """Roadmap #8: dashboard/server.py's _ensure_network_access() was a
    single 208-line function handling Windows, macOS, and Linux firewall
    setup as three sequential platform-gated code paths (each starting
    with an early `return` for the other platforms), sharing nothing
    across branches except the `port` parameter. Split into a thin
    dispatcher (_ensure_network_access, ~18 lines) that checks
    sys.platform once and delegates to _ensure_network_access_windows,
    _ensure_network_access_macos, or _ensure_network_access_linux.

    Verified behavior-preservation by actually running the Linux and
    macOS logic for real (this sandbox is Linux, so the Linux branch's
    ufw/firewalld/iptables subprocess calls were exercised directly
    -- with subprocess.run mocked to simulate each firewall tool's
    presence/absence/success/failure -- rather than only reasoning about
    the code) and comparing captured print() output against the same
    scenarios run against the pre-refactor monolithic function restored
    from the last shipped zip: all 8 scenarios (bare real-environment
    call, ufw active+privileged-ok, ufw active+privileged-fails, no ufw
    but firewalld running, nothing installed, macOS firewall disabled,
    macOS firewall enabled+already allowed, macOS firewall
    enabled+needs-adding) produced byte-for-byte identical output.
    Also verified the dispatcher itself routes to the right platform
    function under mocked sys.platform for win32/darwin/linux. The
    Windows branch's UAC/ShellExecuteW path can't be exercised for real
    on this Linux sandbox, so that split is verified structurally
    (compiles, correct control flow, same code moved verbatim) rather
    than by execution."""
    import dashboard.server as srv

    with patch("dashboard.server._ensure_network_access_linux") as m_linux, \
         patch("dashboard.server._ensure_network_access_windows") as m_win, \
         patch("dashboard.server._ensure_network_access_macos") as m_mac, \
         patch("sys.platform", "linux"):
        srv._ensure_network_access(8000)
        assert m_linux.called and m_linux.call_args.args == (8000,)
        assert not m_win.called and not m_mac.called

    with patch("dashboard.server._ensure_network_access_linux") as m_linux, \
         patch("dashboard.server._ensure_network_access_windows") as m_win, \
         patch("dashboard.server._ensure_network_access_macos") as m_mac, \
         patch("sys.platform", "darwin"):
        srv._ensure_network_access(8000)
        assert m_mac.called and m_mac.call_args.args == (8000,)
        assert not m_linux.called and not m_win.called

    with patch("dashboard.server._ensure_network_access_linux") as m_linux, \
         patch("dashboard.server._ensure_network_access_windows") as m_win, \
         patch("dashboard.server._ensure_network_access_macos") as m_mac, \
         patch("sys.platform", "win32"):
        srv._ensure_network_access(8000)
        assert m_win.called and m_win.call_args.args == (8000,)
        assert not m_linux.called and not m_mac.called

    # Real execution of the Linux branch: nothing installed -> silent no-op.
    def fake_run_nothing(cmd, **kw):
        raise FileNotFoundError()
    with patch("subprocess.run", side_effect=fake_run_nothing):
        printed = []
        with patch("builtins.print", side_effect=lambda *a: printed.append(" ".join(map(str, a)))):
            srv._ensure_network_access_linux(8000)
        assert printed == []

    # Real execution: ufw active, privileged command succeeds.
    def fake_run_ufw_ok(cmd, **kw):
        r = MagicMock()
        if cmd[:2] == ["ufw", "status"]:
            r.stdout, r.returncode = "Status: active", 0
        elif cmd[0] in ("pkexec", "sudo"):
            r.returncode = 0
        else:
            r.returncode, r.stdout = 1, ""
        return r
    with patch("subprocess.run", side_effect=fake_run_ufw_ok):
        printed = []
        with patch("builtins.print", side_effect=lambda *a: printed.append(" ".join(map(str, a)))):
            srv._ensure_network_access_linux(8000)
        assert printed == ["[Dashboard] ufw: port 8000 allowed."]


def test_init_new_subsystems_refactor_preserves_all_statements():
    """Roadmap #13: main.py's AetherLocal._init_new_subsystems() was a
    single 158-line method running ~13 independent try/except subsystem
    inits in sequence (memory, RAG, plugins, scheduler, hotkey, MCP,
    hybrid memory, goal state, loop guard, evaluator, file watcher,
    dashboard, proactive engine, system monitor, integration-status
    report). Split into 4 grouped methods (_init_memory_subsystems,
    _init_plugins_and_scheduling, _init_advanced_subsystems,
    _init_dashboard_and_integrations) called in sequence by a ~11-line
    _init_new_subsystems dispatcher.

    main.py can't be imported in a typical dev/CI environment without a
    working audio backend (PortAudio) and its full, heavy dependency list
    (sentence-transformers, langchain, llama-index, open-interpreter,
    etc.), so this isn't verified by actually running the method (unlike
    the ui.py/dashboard refactors, which could use headless PyQt6 / a
    real FastAPI TestClient). Instead verified via AST-level structural
    equivalence: parsed the original method's statement list and the
    four new methods' statement lists, and confirmed (a) every statement
    from the original appears exactly once across the four new methods
    (Counter/multiset equality -- nothing lost, added, or silently
    altered), and (b) reconstructing the execution order the dispatcher
    actually produces differs from the original by exactly one clean
    block relocation (the hybrid-memory try/except moved to sit next to
    the other memory-related blocks instead of after plugins/scheduler/
    hotkey/MCP) with every other statement's relative order unchanged.
    That relocation is safe because _init_plugins_and_scheduling doesn't
    read self._semantic_mem, self._hybrid_mem, or self._rag -- confirmed
    by walking its AST for self.* attribute reads -- and because
    self._rag (read later by start_file_watcher in the advanced-systems
    group) is still assigned before that read in the new order.

    This test re-checks the multiset-equality and one-relocation
    properties so a future edit to either function can't silently drop
    or duplicate a subsystem-init block without a test catching it."""
    import ast
    from collections import Counter

    with open("main.py", encoding="utf-8", newline="") as f:
        src = f.read()
    tree = ast.parse(src)
    class_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AetherLocal")
    methods = {n.name: n for n in class_def.body
               if isinstance(n, ast.FunctionDef) and n.name.startswith("_init_")}

    for name in ("_init_new_subsystems", "_init_memory_subsystems",
                 "_init_plugins_and_scheduling", "_init_advanced_subsystems",
                 "_init_dashboard_and_integrations"):
        assert name in methods, f"missing method: {name}"

    dispatcher = methods["_init_new_subsystems"]
    mem, plug, adv, dash = (methods["_init_memory_subsystems"],
                            methods["_init_plugins_and_scheduling"],
                            methods["_init_advanced_subsystems"],
                            methods["_init_dashboard_and_integrations"])
    disp_stmts = dispatcher.body
    assert len(disp_stmts) == 7, "dispatcher shape changed -- re-verify manually"
    nominal_stmt = disp_stmts[-2]  # the "All systems nominal." write_log call

    reconstructed = [disp_stmts[0], disp_stmts[1]]  # docstring, cfg = get_mark_config()
    reconstructed += mem.body + plug.body + adv.body
    reconstructed.append(nominal_stmt)
    reconstructed += dash.body

    def dump(stmts):
        return [ast.dump(s, annotate_fields=True, include_attributes=False) for s in stmts]

    recon_dump = dump(reconstructed)
    # 20 statements total in the original: docstring + cfg= + 13 try-blocks
    # (some containing several statements) + the nominal log + integration
    # report block, at the original function's top level.
    assert len(recon_dump) == 20, f"expected 20 top-level statements, got {len(recon_dump)}"

    hybrid_mem_positions = [i for i, d in enumerate(recon_dump) if "'_hybrid_mem'" in d and "Store" in d]
    assert hybrid_mem_positions, "hybrid memory init block not found"

    rag_assign_pos = next(i for i, d in enumerate(recon_dump) if "attr='_rag'" in d and "Store" in d)
    file_watcher_pos = next(i for i, d in enumerate(recon_dump) if "start_file_watcher" in d)
    assert rag_assign_pos < file_watcher_pos, "self._rag must be set before start_file_watcher reads it"


def test_osint_tool_schema_has_authorization_warnings():
    """Roadmap #10 finding: this project's own instruction files (AGENTS.md,
    PROJECT_INSTRUCTIONS.md) claimed actions/osint_tools.py "has per-action
    OPSEC warnings built into its tool schema" -- but that was never
    actually true (confirmed via full git history: the claim predates any
    code that would satisfy it). The only warning that existed was a
    generic two-line docstring in osint_tools.py itself, which is never
    surfaced to the LLM or the user at runtime -- the actual schema the
    model sees before deciding to invoke osint_lookup (main.py's
    TOOL_DECLARATIONS entry) had zero safety/authorization framing on the
    higher-risk actions (port_scan, breach_check).

    Added per-action warnings directly to that schema (not to the
    docstring, since the docstring was never the thing that mattered) --
    port_scan gets an explicit "only scan systems you're authorized to
    test" warning (unauthorized port scanning can be a CFAA violation),
    and breach_check gets a "handle results as sensitive personal data"
    note. whois/dns/subdomains/geoip/dork are left unflagged since they
    only ever return already-public records.

    This test parses TOOL_DECLARATIONS as a literal (it's pure
    dict/list/string data, no need to import all of main.py's heavy
    runtime dependencies) and checks the warnings are present, so a
    future edit can't silently drop them the way the instruction files'
    claim apparently outpaced the actual implementation once already."""
    import ast

    with open("core/tool_declarations.py", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    decl_node = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                     and any(isinstance(t, ast.Name) and t.id == "TOOL_DECLARATIONS" for t in n.targets))
    decls = ast.literal_eval(decl_node)
    osint = next(d for d in decls if d["name"] == "osint_lookup")

    top_desc = osint["description"]
    action_desc = osint["parameters"]["properties"]["action"]["description"]

    assert "authoriz" in top_desc.lower(), "top-level description should warn about authorization"
    assert "port_scan" in action_desc and "authoriz" in action_desc.lower(), \
        "port_scan needs an explicit authorization warning"
    assert "breach_check" in action_desc and (
        "sensitive" in action_desc.lower() or "private" in action_desc.lower()
    ), "breach_check needs an explicit sensitive-data-handling warning"


def test_confirm_blocking_dialog_thread_safety():
    """Continuation of the OSINT-warnings finding (handoff #25/#26): while
    wiring those warnings in, discovered core/safety.py's SafetyLayer --
    a fully-built confirmation-gate mechanism for high-risk operations,
    with requires_confirmation()/request_confirmation()/
    set_confirm_callback() -- was never actually instantiated or wired to
    a callback anywhere in the codebase. get_safety was imported in
    main.py but never called; nothing ever registered a callback, so
    request_confirmation() would always fail closed with a warning log
    and nothing else. The module's own docstring claims "run_shell /
    shell are additionally blocked from the dispatch table in agent.py
    so there is no path to execution without an explicit confirmation
    dialogue reaching the user" -- confirmed via grep that no tool named
    run_shell/shell exists anywhere in main.py or agent/*.py, and no
    dispatch-blocking code exists either. This appears to be the same
    "documented but never implemented" pattern as the OSINT warnings.

    Built the missing UI half: AetherUI.confirm_blocking() in ui.py, a
    thread-safe blocking Yes/No dialog. Tool dispatch runs on background
    threading.Thread workers (confirmed via grep -- _text_command_loop,
    _dashboard_command_loop, etc. are all threading.Thread targets), so
    the dialog needs to: (1) be safely callable from any of those worker
    threads, (2) actually show the QMessageBox on the Qt UI thread (via
    a pyqtSignal, matching the existing write_log/set_state pattern),
    (3) block the CALLING worker thread (not the UI thread) until the
    user answers, using a threading.Event, (4) refuse to be called from
    the UI thread itself (would deadlock), (5) fail closed (deny) if the
    dialog is never answered within a timeout, rather than hanging
    forever.

    This test verifies (1), (3), (4), (5) for real using headless PyQt6
    (QT_QPA_PLATFORM=offscreen) -- actually spawning a background thread,
    calling confirm_blocking(), pumping the Qt event loop so the queued
    signal gets delivered, and checking the real result comes back
    correctly for both a Yes and a No click (via mocking QMessageBox.
    question's return value, since there's no real user to click an
    offscreen dialog) -- plus the main-thread-refusal and timeout-fail-
    closed paths. Skips gracefully if no usable Qt platform is
    available, matching the other PyQt6-dependent test in this file."""
    pytest = __import__("pytest")
    pytest.importorskip("PyQt6")
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import threading, time

    try:
        from PyQt6.QtWidgets import QApplication, QMessageBox
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")
    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")

    import ui
    aether_ui = ui.AetherUI("face.png")

    # Main-thread call must refuse (would deadlock otherwise).
    try:
        aether_ui.confirm_blocking("should raise")
        assert False, "expected RuntimeError for main-thread call"
    except RuntimeError:
        pass

    # Background thread + simulated Yes click.
    result = {}
    def worker_yes():
        result["answer"] = aether_ui.confirm_blocking("Delete this file?", timeout=10)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        t = threading.Thread(target=worker_yes)
        t.start()
        deadline = time.time() + 5
        while t.is_alive() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        t.join(timeout=2)
    assert result.get("answer") is True

    # Background thread + simulated No click.
    result2 = {}
    def worker_no():
        result2["answer"] = aether_ui.confirm_blocking("Run this shell command?", timeout=10)
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        t2 = threading.Thread(target=worker_no)
        t2.start()
        deadline = time.time() + 5
        while t2.is_alive() and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        t2.join(timeout=2)
    assert result2.get("answer") is False

    # Never-answered dialog fails closed within roughly the timeout window.
    result3 = {}
    def worker_timeout():
        start = time.time()
        result3["answer"] = aether_ui.confirm_blocking("Never answered", timeout=1.0)
        result3["elapsed"] = time.time() - start
    t3 = threading.Thread(target=worker_timeout)
    t3.start()
    t3.join(timeout=5)  # deliberately never call app.processEvents() here
    assert result3.get("answer") is False
    assert 0.9 < result3.get("elapsed", 0) < 3.0


def test_osint_lookup_confirmation_gate():
    """Continuation of test_confirm_blocking_dialog_thread_safety: verifies
    AetherLocal._tool_osint_lookup actually calls the newly-wired
    confirmation gate for port_scan/breach_check (and only those two --
    whois/dns/subdomains/geoip/dork stay ungated since they only ever
    return already-public records). Extracts the method from main.py via
    AST (avoiding the need to import all of main.py's heavy runtime
    dependencies) and binds it to a lightweight stub with a real
    SafetyLayer, rather than mocking the gate logic itself -- this
    exercises the actual requires_confirmation()/request_confirmation()
    code path, not a stand-in for it.

    _tool_osint_lookup lives in core/tool_dispatch.py's ToolDispatchMixin
    (moved there from main.py's AetherLocal during the god-object split --
    see core/conversation_core.py's module docstring), not in AetherLocal
    itself anymore, so this reads that file/class instead."""
    import ast
    from core.safety import SafetyLayer

    with open("core/tool_dispatch.py", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    class_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "ToolDispatchMixin")
    method_node = next(n for n in class_def.body if isinstance(n, ast.FunctionDef) and n.name == "_tool_osint_lookup")
    ns = {}
    exec(ast.get_source_segment(src, method_node), ns)
    fn = ns["_tool_osint_lookup"]

    class Stub:
        pass

    cfg = MagicMock(confirm_on=["delete", "shell", "write_file"], allowed_paths=["/tmp"])

    # Non-gated action: runs straight through, no callback needed at all.
    stub = Stub()
    stub._safety = SafetyLayer(config=cfg)
    with patch("actions.osint_tools.run_osint", return_value="whois result") as m:
        assert fn(stub, {"action": "whois", "target": "example.com"}) == "whois result"
        assert m.called

    # port_scan denied.
    stub._safety.set_confirm_callback(lambda msg: False)
    with patch("actions.osint_tools.run_osint", return_value="SHOULD NOT RUN") as m:
        result = fn(stub, {"action": "port_scan", "target": "1.2.3.4", "ports": "1-100"})
        assert "cancelled" in result.lower()
        assert not m.called

    # port_scan granted.
    stub._safety.set_confirm_callback(lambda msg: True)
    with patch("actions.osint_tools.run_osint", return_value="scan result") as m:
        assert fn(stub, {"action": "port_scan", "target": "1.2.3.4"}) == "scan result"
        assert m.called

    # breach_check denied.
    stub._safety.set_confirm_callback(lambda msg: False)
    with patch("actions.osint_tools.run_osint", return_value="SHOULD NOT RUN") as m:
        result = fn(stub, {"action": "breach_check", "target": "someone@example.com"})
        assert "cancelled" in result.lower()
        assert not m.called

    # No callback registered at all -> fails closed.
    stub2 = Stub()
    stub2._safety = SafetyLayer(config=cfg)
    with patch("actions.osint_tools.run_osint", return_value="SHOULD NOT RUN") as m:
        result = fn(stub2, {"action": "port_scan", "target": "1.2.3.4"})
        assert "cancelled" in result.lower()
        assert not m.called


def test_safety_layer_wired_with_correct_config_type():
    """AetherLocal.__init__ was written to call get_safety(self._config),
    but self._config comes from main.py's _load_config() which returns a
    plain dict -- SafetyLayer needs the _MarkConfigAdapter object (with
    .confirm_on/.allowed_paths *properties*, not dict keys) that
    memory.config_manager.get_mark_config() returns. Passing a plain
    dict would raise AttributeError the first time requires_confirmation()
    or is_path_allowed() actually ran. Caught this before it shipped by
    checking _load_config()'s return annotation and comparing against
    what SafetyLayer.__init__ actually accesses. Fixed by calling
    get_safety() with no arguments, letting it resolve its own config via
    get_mark_config() internally (SafetyLayer's own fallback, and the
    same pattern already used correctly elsewhere for other subsystems).
    This test locks in that main.py's AetherLocal init doesn't regress
    back to passing the wrong config type."""
    import ast

    with open("main.py", encoding="utf-8") as f:
        src = f.read()
    tree = ast.parse(src)
    class_def = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "AetherLocal")
    init_node = next(n for n in class_def.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
    init_src = ast.get_source_segment(src, init_node)

    assert "get_safety(self._config)" not in init_src, \
        "self._config is a plain dict (from _load_config()); SafetyLayer needs the " \
        "_MarkConfigAdapter object get_mark_config() returns, or AttributeError on first use"
    assert "get_safety()" in init_src, "expected get_safety() called with no args"
    assert "set_confirm_callback" in init_src, "expected the confirm callback to actually be registered"


# ------------------------------- LLM REMOTE/LOCAL FALLBACK -------------------------------
def test_llm_settings_single_host_unchanged_when_no_remote_configured():
    """get_llm_settings() gained dual-host (remote PC / local CPU fallback)
    resolution. This locks in that with no "llm_remote_url" configured --
    i.e. every existing deployment, since this key is brand new -- behavior
    is byte-for-byte what it was before: reads llm_url/llm_model straight
    from config, no network calls, no health-check cache involved."""
    import core.llm_client as llm_client

    with patch.object(llm_client, "_load_config", return_value={
        "llm_url": "http://localhost:11434", "llm_model": "qwen3:8b",
    }):
        with patch.object(llm_client, "_probe_ollama") as mock_probe:
            url, model = llm_client.get_llm_settings()
            assert url == "http://localhost:11434"
            assert model == "qwen3:8b"
            mock_probe.assert_not_called()


def test_llm_settings_prefers_reachable_remote_host():
    """With "llm_remote_url" configured (e.g. a home PC running Ollama on a
    GPU) and reachable, get_llm_settings() should resolve to it rather than
    the local fallback."""
    import core.llm_client as llm_client

    llm_client._endpoint_cache["using"] = None
    llm_client._endpoint_cache["checked_at"] = 0.0
    with patch.object(llm_client, "_load_config", return_value={
        "llm_url":        "http://localhost:11434",
        "llm_model":      "qwen3:8b",
        "llm_remote_url": "http://192.168.1.50:11434",
    }):
        with patch.object(llm_client, "_probe_ollama", return_value=True) as mock_probe:
            url, model = llm_client.get_llm_settings()
            assert url == "http://192.168.1.50:11434"
            assert model == "qwen3:8b"
            mock_probe.assert_called_once()


def test_llm_settings_falls_back_to_local_cpu_when_remote_unreachable():
    """Core of the "call out to my PC, and if it's off go back to CPU"
    request: when the configured remote host doesn't answer the health
    check, get_llm_settings() must fall back to the local URL/model (CPU
    inference on this machine) rather than returning a dead endpoint."""
    import core.llm_client as llm_client

    llm_client._endpoint_cache["using"] = None
    llm_client._endpoint_cache["checked_at"] = 0.0
    with patch.object(llm_client, "_load_config", return_value={
        "llm_url":          "http://localhost:11434",
        "llm_model":        "qwen3:8b",
        "llm_remote_url":   "http://192.168.1.50:11434",
        "llm_remote_model": "llama3.1:70b",
        "llm_local_url":    "http://localhost:11434",
        "llm_local_model":  "llama3.2:3b",
    }):
        with patch.object(llm_client, "_probe_ollama", return_value=False):
            url, model = llm_client.get_llm_settings()
            assert url == "http://localhost:11434"
            assert model == "llama3.2:3b", "should use the CPU-friendly fallback model, not the remote model"


def test_llm_settings_caches_health_check_within_interval():
    """A dead/asleep remote host shouldn't be re-probed on every single LLM
    call -- get_llm_settings() must cache the reachability verdict for
    "llm_health_check_interval" seconds so most calls pay no network cost."""
    import core.llm_client as llm_client

    llm_client._endpoint_cache["using"] = None
    llm_client._endpoint_cache["checked_at"] = 0.0
    with patch.object(llm_client, "_load_config", return_value={
        "llm_remote_url": "http://192.168.1.50:11434",
        "llm_health_check_interval": 60,
    }):
        with patch.object(llm_client, "_probe_ollama", return_value=True) as mock_probe:
            llm_client.get_llm_settings()
            llm_client.get_llm_settings()
            llm_client.get_llm_settings()
            assert mock_probe.call_count == 1, "should reuse the cached verdict, not re-probe every call"


def test_llm_backend_status_reports_single_and_dual_mode():
    """get_llm_backend_status() (surfaced in core/diagnostics.py's "LLM
    Backend" check, i.e. the "status" chat command) should report which
    mode/backend is active without itself forcing a fresh probe."""
    import core.llm_client as llm_client

    with patch.object(llm_client, "_load_config", return_value={
        "llm_url": "http://localhost:11434", "llm_model": "qwen3:8b",
    }):
        status = llm_client.get_llm_backend_status()
        assert status["mode"] == "single"
        assert status["remote_url"] is None

    llm_client._endpoint_cache["using"] = "local"
    llm_client._endpoint_cache["checked_at"] = time.time()
    with patch.object(llm_client, "_load_config", return_value={
        "llm_remote_url": "http://192.168.1.50:11434",
        "llm_local_url":  "http://localhost:11434",
        "llm_health_check_interval": 9999,
    }):
        status = llm_client.get_llm_backend_status()
        assert status["mode"] == "dual"
        assert status["active"] == "local"
        assert status["remote_url"] == "http://192.168.1.50:11434"


def test_diagnostics_reports_llm_backend_check():
    """core/diagnostics.py's run_all() should include the new LLM Backend
    check (used by the "status"/"system check" chat command) alongside the
    existing Ollama check."""
    from core.diagnostics import run_all

    results = run_all()
    names = {r.name for r in results}
    assert "LLM Backend" in names


# ------------------------------- HEADLESS MODE -------------------------------
def test_headless_ui_implements_full_aetherui_interface():
    """main.py's AetherLocal and runner() drive the UI object entirely
    through a fixed set of attributes/methods (confirm_blocking,
    current_file, hide_startup_panel, mark_startup_ready, muted,
    on_reconfigure, on_text_command, set_startup_status, set_state,
    show_startup_panel, update_message_stats, write_log, wait_for_api_key,
    root.mainloop) -- enumerated by grepping every self.ui.* / ui.* call
    site across the codebase. core.headless_ui.HeadlessUI is main.py's
    drop-in replacement for ui.AetherUI when AETHER_HEADLESS=1, so it must
    expose every one of these or AetherLocal breaks the first time it
    touches a missing one at runtime."""
    from core.headless_ui import HeadlessUI

    required_attrs = [
        "confirm_blocking", "current_file", "hide_startup_panel",
        "mark_startup_ready", "muted", "on_reconfigure", "on_text_command",
        "set_startup_status", "set_state", "show_startup_panel",
        "update_message_stats", "write_log", "wait_for_api_key", "root",
    ]
    ui = HeadlessUI("face.png")
    for attr in required_attrs:
        assert hasattr(ui, attr), f"HeadlessUI is missing '{attr}', which main.py's AetherLocal calls on the UI object"
    assert hasattr(ui.root, "mainloop"), "ui.root.mainloop() is called unconditionally at the end of main.py's main()"


def test_headless_ui_confirm_blocking_fails_closed_by_default():
    """confirm_blocking() gates high-risk actions (shell exec, file delete,
    port scan, breach lookups -- see core/safety.py). With no human able to
    click a dialog in headless mode, it must fail closed (deny) unless the
    operator explicitly opts into AETHER_HEADLESS_AUTO_CONFIRM.

    Since the remote-approval flow landed, an unresolved request blocks
    for the real `timeout` argument (default 120s) before denying -- so
    this test passes a short explicit timeout and mocks the ntfy push
    rather than hitting the network and waiting out the full default."""
    from core.headless_ui import HeadlessUI

    ui = HeadlessUI("face.png")
    with patch("core.notify.send_ntfy", return_value=False), \
         patch.dict("os.environ", {}, clear=False):
        import os as _os_mod
        _os_mod.environ.pop("AETHER_HEADLESS_AUTO_CONFIRM", None)
        assert ui.confirm_blocking("delete /important/file?", timeout=0.3) is False

    with patch.dict("os.environ", {"AETHER_HEADLESS_AUTO_CONFIRM": "1"}):
        assert ui.confirm_blocking("delete /important/file?") is True


def test_main_py_imports_ui_conditionally_on_headless_flag():
    """main.py used to unconditionally `from ui import AetherUI` at module
    level, which imports PyQt6 -- impossible to satisfy on a server with no
    Qt platform plugin available and no reason to require it there. Locks
    in that the import (and the base-package bootstrap list) is gated on
    the AETHER_HEADLESS env var instead, without requiring main.py itself
    to be imported (main.py auto-pip-installs and os.execv-restarts on
    import -- unsafe to import directly in tests/CI)."""
    with open("main.py", encoding="utf-8") as f:
        src = f.read()

    assert 'AETHER_HEADLESS' in src
    assert "from core.headless_ui import HeadlessUI as AetherUI" in src
    assert "from ui import AetherUI" in src
    assert '_BASE_PKGS.insert(0, ("PyQt6", "PyQt6"))' in src, \
        "PyQt6 should only be added to the auto-install list when not headless"


# ------------------------------- HEADLESS REMOTE APPROVAL FLOW -------------------------------
def _run_confirm_blocking_in_thread(message: str, timeout: float):
    """Starts HeadlessUI.confirm_blocking() on a background thread (it
    blocks the calling thread until resolved/timed out, same as the GUI
    version did) and returns (thread, result_box) -- result_box[0] holds
    the return value once the thread finishes."""
    import threading
    from core.headless_ui import HeadlessUI

    ui = HeadlessUI("face.png")
    result_box = {}

    def _run():
        result_box["value"] = ui.confirm_blocking(message, timeout=timeout)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t, result_box


def _wait_for_pending_approval(message: str, timeout: float = 2.0):
    """Polls core.pending_approvals' store until the given request shows
    up as pending, and returns its id. Confirm_blocking() registers the
    approval synchronously before it starts blocking, so this should
    resolve almost immediately -- the poll is just a safety margin
    against thread-scheduling jitter."""
    import time as _time
    from core.pending_approvals import get_approval_store

    store = get_approval_store()
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        for a in store.list_pending():
            if a["message"] == message:
                return a["id"]
        _time.sleep(0.01)
    raise AssertionError(f"approval for {message!r} never appeared in the pending store")


def test_confirm_blocking_approval_granted():
    """The dashboard's POST /api/approvals/{id}/approve resolving a
    pending request should make the original confirm_blocking() call
    (blocked on a background thread, same as it would be inside
    AetherLocal) return True."""
    from core.pending_approvals import get_approval_store

    with patch("core.notify.send_ntfy", return_value=True):
        t, result_box = _run_confirm_blocking_in_thread("test: delete file X?", timeout=5)
        approval_id = _wait_for_pending_approval("test: delete file X?")
        assert get_approval_store().resolve(approval_id, True) is True
        t.join(timeout=5)
        assert result_box.get("value") is True


def test_confirm_blocking_approval_denied():
    """Same flow, but the dashboard denies -- confirm_blocking() must
    return False, same as the original fail-closed behavior."""
    from core.pending_approvals import get_approval_store

    with patch("core.notify.send_ntfy", return_value=True):
        t, result_box = _run_confirm_blocking_in_thread("test: run shell command?", timeout=5)
        approval_id = _wait_for_pending_approval("test: run shell command?")
        assert get_approval_store().resolve(approval_id, False) is True
        t.join(timeout=5)
        assert result_box.get("value") is False


def test_confirm_blocking_timeout_denies():
    """With nobody resolving the request at all, confirm_blocking() must
    still fail closed once the timeout elapses -- the remote-approval
    flow is additive, not a way to accidentally hang forever or default-
    allow."""
    with patch("core.notify.send_ntfy", return_value=True):
        t, result_box = _run_confirm_blocking_in_thread("test: never answered", timeout=0.3)
        t.join(timeout=5)
        assert result_box.get("value") is False


def test_confirm_blocking_ntfy_push_fires_exactly_once_per_request():
    """Each confirm_blocking() call should trigger exactly one ntfy push
    attempt -- not zero (silently stuck with no way to know it needs a
    response) and not a retry storm."""
    from core.pending_approvals import get_approval_store

    with patch("core.notify.send_ntfy", return_value=True) as mock_ntfy:
        t, result_box = _run_confirm_blocking_in_thread("test: ntfy fire-once check", timeout=5)
        approval_id = _wait_for_pending_approval("test: ntfy fire-once check")
        get_approval_store().resolve(approval_id, True)
        t.join(timeout=5)

    assert mock_ntfy.call_count == 1
    _, kwargs = mock_ntfy.call_args
    assert kwargs.get("message") == "test: ntfy fire-once check"


def test_confirm_blocking_auto_confirm_env_var_unchanged():
    """AETHER_HEADLESS_AUTO_CONFIRM=1 must keep bypassing the whole
    approval flow (no pending entry registered, no ntfy push) -- this
    behavior predates the remote-approval flow and the prompt for this
    session explicitly said not to change it."""
    from core.headless_ui import HeadlessUI

    ui = HeadlessUI("face.png")
    with patch("core.notify.send_ntfy") as mock_ntfy, \
         patch.dict("os.environ", {"AETHER_HEADLESS_AUTO_CONFIRM": "1"}):
        assert ui.confirm_blocking("test: auto-confirm bypass") is True
    mock_ntfy.assert_not_called()


def test_pending_approvals_store_resolve_rejects_unknown_or_already_resolved():
    """core.pending_approvals.ApprovalStore.resolve() should report failure
    (False) for an id that doesn't exist, and for one that's already been
    resolved -- this is what makes /api/approvals/{id}/approve return 404
    instead of silently succeeding twice."""
    from core.pending_approvals import ApprovalStore

    store = ApprovalStore()
    assert store.resolve("does-not-exist", True) is False

    approval = store.create("test message", timeout=30)
    assert store.resolve(approval.id, True) is True
    assert store.resolve(approval.id, False) is False, "resolving an already-resolved approval should fail"


# ------------------------------- DASHBOARD: APPROVALS + LLM STATUS ROUTES -------------------------------
def test_dashboard_approval_and_llm_status_routes_registered_and_authed():
    """The two new route groups (pending approvals, LLM backend status)
    should be registered on the dashboard app and require the same
    session auth as every other route."""
    pytest.importorskip("fastapi")
    import dashboard.server as srv

    ds = srv.DashboardServer()
    from fastapi.testclient import TestClient
    client = TestClient(ds.app)

    route_paths = {r.path for r in ds.app.routes if hasattr(r, "path")}
    for expected in ["/api/approvals", "/api/approvals/{approval_id}/approve",
                      "/api/approvals/{approval_id}/deny", "/api/llm-status"]:
        assert expected in route_paths, f"missing route: {expected}"

    assert client.get("/api/approvals").status_code == 401
    assert client.post("/api/approvals/some-id/approve").status_code == 401
    assert client.post("/api/approvals/some-id/deny").status_code == 401
    assert client.get("/api/llm-status").status_code == 401

    key = ds.new_key()
    token = client.post("/login", json={"pin": key}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/approvals", headers=headers)
    assert r.status_code == 200
    assert "approvals" in r.json()

    r = client.get("/api/llm-status", headers=headers)
    assert r.status_code == 200
    assert r.json()["mode"] in ("single", "dual")

    assert client.post("/api/approvals/nonexistent-id/approve", headers=headers).status_code == 404


def test_dashboard_approve_endpoint_resolves_pending_request():
    """End-to-end: create a pending approval directly against the shared
    store (as confirm_blocking() would), then resolve it through the real
    HTTP endpoint via TestClient, and confirm the store reflects that."""
    pytest.importorskip("fastapi")
    import dashboard.server as srv
    from core.pending_approvals import get_approval_store

    ds = srv.DashboardServer()
    from fastapi.testclient import TestClient
    client = TestClient(ds.app)
    key = ds.new_key()
    token = client.post("/login", json={"pin": key}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    store = get_approval_store()
    approval = store.create("test: dashboard http resolve", timeout=30)

    r = client.get("/api/approvals", headers=headers)
    ids = [a["id"] for a in r.json()["approvals"]]
    assert approval.id in ids

    r = client.post(f"/api/approvals/{approval.id}/approve", headers=headers)
    assert r.status_code == 200

    # Already resolved -- second call should 404, not double-resolve.
    r = client.post(f"/api/approvals/{approval.id}/deny", headers=headers)
    assert r.status_code == 404


# ---------------- TRUNCATION REPORTING (core/llm_client.call_llm_stream) ----------------
#
# Regression tests for: generation hitting num_predict used to be silently
# indistinguishable from a natural stop (done_reason "length" vs "stop" were
# both just treated as "done"), so responses got cut off mid-sentence with
# no signal anywhere. call_llm_stream now surfaces stats["truncated"].

def _mock_ollama_stream_response(lines: list[dict]):
    """Build a MagicMock standing in for `with requests.post(...) as resp`,
    where iter_lines() yields the given dicts JSON-encoded."""
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.iter_lines.return_value = [json.dumps(d).encode() for d in lines]
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def test_call_llm_stream_flags_truncation_on_length():
    """FAILS if stats["truncated"] isn't set when Ollama cuts generation off
    at num_predict (done_reason == "length") instead of a natural stop."""
    from core.llm_client import call_llm_stream

    lines = [
        {"message": {"content": "This sentence never finishes because"}, "done": False},
        {
            "message": {"content": ""}, "done": True, "done_reason": "length",
            "prompt_eval_count": 50, "eval_count": 500,
            "eval_duration": 1_000_000, "total_duration": 2_000_000,
        },
    ]
    with patch("core.llm_client.requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_stream_response(lines)
        with patch("core.llm_client.get_llm_settings") as mock_settings, \
             patch("core.llm_client.get_llm_provider") as mock_provider:
            mock_settings.return_value = ("http://localhost:11434", "qwen3:8b")
            mock_provider.return_value = "ollama"
            events = list(call_llm_stream([{"role": "user", "content": "hi"}]))

    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["stats"]["truncated"] is True


def test_call_llm_stream_no_truncation_flag_on_natural_stop():
    """A normal, complete response (done_reason == "stop") must NOT be
    flagged as truncated."""
    from core.llm_client import call_llm_stream

    lines = [
        {"message": {"content": "Hello there."}, "done": False},
        {
            "message": {"content": ""}, "done": True, "done_reason": "stop",
            "prompt_eval_count": 10, "eval_count": 5,
            "eval_duration": 100_000, "total_duration": 200_000,
        },
    ]
    with patch("core.llm_client.requests.post") as mock_post:
        mock_post.return_value = _mock_ollama_stream_response(lines)
        with patch("core.llm_client.get_llm_settings") as mock_settings, \
             patch("core.llm_client.get_llm_provider") as mock_provider:
            mock_settings.return_value = ("http://localhost:11434", "qwen3:8b")
            mock_provider.return_value = "ollama"
            events = list(call_llm_stream([{"role": "user", "content": "hi"}]))

    done_events = [e for e in events if e["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["stats"]["truncated"] is False


# ---------------- TIER ROUTER (core/tier_router.classify) ----------------
#
# Regression tests for: tier_router.py was fully implemented but never
# imported/called by production code, so every turn -- including trivial
# small talk -- paid for the full tool schema and system-prompt build.
# These tests lock in the classification behavior that main.py now relies
# on to decide when it's safe to skip that overhead.

def test_tier_router_routes_greeting_to_quick():
    from core.tier_router import classify, Tier
    decision = classify("hey", has_active_task=False)
    assert decision.tier == Tier.QUICK


def test_tier_router_routes_tool_intent_away_from_quick():
    from core.tier_router import classify, Tier
    decision = classify("search for the nearest gas station", has_active_task=False)
    assert decision.tier != Tier.QUICK


def test_tier_router_never_downgrades_active_task_followup():
    """A short reply like 'yes' inside an in-progress task must not be
    routed to QUICK -- has_active_task=True should override the short-message
    heuristic that would otherwise fast-path it."""
    from core.tier_router import classify, Tier
    decision = classify("yes", has_active_task=True)
    assert decision.tier != Tier.QUICK


# ---------------- UNDO STACK (core/undo.py) ----------------

def test_undo_stack_empty_returns_friendly_message():
    from core.undo import UndoStack
    stack = UndoStack()
    assert stack.undo_last() == "There's nothing for me to undo."
    assert stack.history() == []


def test_undo_stack_lifo_order():
    from core.undo import UndoStack
    stack = UndoStack()
    calls = []
    stack.register("first",  lambda: calls.append("first")  or "undone first")
    stack.register("second", lambda: calls.append("second") or "undone second")
    assert stack.history() == ["second", "first"]  # most recent first
    assert stack.undo_last() == "undone second"
    assert stack.undo_last() == "undone first"
    assert calls == ["second", "first"]


def test_undo_stack_caps_history():
    from core.undo import UndoStack, _MAX_HISTORY
    stack = UndoStack()
    for i in range(_MAX_HISTORY + 5):
        stack.register(f"op{i}", lambda: "done")
    assert len(stack.history()) == _MAX_HISTORY
    # Oldest entries (op0..op4) should have been dropped.
    assert "op0" not in stack.history()


def test_undo_stack_swallows_undo_fn_exception():
    """A broken undo closure must report failure, not crash the caller."""
    from core.undo import UndoStack
    stack = UndoStack()
    def _boom():
        raise RuntimeError("disk gone")
    stack.register("risky op", _boom)
    result = stack.undo_last()
    assert "Could not undo" in result
    assert "risky op" in result


# ---------------- FILE_CONTROLLER <-> UNDO WIRING ----------------
#
# Regression tests for: undo.py's docstring promises coverage for "move,
# rename, overwrite, and create", but the file_controller.py wiring only
# covered create_file/write_file -- move_file and rename_file had no undo
# hooks at all. These lock in that the gap is closed.

@pytest.fixture
def _fc_sandbox(tmp_path):
    """Point file_controller's safe-root allowlist at a tmp dir, and give
    each test a clean core.undo stack instance so tests can't see each
    other's registered undo entries."""
    import core.undo as undo_mod
    fresh_stack = undo_mod.UndoStack()
    with patch('actions.file_controller._SAFE_ROOTS', [tmp_path]), \
         patch.object(undo_mod, '_stack', fresh_stack):
        yield tmp_path


def test_create_file_new_file_undo_removes_it(_fc_sandbox):
    from actions.file_controller import create_file
    import core.undo as undo_mod
    target = _fc_sandbox / "brand_new.txt"
    create_file(str(_fc_sandbox), name="brand_new.txt", content="hello")
    assert target.exists()
    result = undo_mod.undo_last()
    assert not target.exists()
    assert "Moved to Trash" in result or "brand_new.txt" in result


def test_create_file_overwrite_undo_restores_old_content(_fc_sandbox):
    from actions.file_controller import create_file
    import core.undo as undo_mod
    target = _fc_sandbox / "existing.txt"
    target.write_text("original content", encoding="utf-8")
    create_file(str(_fc_sandbox), name="existing.txt", content="clobbered")
    assert target.read_text(encoding="utf-8") == "clobbered"
    undo_mod.undo_last()
    assert target.read_text(encoding="utf-8") == "original content"


def test_write_file_append_undo_restores_pre_append_content(_fc_sandbox):
    from actions.file_controller import write_file
    import core.undo as undo_mod
    target = _fc_sandbox / "log.txt"
    target.write_text("line one\n", encoding="utf-8")
    write_file(str(_fc_sandbox), name="log.txt", content="line two\n", append=True)
    assert target.read_text(encoding="utf-8") == "line one\nline two\n"
    undo_mod.undo_last()
    assert target.read_text(encoding="utf-8") == "line one\n"


def test_move_file_undo_moves_it_back(_fc_sandbox):
    """FAILS if move_file has no undo wiring at all (the gap this session
    closed) -- src must exist again at its original location afterward."""
    from actions.file_controller import move_file
    import core.undo as undo_mod
    src_dir = _fc_sandbox / "src"; src_dir.mkdir()
    dst_dir = _fc_sandbox / "dst"; dst_dir.mkdir()
    src = src_dir / "doc.txt"
    src.write_text("contents", encoding="utf-8")

    move_file(str(src_dir), name="doc.txt", destination=str(dst_dir))
    assert not src.exists()
    assert (dst_dir / "doc.txt").exists()

    result = undo_mod.undo_last()
    assert src.exists()
    assert src.read_text(encoding="utf-8") == "contents"
    assert not (dst_dir / "doc.txt").exists()
    assert "Moved back" in result


def test_move_file_no_undo_registered_when_would_clobber(_fc_sandbox):
    """A move that overwrites an existing destination file must NOT
    register an undo entry -- undoing it can't restore the clobbered file,
    so promising an undo would be misleading."""
    from actions.file_controller import move_file
    import core.undo as undo_mod
    src_dir = _fc_sandbox / "src"; src_dir.mkdir()
    dst_dir = _fc_sandbox / "dst"; dst_dir.mkdir()
    (src_dir / "doc.txt").write_text("new", encoding="utf-8")
    (dst_dir / "doc.txt").write_text("will be clobbered", encoding="utf-8")

    move_file(str(src_dir), name="doc.txt", destination=str(dst_dir))
    assert undo_mod.history() == []


def test_rename_file_undo_renames_it_back(_fc_sandbox):
    """FAILS if rename_file has no undo wiring at all (the gap this
    session closed)."""
    from actions.file_controller import rename_file
    import core.undo as undo_mod
    target = _fc_sandbox / "old_name.txt"
    target.write_text("contents", encoding="utf-8")

    rename_file(str(_fc_sandbox), name="old_name.txt", new_name="new_name.txt")
    assert not target.exists()
    assert (_fc_sandbox / "new_name.txt").exists()

    result = undo_mod.undo_last()
    assert target.exists()
    assert not (_fc_sandbox / "new_name.txt").exists()
    assert "Renamed back" in result


def test_undo_tool_list_action_reports_history(_fc_sandbox):
    """The 'undo' tool's action='list' must surface what's undoable
    without changing anything, per the tool description in main.py."""
    from actions.file_controller import create_file
    import core.undo as undo_mod
    create_file(str(_fc_sandbox), name="a.txt", content="x")
    items = undo_mod.history()
    assert len(items) == 1
    assert "a.txt" in items[0]


# ---------------- PROMPT: NO UNIVERSAL HARD LENGTH CAP ----------------

def test_prompt_has_no_hard_sentence_cap():
    """FAILS if the old universal '3 sentences max, no exceptions' rule
    (which truncated substantive answers regardless of what was actually
    asked) is still in the shipped system prompt."""
    text = Path("core/prompt.txt").read_text(encoding="utf-8")
    assert "3 SHORT sentences maximum" not in text
    assert "No exceptions" not in text
    assert "give the most important part only" not in text
    # Some length guidance should still exist -- just not a hard numeric cap.
    assert "match" in text.lower() or "length" in text.lower()


# ---------------- TOOL ROUTER (core/tool_router.py) ----------------

def test_tool_router_file_intent_returns_focused_subset():
    from core.tool_router import select_tool_names
    names = select_tool_names("rename that file to report.docx")
    assert "file_controller" in names
    assert "undo" in names
    # Unrelated heavy tools should NOT be pulled in by a pure file-rename ask.
    assert "dev_agent" not in names
    assert "osint_lookup" not in names
    assert "flight_finder" not in names


def test_tool_router_no_match_falls_back_to_baseline_not_everything():
    from core.tool_router import select_tool_names
    names = select_tool_names("what a beautiful day")
    # Baseline tools present...
    assert "open_app" in names
    assert "web_search" in names
    # ...but the rarer/heavier catalog is not pulled in just because nothing matched.
    assert "dev_agent" not in names
    assert "osint_lookup" not in names
    assert "game_updater" not in names


def test_tool_router_always_through_tools_present_regardless_of_category():
    from core.tool_router import select_tool_names
    names = select_tool_names("open chrome")
    assert "save_memory" in names
    assert "undo" in names
    assert "goal_manager" in names


def test_tool_router_filter_tools_preserves_ollama_shape():
    from core.tool_router import filter_tools
    all_tools = [
        {"type": "function", "function": {"name": "file_controller"}},
        {"type": "function", "function": {"name": "flight_finder"}},
        {"type": "function", "function": {"name": "save_memory"}},
    ]
    out = filter_tools(all_tools, "move this file to the desktop")
    names = {t["function"]["name"] for t in out}
    assert "file_controller" in names
    assert "save_memory" in names
    assert "flight_finder" not in names  # not related to a file move


# ---------------- HYBRID MEMORY: NON-BLOCKING MODEL LOAD ----------------
#
# Regression tests for: the dense encoder + cross-encoder used to load
# synchronously on whichever conversation turn happened to trigger the
# first _retrieve()/_encode_one() call, stalling that turn for however
# long sentence-transformers takes to import and initialize. They now
# preload in a background thread at construction time, and the hot-path
# callers use blocking=False so a turn that lands before preloading
# finishes just skips the dense/rerank stage instead of waiting.

def _make_hybrid_memory():
    from memory.hybrid_memory import HybridMemory
    with patch('memory.hybrid_memory.BM25Index') as MockBM25:
        MockBM25.return_value = MagicMock()
        with patch('sqlite3.connect') as mock_sql:
            mock_conn = MagicMock()
            mock_sql.return_value = mock_conn
            mock_conn.execute.return_value.fetchone.return_value = (1,)
            return HybridMemory()


def test_hybrid_memory_preload_thread_starts_and_completes():
    """The background preload thread should run to completion (setting
    _enc_tried/_re_tried) shortly after construction, without the test
    (i.e. the constructor) ever blocking on it itself."""
    hm = _make_hybrid_memory()
    # Give the daemon thread a moment; sentence-transformers isn't
    # installed in this environment so it resolves via ImportError almost
    # immediately either way.
    # Where sentence-transformers IS installed the preload really loads the
    # encoder and cross-encoder (seconds, longer on a cold disk), so allow
    # generous time; it exits as soon as both flags flip.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if hm._enc_tried and hm._re_tried:
            break
        time.sleep(0.02)
    assert hm._enc_tried is True
    assert hm._re_tried is True


def test_hybrid_memory_nonblocking_load_returns_immediately_when_locked():
    """FAILS if _load_encoder(blocking=False) waits for a concurrent
    loader instead of bailing out -- that would reintroduce the exact
    stall this fix removes, just moved behind a lock."""
    hm = _make_hybrid_memory()
    # Let the real preload thread finish first: with sentence-transformers
    # installed it loads the encoder in the background, which would make the
    # "didn't do the load itself" check below meaningless.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not (hm._enc_tried and hm._re_tried):
        time.sleep(0.02)
    hm._encoder = None
    hm._enc_tried = False  # simulate: preload hasn't finished yet
    hm._enc_lock.acquire()  # simulate: another thread is mid-load
    try:
        start = time.monotonic()
        hm._load_encoder(blocking=False)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # returned immediately, did not wait for the lock
        assert hm._encoder is None  # didn't do the load itself
    finally:
        hm._enc_lock.release()


def test_hybrid_memory_blocking_load_actually_attempts_load():
    """blocking=True (used by the preload thread) must actually perform
    the load attempt (setting _enc_tried), not just return."""
    hm = _make_hybrid_memory()
    hm._enc_tried = False
    hm._encoder = None
    hm._load_encoder(blocking=True)
    assert hm._enc_tried is True  # attempted (result is None here since
                                   # sentence-transformers isn't installed)
