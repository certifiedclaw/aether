"""
Aether — Improved Edition
STT (Whisper / Vosk)  +  Ollama LLM  +  TTS (EdgeTTS / Kokoro / ElevenLabs)

NEW in this release (integrated from Aether v4.2):
  ✅ Semantic memory  — SQLite + sentence-transformers, persists across sessions
  ✅ Smart context    — auto-compresses history to fit LLM token window
  ✅ RAG engine       — index local docs/Obsidian vault, query from them
  ✅ Plugin system    — drop .py files into plugins/ for instant new tools
  ✅ Plugin hot-reload — changes in plugins/ take effect without restart
  ✅ Task scheduler   — register recurring background tasks
  ✅ Global hotkey    — Ctrl+Alt+A to show/hide (requires keyboard package)
  ✅ Safety layer     — path ACL + high-risk tool confirmation callbacks
  ✅ MCP bridge       — connect external MCP servers via config
  ✅ OSINT tools      — WHOIS, DNS, breach checks, dorks, port scan, and more
  ✅ PDF tools        — summarise, extract, query PDFs
  ✅ Diagnostics      — type "status" in chat for a full system report
  ✅ Rotating logs    — logs/ directory, 5 MB cap per file
"""
# ── Silence verbose logs + block heavy unused backends ─────────────────────
import os as _os
_os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL",  "3")
_os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
_os.environ.setdefault("GRPC_VERBOSITY",         "ERROR")
_os.environ.setdefault("USE_TF",                 "0")
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
_os.environ.setdefault("HF_HUB_OFFLINE",      "1")
_os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
_os.environ.setdefault("HF_DATASETS_OFFLINE",  "1")
import warnings as _warnings
_warnings.filterwarnings("ignore", category=UserWarning)
_warnings.filterwarnings("ignore", category=DeprecationWarning)
_warnings.filterwarnings("ignore", category=FutureWarning)
# ───────────────────────────────────────────────────────────────────────────

# ── Headless mode — run on a server with no display/GUI/mic ────────────────
# Set AETHER_HEADLESS=1 (or true/yes) to skip PyQt6 and the desktop UI
# entirely; interaction happens via the web dashboard / phone remote
# instead (dashboard/server.py already delivers typed commands straight
# to AetherLocal, bypassing the UI). See core/headless_ui.py.
HEADLESS = _os.environ.get("AETHER_HEADLESS", "").strip().lower() in ("1", "true", "yes")

# ── Bootstrap: auto-install base UI packages before anything else ──────────
import importlib.util as _ilu
import subprocess      as _sp
import sys             as _sys

_BASE_PKGS = [
    ("psutil",      "psutil"),
    ("numpy",       "numpy"),
    ("sounddevice", "sounddevice"),
    ("PIL",         "pillow"),
    ("requests",    "requests"),
]
if not HEADLESS:
    _BASE_PKGS.insert(0, ("PyQt6", "PyQt6"))

def _bootstrap() -> None:
    need = [pkg for mod, pkg in _BASE_PKGS if _ilu.find_spec(mod) is None]
    if not need:
        return
    _skip = _os.environ.get("AETHER_SKIP_INSTALL", "").strip().lower()
    if _skip in ("1", "true", "yes") or (HEADLESS and _skip not in ("0", "false", "no")):
        print(f"[Aether] Missing packages ({', '.join(need)}) but runtime installs are "
              "disabled (server mode) - continuing; install them in the image.")
        return
    print(f"\n[Aether] First-run setup — installing: {', '.join(need)}")
    print("[Aether] This happens only once.\n")
    _sp.run([_sys.executable, "-m", "pip", "install", *need], check=True)
    print("\n[Aether] Base packages ready — restarting…\n")
    _os.execv(_sys.executable, [_sys.executable] + _sys.argv)

_bootstrap()
# ───────────────────────────────────────────────────────────────────────────

import json
import logging
import queue
import re
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

# numpy (was used by _VADBuffer / the mic callbacks) moved with them to
# core/listen_loops.py -- no longer needed here.
try:
    import sounddevice as sd
except Exception as _e:
    # PortAudio (the native lib sounddevice binds to) often isn't present
    # on a headless server. Mic-based STT paths already run inside their
    # own try/except and simply won't work without it -- fine for a
    # server driven via the dashboard/API instead of a microphone.
    sd = None
    if HEADLESS:
        print(f"[Aether] sounddevice unavailable ({_e}) -- mic input disabled, continuing headless.")
    else:
        raise

if HEADLESS:
    from core.headless_ui import HeadlessUI as AetherUI
else:
    from ui import AetherUI
from version import APP_NAME, __version__, version_string
from memory.memory_manager import load_memory as _load_memory_raw, update_memory as _update_memory_raw, format_memory_for_prompt

# ── Memory cache — avoid re-reading the JSON file every turn ──────────────
_memory_cache: dict | None = None

def load_memory() -> dict:
    global _memory_cache
    if _memory_cache is None:
        _memory_cache = _load_memory_raw()
    return _memory_cache

def update_memory(data: dict) -> None:
    global _memory_cache
    _update_memory_raw(data)
    _memory_cache = None  # invalidate so next turn re-reads
from core.llm_client import call_llm, call_llm_stream, get_llm_settings, get_context_window, get_llm_provider, get_gemini_settings, GEMINI_BASE_URL

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

# ── Aether-sourced modules ────────────────────────────────────────────
from memory.semantic_memory    import MemoryEngine
from memory.rag                import RAGEngine
from core.smart_context        import compress_if_needed, estimate_tool_tokens
from core.tier_router          import classify as _classify_tier, Tier as _Tier
from core.tool_router           import filter_tools as _filter_tools_for_turn
from core.plugins              import PluginSystem
from core.plugin_watcher       import start_plugin_watcher
from core.task_scheduler       import TaskScheduler
from core.hotkey_manager       import HotkeyManager
from core.safety               import get_safety
from core.mcp_bridge            import MCPBridge
from core                       import undo as undo_stack
from memory.config_manager     import get_mark_config

