"""Short follow-ups ("yes", "retry", "view") must keep their tools.

Regression for a live log: after "open roblox" -> "retry"/"failed", and after
"Would you like to view the logs?" -> "yes"/"view", every follow-up went to the
no-tools QUICK tier and the model answered "I can't open apps / view files".
"""
import inspect

import pytest

from core import tool_router
from core.tier_router import Tier, classify

FOLLOWUPS = [
    "yes", "yeah", "yep", "sure", "please", "retry", "try again", "again",
    "failed", "it failed", "that failed", "didn't work", "view", "show me",
    "open it", "do it", "go ahead", "continue", "the first one", "yes please",
    "no that's wrong", "same error",
]


@pytest.mark.parametrize("text", FOLLOWUPS)
def test_followup_after_a_reply_is_everyday_not_quick(text):
    d = classify(text, follows_turn=True)
    assert d.tier == Tier.EVERYDAY and d.reason == "follow-up"


@pytest.mark.parametrize("text", ["retry", "failed", "try again", "do it"])
def test_same_words_with_no_prior_reply_keep_the_old_quick_behaviour(text):
    assert classify(text, follows_turn=False).tier == Tier.QUICK


@pytest.mark.parametrize("text", ["ok", "okay", "thanks", "thank you", "cool", "hey", "bye"])
def test_acknowledgements_and_greetings_stay_quick_even_mid_conversation(text):
    assert classify(text, follows_turn=True).tier == Tier.QUICK


@pytest.mark.parametrize("text", ["what is 2+2", "how are you", "whats the capital of france"])
def test_ordinary_short_questions_stay_quick_mid_conversation(text):
    assert classify(text, follows_turn=True).tier == Tier.QUICK


def test_a_long_message_starting_with_a_followup_word_is_not_treated_as_one():
    d = classify("yes I would like a long explanation of how bridges are built", follows_turn=True)
    assert d.reason != "follow-up"


def test_active_task_still_never_downgrades():
    assert classify("yes", has_active_task=True).tier != Tier.QUICK
    assert classify("yes", has_active_task=True, follows_turn=True).tier != Tier.QUICK


def test_bare_view_is_tool_intent_even_without_context():
    assert classify("view my logs").tier != Tier.QUICK


@pytest.mark.parametrize("text", [
    "do deep resurche on it", "resarch loggerhead turtles", "can you research bridges",
    "do some reserch", "RESEARCH it",
])
def test_misspelled_research_is_still_tool_intent(text):
    assert classify(text).tier != Tier.QUICK
    assert "web_search" in tool_router.select_tool_names(text)


@pytest.mark.parametrize("text", ["reach the top", "resource usage", "what is a resin"])
def test_research_typo_pattern_does_not_swallow_ordinary_words(text):
    # (select_tool_names always includes web_search in its default set, so check
    # the intent pattern itself: these short messages must stay QUICK.)
    assert classify(text).tier == Tier.QUICK
    assert not tool_router._CATEGORIES[[i for i, (pat, _) in enumerate(tool_router._CATEGORIES)
                                        if "investigate" in pat.pattern][0]][0].search(text)


def test_conversation_core_passes_the_follow_up_signal_to_the_router():
    from core.conversation_core import ConversationCoreMixin

    source = inspect.getsource(ConversationCoreMixin._process_message_inner)
    call = source.split("_classify_tier(", 1)[1].split(")", 1)[0]
    assert "follows_turn=_follows_turn" in call
    assert 'm.get("role") == "assistant"' in source.split("_classify_tier(", 1)[0]
