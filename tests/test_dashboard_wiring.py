"""The web dashboard must be usable: a way to get a pairing code, replies that reach the
browser, and no blocking work on its event loop."""
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
from dashboard import server as ds


def _dash(monkeypatch, host="127.0.0.1", ssl=False):
    monkeypatch.setenv("AETHER_DASHBOARD_HOST", host)
    monkeypatch.setattr(ds.DashboardServer, "_ssl_enabled", staticmethod(lambda: ssl))
    return ds.DashboardServer()


def test_pairing_info_creates_a_working_one_time_code(monkeypatch):
    d = _dash(monkeypatch)
    info = d.pairing_info()
    assert len(info["code"]) == 6 and info["code"] in d._pending_keys
    assert info["auto_login_url"] == f"{info['url']}/auto-login?key={info['code']}"
    assert info["loopback"] is True and info["https"] is False
    assert d.pairing_info()["code"] != info["code"]        # a fresh code each time


def test_pairing_code_logs_in_through_the_real_route(monkeypatch):
    from fastapi.testclient import TestClient
    d = _dash(monkeypatch)
    code = d.pairing_info()["code"]
    client = TestClient(d.app)
    ok = client.post("/login", json={"pin": code})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert client.post("/login", json={"pin": code}).status_code == 401    # one use only


def test_https_is_reported_so_the_user_knows_to_expect_a_certificate_warning(monkeypatch):
    d = _dash(monkeypatch, host="0.0.0.0", ssl=True)
    assert d.pairing_info()["https"] is True and d.get_url().startswith("https://")


def test_loopback_stays_plain_http_even_when_a_certificate_exists(monkeypatch):
    """A leftover certificate must not turn http://localhost:8000 into an empty response."""
    d = _dash(monkeypatch, host="127.0.0.1", ssl=True)
    info = d.pairing_info()
    assert info["https"] is False and d.get_url().startswith("http://")
    assert d.get_manual_url() == f"{d._ip}:8000"


def test_status_mirror_reports_thinking_so_the_page_can_show_it(monkeypatch):
    d = _dash(monkeypatch)
    sent = []
    ui = type("U", (), {"write_log": lambda self, t, *a, **k: None, "set_state": lambda self, s, *a, **k: None})()
    ds.attach_ui_mirror(d, ui, push=lambda dash, msg: sent.append(msg))
    for state in ("THINKING", "PROCESSING", "LISTENING", "MUTED"):
        ui.set_state(state)
    assert [m["state"] for m in sent] == ["thinking", "thinking", "active", "sleeping"]


# ── replies reach the browser ───────────────────────────────────────────────────
class _FakeUI:
    def __init__(self):
        self.logged, self.states = [], []

    def write_log(self, text):
        self.logged.append(text)

    def set_state(self, state):
        self.states.append(state)


def test_assistant_replies_and_system_lines_are_mirrored_to_the_dashboard():
    ui, sent = _FakeUI(), []
    ds.attach_ui_mirror("dash", ui, push=lambda dash, msg: sent.append(msg))
    ui.write_log("Aether: it is 14C and cloudy")
    ui.write_log("SYS: tool weather_report ok")
    ui.write_log("ERR: web_search failed")
    ui.write_log("You: what's the weather")            # your own line is not echoed back
    assert ui.logged[0] == "Aether: it is 14C and cloudy"      # desktop log unchanged
    assert {"type": "log", "speaker": "aether", "text": "it is 14C and cloudy"} in sent
    assert {"type": "sys", "text": "tool weather_report ok"} in sent
    assert {"type": "sys", "text": "ERR: web_search failed"} in sent
    assert not any("what's the weather" in m["text"] for m in sent)


def test_state_changes_update_the_dashboard_status_pill():
    ui, sent = _FakeUI(), []
    ds.attach_ui_mirror("dash", ui, push=lambda dash, msg: sent.append(msg))
    ui.set_state("LISTENING")
    ui.set_state("MUTED")
    assert sent == [{"type": "status", "state": "active"}, {"type": "status", "state": "sleeping"}]


def test_a_failing_dashboard_never_breaks_the_desktop_log():
    ui = _FakeUI()

    def boom(dash, msg):
        raise RuntimeError("socket closed")

    ds.attach_ui_mirror("dash", ui, push=boom)
    ui.write_log("Aether: hello")
    ui.set_state("LISTENING")
    assert ui.logged == ["Aether: hello"] and ui.states == ["LISTENING"]


# ── /remote command ─────────────────────────────────────────────────────────────
def test_remote_command_prints_address_and_code(monkeypatch):
    from core.chat_commands import ChatCommandsMixin
    d = _dash(monkeypatch)
    host = SimpleNamespace(_dashboard=d, ui=_FakeUI())
    host._format_remote_info = ChatCommandsMixin._format_remote_info
    host._cmd_remote = lambda: ChatCommandsMixin._cmd_remote(host)
    assert ChatCommandsMixin._handle_chat_command(host, "/remote") is True
    text = host.ui.logged[0]
    assert d.get_url() in text and any(c in text for c in d._pending_keys) and "this PC only" in text


def test_remote_command_explains_when_the_dashboard_is_not_running():
    from core.chat_commands import ChatCommandsMixin
    host = SimpleNamespace(_dashboard=None, ui=_FakeUI())
    host._cmd_remote = lambda: ChatCommandsMixin._cmd_remote(host)
    assert ChatCommandsMixin._handle_chat_command(host, "/remote") is True
    assert "not running" in host.ui.logged[0]


# ── the event loop stays free ───────────────────────────────────────────────────
def test_slow_llm_status_does_not_block_other_requests(monkeypatch):
    """/api/llm-status can probe a remote PC for seconds in dual mode; it must run in a thread."""
    import asyncio
    import httpx
    d = _dash(monkeypatch)
    tok = "t" * 20
    d._tokens.add(tok)
    from core import llm_client
    monkeypatch.setattr(llm_client, "get_llm_backend_status", lambda: (time.sleep(1.0), {"mode": "single"})[1])

    async def run():
        transport = httpx.ASGITransport(app=d.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            h = {"Authorization": f"Bearer {tok}"}
            slow = asyncio.create_task(c.get("/api/llm-status", headers=h))
            await asyncio.sleep(0.1)
            start = time.monotonic()
            fast = await c.get("/login")
            fast_s = time.monotonic() - start
            await slow
            return fast.status_code, fast_s

    status, fast_s = asyncio.run(run())
    assert status == 200 and fast_s < 0.5