# ── Advanced modules ───────────────────────────────────────────────
from core.observability        import get_obs
from core.goal_state           import GoalState, GOAL_TOOL_DECLARATIONS
from memory.hybrid_memory      import HybridMemory
from core.conversation_core    import ConversationCoreMixin
from core.tool_dispatch        import ToolDispatchMixin
from core.prompt_builder       import PromptBuilderMixin
from core.chat_commands        import ChatCommandsMixin
from core.listen_loops         import ListenLoopsMixin
try:
    from core.loop_guard   import LoopGuard
    from core.evaluator    import Evaluator
    from core.file_watcher import FileWatcher, start_file_watcher
    _ADVANCED_OK = True
except ImportError:
    _ADVANCED_OK = False

# ── Crash logger, self-test, memory tools, config validator ────────
try:
    from core.crash_logger  import log_crash, get_recent_crashes
    from core.self_test     import run_self_test, run_self_test_async
    from core.memory_tools  import backup_memory, export_memory, import_memory, prune_memory, memory_stats
    from core.config_validator import validate_config
    _V4_OK = True
except ImportError as _v4_err:
    _V4_OK = False
    def log_crash(exc, **kw): pass
    def get_recent_crashes(n=5): return "Crash logger not available."
    def run_self_test(aether=None, config=None): return None
    def run_self_test_async(callback, aether=None, config=None):
        callback(None)
        return None
    def backup_memory(**kw): return "Memory tools not available."
    def export_memory(**kw): return "Memory tools not available."
    def import_memory(**kw): return "Memory tools not available."
    def prune_memory(**kw): return "Memory tools not available."
    def memory_stats(): return "Memory tools not available."
    def validate_config(config=None): return None

# ── Integration modules (lazy — graceful if not installed) ─────────
try:
    from integrations.langchain_tools import (
        langchain_tool, LANGCHAIN_TOOL_DECLARATION, langchain_available,
    )
    # The wrapper module imports fine even without langchain installed (all
    # langchain imports are lazy), so probe the real dependencies too --
    # otherwise the tool is offered to the model and can only fail.
    _LANGCHAIN_OK = langchain_available()
    if not _LANGCHAIN_OK:
        LANGCHAIN_TOOL_DECLARATION = None
except ImportError:
    _LANGCHAIN_OK = False
    LANGCHAIN_TOOL_DECLARATION = None

try:
    from integrations.oi_shell import oi_tool, OI_TOOL_DECLARATION
    _OI_OK = True
except ImportError:
    _OI_OK = False
    OI_TOOL_DECLARATION = None

try:
    from integrations.llama_index_rag import llama_index_tool, LLAMA_INDEX_TOOL_DECLARATION
    _LLAMA_OK = True
except ImportError:
    _LLAMA_OK = False
    LLAMA_INDEX_TOOL_DECLARATION = None

try:
    from integrations.searxng import searxng_tool, SEARXNG_TOOL_DECLARATION
    _SEARXNG_OK = True
except ImportError:
    _SEARXNG_OK = False
    SEARXNG_TOOL_DECLARATION = None

# ── Logging setup ──────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
from logging.handlers import RotatingFileHandler as _RFH
_root_log = logging.getLogger()
_root_log.setLevel(logging.INFO)
_fh = _RFH("logs/aether.log", maxBytes=5*1024*1024, backupCount=7, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s — %(message)s"))
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter("%(levelname)-8s %(name)s — %(message)s"))
if not _root_log.handlers:
    _root_log.addHandler(_fh)
    _root_log.addHandler(_ch)
logger = logging.getLogger("aether")
# ───────────────────────────────────────────────────────────────────────────

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = _get_base_dir()
from core import config_home as _config_home
API_CONFIG_PATH = _config_home.config_path()
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

# SAMPLE_RATE_IN, BLOCK_SIZE, CHANNELS moved to core/listen_loops.py.

# ---------------------------------------------------------------------------
# Tool declarations
# ---------------------------------------------------------------------------

# Tools whose raw output must go back through the LLM for synthesis before
# being spoken/displayed. Defined once here rather than inside the hot loop.
_NEEDS_LLM_ROUND: frozenset[str] = frozenset({
    "web_search", "screen_process", "agent_task",
    "rag_query", "osint_lookup", "pdf_tool",
    "code_helper", "dev_agent",  # return raw generated output
    "extra_tool",                # many actions return raw data / file paths
    # v3 integrations — always need LLM to synthesize their output
    "langchain", "open_interpreter", "llama_index",
})

# TOOL_DECLARATIONS, _TYPE_MAP, _convert_type, _convert_props, and
# _to_ollama_tools moved to core/tool_declarations.py (mechanical
# extraction -- pure data + pure functions, no logic changed).
from core.tool_declarations import TOOL_DECLARATIONS, _to_ollama_tools



