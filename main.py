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

# ── Bootstrap: auto-install base UI packages before anything else ──────────
import importlib.util as _ilu
import subprocess      as _sp
import sys             as _sys

_BASE_PKGS = [
    ("PyQt6",       "PyQt6"),
    ("psutil",      "psutil"),
    ("numpy",       "numpy"),
    ("sounddevice", "sounddevice"),
    ("PIL",         "pillow"),
    ("requests",    "requests"),
]

def _bootstrap() -> None:
    need = [pkg for mod, pkg in _BASE_PKGS if _ilu.find_spec(mod) is None]
    if not need:
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

import numpy as np
import sounddevice as sd

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
from core.llm_client import call_llm, call_llm_stream, get_llm_settings, get_context_window

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

# ── NEW: Aether-sourced modules ────────────────────────────────────────────
from memory.semantic_memory    import MemoryEngine
from memory.rag                import RAGEngine
from core.smart_context        import compress_if_needed
from core.plugins              import PluginSystem
from core.plugin_watcher       import start_plugin_watcher
from core.task_scheduler       import TaskScheduler
from core.hotkey_manager       import HotkeyManager
from core.safety               import get_safety
from core.mcp_bridge           import MCPBridge
from memory.config_manager     import get_mark_config

# ── NEW v2: advanced modules ───────────────────────────────────────────────
from core.observability        import get_obs
from core.goal_state           import GoalState, GOAL_TOOL_DECLARATIONS
from memory.hybrid_memory      import HybridMemory
try:
    from loop_guard   import LoopGuard
    from evaluator    import Evaluator
    from file_watcher import FileWatcher, start_file_watcher
    _ADVANCED_OK = True
except ImportError:
    _ADVANCED_OK = False

# ── NEW v4: crash logger, self-test, memory tools, config validator ────────
try:
    from crash_logger  import log_crash, get_recent_crashes
    from self_test     import run_self_test, run_self_test_async
    from memory_tools  import backup_memory, export_memory, import_memory, prune_memory, memory_stats
    from config_validator import validate_config
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

# ── NEW v3: integration modules (lazy — graceful if not installed) ─────────
try:
    from integrations.langchain_tools import langchain_tool, LANGCHAIN_TOOL_DECLARATION
    _LANGCHAIN_OK = True
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
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

