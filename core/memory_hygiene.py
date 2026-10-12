"""core/memory_hygiene.py -- keep failed turns out of long-term memory.

A small local model that once answered "How can I assist you?" to a real
question gets that exchange stored, retrieved the next time a similar question
arrives, and copies it: one bad turn becomes a permanent habit. These checks
decide whether an exchange is worth remembering. Pure functions, no I/O.
"""
from __future__ import annotations

import re

_FILLER_START = re.compile(
    r"^\s*(?:i'?m\s+(?:here|ready)\b|how\s+can\s+i\s+(?:assist|help)\b|what\s+(?:can\s+i|would\s+you\s+like)\b|"
    r"is\s+there\s+(?:something|anything)\b|sure,?\s+i\s+can\s+help\b|i\s+understand\.\s+how\s+can\b|"
    r"noted\.?\s*$)", re.I)
_FAILURE_MARKERS = (
    "<tool_call", "unknown action", "i wasn't able to finish that",
    "could not parse", "api key", "http error",
)


def reply_is_bad(reply: str) -> bool:
    r = (reply or "").strip()
    if not r:
        return True
    low = r.lower()
    if any(m in low for m in _FAILURE_MARKERS):
        return True
    return bool(_FILLER_START.match(r))


def user_text_is_noise(user_text: str) -> bool:
    return (user_text or "").lstrip().startswith("[SYSTEM_ALERT]")


def is_storable_exchange(user_text: str, reply: str) -> bool:
    return not (user_text_is_noise(user_text) or reply_is_bad(reply))


def stored_row_is_poisoned(content: str) -> bool:
    """For rows already in memory.db, content looks like 'User: ...\\nAssistant: ...'."""
    c = content or ""
    if "\nAssistant:" not in c:
        return False
    user, _, reply = c.partition("\nAssistant:")
    user = user.removeprefix("User:").strip()
    return user_text_is_noise(user) or reply_is_bad(reply)
