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
    assert set(ps.read(path=log)[0]) == {"ts", "model", "route", "prompt_tokens", "output_tokens",
                                         "elapsed_s", "ttft_s", "tps"}


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


def test_time_to_first_token_recorded_summarized_and_rendered(log):
    for tt in (0.2, 0.4, 0.6, 3.0):
        ps.record("m", "local", 10, 10, 1e9, 5.0, path=log, ttft_s=tt)
    ps.record("m", "local", 10, 10, 1e9, 5.0, path=log)                 # round with no first-token reading
    ps.record("m", "local", 10, 10, 1e9, 5.0, path=log, ttft_s="junk")
    entries = ps.read(path=log)
    assert entries[0]["ttft_s"] == 0.2 and entries[4]["ttft_s"] is None and entries[5]["ttft_s"] is None
    row = ps.summarize(entries)["models"][0]
    assert row["ttft_p50_s"] == 0.6 and row["ttft_p95_s"] == 3.0        # unknowns are ignored, not counted as 0
    rep = {"hours": 24.0, "summary": ps.summarize(entries), "warning": "",
           "memory": {"process_mb": None, "loaded": []}}
    assert "first token 0.6s (p95 3.0s)" in ps.render(rep)
    no_tt = ps.summarize([{"ts": time.time(), "model": "x", "route": "local", "elapsed_s": 1, "tps": 5}])
    assert no_tt["models"][0]["ttft_p50_s"] is None


def test_ollama_stream_reports_ttft(monkeypatch):
    """Real stream parsing: ttft is measured from request start to the first content chunk."""
    from core import llm_client as lc
    import json as _json

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            yield _json.dumps({"message": {"content": ""}, "done": False}).encode()      # empty: not a first token
            time.sleep(0.05)
            yield _json.dumps({"message": {"content": "Hello there."}, "done": False}).encode()
            yield _json.dumps({"message": {"content": ""}, "done": True, "eval_count": 3,
                               "eval_duration": 1_000_000_000}).encode()

    monkeypatch.setattr(lc, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(lc, "get_llm_settings", lambda: ("http://x", "m"))
    monkeypatch.setattr(lc.requests, "post", lambda *a, **k: Resp())
    done = [e for e in lc.call_llm_stream([{"role": "user", "content": "hi"}]) if e["type"] == "done"][0]
    assert done["stats"]["ttft_s"] is not None and 0.04 <= done["stats"]["ttft_s"] < 2.0


def test_openai_stream_reports_ttft(monkeypatch):
    from core import llm_client as lc

    class Resp:
        status_code = 200
        text = ""
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            yield b'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null}]}'
            time.sleep(0.05)
            yield b'data: {"choices":[{"delta":{"content":"Hi there."},"finish_reason":null}]}'
            yield b'data: [DONE]'

    monkeypatch.setattr(lc, "_load_config", lambda: {"llm_provider": "openai", "llm_url": "http://x",
                                                      "llm_model": "m"})
    monkeypatch.setattr(lc.requests, "post", lambda *a, **k: Resp())
    done = [e for e in lc.call_llm_stream([{"role": "user", "content": "hi"}]) if e["type"] == "done"][0]
    assert done["stats"]["ttft_s"] is not None and done["stats"]["ttft_s"] >= 0.04
