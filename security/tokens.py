"""
security/tokens.py — SecurityManager: state + token issuance + device pairing.

This is the central authority for:
  * Has the user completed first-time setup? (data/security/install.json)
  * Which devices are paired? (data/security/devices.json)
  * Which session tokens are valid right now? (data/security/sessions.json)
  * Which PINs are pending consumption? (data/security/pending_pins.json)

Files are simple JSON. Reads are atomic-ish (open + load), writes go through
a temp file + replace, and the SecurityManager holds a per-file write lock.
This is a single-user desktop app, not a multi-tenant server; the simple
format is correct.

Aether's existing dashboard has its own `_tokens` set and `_device_sessions`
dict (in `dashboard/server.py`). Those continue to work; the new
`SecurityManager` is consulted by `dashboard/api_secure.py` for the gated
endpoints. We deliberately do not merge the two — keeping the old auth path
unmodified preserves backward compatibility for any in-flight clients.
"""

from __future__ import annotations
import hashlib
import json
import os
import secrets
import string
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .capabilities import Capability
from . import audit


# ── Paths ────────────────────────────────────────────────────────────────
_REPO_ROOT  = Path(__file__).resolve().parent.parent
_DATA_DIR   = _REPO_ROOT / "data" / "security"
_INSTALL    = _DATA_DIR / "install.json"
_DEVICES    = _DATA_DIR / "devices.json"
_SESSIONS   = _DATA_DIR / "sessions.json"
_PENDING    = _DATA_DIR / "pending_pins.json"


# ── Tunables ─────────────────────────────────────────────────────────────
_PIN_LEN              = 6
_PIN_ALPHABET         = string.ascii_uppercase + string.digits
_PIN_TTL_SECS         = 10 * 60         # 10 minutes
_SESSION_TTL_SECS     = 24 * 60 * 60    # 24 hours
_DEVICE_TOKEN_TTL     = None           # persistent until revoked
_MAX_DEVICES          = 5
_RATE_LIMIT_MAX       = 5              # attempts per window per IP
_RATE_LIMIT_WINDOW    = 15 * 60        # 15 minutes
_RATE_LIMIT_LOCKOUT   = 60 * 60        # 1 hour after 10 consecutive fails
_MAX_CONSEC_FAILS     = 10


# ── Data classes ──────────────────────────────────────────────────────────
@dataclass
class DeviceRecord:
    device_id: str
    name: str
    token_hash: str        # SHA-256(salt ‖ device_token)
    salt: str              # per-device 16-byte random hex
    caps: list[str]        # list of Capability values
    paired_at: float
    last_seen: float
    consecutive_fails: int = 0
    locked_until: float = 0.0

    def has_cap(self, cap: Capability) -> bool:
        return cap.value in self.caps

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "DeviceRecord":
        return cls(
            device_id=d["device_id"],
            name=d.get("name", "unknown"),
            token_hash=d["token_hash"],
            salt=d["salt"],
            caps=d.get("caps", []),
            paired_at=d.get("paired_at", 0.0),
            last_seen=d.get("last_seen", 0.0),
            consecutive_fails=d.get("consecutive_fails", 0),
            locked_until=d.get("locked_until", 0.0),
        )


@dataclass
class SessionRecord:
    session_id: str        # the bearer token, also its id
    device_id: str
    issued: float
    expires: float

    def is_valid(self, now: float | None = None) -> bool:
        return (now or time.time()) < self.expires

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "SessionRecord":
        return cls(
            session_id=d["session_id"],
            device_id=d["device_id"],
            issued=d.get("issued", 0.0),
            expires=d.get("expires", 0.0),
        )


