"""
dashboard/api_secure.py — Stage 2 of the remote-access build (see HANDOFF.md #3).

This is the new, capability-gated router that `security/__init__.py`'s
docstring promises: "mounted as a FastAPI router by the one opt-in line in
dashboard/server.py's _build_app(), guarded by env var AETHER_SECURE_ROUTER=1".

It is intentionally independent of Aether's existing dashboard auth
(the AES-256-CBC session-key scheme in dashboard/server.py) — that scheme
is untouched. This router lives entirely under /api/secure/* and uses its
own bearer-token sessions from security/tokens.py.

Scope of this first cut — deliberately narrow:
    POST /pair                      — consume a PIN, register a new remote
                                       device, get back a session token
    POST /reauth                    — trade a device_token for a fresh
                                       session token (no PIN needed)
    GET  /me                        — identify the calling device + its caps
    GET  /approvals/{approval_id}   — poll a HIGH-risk request you made

What's deliberately NOT here yet: device management (list/revoke/grant),
and deciding (approve/deny) a pending approval. Per capabilities.py's own
docstring, the desktop UI is "a separate, always-trusted device... because
the user is literally sitting in front of it" — i.e. those actions belong
in-process inside the PyQt6 app (ui.py) calling security.tokens /
security.policy directly, not over HTTP. There is no documented flow for a
remote device to ever obtain Capability.ADMIN, so an HTTP endpoint gated on
it today would be dead code guarding nothing -- worse, an easy thing to
wire up wrong later. Left for a follow-up once the desktop-UI side of that
wiring exists to review against.

Pairing/reauth are rate-limited using the same per-IP counters
security/tokens.py already tracks for exactly this purpose.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel, Field

from security.tokens import get_manager
from security.middleware import require_session, _client_ip
from security.policy import get_store
from security import audit


router = APIRouter()


# ── request bodies ─────────────────────────────────────────────────────
class PairRequest(BaseModel):
    pin: str = Field(min_length=1, max_length=12)
    device_name: str = Field(default="unnamed device", max_length=64)


class ReauthRequest(BaseModel):
    device_token: str = Field(min_length=1, max_length=256)


# ── pairing: no session required -- this is how a device gets one ──────
@router.post("/pair")
async def pair(req: Request, body: PairRequest):
    mgr = get_manager()
    ip = _client_ip(req)

    if mgr.is_ip_locked(ip) or mgr.is_rate_limited(ip):
        audit("pair.denied", ip=ip, reason="rate_limited")
        raise HTTPException(status_code=429, detail="Too many attempts, try again later")

    ok = mgr.consume_pin(body.pin.strip().upper())
    mgr.record_attempt(ip, success=ok)
    if not ok:
        audit("pair.denied", ip=ip, reason="bad_pin")
        raise HTTPException(status_code=403, detail="Invalid or expired PIN")

    try:
        result = mgr.pair_device(body.device_name.strip() or "unnamed device")
    except RuntimeError as e:
        # e.g. device cap reached
        audit("pair.denied", ip=ip, reason=str(e))
        raise HTTPException(status_code=409, detail=str(e))

    audit("pair.success", ip=ip, device_id=result["device_id"])
    return result


@router.post("/reauth")
async def reauth(req: Request, body: ReauthRequest):
    mgr = get_manager()
    ip = _client_ip(req)

    if mgr.is_rate_limited(ip):
        audit("reauth.denied", ip=ip, reason="rate_limited")
        raise HTTPException(status_code=429, detail="Too many attempts, try again later")

    result = mgr.reauth_device(body.device_token)
    mgr.record_attempt(ip, success=result is not None)
    if result is None:
        audit("reauth.denied", ip=ip, reason="unknown_or_locked_token")
        raise HTTPException(status_code=401, detail="Unknown or locked device token")
    return result


# ── identity ─────────────────────────────────────────────────────────
@router.get("/me")
async def me(session=Depends(require_session)):
    device, sess = session
    return {
        "device_id": device.device_id,
        "name": device.name,
        "caps": device.caps,
        "session_expires": sess.expires,
    }


# ── approvals: a device polls the outcome of its own HIGH-risk request ─
@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, session=Depends(require_session)):
    device, _sess = session
    a = get_store().get(approval_id)
    if not a or a.device_id != device.device_id:
        # Same 404 whether it doesn't exist or belongs to someone else --
        # don't leak which.
        raise HTTPException(status_code=404, detail="Unknown approval")
    return a.to_json()
