"""
security — Aether remote-access security layer (v4.1+).

Public surface (importable from `security`):

    SecurityManager   — top-level coordinator (load, save, issue, revoke, audit)
    Capability        — enum of gated actions
    RiskLevel         — LOW / MEDIUM / HIGH
    check_capability  — middleware helper
    require_approval  — high-risk approval gate
    audit             — append-only security event log

This package is independent of `dashboard/server.py`. It is mounted as a
FastAPI router by the one opt-in line in `dashboard/server.py`'s `_build_app()`,
guarded by env var `AETHER_SECURE_ROUTER=1`.

State is persisted to `data/security/` (gitignored):
    install.json     — first-run setup result (admin password hash, salt, created)
    devices.json     — paired devices (id, name, hashed token, caps, paired_at)
    sessions.json    — active session tokens (id, device_id, issued, expires)
    pending_pins.json — not-yet-consumed pairing PINs (id, expires)

Audit log lives at `logs/security.log` (gitignored), one JSON object per line.
"""

from .capabilities import Capability, RiskLevel
from .audit import audit
from .middleware import (
    check_capability,
    require_session,
    require_approval,
)
from .tokens import SecurityManager

__all__ = [
    "Capability",
    "RiskLevel",
    "SecurityManager",
    "audit",
    "check_capability",
    "require_session",
    "require_approval",
]
