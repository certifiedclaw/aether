"""
smart_context.py — Aether v3 Context Compression (stub / implementation)

Both agent.py and main_window.py reference:
    from smart_context import compress_if_needed

This module provides that function. It trims the session message list when it
exceeds a token budget so the LLM context window doesn't overflow.

If you want full summarisation, set ENABLE_SUMMARIZE = True and ensure
the router is passed (it uses the fast model to produce a summary).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Approximate characters-per-token ratio for estimation (conservative)
_CHARS_PER_TOKEN = 4
# Default budget in tokens before compression kicks in
DEFAULT_TOKEN_BUDGET = 3000
# Whether to summarise the dropped messages (requires a live router)
ENABLE_SUMMARIZE = False


def _estimate_tokens(messages: list[dict]) -> int:
    total = sum(len(str(m.get("content", ""))) for m in messages)
    return total // _CHARS_PER_TOKEN


def estimate_tool_tokens(tools: list | None) -> int:
    """Rough token cost of a tool/function schema list, using the same
    chars-per-token heuristic as message content.

    Tool schemas are sent in the request body alongside `messages` but were
    previously invisible to compress_if_needed's budget — a request could
    be judged "fits in 3000 tokens" while actually carrying several
    thousand additional tokens of tool declarations, so the "fits" decision
    didn't guarantee the request fit the model's real context window.
    Callers should subtract this from the message token budget before
    compressing.
    """
    if not tools:
        return 0
    try:
        return len(json.dumps(tools)) // _CHARS_PER_TOKEN
    except Exception:
        return 0


def compress_if_needed(
    messages: list[dict],
    router=None,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> list[dict]:
    """
    Return a (possibly shortened) copy of `messages` that fits within
    `token_budget` tokens.

    Strategy:
    1. If the session is within budget, return as-is.
    2. Drop oldest non-system messages until under budget.
    3. Optionally replace the dropped block with a one-line summary
       produced by the router (when ENABLE_SUMMARIZE=True and router is live).

    Parameters
    ----------
    messages : list of {"role": str, "content": str}
        The full conversation / session history.
    router : optional router object with .chat_sync()
        Used for summarisation when ENABLE_SUMMARIZE is True.
    token_budget : int
        Maximum tokens to allow in the returned list.

    Returns
    -------
    list of message dicts — same structure, possibly shorter.
    """
    if not messages:
        return messages

    if _estimate_tokens(messages) <= token_budget:
        return messages

    # Separate system messages (always kept) from the rest
    system_msgs = [m for m in messages if m.get("role") == "system"]
    conv_msgs   = [m for m in messages if m.get("role") != "system"]

    # Drop oldest conversation turns until we're under budget.
    # A tool-response message must stay paired with the assistant message
    # that issued the matching tool_calls — dropping one without the other
    # leaves a dangling "tool" message that the LLM API will reject on the
    # next call. So when we drop an assistant message with tool_calls, we
    # also drop the tool-response message(s) that immediately follow it.
    #
    # Track the running estimate instead of recalling _estimate_tokens()
    # over the full remaining list on every iteration — that re-scans
    # every surviving message each time a message is dropped, which is
    # O(n^2) for a session that needs to shed many messages to fit.
    dropped: list[dict] = []
    system_tokens = _estimate_tokens(system_msgs)
    running_tokens = system_tokens + _estimate_tokens(conv_msgs)

    def _msg_tokens(m: dict) -> int:
        return len(str(m.get("content", ""))) // _CHARS_PER_TOKEN

    # Never drop the newest message: it is the user's current request. When the
    # system prompt alone exceeds the budget (large tool schema -> budget floor),
    # dropping everything leaves the model with no question to answer.
    while len(conv_msgs) > 1 and running_tokens > token_budget:
        msg = conv_msgs.pop(0)
        running_tokens -= _msg_tokens(msg)
        dropped.append(msg)
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            while conv_msgs and conv_msgs[0].get("role") == "tool":
                tool_msg = conv_msgs.pop(0)
                running_tokens -= _msg_tokens(tool_msg)
                dropped.append(tool_msg)
    # Also guard the other direction: if what's left now starts with an
    # orphaned "tool" message (its assistant call got dropped above, or the
    # budget was already tight enough at the very first message), drop it too.
    while len(conv_msgs) > 1 and conv_msgs[0].get("role") == "tool":
        dropped.append(conv_msgs.pop(0))

    # Optionally summarise the dropped block
    if dropped and ENABLE_SUMMARIZE and router is not None:
        try:
            dropped_text = "\n".join(
                f"{m['role']}: {m['content'][:300]}" for m in dropped
            )
            summary_prompt = (
                f"Summarise these earlier messages in 1-2 sentences:\n{dropped_text}"
            )
            summary = router.chat_sync([{"role": "user", "content": summary_prompt}])
            summary_msg = {"role": "system", "content": f"[Earlier context] {summary}"}
            result = system_msgs + [summary_msg] + conv_msgs
        except Exception as exc:
            logger.warning("smart_context summarisation failed: %s", exc)
            result = system_msgs + conv_msgs
    else:
        if dropped:
            logger.debug(
                "smart_context: dropped %d messages to fit token budget", len(dropped)
            )
        result = system_msgs + conv_msgs

    return result
