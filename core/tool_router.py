"""
tool_router.py — per-turn tool subset selection for EVERYDAY-tier turns.

core/tier_router.py already sends QUICK-tier turns (greetings, "thanks",
short no-intent replies) zero tools. HARD-tier turns keep the full schema —
that tier is already an intentional escalation for deep/complex work, and
second-guessing what it might need adds risk for little benefit.

This module only narrows the middle tier: ordinary conversation, which is
most turns, and which previously received the entire ~28-tool catalog
(plus goal/plugin/integration declarations) regardless of whether the
message had anything to do with tools at all. See the audit's Layer 3 Tool
Router recommendation — "give the model only the relevant tool subset".

Deliberately conservative: this filters BY CATEGORY (a handful of tools
sharing a topic), never down to a single tool, and a turn matching no
category falls back to a small baseline of commonly-needed tools rather
than either the full catalog or nothing. A miscategorized turn should
degrade gracefully (missing a rarely-needed tool for one turn) rather than
silently fail outright — same philosophy as tier_router's own heuristics.
Explicit output-only requests with no action category are the exception:
tools can derail a simple formatting answer, so those turns get no schema.
"""

from __future__ import annotations

import re

# category pattern -> tool names it should pull in. A turn can match more
# than one category; the result is the union.
_CATEGORIES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\b(files?|folders?|directory|directories|desktop|downloads|documents?|list|rename|delete|move|copy|undo)\b", re.I),
     ["file_controller", "undo", "file_processor"]),
    (re.compile(r"\b(open|launch|start|close|quit|volume|louder|quieter|mute|unmute|brightness|wifi|bluetooth|"
                r"screenshot|click|type|turn (up|down|on|off))\b", re.I),
     ["open_app", "computer_settings", "computer_control", "desktop_control", "screen_process"]),
    (re.compile(r"\b(search|google|look ?up|news|weather)\b", re.I),
     ["web_search", "weather_report"]),
    (re.compile(r"\b(message|text|email|whatsapp|telegram|send .* to)\b", re.I),
     ["send_message"]),
    (re.compile(r"\b(code|script|program|function|bug|debug|refactor)\b", re.I),
     ["code_helper", "dev_agent"]),
    (re.compile(r"\b(browser|website|\burl\b|\btab\b|navigate|webpage)\b", re.I),
     ["browser_control"]),
    (re.compile(r"\b(youtube|video|play music|song)\b", re.I),
     ["youtube_video"]),
    (re.compile(r"\b(remind|reminder|schedule|calendar|alarm|timer)\b", re.I),
     ["reminder"]),
    (re.compile(r"\b(flight|plane ticket|book .* trip)\b", re.I),
     ["flight_finder"]),
    (re.compile(r"\b(game|steam)\b", re.I),
     ["game_updater"]),
    (re.compile(r"\b(pdf|index (my|the) document|search (my|the) document)\b", re.I),
     ["pdf_tool", "rag_query"]),
    (re.compile(r"\b(whois|\bdns\b|subdomain|breach|port scan|osint)\b", re.I),
     ["osint_lookup"]),
    (re.compile(r"\b(diagnostic|crash|health check|logs)\b", re.I),
     ["diagnostics", "crash_report"]),
    (re.compile(r"\b(remember|recall|forget|what did i tell you|my preferences)\b", re.I),
     ["memory_tool", "save_memory"]),
    (re.compile(r"\b(multi-?step|several steps|plan out|agent)\b", re.I),
     ["agent_task"]),
    (re.compile(r"\b(shut ?down|turn off aether|exit aether)\b", re.I),
     ["shutdown_aether"]),
]

# Commonly-needed tools kept available even when no category matches, so an
# unrecognized phrasing doesn't lose the most frequent ambient asks.
_BASELINE = ["open_app", "web_search", "weather_report", "send_message", "save_memory", "undo"]

# Small/cheap tools + integrations that stay available on every EVERYDAY
# turn regardless of category match — narrowing these wouldn't save much
# and they're either already gated by their own availability checks
# (langchain/open_interpreter/llama_index) or needed unpredictably
# (save_memory, undo, goal_manager).
_ALWAYS_THROUGH = {"save_memory", "undo", "goal_manager",
                    "langchain", "open_interpreter", "llama_index"}

_RESPONSE_ONLY = re.compile(r"\b(?:output|reply|respond|return)\s+only\b", re.I)


def _is_clear_response_only(text: str) -> bool:
    """Avoid advertising action tools for explicit output-only requests."""
    return bool(_RESPONSE_ONLY.search(text or "")) and not any(
        pattern.search(text or "") for pattern, _ in _CATEGORIES
    )


def select_tool_names(user_text: str) -> set[str]:
    """Union of every category this turn's text matches, or the baseline
    set if nothing matches. Explicit output-only requests with no action
    category receive no tools rather than the fallback baseline."""
    text = user_text or ""
    matched: set[str] = set()
    for pattern, names in _CATEGORIES:
        if pattern.search(text):
            matched.update(names)
    if not matched:
        if _is_clear_response_only(text):
            return set()
        matched.update(_BASELINE)
    return matched | _ALWAYS_THROUGH


# HARD turns are deep/complex work: keep a broad research/code/file toolkit on
# top of whatever the text matched, but not the whole ~31-tool catalog (the
# schema alone overran the 3000-token message budget and crowded out history).
# File-writing tools are deliberately NOT here: on a local fallback the model
# once "analyzed a note" by trying to write it to the Desktop. They still come
# in when the text mentions files/folders (see _CATEGORIES).
_HARD_EXTRA = {"web_search", "agent_task", "code_helper", "dev_agent", "browser_control",
               "rag_query", "memory_tool"}


def select_tool_names_hard(user_text: str) -> set[str]:
    if _is_clear_response_only(user_text or ""):
        return set()
    return select_tool_names(user_text) | _HARD_EXTRA


def filter_tools_hard(all_tools: list[dict], user_text: str) -> list[dict]:
    """Like filter_tools but with the wider HARD-tier toolkit."""
    wanted = select_tool_names_hard(user_text)
    out = []
    for t in all_tools:
        name = (t.get("function") or {}).get("name") or t.get("name")
        if name is None or name in wanted:
            out.append(t)
    return out


def filter_tools(all_tools: list[dict], user_text: str) -> list[dict]:
    """Filter an OLLAMA_TOOLS-shaped list (`{"type": "function", "function":
    {"name": ...}}`) down to the subset relevant to this turn. Entries with
    no readable name pass through unfiltered rather than being silently
    dropped."""
    wanted = select_tool_names(user_text)
    out = []
    for t in all_tools:
        name = (t.get("function") or {}).get("name") or t.get("name")
        if name is None or name in wanted:
            out.append(t)
    return out
