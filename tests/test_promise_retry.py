"""Roadmap #92: a reply that only promises an action ('I will research...') is
detected so the turn loop can nudge the model once to actually call the tool."""
import inspect

import pytest

from core import conversation_core as cc


@pytest.mark.parametrize("text", [
    "I will research this for you now.",
    "I'll look that up.",
    "Sure, I'll search the web for that.",
    "Let me check the latest news on that.",
    "Okay! I am going to open the file and read it.",
    "I will now run the command.",
    "I’ll fetch the page.",
])
def test_promises_are_detected(text):
    assert cc._is_unfulfilled_promise(text)


@pytest.mark.parametrize("text", [
    "",
    "   ",
    "The capital of France is Paris.",
    "2 + 2 is 4.",
    "I will research this. Here is what I found: the sky is blue because of "
    "Rayleigh scattering.\n\nMore detail follows in a second paragraph.",
    "Would you like me to search for that?",
    "I will not be able to do that.",
    "I think the answer is 42.",
    '<tool_call>{"name": "web_search"}</tool_call>',
    "```python\nprint('I will run')\n```",
    "I will research " + "x" * 400,
])
def test_real_answers_are_not_flagged(text):
    assert not cc._is_unfulfilled_promise(text)


def test_turn_loop_is_wired_to_retry_once_with_nudge():
    src = inspect.getsource(cc.ConversationCoreMixin)
    assert "_promise_retried" in src
    assert "_is_unfulfilled_promise(" in src
    assert "PROMISE_NUDGE" in src
    # the retry must not be allowed to fire a second time
    assert src.count("_promise_retried = True") == 1
