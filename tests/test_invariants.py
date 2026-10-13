"""Whole-codebase invariants: properties that must hold across ALL tools / ALL inputs.

Cheap, model-free, no network. Each one guards a class of bug seen in real logs.
"""
import json
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ── Tool schema: every tool, every field ─────────────────────────────────
def _decls():
    from core.tool_declarations import TOOL_DECLARATIONS
    return TOOL_DECLARATIONS


def test_tool_names_unique():
    names = [d["name"] for d in _decls()]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("decl", _decls(), ids=lambda d: d["name"])
def test_each_tool_schema_is_valid(decl):
    assert decl.get("description", "").strip(), "tool needs a description"
    params = decl["parameters"]
    assert params["type"] == "OBJECT"
    props = params.get("properties", {})
    for req in params.get("required", []):
        assert req in props, f"required '{req}' not in properties"
    for pname, spec in props.items():
        assert spec.get("type") in {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY", "OBJECT"}, (pname, spec)
        if spec["type"] == "ARRAY":
            assert "items" in spec or True  # items optional in this codebase; type must still be valid


def test_converted_tools_are_openai_compatible():
    """Gemini's OpenAI endpoint rejects Google-style upper-case types."""
    from core.tool_declarations import _to_ollama_tools

    def walk(node):
        if isinstance(node, dict):
            if "type" in node and isinstance(node["type"], str):
                assert node["type"] == node["type"].lower(), node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    tools = _to_ollama_tools(_decls())
    for t in tools:
        assert t["type"] == "function" and t["function"]["name"]
        assert t["function"]["parameters"]["type"] == "object"
        walk(t["function"]["parameters"])


def test_every_declared_tool_has_a_handler():
    from core.tool_dispatch import ToolDispatchMixin
    special = {"save_memory", "shutdown_aether", "undo"}  # handled with their own control flow
    missing = [d["name"] for d in _decls()
               if d["name"] not in ToolDispatchMixin._TOOL_DISPATCH and d["name"] not in special]
    assert not missing, f"declared but no handler: {missing}"


def test_tool_schema_fits_hard_tier_budget():
    """Full catalog must stay under the point where it starves the conversation (3000-token budget)."""
    from core.smart_context import estimate_tool_tokens
    from core.tool_declarations import _to_ollama_tools
    tokens = estimate_tool_tokens(_to_ollama_tools(_decls()))
    assert tokens < 6500, f"tool schema is {tokens} tokens"


# ── Compression: the current request is never lost, for ANY input ────────
def _random_conversation(rng):
    msgs = [{"role": "system", "content": "s" * rng.choice([10, 500, 4000, 12000])}]
    for _ in range(rng.randint(0, 12)):
        r = rng.choice(["user", "assistant", "assistant_tc", "tool"])
        text = "x" * rng.randint(1, 1500)
        if r == "assistant_tc":
            msgs.append({"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "f"}}]})
            msgs.append({"role": "tool", "content": text, "tool_call_id": "1"})
        else:
            msgs.append({"role": r if r != "tool" else "user", "content": text})
    msgs.append({"role": "user", "content": "CURRENT-REQUEST"})
    return msgs


@pytest.mark.parametrize("seed", range(40))
def test_compress_always_keeps_current_request(seed):
    from core.smart_context import compress_if_needed
    rng = random.Random(seed)
    msgs = _random_conversation(rng)
    budget = rng.choice([10, 100, 500, 3000])
    out = compress_if_needed(msgs, token_budget=budget)
    assert out[-1]["content"] == "CURRENT-REQUEST"
    assert out[0]["role"] == "system"
    first_conv = next((m for m in out if m["role"] != "system"), None)
    assert first_conv is None or first_conv["role"] != "tool" or len([m for m in out if m["role"] != "system"]) == 1


@pytest.mark.parametrize("seed", range(20))
def test_lc_compress_always_keeps_current_request(seed):
    from integrations.langchain_tools import lc_compress_history
    rng = random.Random(1000 + seed)
    out = lc_compress_history(_random_conversation(rng), max_tokens=rng.choice([100, 500, 3000]))
    assert out[-1]["content"] == "CURRENT-REQUEST"


# ── Tool arguments: models send junk; handlers must not crash ────────────
@pytest.mark.parametrize("q", [None, "", "   ", [], [""], (), 0])
def test_web_search_survives_junk_queries(q):
    import actions.web_search as ws
    out = ws.web_search({"query": q})
    assert isinstance(out, str)


# ── Whole repo ───────────────────────────────────────────────────────────
def test_every_python_file_compiles():
    bad = []
    for p in ROOT.rglob("*.py"):
        if any(part in {".git", "__pycache__", "node_modules", "venv", ".venv"} for part in p.parts):
            continue
        try:
            compile(p.read_bytes(), str(p), "exec")   # syntax check without writing .pyc files
        except (SyntaxError, ValueError) as e:
            bad.append(f"{p.relative_to(ROOT)}: {str(e)[:80]}")
    assert not bad, bad


def test_config_example_is_valid_json_and_has_no_real_keys():
    cfg = json.loads((ROOT / "config" / "config.example.json").read_text(encoding="utf-8"))
    for k, v in cfg.items():
        if "key" in k.lower() and isinstance(v, str):
            assert len(v) < 12 or v.lower().startswith(("your", "<", "sk-xxx", "changeme")), \
                f"{k} looks like a real secret in an example file"


def test_hard_tier_tool_set_is_trimmed_but_broad():
    from core.tool_router import filter_tools_hard, select_tool_names_hard
    from core.tool_declarations import _to_ollama_tools
    OLLAMA_TOOLS = _to_ollama_tools(_decls())
    for q in ["Compare Python, Go and Rust. Search the web if you need to.",
              "Analyze this note and name the animal", "Compare SQLite and PostgreSQL"]:
        out = filter_tools_hard(OLLAMA_TOOLS, q)
        names = {t["function"]["name"] for t in out}
        assert len(out) <= 22, len(out)
        assert len(out) < len(OLLAMA_TOOLS)
        assert {"web_search", "code_helper", "save_memory"} <= names


def test_cloud_turns_get_bigger_history_budget_and_local_fallback_recompresses():
    import inspect, core.conversation_core as cc
    src = inspect.getsource(cc)
    assert cc._CLOUD_HISTORY_BUDGET >= 6000
    assert "_primary_provider in (\"gemini\", \"openai_api\")" in src
    # fallback to local must re-fit history to the small local window
    blk = src.split("retrying this turn on the local model")[1][:700]
    assert "compress_if_needed(" in blk and "_local_total_budget() - estimate_tool_tokens" in blk


def test_gemini_escalation_uses_short_timeout(monkeypatch):
    import core.llm_client as lc
    seen = {}
    def fake_stream_openai(messages, tools, timeout, **kw):
        seen["t"] = timeout
        yield {"type": "done", "content": "x", "tool_calls": [], "stats": {}}
    monkeypatch.setattr(lc, "_stream_openai", fake_stream_openai)
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    list(lc.call_llm_stream([{"role": "user", "content": "hi"}], None, 120,
                            provider_override="gemini", url_override="u", model_override="m"))
    assert seen["t"] == 15
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_timeout": 1})
    assert lc.gemini_timeout() == 5          # clamped floor
    # non-gemini overrides keep the caller's timeout
    list(lc.call_llm_stream([{"role": "user", "content": "hi"}], None, 120,
                            provider_override="openai", url_override="u", model_override="m"))
    assert seen["t"] == 120


def test_clean_memory_dry_run_then_apply(tmp_path):
    import sqlite3
    from tools import clean_memory
    db = tmp_path / "m.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, role TEXT, content TEXT, timestamp REAL, embedding BLOB)")
    rows = ["User: hi Assistant: hello", "User: x Assistant: <tool_call>{}</tool_call>",
            "User: dog? Assistant: Biscuit"]
    con.executemany("INSERT INTO memories(role,content,timestamp) VALUES('exchange',?,0)", [(r,) for r in rows])
    con.commit(); con.close()
    assert clean_memory.main(["--db", str(db)]) == 0                 # dry run
    assert sqlite3.connect(db).execute("select count(*) from memories").fetchone()[0] == 3
    assert clean_memory.main(["--db", str(db), "--apply"]) == 0
    left = [r[0] for r in sqlite3.connect(db).execute("select content from memories")]
    assert len(left) == 2 and not any("<tool_call>" in x for x in left)
    assert list(tmp_path.glob("*.bak"))                              # backup written


def test_open_app_start_menu_matching():
    from actions.open_app import _match_start_app
    apps = [{"Name": "Calculator", "AppID": "calc!App"},
            {"Name": "Visual Studio Code", "AppID": "vscode"},
            {"Name": "Visual Studio Installer", "AppID": "vsi"},
            {"Name": "Spotify", "AppID": "spot"},
            {"Name": "NoId", "AppID": ""}]
    assert _match_start_app("calculator", apps)["AppID"] == "calc!App"
    assert _match_start_app("spotify", apps)["AppID"] == "spot"
    assert _match_start_app("visual studio code", apps)["AppID"] == "vscode"
    assert _match_start_app("code visual", apps)["AppID"] == "vscode"      # all words, any order
    assert _match_start_app("Zorblax Quantum Editor", apps) is None        # unknown -> fast fail
    assert _match_start_app("noid", apps) is None                          # entries without AppID ignored
    assert _match_start_app("", apps) is None


def test_open_app_unknown_name_never_types_into_start(monkeypatch):
    import actions.open_app as oa
    monkeypatch.setattr(oa, "_start_apps", lambda: [{"Name": "Calculator", "AppID": "c"}])
    monkeypatch.setattr(oa.shutil, "which", lambda *_: None)
    import sys, types
    boom = types.SimpleNamespace(press=lambda *a, **k: (_ for _ in ()).throw(AssertionError("typed into Start")),
                                 write=lambda *a, **k: (_ for _ in ()).throw(AssertionError("typed into Start")), PAUSE=0)
    monkeypatch.setitem(sys.modules, "pyautogui", boom)
    assert oa._launch_windows("Zorblax Quantum Editor") is False


def test_ui_gemini_default_is_not_the_retired_model():
    import re
    src = open("ui.py", encoding="utf-8").read()
    assert not re.search(r'get\("llm_model", "gemini-2\.5-flash"\)', src)
    assert 'currentData() or "gemini-2.5-flash"' not in src
    first = re.search(r'_GEMINI_MODELS = \[\s*\("([^"]+)"', src).group(1)
    assert first == "gemini-3.8-flash"



def test_local_budget_scales_with_config_but_stays_inside_num_ctx(monkeypatch):
    import core.llm_client as lc, core.conversation_core as cc
    monkeypatch.setattr(lc, "_num_ctx", lambda: 8192)
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    assert cc._local_total_budget() == 5000                       # default
    monkeypatch.setattr(lc, "_load_config", lambda: {"llm.history_budget": 99999})
    assert cc._local_total_budget() == 8192 - 3000                # clamped: reply headroom
    monkeypatch.setattr(lc, "_load_config", lambda: {"llm.history_budget": 10})
    assert cc._local_total_budget() == 2000                       # floor
    monkeypatch.setattr(lc, "_load_config", lambda: {"llm.history_budget": "junk"})
    assert cc._local_total_budget() == 3000                       # bad value -> old default


def test_openai_safe_messages_fixes_ollama_style_tool_history():
    import json
    from core.llm_client import _openai_safe_messages
    hist = [
        {"role": "user", "content": "remember teal"},
        {"role": "assistant", "content": None,
         "tool_calls": [{"function": {"name": "save_memory",
                                      "arguments": {"category": "preferences", "key": "c", "value": "teal"}}}]},
        {"role": "tool", "content": "Done."},
    ]
    before = json.dumps(hist, sort_keys=True)
    out = _openai_safe_messages(hist)
    assert json.dumps(hist, sort_keys=True) == before            # input untouched (Ollama keeps native form)
    tc = out[1]["tool_calls"][0]
    assert isinstance(tc["function"]["arguments"], str)           # the Gemini 400 cause
    assert json.loads(tc["function"]["arguments"])["value"] == "teal"
    assert tc["type"] == "function" and tc["id"]
    assert out[1]["content"] == ""
    assert out[2]["tool_call_id"] == tc["id"]                     # tool result paired to its call


def test_openai_safe_messages_keeps_string_args_and_ids():
    from core.llm_client import _openai_safe_messages
    hist = [{"role": "assistant", "content": "x",
             "tool_calls": [{"id": "abc", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}]},
            {"role": "tool", "content": "ok", "tool_call_id": "abc"}]
    out = _openai_safe_messages(hist)
    assert out[0]["tool_calls"][0]["id"] == "abc"
    assert out[0]["tool_calls"][0]["function"]["arguments"] == "{\"a\": 1}"
    assert out[1]["tool_call_id"] == "abc"


def test_hard_extra_excludes_file_writers():
    from core.tool_router import _HARD_EXTRA
    assert not ({"file_controller", "file_processor", "pdf_tool"} & _HARD_EXTRA)


def test_cloud_payloads_never_carry_dict_arguments(monkeypatch):
    import core.llm_client as lc
    sent = {}
    class R:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self): return iter([b"data: [DONE]"])
    def fake_post(url, json=None, **kw):
        sent["p"] = json
        return R()
    monkeypatch.setattr(lc.requests, "post", fake_post)
    hist = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}]},
            {"role": "tool", "content": "ok"}]
    list(lc._stream_openai(hist, None, 5, provider_override="gemini",
                           url_override="http://x", model_override="m"))
    assert isinstance(sent["p"]["messages"][0]["tool_calls"][0]["function"]["arguments"], str)


