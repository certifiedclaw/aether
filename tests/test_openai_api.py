import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dashboard import openai_api as oa

TOKEN = "t" * 32


def make(cfg, events=None, raises=None):
    seen = {}

    def stream(messages, tools, **kw):
        seen["messages"], seen["tools"] = messages, tools
        if raises:
            raise raises
        yield from (events or [{"type": "sentence", "text": "Hello there."},
                               {"type": "done", "content": "Hello there."}])

    app = FastAPI()
    app.include_router(oa.build_router(lambda: cfg, stream, lambda: "qwen3:4b"))
    return TestClient(app), seen


ON = {"api_server_enabled": True, "api_server_token": TOKEN}
H = {"Authorization": f"Bearer {TOKEN}"}
BODY = {"messages": [{"role": "user", "content": "hi"}]}


def test_disabled_by_default_is_404():
    c, _ = make({})
    assert c.get("/v1/models", headers=H).status_code == 404
    assert c.post("/v1/chat/completions", json=BODY, headers=H).status_code == 404


def test_short_token_does_not_enable():
    assert not oa.api_enabled({"api_server_enabled": True, "api_server_token": "short"})
    assert oa.api_enabled(ON)
    assert oa.api_enabled({"api_server_enabled": "yes", "api_server_token": TOKEN})


def test_auth_required():
    c, _ = make(ON)
    assert c.get("/v1/models").status_code == 401
    assert c.get("/v1/models", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = c.get("/v1/models", headers=H)
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "qwen3:4b"


def test_lockout_after_repeated_failures():
    c, _ = make(ON)
    for _ in range(oa._MAX_FAILS):
        c.get("/v1/models", headers={"Authorization": "Bearer wrong"})
    assert c.get("/v1/models", headers=H).status_code == 429


def test_chat_completion_and_no_tools_passed():
    c, seen = make(ON)
    r = c.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 200
    j = r.json()
    assert j["choices"][0]["message"] == {"role": "assistant", "content": "Hello there."}
    assert seen["tools"] is None          # clients never get Aether's tools


def test_client_cannot_inject_tools_or_odd_roles():
    c, seen = make(ON)
    body = dict(BODY, tools=[{"type": "function"}])
    assert c.post("/v1/chat/completions", json=body, headers=H).status_code == 200
    assert seen["tools"] is None
    bad = {"messages": [{"role": "tool", "content": "x"}, {"role": "user", "content": "y"}]}
    assert c.post("/v1/chat/completions", json=bad, headers=H).status_code == 400


def test_streaming_sse():
    c, _ = make(ON)
    r = c.post("/v1/chat/completions", json=dict(BODY, stream=True), headers=H)
    assert r.status_code == 200
    assert "Hello there." in r.text and r.text.rstrip().endswith("data: [DONE]")


def test_validation_limits():
    with pytest.raises(ValueError):
        oa.clean_messages([])
    with pytest.raises(ValueError):
        oa.clean_messages([{"role": "assistant", "content": "x"}])
    with pytest.raises(ValueError):
        oa.clean_messages([{"role": "user", "content": "x" * (oa.MAX_CHARS + 1)}])
    with pytest.raises(ValueError):
        oa.clean_messages([{"role": "user", "content": "x"}] * (oa.MAX_MESSAGES + 1))
    got = oa.clean_messages([{"role": "user", "content": [
        {"type": "text", "text": "a"}, {"type": "image_url", "image_url": {"url": "x"}}]}])
    assert got == [{"role": "user", "content": "a"}]


def test_bad_json_and_backend_failure():
    c, _ = make(ON)
    r = c.post("/v1/chat/completions", content=b"{nope", headers={**H, "content-type": "application/json"})
    assert r.status_code == 400
    c2, _ = make(ON, raises=RuntimeError("secret detail"))
    r = c2.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.status_code == 502 and "secret detail" not in r.text


def test_dashboard_mounts_routes_and_serves_them(monkeypatch):
    import dashboard.server as srv
    from core import llm_client as llm
    client = TestClient(srv.DashboardServer().app)
    monkeypatch.setattr(llm, "_load_config", lambda: {})
    r = client.get("/v1/models", headers=H)
    assert r.status_code == 404 and "disabled" in r.text          # mounted, but off
    monkeypatch.setattr(llm, "_load_config", lambda: dict(ON))
    monkeypatch.setattr(llm, "get_llm_settings", lambda: ("http://x", "m:1b"))
    assert client.get("/v1/models").status_code == 401
    r = client.get("/v1/models", headers=H)
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "m:1b"
    events = [{"type": "sentence", "text": "ok."}, {"type": "done", "content": "ok."}]
    monkeypatch.setattr(llm, "call_llm_stream", lambda *a, **k: iter(events))
    r = client.post("/v1/chat/completions", json=BODY, headers=H)
    assert r.json()["choices"][0]["message"]["content"] == "ok."
