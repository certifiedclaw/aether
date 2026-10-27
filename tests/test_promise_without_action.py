"""Roadmap #92: small models that announce an action but never call a tool.

Covers the pure detector (core.conversation_core._looks_like_unfulfilled_promise)
and checks the one-shot retry is wired into the tool loop.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import conversation_core
from core.conversation_core import _PROMISE_NUDGE, _looks_like_unfulfilled_promise


@pytest.mark.parametrize("text", [
    "I'll research that now.",
    "I will research the Artemis program and get back to you.",
    "Sure, I'll look into that.",
    "Okay! Let me search for that.",
    "Let me check the weather for you.",
    "I'm going to open the browser now.",
    "Alright, I’ll pull up the latest news.",
    "Got it. I will start by searching the web.",
    "Absolutely, I'm gonna look that up.",
    "One moment. I'll run a search on that.",   # promise in the last sentence
    "I'll first gather the sources and then summarize them.",
])
def test_detects_pure_promises(text):
    assert _looks_like_unfulfilled_promise(text), text


@pytest.mark.parametrize("text", [
    "",
    "   ",
    None,
    "The capital of France is Paris.",
    "240 times 17 is 4080.",
    "I'll keep that in mind.",                   # not an action verb
    "I will be happy to help whenever you're ready.",
    "Would you like me to research that? I'll do it right away.",   # a question to the user
    "Do you want me to look into it?",
    "I can research that if you tell me which program you mean.",   # offer, not a promise
    "Here is the code:\n```python\nprint('I will search')\n```",
    "Done. I searched for it and found three results.",   # past tense
    "You said you'll research it yourself.",     # not first person promise at sentence start
    "x" * 500,                                    # long answers are never treated as bare promises
    "I'll research that now. " + "The answer is long and detailed. " * 20,
])
def test_ignores_real_answers_and_non_promises(text):
    assert not _looks_like_unfulfilled_promise(text), text


def test_nudge_is_plain_instruction_without_markup():
    assert "call" in _PROMISE_NUDGE.lower()
    assert "tool" in _PROMISE_NUDGE.lower()
    assert "<" not in _PROMISE_NUDGE


def _loop_source() -> str:
    # The class that owns _process_message_inner may be a mixin; search the module.
    return inspect.getsource(conversation_core)


def test_retry_is_one_shot_and_scoped_to_working_messages():
    src = _loop_source()
    assert "_promise_retried = False" in src
    block = src[src.index("Promise without action (#92)"):]
    block = block[: block.index("if not final_tool_calls:")]
    # Guard conditions: only when no tool call, tools were offered, not already
    # retried, and the reply wasn't cut off mid-thought.
    for needle in ("not final_tool_calls", "and tools", "not _promise_retried",
                   "not _was_truncated", "_looks_like_unfulfilled_promise(final_content)"):
        assert needle in block, needle
    # The promise + nudge go into the working messages only, never the saved chat.
    assert "messages.append" in block
    assert "self._conversation.append" not in block
    assert "_promise_retried = True" in block
    assert block.rstrip().endswith("continue")


def test_retry_runs_after_fake_tool_call_recovery():
    src = _loop_source()
    assert src.index("llm.fake_tool_call_unrecoverable") < src.index("Promise without action (#92)")


# ---------------- end-to-end through the real tool loop ----------------

def _run_turn(monkeypatch, script, user_text="research the Artemis program for me using the web"):
    """Drive ConversationCoreMixin._process_message_inner against a mock host and a
    scripted LLM. Returns (llm_call_snapshots, host)."""
    import threading
    import types
    from unittest.mock import MagicMock

    stub_main = types.ModuleType("main")          # real main.py needs PortAudio
    stub_main._NEEDS_LLM_ROUND = frozenset()
    monkeypatch.setitem(sys.modules, "main", stub_main)

    calls: list = []

    def fake_stream(messages, tools, cancel_event=None, **kw):
        calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        r = script[min(len(calls), len(script)) - 1]
        yield {"type": "done", "content": r.get("content", ""),
               "tool_calls": r.get("tool_calls", []), "stats": {}}

    tool_schema = [{"type": "function", "function": {
        "name": "web_search", "description": "search the web",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}}]
    monkeypatch.setattr(conversation_core, "call_llm_stream", fake_stream)
    monkeypatch.setattr(conversation_core, "_filter_tools_for_turn", lambda t, _u: t)
    monkeypatch.setattr(conversation_core, "_filter_tools_hard", lambda t, _u: t)

    host = MagicMock()
    host._handle_chat_command.return_value = False
    host._try_quick_intent.return_value = False
    host._cancel_current_turn = threading.Event()
    host._goals.list_active.return_value = []
    host._execute_tool_calls.return_value = ([], False)
    host.ui.muted = True
    host._tools_for_current_turn.return_value = tool_schema
    host._TOOL_DISPATCH = {"web_search": object()}
    host._prepare_conversation_messages.return_value = (
        [{"role": "system", "content": "sys"}, {"role": "user", "content": user_text}], None)

    conversation_core.ConversationCoreMixin._process_message_inner(host, user_text)
    return calls, host


_TOOL_CALL = {"id": "1", "function": {"name": "web_search", "arguments": {"query": "artemis"}}}


def test_e2e_promise_is_retried_once_and_tool_then_runs(monkeypatch):
    calls, host = _run_turn(monkeypatch, [
        {"content": "Sure, I'll research that now."},
        {"tool_calls": [_TOOL_CALL]},
        {"content": "Artemis aims to return humans to the Moon."},
    ])
    assert len(calls) == 3
    retry_msgs = calls[1]["messages"]
    assert retry_msgs[-2] == {"role": "assistant", "content": "Sure, I'll research that now."}
    assert retry_msgs[-1] == {"role": "user", "content": _PROMISE_NUDGE}
    assert calls[1]["tools"], "retry must still offer tools"
    assert host._execute_tool_calls.call_count == 1
    host._obs.count.assert_any_call("llm.promise_without_action_retry")
    # Saved chat history keeps neither the promise nor the nudge.
    saved = [c.args[0].get("content", "") for c in host._conversation.append.call_args_list]
    assert not any("I'll research" in s or s == _PROMISE_NUDGE for s in saved)
    assert saved[-1] == "Artemis aims to return humans to the Moon."


def test_e2e_normal_answer_is_not_retried(monkeypatch):
    calls, host = _run_turn(monkeypatch, [
        {"content": "The Artemis program is NASA's plan to return humans to the Moon."},
    ])
    assert len(calls) == 1
    assert host._execute_tool_calls.call_count == 0


def test_e2e_second_promise_is_not_retried_again(monkeypatch):
    calls, host = _run_turn(monkeypatch, [
        {"content": "I'll research that now."},
        {"content": "Let me look into it right away."},
        {"content": "should never be reached"},
    ])
    assert len(calls) == 2          # exactly one retry, then the turn ends
    assert host._execute_tool_calls.call_count == 0


def test_e2e_no_retry_when_no_tools_offered(monkeypatch):
    # A QUICK-tier turn sends no tools; a retry there could not help and would just waste a round.
    calls, _ = _run_turn(monkeypatch, [{"content": "I'll research that now."}],
                         user_text="hi")
    assert len(calls) == 1