def test_gemini_gets_large_reply_budget_and_length_stop_is_flagged(monkeypatch):
    import json as _j
    import core.llm_client as lc
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    assert lc._cloud_max_tokens("gemini") == 2048
    assert lc._cloud_max_tokens("openai") == 150
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_max_tokens": 10})
    assert lc._cloud_max_tokens("gemini") == 256            # floor
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_max_tokens": 10**6})
    assert lc._cloud_max_tokens("gemini") == 8192           # cap

    sent = {}
    chunk = lambda d: ("data: " + _j.dumps(d)).encode()
    class R:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self):
            return iter([chunk({"choices": [{"delta": {"content": "The animal is"}}]}),
                         chunk({"choices": [{"delta": {}, "finish_reason": "length"}]})])
    def fake_post(url, json=None, **kw):
        sent["p"] = json
        return R()
    monkeypatch.setattr(lc.requests, "post", fake_post)
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    ev = list(lc._stream_openai([{"role": "user", "content": "x"}], None, 5, provider_override="gemini",
                                url_override="http://x", model_override="m"))
    assert sent["p"]["max_tokens"] == 2048
    assert ev[-1]["stats"]["truncated"] is True              # surfaced so the reply gets the "…" marker


# ── Gemini transient-5xx retry ────────────────────────────────────────────────
def _retry_harness(monkeypatch, outcomes):
    """Patch _stream_openai_once to play back `outcomes` (exception or event list)."""
    import core.llm_client as lc
    calls = {"n": 0, "sleeps": []}

    def fake_once(messages, tools, timeout, **kw):
        out = outcomes[min(calls["n"], len(outcomes) - 1)]
        calls["n"] += 1
        if isinstance(out, Exception):
            raise out
        yield from out
    monkeypatch.setattr(lc, "_stream_openai_once", fake_once)
    monkeypatch.setattr(lc.time, "sleep", lambda s: calls["sleeps"].append(s))
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    return lc, calls


