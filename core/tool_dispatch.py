"""core/tool_dispatch.py — Aether Tool Dispatch

Extracted from main.py's AetherLocal god-object (continuation of the
Conversation Core split — see core/conversation_core.py's module
docstring for background). Mechanical extraction: every method below is
the same logic that used to live inline in AetherLocal, moved here so
the "one tool name -> one handler" dispatch table and its ~30
`_tool_*` methods aren't interleaved with UI wiring, STT/TTS, dashboard,
and bootstrap code in a 2,700-line file.

`ToolDispatchMixin` is meant to be mixed into `AetherLocal`
(`class AetherLocal(ConversationCoreMixin, ToolDispatchMixin, ...)` in
main.py) — it isn't usable standalone. It still reaches into
`self.ui`, `self.speak`, `self.speak_error`, `self._rag`, `self._obs`,
`self._goals`, `self._plugins`, `self._mcp`, `self._semantic_mem`,
`self._hybrid_mem`, and `self._tool_fallback` exactly as it did before
the move (`_tool_fallback` stays on AetherLocal itself for now — it
handles the MCP/plugin catch-all path and wasn't included in this
extraction).

A few names here (`_LANGCHAIN_OK`, `_LLAMA_OK`, `_OI_OK`, `log_crash`,
`get_recent_crashes`, `backup_memory`, `export_memory`, `import_memory`,
`prune_memory`, `memory_stats`, `langchain_tool`, `oi_tool`,
`llama_index_tool`, `update_memory`) are set up in main.py at import
time — several behind try/except with fallback stubs if the optional
dependency isn't installed. Importing them at module load time here
would both risk a circular import (main.py -> core.tool_dispatch ->
main.py while main.py is still mid-import) and could grab the wrong
(pre-fallback) value. They're imported lazily inside the specific
method that uses each one instead, same pattern as conversation_core.py.
"""

import os
from core.risk import gated  # noqa: E402
import threading
import time
import traceback

from actions.file_processor    import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater

from core import undo as undo_stack


