import json
import sys
import types

import pytest

from core.mcp_bridge import MCPBridge, MCPServerProcess
from core.tool_declarations import MCP_TOOL_DECLARATION, _to_ollama_tools
from core.tool_dispatch import ToolDispatchMixin


def test_mcp_server_inherits_only_runtime_environment_and_explicit_values(monkeypatch):
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-secret")
    server = MCPServerProcess(
        "test", "command", [], env={"OPENAI_API_KEY": "explicit-secret", "CUSTOM_SETTING": "value"}
    )

    assert next(value for key, value in server._env.items() if key.upper() == "PATH") == "safe-path"
    assert "inherited-secret" not in server._env.values()
    assert server._env["OPENAI_API_KEY"] == "explicit-secret"
    assert server._env["CUSTOM_SETTING"] == "value"


def test_mcp_initialized_notification_is_sent_without_waiting_for_a_response():
    class Stdin:
        def __init__(self):
            self.lines = []

        def write(self, value):
            self.lines.append(value)

        def flush(self):
            pass

    class Process:
        def __init__(self):
            self.stdin = Stdin()

        def poll(self):
            return None

    server = MCPServerProcess("test", "command", [])
    process = Process()
    server._proc = process
    requests = []
    server._send = lambda method, params=None: requests.append((method, params)) or {}

    server._initialize()

    notification = json.loads(process.stdin.lines[0].decode())
    assert requests[0][0] == "initialize"
    assert notification["method"] == "notifications/initialized"
    assert "id" not in notification


def test_mcp_process_rejects_unadvertised_tool_without_rpc(monkeypatch):
    server = MCPServerProcess("test", "command", [])
    server._tools = [{"name": "visible_tool"}]
    calls = []
    monkeypatch.setattr(server, "_send", lambda *args, **kwargs: calls.append(args))

    with pytest.raises(ValueError, match="not advertised"):
        server.call_tool("hidden_tool", {})

    assert calls == []


def test_mcp_dispatch_requires_approval_before_call(monkeypatch):
    import core.safety as safety

    class Bridge:
        def __init__(self):
            self.calls = []

        def has_tool(self, server, tool):
            return server == "notes" and tool == "search"

        def call(self, **kwargs):
            self.calls.append(kwargs)
            return "called"

    class Dispatch:
        _mcp = Bridge()

    prompts = []
    monkeypatch.setattr(safety, "confirm_action", lambda operation, detail="": prompts.append((operation, detail)) or False)
    args = {"server": "notes", "tool": "search", "args": {"query": "meeting"}}

    result = ToolDispatchMixin._tool_fallback(Dispatch(), "mcp.call", args)

    assert result.startswith("Cancelled")
    assert Dispatch._mcp.calls == []
    assert prompts and "meeting" in prompts[0][1]


def test_mcp_dispatch_skips_approval_for_unadvertised_tools(monkeypatch):
    import core.safety as safety

    class Bridge:
        def has_tool(self, server, tool):
            return False

    class Dispatch:
        _mcp = Bridge()

    monkeypatch.setattr(safety, "confirm_action", lambda *args, **kwargs: pytest.fail("approval should not be requested"))

    result = ToolDispatchMixin._tool_fallback(
        Dispatch(), "mcp.call", {"server": "notes", "tool": "hidden", "args": {}}
    )

    assert "not available" in result


def test_nested_secrets_are_redacted_from_approval_details():
    from core.risk import _detail

    detail = _detail("mcp.call", "", {"arguments": {"options": [{"api_key": "secret-value"}]}})

    assert "secret-value" not in detail
    assert "(hidden)" in detail


def test_mcp_call_declaration_converts_to_a_valid_structured_tool():
    converted = _to_ollama_tools([MCP_TOOL_DECLARATION])[0]["function"]

    assert converted["name"] == "mcp.call"
    assert converted["parameters"]["type"] == "object"
    assert converted["parameters"]["required"] == ["server", "tool", "args"]


def test_mcp_call_is_advertised_only_when_a_server_has_tools(monkeypatch):
    from core.conversation_core import ConversationCoreMixin
    from core.tool_declarations import _to_ollama_tools

    fake_main = types.ModuleType("main")
    fake_main.OLLAMA_TOOLS = []
    fake_main._to_ollama_tools = _to_ollama_tools
    monkeypatch.setitem(sys.modules, "main", fake_main)

    class Bridge:
        def list_all_tools(self):
            return ["mcp.call(server='notes', tool='search', args={...})"]

    conversation = object.__new__(ConversationCoreMixin)
    conversation._config = {}
    conversation._plugins = None
    conversation._mcp = Bridge()

    tools = conversation._tools_for_current_turn()

    assert [item["function"]["name"] for item in tools] == ["mcp.call"]