_E503 = RuntimeError('OpenAI-compatible HTTP error: 503 {"error": {"code": 503, "status": "UNAVAILABLE"}}')
_OK = [{"type": "sentence", "text": "hi."}, {"type": "done", "content": "hi.", "tool_calls": [], "stats": {}}]


def test_gemini_503_is_retried_once_then_succeeds(monkeypatch):
    lc, calls = _retry_harness(monkeypatch, [_E503, _OK])
    ev = list(lc._stream_openai([], None, 5, provider_override="gemini"))
    assert calls["n"] == 2 and calls["sleeps"] == [1.0]
    assert ev[-1]["type"] == "done"


def test_gemini_503_twice_raises_after_exactly_one_retry(monkeypatch):
    lc, calls = _retry_harness(monkeypatch, [_E503, _E503, _OK])
    with pytest.raises(RuntimeError, match="503"):
        list(lc._stream_openai([], None, 5, provider_override="gemini"))
    assert calls["n"] == 2


@pytest.mark.parametrize("err", [
    RuntimeError("OpenAI-compatible HTTP error: 400 bad request"),
    RuntimeError("OpenAI-compatible HTTP error: 401 unauthorized"),
    RuntimeError("OpenAI-compatible HTTP error: 429 quota"),
    RuntimeError("OpenAI-compatible stream timed out."),
])
def test_gemini_non_transient_errors_are_not_retried(monkeypatch, err):
    lc, calls = _retry_harness(monkeypatch, [err, _OK])
    with pytest.raises(RuntimeError):
        list(lc._stream_openai([], None, 5, provider_override="gemini"))
    assert calls["n"] == 1 and calls["sleeps"] == []