SAMPLE_RATE_IN = 16_000
BLOCK_SIZE     = 1_024
CHANNELS       = 1

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

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens or launches any application, website, or program on the computer. "
            "ALWAYS use this when the user says: open, launch, start, run, pull up, "
            "or 'open X real quick'. Examples: 'open WhatsApp', 'open Chrome', "
            "'launch Spotify', 'open calculator', 'pull up WhatsApp'. "
            "Do NOT use send_message just because the app is a messaging app — "
            "if the user only says to open it, call open_app."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {"type": "STRING", "description": "Name of the application or website to open"}
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {"city": {"type": "STRING", "description": "City name"}},
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": (
            "Sends a message to a specific person via WhatsApp, Telegram, or similar. "
            "ONLY use this when the user explicitly provides BOTH a recipient AND message content."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The exact message text to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": "Controls YouTube: play, summarize, get info, trending.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "play | summarize | get_info | trending"},
                "query":  {"type": "STRING", "description": "Search query for play action"},
                "save":   {"type": "BOOLEAN", "description": "Save summary to Notepad"},
                "region": {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":    {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' or 'camera'. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform"},
                "description": {"type": "STRING", "description": "Natural language description"},
                "value":       {"type": "STRING", "description": "Optional value"}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": "Controls any web browser: navigation, clicking, forms, screenshots.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | get_url | press | new_tab | close_tab | screenshot | back | forward | reload | switch | list_browsers | close | close_all"},
                "browser":     {"type": "STRING", "description": "chrome | edge | firefox | opera | operagx | brave | vivaldi | safari"},
                "url":         {"type": "STRING", "description": "URL for go_to / new_tab action"},
                "query":       {"type": "STRING", "description": "Search query"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description"},
                "direction":   {"type": "STRING", "description": "up | down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels"},
                "key":         {"type": "STRING", "description": "Key name for press"},
                "path":        {"type": "STRING", "description": "Save path for screenshot"},
                "incognito":   {"type": "BOOLEAN", "description": "Open in private/incognito mode"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto"},
                "description": {"type": "STRING", "description": "What the code should do"},
                "language":    {"type": "STRING", "description": "Programming language"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": "Steam or Epic Games: install, update, list, download status.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both"},
                "game_name": {"type": "STRING",  "description": "Game name"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when done"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
        "name": "shutdown_aether",
        "description": (
            "Shuts down the assistant completely. "
            "Call this when the user expresses intent to end the conversation, "
            "close the assistant, say goodbye, or stop."
        ),
        "parameters": {"type": "OBJECT", "properties": {}}
    },
    {
        "name": "file_processor",
        "description": (
            "Processes any file that the user has uploaded or dropped onto the interface. "
            "Supports: images, PDFs, Word docs, CSV/Excel, JSON, code files, audio, video, archives."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file_path":   {"type": "STRING",  "description": "Full path to the uploaded file"},
                "action":      {"type": "STRING",  "description": "What to do with the file"},
                "instruction": {"type": "STRING",  "description": "Free-form instruction"},
            },
            "required": []
        }
    },
    {
        "name": "save_memory",
        "description": (
            "Save a personal fact about the user to permanent long-term memory. "
            "MANDATORY: call this IMMEDIATELY whenever the user states or corrects: "
            "their name, age, city, job, school, language, nationality, a preference, a goal, or a relationship. "
            "Call SILENTLY alongside your verbal reply — never announce that you are saving."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity (name/age/city/job/school/nationality) | "
                        "preferences (likes/dislikes/habits) | "
                        "projects (active work/goals) | "
                        "relationships (people in their life) | "
                        "wishes (future plans/wants) | "
                        "notes (anything else)"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key, e.g. 'name', 'age', 'favorite_color'"},
                "value": {"type": "STRING", "description": "Concise value in English"},
            },
            "required": ["category", "key", "value"]
        }
    },
    # ── NEW tools from Aether v4.2 ─────────────────────────────────────────
    {
        "name": "osint_lookup",
        "description": (
            "OSINT investigation tools: WHOIS lookup, DNS records, subdomain enumeration, "
            "email breach check, port scan, Google dork search, IP geolocation. "
            "Use for: 'check if email was breached', 'who owns domain X', 'open ports on Y'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "whois | dns | subdomains | breach_check | port_scan | dork | geoip"},
                "target": {"type": "STRING", "description": "Domain, IP, email, or search query"},
                "ports":  {"type": "STRING", "description": "Port range for port_scan, e.g. '1-1000'"},
            },
            "required": ["action", "target"]
        }
    },
    {
        "name": "pdf_tool",
        "description": (
            "Work with PDF files: summarize, extract text, extract tables, "
            "search within a PDF, answer questions from a PDF."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING", "description": "summarize | extract_text | extract_tables | search | qa"},
                "file_path": {"type": "STRING", "description": "Path to the PDF file"},
                "query":     {"type": "STRING", "description": "Question or search term"},
                "pages":     {"type": "STRING", "description": "Page range, e.g. '1-5'"},
            },
            "required": ["action", "file_path"]
        }
    },
    {
        "name": "rag_query",
        "description": (
            "Query your locally indexed documents (Obsidian vault, PDFs, Markdown notes). "
            "Use for: 'search my notes about X', 'what does my doc say about Y', "
            "'index this folder', 'add this file to my knowledge base'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "query | index_file | index_directory | stats"},
                "query":   {"type": "STRING", "description": "Question to ask your documents"},
                "path":    {"type": "STRING", "description": "File or directory path to index"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "diagnostics",
        "description": (
            "Run a full system diagnostic report. Shows status of Ollama, Python, "
            "GPU, installed packages, and browser CDP connection. "
            "Trigger when user says: 'status', 'system check', 'diagnostics', 'health check'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "memory_tool",
        "description": (
            "Manage Aether's memory: backup, export, import, prune duplicates, or view stats. "
            "Trigger when user says: 'backup memory', 'export memories', 'clean up memory', "
            "'prune memory', 'memory stats', 'how much do you remember', 'restore memory'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":  {"type": "STRING", "description": "backup | export | import | prune | stats"},
                "path":    {"type": "STRING", "description": "File path for export/import"},
                "tag":     {"type": "STRING", "description": "Optional label for backup"},
                "dry_run": {"type": "BOOLEAN", "description": "If true, show what prune would delete without deleting"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "crash_report",
        "description": (
            "Show recent crash logs and error reports. "
            "Trigger when user says: 'show crashes', 'recent errors', 'crash report', 'what went wrong'."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "n": {"type": "INTEGER", "description": "Number of recent crashes to show (default 5)"},
            }
        }
    },
    {
        "name": "extra_tool",
        "description": (
            "Extended utility toolkit. Use for: Wikipedia lookups, word definitions, battery status, "
            "notes (save/read/list/delete), reminders, timers, password generation, unit conversion, "
            "zip/unzip files, file hashing, file diff, find large files, word count, batch rename, "
            "list/kill processes, list/focus windows, type text, set volume, run Python snippets, "
            "calculate math expressions, format/lint code, ping hosts, check ports, wifi list, "
            "local IP, play media files, generate QR codes, screenshot region, translate text, "
            "read/write clipboard, get current datetime, random facts."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "wikipedia|define|battery|save_note|list_notes|read_note|delete_note|"
                        "remind_me|list_reminders|cancel_reminder|timer|password|convert_units|"
                        "zip|unzip|hash_file|diff|large_files|word_count|batch_rename|"
                        "list_processes|kill_process|list_windows|focus_window|auto_type|set_volume|"
                        "run_python|calculate|format_json|lint|ping|check_port|wifi_list|local_ip|"
                        "play_file|qr_code|screenshot_region|translate|clipboard_read|clipboard_write|"
                        "datetime|random_fact"
                    )
                },
                "query":        {"type": "STRING",  "description": "Query / topic / text for the action"},
                "text":         {"type": "STRING",  "description": "Text input (notes, typing, clipboard, translate)"},
                "path":         {"type": "STRING",  "description": "File or folder path"},
                "title":        {"type": "STRING",  "description": "Note title"},
                "content":      {"type": "STRING",  "description": "Note content"},
                "message":      {"type": "STRING",  "description": "Reminder message"},
                "minutes":      {"type": "NUMBER",  "description": "Minutes until reminder fires"},
                "seconds":      {"type": "INTEGER", "description": "Timer duration in seconds"},
                "level":        {"type": "INTEGER", "description": "Volume level 0-100"},
                "value":        {"type": "NUMBER",  "description": "Value to convert"},
                "from_unit":    {"type": "STRING",  "description": "Source unit"},
                "to_unit":      {"type": "STRING",  "description": "Target unit"},
                "expression":   {"type": "STRING",  "description": "Math expression to evaluate"},
                "host":         {"type": "STRING",  "description": "Hostname or IP"},
                "port":         {"type": "INTEGER", "description": "Port number"},
                "target_lang":  {"type": "STRING",  "description": "Language code for translation (e.g. 'es', 'de')"},
                "timezone":     {"type": "STRING",  "description": "Timezone for datetime (e.g. 'Europe/Istanbul')"},
                "code":         {"type": "STRING",  "description": "Python code to run"},
            },
            "required": ["action"]
        }
    },
]


