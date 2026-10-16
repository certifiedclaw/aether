from core import degrade as d


def kinds(msg):
    return d.classify(RuntimeError(msg)).kind


def test_classify_common_failures():
    assert kinds("Cannot connect to Ollama at http://x. Make sure Ollama is installed") == d.OLLAMA_DOWN
    assert kinds("HTTPConnectionPool: Max retries exceeded") == d.OLLAMA_DOWN
    assert kinds("Model 'qwen3:8b' not found in Ollama. Installed: a") == d.MODEL_MISSING
    assert kinds("Ollama stream timed out.") == d.TIMEOUT
    assert kinds("OpenAI-compatible HTTP error: 429 quota") == d.RATE_LIMIT
    assert kinds("OpenAI-compatible HTTP error: 401") == d.AUTH
    assert kinds("HTTP error: 400") == d.BAD_REQUEST
    assert kinds("CUDA error: out of memory") == d.OOM
    assert kinds("Cloud model (gemini) blocked by config (cloud_allow=false).") == d.CLOUD_BLOCKED
    assert kinds("weird") == d.UNKNOWN
    assert kinds("") == d.UNKNOWN


def test_messages_are_plain_and_leak_nothing():
    f = d.classify(RuntimeError("Cannot connect to Ollama at http://10.0.0.5:11434 key=sk-abc"))
    assert "sk-abc" not in f.message and "10.0.0.5" not in f.message


def plan(kind, attempt=0, cfg=None, **kw):
    return d.plan_recovery(d.Failure(kind, "m"), attempt, cfg or {}, **kw)


def test_down_or_timeout_waits_once_then_gives_up_by_default():
    a = plan(d.OLLAMA_DOWN, 0)
    assert a.kind == "wait_retry" and a.wait_s == 4
    assert plan(d.OLLAMA_DOWN, 1).kind == "give_up"
    assert plan(d.TIMEOUT, 0, {"retry_wait_s": 1}).wait_s == 1


def test_retry_wait_is_bounded_and_tolerates_junk():
    assert plan(d.TIMEOUT, 0, {"retry_wait_s": 9999}).wait_s == 30
    assert plan(d.TIMEOUT, 0, {"retry_wait_s": "x"}).wait_s == 4
    assert plan(d.TIMEOUT, 0, {"retry_wait_s": -5}).wait_s == 0


def test_fallback_model_for_missing_or_oom():
    cfg = {"fallback_model": "qwen3:4b"}
    a = plan(d.MODEL_MISSING, 0, cfg, current_model="qwen3:8b")
    assert a.kind == "switch_model" and a.model == "qwen3:4b"
    assert plan(d.OOM, 1, cfg, current_model="qwen3:8b").kind == "switch_model"
    assert plan(d.MODEL_MISSING, 0, cfg, current_model="qwen3:4b").kind == "give_up"   # already on it
    assert plan(d.MODEL_MISSING, 0, {}, current_model="x").kind == "give_up"


def test_cloud_fallback_is_opt_in_and_respects_cloud_allow():
    assert plan(d.OLLAMA_DOWN, 1, {}, has_gemini_key=True).kind == "give_up"
    on = {"cloud_fallback": True}
    assert plan(d.OLLAMA_DOWN, 1, on, has_gemini_key=True).kind == "cloud"
    assert plan(d.OLLAMA_DOWN, 1, on, has_gemini_key=False).kind == "give_up"
    assert plan(d.OLLAMA_DOWN, 1, dict(on, cloud_allow=False), has_gemini_key=True).kind == "give_up"
    assert plan(d.MODEL_MISSING, 0, {"cloud_fallback": "yes"}, has_gemini_key=True).kind == "cloud"


def test_never_loops_forever_and_never_retries_hopeless_errors():
    cfg = {"cloud_fallback": True, "fallback_model": "z"}
    assert plan(d.OLLAMA_DOWN, d.MAX_ATTEMPTS - 1, cfg, has_gemini_key=True).kind == "give_up"
    for k in (d.AUTH, d.BAD_REQUEST, d.CLOUD_BLOCKED, d.UNKNOWN):
        assert plan(k, 0, cfg, has_gemini_key=True).kind == "give_up"
    assert plan(d.OLLAMA_DOWN, 0, cfg, on_cloud=True).kind == "give_up"


def test_dropped_connection_is_ollama_down():
    assert kinds("Cannot connect to Ollama at http://x (connection dropped: ConnectionError)") == d.OLLAMA_DOWN
    assert kinds("('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))") == d.OLLAMA_DOWN


def test_stream_retry_failure_becomes_runtimeerror(monkeypatch):
    """Ping succeeds but the chat connection keeps dropping: must be RuntimeError, not a raw requests error."""
    import pytest
    import requests
    from core import llm_client as lc
    monkeypatch.setattr(lc, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(lc, "get_llm_settings", lambda: ("http://x", "m"))
    monkeypatch.setattr(lc, "ensure_ollama_running", lambda *a, **k: True)

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("Connection aborted.")
    monkeypatch.setattr(lc.requests, "post", boom)
    with pytest.raises(RuntimeError) as ei:
        list(lc.call_llm_stream([{"role": "user", "content": "hi"}]))
    assert d.classify(ei.value).kind == d.OLLAMA_DOWN
