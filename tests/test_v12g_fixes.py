"""v12g: a missing Ollama model / missing config must fail loudly and early, not as 24 silent 404s."""
import io
import json

import pytest
import requests

from core import llm_client as lc


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.exceptions.HTTPError(f"{self.status_code}")
            err.response = self
            raise err

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_missing_model_message_names_installed_variants(monkeypatch):
    monkeypatch.setattr(lc.requests, "get", lambda *a, **k: _Resp(200, {"models": [
        {"name": "llama3.2:3b"}, {"name": "qwen3:8b"}]}))
    msg = lc._ollama_missing_model_message("llama3.2", "http://x")
    assert "llama3.2" in msg and "llama3.2:3b" in msg and "llm_model" in msg and "ollama pull" in msg


def test_missing_model_message_still_works_when_ollama_is_unreachable(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("down")
    monkeypatch.setattr(lc.requests, "get", boom)
    msg = lc._ollama_missing_model_message("llama3.2", "http://x")
    assert "not found" in msg and "ollama pull llama3.2" in msg


def test_stream_404_raises_the_helpful_message_not_a_bare_404(monkeypatch):
    monkeypatch.setattr(lc, "get_llm_provider", lambda: "ollama")
    monkeypatch.setattr(lc, "get_llm_settings", lambda: ("http://x", "llama3.2"))
    monkeypatch.setattr(lc, "ensure_ollama_running", lambda: True, raising=False)
    monkeypatch.setattr(lc.requests, "post", lambda *a, **k: _Resp(404, {"error": "model not found"}))
    monkeypatch.setattr(lc.requests, "get", lambda *a, **k: _Resp(200, {"models": [{"name": "llama3.2:3b"}]}))
    with pytest.raises(RuntimeError) as ei:
        list(lc.call_llm_stream([{"role": "user", "content": "hi"}]))
    assert "HTTP error: 404" not in str(ei.value)
    assert "llama3.2:3b" in str(ei.value)


# ── verify_local ─────────────────────────────────────────────────────────────
@pytest.fixture
def vl(monkeypatch, tmp_path):
    import tools.verify_local as v
    (tmp_path / "config").mkdir()
    (tmp_path / "requirements.txt").write_text("requests\n")
    monkeypatch.setattr(v, "ROOT", tmp_path)
    monkeypatch.setattr(v, "_resource_snapshot", lambda: None)
    monkeypatch.setattr(v, "_other_aether_processes", lambda: [])
    v.out.clear()
    return v


def _tags(monkeypatch, v, names):
    payload = json.dumps({"models": [{"name": n} for n in names]}).encode()
    monkeypatch.setattr(v.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))


def test_missing_config_is_a_blocker_and_secret_sources_are_still_reported(vl, monkeypatch):
    _tags(monkeypatch, vl, ["llama3.2:3b"])
    monkeypatch.setenv("AETHER_GEMINI_API_KEY", "SECRETVALUE123")
    vl.check_env()
    text = "\n".join(vl.out)
    assert vl.ENV_BLOCKER and "MISSING" in vl.ENV_BLOCKER
    assert "gemini_api_key" in text and "env" in text and "SECRETVALUE123" not in text


def test_default_model_that_is_not_installed_is_flagged_with_variants(vl, monkeypatch):
    (vl.ROOT / "config" / "api_keys.json").write_text(json.dumps({"llm_provider": "ollama"}))
    _tags(monkeypatch, vl, ["llama3.2:3b", "qwen3:8b"])
    vl.check_env()
    assert vl.ENV_BLOCKER and "llama3.2:3b" in vl.ENV_BLOCKER


def test_healthy_setup_has_no_blocker(vl, monkeypatch):
    (vl.ROOT / "config" / "api_keys.json").write_text(json.dumps({"llm_model": "qwen3:8b"}))
    _tags(monkeypatch, vl, ["qwen3:8b"])
    vl.check_env()
    assert vl.ENV_BLOCKER is None


def test_replay_is_skipped_not_waited_out_when_env_is_broken(vl, monkeypatch):
    vl.ENV_BLOCKER = "config/api_keys.json is MISSING"
    monkeypatch.setattr(vl, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not launch main.py")))
    vl.out.clear()
    assert vl.check_replay(5) is False
    assert "SKIPPED" in "\n".join(vl.out)


# ── run_python hardening (it is NOT a sandbox; these are the guard rails it does have) ──
def test_run_python_strips_secret_looking_env_vars_and_uses_scratch_cwd(monkeypatch, tmp_path):
    from actions import extra_tools as et
    monkeypatch.setenv("GEMINI_API_KEY", "LEAKME")
    monkeypatch.setenv("AETHER_SOMETHING", "LEAKME2")
    monkeypatch.setenv("HARMLESS_VAR", "visible")
    monkeypatch.chdir(tmp_path)
    out = et.run_python(
        "import os; print(os.environ.get('GEMINI_API_KEY'), os.environ.get('AETHER_SOMETHING'),"
        " os.environ.get('HARMLESS_VAR')); print(os.getcwd())")
    assert "LEAKME" not in out and "visible" in out
    assert str(tmp_path) not in out and "aether_py_" in out


def test_run_python_uses_the_current_interpreter_in_isolated_mode():
    from actions import extra_tools as et
    out = et.run_python("import sys; print(sys.executable); print(sys.flags.isolated)")
    import sys
    assert sys.executable in out and "1" in out.splitlines()[-1]


def test_run_python_rejects_code_larger_than_approval_preview(monkeypatch):
    from actions import extra_tools as et
    monkeypatch.setattr(et.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    out = et.run_python("x" * 4001)
    assert out == "Code too long (maximum 4000 characters)."


def test_run_python_clamps_a_model_chosen_timeout(monkeypatch):
    from actions import extra_tools as et
    seen = {}
    real = et.subprocess.run

    def spy(*a, **k):
        seen["timeout"] = k["timeout"]
        return real(*a, **k)
    monkeypatch.setattr(et.subprocess, "run", spy)
    et.run_python("print(1)", timeout=10**6)
    assert seen["timeout"] == 60
    et.run_python("print(1)", timeout="garbage")
    assert seen["timeout"] == 10
    assert et.run_python("while True: pass", timeout=1) == "Timed out after 1s"


def test_approval_dialog_shows_the_whole_code_for_ask_tier_tools():
    from core import risk
    code = "x = 1\n" * 500 + "print('TAIL_MARKER')"          # ~3000 chars
    detail = risk._detail("extra_tool", "run_python", {"action": "run_python", "code": code})
    assert "TAIL_MARKER" in detail
    huge = "y" * 9000
    assert "truncated" in risk._detail("extra_tool", "run_python", {"code": huge})
