"""Regression tests for the runtime-audit findings.

1. A tool the turn did not offer must not execute (allowlist enforced at execution).
2. Malformed tool arguments must be rejected, not silently run as {}.
3. Stop must prevent the remaining calls in a batch from starting.
4. The executor must not turn an unknown tool name into generated code.
5. The executor must not report success for a run it could not verify.
6. shutdown_aether must be gated; import_memory must accept the kwarg its callers use.
"""
import inspect
import subprocess
import threading
from contextlib import contextmanager

import pytest

from core.conversation_core import ConversationCoreMixin


class _UI:
    def set_state(self, _s):
        pass

    def write_log(self, _m):
        pass


class _Obs:
    @contextmanager
    def span(self, *_a, **_k):
        yield self

    def set(self, *_a, **_k):
        pass

    def count(self, *_a, **_k):
        pass


class _Conv(ConversationCoreMixin):
    def __init__(self):
        self.ui = _UI()
        self._obs = _Obs()
        self._loop_guard = None
        self._conversation = []
        self._cancel_current_turn = threading.Event()
        self.executed = []

    def _execute_tool(self, name, args):
        self.executed.append((name, args))
        return "ok"


def _call(name, args, cid="c1"):
    return {"id": cid, "function": {"name": name, "arguments": args}}


def _tool_msgs(messages):
    return [m for m in messages if m.get("role") == "tool"]


# ── 1. allowlist ──────────────────────────────────────────────────────────

def test_tool_not_offered_this_turn_is_rejected():
    c, messages = _Conv(), []
    c._execute_tool_calls([_call("computer_control", {"action": "click"})], messages,
                          allowed_tools={"web_search"})
    assert c.executed == []
    msg = _tool_msgs(messages)[0]
    assert "not_authorized_for_turn" in msg["content"]
    assert msg["tool_call_id"] == "c1"          # pairing preserved for the provider


def test_tool_offered_this_turn_still_runs():
    c, messages = _Conv(), []
    c._execute_tool_calls([_call("web_search", {"query": "x"})], messages,
                          allowed_tools={"web_search"})
    assert c.executed == [("web_search", {"query": "x"})]


def test_empty_allowlist_rejects_everything():
    c, messages = _Conv(), []
    c._execute_tool_calls([_call("shutdown_aether", {})], messages, allowed_tools=set())
    assert c.executed == []


# ── 2. malformed arguments ────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ['{"path": "a"', "not json", "[1, 2]", '"str"'])
def test_malformed_arguments_are_not_executed(raw):
    c, messages = _Conv(), []
    c._execute_tool_calls([_call("file_controller", raw)], messages, allowed_tools={"file_controller"})
    assert c.executed == []
    assert "malformed_tool_arguments" in _tool_msgs(messages)[0]["content"]


@pytest.mark.parametrize("raw", ["", "   ", None, {}])
def test_empty_arguments_still_run_as_empty_dict(raw):
    c, messages = _Conv(), []
    c._execute_tool_calls([_call("weather_report", raw)], messages, allowed_tools={"weather_report"})
    assert c.executed == [("weather_report", {})]


def test_valid_json_string_arguments_are_parsed():
    c, messages = _Conv(), []
    c._execute_tool_calls([_call("web_search", '{"query": "q"}')], messages,
                          allowed_tools={"web_search"})
    assert c.executed == [("web_search", {"query": "q"})]


# ── 3. cancellation inside a batch ────────────────────────────────────────

def test_stop_during_first_call_prevents_the_rest_of_the_batch():
    c, messages = _Conv(), []

    def first_call_then_stop(name, args):
        c.executed.append(name)
        c._cancel_current_turn.set()            # user presses Stop while call 1 runs
        return "ok"

    c._execute_tool = first_call_then_stop
    calls = [_call("a", {}, "1"), _call("b", {}, "2"), _call("c", {}, "3")]
    c._execute_tool_calls(calls, messages, allowed_tools={"a", "b", "c"})

    assert c.executed == ["a"]
    tm = _tool_msgs(messages)
    assert [m["tool_call_id"] for m in tm] == ["1", "2", "3"]   # every call still answered
    assert all("cancel" in m["content"].lower() for m in tm[1:])


# ── 4 + 5. executor ───────────────────────────────────────────────────────

def test_unknown_tool_does_not_fall_back_to_generated_code(monkeypatch):
    from agent import executor
    ran = []
    monkeypatch.setattr(executor, "_run_generated_code", lambda *a, **k: ran.append(a) or "x")
    with pytest.raises(ValueError, match="Unknown tool"):
        executor._call_tool("definitely_not_a_tool", {"x": 1}, speak=None)
    assert ran == []


def _fake_run(returncode, stdout="", stderr=""):
    def run(*_a, **_k):
        return subprocess.CompletedProcess([], returncode, stdout, stderr)
    return run


@pytest.fixture
def exec_env(monkeypatch):
    from agent import executor
    import core.llm_client as llm
    import core.safety as safety
    monkeypatch.setattr(llm, "call_llm_text", lambda *a, **k: "print('hi')", raising=False)
    monkeypatch.setattr(executor, "call_llm_text", lambda *a, **k: "print('hi')", raising=False)
    monkeypatch.setattr(safety, "confirm_action", lambda *a, **k: True)
    return executor


def test_nonzero_exit_without_stderr_is_a_failure(exec_env, monkeypatch):
    monkeypatch.setattr(exec_env.subprocess, "run", _fake_run(3))
    with pytest.raises(RuntimeError, match="exit"):
        exec_env._run_generated_code("do a thing", speak=None)


def test_silent_success_is_not_reported_as_verified(exec_env, monkeypatch):
    monkeypatch.setattr(exec_env.subprocess, "run", _fake_run(0))
    out = exec_env._run_generated_code("do a thing", speak=None)
    assert "unverified" in out.lower() or "not verified" in out.lower()
    assert out != "Task completed successfully."


