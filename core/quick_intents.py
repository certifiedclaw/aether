"""core/quick_intents.py -- run simple voice commands without asking the LLM.

Why: with a small local model (qwen2.5:7b etc.) a request like "turn the volume
down a little" is unreliable. The model invents action names ("set_volume",
"web_search"), answers from imagination (a made-up Desktop file list), or just
says "How can I assist you?". For a handful of unambiguous phrases there is
nothing to reason about, so map them straight to the real tool.

Design rules:
  * Conservative. Anything that does not match EXACTLY goes to the LLM as before.
  * Pure (no I/O): match_all() only returns a plan; the caller executes it.
  * Compound requests ("X, then Y") run only if EVERY clause matches; otherwise
    the whole message goes to the LLM, so we never half-handle a request.

Plan item: (tool_name, args, friendly) where friendly(result) -> spoken reply.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

Friendly = Optional[Callable[[str], str]]
Plan = list  # list[tuple[str, dict, Friendly]]

_TAIL = r"[\s.!?]*$"
_POLITE = r"^(?:(?:hey|ok|okay)\s+\w+[,\s]+)?(?:(?:please|can you|could you|would you|will you)\s+)*"
_SPLIT = re.compile(r"\s*(?:,|;|\.)?\s*(?:\band then\b|\bthen\b|\bafter that\b|\band also\b)\s+", re.I)

_UP = r"(?:up|louder|higher|increase|raise)"
_DOWN = r"(?:down|quieter|lower|softer|decrease|reduce)"
_AMOUNT = r"(?:\s+(?:a\s+(?:little|bit|touch|notch)|slightly|some|more|please))*"

_VOL_UP = [
    re.compile(_POLITE + r"(?:turn|crank|bring|pump|make)\s+(?:the\s+)?(?:volume|sound)\s+up" + _AMOUNT + _TAIL, re.I),
    re.compile(_POLITE + r"(?:turn|crank|pump)\s+up\s+(?:the\s+)?(?:volume|sound)" + _AMOUNT + _TAIL, re.I),
    re.compile(_POLITE + r"(?:increase|raise)\s+(?:the\s+)?(?:volume|sound)" + _AMOUNT + _TAIL, re.I),
    re.compile(_POLITE + r"(?:volume\s+up|louder|make it louder|turn it up)" + _AMOUNT + _TAIL, re.I),
]
_VOL_DOWN = [
    re.compile(_POLITE + r"(?:turn|bring|lower|put|make)\s+(?:the\s+)?(?:volume|sound)\s+down" + _AMOUNT + _TAIL, re.I),
    re.compile(_POLITE + r"(?:turn|bring)\s+down\s+(?:the\s+)?(?:volume|sound)" + _AMOUNT + _TAIL, re.I),
    re.compile(_POLITE + r"(?:decrease|reduce|lower)\s+(?:the\s+)?(?:volume|sound)" + _AMOUNT + _TAIL, re.I),
    re.compile(_POLITE + r"(?:volume\s+down|quieter|softer|make it quieter|turn it down)" + _AMOUNT + _TAIL, re.I),
]
_VOL_SET = re.compile(
    _POLITE + r"(?:set|put|change|turn)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)\s+(\d{1,3})\s*(?:%|percent)?" + _TAIL, re.I)
_MUTE = re.compile(_POLITE + r"(?:mute|mute (?:the )?(?:sound|volume|audio|computer|pc))" + _TAIL, re.I)
_UNMUTE = re.compile(_POLITE + r"(?:unmute|unmute (?:the )?(?:sound|volume|audio|computer|pc))" + _TAIL, re.I)

_WEATHER = re.compile(
    _POLITE + r"(?:(?:what(?:'s|\s+is)|how(?:'s|\s+is)|tell\s+me|give\s+me|show\s+me|get\s+me)\s+(?:the\s+)?)?"
    r"(?:current\s+)?weather(?:\s+(?:like|report|forecast))?\s+(?:in|for|at)\s+"
    r"([A-Za-z][A-Za-z .'\-]{1,40}?)"
    r"(?:\s+(?:right\s+now|now|today|tonight|tomorrow))?" + _TAIL, re.I)
_WEATHER_TOMORROW = re.compile(r"\btomorrow\b", re.I)

_FOLDERS = r"(desktop|downloads|documents|pictures|music|videos)"
_LIST_FILES = re.compile(
    _POLITE + r"(?:list|show|display|what(?:'s|\s+is)\s+on|what\s+files\s+are\s+(?:on|in))"
    r"(?:\s+me)?\s*(?:all\s+)?(?:the\s+)?(?:files|contents|stuff|things)?\s*(?:on|in|of)?\s*(?:my\s+|the\s+)?"
    + _FOLDERS + r"(?:\s+(?:folder|directory))?" + _TAIL, re.I)

_OPEN = re.compile(
    _POLITE + r"(?:open|launch|start|run)\s+(?:up\s+)?(?:the\s+)?"
    r"(?:(?:an?\s+)?(?:application|app|program)\s+(?:called|named)\s+|(?:application|app|program)\s+)?"
    r"([A-Za-z0-9][A-Za-z0-9 .+#'\-]{1,40}?)" + _TAIL, re.I)
# Things "open ..." usually means something other than launching an installed app.
_OPEN_BLOCK = re.compile(
    r"\b(tab|window|file|folder|page|website|site|link|url|browser|door|settings|menu|"
    r"document|doc|pdf|email|mail|chat|conversation|new|last|previous|that|this|it|them|"
    r"google|youtube|http|www)\b|\.\w{2,4}\b", re.I)


_SEARCH = re.compile(
    _POLITE + r"(?:search|look\s+up|google|find\s+out)(?:\s+(?:the\s+web|the\s+internet|online|web))?"
    r"(?:\s+for)?\s+(.{3,120}?)" + _TAIL, re.I)
# Searches that really mean "find a file on this computer" etc. stay with the LLM.
_SEARCH_BLOCK = re.compile(r"\b(my|on my|this computer|my pc|my files?|folder|desktop|downloads)\b", re.I)


def _volume_reply(label: str) -> Callable[[str], str]:
    def f(result: str) -> str:
        r = (result or "").strip()
        bad = ("unknown action", "could not", "action failed", "failed", "not installed", "no action")
        if any(b in r.lower() for b in bad):
            return r
        return label
    return f


def _list_reply(folder: str) -> Callable[[str], str]:
    """Speak a short summary instead of reading 60+ file names aloud."""
    def f(result: str) -> str:
        r = (result or "").strip()
        m = re.match(r"Contents of .*?\((\d+) items?\)", r)
        if not m:
            return r
        names = []
        for ln in r.splitlines()[1:]:
            ln = ln.strip()
            if ln:
                names.append(re.sub(r"\s*\([\d.]+\s*[KMGB]+\)\s*$", "", ln.lstrip("\U0001F4C1\U0001F4C4 ").strip()))
        n = int(m.group(1))
        if n == 0:
            return f"Your {folder} is empty."
        shown = ", ".join(names[:5])
        more = f" and {n - 5} more" if n > 5 else ""
        return f"Your {folder} has {n} items: {shown}{more}."
    return f


def _match_clause(clause: str):
    """Return one plan item for a single clause, or None."""
    c = clause.strip()
    if not c:
        return None
    for rx in _VOL_UP:
        if rx.match(c):
            return ("computer_settings", {"action": "volume_up"}, _volume_reply("Volume up."))
    for rx in _VOL_DOWN:
        if rx.match(c):
            return ("computer_settings", {"action": "volume_down"}, _volume_reply("Volume down."))
    m = _VOL_SET.match(c)
    if m:
        level = max(0, min(100, int(m.group(1))))
        return ("computer_settings", {"action": "volume_set", "value": level},
                _volume_reply(f"Volume set to {level} percent."))
    if _UNMUTE.match(c):
        return ("computer_settings", {"action": "unmute"}, _volume_reply("Unmuted."))
    if _MUTE.match(c):
        return ("computer_settings", {"action": "mute"}, _volume_reply("Muted."))
    m = _WEATHER.match(c)
    if m:
        city = m.group(1).strip(" .'-")
        when = "tomorrow" if _WEATHER_TOMORROW.search(c) else "today"
        return ("weather_report", {"city": city, "time": when}, None)
    m = _LIST_FILES.match(c)
    if m:
        folder = m.group(1).lower()
        return ("file_controller", {"action": "list", "path": folder}, _list_reply(folder))
    m = _SEARCH.match(c)
    if m:
        q = m.group(1).strip(" .?!")
        if q and not _SEARCH_BLOCK.search(q):
            return ("web_search", {"query": q}, None)
    m = _OPEN.match(c)
    if m:
        name = m.group(1).strip(" .'-")
        if name and not _OPEN_BLOCK.search(name) and len(name.split()) <= 5:
            return ("open_app", {"app_name": name}, None)
    return None


def match_all(text: str) -> Plan | None:
    """Plan for the whole message, or None if ANY part needs the LLM."""
    t = (text or "").strip()
    if not t or len(t) > 160:
        return None
    clauses = [x for x in _SPLIT.split(t) if x and x.strip()]
    if not clauses or len(clauses) > 3:
        return None
    plan = []
    for cl in clauses:
        item = _match_clause(cl)
        if item is None:
            return None
        plan.append(item)
    return plan