# ── SecurityManager ──────────────────────────────────────────────────────
class SecurityManager:
    """Single instance per process. Thread-safe.

    The class is the only writer to the JSON files under data/security/.
    Aether's existing dashboard continues to use its own auth state; this
    manager exists for the new secure router (`dashboard/api_secure.py`).
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._dir = data_dir or _DATA_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        # POSIX tightening. Windows uses inherited ACL.
        if os.name != "nt":
            try:
                os.chmod(self._dir, 0o700)
            except OSError:
                pass
        self._install_path = self._dir / "install.json"
        self._lock = threading.RLock()
        self._devices:  dict[str, DeviceRecord]  = {}
        self._sessions: dict[str, SessionRecord] = {}
        self._pending:  dict[str, float]         = {}   # pin → expires
        self._attempts: dict[str, list[float]]   = {}   # ip → [ts, ...]
        self._lockouts: dict[str, float]         = {}   # ip → lock-until
        self._load_all()

    # ── Persistence ────────────────────────────────────────────────────
    def _load_all(self) -> None:
        with self._lock:
            for path, container, ctor in (
                (_DEVICES,  "devices",  DeviceRecord),
                (_SESSIONS, "sessions", SessionRecord),
            ):
                p = self._dir / f"{container}.json"
                if not p.exists():
                    continue
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
                target = self._devices if container == "devices" else self._sessions
                for k, v in raw.items():
                    try:
                        target[k] = ctor.from_json(v)
                    except Exception:
                        continue
            p = self._dir / "pending_pins.json"
            if p.exists():
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        self._pending = json.load(f)
                except (OSError, json.JSONDecodeError):
                    self._pending = {}

    def _save(self, name: str, payload: dict) -> None:
        p = self._dir / f"{name}.json"
        tmp = p.with_suffix(".json.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, p)
            if os.name != "nt":
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        except OSError:
            # Audit but don't raise — persistence is best-effort here.
            audit("persist.error", file=name)

    def _save_devices(self) -> None:
        self._save("devices", {k: v.to_json() for k, v in self._devices.items()})

    def _save_sessions(self) -> None:
        self._save("sessions", {k: v.to_json() for k, v in self._sessions.items()})

    def _save_pending(self) -> None:
        self._save("pending_pins", self._pending)

    # ── First-run setup ────────────────────────────────────────────────
    def is_installed(self) -> bool:
        return self._install_path.exists()

    def first_run_complete(self, admin_password: str) -> dict:
        """Initialize install.json. Returns it for the caller to display once.

        Generates:
          * salt (16 random bytes, hex)
          * admin_password_hash = SHA-256(salt ‖ admin_password)
        The cleartext password is returned once; we never store it.
        """
        with self._lock:
            if self._install_path.exists():
                raise RuntimeError("install.json already exists; refusing to re-init")
            salt = secrets.token_hex(16)
            pw_hash = hashlib.sha256((salt + admin_password).encode("utf-8")).hexdigest()
            payload = {
                "version": 1,
                "created": time.time(),
                "salt": salt,
                "admin_password_hash": pw_hash,
            }
            self._save("install", payload)
            audit("install.first_run")
            return payload

    def verify_admin_password(self, password: str) -> bool:
        if not self._install_path.exists():
            return False
        try:
            with open(self._install_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        candidate = hashlib.sha256((data["salt"] + password).encode("utf-8")).hexdigest()
        # Constant-time compare to avoid timing leaks
        return secrets.compare_digest(candidate, data["admin_password_hash"])

    # ── PIN issuance & rate limiting ───────────────────────────────────
    def _prune_attempts(self, ip: str, now: float) -> None:
        window_start = now - _RATE_LIMIT_WINDOW
        if ip in self._attempts:
            self._attempts[ip] = [t for t in self._attempts[ip] if t >= window_start]
        if ip in self._lockouts and self._lockouts[ip] <= now:
            del self._lockouts[ip]

    def is_ip_locked(self, ip: str) -> bool:
        with self._lock:
            self._prune_attempts(ip, time.time())
            return ip in self._lockouts

    def record_attempt(self, ip: str, success: bool) -> None:
        """Record one PIN attempt. On failure increments the consecutive
        counter; on success resets it. After _MAX_CONSEC_FAILS failures
        within _RATE_LIMIT_WINDOW, locks the IP for _RATE_LIMIT_LOCKOUT."""
        with self._lock:
            now = time.time()
            self._prune_attempts(ip, now)
            self._attempts.setdefault(ip, []).append(now)

            if success:
                # Reset consecutive-fail counter for any device that matches
                # the same IP — we don't know the device_id yet at PIN time,
                # so this resets all devices from that IP. Acceptable for a
                # single-user home network.
                for d in self._devices.values():
                    d.consecutive_fails = 0
                    d.locked_until = 0.0
                self._save_devices()
                return

            # Consecutive-fail check across all devices from this IP
            for d in self._devices.values():
                d.consecutive_fails += 1
                if d.consecutive_fails >= _MAX_CONSEC_FAILS:
                    d.locked_until = now + _RATE_LIMIT_LOCKOUT
            # Escalate the IP itself to the long lockout only after
            # _MAX_CONSEC_FAILS failures within the window -- not on every
            # single failed attempt (that's what is_rate_limited()'s
            # _RATE_LIMIT_MAX/window check is already for).
            if len(self._attempts.get(ip, [])) >= _MAX_CONSEC_FAILS:
                self._lockouts[ip] = now + _RATE_LIMIT_LOCKOUT
            self._save_devices()

    def is_rate_limited(self, ip: str) -> bool:
        with self._lock:
            self._prune_attempts(ip, time.time())
            attempts = len(self._attempts.get(ip, []))
            return attempts >= _RATE_LIMIT_MAX or ip in self._lockouts

    def issue_pin(self) -> str:
        """Issue a new one-time pairing PIN. Old PINs are pruned."""
        with self._lock:
            now = time.time()
            self._pending = {k: v for k, v in self._pending.items() if v > now}
            pin = "".join(secrets.choice(_PIN_ALPHABET) for _ in range(_PIN_LEN))
            self._pending[pin] = now + _PIN_TTL_SECS
            self._save_pending()
            return pin

    def consume_pin(self, pin: str) -> bool:
        """Consume a PIN. Returns True iff it was valid (and removes it)."""
        with self._lock:
            now = time.time()
            self._pending = {k: v for k, v in self._pending.items() if v > now}
            if pin in self._pending:
                del self._pending[pin]
                self._save_pending()
                return True
            return False

    # ── Device pairing ─────────────────────────────────────────────────
    def pair_device(self, device_name: str, caps: set[Capability] | None = None) -> dict:
        """Pair a new device. Returns {device_id, device_token, session_token, caps}."""
        with self._lock:
            if len(self._devices) >= _MAX_DEVICES:
                raise RuntimeError(f"Device cap reached ({_MAX_DEVICES}). "
                                   f"Revoke an existing device first.")
            device_id = "dev_" + secrets.token_urlsafe(8)
            device_token = secrets.token_urlsafe(32)
            salt = secrets.token_hex(16)
            token_hash = hashlib.sha256((salt + device_token).encode("utf-8")).hexdigest()

            granted = caps if caps is not None else Capability.default_pairing_caps()
            rec = DeviceRecord(
                device_id=device_id,
                name=device_name,
                token_hash=token_hash,
                salt=salt,
                caps=[c.value for c in granted],
                paired_at=time.time(),
                last_seen=time.time(),
            )
            self._devices[device_id] = rec
            self._save_devices()

            session = self._issue_session_locked(device_id)
            return {
                "device_id": device_id,
                "device_token": device_token,
                "session_token": session.session_id,
                "session_expires": session.expires,
                "caps": [c.value for c in granted],
            }

    def _issue_session_locked(self, device_id: str) -> SessionRecord:
        now = time.time()
        # Drop expired sessions first
        self._sessions = {k: v for k, v in self._sessions.items() if v.is_valid(now)}
        session = SessionRecord(
            session_id=secrets.token_urlsafe(32),
            device_id=device_id,
            issued=now,
            expires=now + _SESSION_TTL_SECS,
        )
        self._sessions[session.session_id] = session
        self._save_sessions()
        return session

    def reauth_device(self, device_token: str) -> dict | None:
        """Re-pair an existing device_token → fresh session. Returns the
        new session payload, or None if the token is unknown / locked."""
        with self._lock:
            now = time.time()
            for rec in self._devices.values():
                candidate = hashlib.sha256(
                    (rec.salt + device_token).encode("utf-8")
                ).hexdigest()
                if secrets.compare_digest(candidate, rec.token_hash):
                    if rec.locked_until > now:
                        return None
                    rec.last_seen = now
                    rec.consecutive_fails = 0
                    self._save_devices()
                    session = self._issue_session_locked(rec.device_id)
                    return {
                        "device_id": rec.device_id,
                        "session_token": session.session_id,
                        "session_expires": session.expires,
                        "caps": rec.caps,
                    }
            return None

    def list_devices(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "device_id": d.device_id,
                    "name": d.name,
                    "caps": d.caps,
                    "paired_at": d.paired_at,
                    "last_seen": d.last_seen,
                    "locked": d.locked_until > time.time(),
                }
                for d in self._devices.values()
            ]

    def revoke_device(self, device_id: str) -> bool:
        with self._lock:
            if device_id not in self._devices:
                return False
            del self._devices[device_id]
            # Kill all sessions for this device
            self._sessions = {
                k: v for k, v in self._sessions.items() if v.device_id != device_id
            }
            self._save_devices()
            self._save_sessions()
            return True

    def grant_capability(self, device_id: str, cap: Capability) -> bool:
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                return False
            if cap.value not in rec.caps:
                rec.caps.append(cap.value)
                self._save_devices()
            return True

    def revoke_capability(self, device_id: str, cap: Capability) -> bool:
        with self._lock:
            rec = self._devices.get(device_id)
            if not rec:
                return False
            if cap.value in rec.caps:
                rec.caps.remove(cap.value)
                self._save_devices()
            return True

    # ── Session validation (called from middleware) ────────────────────
    def validate_session(self, session_token: str) -> tuple[DeviceRecord, SessionRecord] | None:
        with self._lock:
            now = time.time()
            sess = self._sessions.get(session_token)
            if not sess or not sess.is_valid(now):
                if sess:
                    del self._sessions[session_token]
                    self._save_sessions()
                return None
            rec = self._devices.get(sess.device_id)
            if not rec or rec.locked_until > now:
                return None
            rec.last_seen = now
            return rec, sess

    def revoke_session(self, session_token: str) -> bool:
        with self._lock:
            if session_token in self._sessions:
                del self._sessions[session_token]
                self._save_sessions()
                return True
            return False

    def revoke_all_sessions(self) -> int:
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
            self._save_sessions()
            return n


# Module-level singleton; Aether imports this directly.
_manager: SecurityManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> SecurityManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = SecurityManager()
        return _manager
