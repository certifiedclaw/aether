"""Bounded, atomic local storage for named chat transcripts."""

import json
import os
import tempfile
import time
import uuid
from pathlib import Path


_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_MESSAGES = 200
_MAX_MESSAGE_BYTES = 128 * 1024
_MAX_SESSIONS = 30
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


def _default_session(messages=None, title="New chat", auto_title=False) -> dict:
    return {"id": uuid.uuid4().hex, "title": title, "updated_at": time.time(),
            "messages": _valid_history(messages or []), "auto_title": auto_title}


def _preserve_invalid_store(path: Path) -> None:
    backup = path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex}")
    path.replace(backup)


def _valid_store(payload) -> dict:
    if not isinstance(payload, dict):
        return {"active_id": "", "sessions": []}
    if payload.get("version") == 1:
        prior = payload.get("messages", [])
        sessions = [_default_session(prior, "Previous chat")] if prior else []
        return {"active_id": sessions[0]["id"] if sessions else "", "sessions": sessions}
    if payload.get("version") != 2 or not isinstance(payload.get("sessions"), list):
        return {"active_id": "", "sessions": []}
    sessions = []
    names = set()
    for session in payload["sessions"][-_MAX_SESSIONS:]:
        if not isinstance(session, dict):
            continue
        session_id = session.get("id")
        title = session.get("title")
        if not isinstance(session_id, str) or not session_id or not isinstance(title, str):
            continue
        title = title.strip()[:80] or "New chat"
        if title.casefold() in names:
            continue
        names.add(title.casefold())
        try:
            updated_at = float(session.get("updated_at", 0) or 0)
        except (TypeError, ValueError):
            updated_at = 0.0
        sessions.append({
            "id": session_id,
            "title": title,
            "updated_at": updated_at,
            "messages": _valid_history(session.get("messages")),
            "auto_title": bool(session.get("auto_title", False)),
        })
    active_id = payload.get("active_id")
    if active_id not in {session["id"] for session in sessions}:
        active_id = sessions[-1]["id"] if sessions else ""
    return {"active_id": active_id, "sessions": sessions}


