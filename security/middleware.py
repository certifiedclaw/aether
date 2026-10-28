"""
security/middleware.py — FastAPI dependencies for capability / session checks.

Use as:

    from fastapi import Depends, APIRouter
    from security import check_capability, Capability, require_session

    router = APIRouter()

    @router.post("/api/control/shell",
                 dependencies=[Depends(require_session),
                               Depends(check_capability(Capability.SHELL))])
    async def shell_endpoint(req: Request):
        ...

Or with the result:

    from security import Capability
    from security.middleware import current_device

    @router.get("/api/me")
    async def me(req: Request):
        device = current_device(req)
        return {"name": device.name, "caps": device.caps}

The middleware writes one audit-log entry per request (sampled; a single
chat text command doesn't need 3 entries, but a capability denial does).
"""

from __future__ import annotations
import functools
import time
from typing import Optional, Callable, Awaitable

from fastapi import Request, HTTPException, Depends
from fastapi.security.utils import get_authorization_scheme_param

from .capabilities import Capability, RiskLevel, risk_of
from .tokens import get_manager, DeviceRecord, SessionRecord
from . import audit


# Context: stash the (DeviceRecord, SessionRecord) on request.state
# FastAPI's `Depends` machinery gives us a place to attach this; we use
# request.state so the rest of the handler can read it without re-parsing.

def _state_key() -> str:
    return "aether_security"


def _client_ip(req: Request) -> str:
    """Best-effort client IP, respecting X-Forwarded-For when behind a tunnel.

    Order of preference:
      1. X-Forwarded-For (leftmost) — only trust this when running behind
         Cloudflare / ngrok, which set it. In a direct LAN setup, a
         malicious client could spoof it; that's acceptable for a single-
         user home assistant.
      2. req.client.host (the socket peer)
    """
    xff = req.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    if req.client and req.client.host:
        return req.client.host
    return "0.0.0.0"


# Public dependency: require_session
async def require_session(req: Request) -> tuple[DeviceRecord, SessionRecord]:
    """Dependency: 401 if no/invalid session, else attach device to request."""
    auth = req.headers.get("authorization", "")
    scheme, token = get_authorization_scheme_param(auth)
    if scheme.lower() != "bearer" or not token:
        # sessionStorage tokens are sent in headers by the PWA / RN app.
        # If a request is from a browser <a> or <img>, the token is in
        # ?token= instead — handled per-endpoint.
        raise HTTPException(status_code=401, detail="Missing bearer token")
    mgr = get_manager()
    result = mgr.validate_session(token)
    if result is None:
        audit("session.expired", ip=_client_ip(req))
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    device, session = result
    req.state[_state_key()] = (device, session)
    return device, session


def current_device(req: Request) -> DeviceRecord:
    """Read the device attached by require_session. Raises if missing."""
    state = getattr(req.state, _state_key(), None)
    if not state:
        raise HTTPException(status_code=500, detail="Security state missing")
    return state[0]


# Public dependency factory: check_capability
def check_capability(cap: Capability) -> Callable[[Request], Awaitable[None]]:
    """Dependency factory: 403 if the device's token doesn't include `cap`."""
    async def _checker(req: Request) -> None:
        # ensure session was validated first
        if not hasattr(req.state, _state_key()):
            # Try the header-only path; if no header, fail
            try:
                await require_session(req)
            except HTTPException:
                audit("session.expired",
                      ip=_client_ip(req), attempted_cap=cap.value)
                raise
        device = current_device(req)
        if not device.has_cap(cap):
            audit("cap.denied",
                  ip=_client_ip(req), device_id=device.device_id,
                  capability=cap.value, reason="not_in_token")
            raise HTTPException(
                status_code=403,
                detail=f"Device lacks capability: {cap.value}",
            )
        # Audit the granted check only for MEDIUM/HIGH risk; LOW would flood
        # the log on every chat message. Each device's last_seen is updated
        # by validate_session, which is sufficient.
        if risk_of(cap) in (RiskLevel.MEDIUM, RiskLevel.HIGH):
            audit("cap.granted",
                  ip=_client_ip(req), device_id=device.device_id,
                  capability=cap.value)
    return _checker


# High-risk approval gate
async def require_approval(req: Request, cap: Capability) -> str:
    """For HIGH-risk caps: enforce that an approval has been granted within
    the last 60s. Returns the approval_id. If the request body has a
    pre-existing approval_id, validate it; otherwise create a new one and
    return a 202 signal.

    Usage in an endpoint:

        @router.post("/api/control/shell",
                     dependencies=[Depends(require_session),
                                   Depends(check_capability(Capability.SHELL))])
        async def shell(req: Request):
            approval_id = await require_approval(req, Capability.SHELL)
            # ... forward to Aether actions layer
    """
    from .policy import get_store, needs_approval
    if not needs_approval(cap):
        return ""   # no approval needed; placeholder

    body: dict = {}
    try:
        body = await req.json()
    except Exception:
        body = {}

    store = get_store()
    provided = (body.get("approval_id") or "").strip()
    if provided:
        a = store.get(provided)
        if not a:
            audit("cap.denied", ip=_client_ip(req), capability=cap.value,
                  reason="unknown_approval_id")
            raise HTTPException(status_code=403, detail="Unknown approval_id")
        if a.status != "approved":
            audit("cap.denied", ip=_client_ip(req), capability=cap.value,
                  approval_id=provided, status=a.status)
            raise HTTPException(
                status_code=403,
                detail=f"Approval status: {a.status}",
            )
        return provided

    # No approval_id provided → create one and signal 202.
    device = current_device(req)
    summary = _summarize(cap, body)
    endpoint = f"{req.method} {req.url.path}"
    a = store.create(
        device_id=device.device_id, cap=cap, summary=summary,
        payload=body, endpoint=endpoint,
    )
    audit("cap.requested", ip=_client_ip(req), device_id=device.device_id,
          capability=cap.value, approval_id=a.approval_id,
          summary=summary, expires_in=int(a.expires - a.created))
    # 202 Accepted — the caller polls GET /api/approvals/{id}
    raise HTTPException(
        status_code=202,
        detail={
            "approval_id": a.approval_id,
            "expires_in": int(a.expires - a.created),
            "summary": summary,
            "endpoint": endpoint,
            "poll_url": f"/api/approvals/{a.approval_id}",
        },
    )


def _summarize(cap: Capability, body: dict) -> str:
    """Make a short, human-readable summary for the desktop-UI prompt."""
    if cap == Capability.SHELL:
        cmd = str(body.get("cmd", "")).strip()
        return f"shell: {cmd[:80]}" if cmd else "shell: <empty>"
    if cap == Capability.COMPUTER_CONTROL:
        action = str(body.get("action", "")).strip()
        return f"computer-control: {action[:80]}" if action else "computer-control: <no action>"
    if cap == Capability.PLUGIN_EXEC:
        plugin = str(body.get("plugin", "")).strip()
        return f"plugin-exec: {plugin[:80]}" if plugin else "plugin-exec: <no plugin>"
    if cap == Capability.MEMORY_WRITE:
        return "memory-write"
    return f"{cap.value}"
