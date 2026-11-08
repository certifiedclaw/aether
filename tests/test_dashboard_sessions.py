"""Dashboard bearer-token sessions, device revocation, approvals endpoints, firewall helper."""
import sys
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")
from dashboard import server as srv  # noqa: E402


def _server():
    return srv.DashboardServer()


def test_revoke_devices_kills_tokens_minted_from_devices():
    d = _server()
    d._add_token("dev-tok", "k1", device="dev-1")
    d._add_token("pair-tok", "k2")                    # pairing-code session, no device
    d._device_sessions["dev-1"] = {"session_key": "k1"}
    count, _ = d._revoke_device_sessions()
    assert count == 1
    assert not d._token_valid("dev-tok")
    assert "dev-tok" not in d._sessions
    assert d._token_valid("pair-tok")                  # untouched
    assert d._decrypt("dev-tok", "x") is None


def test_revoked_token_gets_401_over_http_and_no_ws_ticket():
    from fastapi.testclient import TestClient
    d = _server()
    d._add_token("dev-tok", "k1", device="dev-1")
    d._add_token("admin", "k2")
    d._device_sessions["dev-1"] = {"session_key": "k1"}
    with TestClient(d.app) as c:
        ok = c.post("/api/revoke-devices", headers={"Authorization": "Bearer admin"})
        assert ok.status_code == 200 and ok.json()["revoked"] == 1
        again = c.post("/api/revoke-devices", headers={"Authorization": "Bearer dev-tok"})
        assert again.status_code == 401
    assert d._issue_ws_ticket("dev-tok", "main") is None


def test_tokens_expire(monkeypatch):
    d = _server()
    d._add_token("t", "k")
    assert d._token_valid("t")
    real = time.monotonic
    monkeypatch.setattr(srv.time, "monotonic", lambda: real() + srv.TOKEN_TTL + 1)
    assert not d._token_valid("t")
    assert "t" not in d._sessions


def test_ticket_issued_before_expiry_is_rejected_after(monkeypatch):
    d = _server()
    d._add_token("t", "k")
    ticket = d._issue_ws_ticket("t", "main")
    assert ticket
    d._sessions.pop("t")
    assert d._consume_ws_ticket(ticket, "main") is None


def test_windows_firewall_commands_are_scoped_and_never_reclassify():
    from dashboard.firewall import _windows_firewall_commands
    cmds = _windows_firewall_commands(8765, scoped_exists=False,
                                      legacy_port_exists=True, legacy_prog_exists=True)
    text = "\n".join(cmds)
    assert "Set-NetConnectionProfile" not in text and "program=" not in text
    add = [c for c in cmds if " add rule" in c]
    assert len(add) == 1
    assert "profile=private,domain" in add[0] and "remoteip=LocalSubnet" in add[0]
    assert "localport=8765" in add[0]
    assert any("delete rule" in c and "Python" in c for c in cmds)      # old broad rule removed
    assert _windows_firewall_commands(8765, True, False, False) == []   # idempotent


def test_windows_setup_script_never_contains_profile_change_or_program_rule(monkeypatch):
    """Drive the real function with subprocess/ctypes mocked and read the .bat it writes."""
    import codecs, subprocess, types
    from dashboard import firewall as s
    seen = {}
    codecs.register(lambda n: codecs.lookup("latin-1") if n == "mbcs" else None)   # Windows-only codec

    def fake_run(cmd, *a, **k):
        if isinstance(cmd, list) and cmd and str(cmd[0]).endswith(".bat"):
            seen["bat"] = open(cmd[0], "rb").read().decode("mbcs" if sys.platform == "win32" else "latin-1")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        # netsh "show rule" -> nothing exists; powershell public check -> 1 public network
        out = "1" if cmd and cmd[0] == "powershell" else "No rules match"
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setitem(sys.modules, "ctypes", types.SimpleNamespace(windll=None))
    s._ensure_network_access_windows(8765)
    bat = seen["bat"]
    assert "Set-NetConnectionProfile" not in bat and "program=" not in bat
    assert "profile=private,domain" in bat and "remoteip=" in bat


def test_revoke_closes_phone_audio_socket_too():
    import contextlib
    from fastapi.testclient import TestClient
    d = _server()
    d._add_token("dev-tok", "k1", device="dev-1")
    d._add_token("admin", "k2")
    d._device_sessions["dev-1"] = {"session_key": "k1"}
    closed = False
    with TestClient(d.app) as c:
        ticket = d._issue_ws_ticket("dev-tok", "phone-audio")
        with contextlib.suppress(Exception):          # tearing down a closed socket may raise
            with c.websocket_connect(f"/ws/phone-audio?ticket={ticket}") as ws:
                ws.send_bytes(b"\x00\x01")
                assert c.post("/api/revoke-devices",
                              headers={"Authorization": "Bearer admin"}).status_code == 200
                for _ in range(40):                   # bounded: never blocks on the old behaviour
                    try:
                        ws.send_bytes(b"\x00\x01")
                    except Exception:
                        closed = True
                        break
                    time.sleep(0.05)
    assert closed, "server did not close the phone-audio socket after revoke"


def test_dashboard_resolves_remote_capability_approvals_and_records_who():
    from fastapi.testclient import TestClient
    from security.policy import get_store, Capability
    d = _server()
    d._add_token("admin", "k")
    a = get_store().create(device_id="phone-1", cap=Capability.SHELL, summary="shell: ls",
                           payload={"cmd": "ls"}, endpoint="POST /api/control/shell")
    with TestClient(d.app) as c:
        h = {"Authorization": "Bearer admin"}
        assert c.post(f"/api/approvals/{a.approval_id}/approve", headers=h).status_code == 200
        assert get_store().get(a.approval_id).decided_by == "dashboard"
        assert c.post(f"/api/approvals/{a.approval_id}/deny", headers=h).status_code == 404  # already resolved
        assert c.post("/api/approvals/nope/approve").status_code == 401