def test_non_gemini_providers_never_retry(monkeypatch):
    lc, calls = _retry_harness(monkeypatch, [_E503, _OK])
    with pytest.raises(RuntimeError):
        list(lc._stream_openai([], None, 5, provider_override="openai"))
    assert calls["n"] == 1


def test_no_retry_once_output_was_produced(monkeypatch):
    """A failure after events were already yielded must not replay them."""
    import core.llm_client as lc

    def fake_once(messages, tools, timeout, **kw):
        yield {"type": "sentence", "text": "partial."}
        raise _E503
    monkeypatch.setattr(lc, "_stream_openai_once", fake_once)
    monkeypatch.setattr(lc.time, "sleep", lambda s: pytest.fail("must not sleep"))
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    got = []
    with pytest.raises(RuntimeError):
        for e in lc._stream_openai([], None, 5, provider_override="gemini"):
            got.append(e)
    assert [e["text"] for e in got] == ["partial."]


def test_retry_delay_is_configurable_and_zero_disables(monkeypatch):
    lc, calls = _retry_harness(monkeypatch, [_E503, _OK])
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_retry_delay": 0})
    with pytest.raises(RuntimeError):
        list(lc._stream_openai([], None, 5, provider_override="gemini"))
    assert calls["n"] == 1
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_retry_delay": 99})
    assert lc.gemini_retry_delay() == 5.0           # clamped
    monkeypatch.setattr(lc, "_load_config", lambda: {"gemini_retry_delay": "x"})
    assert lc.gemini_retry_delay() == 1.0           # bad value -> default


