import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from security.capabilities import Capability
from security.middleware import require_approval
from security.policy import ApprovalStore


def make_request(body, *, path="/api/control/shell", device_id="device-a"):
    encoded = json.dumps(body).encode()
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "state": {"aether_security": (type("Device", (), {"device_id": device_id})(), None)},
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request(scope, receive)


@pytest.mark.parametrize("mismatch", ["device", "capability", "endpoint", "payload"])
@pytest.mark.asyncio
async def test_approved_id_is_rejected_for_different_request(monkeypatch, mismatch):
    store = ApprovalStore()
    params = {"cmd": "dir"}
    approval = store.create(
        device_id="device-a", cap=Capability.SHELL, summary="shell: dir",
        payload=params, endpoint="POST /api/control/shell",
    )
    store.decide(approval.approval_id, "approved")
    monkeypatch.setattr("security.policy.get_store", lambda: store)
    request_params = dict(params)
    path = "/api/control/shell"
    device_id = "device-a"
    cap = Capability.SHELL
    if mismatch == "device":
        device_id = "device-b"
    elif mismatch == "capability":
        cap = Capability.COMPUTER_CONTROL
    elif mismatch == "endpoint":
        path = "/api/control/other"
    else:
        request_params["cmd"] = "del *"

    with pytest.raises(HTTPException) as error:
        await require_approval(
            make_request({**request_params, "approval_id": approval.approval_id},
                         path=path, device_id=device_id), cap
        )
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_approved_id_allows_exact_bound_request(monkeypatch):
    store = ApprovalStore()
    params = {"cmd": "dir"}
    approval = store.create(
        device_id="device-a", cap=Capability.SHELL, summary="shell: dir",
        payload=params, endpoint="POST /api/control/shell",
    )
    store.decide(approval.approval_id, "approved")
    monkeypatch.setattr("security.policy.get_store", lambda: store)

    result = await require_approval(
        make_request({**params, "approval_id": approval.approval_id}),
        Capability.SHELL,
    )
    assert result == approval.approval_id


def test_dashboard_lists_and_resolves_secure_approval(monkeypatch):
    from fastapi.testclient import TestClient
    from dashboard.server import DashboardServer

    store = ApprovalStore()
    approval = store.create(
        device_id="device-a", cap=Capability.SHELL, summary="shell: dir",
        payload={"cmd": "dir"}, endpoint="POST /api/control/shell",
    )
    monkeypatch.setattr("security.policy.get_store", lambda: store)
    dashboard = DashboardServer()
    dashboard._add_token("operator", "session-key")
    headers = {"Authorization": "Bearer operator"}

    with TestClient(dashboard.app) as client:
        listed = client.get("/api/approvals", headers=headers)
        assert listed.status_code == 200
        assert any(item["approval_id"] == approval.approval_id
                   for item in listed.json()["approvals"])

        decided = client.post(
            f"/api/approvals/{approval.approval_id}/approve", headers=headers
        )
        repeated = client.post(
            f"/api/approvals/{approval.approval_id}/approve", headers=headers
        )

    assert decided.status_code == 200
    assert repeated.status_code == 404
    assert store.get(approval.approval_id).status == "approved"


@pytest.mark.asyncio
async def test_202_poll_url_exists_on_the_secure_router(monkeypatch):
    """The URL handed to a remote device must exist where the router is mounted
    (dashboard/server.py mounts it at /api/secure): 401 (needs a session), never 404."""
    import httpx
    from fastapi import FastAPI
    from dashboard.api_secure import router
    app = FastAPI()
    app.include_router(router, prefix="/api/secure")
    monkeypatch.setattr("security.policy.get_store", lambda: ApprovalStore())
    with pytest.raises(HTTPException) as error:
        await require_approval(make_request({"cmd": "dir"}), Capability.SHELL)
    assert error.value.status_code == 202
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        reply = await client.get(error.value.detail["poll_url"])
    assert reply.status_code == 401
