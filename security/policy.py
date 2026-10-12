"""
security/policy.py — runtime approval flow for HIGH-risk capabilities.

The desktop UI is the source of truth for "approve this risky action?".
A high-risk request from a phone looks like:

    1. Phone → POST /api/control/shell  with body {"cmd": "ls"}
    2. Server: token has SHELL capability? → yes
       Risk level HIGH → need approval
       Create Approval(approval_id, expires_in=60s, cmd=...)
       Return 202 {"approval_id": "u7k", "expires_in": 60}
    3. Server → desktop UI: notification / dialog with the pending approval
    4. Desktop UI → user clicks Approve or Deny
       Server: Approval.status = APPROVED or DENIED, audit log
    5. Phone polls GET /api/approvals/{id}  → returns 200/403

The phone should poll with backoff (1s, 2s, 4s, 8s, … up to 60s).
A 60s timeout auto-denies.

Approvals are stored in memory only — restarting Aether clears pending
approvals. This is correct: if the desktop UI is gone, neither approver
nor requester is around to act, so dropping the request is the safe move.
"""

from __future__ import annotations
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Literal

from .capabilities import Capability, RiskLevel, risk_of
from . import audit


ApprovalStatus = Literal["pending", "approved", "denied", "timeout", "expired"]


@dataclass
class Approval:
    approval_id: str
    device_id: str
    capability: Capability
    summary: str              # human-readable: "shell: ls -la"
    payload: dict             # request body to forward if approved
    endpoint: str             # e.g. "POST /api/control/shell"
    created: float
    expires: float
    status: ApprovalStatus = "pending"
    decided_at: float | None = None
    decided_by: str | None = None  # "desktop" or "auto-timeout"

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires

    def to_json(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "device_id": self.device_id,
            "capability": self.capability.value,
            "summary": self.summary,
            "payload": self.payload,
            "endpoint": self.endpoint,
            "created": self.created,
            "expires": self.expires,
            "status": self.status,
            "decided_at": self.decided_at,
            "decided_by": self.decided_by,
            "seconds_remaining": max(0, int(self.expires - time.time())),
        }


class ApprovalStore:
    """In-memory store of pending + recent approvals.

    Pending approvals expire after 60s; recent (decided) approvals are kept
    for 5 minutes so the phone can poll once more and learn the final
    decision before we garbage-collect them.
    """

    _PENDING_TTL = 60.0        # seconds until auto-deny
    _KEPT_AFTER_DECISION = 300  # seconds a decided approval is still queryable

    def __init__(self) -> None:
        self._items: dict[str, Approval] = {}
        self._lock = threading.RLock()

    def create(self, *, device_id: str, cap: Capability, summary: str,
               payload: dict, endpoint: str) -> Approval:
        with self._lock:
            now = time.time()
            self._gc(now)
            a = Approval(
                approval_id=secrets.token_urlsafe(8),
                device_id=device_id,
                capability=cap,
                summary=summary,
                payload=payload,
                endpoint=endpoint,
                created=now,
                expires=now + self._PENDING_TTL,
            )
            self._items[a.approval_id] = a
            return a

    def get(self, approval_id: str) -> Optional[Approval]:
        with self._lock:
            self._gc(time.time())
            return self._items.get(approval_id)

    def list_pending(self) -> list[Approval]:
        """For the desktop UI: which approvals are waiting on the user?"""
        with self._lock:
            self._gc(time.time())
            return [
                a for a in self._items.values()
                if a.status == "pending" and not a.is_expired()
            ]

    def list_recent(self, limit: int = 50) -> list[Approval]:
        with self._lock:
            self._gc(time.time())
            items = sorted(
                self._items.values(), key=lambda a: a.created, reverse=True,
            )
            return items[:limit]

    def decide(self, approval_id: str, decision: Literal["approved", "denied"],
               decided_by: str = "desktop") -> Optional[Approval]:
        with self._lock:
            a = self._items.get(approval_id)
            if not a:
                return None
            if a.status != "pending":
                return a
            now = time.time()
            if a.is_expired(now):
                a.status = "timeout"
                a.decided_at = now
                a.decided_by = "auto-timeout"
                audit("cap.timeout", approval_id=approval_id,
                      capability=a.capability.value, device_id=a.device_id)
                return a
            a.status = decision
            a.decided_at = now
            a.decided_by = decided_by
            audit(
                "cap.approved" if decision == "approved" else "cap.denied",
                approval_id=approval_id,
                capability=a.capability.value,
                device_id=a.device_id,
                decided_by=decided_by,
            )
            return a

    def _gc(self, now: float) -> None:
        """Garbage-collect: pending beyond expiry → timeout, decided older
        than _KEPT_AFTER_DECISION → drop."""
        drop: list[str] = []
        for aid, a in self._items.items():
            if a.status == "pending" and a.is_expired(now):
                a.status = "timeout"
                a.decided_at = now
                a.decided_by = "auto-timeout"
                audit("cap.timeout", approval_id=aid,
                      capability=a.capability.value, device_id=a.device_id)
            if a.status != "pending" and a.decided_at is not None \
                    and (now - a.decided_at) > self._KEPT_AFTER_DECISION:
                drop.append(aid)
        for aid in drop:
            del self._items[aid]


# Module-level singleton
_store: ApprovalStore | None = None
_store_lock = threading.Lock()


def get_store() -> ApprovalStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = ApprovalStore()
        return _store


def needs_approval(cap: Capability) -> bool:
    """Does this capability require a desktop-UI approval grant?"""
    return risk_of(cap) == RiskLevel.HIGH