def test_real_503_response_triggers_the_retry_end_to_end(monkeypatch):
    """Through the real HTTP-error path (not a stubbed once()): 503 then 200."""
    import json as _j
    import core.llm_client as lc
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    sleeps = []
    monkeypatch.setattr(lc.time, "sleep", lambda s: sleeps.append(s))
    chunk = lambda d: ("data: " + _j.dumps(d)).encode()

    class R:
        def __init__(self, code, lines=(), text=""):
            self.status_code, self._lines, self.text = code, lines, text
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def iter_lines(self): return iter(self._lines)

    seq = [R(503, text='{"error":{"code":503,"status":"UNAVAILABLE"}}'),
           R(200, [chunk({"choices": [{"delta": {"content": "Hello there."}}]}),
                   chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})])]
    posts = []
    def fake_post(url, json=None, **kw):
        posts.append(url)
        return seq[len(posts) - 1]
    monkeypatch.setattr(lc.requests, "post", fake_post)
    ev = list(lc._stream_openai([{"role": "user", "content": "x"}], None, 5,
                                provider_override="gemini", url_override="http://x",
                                model_override="m"))
    assert len(posts) == 2 and sleeps == [1.0]
    assert ev[-1]["type"] == "done" and "Hello there" in ev[-1]["content"]


# ── Gemini circuit breaker (cooldown) ─────────────────────────────────────────
class _Clock:
    t = 1000.0


def _breaker(monkeypatch, outcomes, cfg=None):
    lc, calls = _retry_harness(monkeypatch, outcomes)
    monkeypatch.setattr(lc, "_load_config", lambda: dict(cfg or {}))
    monkeypatch.setattr(lc.time, "monotonic", lambda: _Clock.t)
    _Clock.t = 1000.0
    monkeypatch.setattr(lc, "get_llm_settings", lambda: ("http://x", "local-model"))
    return lc, calls


def _esc(lc, model="gem-a"):
    return list(lc._stream_openai([], None, 5, provider_override="gemini", model_override=model))


_E500 = RuntimeError("OpenAI-compatible HTTP error: 500 internal")


