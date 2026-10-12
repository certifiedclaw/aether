"""
security/audit.py — append-only security event log.

Every authentication, command, capability check, approval, and revocation is
written to `logs/security.log` as a single JSON object per line. The log is
gitignored, mode 0600 where the OS allows, and rotated by size at 5 MB
(keeping the last 3 rotations). The format is line-delimited JSON so a
human can `tail -f` it and `jq` can filter it.

Public API:
    audit(event, **fields)        — write one event, never raises
    read_recent(since_ts)         — generator over events since a unix ts
    get_log_path()                — Path to the current log file
    rotate_if_needed()            — called automatically before each write

Schema (every line):
    {"ts": "2026-07-13T18:30:01Z",
     "event": "pair.success",
     "ip": "203.0.113.4",
     ...additional fields specific to the event...}

Event types (extending the list is fine; consumers should ignore unknowns):
    pair.request      — a /login or /api/pair request was received
    pair.denied       — bad PIN or rate limit
    pair.success      — device paired, returning a session + device token
    session.issued    — fresh session token created
    session.expired   — token validation failed (no / bad / expired)
    session.revoked   — admin revoked a session
    device.added      — first pairing of a new device id
    device.revoked    — admin revoked a device (all its sessions die)
    cmd.text          — text command processed (cap: CHAT)
    cmd.voice_frame   — voice frame received (cap: VOICE_TX) — future
    cap.requested     — high-risk action requested, awaiting approval
    cap.approved      — desktop UI approved a high-risk action
    cap.denied        — desktop UI denied OR token lacks capability OR timeout
    cap.timeout       — high-risk approval wasn't granted within TTL
    tunnel.started    — remote tunnel (Cloudflare/ngrok) started
    tunnel.stopped    — remote tunnel stopped
    install.first_run — first-time setup completed
"""

from __future__ import annotations
import json
import os
import sys
import time
import threading
from datetime import datetime, timezone
from pathlib import Path

# Resolve log dir relative to repo root so the import works from anywhere.
# security/ sits at the repo root, so security/audit.py's parent.parent is root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR   = _REPO_ROOT / "logs"
_LOG_FILE  = _LOG_DIR / "security.log"
_MAX_BYTES = 5 * 1024 * 1024   # 5 MB
_KEEP_ROTATIONS = 3

_write_lock = threading.Lock()
_utc_iso = lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_log_path() -> Path:
    return _LOG_FILE


def _ensure_log_dir() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    # Best-effort permission tightening on POSIX. On Windows the ACL is left
    # at default; users on a shared machine should run Aether as their own user.
    if sys.platform != "win32":
        try:
            os.chmod(_LOG_DIR, 0o700)
        except OSError:
            pass


def _rotate_if_needed() -> None:
    """Rotate the log if it exceeds _MAX_BYTES. Keep last _KEEP_ROTATIONS."""
    if not _LOG_FILE.exists():
        return
    try:
        if _LOG_FILE.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return

    # Shift: security.log.2 → .3, .1 → .2, current → .1
    for i in range(_KEEP_ROTATIONS, 0, -1):
        src = _LOG_FILE.with_suffix(f".log.{i}")
        dst = _LOG_FILE.with_suffix(f".log.{i + 1}")
        if i == _KEEP_ROTATIONS and dst.exists():
            dst.unlink()
        if src.exists():
            src.rename(dst)
    _LOG_FILE.rename(_LOG_FILE.with_suffix(".log.1"))


def audit(event: str, **fields) -> None:
    """Append one event to the security log. Never raises.

    Intended to be safe to call from any code path — auth failures, exceptions,
    or filesystem errors must not bubble up and break the caller (which is
    often the auth middleware itself).
    """
    try:
        with _write_lock:
            _ensure_log_dir()
            _rotate_if_needed()
            entry = {"ts": _utc_iso(), "event": event, **fields}
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, separators=(",", ":")) + "\n")
            if sys.platform != "win32":
                try:
                    os.chmod(_LOG_FILE, 0o600)
                except OSError:
                    pass
    except Exception:
        # Audit must never break the caller. Swallow.
        pass


def read_recent(since_ts: float | None = None) -> list[dict]:
    """Read recent log entries. If since_ts is given, only entries with
    ts >= since_ts (compared against the ISO ts converted to unix) are
    returned. Malformed lines are skipped (not raised)."""
    if not _LOG_FILE.exists():
        return []

    out: list[dict] = []
    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if since_ts is not None:
                    try:
                        # ISO "2026-07-13T18:30:01Z" → unix
                        ev_iso = entry.get("ts", "")
                        if ev_iso.endswith("Z"):
                            ev_iso = ev_iso[:-1] + "+00:00"
                        ev_unix = datetime.fromisoformat(ev_iso).timestamp()
                        if ev_unix < since_ts:
                            continue
                    except Exception:
                        pass
                out.append(entry)
    except OSError:
        pass
    return out
