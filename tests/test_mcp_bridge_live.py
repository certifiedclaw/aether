"""MCP bridge against a real throwaway stdio server."""
import sys
import textwrap
import time

import pytest

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
