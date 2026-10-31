"""
core/crash_logger.py — Aether Crash Recovery Logging

Writes structured crash reports to logs/crashes/ whenever an unhandled
exception occurs during tool execution or message processing.

Each crash file is a JSON document containing:
  - timestamp + human-readable date
  - exception type, message, and full traceback
  - the user message that triggered it
  - the tool being executed (if any)
  - system context (Python version, platform, memory usage)

Usage:
    from core.crash_logger import log_crash, get_recent_crashes

    try:
        ...
    except Exception as e:
        log_crash(e, user_message="What time is it?", tool="reminder")

Read recent crashes in chat: "show recent crashes" / "crash report"
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CRASH_DIR = Path("logs/crashes")
_MAX_CRASH_FILES = 50   # rotate when this many exist


_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password|passwd|authorization|bearer)"
    r"[\"'\s:=]+)([^\s\"',}]{6,})")
_KEY_SHAPES = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
                         r"|xox[baprs]-[A-Za-z0-9-]{10,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})")


def sanitize(text: str) -> str:
    """Strip what must not sit in a shareable crash report: secrets, API-key shapes,
    the user's home directory / account name, and e-mail addresses."""
    if not text:
        return text
    text = _KEY_SHAPES.sub("[redacted-key]", str(text))
    text = _SECRET_RE.sub(lambda m: m.group(1) + "[redacted]", text)
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text)
    home = str(Path.home())
    user = os.path.basename(home.rstrip("/\\"))
    for h in {home, home.replace("/", "\\"), home.replace("\\", "/")}:
        if h and len(h) > 3:
            text = text.replace(h, "~")
    if user and len(user) > 2:
        text = re.sub(r"(?i)(?<=[\\/])" + re.escape(user) + r"(?=[\\/])", "<user>", text)
    return text


def _sanitize_extra(extra: dict | None) -> dict:
    try:
        return json.loads(sanitize(json.dumps(extra or {}, default=str)))
    except Exception:  # noqa: BLE001 - redaction broke the JSON: keep nothing rather than leak
        return {"note": "extra data dropped (could not be sanitized)"}


def _system_context() -> dict:
    ctx: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": sanitize(str(Path.cwd())),
    }
    try:
        import psutil
        vm = psutil.virtual_memory()
        ctx["ram_used_mb"]  = vm.used  // (1024 * 1024)
        ctx["ram_total_mb"] = vm.total // (1024 * 1024)
        ctx["ram_pct"]      = vm.percent
        ctx["cpu_pct"]      = psutil.cpu_percent(interval=0.1)
    except ImportError:
        pass
    return ctx


def log_crash(exc: BaseException,
              user_message: str = "",
              tool: str = "",
              extra: dict | None = None) -> str:
    """
    Write a crash report to logs/crashes/<timestamp>.json.
    Returns the path to the crash file (for display to the user).
    """
    try:
        _CRASH_DIR.mkdir(parents=True, exist_ok=True)

        # Rotate old crash files
        existing = sorted(_CRASH_DIR.glob("*.json"))
        while len(existing) >= _MAX_CRASH_FILES:
            existing.pop(0).unlink(missing_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
        path  = _CRASH_DIR / f"crash_{stamp}.json"

        report = {
            "timestamp":    time.time(),
            "date":         datetime.now().isoformat(),
            "exception":    type(exc).__name__,
            "message":      sanitize(str(exc)),
            "traceback":    sanitize(traceback.format_exc()),
            "user_message": sanitize(user_message[:500]) if user_message else "",
            "tool":         tool,
            "system":       _system_context(),
            "extra":        _sanitize_extra(extra),
        }

        path.write_text(json.dumps(report, indent=2, default=str),
                        encoding="utf-8")
        logger.debug("Crash logged: %s", path)
        return str(path)

    except Exception as write_err:
        logger.error("Failed to write crash log: %s", write_err)
        return ""


def get_recent_crashes(n: int = 5) -> str:
    """Return a human-readable summary of the n most recent crashes."""
    try:
        if not _CRASH_DIR.exists():
            return "No crash logs found."

        files = sorted(_CRASH_DIR.glob("*.json"))[-n:]
        if not files:
            return "No crash logs found."

        lines = [f"## Recent Crashes ({len(files)} of {len(list(_CRASH_DIR.glob('*.json')))} total)", ""]
        for f in reversed(files):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                date = data.get("date", "?")[:19]
                exc  = data.get("exception", "?")
                msg  = data.get("message",   "?")[:100]
                tool = data.get("tool", "")
                user = data.get("user_message", "")[:80]
                lines.append(f"**{date}**  `{exc}`: {msg}")
                if tool:
                    lines.append(f"  Tool: `{tool}`")
                if user:
                    lines.append(f"  Trigger: \"{user}\"")
                lines.append("")
            except Exception:
                lines.append(f"{f.name}: could not read")

        lines.append(f"Crash files at: {_CRASH_DIR}/")
        return "\n".join(lines)
    except Exception as e:
        return f"Could not read crash logs: {e}"


def crash_count() -> int:
    """Return total number of crash files."""
    if not _CRASH_DIR.exists():
        return 0
    return len(list(_CRASH_DIR.glob("*.json")))