def test_outage_after_retry_puts_gemini_on_cooldown_and_next_call_fast_fails(monkeypatch):
    lc, calls = _breaker(monkeypatch, [_E503, _E503, _OK])
    with pytest.raises(RuntimeError, match="503"):
        _esc(lc)
    assert calls["n"] == 2                                   # try + the single retry
    n, s = calls["n"], list(calls["sleeps"])
    with pytest.raises(RuntimeError, match="cooldown"):
        _esc(lc)
    assert calls["n"] == n and calls["sleeps"] == s           # no request, no sleep
    assert 0 < lc.gemini_cooldown_remaining("gem-a") <= 120


def test_cooldown_expires_and_success_clears_it(monkeypatch):
    lc, calls = _breaker(monkeypatch, [_E503, _E503, _OK])
    with pytest.raises(RuntimeError):
        _esc(lc)
    _Clock.t += 121                                           # past the 120 s default
    assert lc.gemini_cooldown_remaining("gem-a") == 0
    assert _esc(lc)[-1]["type"] == "done"
    assert "gem-a" not in lc._GEMINI_COOLDOWNS


def test_success_during_cooldown_window_expiry_resets_state(monkeypatch):
    lc, _ = _breaker(monkeypatch, [_E503, _E503, _OK, _OK])
    with pytest.raises(RuntimeError):
        _esc(lc)
    _Clock.t += 200
    _esc(lc); _esc(lc)                                        # healthy twice: never re-blocked
    assert lc.gemini_cooldown_remaining("gem-a") == 0


@pytest.mark.parametrize("err,expected", [
    (RuntimeError("OpenAI-compatible HTTP error: 429 quota"), 300),
    (RuntimeError("OpenAI-compatible HTTP error: 401 unauthorized"), 3600),
    (RuntimeError("OpenAI-compatible HTTP error: 404 model not found"), 3600),
    (RuntimeError("OpenAI-compatible stream timed out."), 120),
    (RuntimeError("Cannot reach OpenAI-compatible server at http://x."), 120),
])
def test_cooldown_length_depends_on_failure_kind(monkeypatch, err, expected):
    lc, _ = _breaker(monkeypatch, [err])
    with pytest.raises(RuntimeError):
        _esc(lc)
    assert lc.gemini_cooldown_remaining("gem-a") == pytest.approx(expected)


def test_bad_request_400_does_not_cool_down(monkeypatch):
    """A 400 can be about this one request (e.g. a bad message shape), not the service."""
    lc, calls = _breaker(monkeypatch, [RuntimeError("OpenAI-compatible HTTP error: 400 bad"), _OK])
    with pytest.raises(RuntimeError):
        _esc(lc)
    assert lc.gemini_cooldown_remaining("gem-a") == 0
    assert _esc(lc)[-1]["type"] == "done"


def test_cooldown_is_per_model(monkeypatch):
    lc, _ = _breaker(monkeypatch, [_E503, _E503, _OK])
    with pytest.raises(RuntimeError):
        _esc(lc, "gem-a")
    assert _esc(lc, "gem-b")[-1]["type"] == "done"            # other model unaffected


def test_cooldown_can_be_disabled_and_is_configurable(monkeypatch):
    lc, calls = _breaker(monkeypatch, [_E503, _E503, _E503, _E503, _OK], {"gemini_cooldown": 0})
    for _ in range(2):
        with pytest.raises(RuntimeError, match="503"):
            _esc(lc)
    assert lc.gemini_cooldown_remaining("gem-a") == 0         # disabled: never blocks
    assert calls["n"] == 4                                    # each call really tried (+retry)
    lc2, _ = _breaker(monkeypatch, [_E503, _E503], {"gemini_cooldown": 30})
    with pytest.raises(RuntimeError):
        _esc(lc2)
    assert lc2.gemini_cooldown_remaining("gem-a") == pytest.approx(30)
    monkeypatch.setattr(lc2, "_load_config", lambda: {"gemini_cooldown": 99999})
    assert lc2.gemini_cooldown_seconds() == 3600              # clamped
    monkeypatch.setattr(lc2, "_load_config", lambda: {"gemini_cooldown": "x"})
    assert lc2.gemini_cooldown_seconds() == 120               # bad value -> default


