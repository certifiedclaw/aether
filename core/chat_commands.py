"""core/chat_commands.py — Aether Built-in Chat Commands

Extracted from main.py's AetherLocal god-object (continuation of the
Conversation Core split — see core/conversation_core.py's module
docstring for background). Mechanical extraction: `_handle_chat_command`
is the same logic that used to live inline in AetherLocal, moved here so
the built-in slash-command handling (/status, /clear, /selftest,
/crashes, /backup, /memstats, /prune, /export, /import, /newchat,
/chats, /switchchat, /renamechat, /deletechat, /help) is its
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

    @staticmethod
    def _format_remote_info(info: dict) -> str:
        """Human-readable connection steps for /remote (also printed at headless start)."""
        lines = ["SYS: Web dashboard", f"  Open:   {info['url']}   (or type {info['manual_url']} in the address bar)"]
        lines.append(f"  Code:   {info['code']}   (one use, valid {info['expires_min']} min; type /remote for a new one)")
        lines.append(f"  Phone:  open {info['auto_login_url']} to sign in without typing the code")
        if info.get("https"):
            lines.append("  The browser will warn about the certificate (it is self-signed): choose Advanced, then continue.")
        if info.get("loopback"):
            lines.append("  Reachable from this PC only. To use it from a phone, set AETHER_DASHBOARD_HOST=0.0.0.0 "
                         "(this needs a TLS certificate in the config folder).")
        return "\n".join(lines)

    def _cmd_remote(self) -> bool:
        dash = getattr(self, "_dashboard", None)
        if dash is None:
            self.ui.write_log(
                "SYS: The web dashboard is not running. Install it with: "
                "pip install fastapi \"uvicorn[standard]\" cryptography python-multipart")
        else:
            self.ui.write_log(self._format_remote_info(dash.pairing_info()))
        return True

    def _clear_chat_memory(self) -> None:
        memory = getattr(self, "_hybrid_mem", None) or self._semantic_mem
        if memory:
            try:
                memory.clear_session()
            except Exception as exc:
                self.ui.write_log(f"WRN: Could not reset short-term chat memory: {exc}")

    def _handle_chat_command(self, user_text: str) -> bool:
        """Built-in slash/text commands (status, /clear, /help, etc.).

        Returns True if `user_text` was a recognized command and has already
        been fully handled (caller should return immediately), False if it
        wasn't a command and normal LLM processing should continue.
        """
        if user_text.strip().lower() in ("/remote", "/pair", "/dashboard"):
            return self._cmd_remote()
        from main import (get_recent_crashes, backup_memory, memory_stats,
                           prune_memory, export_memory, import_memory,
                           run_self_test_async)  # lazy: avoids circular import at module load time
        command = user_text.strip()
        lowered = command.lower()
        if lowered == "/newchat" or lowered.startswith("/newchat "):
            self._chat_sessions.save_current(self._conversation)
            try:
                session = self._chat_sessions.create(command[len("/newchat"):].strip())
            except ValueError as exc:
                self.ui.write_log(f"ERR: {exc}")
                return True
            self._active_chat_id = session["id"]
            self._conversation = []
            self._clear_chat_memory()
            self.ui.clear_log()
            self._refresh_chat_list()
            self.ui.write_log(f"SYS: Started chat: {session['title']}")
            return True
        if lowered == "/chats":
            lines = ["SYS: Saved chats (most recent first):"]
            for session in self._chat_sessions.list_sessions():
                marker = "  (active)" if session["id"] == self._active_chat_id else ""
                lines.append(f"- {session['title']}{marker}")
            self.ui.write_log("\n".join(lines))
            return True
        if lowered == "/searchchats" or lowered.startswith("/searchchats "):
            query = command[len("/searchchats"):].strip()
            if not query:
                self.ui.write_log("ERR: Usage: /searchchats <text>")
                return True
            if len(query) > 200:
                self.ui.write_log("ERR: Search text must be 200 characters or fewer.")
                return True
            query = query.casefold()
            matches = []
            for session in self._chat_sessions.list_sessions():
                for message in session.get("messages", []):
                    if message.get("role") not in ("user", "assistant"):
                        continue
                    content = message.get("content")
                    if not isinstance(content, str) or query not in content.casefold():
                        continue
                    snippet = " ".join(content.split())
                    if len(snippet) > 180:
                        position = snippet.casefold().find(query)
                        start = max(0, position - 60)
                        has_more = start + 180 < len(snippet)
                        snippet = ("…" if start else "") + snippet[start:start + 180]
                        if has_more:
                            snippet += "…"
                    label = "You" if message["role"] == "user" else "Aether"
                    matches.append(f"- {session['title']} — {label}: {snippet}")
                    if len(matches) == 10:
                        break
                if len(matches) == 10:
                    break
            if matches:
                self.ui.write_log("SYS: Chat search results (up to 10):\n" + "\n".join(matches))
            else:
                self.ui.write_log("SYS: No saved chat messages matched that search.")
            return True
        if lowered.startswith("/switchchat "):
            title = command[len("/switchchat "):].strip()
            self._chat_sessions.save_current(self._conversation)
            session = self._chat_sessions.switch(title)
            if session is None:
                self.ui.write_log(f"ERR: No saved chat named '{title}'. Use /chats to list chats.")
                return True
            self._active_chat_id = session["id"]
            self._conversation = list(session["messages"])
            self._clear_chat_memory()
            self.ui.clear_log()
            self._refresh_chat_list()
            self.ui.write_log(f"SYS: Switched to chat: {session['title']}")
            for message in self._conversation:
                if message.get("role") in ("user", "assistant") and isinstance(message.get("content"), str):
                    label = "You" if message["role"] == "user" else "Aether"
                    self.ui.write_log(f"{label}: {message['content']}")
            return True
        if lowered.startswith("/renamechat "):
            title = command[len("/renamechat "):].strip()
            if not self._chat_sessions.rename_active(title):
                self.ui.write_log("ERR: Usage: /renamechat <new-name> (renames the active chat)")
            else:
                self._refresh_chat_list()
                self.ui.write_log("SYS: Chat renamed.")
            return True
        if lowered.startswith("/deletechat "):
            title = command[len("/deletechat "):].strip()
            deleting_active = title.casefold() == self._chat_sessions.active_title.casefold()
            if not self._chat_sessions.delete(title):
                self.ui.write_log("ERR: Could not delete that chat. The last remaining chat cannot be deleted.")
            else:
                active = self._chat_sessions.active_session
                self._active_chat_id = active["id"]
                self._conversation = list(active["messages"])
                if deleting_active:
                    self._clear_chat_memory()
                self._render_chat_history()
                self._refresh_chat_list()
                self.ui.write_log(f"SYS: Deleted chat: {title}")
            return True
        if user_text.strip().lower() in ("status", "aether status", "/status"):
            self._execute_tool("diagnostics", {})
            return True
        if user_text.strip().lower() == "/clear":
            with self._conversation_lock:
                self._conversation.clear()
            self._clear_chat_memory()
            self.ui.write_log("SYS: Chat history cleared.")
            self.speak("History cleared.")
            return True
        if user_text.strip().lower().split(" ")[0] == "/audit":
            try:
                from core import audit_log
                arg = user_text.strip()[len("/audit"):].strip()
                if arg.lower() == "verify":
                    ok, msg = audit_log.verify()
                    self.ui.write_log(f"SYS: Audit log: {'OK' if ok else 'PROBLEM'} - {msg}")
                else:
                    self.ui.write_log("SYS: " + audit_log.render(audit_log.read(**audit_log.parse_query(arg))))
            except Exception as e:  # noqa: BLE001
                self.ui.write_log(f"SYS: Audit log unavailable: {type(e).__name__}: {e}")
            return True
        if user_text.strip().lower().split(" ")[0] in ("/perf", "/speed"):
            def _perf():
                try:
                    from core import perf_stats
                    arg = user_text.strip().split(" ", 1)[1].strip() if " " in user_text.strip() else ""
                    hours = float(arg) if arg.replace(".", "", 1).isdigit() else 24.0
                    self.ui.write_log("SYS: " + perf_stats.render(perf_stats.full_report(hours)))
                except Exception as e:  # noqa: BLE001
                    self.ui.write_log(f"SYS: Performance report unavailable: {type(e).__name__}: {e}")
            import threading as _th    # probes Ollama /api/ps; keep it off the command loop
            _th.Thread(target=_perf, daemon=True, name="aether-perf").start()
            return True
        if user_text.strip().lower() in ("/resources", "/governor"):
            try:
                from core.resource_governor import get_governor
                self.ui.write_log("SYS: " + get_governor().status())
            except Exception as e:  # noqa: BLE001
                self.ui.write_log(f"SYS: Resource status unavailable: {type(e).__name__}: {e}")
            return True
        if user_text.strip().lower() in ("/doctor", "/doctor fix"):
            # Backgrounded like /selftest: the checks probe Ollama/remote hosts with real timeouts.
            apply = user_text.strip().lower().endswith("fix")
            self.ui.write_log("SYS: Running doctor" + (" (applying safe fixes)" if apply else "") + "…")

            def _doctor():
                try:
                    from core import doctor
                    found = doctor.run_checks()
                    fixed = doctor.apply_fixes(found) if apply else None
                    if fixed:
                        found = doctor.run_checks()
                    self.ui.write_log(doctor.render(found, fixed))
                except Exception as e:  # noqa: BLE001 - a diagnostic must never take the app down
                    self.ui.write_log(f"SYS: Doctor failed: {type(e).__name__}: {e}")

            import threading as _th
            _th.Thread(target=_doctor, daemon=True, name="aether-doctor").start()
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
                "/audit [N] [tool=x] [decision=denied] [hours=24] [words]  Show recent tool calls and approvals; /audit verify checks the log hasn't been tampered with\n"
                "/perf [hours]      Speed per model (median/p95 time, tokens/s), memory use, cloud tokens today\n"
                "/remote            Show the web dashboard address and a one-time pairing code (phone or browser)\n"
                "/resources         Show whether background jobs are paused because the machine is busy\n"
                "/doctor            Find install problems and how to fix them (/doctor fix applies safe fixes)\n"
                "/backup            Snapshot memory to zip\n"
                "/memstats          Memory system statistics\n"
                "/prune             Remove duplicate/old memories\n"
                "/export [path]     Export memories to JSON file\n"
                "/import <path>     Restore memories from export file\n"
                "/clear             Clear chat history\n"
                "/newchat [name]    Start a new saved chat\n"
                "/chats             List saved chats\n"
                "/switchchat <name> Switch to a saved chat\n"
                "/renamechat <name>  Rename the active chat\n"
                "/deletechat <name> Delete a saved chat\n"
                "/searchchats <text> Search saved conversations\n"
                "/help              Show this command list\n"
                "─────────────────────────────────────────────────────\n"
                "Hotkeys: F4 mute · F11 fullscreen · Ctrl+Alt+A show/hide · Esc collapse"
            )
            self.ui.write_log(help_text)
            return True
        return False

