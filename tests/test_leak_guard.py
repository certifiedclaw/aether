import pytest
from core import leak_guard as lg

GKEY = "AIza" + "A" * 35
SK = "sk-" + "b" * 30
GH = "ghp_" + "c" * 36


def msgs(text, role="user"):
    return [{"role": role, "content": text}]


def test_local_provider_untouched():
    m = msgs(f"key {GKEY}")
    assert lg.guard_messages(m, "ollama", {}) is m
    assert lg.guard_messages(m, "openai", {}) is m


@pytest.mark.parametrize("secret,name", [
    (GKEY, "google_key"), (SK, "openai_key"), (GH, "github_token"),
    ("AKIA" + "A" * 16, "aws_key"),
    ("xoxb-1234567890-abcdefghij", "slack_token"),
    ("eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeKKF2QT4", "jwt"),
])
def test_known_patterns_redacted(secret, name):
    out = lg.guard_messages(msgs(f"use {secret} now"), "gemini", {})
    assert secret not in out[0]["content"]
    assert f"[REDACTED:{name}]" in out[0]["content"]


def test_private_key_block():
    pk = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    out = lg.guard_messages(msgs(f"here {pk} ok"), "gemini", {})[0]["content"]
    assert "MIIabc" not in out and "ok" in out


def test_assignment_keeps_name_hides_value():
    out = lg.guard_messages(msgs('password = "hunter2hunter2"'), "gemini", {})[0]["content"]
    assert "hunter2hunter2" not in out and "password" in out


def test_config_secret_exact_value():
    cfg = {"my_service_token": "zzz-custom-secret-value", "llm_model": "qwen3:8b"}
    out = lg.guard_messages(msgs("it is zzz-custom-secret-value ok"), "openai_api", cfg)[0]["content"]
    assert "zzz-custom" not in out
    assert "qwen3:8b" == cfg["llm_model"]


def test_card_luhn_only():
    ok = lg.guard_messages(msgs("card 4111 1111 1111 1111"), "gemini", {})[0]["content"]
    bad = lg.guard_messages(msgs("num 1234 5678 9012 3456"), "gemini", {})[0]["content"]
    assert "4111" not in ok and "1234 5678 9012 3456" in bad


def test_plain_text_unchanged_and_input_not_mutated():
    m = msgs(f"hello {GKEY}")
    orig = m[0]["content"]
    out = lg.guard_messages(m, "gemini", {})
    assert m[0]["content"] == orig and out is not m
    assert lg.guard_messages(msgs("what is 2+2"), "gemini", {})[0]["content"] == "what is 2+2"


def test_tool_calls_and_list_content():
    m = [{"role": "assistant", "content": [{"type": "text", "text": f"k {SK}"}],
          "tool_calls": [{"id": "1", "type": "function",
                          "function": {"name": "x", "arguments": f'{{"k":"{GH}"}}'}}]}]
    out = lg.guard_messages(m, "gemini", {})
    assert SK not in str(out) and GH not in str(out)
    assert out[0]["tool_calls"][0]["function"]["name"] == "x"


def test_block_mode_and_opt_out():
    with pytest.raises(lg.CloudBlocked):
        lg.guard_messages(msgs("hi"), "gemini", {"cloud_allow": False})
    m = msgs(f"k {GKEY}")
    assert lg.guard_messages(m, "gemini", {"cloud_redact": False}) is m


def test_stats_count_not_values():
    lg.reset_stats()
    lg.guard_messages(msgs(f"{GKEY} {SK}"), "gemini", {})
    st = lg.stats()
    assert st["google_key"] == 1 and st["openai_key"] == 1
    assert GKEY not in str(st)


def test_wired_into_llm_client(monkeypatch):
    import core.llm_client as lc
    seen = {}

    class R:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "ok"}}]}

    def post(url, json=None, **kw):
        seen["payload"] = json
        return R()

    monkeypatch.setattr(lc.requests, "post", post)
    monkeypatch.setattr(lc, "_auth_headers", lambda *a, **k: {})
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    lc.call_llm_text(f"secret {GKEY}", url="http://x", provider_override="gemini", model_override="m")
    assert GKEY not in str(seen["payload"])


def test_cloud_allowed_flag():
    assert lg.cloud_allowed({}) is True
    assert lg.cloud_allowed({"cloud_allow": True}) is True
    assert lg.cloud_allowed({"cloud_allow": False}) is False
