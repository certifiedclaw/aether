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
  - the trace session/turn and the last 40 trace events leading up to it
Every report is sanitized before it is written: secret-named fields and API-key /
token patterns are removed, and the home directory, user name and install path are
replaced with ~ / <user> / <aether>, so a report is safe to attach to a bug report.

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
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CRASH_DIR = Path("logs/crashes")
_MAX_CRASH_FILES = 50   # rotate when this many exist


def _path_scrubber():
    """Replace the home dir, user name and project root so a report can be shared
    without revealing who or where. Longest paths first."""
    pairs: list[tuple[str, str]] = []
    root = Path(__file__).resolve().parent.parent
    for raw, label in ((str(root), "<aether>"), (str(Path.home()), "~")):
        for variant in {raw, raw.replace("\\", "/"), raw.replace("/", "\\")}:
            if len(variant) > 3:
                pairs.append((variant, label))
    user = ""
    try:
        user = os.path.basename(str(Path.home())) or os.environ.get("USERNAME", "") or os.environ.get("USER", "")
    except Exception:  # noqa: BLE001
        pass
    pairs.sort(key=lambda p: -len(p[0]))

    def scrub(text: str) -> str:
        for old, new in pairs:
            text = text.replace(old, new)
        if len(user) >= 3 and user.lower() not in ("root", "user", "home"):
            text = text.replace(user, "<user>")
        return text
    return scrub


def sanitize_report(report: dict) -> dict:
    """Secrets (key names and token patterns) and personal paths removed from every
    string in the report, however deeply nested."""
    try:
        from core import trace_log
        clean = trace_log.sanitize(report)
    except Exception:  # noqa: BLE001 - fall back to path scrubbing only
        clean = report
    scrub = _path_scrubber()

    def walk(v):
        if isinstance(v, str):
            return scrub(v)
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [walk(x) for x in v]
        return v
    return walk(clean)


def _system_context() -> dict:
    ctx: dict[str, Any] = {
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": str(Path.cwd()),
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
              extra: dict | None = None,
              context: str = "") -> str:
    """
    Write a crash report to logs/crashes/<timestamp>.json.
    Returns the path to the crash file (for display to the user).

    `context` names where it happened (e.g. "STT-Whisper"). The STT loops always
    passed it, but the parameter did not exist, so every mic failure raised a
    TypeError here and the real error was lost.
    """
    try:
        from core import trace_log
        trace_log.event("crash", exception=type(exc).__name__, message=str(exc),
                        traceback=traceback.format_exc(), user_message=user_message,
                        tool=tool, context=context, extra=extra or {})
        trace_log.flush(2.0)
    except Exception:  # noqa: BLE001 - the trace must never stop a crash report
        pass
    if context:
        extra = {**(extra or {}), "context": context}
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
            "message":      str(exc),
            "traceback":    "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            "user_message": user_message[:500] if user_message else "",
            "tool":         tool,
            "system":       _system_context(),
            "extra":        extra or {},
        }
        try:
            from core import trace_log
            report["trace"] = {
                "session": trace_log.session_id(),
                "turn": trace_log.current_turn(),
                "file": str(trace_log.trace_path() or ""),
                "last_events": trace_log.recent_events(40),
            }
        except Exception:  # noqa: BLE001
            pass
        report = sanitize_report(report)
        report["sanitized"] = True

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