def _read_store(path: Path) -> dict:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return {"active_id": "", "sessions": []}
    if size > _MAX_FILE_BYTES:
        _preserve_invalid_store(path)
        return {"active_id": "", "sessions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"active_id": "", "sessions": []}
    except (UnicodeError, json.JSONDecodeError):
        _preserve_invalid_store(path)
        return {"active_id": "", "sessions": []}
    supported = (
        isinstance(payload, dict)
        and (
            payload.get("version") == 1
            or (payload.get("version") == 2 and isinstance(payload.get("sessions"), list))
        )
    )
    if not supported:
        _preserve_invalid_store(path)
        return {"active_id": "", "sessions": []}
    store = _valid_store(payload)
    if payload.get("version") == 2 and payload["sessions"] and not store["sessions"]:
        _preserve_invalid_store(path)
        return {"active_id": "", "sessions": []}
    return store


def _write_store(payload, path: Path) -> None:
    safe_store = _valid_store({"version": 2, **payload})
    def _encoded_store():
        return json.dumps(
            {"version": 2, **safe_store}, ensure_ascii=False, indent=2
        ).encode("utf-8")

    encoded = _encoded_store()
    if len(encoded) > _MAX_FILE_BYTES:
        for session in safe_store["sessions"]:
            session["messages"] = session["messages"][-1:]
        encoded = _encoded_store()
    while len(encoded) > _MAX_FILE_BYTES and len(safe_store["sessions"]) > 1:
        oldest = min(
            (session for session in safe_store["sessions"]
             if session["id"] != safe_store["active_id"]),
            key=lambda item: item["updated_at"],
        )
        safe_store["sessions"].remove(oldest)
        encoded = _encoded_store()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix="chat_history_", suffix=".tmp", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class ChatSessionStore:
    def __init__(self, path: Path | None = None):
        self.path = path or history_path()
        self._store = _read_store(self.path)
        if not self._store["sessions"]:
            session = _default_session(auto_title=True)
            self._store = {"active_id": session["id"], "sessions": [session]}
            self._persist()

    @property
    def active_id(self) -> str:
        return self._store["active_id"]

    @property
    def active_messages(self) -> list[dict]:
        return self._active()["messages"]

    @property
    def active_title(self) -> str:
        return self._active()["title"]

    @property
    def active_session(self) -> dict:
        return self._active()

    def list_sessions(self) -> list[dict]:
        return sorted(self._store["sessions"], key=lambda item: item["updated_at"], reverse=True)

    def _active(self) -> dict:
        return next(session for session in self._store["sessions"]
                    if session["id"] == self._store["active_id"])

    def save_current(self, messages) -> None:
        session = self._active()
        session["messages"] = _valid_history(messages)
        session["updated_at"] = time.time()
        if session.get("auto_title"):
            first_user_message = next(
                (item.get("content", "") for item in session["messages"]
                 if item.get("role") == "user" and isinstance(item.get("content"), str)),
                "",
            )
            if first_user_message.strip():
                title = " ".join(first_user_message.split())[:60]
                names = {item["title"].casefold() for item in self._store["sessions"]
                         if item["id"] != session["id"]}
                base, suffix = title, 2
                while title.casefold() in names:
                    ending = f" ({suffix})"
                    title = base[:60 - len(ending)] + ending
                    suffix += 1
                session["title"] = title
                session["auto_title"] = False
        self._persist()

    def create(self, title: str = "") -> dict:
        auto_title = not title.strip()
        title = title.strip()[:80] or "New chat"
        used = {session["title"].casefold() for session in self._store["sessions"]}
        base, suffix = title, 2
        while title.casefold() in used:
            ending = f" ({suffix})"
            title = base[:80 - len(ending)] + ending
            suffix += 1
        if len(self._store["sessions"]) >= _MAX_SESSIONS:
            raise ValueError(f"Chat limit reached ({_MAX_SESSIONS}); delete a chat first.")
        session = _default_session(title=title, auto_title=auto_title)
        self._store["sessions"].append(session)
        self._store["active_id"] = session["id"]
        self._persist()
        return session

    def switch(self, title: str) -> dict | None:
        wanted = title.strip().casefold()
        session = next((item for item in self._store["sessions"]
                        if item["title"].casefold() == wanted), None)
        if session is None:
            return None
        self._store["active_id"] = session["id"]
        session["updated_at"] = time.time()
        self._persist()
        return session

    def delete(self, title: str) -> bool:
        wanted = title.strip().casefold()
        session = next((item for item in self._store["sessions"]
                        if item["title"].casefold() == wanted), None)
        if session is None or len(self._store["sessions"]) == 1:
            return False
        self._store["sessions"].remove(session)
        if self._store["active_id"] == session["id"]:
            self._store["active_id"] = self._store["sessions"][-1]["id"]
        self._persist()
        return True

    def rename(self, old_title: str, new_title: str) -> bool:
        session = next((item for item in self._store["sessions"]
                        if item["title"].casefold() == old_title.strip().casefold()), None)
        new_title = new_title.strip()[:80]
        if session is None or not new_title:
            return False
        if any(item["id"] != session["id"] and item["title"].casefold() == new_title.casefold()
               for item in self._store["sessions"]):
            return False
        session["title"] = new_title
        session["updated_at"] = time.time()
        self._persist()
        return True

    def rename_active(self, new_title: str) -> bool:
        new_title = new_title.strip()[:80]
        if not new_title:
            return False
        return self.rename(self.active_title, new_title)

    def _persist(self) -> None:
        _write_store(self._store, self.path)


def load_history(path: Path | None = None) -> list[dict]:
    source = path or history_path()
    return ChatSessionStore(source).active_messages


def save_history(messages, path: Path | None = None) -> None:
    destination = path or history_path()
    store = ChatSessionStore(destination)
    store.save_current(messages)
