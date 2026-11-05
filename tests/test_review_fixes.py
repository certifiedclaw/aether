"""Regression tests for the Nov-2026 external review (see HANDOFF.md).

Real execution where possible: a throwaway stdio MCP server, a real sqlite DB,
the real DashboardServer under TestClient.
"""
import sqlite3
import sys
import textwrap
import threading
import time

import pytest

# ── 1. dashboard token lifecycle ─────────────────────────────────────────

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from dashboard import server as srv  # noqa: E402


def _server():
    return srv.DashboardServer()


def test_revoke_devices_kills_tokens_minted_from_devices():
    d = _server()
    d._add_token("dev-tok", "k1", device="dev-1")
    d._add_token("pair-tok", "k2")                    # pairing-code session, no device
    d._device_sessions["dev-1"] = {"session_key": "k1"}
    count, _ = d._revoke_device_sessions()
    assert count == 1
    assert not d._token_valid("dev-tok")
    assert "dev-tok" not in d._tokens and "dev-tok" not in d._token_keys
    assert d._token_valid("pair-tok")                  # untouched
    assert d._decrypt("dev-tok", "x") is None


def test_revoked_token_gets_401_over_http_and_no_ws_ticket():
    from fastapi.testclient import TestClient
    d = _server()
    d._add_token("dev-tok", "k1", device="dev-1")
    d._add_token("admin", "k2")
    d._device_sessions["dev-1"] = {"session_key": "k1"}
    with TestClient(d.app) as c:
        ok = c.post("/api/revoke-devices", headers={"Authorization": "Bearer admin"})
        assert ok.status_code == 200 and ok.json()["revoked"] == 1
        again = c.post("/api/revoke-devices", headers={"Authorization": "Bearer dev-tok"})
        assert again.status_code == 401
    assert d._issue_ws_ticket("dev-tok", "main") is None


def test_tokens_expire(monkeypatch):
    d = _server()
    d._add_token("t", "k")
    assert d._token_valid("t")
    real = time.monotonic
    monkeypatch.setattr(srv.time, "monotonic", lambda: real() + srv.TOKEN_TTL + 1)
    assert not d._token_valid("t")
    assert "t" not in d._tokens and "t" not in d._token_keys


def test_ticket_issued_before_expiry_is_rejected_after(monkeypatch):
    d = _server()
    d._add_token("t", "k")
    ticket = d._issue_ws_ticket("t", "main")
    assert ticket
    d._drop_token("t")
    assert d._consume_ws_ticket(ticket, "main") is None


# ── 2. TaskQueue must not call a failed run COMPLETED ────────────────────

from agent import executor as ex  # noqa: E402
from agent import task_queue as tq  # noqa: E402


class _FakeExec:
    def __init__(self, result):
        self.result = result

    def execute(self, goal, speak=None, cancel_flag=None):
        return self.result


def _run(result):
    q = tq.TaskQueue(max_concurrent=1, persist_path=None)
    q._executor = _FakeExec(result)
    done = threading.Event()
    tid = q.submit("g", on_complete=lambda *_: done.set()) if "on_complete" in q.submit.__code__.co_varnames else q.submit("g")
    q.start() if hasattr(q, "start") else None
    deadline = time.time() + 5
    while time.time() < deadline:
        t = q.get(tid) if hasattr(q, "get") else q._tasks[tid]
        if t.status in (tq.TaskStatus.COMPLETED, tq.TaskStatus.FAILED, tq.TaskStatus.CANCELLED):
            break
        time.sleep(0.05)
    q.stop()
    return t


def test_failed_outcome_is_marked_failed():
    t = _run(ex.ExecutionOutcome("Task failed after 2 replan attempts, sir.", "failed"))
    assert t.status == tq.TaskStatus.FAILED


def test_cancelled_outcome_is_marked_cancelled():
    t = _run(ex.ExecutionOutcome("Task cancelled.", "cancelled"))
    assert t.status == tq.TaskStatus.CANCELLED


def test_plain_string_still_completes():
    t = _run("All done.")
    assert t.status == tq.TaskStatus.COMPLETED


def test_executor_reports_failed_when_no_plan(monkeypatch):
    monkeypatch.setattr(ex, "create_plan", lambda goal: {"steps": []})
    out = ex.AgentExecutor().execute("anything")
    assert ex.outcome_status(out) == "failed"


# ── 3. prune_memory actually deletes (VACUUM outside the transaction) ────

def test_prune_memory_deletes_and_vacuums(tmp_path, monkeypatch):
    from core import memory_tools as mt
    db = tmp_path / "memory.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL)")
    now = time.time()
    con.executemany("INSERT INTO memories(role,content,timestamp) VALUES (?,?,?)", [
        ("user", "x", now),                                  # too short
        ("user", "a perfectly reasonable memory line", now),
        ("user", "a perfectly reasonable memory line", now),  # duplicate
    ])
    con.commit(); con.close()
    monkeypatch.setattr(mt, "_MEMORY_DB", db)
    out = mt.prune_memory()
    assert "Pruning complete" in out, out
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1


# ── 4. router: short hard prompts are not QUICK ──────────────────────────