class ToolDispatchMixin:
    """Owns tool execution: the dispatch table and every `_tool_*` handler.

    Mixed into AetherLocal — see module docstring above.
    """

    def _execute_tool(self, name: str, args: dict) -> str:
        print(f"[Aether] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            return self._tool_save_memory(args)

        result = "Done."
        try:
            if name == "shutdown_aether":
                # Unique control flow: returns immediately, bypassing the
                # trailing state-reset/log lines below (there's nothing left
                # to reset once the shutdown thread has been started), so
                # this stays inline rather than going through the dispatch
                # table like every other tool.
                self.ui.write_log("SYS: Shutdown requested.")
                def _shutdown():
                    import time, os
                    self.speak("Goodbye.")
                    time.sleep(2.5)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()
                return "Shutting down."

            handler = self._TOOL_DISPATCH.get(name)
            if handler is not None:
                result = handler(self, args)
            elif name in ("goal_create", "goal_update", "goal_complete",
                          "goal_cancel", "goal_list", "goal_add_subtask"):
                # Convenience aliases — LLM may call these directly per
                # goal_state docstring.
                result = self._tool_goal_alias(name, args)
            else:
                # ── Try plugin tools ───────────────────────────────────
                if self._plugins:
                    # Support both "plugin.tool_name" and bare "tool_name"
                    tool_key = name.replace("plugin.", "")
                    if tool_key in (self._plugins.list_tools() or []):
                        result = self._plugins.execute_tool(
                            tool_key,
                            **args,
                            player=self.ui,
                            session_memory=self._semantic_mem or self._hybrid_mem,
                        )
                        if not self.ui.muted:
                            self.ui.set_state("LISTENING")
                        return result
                # ── Try MCP tools, else unknown ────────────────────────
                result = self._tool_fallback(name, args)

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            from main import log_crash  # lazy: avoids circular import at module load time
            log_crash(e, tool=name, extra={"args": args})
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[Aether] 📤 {name} → {str(result)[:80]}")
        return result

    def _tool_save_memory(self, args: dict) -> str:
        category = args.get("category", "notes")
        key      = args.get("key", "")
        value    = args.get("value", "")
        if key and value:
            from main import update_memory  # lazy: avoids circular import at module load time
            update_memory({category: {key: {"value": value}}})
            print(f"[Memory] 💾 {category}/{key} = {value}")
        if not self.ui.muted:
            self.ui.set_state("LISTENING")
        return "__SILENT__"

    def _tool_open_app(self, args: dict) -> str:
        r = open_app(parameters=args, response=None, player=self.ui)
        return r or f"Opened {args.get('app_name')}."

    def _tool_weather_report(self, args: dict) -> str:
        r = weather_action(parameters=args, player=self.ui)
        return r or "Weather delivered."

    def _tool_browser_control(self, args: dict) -> str:
        r = browser_control(parameters=args, player=self.ui)
        return r or "Done."

    def _tool_file_controller(self, args: dict) -> str:
        r = file_controller(parameters=args, player=self.ui)
        return r or "Done."

    def _tool_undo(self, args: dict) -> str:
        action = (args.get("action") or "undo").strip().lower()
        if action == "list":
            items = undo_stack.history()
            if not items:
                return "There's nothing to undo right now."
            return "Most recent first:\n" + "\n".join(f"  {i+1}. {d}" for i, d in enumerate(items))
        return undo_stack.undo_last()

    def _tool_send_message(self, args: dict) -> str:
        r = send_message(parameters=args, response=None, player=self.ui, session_memory=None)
        return r or f"Message sent to {args.get('receiver')}."

    def _tool_reminder(self, args: dict) -> str:
        r = reminder(parameters=args, response=None, player=self.ui)
        return r or "Reminder set."

    def _tool_youtube_video(self, args: dict) -> str:
        r = youtube_video(parameters=args, response=None, player=self.ui)
        return r or "Done."

    def _tool_screen_process(self, args: dict) -> str:
        r = screen_process(parameters=args, response=None, player=self.ui, session_memory=None)
        return r if isinstance(r, str) and r else "Screen analyzed."

    def _tool_computer_settings(self, args: dict) -> str:
        r = computer_settings(parameters=args, response=None, player=self.ui)
        return r or "Done."

    def _tool_desktop_control(self, args: dict) -> str:
        r = desktop_control(parameters=args, player=self.ui)
        return r or "Done."

    def _tool_code_helper(self, args: dict) -> str:
        r = code_helper(parameters=args, player=self.ui, speak=self.speak)
        return r or "Done."

    def _tool_dev_agent(self, args: dict) -> str:
        r = dev_agent(parameters=args, player=self.ui, speak=self.speak)
        return r or "Done."

    def _tool_agent_task(self, args: dict) -> str:
        from agent.task_queue import get_queue, TaskPriority
        priority_map = {
            "low": TaskPriority.LOW,
            "normal": TaskPriority.NORMAL,
            "high": TaskPriority.HIGH,
        }
        priority = priority_map.get(
            args.get("priority", "normal").lower(), TaskPriority.NORMAL
        )
        task_id = get_queue().submit(
            goal=args.get("goal", ""), priority=priority, speak=self.speak
        )
        return f"Task started (ID: {task_id})."

    def _tool_web_search(self, args: dict) -> str:
        r = web_search_action(parameters=args, player=self.ui)
        return r or "Done."

    def _tool_file_processor(self, args: dict) -> str:
        if not args.get("file_path") and self.ui.current_file:
            args["file_path"] = self.ui.current_file
        r = file_processor(parameters=args, player=self.ui, speak=self.speak)
        return r or "Done."

    def _tool_computer_control(self, args: dict) -> str:
        r = computer_control(parameters=args, player=self.ui)
        return r or "Done."

    def _tool_game_updater(self, args: dict) -> str:
        r = game_updater(parameters=args, player=self.ui, speak=self.speak)
        return r or "Done."

    def _tool_flight_finder(self, args: dict) -> str:
        r = flight_finder(parameters=args, player=self.ui)
        return r or "Done."

    # ── NEW tools ─────────────────────────────────────────────────

    def _tool_osint_lookup(self, args: dict) -> str:
        try:
            action = args.get("action", "")
            target = args.get("target", "")

            # port_scan/breach_check get a blocking confirmation dialog before
            # running -- see core/safety.py's _DEFAULT_HIGH_RISK and the
            # authorization warnings on this tool's schema in TOOL_DECLARATIONS.
            gate_op = {"port_scan": "osint_port_scan", "breach_check": "osint_breach_check"}.get(action)
            if gate_op and self._safety.requires_confirmation(gate_op):
                detail = (f"Action: {action}\nTarget: {target}\n"
                          + (f"Ports: {args.get('ports') or '80,443,22,21,8080'}\n"
                             if action == "port_scan" else "") + "\n"
                          + ("Only scan systems you own or are explicitly authorized to test."
                             if action == "port_scan" else
                             "Returns whether this email appears in known breach datasets — "
                             "handle the result as you would any other person's private information."))
                if not self._safety.request_confirmation(f"OSINT {action}", detail):
                    return f"OSINT {action} cancelled — not confirmed."

            from actions.osint_tools import run_osint
            r = run_osint(action, target, args)
            return r or "OSINT complete."
        except ImportError:
            return "OSINT tools not available. Install: pip install python-whois dnspython"
        except Exception as e:
            return f"OSINT error: {e}"

    def _tool_pdf_tool(self, args: dict) -> str:
        try:
            from actions.pdf_tools import run_pdf_tool
            r = run_pdf_tool(
                action=args.get("action", "summarize"),
                file_path=args.get("file_path", ""),
                query=args.get("query", ""),
                pages=args.get("pages"),
            )
            return r or "PDF processed."
        except ImportError:
            return "PDF tools not available. Install: pip install pymupdf"
        except Exception as e:
            return f"PDF tool error: {e}"

    def _tool_rag_query(self, args: dict) -> str:
        from main import _LLAMA_OK, llama_index_tool  # lazy: avoids circular import at module load time
        action = args.get("action", "query")
        # Prefer LlamaIndex for query/index when available
        if _LLAMA_OK and action in ("query", "index_file", "index_directory"):
            if action == "query":
                return llama_index_tool("query", {
                    "query": args.get("query", ""),
                    "collection": args.get("collection", "aether"),
                    "mode": args.get("mode", "auto"),
                })
            elif action in ("index_file", "index_directory"):
                path = args.get("path", args.get("file_path", ""))
                return llama_index_tool("index", {
                    "path": path,
                    "collection": args.get("collection", "aether"),
                })
        elif self._rag is None:
            return "RAG engine not initialized."
        else:
            if action == "query":
                return self._rag.query(args.get("query", ""))
            elif action == "index_file":
                return self._rag.index_file(args.get("path", ""))
            elif action == "index_directory":
                return self._rag.index_directory(args.get("path", ""))
            elif action == "stats":
                return str(self._rag.stats())
            else:
                return f"Unknown RAG action: {action}"

    def _tool_diagnostics(self, args: dict) -> str:
        try:
            from core.diagnostics import run_all, format_report
            report = format_report(run_all())
            self.ui.write_log(report)
            return report
        except Exception as e:
            return f"Diagnostics error: {e}"

    # ── NEW v2: goal manager ───────────────────────────────────

    def _tool_goal_manager(self, args: dict) -> str:
        if self._goals:
            action = args.get("action", "goal_list")
            return self._goals.handle_tool(action, args)
        return "Goal system not initialised."

    def _tool_goal_alias(self, name: str, args: dict) -> str:
        if self._goals:
            return self._goals.handle_tool(name, args)
        return "Goal system not initialised."

    # ── NEW v2: observability report ───────────────────────────

    def _tool_obs_report(self, args: dict) -> str:
        return self._obs.report()

    # ── NEW v4: memory management tools ───────────────────────

    @gated("memory_tool")
    def _tool_memory_tool(self, args: dict) -> str:
        from main import backup_memory, export_memory, import_memory, prune_memory, memory_stats  # lazy: avoids circular import at module load time
        action = args.get("action", "stats")
        if action == "backup":
            return backup_memory(tag=args.get("tag", ""))
        elif action == "export":
            return export_memory(output_path=args.get("path", ""))
        elif action == "import":
            return import_memory(input_path=args.get("path", ""))
        elif action == "prune":
            return prune_memory(dry_run=args.get("dry_run", False))
        elif action == "stats":
            return memory_stats()
        else:
            return f"Unknown memory_tool action '{action}'. Use: backup | export | import | prune | stats"

    # ── NEW v4: crash report ───────────────────────────────────

    def _tool_crash_report(self, args: dict) -> str:
        from main import get_recent_crashes  # lazy: avoids circular import at module load time
        return get_recent_crashes(n=args.get("n", 5))

    # ── extra utility toolkit ──────────────────────────────────

    def _tool_extra_tool(self, args: dict) -> str:
        from actions.extra_tools import run_extra_tool
        return run_extra_tool(args.get("action", ""), args)

    # ── NEW v3: LangChain integration ──────────────────────────

    def _tool_langchain(self, args: dict) -> str:
        from main import _LANGCHAIN_OK, langchain_tool  # lazy: avoids circular import at module load time
        if _LANGCHAIN_OK:
            return langchain_tool(
                action=args.get("action", ""),
                args=args,
            )
        return (
            "UNAVAILABLE: the langchain tool is not usable on this machine "
            "(LangChain or one of its dependencies is missing). Nothing was "
            "searched. Do NOT say you researched anything; answer from your "
            "own knowledge and say it is unverified, or use web_search.\n"
            "Owner fix: pip install langchain langchain-community "
            "langchain-ollama ddgs wikipedia"
        )

    # ── NEW v3: Open Interpreter ───────────────────────────────

    def _tool_open_interpreter(self, args: dict) -> str:
        from main import _OI_OK, oi_tool  # lazy: avoids circular import at module load time
        if _OI_OK:
            return oi_tool(
                action=args.get("action", "run"),
                args=args,
            )
        return (
            "Open Interpreter not installed.\n"
            "Fix: pip install open-interpreter"
        )

    # ── NEW v3: LlamaIndex RAG ─────────────────────────────────

    def _tool_llama_index(self, args: dict) -> str:
        from main import _LLAMA_OK, llama_index_tool  # lazy: avoids circular import at module load time
        if _LLAMA_OK:
            return llama_index_tool(
                action=args.get("action", "query"),
                args=args,
            )
        return (
            "LlamaIndex not installed.\n"
            "Fix: pip install llama-index llama-index-llms-ollama "
            "llama-index-embeddings-ollama"
        )

    def _tool_fallback(self, name: str, args: dict) -> str:
        if self._mcp and name == "mcp.call":
            server = args.get("server", "")
            tool = args.get("tool", "")
            tool_args = args.get("args", {})
            if not isinstance(server, str) or not isinstance(tool, str) or not isinstance(tool_args, dict):
                return "[MCP] Server and tool must be strings, and args must be an object."
            if not self._mcp.has_tool(server, tool):
                return f"[MCP] Tool '{tool}' is not available on server '{server}'."
            from core.risk import check
            denied = check("mcp.call", "", {
                "server": server,
                "tool": tool,
                "arguments": tool_args,
            })
            if denied:
                return denied
            return self._mcp.call(
                server=server,
                tool=tool,
                args=tool_args,
            )
        return f"Unknown tool: {name}"

    # Dispatch table for the straightforward "one tool name -> one handler"
    # cases. Built as a dict of unbound functions (called as handler(self,
    # args)) rather than a dict of bound methods, since it's a class-level
    # constant assembled before any instance exists. shutdown_aether, the
    # goal_create/... aliases, and the plugin/MCP fallback aren't in here —
    # each needs its own control flow (see _execute_tool above) and is
    # handled explicitly instead.
    _TOOL_DISPATCH = {
        "open_app": _tool_open_app,
        "weather_report": _tool_weather_report,
        "browser_control": _tool_browser_control,
        "file_controller": _tool_file_controller,
        "undo": _tool_undo,
        "send_message": _tool_send_message,
        "reminder": _tool_reminder,
        "youtube_video": _tool_youtube_video,
        "screen_process": _tool_screen_process,
        "computer_settings": _tool_computer_settings,
        "desktop_control": _tool_desktop_control,
        "code_helper": _tool_code_helper,
        "dev_agent": _tool_dev_agent,
        "agent_task": _tool_agent_task,
        "web_search": _tool_web_search,
        "file_processor": _tool_file_processor,
        "computer_control": _tool_computer_control,
        "game_updater": _tool_game_updater,
        "flight_finder": _tool_flight_finder,
        "osint_lookup": _tool_osint_lookup,
        "pdf_tool": _tool_pdf_tool,
        "rag_query": _tool_rag_query,
        "diagnostics": _tool_diagnostics,
        "goal_manager": _tool_goal_manager,
        "obs_report": _tool_obs_report,
        "observability": _tool_obs_report,
        "memory_tool": _tool_memory_tool,
        "crash_report": _tool_crash_report,
        "extra_tool": _tool_extra_tool,
        "langchain": _tool_langchain,
        "open_interpreter": _tool_open_interpreter,
        "llama_index": _tool_llama_index,
    }