_INTEGRATION_DECLS = [
    d for d in [LANGCHAIN_TOOL_DECLARATION, OI_TOOL_DECLARATION, LLAMA_INDEX_TOOL_DECLARATION]
    if d is not None
]
OLLAMA_TOOLS = _to_ollama_tools(TOOL_DECLARATIONS + GOAL_TOOL_DECLARATIONS + _INTEGRATION_DECLS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    return _config_home.load_config(API_CONFIG_PATH)


_system_prompt_cache: str | None = None
_system_prompt_mtime: float | None = None


def _load_system_prompt() -> str:
    """Reads the system prompt file, cached and invalidated on file change.

    _build_system_prompt() calls this on every single message, so caching
    avoids a disk read per turn.
    """
    global _system_prompt_cache, _system_prompt_mtime
    fallback = (
        "You are Aether, a local voice assistant. "
        "Be concise, direct, and always use the provided tools to complete tasks. "
        "Never simulate or guess results — always call the appropriate tool."
    )
    try:
        mtime = PROMPT_PATH.stat().st_mtime
    except Exception:
        return _system_prompt_cache if _system_prompt_cache is not None else fallback

    if _system_prompt_cache is None or mtime != _system_prompt_mtime:
        try:
            _system_prompt_cache = PROMPT_PATH.read_text(encoding="utf-8")
        except Exception:
            _system_prompt_cache = fallback
        _system_prompt_mtime = mtime

    return _system_prompt_cache


# _VADBuffer, SAMPLE_RATE_IN, BLOCK_SIZE, CHANNELS moved to
# core/listen_loops.py (mechanical extraction, used nowhere else).

# ---------------------------------------------------------------------------
# AetherLocal — main assistant class
# ---------------------------------------------------------------------------

class AetherLocal(ConversationCoreMixin, ToolDispatchMixin, PromptBuilderMixin, ChatCommandsMixin, ListenLoopsMixin):

    def __init__(self, ui: AetherUI):
        self.ui               = ui
        self._config          = _load_config()
        self._stt             = None
        self._tts             = None
        self._tts_ready       = threading.Event()
        self._speaking        = False
        self._speaking_lock   = threading.Lock()
        self._text_queue:     queue.Queue = queue.Queue()
        self._tts_queue:      queue.Queue = queue.Queue()
        self._cancel_current_turn = threading.Event()
        from core.chat_history import ChatSessionStore
        self._chat_sessions = ChatSessionStore()
        self._active_chat_id = self._chat_sessions.active_id
        self._conversation:   list[dict]  = list(self._chat_sessions.active_messages)
        # STT (whisper/vosk), the dashboard command loop, the proactive loop,
        # and the system monitor loop can each call _process_message on their
        # own thread. Without serialization two calls can interleave reads/
        # writes of _conversation (lost appends, or a reply landing after the
        # wrong user turn) — this makes each full message-processing pass
        # atomic with respect to the others.
        self._conversation_lock = threading.RLock()

        # ── Safety layer: gates high-risk operations behind a blocking UI
        # confirmation dialog. Was previously imported (get_safety) but
        # never instantiated or wired to a callback anywhere in the
        # codebase -- requires_confirmation()/request_confirmation() would
        # silently deny everything (no callback registered) or just never
        # get called at all. self.ui.confirm_blocking() is safe to call
        # from any background worker thread (which is where all tool
        # dispatch happens -- see _text_command_loop/_execute_tool).
        self._safety = get_safety()
        self._safety.set_confirm_callback(self.ui.confirm_blocking)

        # ── Aether-sourced subsystems ────────────────────────────────
        self._semantic_mem: MemoryEngine | None = None
        self._rag:          RAGEngine    | None = None
        self._plugins:      PluginSystem | None = None
        self._scheduler:    TaskScheduler | None = None
        self._hotkey:       HotkeyManager | None = None
        self._mcp:          MCPBridge    | None = None

        # ── Advanced systems ───────────────────────────────────────
        self._hybrid_mem:   HybridMemory | None = None
        self._goals:        GoalState    | None = None
        self._loop_guard                        = None
        self._evaluator                         = None
        self._file_watcher                      = None
        self._obs           = get_obs()

        # ── Barge-in: audio energy accumulator used while TTS is speaking ──
        self._barge_in_energy: float = 0.0
        self._barge_in_count:  int   = 0
        self._BARGE_IN_THRESHOLD: float = 0.02   # RMS threshold
        self._BARGE_IN_FRAMES:    int   = 6      # consecutive loud frames to trigger

        # ── Dashboard, proactive check-ins, monitor (ported from Mark-XLVIII) ─
        self._dashboard                         = None   # DashboardServer | None
        self._proactive                         = None   # ProactiveEngine | None
        self._sys_monitor                       = None   # SystemMonitor   | None
        self._last_user_speech: float           = time.monotonic()

        self.ui.on_text_command = self._on_text_command
        self.ui.on_stop_command = self._stop_current_response
        self.ui.on_action_catalog = self._tools_for_current_turn
        self.ui.on_action_command = self._on_action_command
        self._refresh_chat_list()
        self._render_chat_history()

    def _render_chat_history(self) -> None:
        self.ui.clear_log()
        for message in self._conversation:
            if message.get("role") in ("user", "assistant") and isinstance(message.get("content"), str):
                label = "You" if message["role"] == "user" else "Aether"
                self.ui.write_log(f"{label}: {message['content']}")

    def _refresh_chat_list(self) -> None:
        sessions = self._chat_sessions.list_sessions()
        self.ui.set_chat_sessions(
            [session["title"] for session in sessions],
            self._chat_sessions.active_title,
        )

    # ------------------------------------------------------------------
    # System prompt — now injects semantic memory context
    # ------------------------------------------------------------------

    # ── System prompt assembly — moved to core/prompt_builder.py ───
    # See PromptBuilderMixin, which AetherLocal now inherits from.


    # ------------------------------------------------------------------
    # Message stats — pushes per-response metrics to the UI stats panel
    # ------------------------------------------------------------------

    # ── Message stats — moved to core/conversation_core.py ────────


    # ------------------------------------------------------------------
    # TTS queue worker
    # ------------------------------------------------------------------

    def _tts_worker(self) -> None:
        self._tts_ready.wait(timeout=120)
        while True:
            text = self._tts_queue.get()
            try:
                if text and self._tts:
                    with self._speaking_lock:
                        self._speaking = True
                    self.ui.set_state("SPEAKING")
                    self._tts.speak(text)
            except Exception as e:
                self.ui.write_log(f"ERR: Speech synthesis failed: {e}")
            finally:
                self._tts_queue.task_done()
                if self._tts_queue.empty():
                    with self._speaking_lock:
                        self._speaking = False
                    if not self.ui.muted:
                        self.ui.set_state("LISTENING")

    def set_speaking(self, value: bool) -> None:
        with self._speaking_lock:
            self._speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def _interrupt_speaking(self, reason: str = "barge-in") -> None:
        """Stop TTS playback immediately (barge-in)."""
        try:
            if self._tts and hasattr(self._tts, "stop"):
                self._tts.stop()
        except Exception:
            pass
        # Drain the TTS queue so queued sentences are discarded
        while not self._tts_queue.empty():
            try:
                self._tts_queue.get_nowait()
            except Exception:
                break
        with self._speaking_lock:
            self._speaking = False
        self.ui.set_state("LISTENING")
        self.ui.write_log(f"SYS: [{reason}] speech interrupted")

    def speak(self, text: str) -> None:
        if not text or not self._tts:
            return
        with self._speaking_lock:
            self._speaking = True
        self._tts_queue.put(text)

    def speak_error(self, tool_name: str, error) -> None:
        short = str(error)[:300]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        spoken = f"{tool_name} encountered an error."
        if tool_name == "LLM":
            low = short.lower()
            if "404" in low:
                spoken = "The language model is not installed. Pull it with ollama, or pick another model in settings."
            elif "400" in low or "401" in low or "403" in low:
                spoken = "The language model rejected the request. Check the model name and API key in settings."
            elif "cannot connect" in low or "timed out" in low:
                spoken = "I can't reach the language model. Check that Ollama is running."
        self.speak(spoken)

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    def reconfigure(self, new_config: dict) -> None:
        threading.Thread(
            target=self._do_reconfigure, args=(new_config,), daemon=True
        ).start()

    def _tts_fallback_to_edge(self, failed_engine: str, config: dict) -> bool:
        """A failed or unsupported TTS engine (e.g. Kokoro on Python 3.13+) must not
        leave Aether mute. Fall back to EdgeTTS, the project default. It is a CLOUD
        voice (the spoken text goes to Microsoft, and it needs internet), so say so.
        The configured voice belongs to the failed engine, hence EdgeTTS's own."""
        if failed_engine == "edgetts":
            return False
        try:
            self.ui.write_log("SYS: Falling back to EdgeTTS (cloud voice, needs internet).")
            from core.tts import create_tts_player
            self._tts = create_tts_player(
                {**config, "tts_engine": "edgetts", "tts_voice": "en-US-GuyNeural"})
            self._tts_ready.set()
            return True
        except Exception as e2:
            self.ui.write_log(f"ERR: TTS fallback — {e2}")
            return False

    def _do_reconfigure(self, new_config: dict) -> None:
        old_stt_engine = self._config.get("stt_engine", "whisper").lower()
        old_llm_model  = self._config.get("llm_model", "")
        new_stt_engine = new_config.get("stt_engine", "whisper").lower()
        self._config = new_config
        # Invalidate the config_manager singleton so subsystems get fresh values
        try:
            from memory.config_manager import invalidate_config_cache
            invalidate_config_cache()
        except Exception:
            pass
        try:
            from core.installer import install_for_config
            install_for_config(new_config, log=self.ui.write_log)
        except Exception as e:
            self.ui.write_log(f"ERR: Dependency install — {e}")
        try:
            from core.tts import create_tts_player
            self._tts = create_tts_player(new_config)
            self._tts_ready.set()
            self.ui.write_log("SYS: TTS reconfigured.")
        except Exception as e:
            self.ui.write_log(f"ERR: TTS reconfigure — {e}")
            self._tts_fallback_to_edge(new_config.get("tts_engine", "edgetts").lower(), new_config)
        if old_stt_engine == new_stt_engine:
            try:
                stt_language = new_config.get("stt_language", "auto")
                if new_stt_engine == "vosk":
                    from core.stt import VoskSTT
                    self._stt = VoskSTT(new_config.get("vosk_model_path"), language=stt_language)
                else:
                    from core.stt import WhisperSTT
                    self._stt = WhisperSTT(new_config.get("stt_model", "small"), language=stt_language)
                self.ui.write_log("SYS: STT reconfigured.")
            except Exception as e:
                self.ui.write_log(f"ERR: STT reconfigure — {e}")
        else:
            self.ui.write_log("SYS: STT engine changed — restart required.")
        if new_config.get("llm_model", "") != old_llm_model:
            self.ui.write_log("SYS: Warming up new LLM model…")
            from core.llm_client import warmup_model
            static_prompt = _load_system_prompt()
            warmup_model(system_prompt=static_prompt)
            self.ui.write_log("SYS: New LLM model ready.")
        if old_stt_engine == new_stt_engine:
            self.speak("Configuration applied.")
        else:
            self.speak("LLM and TTS updated. Restart for speech engine change.")

    # ------------------------------------------------------------------
    # Text command (from UI)
    # ------------------------------------------------------------------

    def _on_text_command(self, text: str) -> None:
        self._text_queue.put(text)

    def _on_action_command(self, name: str, args: dict) -> None:
        self._text_queue.put(("action", name, args))

    def _run_action_from_ui(self, name: str, args: dict) -> None:
        with self._conversation_lock:
            self.ui.write_log(f"SYS: Running action from Actions: {name}")
            try:
                result = self._execute_tool(name, args)
            except Exception as exc:
                logger.exception("Direct UI action failed: %s", name)
                self.ui.write_log(f"ERR: {name} — {exc}")
                return
            if result != "__SILENT__":
                self.ui.write_log(f"Aether: {result}")

    def _stop_current_response(self) -> None:
        self._cancel_current_turn.set()
        self._interrupt_speaking(reason="response stopped")
        self.ui.write_log("SYS: Stop requested; an active tool action may finish first.")

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    # ── Tool dispatch (execute_tool + all _tool_* handlers) ────────
    # Moved to core/tool_dispatch.py — see ToolDispatchMixin, which
    # AetherLocal now inherits from (see class declaration below).


    # ------------------------------------------------------------------
    # LLM processing loop — with smart context compression
    # ------------------------------------------------------------------

    # ── _process_message / _process_message_locked — moved to ─────
    # core/conversation_core.py (ConversationCoreMixin).


    # ── Built-in chat commands — moved to core/chat_commands.py ────
    # (ChatCommandsMixin).


    # ── Conversation core (context prep / tool loop / finalize) ───
    # Moved to core/conversation_core.py — see ConversationCoreMixin,
    # which AetherLocal now inherits from (see class declaration below).
    # Methods that used to live here: _prepare_conversation_messages,
    # _execute_tool_calls, _tools_for_current_turn, _process_message_inner,
    # _finalize_response.

    # _listen_whisper, _listen_vosk, _phone_audio_stt_loop moved to
    # core/listen_loops.py -- see ListenLoopsMixin, which AetherLocal now
    # inherits from (see class declaration above).

    def _dashboard_command_loop(self) -> None:
        """Drains text commands submitted via the web dashboard / phone remote."""
        while True:
            if self._dashboard is None:
                time.sleep(1.0)
                continue
            try:
                text = self._dashboard._command_queue.get(timeout=0.5)
                if text and text.strip():
                    self._on_text_command(text)
            except queue.Empty:
                pass
            except Exception:
                time.sleep(0.5)

    def _proactive_loop(self) -> None:
        """Periodically checks whether Aether should say something unprompted."""
        while True:
            time.sleep(60)
            if self._proactive is None:
                continue
            try:
                # Unprompted chatter yields to a busy host (core/resource_governor.py).
                from core.resource_governor import get_governor
                if not get_governor().allow_background():
                    continue
                if self._proactive.should_trigger(self._last_user_speech):
                    try:
                        from memory.memory_manager import load_memory
                        memory = load_memory()
                    except Exception:
                        memory = {}
                    prompt = self._proactive.build_prompt(memory)
                    self._proactive.mark_triggered()
                    self._process_message(prompt)
            except Exception as e:
                self.ui.write_log(f"WRN: Proactive check — {e}")

    def _system_monitor_loop(self) -> None:
        """Background voice alerts when CPU/RAM/temp/GPU cross thresholds."""
        while True:
            time.sleep(10)
            if self._sys_monitor is None:
                continue
            try:
                # Speak/log the alert directly. Sending it through _process_message
                # injected it as a user turn: the model saw instructions as if the
                # user said them, and even called save_memory on a GPU alert.
                alert = self._sys_monitor.check(plain=True)
                if alert:
                    self.ui.write_log(f"SYS: {alert}")
                    self.speak(alert)
            except Exception as e:
                self.ui.write_log(f"WRN: System monitor — {e}")

    def _text_command_loop(self) -> None:
        while True:
            try:
                command = self._text_queue.get(timeout=0.5)
                if (isinstance(command, tuple) and len(command) == 3
                        and command[0] == "action"):
                    self._run_action_from_ui(command[1], command[2])
                elif isinstance(command, str) and command.strip():
                    self._process_message(command)
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Startup — initialise all new subsystems
    # ------------------------------------------------------------------

    def _init_new_subsystems(self) -> None:
        """Initialise Aether-sourced modules in a background thread."""
        cfg = get_mark_config()

        self._init_memory_subsystems(cfg)
        self._init_plugins_and_scheduling(cfg)
        self._init_advanced_subsystems(cfg)

        self.ui.write_log("SYS: All systems nominal.")

        self._init_dashboard_and_integrations()

    def _init_memory_subsystems(self, cfg) -> None:
        # Memory: init semantic_mem first as fallback, then try hybrid_mem.
        # If hybrid_mem succeeds it becomes the sole active engine and
        # semantic_mem is set to None so nothing writes to the DB twice.
        try:
            self._semantic_mem = MemoryEngine(cfg)
            self.ui.write_log("SYS: Semantic memory ready (fallback).")
        except Exception as e:
            self.ui.write_log(f"WRN: Semantic memory — {e}")

        # RAG engine
        try:
            self._rag = RAGEngine(cfg)
            self.ui.write_log("SYS: RAG engine ready.")
        except Exception as e:
            self.ui.write_log(f"WRN: RAG engine — {e}")

        # ── Hybrid memory (BM25 + dense + reranker) ───────────────
        try:
            self._hybrid_mem = HybridMemory(cfg)
            stats = self._hybrid_mem.stats()
            self.ui.write_log(
                f"SYS: Hybrid memory ready "
                f"(BM25={'yes' if stats['bm25_docs'] > 0 else 'empty'}, "
                f"encoder={'yes' if stats['semantic'] else 'no'}, "
                f"reranker={'yes' if stats['reranker'] else 'no'}).")
            # Hybrid supersedes semantic — disable it to prevent dual DB writes
            self._semantic_mem = None
            self.ui.write_log("SYS: Semantic memory deactivated (hybrid is active).")
        except Exception as e:
            self.ui.write_log(f"WRN: Hybrid memory — {e} — falling back to semantic memory.")

    def _init_plugins_and_scheduling(self, cfg) -> None:
        # Plugin system
        try:
            self._plugins = PluginSystem(cfg)
            count = self._plugins.load_all()
            self.ui.write_log(f"SYS: Plugins — {count} loaded.")
            start_plugin_watcher(self._plugins, plugin_dir="./plugins")
        except Exception as e:
            self.ui.write_log(f"WRN: Plugins — {e}")

        # Task scheduler
        try:
            self._scheduler = TaskScheduler(config=cfg)
            self._scheduler.start()
            self.ui.write_log("SYS: Task scheduler running.")
        except Exception as e:
            self.ui.write_log(f"WRN: Task scheduler — {e}")

        # Global hotkey
        try:
            self._hotkey = HotkeyManager(config=cfg)
            self._hotkey.start()
            _hk_label = self._hotkey.hotkey.replace("+", "+").upper()
            self.ui.write_log(f"SYS: Global hotkey registered ({_hk_label}).")
        except Exception as e:
            self.ui.write_log(f"WRN: Hotkey — {e}")

        # MCP bridge
        try:
            self._mcp = MCPBridge(cfg)
            mcp_tools = self._mcp.list_all_tools()
            if mcp_tools:
                self.ui.write_log(f"SYS: MCP bridge — {len(mcp_tools)} tools.")
            else:
                self.ui.write_log("SYS: MCP bridge ready (no servers configured).")
        except Exception as e:
            self.ui.write_log(f"WRN: MCP bridge — {e}")

    def _init_advanced_subsystems(self, cfg) -> None:
        # ── Goal state manager ────────────────────────────────────
        try:
            self._goals = GoalState(cfg)
            self._goals.start()
            active = len(self._goals.list_active())
            self.ui.write_log(f"SYS: Goal manager ready ({active} active goals).")
        except Exception as e:
            self.ui.write_log(f"WRN: Goal manager — {e}")

        # ── Loop guard ────────────────────────────────────────────
        if _ADVANCED_OK:
            try:
                self._loop_guard = LoopGuard(max_repeats=2, window=6)
                self.ui.write_log("SYS: Loop guard active.")
            except Exception as e:
                self.ui.write_log(f"WRN: Loop guard — {e}")

            # ── Evaluator ─────────────────────────────────────────
            try:
                self._evaluator = Evaluator(enabled=True, threshold=0.6)
                self.ui.write_log("SYS: Self-evaluator active.")
            except Exception as e:
                self.ui.write_log(f"WRN: Evaluator — {e}")

            # ── File watcher → auto-index dropped files ───────────
            try:
                self._file_watcher = start_file_watcher(
                    self._rag, cfg, notify_cb=self.ui.write_log)
                if self._file_watcher:
                    self.ui.write_log("SYS: File watcher running.")
            except Exception as e:
                self.ui.write_log(f"WRN: File watcher — {e}")

    def _init_dashboard_and_integrations(self) -> None:
        # ── Web dashboard (ported from Mark-XLVIII) ───────────────
        try:
            from dashboard.server import start_dashboard_server
            self._dashboard = start_dashboard_server(
                wake_callback=lambda: self.ui.set_state("LISTENING"),
            )
            if self._dashboard:
                from dashboard.server import attach_ui_mirror
                attach_ui_mirror(self._dashboard, self.ui)
                self.ui.write_log(
                    f"SYS: Web dashboard — {self._dashboard.get_url()}  "
                    "(type /remote to get the pairing code)")
                if _os.environ.get("AETHER_HEADLESS"):
                    # No window and no button on a server: print the code where `docker logs` shows it.
                    self.ui.write_log(self._format_remote_info(self._dashboard.pairing_info()))
            else:
                self.ui.write_log(
                    "SYS: Dashboard not started — pip install fastapi "
                    "\"uvicorn[standard]\" cryptography python-multipart"
                )
        except Exception as e:
            self.ui.write_log(f"WRN: Dashboard — {e}")

        # ── Proactive check-in engine ─────────────────────────────
        try:
            from actions.proactive import ProactiveEngine
            self._proactive = ProactiveEngine(
                min_silence_secs=self._config.get("proactive_min_silence_secs", 900),
                check_cooldown=self._config.get("proactive_check_cooldown", 600),
            )
            self.ui.write_log("SYS: Proactive engine ready.")
        except Exception as e:
            self.ui.write_log(f"WRN: Proactive engine — {e}")

        # ── System resource monitor (voice alerts) ────────────────
        try:
            from actions.system_monitor import SystemMonitor
            self._sys_monitor = SystemMonitor()
            self.ui.write_log("SYS: System monitor ready.")
        except Exception as e:
            self.ui.write_log(f"WRN: System monitor — {e}")

        # ── Report integration status ─────────────────────────────
        integration_status = []
        if _LANGCHAIN_OK:
            integration_status.append("LangChain ✓")
        if _OI_OK and self._config.get("integrations.open_interpreter", False):
            integration_status.append("OpenInterpreter ✓")
        if _LLAMA_OK:
            integration_status.append("LlamaIndex ✓")
        if integration_status:
            self.ui.write_log(f"SYS: Integrations ready — {', '.join(integration_status)}")
        else:
            self.ui.write_log(
                "SYS: No v3 integrations installed. "
                "Run: pip install langchain langchain-community langchain-ollama "
                "duckduckgo-search llama-index llama-index-llms-ollama "
                "llama-index-embeddings-ollama"
            )

    def run(self) -> None:
        try:
            self.ui.on_reconfigure = self.reconfigure

            from core.llm_client import ensure_ollama_running, warmup_model
            self.ui.write_log("SYS: Checking Ollama…")
            if ensure_ollama_running():
                self.ui.write_log("SYS: Ollama OK.")
            else:
                self.ui.write_log("ERR: Ollama unavailable — run: ollama serve")

            stt_engine   = self._config.get("stt_engine",   "whisper").lower()
            stt_language = self._config.get("stt_language", "auto")
            stt_model    = self._config.get("stt_model",    "small")
            tts_engine   = self._config.get("tts_engine",   "edgetts").lower()

            self.ui.show_startup_panel()

            _warmup_done = threading.Event()
            _stt_done    = threading.Event()

            def _do_warmup():
                try:
                    static_prompt = _load_system_prompt()
                    warmup_model(system_prompt=static_prompt)
                    self.ui.write_log("SYS: LLM ready.")
                    self.ui.mark_startup_ready("llm")
                except Exception as e:
                    self.ui.write_log(f"ERR: LLM warmup — {e}")
                    self.ui.mark_startup_ready("llm", error=True)
                finally:
                    _warmup_done.set()

            _voice_off = HEADLESS and _os.environ.get(
                "AETHER_HEADLESS_VOICE", "").strip().lower() not in ("1", "true", "yes")

            def _do_stt():
                if _voice_off:
                    self.ui.write_log("SYS: Headless - STT skipped (AETHER_HEADLESS_VOICE=1 to enable).")
                    self.ui.mark_startup_ready("stt")
                    _stt_done.set()
                    return
                try:
                    self.ui.write_log(f"SYS: Loading {stt_engine.upper()} STT…")
                    if stt_engine == "vosk":
                        from core.stt import VoskSTT
                        self._stt = VoskSTT(
                            self._config.get("vosk_model_path"),
                            language=stt_language,
                        )
                    else:
                        from core.stt import WhisperSTT
                        self._stt = WhisperSTT(stt_model, language=stt_language)
                    self.ui.write_log("SYS: STT ready.")
                    self.ui.mark_startup_ready("stt")
                except Exception as e:
                    self.ui.write_log(f"ERR: STT — {e}")
                    self.ui.mark_startup_ready("stt", error=True)
                finally:
                    _stt_done.set()

            def _do_tts():
                if _voice_off:
                    self.ui.write_log("SYS: Headless - TTS skipped (AETHER_HEADLESS_VOICE=1 to enable).")
                    self.ui.mark_startup_ready("tts")
                    self.ui.set_startup_status("● All systems ready.")
                    self.ui.hide_startup_panel()
                    self._tts_ready.set()
                    return
                try:
                    self.ui.write_log(f"SYS: Loading {tts_engine.upper()} TTS…")
                    if tts_engine == "kokoro":
                        self.ui.write_log("SYS: Kokoro — loading model + compiling JIT…")
                    from core.tts import create_tts_player
                    self._tts = create_tts_player(self._config)
                    self._tts_ready.set()
                    self.ui.write_log("SYS: TTS ready.")
                    self.ui.mark_startup_ready("tts")
                    self.ui.set_startup_status("● All systems ready.")
                    self.ui.hide_startup_panel()
                    self.speak("Aether fully online.")
                except Exception as e:
                    import traceback as _tb; _tb.print_exc()
                    self.ui.write_log(f"ERR: TTS — {e}")
                    if self._tts_fallback_to_edge(tts_engine, self._config):
                        self.ui.mark_startup_ready("tts")
                        self.ui.set_startup_status("● All systems ready.")
                        self.ui.hide_startup_panel()
                        self.speak("Aether online, using the fallback voice.")
                        return
                    self.ui.mark_startup_ready("tts", error=True)
                    self._tts_ready.set()

            def _do_new_subsystems():
                self._init_new_subsystems()

            self.ui.write_log("SYS: Loading systems in parallel…")
            threading.Thread(target=_do_warmup,          daemon=True).start()
            threading.Thread(target=_do_stt,             daemon=True).start()
            threading.Thread(target=_do_tts,             daemon=True).start()
            threading.Thread(target=_do_new_subsystems,  daemon=True).start()

            _warmup_done.wait(timeout=60)
            _stt_done.wait(timeout=60)

            self.ui.write_log("SYS: Aether online.")
            self.ui.set_state("LISTENING")
            self.ui.set_startup_status("● Aether online · Voice loading in background…")

            threading.Thread(target=self._tts_worker,        daemon=True).start()
            threading.Thread(target=self._text_command_loop,  daemon=True).start()
            threading.Thread(target=self._dashboard_command_loop, daemon=True).start()
            threading.Thread(target=self._phone_audio_stt_loop,   daemon=True).start()
            threading.Thread(target=self._proactive_loop,          daemon=True).start()
            threading.Thread(target=self._system_monitor_loop,     daemon=True).start()

            _stdin_replay = HEADLESS and _os.environ.get(
                "AETHER_HEADLESS_STDIN", "").strip().lower() in ("1", "true", "yes")
            if _stdin_replay:
                # Scripted replay: the open mic only adds noise (music/room
                # sound transcribed as prompts, barge-in interrupting replies).
                self.ui.write_log("SYS: Mic disabled (headless stdin replay).")
                threading.Event().wait()   # the stdin driver ends the process
            elif _voice_off:
                self.ui.write_log("SYS: Headless server idle - drive me via the dashboard.")
                threading.Event().wait()
            elif stt_engine == "vosk":
                self._listen_vosk()
            else:
                self._listen_whisper()

        except Exception as e:
            self.ui.write_log(f"ERR: Init failed — {e}")
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> None:
    def _preload_torch():
        try:
            import torch  # noqa
        except Exception:
            pass
    threading.Thread(target=_preload_torch, daemon=True).start()

    def _refresh_maintenance_snapshot():
        # Regenerates PROJECT_STATE.md / HEALTH_REPORT.md / ROADMAP.md / HANDOFF.md
        # so they never go stale like hand-written snapshot docs do, then
        # auto-commits + pushes them to origin (certifiedclaw/aether) so the
        # repo on GitHub reflects current state too.
        #
        # Safe by design for "every launch": write_if_changed() ignores the
        # volatile "Generated: ..." timestamp lines, so a launch where
        # nothing substantive changed produces no diff -> no commit -> no
        # push. Uses your own local git credentials; if you're not
        # authenticated for that remote it just logs a failure and moves on.
        #
        # DEPENDENCY_REPORT.md is NOT refreshed here (needs internet, slower,
        # and less useful to check on every single launch) — run
        # `python -m tools.maintenance.dep_watcher` for that separately.
        #
        # Env vars:
        #   AETHER_SKIP_MAINTENANCE=1  disables this entirely
        #   AETHER_NO_AUTOPUSH=1        still refreshes reports, commits locally, skips push
        if _os.environ.get("AETHER_SKIP_MAINTENANCE"):
            return
        try:
            import argparse as _argparse
            from tools.maintenance import project_state, health_check, roadmap, handoff, publish
            project_state.main()
            health_check.main()
            roadmap.cmd_render(_argparse.Namespace())
            handoff.cmd_render(_argparse.Namespace())
            ok, status = publish.commit_and_push(push=not _os.environ.get("AETHER_NO_AUTOPUSH"))
            print(f"[Aether] maintenance publish: {status}")
        except Exception as e:
            print(f"[Aether] maintenance snapshot skipped: {e}")
    threading.Thread(target=_refresh_maintenance_snapshot, daemon=True).start()

    # Ensure runtime directories exist
    from core.data_home import data_dir
    for d in [data_dir(), data_dir() / "screenshots", data_dir() / "rag_index", Path("logs"), Path("plugins")]:
        d.mkdir(parents=True, exist_ok=True)

    ui = AetherUI("face.png")

    def runner():
        ui.wait_for_api_key()

        ui.write_log("SYS: Checking dependencies…")
        cfg = _load_config()
        _install_done = threading.Event()

        def _do_install():
            try:
                from core.installer import install_for_config
                install_for_config(cfg, log=ui.write_log)
            except Exception as e:
                ui.write_log(f"ERR: Dependency install — {e}")
            finally:
                _install_done.set()

        threading.Thread(target=_do_install, daemon=True).start()
        _install_done.wait()

        aether = AetherLocal(ui)

        # ── Validate config before any subsystem loads ────────────
        if _V4_OK:
            try:
                val = validate_config()
                if val:
                    for issue in val.issues:
                        prefix = {"error": "ERR", "warning": "WRN", "info": "SYS"}.get(issue.level, "SYS")
                        ui.write_log(f"{prefix}: [Config] {issue.key} — {issue.message}")
                    if not val.ok:
                        ui.write_log("ERR: Config has errors — some features may not work. See above.")
            except Exception as _val_err:
                ui.write_log(f"WRN: Config validation failed — {_val_err}")

        # ── Headless scripted testing ────────────────────────────────
        # Set AETHER_HEADLESS=1 AETHER_HEADLESS_STDIN=1 and pipe a file of
        # test messages, one per line, into stdin -- e.g.:
        #   set AETHER_HEADLESS=1
        #   set AETHER_HEADLESS_STDIN=1
        #   python main.py < tests\live_test_script.txt
        # Runs each line through the real pipeline (real Ollama/Gemini
        # calls, real tools, real memory) with no GUI/TTS overhead and
        # none of the Qt shutdown-race risk a windowed run has, then
        # exits automatically once stdin closes. This was referenced in
        # headless_ui.py's module docstring but never actually
        # implemented -- there was nothing reading stdin at all.
        #
        # Waits for each line to finish via live_report's turn counter
        # rather than just checking the queue is empty, since the queue
        # item is removed as soon as it's picked up for processing, well
        # before the actual LLM/tool work behind it is done -- checking
        # queue emptiness alone would race far ahead of real completion.
        # Caveat: built-in slash commands (/status etc.) don't go through
        # the normal turn pipeline and so never bump the counter -- those
        # lines stall for the full timeout below before the script
        # continues. Fine for a batch of ordinary conversational test
        # messages; avoid mixing in slash commands if you want a fast run.
        if HEADLESS and _os.environ.get("AETHER_HEADLESS_STDIN", "").strip().lower() in ("1", "true", "yes"):
            def _stdin_test_driver():
                from core.live_report import turn_count
                timeout_s = float(_os.environ.get("AETHER_HEADLESS_STDIN_TIMEOUT", "30"))
                print("[Aether] Headless stdin mode — reading test messages from stdin, one per line.")
                n = 0
                for raw_line in sys.stdin:
                    line = raw_line.strip()
                    if not line:
                        continue
                    n += 1
                    before = turn_count()
                    print(f"[Aether] >>> ({n}) {line}")
                    aether._on_text_command(line)
                    deadline = time.monotonic() + timeout_s
                    while turn_count() == before and time.monotonic() < deadline:
                        time.sleep(0.1)
                    if turn_count() == before:
                        print(f"[Aether] >>> ({n}) timed out after {timeout_s}s "
                              f"— continuing (see caveat about slash commands above)")
                print(f"[Aether] stdin closed — {n} line(s) sent. "
                      f"See logs/live_report.md for results.")
                time.sleep(1)  # let any final live_report write land on disk
                _os._exit(0)
            threading.Thread(target=_stdin_test_driver, daemon=True).start()

        try:
            aether.run()
        except KeyboardInterrupt:
            print("\n[Aether] Shutting down…")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
