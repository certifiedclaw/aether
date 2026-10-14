"""
mcp_bridge.py — Aether v4.1 MCP (Model Context Protocol) Bridge

Connects Aether to external MCP servers defined in config.yaml under mcp.servers.
Each server is a subprocess that speaks the MCP stdio protocol. The bridge
manages process lifetimes, routes tool calls, and returns results as strings
that fit naturally into Aether's existing tool dispatch system.

Usage in config.yaml:
    mcp:
      enabled: true
      servers:
        - name: obsidian
          command: npx
          args: ["-y", "@modelcontextprotocol/server-obsidian", "/path/to/vault"]
        - name: github
          command: npx
          args: ["-y", "@modelcontextprotocol/server-github"]
          env:
            GITHUB_TOKEN: "ghp_..."

Aether can then plan steps like:
    {"tool": "mcp.call", "args": {"server": "obsidian", "tool": "search_notes", "args": {"query": "meeting"}}}

The planner also receives a live list of available MCP tools via _plugin_tools()
so the LLM knows what's connected before planning.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_NEWLINE = b"\n"
MCP_RPC_TIMEOUT_SECONDS = 30
_MCP_STDOUT_QUEUE_SIZE = 16
_MCP_MAX_LINE_BYTES = 8 * 1024 * 1024
_MCP_ENV_ALLOWLIST = frozenset({
    "PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
    "WINDIR", "TEMP", "TMP", "TMPDIR", "HOMEDRIVE", "HOMEPATH", "PATHEXT",
    "COMSPEC", "PROGRAMFILES", "PROGRAMFILES(X86)", "LANG", "LC_ALL", "LC_CTYPE",
    "TZ", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR",
})


def _server_environment(overrides: dict[str, str] | None) -> dict[str, str]:
    inherited = {
        key: value for key, value in os.environ.items()
        if key.upper() in _MCP_ENV_ALLOWLIST
    }
    if overrides:
        inherited.update({str(key): str(value) for key, value in overrides.items()})
    return inherited


class MCPServerProcess:
    """Manages a single MCP server subprocess over stdio JSON-RPC."""

    def __init__(self, name: str, command: str, args: list[str],
                 env: dict[str, str] | None = None) -> None:
        self.name = name
        self._command = command
        self._args = args
        self._env = _server_environment(env)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._msg_id = 0
        self._tools: list[dict] = []
        self._stdout_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_MCP_STDOUT_QUEUE_SIZE)

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        logger.info("MCP: starting server '%s' (%s %s)", self.name, self._command, self._args)
        self._stdout_queue = queue.Queue(maxsize=_MCP_STDOUT_QUEUE_SIZE)
        self._proc = subprocess.Popen(
            [self._command, *self._args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._env,
        )
        threading.Thread(
            target=self._read_stdout,
            args=(self._proc, self._stdout_queue),
            daemon=True,
        ).start()
        try:
            self._initialize()
            self._tools = self._fetch_tools()
        except Exception:
            self.stop()
            raise

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
            except Exception:
                pass

    @staticmethod
    def _read_stdout(proc, response_queue: queue.Queue) -> None:
        try:
            while True:
                raw = proc.stdout.readline(_MCP_MAX_LINE_BYTES + 1)
                if not raw:
                    response_queue.put(None, timeout=1)
                    return
                if len(raw) > _MCP_MAX_LINE_BYTES:
                    raise ValueError("MCP response line exceeds the size limit")
                response_queue.put(raw, timeout=1)
        except (queue.Full, OSError, ValueError) as exc:
            logger.error("MCP stdout reader stopped: %s", exc)
            try:
                proc.terminate()
            except Exception:
                pass
            try:
                response_queue.put_nowait(None)
            except queue.Full:
                pass

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── RPC ──────────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _send(self, method: str, params: dict | None = None) -> Any:
        if not self.is_alive():
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        msg_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": msg_id, "method": method}
        if params:
            payload["params"] = params
        line = (json.dumps(payload) + "\n").encode()
        with self._lock:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
            deadline = time.monotonic() + MCP_RPC_TIMEOUT_SECONDS
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.stop()
                    raise TimeoutError(
                        f"MCP server '{self.name}' timed out waiting for '{method}'"
                    )
                try:
                    raw = self._stdout_queue.get(timeout=remaining)
                except queue.Empty as exc:
                    self.stop()
                    raise TimeoutError(
                        f"MCP server '{self.name}' timed out waiting for '{method}'"
                    ) from exc
                if not raw:
                    self.stop()
                    raise RuntimeError(f"MCP server '{self.name}' closed stdout unexpectedly")
                try:
                    resp = json.loads(raw.decode())
                except json.JSONDecodeError:
                    continue
                if not isinstance(resp, dict):
                    continue
                if resp.get("id") == msg_id:
                    if "error" in resp:
                        raise RuntimeError(f"MCP error from '{self.name}': {resp['error']}")
                    return resp.get("result")

    def _initialize(self) -> None:
        self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aether", "version": "4.1"},
        })
        self._notify("notifications/initialized")

    def _notify(self, method: str, params: dict | None = None) -> None:
        if not self.is_alive():
            raise RuntimeError(f"MCP server '{self.name}' is not running")
        payload = {"jsonrpc": "2.0", "method": method}
        if params:
            payload["params"] = params
        with self._lock:
            self._proc.stdin.write((json.dumps(payload) + "\n").encode())
            self._proc.stdin.flush()

    def _fetch_tools(self) -> list[dict]:
        try:
            result = self._send("tools/list")
            return result.get("tools", []) if result else []
        except Exception as e:
            logger.warning("MCP: could not fetch tools from '%s': %s", self.name, e)
            return []

    # ── Public ────────────────────────────────────────────────────────────

    @property
    def tools(self) -> list[dict]:
        return self._tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        if not isinstance(arguments, dict):
            raise ValueError("MCP tool arguments must be an object")
        if not self.has_tool(tool_name):
            raise ValueError(f"MCP tool '{tool_name}' was not advertised by server '{self.name}'")
        result = self._send("tools/call", {"name": tool_name, "arguments": arguments})
        if not result:
            return "Done."
        # MCP returns a list of content blocks
        content = result.get("content", [])
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "resource":
                parts.append(str(block.get("resource", {}).get("text", "")))
        return "\n".join(parts) or "Done."

    def has_tool(self, tool_name: str) -> bool:
        return any(isinstance(tool, dict) and tool.get("name") == tool_name
                   for tool in self._tools)


class MCPBridge:
    """Singleton bridge that manages all configured MCP servers."""

    _instance: "MCPBridge | None" = None

    def __init__(self, config=None) -> None:
        self._servers: dict[str, MCPServerProcess] = {}
        self._config = config
        if config and config.get("mcp.enabled"):
            self._boot_servers(config.get("mcp.servers") or [])

    @classmethod
    def instance(cls, config=None) -> "MCPBridge":
        if cls._instance is None:
            cls._instance = cls(config)
        return cls._instance

    def _boot_servers(self, server_defs: list[dict]) -> None:
        for defn in server_defs:
            name = defn.get("name")
            if not name:
                continue
            srv = MCPServerProcess(
                name=name,
                command=defn.get("command", "npx"),
                args=defn.get("args", []),
                env=defn.get("env"),
            )
            try:
                srv.start()
                self._servers[name] = srv
                logger.info("MCP: server '%s' started with %d tools", name, len(srv.tools))
            except Exception as e:
                logger.error("MCP: failed to start server '%s': %s", name, e)

    def call(self, server: str, tool: str, args: dict) -> str:
        srv = self._servers.get(server)
        if not srv:
            available = list(self._servers.keys())
            return f"[MCP] Unknown server '{server}'. Available: {available}"
        if not srv.is_alive():
            return f"[MCP] Server '{server}' is not running."
        if not isinstance(args, dict):
            return "[MCP] Tool arguments must be an object."
        if not srv.has_tool(tool):
            return f"[MCP] Tool '{tool}' is not advertised by server '{server}'."
        try:
            return srv.call_tool(tool, args)
        except Exception as e:
            return f"[MCP] Error calling {server}.{tool}: {e}"

    def list_all_tools(self) -> list[str]:
        """Return a flat list of 'server.tool_name' strings for use in the planner prompt."""
        out = []
        for srv_name, srv in self._servers.items():
            for t in srv.tools:
                out.append(f"mcp.call(server='{srv_name}', tool='{t['name']}', args={{...}})")
        return out

    def has_tool(self, server: str, tool: str) -> bool:
        srv = self._servers.get(server)
        return bool(srv and srv.has_tool(tool))

    def shutdown(self) -> None:
        for srv in self._servers.values():
            srv.stop()
        self._servers.clear()
