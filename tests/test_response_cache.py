"""Roadmap #54: opt-in response cache (core/response_cache.py + its wiring in the turn loop)."""
from __future__ import annotations

import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import conversation_core, response_cache as rc
from core.response_cache import ResponseCache


# ---------------- what may be cached ----------------

@pytest.mark.parametrize("text", [
    "What is the capital of France?",
    "How does a transistor work",
    "what's the difference between TCP and UDP",
    "Hey Aether, can you tell me how photosynthesis works?",
    "Who wrote Pride and Prejudice?",
    "Explain how a binary search works",
])
def test_self_contained_questions_are_cacheable(text):
    assert rc.question_verdict(text)[0], rc.question_verdict(text)


@pytest.mark.parametrize("text,why", [
    ("What is the weather like", "can change"),
    ("What time is it", "refers back"),                  # 'it' - refused either way
    ("What's the latest news on AI", "can change"),
    ("What is the price of bitcoin", "can change"),
    ("What did I say about my dog", "about the user"),
    ("Do you remember my name", "about the user"),
    ("What does that mean", "refers back"),
    ("How does it work", "refers back"),
    ("And what about Germany", "refers to the conversation"),
    ("Tell me a joke", "needs 3-40 words"),             # filler stripped, too short anyway
    ("Tell me a funny joke about cats", "not a factual question"),
    ("What is a good random joke", "creative or random"),
    ("Who won the 2022 world cup", "can change"),       # 'won'
    ("What happened in 1969", "mentions a year"),
    ("Is it", "needs 3-40 words"),
    ("open the browser please now", "not a factual question"),
    ("What is the best programming language", "creative or random"),
    ("how much free disk space is left", "can change"),
])
def test_unsafe_questions_are_refused_with_a_reason(text, why):
    ok, reason = rc.question_verdict(text)
    assert not ok and why in reason, (text, reason)


def test_turn_and_reply_guards():
    good = dict(tools_run=[], memory_hit=False, truncated=False, cancelled=False,
                exhausted=False, tier="EVERYDAY", has_active_task=False)
    assert rc.turn_verdict(**good)[0]
    for field, value in (("tools_run", [("web_search", "x")]), ("memory_hit", True),
                         ("truncated", True), ("cancelled", True), ("exhausted", True),
                         ("tier", "HARD"), ("has_active_task", True)):
        assert not rc.turn_verdict(**{**good, field: value})[0], field
    assert rc.reply_verdict("Paris.")[0]
    for bad in ("", " ", "I wasn't able to finish that after a few tool attempts",
                "Sorry, something went wrong", "Unknown tool: x", "ERR: boom"):
        assert not rc.reply_verdict(bad)[0], bad


def test_normalization_makes_rephrasings_match():
    n = rc.normalize
    assert n("What is the capital of France?") == n("what is the capital of france")
    assert n("Hey Aether, what is the capital of France") == n("What is the capital of France?")
    assert n("Can you tell me how a transistor works") == n("how a transistor works")


# ---------------- the store ----------------

def test_hit_miss_scope_and_persistence(tmp_path):
    p = tmp_path / "c.json"
    c = ResponseCache(p)
    assert c.get("s1", "what is a prime number") is None
    c.put("s1", "What is a prime number?", "A number with exactly two divisors.")
    assert c.get("s1", "what is a prime number") == "A number with exactly two divisors."
    assert c.get("other-model", "what is a prime number") is None        # scope isolates
    again = ResponseCache(p)                                              # survives restart
    assert again.get("s1", "what is a prime number?") == "A number with exactly two divisors."
    assert c.stats()["hits"] == 1 and c.stats()["misses"] == 2


def test_expiry(tmp_path):
    now = [1000.0]
    c = ResponseCache(tmp_path / "c.json", ttl=60, clock=lambda: now[0])
    c.put("s", "what is a prime number", "x")
    now[0] += 59
    assert c.get("s", "what is a prime number") == "x"
    now[0] += 61
    assert c.get("s", "what is a prime number") is None
    assert c.stats()["entries"] == 0


def test_bounded_lru(tmp_path):
    now = [0.0]
    c = ResponseCache(tmp_path / "c.json", max_items=3, clock=lambda: now[0])
    for i in range(3):
        now[0] += 1
        c.put("s", f"what is item number {i}", f"r{i}")
    now[0] += 1
    c.get("s", "what is item number 0")            # refresh 0, so 1 is now the oldest
    now[0] += 1
    c.put("s", "what is item number 3", "r3")
    assert c.get("s", "what is item number 1") is None
    assert c.get("s", "what is item number 0") == "r0"
    assert c.stats()["entries"] == 3


