"""
security/capabilities.py — gated actions + risk levels.

Every token carries a `set[Capability]`. Endpoints declare which capability
they need; the middleware in `security/middleware.py` enforces it.

`RiskLevel` is orthogonal to capability: a *capability* says "this token is
allowed to try", a *risk level* says "this action needs desktop-UI approval
even if the token is allowed". Concretely:

    RiskLevel.LOW    — fire immediately (CHAT, WAKE, FILES_LIST)
    RiskLevel.MEDIUM — fire immediately but audit-logged with full payload
                       summary (FILES_UPLOAD, FILES_DOWNLOAD, MEMORY_*)
    RiskLevel.HIGH   — must wait for an approval grant from the desktop UI
                       within 60 s (COMPUTER_CONTROL, SHELL, PLUGIN_EXEC)

Adding a new capability: extend the enum + add it to `_DEFAULT_CAPS` if it
should be granted at first pairing. Adding a new risk level: extend the enum
+ update `policy.py` and `middleware.py` to handle it.
"""

from __future__ import annotations
from enum import Enum


class Capability(str, Enum):
    # ── Always granted to any paired device (cannot be revoked) ──
    CHAT          = "CHAT"            # send text commands
    WAKE          = "WAKE"            # trigger Aether's wake-word equivalent
    NOTIFICATIONS  = "NOTIFICATIONS"  # receive push / backgrounded WS pushes

    # ── Default at first pairing ──
    FILES_LIST    = "FILES_LIST"      # GET /api/files
    VOICE_RX      = "VOICE_RX"        # receive audio replies from Aether (future)
    AUDIT_READ    = "AUDIT_READ"      # read logs/security.log

    # ── Off by default; user must explicitly grant ──
    FILES_UPLOAD  = "FILES_UPLOAD"    # POST /api/upload
    FILES_DOWNLOAD = "FILES_DOWNLOAD" # GET /uploads/{name}
    VOICE_TX      = "VOICE_TX"        # send audio frames (future)
    MEMORY_READ   = "MEMORY_READ"     # read Aether's memory
    MEMORY_WRITE  = "MEMORY_WRITE"    # write to Aether's memory
    COMPUTER_CONTROL = "COMPUTER_CONTROL"  # mouse/keyboard/screen via actions/computer_control.py
    SHELL         = "SHELL"           # run shell commands via actions/extra_tools.py
    PLUGIN_EXEC   = "PLUGIN_EXEC"     # invoke a plugin by name

    # ── Admin (only the user's own desktop UI has this) ──
    ADMIN         = "ADMIN"           # device management, audit, rotation

    @classmethod
    def default_pairing_caps(cls) -> set["Capability"]:
        """Capabilities granted when a device first pairs (no approval needed).

        Conservative: chat + see files + read audit. High-risk capabilities
        are never granted by default — user must explicitly opt in.
        """
        return {
            cls.CHAT,
            cls.WAKE,
            cls.FILES_LIST,
            cls.AUDIT_READ,
            cls.NOTIFICATIONS,
        }

    @classmethod
    def desktop_caps(cls) -> set["Capability"]:
        """Full set of capabilities, used by the Aether desktop UI itself.

        The desktop UI is a separate, always-trusted device (the "brain" PC).
        It has all capabilities; risk-level approval is also skipped for it
        because the user is literally sitting in front of it.
        """
        return set(cls)


class RiskLevel(str, Enum):
    LOW    = "LOW"
    MEDIUM = "MEDIUM"
    HIGH   = "HIGH"


# ── Capability → risk mapping ──────────────────────────────────────────────
# Used by `policy.py` to decide whether a request needs desktop-UI approval.
# Every capability must be in exactly one risk level. Add a new capability →
# add it here.

_RISK_MAP: dict[Capability, RiskLevel] = {
    Capability.CHAT:            RiskLevel.LOW,
    Capability.WAKE:            RiskLevel.LOW,
    Capability.NOTIFICATIONS:   RiskLevel.LOW,
    Capability.FILES_LIST:      RiskLevel.LOW,
    Capability.VOICE_RX:        RiskLevel.LOW,
    Capability.AUDIT_READ:      RiskLevel.LOW,

    Capability.FILES_UPLOAD:    RiskLevel.MEDIUM,
    Capability.FILES_DOWNLOAD:  RiskLevel.MEDIUM,
    Capability.VOICE_TX:        RiskLevel.MEDIUM,
    Capability.MEMORY_READ:     RiskLevel.MEDIUM,

    Capability.MEMORY_WRITE:    RiskLevel.HIGH,
    Capability.COMPUTER_CONTROL: RiskLevel.HIGH,
    Capability.SHELL:           RiskLevel.HIGH,
    Capability.PLUGIN_EXEC:     RiskLevel.HIGH,

    # ADMIN: risk level is N/A — admin endpoints are their own gate
    # (desktop-UI-only, separate from this risk check).
    Capability.ADMIN:           RiskLevel.LOW,
}


def risk_of(cap: Capability) -> RiskLevel:
    if cap not in _RISK_MAP:
        raise ValueError(f"Capability {cap!r} has no risk-level mapping; "
                         f"add it to security/capabilities.py _RISK_MAP")
    return _RISK_MAP[cap]


def caps_for_risk(level: RiskLevel) -> set[Capability]:
    """Return all capabilities that have the given risk level."""
    return {c for c, r in _RISK_MAP.items() if r == level}