def test_short_complex_prompts_route_hard():
    from core.tier_router import classify, Tier
    for msg in ("debug this", "analyze my logs", "refactor it"):
        assert classify(msg).tier == Tier.HARD, msg
    assert classify("hey").tier == Tier.QUICK


# ── 5. MCP bridge against a real stdio server ────────────────────────────

SERVER = textwrap.dedent('''
    import json, sys
    def send(o): sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        m = json.loads(line)
        if "method" not in m or "id" not in m:
            continue
        i, meth = m["id"], m["method"]
        if meth == "initialize":
            send({"jsonrpc": "2.0", "id": i, "result": {}})
        elif meth == "tools/list":
            send({"jsonrpc": "2.0", "id": i, "result": {"tools": [{"name": n} for n in
                  ("echo", "chatty", "boom", "ping", "die")]}})
        elif meth == "tools/call":
            name = m["params"]["name"]
            if name == "ping":
                send({"jsonrpc": "2.0", "id": i, "method": "ping"})
            if name == "die":
                sys.exit(0)
            send({"jsonrpc": "2.0", "id": i, "result": {
                "content": [{"type": "text", "text": name}], "isError": name == "boom"}})
            if name == "chatty":      # idle chatter AFTER the reply, nobody is waiting on it
                for k in range(60):
                    send({"jsonrpc": "2.0", "method": "notifications/message", "params": {"n": k}})
''')


@pytest.fixture
def mcp(tmp_path):
    from core.mcp_bridge import MCPServerProcess
    script = tmp_path / "srv.py"
    script.write_text(SERVER)
    s = MCPServerProcess("t", sys.executable, [str(script)])
    s.start()
    yield s
    s.stop()


def test_idle_chatter_does_not_kill_the_server(mcp):
    assert mcp.call_tool("chatty", {}) == "chatty"
    time.sleep(1.5)                                     # 60 idle notifications > 16-slot queue, > 1s put timeout
    assert mcp.is_alive()
    assert mcp.call_tool("echo", {}) == "echo"


def test_idle_lines_between_calls_are_discarded(mcp):
    mcp._stdout_queue.put_nowait(b'{"jsonrpc":"2.0","id":2,"result":{"stale":true}}\n')
    assert mcp.call_tool("echo", {}) == "echo"


def test_server_initiated_request_is_not_mistaken_for_the_response(mcp):
    assert mcp.call_tool("ping", {}) == "ping"


def test_is_error_becomes_a_failure(mcp):
    from core.mcp_bridge import MCPBridge
    with pytest.raises(RuntimeError):
        mcp.call_tool("boom", {})
    b = MCPBridge.__new__(MCPBridge)
    b._servers = {"t": mcp}
    from core.conversation_core import _is_failed_result
    assert _is_failed_result(b.call("t", "boom", {}))


def test_dead_server_restarts_on_demand(mcp):
    from core.mcp_bridge import MCPBridge
    b = MCPBridge.__new__(MCPBridge)
    b._servers = {"t": mcp}
    first = b.call("t", "die", {})                       # server exits mid-call
    assert first.startswith("[MCP]")
    deadline = time.time() + 3
    while mcp.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    mcp._last_restart = 0.0
    assert b.call("t", "echo", {}) == "echo"


# ── 6. plugin tools are gated; file mutations ask; copytree keeps links as links ──

def test_plugin_tool_asks_before_running_and_not_when_denied(monkeypatch):
    from core import risk, safety
    from core.plugins import PluginSystem
    asked = []
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    monkeypatch.setattr(safety, "confirm_action", lambda op, detail="": asked.append(op) or False)
    pm = PluginSystem.__new__(PluginSystem)
    ran = []
    pm._tools = {"evil": lambda **kw: ran.append(kw) or "pwned"}
    out = pm.execute_tool("evil", target="x", player=object(), session_memory=None)
    assert asked and not ran and out != "pwned"
    monkeypatch.setattr(safety, "confirm_action", lambda op, detail="": True)
    assert pm.execute_tool("evil", target="x") == "pwned"


@pytest.mark.parametrize("action", ["create_file", "create_folder", "write", "copy", "move", "rename", "delete"])
def test_file_mutations_require_approval(action):
    from core import risk
    assert risk.tier_for("file_controller", action) == risk.ASK


def test_copy_directory_does_not_follow_symlinks_out_of_allowed_root(tmp_path, monkeypatch):
    import os
    import actions.file_controller as fc
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text("TOP SECRET")
    src = tmp_path / "home" / "proj"
    src.mkdir(parents=True)
    (src / "ok.txt").write_text("fine")
    try:
        os.symlink(secret, src / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    monkeypatch.setattr(fc, "_is_safe_path", lambda p: str(p).startswith(str(tmp_path / "home")))
    monkeypatch.setattr(fc, "_resolve_path", lambda p: __import__("pathlib").Path(p))
    dst = tmp_path / "home" / "copy"
    out = fc.copy_file(str(src), destination=str(dst))
    assert "Copied" in out, out
    assert (dst / "link.txt").is_symlink()
    assert (dst / "ok.txt").read_text() == "fine"
