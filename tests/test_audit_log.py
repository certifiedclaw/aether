import json
import time

import pytest

from core import audit_log as al


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "_cfg", lambda: {})
    al._last_hash.clear()
    return tmp_path / "a.jsonl"


def rec(log, tool="file_controller", action="delete", tier="ask", decision="approved", **kw):
    al.record(tool, action, tier, decision, kw.pop("params", {"path": "C:/x"}), path=log, **kw)


def test_record_and_read_newest_first(log):
    rec(log, decision="approved")
    rec(log, tool="browser_control", action="open", tier="task", decision="denied")
    got = al.read(path=log)
    assert [e["tool"] for e in got] == ["browser_control", "file_controller"]
    assert got[0]["decision"] == "denied"


def test_filters(log):
    rec(log)
    rec(log, tool="computer_control", tier="task", decision="allowed")
    assert len(al.read(path=log, tool="computer_control")) == 1
    assert len(al.read(path=log, decision="approved")) == 1
    assert len(al.read(path=log, tier="task")) == 1
    assert len(al.read(path=log, text="C:/X")) == 2          # case-insensitive text search
    assert al.read(path=log, since=time.time() + 60) == []
    assert len(al.read(path=log, limit=1)) == 1


def test_secrets_are_redacted_and_values_truncated(log):
    rec(log, params={"api_key": "sk-live-abcdefghijklmnop", "note": "key sk-abcdefghijklmnopqrstuvwx here",
                     "text": "x" * 1000})
    raw = log.read_text()
    assert "abcdefghijklmnop" not in raw
    e = al.read(path=log)[0]
    assert e["params"]["api_key"] == "(hidden)"
    assert "chars]" in e["params"]["text"] and len(e["params"]["text"]) < 400


def test_safe_tier_skipped_unless_enabled(log, monkeypatch):
    rec(log, tier="safe", decision="allowed")
    assert not log.exists()
    monkeypatch.setattr(al, "_cfg", lambda: {"audit_log_safe": True})
    rec(log, tier="safe", decision="allowed")
    assert len(al.read(path=log)) == 1


def test_can_be_turned_off(log, monkeypatch):
    monkeypatch.setattr(al, "_cfg", lambda: {"audit_log": False})
    rec(log)
    assert not log.exists()


def test_chain_verifies_and_detects_edit_and_deletion(log):
    for i in range(4):
        rec(log, params={"i": i})
    assert al.verify(log) == (True, "4 entries, chain intact")
    lines = log.read_text().splitlines()
    # edit an entry
    e = json.loads(lines[1]); e["decision"] = "approved"; e["tool"] = "evil"
    tampered = lines[:1] + [json.dumps(e, separators=(",", ":"))] + lines[2:]
    log.write_text("\n".join(tampered) + "\n")
    ok, msg = al.verify(log)
    assert not ok and "chain broken" in msg
    # delete an entry
    log.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert al.verify(log)[0] is False


def test_chain_continues_after_restart(log):
    rec(log); rec(log)
    al._last_hash.clear()                  # simulate a new process
    rec(log)
    assert al.verify(log)[0] is True


def test_empty_log_verifies(log):
    assert al.verify(log) == (True, "no entries")
    assert al.read(path=log) == []


def test_rotation_keeps_chain(log, monkeypatch):
    monkeypatch.setattr(al, "MAX_BYTES", 400)
    for i in range(12):
        rec(log, params={"i": i, "pad": "p" * 40})
    assert any(p.name.startswith("a.jsonl.") for p in log.parent.iterdir())
    ok, msg = al.verify(log)
    assert ok, msg
    assert len(al.read(path=log, limit=500)) >= 6


def test_corrupt_lines_are_skipped_by_read_but_flagged_by_verify(log):
    rec(log)
    with open(log, "a") as f:
        f.write("not json\n")
    assert len(al.read(path=log)) == 1
    assert al.verify(log)[0] is False


def test_record_never_raises(log, monkeypatch):
    monkeypatch.setattr(al, "_cfg", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    al.record("t", "a", "ask", "approved", {}, path=log)       # must not raise


def test_parse_query_and_render(log):
    q = al.parse_query("50 tool=file_controller decision=denied hours=2 delete notes")
    assert q["limit"] == 50 and q["tool"] == "file_controller" and q["decision"] == "denied"
    assert q["text"] == "delete notes" and q["since"] < time.time()
    assert al.parse_query("")["limit"] == 20
    rec(log)
    out = al.render(al.read(path=log))
    assert "file_controller.delete" in out and "approved" in out
    assert "no matching" in al.render([])


# ── integration with the risk gate ────────────────────────────────────────────
def test_risk_gate_records_decisions(monkeypatch):
    from core import risk
    risk.reset_state()
    monkeypatch.setattr(al, "_cfg", lambda: {})
    import core.safety as safety
    assert risk.tier_for("send_message", "") == "ask"
    monkeypatch.setattr(safety, "confirm_action", lambda *a, **k: True)
    assert risk.check("send_message", "", {"to": "a"}) is None
    monkeypatch.setattr(safety, "confirm_action", lambda *a, **k: False)
    assert "Cancelled" in risk.check("send_message", "", {"to": "b"})
    assert risk.check("extra_tool", "run_python", {"code": "print(1)"}) is not None
    decisions = [(e["tool"], e["decision"]) for e in al.read()]
    assert ("send_message", "approved") in decisions
    assert ("send_message", "denied") in decisions
    assert ("extra_tool", "blocked") in decisions
    assert al.verify()[0] is True


# ── dashboard ─────────────────────────────────────────────────────────────────
def test_dashboard_audit_endpoint_requires_auth_and_filters(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import dashboard.server as srv
    monkeypatch.setattr(al, "_cfg", lambda: {})
    al.record("file_controller", "delete", "ask", "approved", {"path": "a"})
    al.record("browser_control", "open", "task", "denied", {"url": "b"})
    ds = srv.DashboardServer()
    c = TestClient(ds.app)
    assert c.get("/api/audit").status_code == 401
    ds._tokens.add("tok")
    h = {"Authorization": "Bearer tok"}
    r = c.get("/api/audit?decision=denied", headers=h)
    assert r.status_code == 200
    j = r.json()
    assert [e["tool"] for e in j["entries"]] == ["browser_control"]
    assert j["chain"]["ok"] is True and j["summary"]["count"] == 1
    assert c.get("/api/audit?hours=junk&limit=junk", headers=h).status_code == 200
    page = c.get("/audit")
    assert page.status_code == 200 and "Audit log" in page.text