def test_corrupt_file_is_ignored_and_clear_works(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    c = ResponseCache(p)
    assert c.get("s", "what is a prime number") is None
    c.put("s", "what is a prime number", "x")
    assert c.clear() == 1 and c.stats()["entries"] == 0


def test_prompt_edit_changes_scope(tmp_path):
    f = tmp_path / "prompt.txt"
    f.write_text("v1", encoding="utf-8")
    a = rc.make_scope("ollama", "m", rc.prompt_fingerprint(f))
    f.write_text("v2", encoding="utf-8")
    assert a != rc.make_scope("ollama", "m", rc.prompt_fingerprint(f))


def test_settings_defaults_and_bad_values():
    assert rc.is_enabled({}) is False
    assert rc.is_enabled({"response_cache_enabled": "true"}) is True
    assert rc.ttl_s({"response_cache_ttl_s": "junk"}) == rc.DEFAULT_TTL_S
    assert rc.ttl_s({"response_cache_ttl_s": -5}) == rc.DEFAULT_TTL_S
    assert rc.max_entries({"response_cache_max": 10**9}) == 5000


# ---------------- through the real turn loop ----------------

def _turn(monkeypatch, tmp_path, script, user_text, *, enabled=True, tool_results=None,
          system="sys"):
    stub_main = types.ModuleType("main")
    stub_main._NEEDS_LLM_ROUND = frozenset()
    monkeypatch.setitem(sys.modules, "main", stub_main)
    cache = ResponseCache(tmp_path / "cache.json")
    monkeypatch.setattr(rc, "_cache", cache)
    cfg = {"response_cache_enabled": enabled, "llm_model": "m1"}
    import core.llm_client as llm
    monkeypatch.setattr(llm, "_load_config", lambda: cfg)

    calls: list = []

    def fake_stream(messages, tools, cancel_event=None, **kw):
        calls.append(1)
        r = script[min(len(calls), len(script)) - 1]
        yield {"type": "done", "content": r.get("content", ""),
               "tool_calls": r.get("tool_calls", []), "stats": {}}

    monkeypatch.setattr(conversation_core, "call_llm_stream", fake_stream)
    monkeypatch.setattr(conversation_core, "_filter_tools_for_turn", lambda t, _u: t)
    monkeypatch.setattr(conversation_core, "_filter_tools_hard", lambda t, _u: t)

    host = MagicMock()
    host._handle_chat_command.return_value = False
    host._try_quick_intent.return_value = False
    host._cancel_current_turn = threading.Event()
    host._goals.list_active.return_value = []
    host._execute_tool_calls.return_value = (tool_results or [], False)
    host.ui.muted = True
    host._tools_for_current_turn.return_value = []
    host._TOOL_DISPATCH = {}
    host._prepare_conversation_messages.return_value = (
        [{"role": "system", "content": system}, {"role": "user", "content": user_text}], None)
    conversation_core.ConversationCoreMixin._process_message_inner(host, user_text)
    return calls, host, cache


def test_second_ask_is_served_from_cache_without_the_model(monkeypatch, tmp_path):
    q = "What is the capital of France?"
    calls, host, cache = _turn(monkeypatch, tmp_path, [{"content": "Paris."}], q)
    assert len(calls) == 1 and cache.stats()["entries"] == 1
    calls2, host2, _ = _turn(monkeypatch, tmp_path, [{"content": "SHOULD NOT BE USED"}],
                             "what is the capital of france")
    assert calls2 == []                                    # no model call
    host2.speak.assert_called_once_with("Paris.")
    saved = [c.args[0] for c in host2._conversation.append.call_args_list]
    assert saved[-1] == {"role": "assistant", "content": "Paris."}
    host2._obs.count.assert_any_call("response_cache.hit")


def test_disabled_by_default_never_stores_or_serves(monkeypatch, tmp_path):
    _, _, cache = _turn(monkeypatch, tmp_path, [{"content": "Paris."}],
                        "What is the capital of France?", enabled=False)
    assert cache.stats()["entries"] == 0


def test_time_sensitive_question_is_not_cached(monkeypatch, tmp_path):
    _, _, cache = _turn(monkeypatch, tmp_path, [{"content": "Sunny."}], "What is the weather like")
    assert cache.stats()["entries"] == 0


def test_turn_that_used_a_tool_is_not_cached(monkeypatch, tmp_path):
    call = {"id": "1", "function": {"name": "web_search", "arguments": {"query": "x"}}}
    _, _, cache = _turn(monkeypatch, tmp_path,
                        [{"tool_calls": [call]}, {"content": "Paris."}],
                        "What is the capital of France?",
                        tool_results=[("web_search", "Paris")])
    assert cache.stats()["entries"] == 0


def test_turn_with_memory_injected_is_not_cached(monkeypatch, tmp_path):
    # Short questions are QUICK tier (no memory retrieval at all); use an EVERYDAY-tier one.
    q = "Explain how a binary search works in detail with an example"
    assert rc.question_verdict(q)[0]
    _, _, cache = _turn(monkeypatch, tmp_path, [{"content": "It halves the range."}], q,
                        system="sys\n[Relevant memory]\nuser lives in Lyon")
    assert cache.stats()["entries"] == 0
    _, _, cache = _turn(monkeypatch, tmp_path, [{"content": "It halves the range."}], q)
    assert cache.stats()["entries"] == 1        # same turn without memory is cached


def test_different_model_does_not_get_anothers_answer(monkeypatch, tmp_path):
    q = "What is the capital of France?"
    _turn(monkeypatch, tmp_path, [{"content": "Paris."}], q)
    import core.llm_client as llm
    cfg = {"response_cache_enabled": True, "llm_model": "m2"}
    monkeypatch.setattr(llm, "_load_config", lambda: cfg)
    host = MagicMock()
    scope1 = conversation_core.ConversationCoreMixin._response_cache_scope(host, "ollama", "", {})
    cfg["llm_model"] = "m1"
    scope2 = conversation_core.ConversationCoreMixin._response_cache_scope(host, "ollama", "", {})
    assert scope1 != scope2