# ---------------------------------------------------------------------------
# Convert Gemini-style declarations to OpenAI/Ollama format
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "OBJECT": "object", "STRING": "string", "ARRAY": "array",
    "INTEGER": "integer", "BOOLEAN": "boolean", "NUMBER": "number",
}


def _convert_type(t: str) -> str:
    return _TYPE_MAP.get(t, t.lower()) if isinstance(t, str) else t


def _convert_props(props: dict) -> dict:
    out = {}
    for k, v in props.items():
        nv = dict(v)
        if "type" in nv:
            nv["type"] = _convert_type(nv["type"])
        if "items" in nv and isinstance(nv["items"], dict):
            nv["items"] = {"type": _convert_type(nv["items"].get("type", "string"))}
        out[k] = nv
    return out


def _to_ollama_tools(decls: list) -> list:
    tools = []
    for d in decls:
        params = d.get("parameters", {})
        new_params: dict = {
            "type":       "object",
            "properties": _convert_props(params.get("properties", {})),
        }
        req = params.get("required")
        if req:
            new_params["required"] = req
        tools.append({
            "type": "function",
            "function": {
                "name":        d["name"],
                "description": d["description"],
                "parameters":  new_params,
            },
        })
    return tools


_INTEGRATION_DECLS = [
    d for d in [LANGCHAIN_TOOL_DECLARATION, OI_TOOL_DECLARATION, LLAMA_INDEX_TOOL_DECLARATION]
    if d is not None
]
OLLAMA_TOOLS = _to_ollama_tools(TOOL_DECLARATIONS + GOAL_TOOL_DECLARATIONS + _INTEGRATION_DECLS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        with open(API_CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_system_prompt_cache: str | None = None
_system_prompt_mtime: float | None = None


def _load_system_prompt() -> str:
    """Reads the system prompt file, cached and invalidated on file change.

    _build_system_prompt() calls this on every single message, so caching
    avoids a disk read per turn.
    """
    global _system_prompt_cache, _system_prompt_mtime
    fallback = (
        "You are Aether, Tony Stark's AI assistant. "
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


# ---------------------------------------------------------------------------
# Voice Activity Detection
# ---------------------------------------------------------------------------

class _VADBuffer:
    def __init__(
        self,
        sample_rate:    int   = 16_000,
        silence_sec:    float = 0.7,
        speech_thresh:  float = 0.008,
        silence_thresh: float = 0.004,
        min_speech_sec: float = 0.3,
        max_speech_sec: float = 30.0,
    ):
        self._sr            = sample_rate
        self._sil_n         = int(silence_sec * sample_rate)
        self._speech_thresh = speech_thresh
        self._sil_thresh    = silence_thresh
        self._min_n         = int(min_speech_sec * sample_rate)
        self._max_n         = int(max_speech_sec * sample_rate)
        self._buf:          list[np.ndarray] = []
        self._in_spch       = False
        self._sil_cnt       = 0

    def process(self, chunk: np.ndarray) -> np.ndarray | None:
        rms     = float(np.sqrt(np.mean(chunk ** 2)))
        total_n = sum(len(c) for c in self._buf)
        if rms > self._speech_thresh:
            self._in_spch = True
            self._sil_cnt = 0
            self._buf.append(chunk.copy())
        elif self._in_spch:
            self._buf.append(chunk.copy())
            if rms < self._sil_thresh:
                self._sil_cnt += len(chunk)
            if self._sil_cnt >= self._sil_n or total_n >= self._max_n:
                audio         = np.concatenate(self._buf)
                self._buf     = []
                self._in_spch = False
                self._sil_cnt = 0
                if len(audio) >= self._min_n:
                    return audio
        return None


# ---------------------------------------------------------------------------
# AetherLocal — main assistant class
# ---------------------------------------------------------------------------

class AetherLocal:

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
        self._conversation:   list[dict]  = []

        # ── NEW: Aether-sourced subsystems ────────────────────────────────
        self._semantic_mem: MemoryEngine | None = None
        self._rag:          RAGEngine    | None = None
        self._plugins:      PluginSystem | None = None
        self._scheduler:    TaskScheduler | None = None
        self._hotkey:       HotkeyManager | None = None
        self._mcp:          MCPBridge    | None = None

        # ── NEW v2: advanced systems ───────────────────────────────────────
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

        # ── NEW v4 (ported from Mark-XLVIII): dashboard, proactive, monitor ─
        self._dashboard                         = None   # DashboardServer | None
        self._proactive                         = None   # ProactiveEngine | None
        self._sys_monitor                       = None   # SystemMonitor   | None
        self._last_user_speech: float           = time.monotonic()

        self.ui.on_text_command = self._on_text_command

    # ------------------------------------------------------------------
    # System prompt — now injects semantic memory context
    # ------------------------------------------------------------------

    def _build_system_prompt(self, user_text: str = "") -> str:
        sys_p   = _load_system_prompt()
        memory  = load_memory()
        mem_str = format_memory_for_prompt(memory)
        now     = datetime.now()
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {now.strftime('%A, %B %d, %Y — %I:%M %p')}\n"
            f"Use this to calculate exact times for reminders."
        )
        parts = [sys_p]
        if mem_str:
            parts.append(mem_str)

        # ── Inject memory context — only from the active engine ───────
        mem_engine = self._hybrid_mem or self._semantic_mem
        if mem_engine and user_text:
            try:
                enriched = mem_engine.inject_context(user_text, "")
                if enriched:
                    parts.append(enriched)
            except Exception:
                pass

        # ── NEW v2: inject active goals ───────────────────────────────
        if self._goals:
            goal_ctx = self._goals.format_for_prompt()
            if goal_ctx:
                parts.append(goal_ctx)

        # ── NEW: inject MCP tool list so the LLM knows what's available
        if self._mcp:
            mcp_tools = self._mcp.list_all_tools()
            if mcp_tools:
                parts.append("[CONNECTED MCP TOOLS]\n" + "\n".join(f"  {t}" for t in mcp_tools))

        # ── NEW: inject plugin tool list ──────────────────────────────
        if self._plugins:
            plugin_tools = self._plugins.list_tools()
            if plugin_tools:
                parts.append("[LOADED PLUGINS]\n" + "\n".join(f"  plugin.{t}" for t in plugin_tools))

        # ── NEW v3: inject active integration list ─────────────────────
        active_integrations = []
        if _LANGCHAIN_OK:
            active_integrations.append(
                "  langchain(action=web_search|wikipedia|summarize|research|load_doc)"
            )
        if _OI_OK:
            active_integrations.append(
                "  open_interpreter(action=run|chat|reset|status) — writes+runs real code"
            )
        if _LLAMA_OK:
            active_integrations.append(
                "  llama_index(action=index|query|summarize|collections) — production RAG"
            )
        if active_integrations:
            parts.append("[ACTIVE INTEGRATIONS]\n" + "\n".join(active_integrations))

        parts.append(time_ctx)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Message stats — pushes per-response metrics to the UI stats panel
    # ------------------------------------------------------------------

    def _publish_message_stats(self, stats: dict, elapsed: float) -> None:
        """Sends model/token/timing info from the last LLM round to the
        Message Stats panel. Best-effort — any missing field is shown as
        '—' by the UI rather than a misleading 0."""
        try:
            model         = stats.get("model")
            prompt_tokens = stats.get("prompt_tokens")
            output_tokens = stats.get("output_tokens")
            eval_duration = stats.get("eval_duration")  # ns, model-generation time only

            tokens_per_sec = None
            if output_tokens:
                if eval_duration:
                    secs = eval_duration / 1e9
                    if secs > 0:
                        tokens_per_sec = output_tokens / secs
                elif elapsed > 0:
                    tokens_per_sec = output_tokens / elapsed

            try:
                context_window = get_context_window(model)
            except Exception:
                context_window = None

            self.ui.update_message_stats({
                "model":          model,
                "prompt_tokens":  prompt_tokens,
                "output_tokens":  output_tokens,
                "elapsed":        elapsed,
                "tokens_per_sec": tokens_per_sec,
                "context_window": context_window,
            })
        except Exception:
            pass  # stats panel is best-effort, never break the response

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
                print(f"[TTS] speak error: {e}")
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

    def _interrupt_speaking(self) -> None:
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
        self.ui.write_log("SYS: [barge-in] speech interrupted")

    def speak(self, text: str) -> None:
        if not text or not self._tts:
            return
        with self._speaking_lock:
            self._speaking = True
        self._tts_queue.put(text)

    def speak_error(self, tool_name: str, error) -> None:
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"{tool_name} encountered an error.")

    # ------------------------------------------------------------------
    # Reconfigure
    # ------------------------------------------------------------------

    def reconfigure(self, new_config: dict) -> None:
        threading.Thread(
            target=self._do_reconfigure, args=(new_config,), daemon=True
        ).start()

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

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _execute_tool(self, name: str, args: dict) -> str:
        print(f"[Aether] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")

        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return "__SILENT__"

        result = "Done."
        try:
            if name == "open_app":
                r = open_app(parameters=args, response=None, player=self.ui)
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = weather_action(parameters=args, player=self.ui)
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = browser_control(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "file_controller":
                r = file_controller(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "send_message":
                r = send_message(parameters=args, response=None, player=self.ui, session_memory=None)
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = reminder(parameters=args, response=None, player=self.ui)
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = youtube_video(parameters=args, response=None, player=self.ui)
                result = r or "Done."

            elif name == "screen_process":
                r = screen_process(parameters=args, response=None, player=self.ui, session_memory=None)
                result = r if isinstance(r, str) and r else "Screen analyzed."

            elif name == "computer_settings":
                r = computer_settings(parameters=args, response=None, player=self.ui)
                result = r or "Done."

            elif name == "desktop_control":
                r = desktop_control(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "code_helper":
                r = code_helper(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Done."

            elif name == "dev_agent":
                r = dev_agent(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Done."

            elif name == "agent_task":
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
                result = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = web_search_action(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = file_processor(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Done."

            elif name == "computer_control":
                r = computer_control(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "game_updater":
                r = game_updater(parameters=args, player=self.ui, speak=self.speak)
                result = r or "Done."

            elif name == "flight_finder":
                r = flight_finder(parameters=args, player=self.ui)
                result = r or "Done."

            elif name == "shutdown_aether":
                self.ui.write_log("SYS: Shutdown requested.")
                def _shutdown():
                    import time, os
                    self.speak("Goodbye.")
                    time.sleep(2.5)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()
                return "Shutting down."

            # ── NEW tools ─────────────────────────────────────────────────

            elif name == "osint_lookup":
                try:
                    from actions.osint_tools import run_osint
                    r = run_osint(args.get("action", ""), args.get("target", ""), args)
                    result = r or "OSINT complete."
                except ImportError:
                    result = "OSINT tools not available. Install: pip install python-whois dnspython"
                except Exception as e:
                    result = f"OSINT error: {e}"

            elif name == "pdf_tool":
                try:
                    from actions.pdf_tools import run_pdf_tool
                    r = run_pdf_tool(
                        action=args.get("action", "summarize"),
                        file_path=args.get("file_path", ""),
                        query=args.get("query", ""),
                        pages=args.get("pages"),
                    )
                    result = r or "PDF processed."
                except ImportError:
                    result = "PDF tools not available. Install: pip install pymupdf"
                except Exception as e:
                    result = f"PDF tool error: {e}"

            elif name == "rag_query":
                action = args.get("action", "query")
                # Prefer LlamaIndex for query/index when available
                if _LLAMA_OK and action in ("query", "index_file", "index_directory"):
                    if action == "query":
                        result = llama_index_tool("query", {
                            "query": args.get("query", ""),
                            "collection": args.get("collection", "aether"),
                            "mode": args.get("mode", "auto"),
                        })
                    elif action in ("index_file", "index_directory"):
                        path = args.get("path", args.get("file_path", ""))
                        result = llama_index_tool("index", {
                            "path": path,
                            "collection": args.get("collection", "aether"),
                        })
                elif self._rag is None:
                    result = "RAG engine not initialized."
                else:
                    if action == "query":
                        result = self._rag.query(args.get("query", ""))
                    elif action == "index_file":
                        result = self._rag.index_file(args.get("path", ""))
                    elif action == "index_directory":
                        result = self._rag.index_directory(args.get("path", ""))
                    elif action == "stats":
                        result = str(self._rag.stats())
                    else:
                        result = f"Unknown RAG action: {action}"

            elif name == "diagnostics":
                try:
                    from core.diagnostics import run_all, format_report
                    report = format_report(run_all())
                    self.ui.write_log(report)
                    result = report
                except Exception as e:
                    result = f"Diagnostics error: {e}"

            # ── NEW v2: goal manager ───────────────────────────────────
            elif name == "goal_manager":
                if self._goals:
                    action = args.get("action", "goal_list")
                    result = self._goals.handle_tool(action, args)
                else:
                    result = "Goal system not initialised."

            # Convenience aliases — LLM may call these directly per goal_state docstring
            elif name in ("goal_create", "goal_update", "goal_complete",
                          "goal_cancel", "goal_list", "goal_add_subtask"):
                if self._goals:
                    result = self._goals.handle_tool(name, args)
                else:
                    result = "Goal system not initialised."

            # ── NEW v2: observability report ───────────────────────────
            elif name in ("obs_report", "observability"):
                result = self._obs.report()

            # ── NEW v4: memory management tools ───────────────────────
            elif name == "memory_tool":
                action = args.get("action", "stats")
                if action == "backup":
                    result = backup_memory(tag=args.get("tag", ""))
                elif action == "export":
                    result = export_memory(output_path=args.get("path", ""))
                elif action == "import":
                    result = import_memory(input_path=args.get("path", ""))
                elif action == "prune":
                    result = prune_memory(dry_run=args.get("dry_run", False))
                elif action == "stats":
                    result = memory_stats()
                else:
                    result = f"Unknown memory_tool action '{action}'. Use: backup | export | import | prune | stats"

            # ── NEW v4: crash report ───────────────────────────────────
            elif name == "crash_report":
                result = get_recent_crashes(n=args.get("n", 5))

            # ── extra utility toolkit ──────────────────────────────────
            elif name == "extra_tool":
                from actions.extra_tools import run_extra_tool
                result = run_extra_tool(args.get("action", ""), args)

            # ── NEW v3: LangChain integration ──────────────────────────
            elif name == "langchain":
                if _LANGCHAIN_OK:
                    result = langchain_tool(
                        action=args.get("action", ""),
                        args=args,
                    )
                else:
                    result = (
                        "LangChain not installed.\n"
                        "Fix: pip install langchain langchain-community "
                        "langchain-ollama duckduckgo-search wikipedia"
                    )

            # ── NEW v3: Open Interpreter ───────────────────────────────
            elif name == "open_interpreter":
                if _OI_OK:
                    result = oi_tool(
                        action=args.get("action", "run"),
                        args=args,
                    )
                else:
                    result = (
                        "Open Interpreter not installed.\n"
                        "Fix: pip install open-interpreter"
                    )

            # ── NEW v3: LlamaIndex RAG ─────────────────────────────────
            elif name == "llama_index":
                if _LLAMA_OK:
                    result = llama_index_tool(
                        action=args.get("action", "query"),
                        args=args,
                    )
                else:
                    result = (
                        "LlamaIndex not installed.\n"
                        "Fix: pip install llama-index llama-index-llms-ollama "
                        "llama-index-embeddings-ollama"
                    )

            else:
                # ── Try plugin tools ───────────────────────────────────
                if self._plugins:
                    # Support both "plugin.tool_name" and bare "tool_name"
                    tool_key = name.replace("plugin.", "")
                    if tool_key in (self._plugins.list_tools() or []):
                        result = self._plugins.execute_tool(tool_key, **args)
                        if not self.ui.muted:
                            self.ui.set_state("LISTENING")
                        return result
                # ── Try MCP tools ──────────────────────────────────────
                if self._mcp and name == "mcp.call":
                    result = self._mcp.call(
                        server=args.get("server", ""),
                        tool=args.get("tool", ""),
                        args=args.get("args", {}),
                    )
                else:
                    result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            log_crash(e, tool=name, extra={"args": args})
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[Aether] 📤 {name} → {str(result)[:80]}")
        return result

    # ------------------------------------------------------------------
    # LLM processing loop — with smart context compression
    # ------------------------------------------------------------------

    def _process_message(self, user_text: str) -> None:
        self._last_user_speech = time.monotonic()
        try:
            self._process_message_inner(user_text)
        except Exception as e:
            log_crash(e, user_message=user_text)
            traceback.print_exc()
            error_msg = f"A critical error occurred: {e}"
            self.ui.write_log(f"ERR: {error_msg}")
            try:
                self.speak("I encountered a critical error. Please check the logs.")
            except Exception:
                pass
            if not self.ui.muted:
                self.ui.set_state("LISTENING")

    def _process_message_inner(self, user_text: str) -> None:
        # ── NEW v2: reset loop guard for new request ──────────────────
        if self._loop_guard:
            self._loop_guard.reset()
        # Track message in observability
        self._obs.count("messages.received")
        # ── NEW: handle built-in chat commands ────────────────────────
        if user_text.strip().lower() in ("status", "aether status", "/status"):
            self._execute_tool("diagnostics", {})
            return
        if user_text.strip().lower() == "/clear":
            self._conversation.clear()
            if self._semantic_mem:
                self._semantic_mem.clear_session()
            self.ui.write_log("SYS: Chat history cleared.")
            self.speak("History cleared.")
            return
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
            return
        if user_text.strip().lower() in ("/crashes", "show crashes", "crash report", "recent crashes"):
            report = get_recent_crashes(5)
            self.ui.write_log(report)
            return
        if user_text.strip().lower() in ("/backup", "backup memory"):
            result = backup_memory()
            self.ui.write_log(result)
            self.speak("Memory backed up.")
            return
        if user_text.strip().lower() in ("/memstats", "memory stats"):
            result = memory_stats()
            self.ui.write_log(result)
            return
        if user_text.strip().lower() in ("/prune", "prune memory"):
            result = prune_memory()
            self.ui.write_log(result)
            return
        if user_text.strip().lower().startswith("/export"):
            parts = user_text.strip().split(maxsplit=1)
            path  = parts[1].strip() if len(parts) > 1 else ""
            result = export_memory(output_path=path)
            self.ui.write_log(result)
            self.speak("Memory exported.")
            return
        if user_text.strip().lower().startswith("/import"):
            parts = user_text.strip().split(maxsplit=1)
            path  = parts[1].strip() if len(parts) > 1 else ""
            if not path:
                self.ui.write_log("ERR: Usage — /import <path to export file>")
                self.speak("Please provide the path to the export file.")
                return
            result = import_memory(input_path=path)
            self.ui.write_log(result)
            self.speak("Memory imported.")
            return
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
            return

        self.ui.set_state("THINKING")
        self.ui.write_log(f"You: {user_text}")

        # ── Store to memory — only one engine writes, never both ───────
        # hybrid_mem supersedes semantic_mem; if both exist only hybrid writes.
        _active_mem = self._hybrid_mem or self._semantic_mem
        if _active_mem:
            try:
                _active_mem.add_message("user", user_text)
            except Exception:
                pass

        self._conversation.append({"role": "user", "content": user_text})

        MAX_HISTORY = int(self._config.get("memory.chat_history_limit", 12))
        if len(self._conversation) > MAX_HISTORY:
            trimmed = self._conversation[-MAX_HISTORY:]
            # Never let the trim start on a dangling "tool" message — it must
            # stay paired with the preceding assistant message that issued the
            # tool_calls, or the next LLM request has an invalid message
            # sequence (API error / stuck round).
            while trimmed and trimmed[0].get("role") == "tool":
                trimmed = trimmed[1:]
            self._conversation = trimmed

        messages = [
            {"role": "system", "content": self._build_system_prompt(user_text)}
        ] + list(self._conversation)

        # ── NEW: compress context to fit token window ─────────────────
        if _LANGCHAIN_OK:
            try:
                from integrations.langchain_tools import lc_compress_history
                messages = lc_compress_history(messages, max_tokens=3000)
            except Exception:
                messages = compress_if_needed(messages, token_budget=3000)
        else:
            messages = compress_if_needed(messages, token_budget=3000)

        # _NEEDS_LLM_ROUND is defined at module level

        MAX_TOOL_ROUNDS = 6
        final_assistant_reply = ""
        _llm_synthesized = False   # True only when the LLM produced the final reply
        _tool_results:  list[tuple[str, str]] = []  # FIX: must be defined before loop

        for _round in range(MAX_TOOL_ROUNDS):
            final_content    = ""
            final_tool_calls: list = []
            _streamed: list[str] = []
            _llm_stats: dict = {}

            _round_start = time.monotonic()
            try:
                for event in call_llm_stream(messages, OLLAMA_TOOLS):
                    if event["type"] == "sentence":
                        _streamed.append(event["text"])
                        self.speak(event["text"])
                    elif event["type"] == "done":
                        final_content    = event["content"]
                        final_tool_calls = event["tool_calls"]
                        _llm_stats       = event.get("stats", {})
            except RuntimeError as e:
                self.speak_error("LLM", e)
                if not self.ui.muted:
                    self.ui.set_state("LISTENING")
                return
            _round_elapsed = time.monotonic() - _round_start

            if not final_tool_calls:
                if _streamed:
                    assistant_msg = {"role": "assistant", "content": final_content}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    self.ui.write_log(f"Aether: {final_content}")
                    final_assistant_reply = final_content
                    _llm_synthesized = True
                    self._publish_message_stats(_llm_stats, _round_elapsed)
                elif final_content:
                    assistant_msg = {"role": "assistant", "content": final_content}
                    messages.append(assistant_msg)
                    self._conversation.append(assistant_msg)
                    self.ui.write_log(f"Aether: {final_content}")
                    self.speak(final_content)
                    final_assistant_reply = final_content
                    _llm_synthesized = True
                    self._publish_message_stats(_llm_stats, _round_elapsed)
                break

            assistant_msg = {
                "role":       "assistant",
                "content":    final_content or "",
                "tool_calls": final_tool_calls,
            }
            messages.append(assistant_msg)
            self._conversation.append(assistant_msg)

            _only_memory = all(
                tc.get("function", {}).get("name") == "save_memory"
                for tc in final_tool_calls
            )
            if _only_memory and final_content:
                for tc in final_tool_calls:
                    fn    = tc.get("function", {})
                    targs = fn.get("arguments", {})
                    if isinstance(targs, str):
                        try:
                            targs = json.loads(targs)
                        except Exception:
                            targs = {}
                    self._execute_tool("save_memory", targs)
                assistant_msg2 = {"role": "assistant", "content": final_content}
                messages.append(assistant_msg2)
                self._conversation.append(assistant_msg2)
                self.ui.write_log(f"Aether: {final_content}")
                if not _streamed:
                    self.speak(final_content)
                final_assistant_reply = final_content
                break

            all_silent    = True
            _tool_results = []   # reset each tool round
            self.ui.set_state("PROCESSING")

            for tc in final_tool_calls:
                fn    = tc.get("function", {})
                tname = fn.get("name", "")
                targs = fn.get("arguments", {})
                if isinstance(targs, str):
                    try:
                        targs = json.loads(targs)
                    except Exception:
                        targs = {}

                tc_id = tc.get("id", "")
                _arg_hint = ""
                if isinstance(targs, dict) and targs:
                    _first_k = next(iter(targs))
                    _first_v = str(targs[_first_k])[:40]
                    _arg_hint = f" ({_first_k}={_first_v})"
                self.ui.write_log(f"SYS: ▶ {tname}{_arg_hint}")

                # ── NEW v2: loop guard — block if tool called too many times ──
                if self._loop_guard:
                    verdict = self._loop_guard.check(tname, targs)
                    if verdict.blocked:
                        result = self._loop_guard.inject_break_message(tname)
                        self.ui.write_log(f"WRN: LoopGuard blocked {tname}")
                        self._obs.count("loop_guard.blocked", tags={"tool": tname})
                    else:
                        with self._obs.span("tool_call", tool=tname) as span:
                            result = self._execute_tool(tname, targs)
                            span.set("result_len", len(str(result)))
                        self._loop_guard.record(tname, targs)
                        self._obs.count("tool.calls", tags={"tool": tname})
                else:
                    with self._obs.span("tool_call", tool=tname):
                        result = self._execute_tool(tname, targs)
                        self._obs.count("tool.calls", tags={"tool": tname})

                if result != "__SILENT__":
                    all_silent = False
                    _tool_results.append((tname, result))

                tool_msg: dict = {
                    "role":    "tool",
                    "content": "Done." if result == "__SILENT__" else str(result),
                }
                if tc_id:
                    tool_msg["tool_call_id"] = tc_id

                messages.append(tool_msg)
                self._conversation.append(tool_msg)

            if not self.ui.muted:
                self.ui.set_state("THINKING")

            if all_silent:
                _saved_name: str | None = None
                for _tc in final_tool_calls:
                    _fn = _tc.get("function", {})
                    if _fn.get("name") == "save_memory":
                        _a = _fn.get("arguments", {})
                        if isinstance(_a, str):
                            try:
                                _a = json.loads(_a)
                            except Exception:
                                _a = {}
                        if isinstance(_a, dict) and _a.get("key") == "name" and _a.get("value"):
                            _saved_name = str(_a["value"])
                            break
                _ack = f"Got it, {_saved_name}." if _saved_name else "Noted."
                _amsg = {"role": "assistant", "content": _ack}
                messages.append(_amsg)
                self._conversation.append(_amsg)
                self.ui.write_log(f"Aether: {_ack}")
                self.speak(_ack)
                final_assistant_reply = _ack
                break

            if _tool_results and not any(n in _NEEDS_LLM_ROUND for n, _ in _tool_results):
                _, _reply = _tool_results[-1]
                _amsg = {"role": "assistant", "content": _reply}
                messages.append(_amsg)
                self._conversation.append(_amsg)
                self.ui.write_log(f"Aether: {_reply}")
                self.speak(_reply)
                final_assistant_reply = _reply
                break

        # ── Store full exchange — only the active engine ───────────────
        if _active_mem and final_assistant_reply:
            try:
                _active_mem.store(user_text, final_assistant_reply)
            except Exception:
                pass

        # ── NEW v2: self-evaluate the response ────────────────────────
        # Only evaluate when the LLM produced the final reply; skip when we
        # used a raw tool result directly (score would be meaningless noise).
        if self._evaluator and final_assistant_reply and _tool_results and _llm_synthesized:
            try:
                # Fast heuristic check first — skip expensive LLM eval if
                # the response already looks like an obvious failure
                if not self._evaluator.quick_check(final_assistant_reply):
                    self._obs.count("eval.quick_fail")
                    self.ui.write_log("WRN: Evaluator — quick check flagged response as likely failure")
                else:
                    eval_result = self._evaluator.evaluate(
                        user_text, final_assistant_reply, _tool_results)
                    self._obs.count("eval.passed" if eval_result.passed else "eval.failed")
                    if not eval_result.passed:
                        self.ui.write_log(
                            f"WRN: Evaluator — {eval_result.reason} "
                            f"(score={eval_result.score:.2f})")
                        self._obs.log("eval_fail",
                                      request=user_text[:80],
                                      reason=eval_result.reason,
                                      score=eval_result.score)
            except Exception:
                pass

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

    # ------------------------------------------------------------------
    # STT loops
    # ------------------------------------------------------------------

    def _listen_whisper(self) -> None:
        vad = _VADBuffer()
        q: queue.Queue = queue.Queue(maxsize=200)

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                is_speaking = self._speaking
            if is_speaking and not self.ui.muted:
                # ── Barge-in detection: loud sustained audio interrupts TTS ──
                rms = float(np.sqrt(np.mean(indata.flatten() ** 2)))
                if rms > self._BARGE_IN_THRESHOLD:
                    self._barge_in_count += 1
                    if self._barge_in_count >= self._BARGE_IN_FRAMES:
                        self._barge_in_count = 0
                        self._interrupt_speaking()
                else:
                    self._barge_in_count = max(0, self._barge_in_count - 1)
            elif not self.ui.muted:
                self._barge_in_count = 0
                try:
                    q.put_nowait(indata.copy())
                except queue.Full:
                    pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=callback,
            ):
                self.ui.write_log("SYS: Mic active (Whisper STT).")
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                        audio = vad.process(chunk.flatten())
                        if audio is not None:
                            self.ui.set_state("THINKING")
                            text = self._stt.transcribe(audio)
                            if text.strip():
                                self._process_message(text)
                    except queue.Empty:
                        pass
        except Exception as e:
            log_crash(e, context="STT-Whisper")
            print(f"[STT-Whisper] Mic error: {e}")
            traceback.print_exc()

    def _listen_vosk(self) -> None:
        q: queue.Queue = queue.Queue(maxsize=200)

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                is_speaking = self._speaking
            if not is_speaking and not self.ui.muted:
                try:
                    q.put_nowait(indata.copy())
                except queue.Full:
                    pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="int16",
                blocksize=4096,
                callback=callback,
            ):
                self.ui.write_log("SYS: Mic active (Vosk STT).")
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                        text, is_final = self._stt.process_chunk(chunk.tobytes())
                        if is_final and text.strip():
                            self._process_message(text)
                    except queue.Empty:
                        pass
        except Exception as e:
            log_crash(e, context="STT-Vosk")
            print(f"[STT-Vosk] Mic error: {e}")
            traceback.print_exc()

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
                alert = self._sys_monitor.check()
                if alert:
                    self._process_message(alert)
            except Exception as e:
                self.ui.write_log(f"WRN: System monitor — {e}")

    def _text_command_loop(self) -> None:
        while True:
            try:
                text = self._text_queue.get(timeout=0.5)
                if text.strip():
                    self._process_message(text)
            except queue.Empty:
                pass

    # ------------------------------------------------------------------
    # Startup — initialise all new subsystems
    # ------------------------------------------------------------------

    def _init_new_subsystems(self) -> None:
        """Initialise Aether-sourced modules in a background thread."""
        cfg = get_mark_config()

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

        # ── NEW v2: Hybrid memory (BM25 + dense + reranker) ───────────────
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

        # ── NEW v2: Goal state manager ────────────────────────────────────
        try:
            self._goals = GoalState(cfg)
            self._goals.start()
            active = len(self._goals.list_active())
            self.ui.write_log(f"SYS: Goal manager ready ({active} active goals).")
        except Exception as e:
            self.ui.write_log(f"WRN: Goal manager — {e}")

        # ── NEW v2: Loop guard ────────────────────────────────────────────
        if _ADVANCED_OK:
            try:
                self._loop_guard = LoopGuard(max_repeats=2, window=6)
                self.ui.write_log("SYS: Loop guard active.")
            except Exception as e:
                self.ui.write_log(f"WRN: Loop guard — {e}")

            # ── NEW v2: Evaluator ─────────────────────────────────────────
            try:
                self._evaluator = Evaluator(enabled=True, threshold=0.6)
                self.ui.write_log("SYS: Self-evaluator active.")
            except Exception as e:
                self.ui.write_log(f"WRN: Evaluator — {e}")

            # ── NEW v2: File watcher → auto-index dropped files ───────────
            try:
                self._file_watcher = start_file_watcher(
                    self._rag, cfg, notify_cb=self.ui.write_log)
                if self._file_watcher:
                    self.ui.write_log("SYS: File watcher running.")
            except Exception as e:
                self.ui.write_log(f"WRN: File watcher — {e}")

        self.ui.write_log("SYS: All systems nominal.")

        # ── NEW v4 (ported from Mark-XLVIII): Web dashboard ───────────────
        try:
            from dashboard.server import start_dashboard_server
            self._dashboard = start_dashboard_server(
                wake_callback=lambda: self.ui.set_state("LISTENING"),
            )
            if self._dashboard:
                self.ui.write_log("SYS: Web dashboard — http://<this-pc-ip>:8000")
            else:
                self.ui.write_log(
                    "SYS: Dashboard not started — pip install fastapi "
                    "\"uvicorn[standard]\" cryptography python-multipart"
                )
        except Exception as e:
            self.ui.write_log(f"WRN: Dashboard — {e}")

        # ── NEW v4: Proactive check-in engine ─────────────────────────────
        try:
            from actions.proactive import ProactiveEngine
            self._proactive = ProactiveEngine(
                min_silence_secs=self._config.get("proactive_min_silence_secs", 900),
                check_cooldown=self._config.get("proactive_check_cooldown", 600),
            )
            self.ui.write_log("SYS: Proactive engine ready.")
        except Exception as e:
            self.ui.write_log(f"WRN: Proactive engine — {e}")

        # ── NEW v4: System resource monitor (voice alerts) ────────────────
        try:
            from actions.system_monitor import SystemMonitor
            self._sys_monitor = SystemMonitor()
            self.ui.write_log("SYS: System monitor ready.")
        except Exception as e:
            self.ui.write_log(f"WRN: System monitor — {e}")

        # ── NEW v3: report integration status ─────────────────────────────
        integration_status = []
        if _LANGCHAIN_OK:
            integration_status.append("LangChain ✓")
        if _OI_OK:
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

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

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

            def _do_stt():
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
            threading.Thread(target=self._proactive_loop,          daemon=True).start()
            threading.Thread(target=self._system_monitor_loop,     daemon=True).start()

            if stt_engine == "vosk":
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
        # so they never go as stale as the old IMPROVEMENTS_V*.md files did, then
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
    for d in ["data", "data/screenshots", "data/rag_index", "logs", "plugins"]:
        Path(d).mkdir(parents=True, exist_ok=True)

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

        # ── NEW v4: validate config before any subsystem loads ────────────
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

        try:
            aether.run()
        except KeyboardInterrupt:
            print("\n[Aether] Shutting down…")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
