"""
integrations/oi_shell.py — Aether × Open Interpreter

Wraps open-interpreter (github.com/openinterpreter/open-interpreter) as a
sub-tool inside Aether. Open Interpreter lets the LLM write and execute
Python/shell/JavaScript in a local sandbox to accomplish tasks like:
  - "Edit that CSV and plot the results"
  - "Find all .log files modified today and zip them"
  - "Install ffmpeg and convert this video to mp3"
  - Any multi-step computer task the base tools can't do cleanly.

Architecture:
  oi_run(task)      — fires a single task through OI, returns the output
  oi_chat(message)  — multi-turn OI session (state kept in module-level instance)
  oi_reset()        — reset OI session
  oi_tool(action, args) — central dispatch for _execute_tool()

Safety:
  - safe_mode is set to "ask" by default (OI will ask before executing).
  - Set safe_mode="off" in your config if you trust the agent fully.
  - All runs are captured; stdout/stderr are returned to Aether's log.

Requires:
    pip install open-interpreter
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ── Config helpers ────────────────────────────────────────────────────────

def _oi_config() -> dict:
    """Pull relevant settings from Aether config, with safe defaults."""
    try:
        from memory.config_manager import get_mark_config
        cfg = get_mark_config()
        # "llm.model" is legacy/unused (never actually stored under that key —
        # see the same fallback in config_validator.py); the real user setting
        # lives under the flat "llm_model" key. LiteLLM (which OI uses under
        # the hood) requires an "ollama/" provider prefix that raw llm_model
        # values don't carry, so add it if missing.
        model = cfg.get("llm.model", cfg.get("llm_model", "ollama/qwen3:8b"))
        if model and "/" not in model:
            model = f"ollama/{model}"
        return {
            "model":     model,
            "base_url":  cfg.ollama_url,
            "safe_mode": cfg.get("oi.safe_mode", "ask"),
            "auto_run":  cfg.get("oi.auto_run", False),
        }
    except Exception:
        return {
            "model": "ollama/qwen3:8b",
            "base_url": "http://localhost:11434",
            "safe_mode": "ask",
            "auto_run": False,
        }


def _make_interpreter():
    """Create and configure an Open Interpreter instance."""
    from interpreter import OpenInterpreter  # pip install open-interpreter
    cfg = _oi_config()
    oi = OpenInterpreter()
    oi.llm.model    = cfg["model"]
    oi.llm.api_base = cfg["base_url"]
    oi.llm.api_key  = "ollama"     # OI needs a non-empty key even for local
    oi.safe_mode    = cfg["safe_mode"]
    oi.auto_run     = cfg["auto_run"]
    oi.verbose      = False
    oi.offline      = True         # never phone home
    return oi


# ── Module-level session (persists across calls in one Aether session) ───

_oi_instance: Any = None

def _get_oi():
    global _oi_instance
    if _oi_instance is None:
        _oi_instance = _make_interpreter()
    return _oi_instance


# ── Public API ────────────────────────────────────────────────────────────

def _run_with_timeout(fn, timeout: int = 120) -> str:
    """
    Run fn() in a daemon thread.  Returns its result or a timeout message.
    Prevents OI from blocking Aether's UI thread indefinitely.
    """
    import threading
    result_box: list = [None]
    exc_box:    list = [None]

    def _worker():
        try:
            result_box[0] = fn()
        except Exception as e:
            exc_box[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return f"[timeout] Open Interpreter did not finish within {timeout}s. It may still be running in the background."
    if exc_box[0]:
        raise exc_box[0]
    return result_box[0]


def oi_run(task: str, timeout: int = 120) -> str:
    """
    Run a single task through Open Interpreter.
    Returns a text summary of everything that happened (code + output).
    Runs in a background thread — never blocks the UI.
    """
    try:
        oi = _get_oi()
        return _run_with_timeout(
            lambda: _extract_output(oi.chat(task, display=False, stream=False)),
            timeout=timeout,
        )
    except ImportError:
        return (
            "Open Interpreter not installed.\n"
            "Fix: pip install open-interpreter\n"
            "Docs: https://github.com/openinterpreter/open-interpreter"
        )
    except Exception as e:
        logger.warning("OI run error: %s", e)
        return f"Open Interpreter error: {e}"


def oi_chat(message: str, timeout: int = 120) -> str:
    """
    Send a message to a persistent OI session.
    Remembers previous context (code, files, variables).
    Runs in a background thread — never blocks the UI.
    """
    try:
        oi = _get_oi()
        return _run_with_timeout(
            lambda: _extract_output(oi.chat(message, display=False, stream=False)),
            timeout=timeout,
        )
    except ImportError:
        return "Install: pip install open-interpreter"
    except Exception as e:
        return f"OI chat error: {e}"


def oi_reset() -> str:
    """Reset the OI session — clears all code state and history."""
    global _oi_instance
    _oi_instance = None
    return "Open Interpreter session reset."


def oi_status() -> str:
    """Return the current OI config / session status."""
    try:
        cfg = _oi_config()
        active = _oi_instance is not None
        return (
            f"Open Interpreter status:\n"
            f"  Model:     {cfg['model']}\n"
            f"  Safe mode: {cfg['safe_mode']}\n"
            f"  Auto-run:  {cfg['auto_run']}\n"
            f"  Session:   {'active' if active else 'not started'}"
        )
    except Exception as e:
        return f"Status error: {e}"


# ── Output formatting ─────────────────────────────────────────────────────

def _extract_output(messages: list[dict]) -> str:
    """
    Convert OI's message list into a clean human-readable string.
    Preserves code blocks and console output, strips internal role noise.
    """
    parts = []
    for msg in messages:
        role    = msg.get("role", "")
        content = msg.get("content", "")
        mtype   = msg.get("type", "")

        if role == "assistant" and mtype == "message" and content:
            parts.append(content)
        elif role == "assistant" and mtype == "code" and content:
            lang = msg.get("format", "python")
            parts.append(f"```{lang}\n{content}\n```")
        elif role == "computer" and mtype in ("console", "output") and content:
            parts.append(f"[output]\n{content}")

    return "\n\n".join(parts).strip() or "Done (no output)."


# ── Main dispatch (called from _execute_tool) ─────────────────────────────

def oi_tool(action: str, args: dict) -> str:
    """
    Central dispatch for the 'open_interpreter' tool in _execute_tool().

    action:
      run    — run a single natural-language task (new sub-session)
      chat   — send to the persistent session
      reset  — clear session state
      status — show config
    """
    dispatch = {
        "run":    lambda: oi_run(
            args.get("task", ""),
            timeout=int(args.get("timeout", 120)),
        ),
        "chat":   lambda: oi_chat(
            args.get("message", args.get("task", "")),
            timeout=int(args.get("timeout", 120)),
        ),
        "reset":  lambda: oi_reset(),
        "status": lambda: oi_status(),
    }
    fn = dispatch.get(action)
    if fn:
        try:
            return fn()
        except Exception as e:
            return f"open_interpreter '{action}' error: {e}"
    return f"Unknown action: {action}. Options: {list(dispatch)}"


# ── Tool declaration (add to TOOL_DECLARATIONS in main.py) ────────────────

OI_TOOL_DECLARATION = {
    "name": "open_interpreter",
    "description": (
        "Natural language computer control powered by Open Interpreter. "
        "Can write and execute Python, shell, and JavaScript code on the local "
        "machine to accomplish multi-step tasks: edit files, process data, "
        "install software, automate the desktop, generate plots, and more. "
        "Use this when the task requires writing and running real code rather "
        "than just describing it. "
        "actions: run (single task), chat (persistent session), reset, status."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action":  {"type": "STRING",
                        "description": "run | chat | reset | status"},
            "task":    {"type": "STRING",
                        "description": "Natural language description of what to do (run/chat)"},
            "message": {"type": "STRING",
                        "description": "Follow-up message for a persistent chat session"},
            "timeout": {"type": "INTEGER",
                        "description": "Max seconds to wait for OI to finish (default 120)"},
        },
        "required": ["action"],
    },
}