def test_output_is_still_returned(exec_env, monkeypatch):
    monkeypatch.setattr(exec_env.subprocess, "run", _fake_run(0, stdout="42\n"))
    assert exec_env._run_generated_code("do a thing", speak=None) == "42"


# ── 6. shutdown gate + import_memory signature ───────────────────────────

def test_shutdown_is_ask_tier():
    from core import risk
    assert risk.tier_for("shutdown_aether", "") == risk.ASK


def test_shutdown_denied_does_not_exit(monkeypatch):
    from core import risk
    from core.tool_dispatch import ToolDispatchMixin
    import os
    exited = []
    monkeypatch.setattr(os, "_exit", lambda code: exited.append(code))
    monkeypatch.setattr(risk, "check", lambda *a, **k: "Denied: not approved.")

    class D(ToolDispatchMixin):
        ui = _UI()
        def speak(self, *_a, **_k):
            pass
        def _tool_save_memory(self, a):
            return ""

    out = D()._execute_tool_impl("shutdown_aether", {})
    assert "Denied" in out
    assert exited == []


def test_import_memory_accepts_input_path_kwarg():
    from core.memory_tools import import_memory
    inspect.signature(import_memory).bind(input_path="x.json")     # no TypeError
    inspect.signature(import_memory).bind(source_path="x.json")    # old name still works
    assert "not found" in import_memory(input_path="/nonexistent/aether.json").lower()


# ── memory tool call sites match their signatures (catches the import_memory class of bug) ──

def _kw_calls(path, names, func=None):
    """(callee, [keyword names]) for every call to one of `names` in `path` (optionally inside `func`)."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / path).read_text(encoding="utf-8"))
    scopes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and (func is None or n.name == func)]
    found = []
    for scope in scopes:
        for n in ast.walk(scope):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in names:
                found.append((n.func.id, [k.arg for k in n.keywords if k.arg]))
    return found


@pytest.mark.parametrize("path,func", [
    ("core/tool_dispatch.py", "_tool_memory_tool"),
    ("core/chat_commands.py", None),
])
def test_memory_call_sites_bind_to_real_signatures(path, func):
    from core import memory_tools
    names = {"backup_memory", "export_memory", "import_memory", "prune_memory", "memory_stats"}
    calls = _kw_calls(path, names, func)
    assert calls, f"no memory calls found in {path}"
    for callee, kwargs in calls:
        sig = inspect.signature(getattr(memory_tools, callee))
        sig.bind(**{k: None for k in kwargs})        # TypeError = call site and signature disagree


# ── bounded command queues ───────────────────────────────────────────────

def _read(path):
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")


def test_main_text_queue_is_bounded_and_ui_puts_do_not_block():
    src = _read("main.py")
    assert "self._text_queue:     queue.Queue = queue.Queue(maxsize=" in src
    assert "self._text_queue.put(" not in src            # only put_nowait via _enqueue_command
    assert "def _enqueue_command" in src


def test_dashboard_command_queue_is_bounded_and_rejects_when_full():
    src = _read("dashboard/server.py")
    assert "self._command_queue               = _queue.Queue(maxsize=" in src
    assert "self._command_queue.put(" not in src
    assert "status_code=429" in src


# ── trace privacy level ──────────────────────────────────────────────────

def test_trace_content_defaults_to_full_and_metadata_is_opt_in():
    from core import trace_log
    assert trace_log._Settings({}).content == "full"
    assert trace_log._Settings({"logging.trace_content": "metadata"}).content == "metadata"
    assert trace_log._Settings({"logging": {"trace_content": "off"}}).content == "metadata"


def test_metadata_mode_drops_long_strings_but_keeps_structure():
    from core.trace_log import _strip_content
    secret = "private document text " * 20
    out = _strip_content({"tool": "file_controller", "ok": True, "result": secret,
                          "args": {"path": "a.txt", "content": secret}, "msgs": [secret, "hi"]})
    assert out["tool"] == "file_controller" and out["ok"] is True
    assert out["args"]["path"] == "a.txt"
    assert secret not in str(out)
    assert out["msgs"][1] == "hi"


# ── 7. a failed call stops the rest of its batch ─────────────────────────

@pytest.mark.parametrize("bad", [
    "Error: something broke",
    "Denied: not approved.",
    "Tool 'a' failed: boom",
    "[LOOP GUARD] Repeated call to 'a' blocked.",
])
def test_failed_call_skips_remaining_calls_in_batch(bad):
    c, messages = _Conv(), []

    def run(name, args):
        c.executed.append(name)
        return bad if name == "a" else "ok"

    c._execute_tool = run
    calls = [_call("a", {}, "1"), _call("b", {}, "2"), _call("c", {}, "3")]
    c._execute_tool_calls(calls, messages, allowed_tools={"a", "b", "c"})

    assert c.executed == ["a"]
    tm = _tool_msgs(messages)
    assert [m["tool_call_id"] for m in tm] == ["1", "2", "3"]      # pairing intact
    assert all("skipped" in m["content"].lower() for m in tm[1:])


def test_successful_batch_still_runs_every_call():
    c, messages = _Conv(), []
    calls = [_call("a", {}, "1"), _call("b", {}, "2")]
    c._execute_tool_calls(calls, messages, allowed_tools={"a", "b"})
    assert [n for n, _ in c.executed] == ["a", "b"]


def test_rejected_call_also_stops_the_batch():
    c, messages = _Conv(), []
    calls = [_call("nope", {}, "1"), _call("a", {}, "2")]
    c._execute_tool_calls(calls, messages, allowed_tools={"a"})
    assert c.executed == []
