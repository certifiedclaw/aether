"""Shared secret redaction and private-file helpers for local logs."""
from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

_SECRET_FIELD = re.compile(
    r"(?:pass(?:word|phrase|wd)?|secret|token|api[_-]?key|credential|"
    r"authorization|cookie|bearer|private[_-]?key|pin)$",
    re.I,
)
_URL_CREDENTIALS = re.compile(r"(?i)\b(https?://)[^/@\s]+@")
_URL_SECRET_PARAMS = re.compile(
    r"(?i)([?&](?:key|api_?key|apikey|access_token|token|auth|password|"
    r"secret|client_secret|signature|sig|x-amz-signature)=)[^&#\s]*"
)
_LOCK = threading.Lock()
_SECRETS: set[str] = set()


def register_secret(value: Any) -> None:
    """Keep a resolved credential available to every log sink for redaction."""
    if not isinstance(value, str) or len(value) < 8:
        return
    with _LOCK:
        _SECRETS.add(value)


def current_secrets() -> list[str]:
    with _LOCK:
        return sorted(_SECRETS, key=len, reverse=True)


def configure_log_redaction(config: dict | None = None) -> None:
    """Register config and environment credentials without querying the keyring."""
    if config is None:
        try:
            from core.llm_client import _load_config
            config = _load_config() or {}
        except Exception:  # noqa: BLE001 - redaction setup must not block startup
            config = {}
    try:
        from core.leak_guard import known_secrets
        for secret in known_secrets(config):
            register_secret(secret)
    except Exception:  # noqa: BLE001 - patterns remain active without config
        pass
    for name, value in os.environ.items():
        if _SECRET_FIELD.search(name):
            register_secret(value)


def redact_text(value: Any, secrets: list[str] | None = None) -> str:
    text = "" if value is None else str(value)
    try:
        from core.leak_guard import redact_text as redact_patterns
        all_secrets = list(dict.fromkeys([*(secrets or []), *current_secrets()]))
        text = redact_patterns(text, all_secrets)[0]
    except Exception:  # noqa: BLE001 - retain safe URL redaction as a fallback
        pass
    text = _URL_CREDENTIALS.sub(r"\1***@", text)
    return _URL_SECRET_PARAMS.sub(r"\1(hidden)", text)


def sanitize_for_logs(value: Any, key: str = "", depth: int = 0) -> Any:
    """Return a JSON-safe, recursively redacted copy of structured log data."""
    if key and _SECRET_FIELD.search(key):
        return "(hidden)" if value not in (None, "", False) else value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if depth >= 12:
        return "<max depth>"
    if isinstance(value, dict):
        sanitized = {
            str(k): sanitize_for_logs(v, str(k), depth + 1)
            for k, v in list(value.items())[:1000]
        }
        if len(value) > 1000:
            sanitized["…"] = f"+{len(value) - 1000} more fields omitted"
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        sanitized = [sanitize_for_logs(v, "", depth + 1) for v in items[:1000]]
        if len(items) > 1000:
            sanitized.append(f"…+{len(items) - 1000} more items omitted")
        return sanitized
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, Path):
        return redact_text(value)
    if isinstance(value, BaseException):
        return redact_text(f"{type(value).__name__}: {value}")
    return redact_text(repr(value))


class RedactingLogFilter(logging.Filter):
    """Redact formatted messages and exception text before handlers persist them."""

    def filter(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_aether_redacted", False):
            return True
        try:
            standard = logging.makeLogRecord({}).__dict__
            for name, value in tuple(record.__dict__.items()):
                if name not in standard and not name.startswith("_"):
                    record.__dict__[name] = sanitize_for_logs(value, name)
            record.msg = redact_text(record.getMessage())
            record.args = ()
            if record.exc_info:
                import traceback
                record.exc_text = redact_text(
                    "".join(traceback.format_exception(*record.exc_info))
                )
                record.exc_info = None
            if record.stack_info:
                record.stack_info = redact_text(record.stack_info)
            record._aether_redacted = True
        except Exception:  # noqa: BLE001 - fail closed; never write a possibly secret message
            record.msg = "[log record omitted because redaction failed]"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
            record._aether_redacted = True
        return True


def open_private_text(path: Path, mode: str = "a"):
    """Open a UTF-8 log with owner-only permissions where the OS supports them."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(path.parent, 0o700)
    flags = os.O_CREAT | os.O_WRONLY
    flags |= os.O_APPEND if "a" in mode else os.O_TRUNC
    fd = os.open(path, flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        return os.fdopen(fd, mode, encoding="utf-8", errors="backslashreplace")
    except BaseException:
        os.close(fd)
        raise
