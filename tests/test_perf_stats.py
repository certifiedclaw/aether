import json
import time

import pytest

from core import perf_stats as ps


@pytest.fixture
def log(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "_cfg", lambda: {})
    return tmp_path / "p.jsonl"


def rec(log, model="qwen3:4b", route="local", pt=100, ot=50, ev=5e9, el=6.0):
    ps.record(model, route, pt, ot, ev, el, path=log)


def test_record_computes_tokens_per_second_from_eval_duration(log):
    rec(log, ot=50, ev=5e9, el=9.0)                     # 50 tokens / 5 s
    e = ps.read(path=log)[0]
    assert e["tps"] == 10.0 and e["elapsed_s"] == 9.0 and e["route"] == "local"


def test_tps_falls_back_to_elapsed_and_handles_missing_numbers(log):
    rec(log, ot=60, ev=None, el=6.0)
    assert ps.read(path=log)[0]["tps"] == 10.0
    rec(log, ot=None, ev=None, el=None, pt=None)
    last = ps.read(path=log)[-1]
    assert last["tps"] is None and last["output_tokens"] is None
    ps.record("m", "local", "junk", -5, True, "x", path=log)     # junk values never raise
    assert len(ps.read(path=log)) == 3


def test_no_message_text_is_stored(log):
    rec(log)
    assert set(ps.read(path=log)[0]) == {"ts", "model", "route", "prompt_tokens", "output_tokens", "elapsed_s", "tps"}


def test_disabled_by_config(log, monkeypatch):
    monkeypatch.setattr(ps, "_cfg", lambda: {"perf_log": False})
    rec(log)
    assert not log.exists()
    monkeypatch.setattr(ps, "_cfg", lambda: {"perf_log": "off"})
    rec(log)
    assert not log.exists()


def test_summarize_per_model_and_cloud_days(log):
    for el in (1.0, 2.0, 3.0, 4.0, 10.0):
        rec(log, el=el, ot=10, ev=1e9)
    rec(log, model="gemini-flash", route="cloud", pt=1000, ot=500, ev=None, el=2.0)
    s = ps.summarize(ps.read(path=log))
    by = {r["model"]: r for r in s["models"]}
    local = by["qwen3:4b (local)"]
    assert local["rounds"] == 5 and local["p50_s"] == 3.0 and local["p95_s"] == 10.0
    assert local["avg_tps"] == 10.0 and local["prompt_tokens"] == 500
    today = time.strftime("%Y-%m-%d")
    assert s["cloud_tokens_by_day"][today] == 1500
    assert s["models"][0]["model"] == "qwen3:4b (local)"        # most-used first


def test_summarize_empty():
    assert ps.summarize([]) == {"models": [], "cloud_tokens_by_day": {}}


def test_since_filter_and_corrupt_lines(log):
    rec(log)
    with open(log, "a") as f:
        f.write("garbage\n")
    assert len(ps.read(path=log)) == 1
    assert ps.read(since=time.time() + 100, path=log) == []


def test_rotation(log, monkeypatch):
    monkeypatch.setattr(ps, "MAX_BYTES", 300)
    for _ in range(20):
        rec(log)
    assert any(p.name.startswith("p.jsonl.") for p in log.parent.iterdir())
    assert len(ps.read(path=log)) >= 5


def test_budget_warning():
    day = "2026-10-16"
    s = {"cloud_tokens_by_day": {day: 850}}
    assert ps.budget_warning(s, {}, day) == ""                                   # no budget set
    assert "80%" in ps.budget_warning(s, {"cloud_daily_token_budget": 1000}, day)
    assert "over" in ps.budget_warning(s, {"cloud_daily_token_budget": 800}, day)
    assert ps.budget_warning(s, {"cloud_daily_token_budget": 5000}, day) == ""
    assert ps.budget_warning(s, {"cloud_daily_token_budget": "junk"}, day) == ""


def test_memory_snapshot_uses_ollama_ps():
    snap = ps.memory_snapshot(fetch=lambda: {"models": [
        {"name": "qwen3:4b", "size": 5 * 1048576, "size_vram": 0},
        {"name": "x", "size": 8 * 1048576, "size_vram": 6 * 1048576}]})
    assert snap["loaded"][0] == {"model": "qwen3:4b", "size_mb": 5, "gpu_mb": 0}
    assert snap["loaded"][1]["gpu_mb"] == 6
    bad = ps.memory_snapshot(fetch=lambda: (_ for _ in ()).throw(OSError("down")))
    assert bad["loaded"] == []                                                    # Ollama down is fine


def test_route_for(monkeypatch):
    from core import llm_client as lc
    monkeypatch.setattr(lc, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(lc, "get_llm_backend_status", lambda: {"mode": "single", "active": None})
    assert ps.route_for({}) == "local"
    assert ps.route_for({"provider_override": "gemini"}) == "cloud"
    monkeypatch.setattr(lc, "get_llm_backend_status", lambda: {"mode": "dual", "active": "remote"})
    assert ps.route_for(None) == "remote"
    monkeypatch.setattr(lc, "get_llm_provider", lambda: "gemini")
    assert ps.route_for(None) == "cloud"


def test_render_text(log):
    rec(log)
    rep = {"hours": 24.0, "summary": ps.summarize(ps.read(path=log)), "warning": "careful",
           "memory": {"process_mb": 200, "host_free_mb": 4000, "host_total_mb": 16000,
                      "loaded": [{"model": "m", "size_mb": 900, "gpu_mb": 0}]}}
    out = ps.render(rep)
    assert "qwen3:4b (local)" in out and "10.0 tok/s" in out and "careful" in out and "CPU/RAM" in out
    assert "no model rounds" in ps.render({"hours": 1.0, "summary": ps.summarize([]), "warning": "",
                                           "memory": {"process_mb": None, "loaded": []}})


def test_dashboard_perf_endpoint(monkeypatch, log):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    import dashboard.server as srv
    monkeypatch.setattr(ps, "LOG_FILE", log)
    monkeypatch.setattr(ps, "memory_snapshot", lambda fetch=None: {"process_mb": 1, "host_free_mb": 2, "host_total_mb": 3, "loaded": []})
    rec(log)
    ds = srv.DashboardServer()
    c = TestClient(ds.app)
    assert c.get("/api/perf").status_code == 401
    ds._tokens.add("tok")
    r = c.get("/api/perf?hours=junk", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200 and r.json()["summary"]["models"][0]["rounds"] == 1
    assert "Performance" in c.get("/perf").text
