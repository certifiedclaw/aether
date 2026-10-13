"""Small, local transcript store for restoring the latest conversation."""

import json
import os
import tempfile
from pathlib import Path


_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_MESSAGES = 200
_MAX_MESSAGE_BYTES = 128 * 1024
_ALLOWED_ROLES = {"assistant", "tool", "user"}


def history_path() -> Path:
    return Path.home() / ".aether" / "chat_history.json"


def _valid_history(messages) -> list[dict]:
    if not isinstance(messages, list):
        return []
    valid = []
    total_bytes = 0
    for message in reversed(messages[-_MAX_MESSAGES:]):
        if not isinstance(message, dict) or message.get("role") not in _ALLOWED_ROLES:
            continue
        try:
            message_bytes = len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            continue
        if message_bytes > _MAX_MESSAGE_BYTES or total_bytes + message_bytes > _MAX_FILE_BYTES - 128:
            continue
        valid.append(message)
        total_bytes += message_bytes
    return list(reversed(valid))


def load_history(path: Path | None = None) -> list[dict]:
    source = path or history_path()
    try:
        if source.stat().st_size > _MAX_FILE_BYTES:
            return []
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return []
    return _valid_history(payload.get("messages"))


def save_history(messages, path: Path | None = None) -> None:
    destination = path or history_path()
    safe_messages = _valid_history(messages)
    payload = json.dumps(
        {"version": 1, "messages": safe_messages},
        ensure_ascii=False,
        indent=2,
    )
    encoded = payload.encode("utf-8")
    if len(encoded) > _MAX_FILE_BYTES:
        safe_messages = safe_messages[-50:]
        payload = json.dumps(
            {"version": 1, "messages": safe_messages},
            ensure_ascii=False,
            indent=2,
        )
        encoded = payload.encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix="chat_history_", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
