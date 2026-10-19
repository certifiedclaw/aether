"""
Tier-based LLM routing for Aether.

Sits in front of core/llm_client.py's dual-host resolution and loop guard —
this module decides WHICH model to call, not HOW to reach it.

Three tiers:
  QUICK    -> small fast model (e.g. qwen2.5:1.5b) for greetings/small talk
  EVERYDAY -> default model (e.g. llama3.2:3b) for normal conversation + basic tools
  HARD     -> cloud fallback (Gemini) for loop-guard failures / deep reasoning

Two ways to use this:
  1. Streaming chat turns (main.py's actual conversation loop, with full
     message history + tool schemas): call `classify()` yourself, and on a
     HARD verdict pass core.llm_client.get_gemini_settings() into
     call_llm_stream(..., provider_override="gemini", url_override=...,
     model_override=...) for just that turn. This is what main.py does.
  2. One-off text asks with no history (planner/executor/dev_agent-style
     calls): use `route_and_generate()` below directly — its default
     call_ollama/call_gemini are already wired to core/llm_client, so it
     works with zero setup as long as a gemini_api_key is configured for
     the HARD tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional

from core.llm_client import call_llm_text as _call_llm_text
from core.llm_client import get_gemini_settings as _get_gemini_settings
from core.llm_client import GEMINI_BASE_URL


class Tier(Enum):
    QUICK = "quick"
    EVERYDAY = "everyday"
    HARD = "hard"


# ---- Config: adjust these to match your setup -----------------------------

TIER_MODELS = {
    Tier.QUICK: "qwen2.5:1.5b",
    Tier.EVERYDAY: "llama3.2:3b",
    Tier.HARD: "gemini",  # handled specially — routed to Gemini client, not Ollama
}

# Heuristic thresholds
QUICK_MAX_WORDS = 6
GREETING_PATTERNS = re.compile(
    r"^\s*(hi|hey|hello|yo|sup|good\s?(morning|afternoon|evening|night)|"
    r"thanks|thank you|ok|okay|cool|nice|bye|goodnight)\b[\s!.?]*$",
    re.IGNORECASE,
)
TOOL_INTENT_PATTERNS = re.compile(
    r"\b(search|find|open|launch|start|close|quit|run|execute|delete|remove|create|"
    r"schedule|remind|reminder|check|scan|look ?up|research|investigate|browse|calculate|read|write|fetch|send|install|"
    r"list|show|play|pause|stop|mute|unmute|turn|set|change|increase|decrease|raise|lower|"
    r"volume|brightness|wifi|bluetooth|screenshot|click|type|weather|news|"
    r"files?|folders?|directory|desktop|downloads|documents|"
    r"remember|recall|forget|"
    r"times|plus|minus|divided|multiplied)\b",
    re.IGNORECASE,
)
PERSONAL_CONTEXT_PATTERNS = re.compile(
    r"\b(?:my name is|call me|i(?:'m| am| live| work| prefer| like| love| hate| was born| have)\b)"
    r"|\b(?:my|our)\b"
    r"|\b(?:what|where|who|when|which|how)\b.{0,40}\b(?:my|our)\b"
    r"|\bwhere do i live\b|\bwhat do i (?:like|prefer|live)\b",
    re.IGNORECASE,
)
SELF_KNOWLEDGE_PATTERNS = re.compile(
    r"\b(?:who are you|what (?:model|provider|tools?|capabilities)|what can you do|"
    r"what do you have access to|are you (?:online|connected|conscious|self[- ]aware)|"
    r"can you access the internet)\b",
    re.IGNORECASE,
)
COMPLEX_HINTS = re.compile(
    r"\b(explain why|analyze|compare|design|architecture|debug|refactor|"
    r"plan|strategy|multi-step|step by step|reason through)\b",
    re.IGNORECASE,
)


@dataclass
class RouteDecision:
    tier: Tier
    model: str
    reason: str


def classify(message: str, *, has_active_task: bool = False) -> RouteDecision:
    """
    Pick an initial tier before any model call happens.
    `has_active_task` should be True if this message is a follow-up inside
    an in-progress tool/goal thread — those never get downgraded to QUICK.
    """
    stripped = message.strip()
    word_count = len(stripped.split())

    # Never route active task follow-ups to the quick tier
    if not has_active_task:
        if GREETING_PATTERNS.match(stripped):
            return RouteDecision(Tier.QUICK, TIER_MODELS[Tier.QUICK], "greeting pattern")
        if (word_count <= QUICK_MAX_WORDS
                and not TOOL_INTENT_PATTERNS.search(stripped)
                and not PERSONAL_CONTEXT_PATTERNS.search(stripped)
                and not SELF_KNOWLEDGE_PATTERNS.search(stripped)):
            return RouteDecision(Tier.QUICK, TIER_MODELS[Tier.QUICK], "short, no tool intent")

    if COMPLEX_HINTS.search(stripped):
        return RouteDecision(Tier.HARD, TIER_MODELS[Tier.HARD], "complex reasoning hint")

    return RouteDecision(Tier.EVERYDAY, TIER_MODELS[Tier.EVERYDAY], "default")


def wants_gemini_for_tools(message: str, *, flag: bool, provider: str, has_key: bool,
                           tier: "Tier") -> bool:
    """Opt-in cloud route (config gemini_for_tools=true) for tool-using turns.

    Never fires for QUICK turns (no tools), when Gemini is already the primary
    provider, or without a key. Default config leaves everything local.
    """
    if not (flag and has_key) or provider == "gemini" or tier == Tier.QUICK:
        return False
    return bool(TOOL_INTENT_PATTERNS.search(message or ""))


def escalate_to_hard(reason: str = "loop guard failure") -> RouteDecision:
    """Call this from the loop guard / retry path instead of re-trying the same tier."""
    return RouteDecision(Tier.HARD, TIER_MODELS[Tier.HARD], reason)


# ---- Generation dispatch ---------------------------------------------------

def default_call_ollama(model_name: str, message: str) -> str:
    """Default QUICK/EVERYDAY dispatch — plain text call to whichever Ollama
    (or LM Studio/OpenAI-compatible) endpoint is configured as the primary
    provider, at the given model tag."""
    return _call_llm_text(message, model=model_name)


def default_call_gemini(message: str) -> str:
    """Default HARD-tier dispatch — always Gemini, via its OpenAI-compatible
    endpoint, independent of whatever the primary configured provider is.
    Raises if no gemini_api_key is configured."""
    api_key, model = _get_gemini_settings()
    if not api_key:
        raise RuntimeError(
            "HARD tier routed to Gemini, but no gemini_api_key is configured. "
            "Add one in Settings → AI Engine → Gemini, or the HARD tier can't fire."
        )
    return _call_llm_text(
        message,
        url=GEMINI_BASE_URL,
        provider_override="gemini",
        model_override=model,
    )


def route_and_generate(
    message: str,
    *,
    has_active_task: bool,
    call_ollama: Callable[[str, str], str] = default_call_ollama,   # (model_name, message) -> response
    call_gemini: Callable[[str], str]      = default_call_gemini,   # (message) -> response
    validate: Optional[Callable[[str], bool]] = None,  # response validator (e.g. tool-call check)
    max_escalations: int = 1,
) -> tuple[str, RouteDecision]:
    """
    Classify, generate, and auto-escalate once on validation failure.
    `validate` should return False if the response looks broken (empty
    tool call, malformed output, etc.) — this is where your loop guard's
    failure detection plugs in.

    call_ollama/call_gemini default to the wired-up implementations above,
    so this works out of the box; pass your own if you need different
    behavior (e.g. reusing an existing conversation-aware client).
    """
    decision = classify(message, has_active_task=has_active_task)
    escalations = 0

    while True:
        if decision.tier == Tier.HARD:
            response = call_gemini(message)
        else:
            response = call_ollama(decision.model, message)

        if validate is None or validate(response) or escalations >= max_escalations:
            return response, decision

        escalations += 1
        decision = escalate_to_hard(reason=f"tier {decision.tier.value} failed validation")
