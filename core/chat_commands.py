"""core/chat_commands.py — Aether Built-in Chat Commands

Extracted from main.py's AetherLocal god-object (continuation of the
Conversation Core split — see core/conversation_core.py's module
docstring for background). Mechanical extraction: `_handle_chat_command`
is the same logic that used to live inline in AetherLocal, moved here so
the built-in slash-command handling (/status, /clear, /selftest,
/crashes, /backup, /memstats, /prune, /export, /import, /help) is its
own reviewable unit.

`ChatCommandsMixin` is meant to be mixed into `AetherLocal`
(`class AetherLocal(ConversationCoreMixin, ToolDispatchMixin,
PromptBuilderMixin, ChatCommandsMixin): ...` in main.py) — it isn't
usable standalone. It still reaches into `self._execute_tool`,
`self._conversation_lock`, `self._conversation`, `self._semantic_mem`,
`self.ui`, and `self.speak` exactly as it did before the move.

`get_recent_crashes`, `backup_memory`, `memory_stats`, `prune_memory`,
`export_memory`, `import_memory`, and `run_self_test_async` are set up
in main.py at import time behind a try/except with fallback stubs if
the optional `crash_logger` / `self_test` / `memory_tools` modules
aren't available. Importing them at module load time here would both
risk a circular import (main.py -> core.chat_commands -> main.py while
main.py is still mid-import) and could grab the wrong (pre-fallback)
value, so they're imported lazily inside the method instead, same
pattern as the other extracted modules.
"""

from version import version_string


class ChatCommandsMixin:
    """Owns the built-in slash/text command handler.

    Mixed into AetherLocal — see module docstring above.
    """

    def _handle_chat_command(self, user_text: str) -> bool:
        """Built-in slash/text commands (status, /clear, /help, etc.).

        Returns True if `user_text` was a recognized command and has already
        been fully handled (caller should return immediately), False if it
        wasn't a command and normal LLM processing should continue.
        """
        from main import (get_recent_crashes, backup_memory, memory_stats,
                           prune_memory, export_memory, import_memory,
                           run_self_test_async)  # lazy: avoids circular import at module load time
        if user_text.strip().lower() in ("status", "aether status", "/status"):
            self._execute_tool("diagnostics", {})
            return True
        if user_text.strip().lower() == "/clear":
            with self._conversation_lock:
                self._conversation.clear()
            if self._semantic_mem:
                self._semantic_mem.clear_session()
            self.ui.write_log("SYS: Chat history cleared.")
            self.speak("History cleared.")
            return True
        if user_text.strip().lower() in ("/selftest", "run self test", "self test"):
            # Backgrounded: self-test hits Ollama/LLM/audio devices with
            # real timeouts (up to ~30s for the LLM check alone), and this
            # handler runs on the shared command loop — running it inline
            # would stall every other command until it finished.
            self.ui.write_log("SYS: Running self-test in the background…")

            def _on_done(result):
                report = result.format() if result else "Self-test module not available."
                self.ui.write_log(report)
                self.speak("Self-test complete. Check the log for results.")

            run_self_test_async(_on_done, aether=self)
            return True
        if user_text.strip().lower() in ("/crashes", "show crashes", "crash report", "recent crashes"):
            report = get_recent_crashes(5)
            self.ui.write_log(report)
            return True
        if user_text.strip().lower() in ("/backup", "backup memory"):
            result = backup_memory()
            self.ui.write_log(result)
            self.speak("Memory backed up.")
            return True
        if user_text.strip().lower() in ("/memstats", "memory stats"):
            result = memory_stats()
            self.ui.write_log(result)
            return True
        if user_text.strip().lower() in ("/prune", "prune memory"):
            result = prune_memory()
            self.ui.write_log(result)
            return True
        if user_text.strip().lower().startswith("/export"):
            parts = user_text.strip().split(maxsplit=1)
            path  = parts[1].strip() if len(parts) > 1 else ""
            result = export_memory(output_path=path)
            self.ui.write_log(result)
            self.speak("Memory exported.")
            return True
        if user_text.strip().lower().startswith("/import"):
            parts = user_text.strip().split(maxsplit=1)
            path  = parts[1].strip() if len(parts) > 1 else ""
            if not path:
                self.ui.write_log("ERR: Usage — /import <path to export file>")
                self.speak("Please provide the path to the export file.")
                return True
            result = import_memory(input_path=path)
            self.ui.write_log(result)
            self.speak("Memory imported.")
            return True
        if user_text.strip().lower() in ("/help", "help", "commands"):
            help_text = (
                f"── {version_string()} · Chat Commands ────────────────\n"
                "/selftest          Run full system self-test\n"
                "/crashes           Show recent crash logs\n"
                "/status            Full system diagnostic\n"
                "/backup            Snapshot memory to zip\n"
                "/memstats          Memory system statistics\n"
                "/prune             Remove duplicate/old memories\n"
                "/export [path]     Export memories to JSON file\n"
                "/import <path>     Restore memories from export file\n"
                "/clear             Clear chat history\n"
                "/help              Show this command list\n"
                "─────────────────────────────────────────────────────\n"
                "Hotkeys: F4 mute · F11 fullscreen · Ctrl+Alt+A show/hide · Esc collapse"
            )
            self.ui.write_log(help_text)
            return True
        return False

