"""
pending_approvals.py — In-memory store for headless remote-approval requests.

When core.headless_ui.HeadlessUI.confirm_blocking() is called (gating
high-risk actions -- shell exec, file delete, port scan, breach lookups;
see core/safety.py's _UNCONDITIONAL_HIGH_RISK / _DEFAULT_HIGH_RISK), there's
no GUI to click "yes" on in headless mode. This store lets that call
register a pending request, notify the operator (ntfy push, see
core/notify.py), and block until the dashboard resolves it via
POST /api/approvals/{id}/approve|deny -- or the timeout is hit, at which
point it fails closed (denied), same as headless mode's prior no-remote-
approval behavior.

In-memory only (no persistence across restarts) -- fine for the current
scope: an approval is only meaningful while the calling thread is still
blocked waiting on it, and a process restart already invalidates that
wait regardless of where the state lived.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class Approval:
    id: str
    message: str
    created_at: float
    timeout: float
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    result: bool | None = None
    resolved_at: float | None = None

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "id": self.id,
            "message": self.message,
            "created_at": self.created_at,
            "timeout": self.timeout,
            "seconds_remaining": max(0.0, (self.created_at + self.timeout) - now),
            "resolved": self.result is not None,
            "result": self.result,
        }


class ApprovalStore:
    """Thread-safe. One process-wide instance via get_approval_store()."""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._approvals: dict[str, Approval] = {}

    def create(self, message: str, timeout: float = 120.0) -> Approval:
        approval = Approval(id=uuid.uuid4().hex, message=message,
                             created_at=time.time(), timeout=timeout)
        with self._lock:
            self._approvals[approval.id] = approval
        return approval

    def list_pending(self) -> list[dict]:
        """Unresolved approvals that haven't yet timed out -- what the
        dashboard's GET /api/approvals shows."""
        now = time.time()
        with self._lock:
            items = list(self._approvals.values())
        return [a.to_dict() for a in items
                if a.result is None and (a.created_at + a.timeout) > now]

    def get(self, approval_id: str) -> Approval | None:
        with self._lock:
            return self._approvals.get(approval_id)

    def resolve(self, approval_id: str, approved: bool) -> bool:
        """Called from the dashboard's approve/deny endpoints. Returns
        False if the approval doesn't exist or was already resolved
        (including by its own timeout)."""
        with self._lock:
            now = time.time()
            approval = self._approvals.get(approval_id)
            if approval is None or approval.result is not None:
                return False
            if approval.created_at + approval.timeout <= now:
                approval.result = False
                approval.resolved_at = now
                expired = True
            else:
                approval.result = approved
                approval.resolved_at = now
                expired = False
        approval.event.set()
        return not expired

    def wait(self, approval_id: str, timeout: float) -> bool:
        """Blocks the calling thread until resolve() is called or timeout
        elapses. Fails closed (returns False) on timeout, and records that
        denial so a late dashboard click against an already-timed-out
        approval reports "already resolved" rather than silently no-op'ing.
        Removes the entry once settled either way, so the store doesn't
        grow unbounded across a long-running process."""
        approval = self.get(approval_id)
        if approval is None:
            return False
        approval.event.wait(timeout=timeout)
        with self._lock:
            result = approval.result
            if result is None:
                approval.result = False
                approval.resolved_at = time.time()
                result = False
            self._approvals.pop(approval_id, None)
        return bool(result)


_store: ApprovalStore | None = None
_store_lock = threading.Lock()


def get_approval_store() -> ApprovalStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ApprovalStore()
    return _store