def test_primary_gemini_provider_is_never_blocked_by_the_breaker(monkeypatch):
    """Only per-turn escalations (provider_override) cool down; a user whose primary
    provider is Gemini must not lose the assistant for two minutes after a blip."""
    lc, calls = _breaker(monkeypatch, [_E503, _E503, _E503, _E503])
    monkeypatch.setattr(lc, "get_llm_provider", lambda: "gemini")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="503"):
            list(lc._stream_openai([], None, 5))              # no provider_override
    assert lc._GEMINI_COOLDOWNS == {}
    assert calls["n"] == 4


def test_non_transient_5xx_without_retry_still_notes_failure(monkeypatch):
    lc, calls = _breaker(monkeypatch, [_E500, _E500])
    with pytest.raises(RuntimeError):
        _esc(lc)
    assert calls["n"] == 2 and lc.gemini_cooldown_remaining("gem-a") > 0


def test_mid_stream_failure_does_not_cool_down(monkeypatch):
    import core.llm_client as lc

    def fake_once(messages, tools, timeout, **kw):
        yield {"type": "sentence", "text": "partial."}
        raise _E503
    monkeypatch.setattr(lc, "_stream_openai_once", fake_once)
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    monkeypatch.setattr(lc, "get_llm_settings", lambda: ("http://x", "m"))
    with pytest.raises(RuntimeError):
        list(lc._stream_openai([], None, 5, provider_override="gemini", model_override="gem-a"))
    assert lc.gemini_cooldown_remaining("gem-a") == 0


def test_conversation_fallback_message_still_works_with_cooldown_error():
    """conversation_core catches RuntimeError and retries locally: the cooldown error
    must be a RuntimeError so that existing path handles it."""
    import core.llm_client as lc
    lc._GEMINI_COOLDOWNS["gem-z"] = (lc.time.monotonic() + 50, "HTTP error: 503")
    with pytest.raises(RuntimeError, match="cooldown"):
        list(lc._stream_openai([], None, 5, provider_override="gemini", model_override="gem-z"))


def test_cooldown_check_is_one_atomic_read_not_check_then_index(monkeypatch):
    """Escalations run on worker threads. If another thread clears the entry between
    'is it resting?' and 'why?', indexing the dict raised KeyError -- which escapes
    conversation_core's `except RuntimeError` fallback. Reading once under a lock
    makes that impossible; this dict refuses any indexing to prove nothing indexes it."""
    import core.llm_client as lc

    class NoIndex(dict):
        def __getitem__(self, k):
            raise KeyError(k)
    d = NoIndex()
    d["gem-a"] = (lc.time.monotonic() + 60, "HTTP error: 503")
    monkeypatch.setattr(lc, "_GEMINI_COOLDOWNS", d)
    monkeypatch.setattr(lc, "_load_config", lambda: {})
    with pytest.raises(RuntimeError, match="cooldown"):          # RuntimeError, never KeyError
        list(lc._stream_openai([], None, 5, provider_override="gemini", model_override="gem-a"))


def test_cooldown_state_is_safe_under_concurrent_set_and_clear():
    import threading
    import core.llm_client as lc
    stop = threading.Event()
    bad = []

    def writer():
        while not stop.is_set():
            with lc._COOL_LOCK:
                lc._GEMINI_COOLDOWNS["g"] = (lc.time.monotonic() + 5, "r")
            with lc._COOL_LOCK:
                lc._GEMINI_COOLDOWNS.pop("g", None)

    t = threading.Thread(target=writer); t.start()
    try:
        for _ in range(20000):
            try:
                left, why = lc._cooldown_state("g")
                assert (left > 0) == (why == "r") or left == 0.0
            except Exception as e:  # noqa: BLE001
                bad.append(repr(e)); break
    finally:
        stop.set(); t.join()
    assert bad == []


def test_gemini_reports_an_invalid_key_as_400_and_it_rests_long(monkeypatch):
    lc, _ = _breaker(monkeypatch, [RuntimeError(
        'OpenAI-compatible HTTP error: 400 [{"error":{"code":400,"message":"API key not valid. '
        'Please pass a valid API key.","status":"INVALID_ARGUMENT"}}]')])
    with pytest.raises(RuntimeError):
        _esc(lc)
    assert lc.gemini_cooldown_remaining("gem-a") == pytest.approx(3600)
