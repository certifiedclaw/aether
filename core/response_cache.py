"""core/response_cache.py - roadmap #54: answer a repeated question without the model.

OPT-IN (config ``response_cache_enabled``, default false). A wrong cached answer is
worse than a slow right one, so this is deliberately narrow. A reply is only cached,
and only served, when ALL of these hold:

  question   a self-contained factual question: starts with a question word, 3-40
             words, nothing about the user ("my", "I", "you"), nothing that points
             back into the conversation ("it", "that", "again", "what about"), nothing
             that changes ("today", "latest", "price", "weather", "time", "status"),
             nothing creative or random ("joke", "story", "random"), no digits-only
             year/date references.
  turn       no tool ran, no memory was injected, nothing truncated/cancelled/failed,
             not escalated to the HARD tier, no goal active.
  reply      non-empty and not an error or apology message.

A hit is keyed by the normalized question AND a scope (provider, model, a hash of the
system prompt), so switching model or editing prompt.txt never serves old answers.
Entries expire (``response_cache_ttl_s``, default 24 h) and the store is bounded
(``response_cache_max``, default 200, least recently used goes first). Stored in
``response_cache.json`` in the data dir. Never raises.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_TTL_S = 24 * 3600
DEFAULT_MAX = 200

_QUESTION_START = {
    "what", "who", "whom", "whose", "where", "why", "how", "which", "define", "explain",
    "is", "are", "was", "were", "does", "do", "did", "can", "could", "should", "will", "would",
    "whats", "whos", "wheres", "hows", "whys", "whens",       # contractions, apostrophe removed
}
# Words that make an answer depend on who is asking, when, or on earlier turns.
_PERSONAL = {"i", "im", "ive", "id", "me", "my", "mine", "myself", "we", "our", "ours", "us",
             "you", "your", "yours", "yourself", "aether"}
_REFERENCE = {"it", "its", "this", "that", "these", "those", "they", "them", "their", "he", "she",
              "him", "her", "his", "above", "previous", "previously", "earlier", "before", "again",
              "also", "another", "else", "same", "other", "former", "latter", "instead"}
_CHANGING = {"today", "tonight", "tomorrow", "yesterday", "now", "current", "currently", "latest",
             "recent", "recently", "newest", "news", "weather", "forecast", "temperature", "time",
             "date", "day", "week", "month", "year", "price", "cost", "score", "stock", "status",
             "battery", "disk", "ram", "cpu", "memory", "volume", "file", "files", "folder",
             "screen", "clipboard", "here", "nearby", "near", "open", "running", "playing",
             "tonight", "morning", "evening", "weekend", "season", "live", "trending", "update",
             "version", "available", "release", "released", "winner", "won", "election"}
_CREATIVE = {"joke", "jokes", "story", "poem", "riddle", "random", "surprise", "brainstorm",
             "idea", "ideas", "suggest", "suggestion", "recommend", "recommendation", "write",
             "draft", "generate", "create", "song", "limerick", "opinion",
             "think", "favorite", "favourite", "best", "worst"}
_STARTS_WITH_REF = ("and ", "but ", "so ", "also ", "then ", "what about", "how about", "what if")
_BAD_REPLY = re.compile(
    r"^(i wasn.t able|i couldn.t|i can.t|i cannot|sorry|unknown tool|error|that failed|"
    r"cancelled|blocked|err\b|wrn\b)|\b(traceback|exception)\b", re.I)

_FILLER = re.compile(r"^(?:(?:hey|hi|ok|okay|please|aether|jarvis)[\s,]+)+", re.I)
_FILLER2 = re.compile(r"^(?:can|could|would|will) you (?:please )?(?:tell me|explain|say)\s+", re.I)
_FILLER3 = re.compile(r"^(?:tell me|i (?:want|need) to know|i.m curious)\s+", re.I)


# ── settings ──────────────────────────────────────────────────────────────

def is_enabled(cfg: dict | None) -> bool:
    v = (cfg or {}).get("response_cache_enabled", False)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def ttl_s(cfg: dict | None) -> float:
    try:
        v = float((cfg or {}).get("response_cache_ttl_s", DEFAULT_TTL_S))
    except (TypeError, ValueError):
        return DEFAULT_TTL_S
    return v if v > 0 else DEFAULT_TTL_S


def max_entries(cfg: dict | None) -> int:
    try:
        v = int((cfg or {}).get("response_cache_max", DEFAULT_MAX))
    except (TypeError, ValueError):
        return DEFAULT_MAX
    return max(1, min(v, 5000))


# ── what may be cached ────────────────────────────────────────────────────

def normalize(text: str) -> str:
    t = " ".join(str(text or "").lower().split())
    for pat in (_FILLER, _FILLER2, _FILLER3):
        t = pat.sub("", t)
    t = re.sub(r"[^\w\s']", " ", t.replace("’", "'"))
    return " ".join(t.split())


def _words(norm: str) -> list[str]:
    return [w.replace("'", "") for w in norm.split()]


def question_verdict(text: str) -> tuple[bool, str]:
    """(cacheable, reason). The reason is logged so you can see why a repeat missed."""
    raw = str(text or "")
    if len(raw) > 300:
        return False, "too long"
    norm = normalize(raw)
    words = _words(norm)
    if not 3 <= len(words) <= 40:
        return False, "needs 3-40 words"
    if norm.startswith(_STARTS_WITH_REF):
        return False, "refers to the conversation"
    if words[0] not in _QUESTION_START:
        return False, "not a factual question"
    s = set(words)
    for label, bad in (("about the user", _PERSONAL), ("refers back", _REFERENCE),
                       ("can change", _CHANGING), ("creative or random", _CREATIVE)):
        hit = s & bad
        if hit:
            return False, f"{label}: {sorted(hit)[0]}"
    if re.search(r"\b(19|20)\d\d\b", norm):
        return False, "mentions a year"
    return True, "ok"


def reply_verdict(reply: str) -> tuple[bool, str]:
    r = (reply or "").strip()
    if len(r) < 2:
        return False, "empty reply"
    if _BAD_REPLY.search(r):
        return False, "looks like an error or refusal"
    return True, "ok"


def turn_verdict(*, tools_run: list | None, memory_hit: Any, truncated: bool, cancelled: bool,
                 exhausted: bool, tier: str, has_active_task: bool) -> tuple[bool, str]:
    if tools_run:
        return False, "a tool ran"
    if memory_hit:
        return False, "memory was injected"
    if truncated or cancelled or exhausted:
        return False, "incomplete turn"
    if str(tier).upper() == "HARD":
        return False, "HARD tier"
    if has_active_task:
        return False, "goal active"
    return True, "ok"


def prompt_fingerprint(path: str | Path | None = None) -> str:
    """Short hash of the system prompt file: editing it invalidates every entry."""
    p = Path(path) if path else Path(__file__).resolve().parent / "prompt.txt"
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()[:10]
    except OSError:
        return "noprompt"


def make_scope(provider: str, model: str, fingerprint: str | None = None) -> str:
    return f"{provider}|{model}|{fingerprint or prompt_fingerprint()}"


# ── the store ─────────────────────────────────────────────────────────────

class ResponseCache:
    def __init__(self, path: str | Path | None, ttl: float = DEFAULT_TTL_S,
                 max_items: int = DEFAULT_MAX, clock=time.time) -> None:
        self.path = Path(path) if path else None
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}
        self._loaded = False
        self.hits = 0
        self.misses = 0

    # key = hash of scope + normalized question; the question is also stored for humans
    @staticmethod
    def key(scope: str, text: str) -> str:
        return hashlib.sha256(f"{scope}\n{normalize(text)}".encode("utf-8")).hexdigest()[:32]

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), dict):
                self._items = {k: v for k, v in data["items"].items()
                               if isinstance(v, dict) and "reply" in v and "ts" in v}
        except (OSError, ValueError):
            self._items = {}

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "items": self._items}, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def get(self, scope: str, text: str) -> str | None:
        with self._lock:
            self._load()
            k = self.key(scope, text)
            e = self._items.get(k)
            now = self._clock()
            if e is None or now - float(e["ts"]) > self.ttl:
                if e is not None:
                    del self._items[k]
                    self._save()
                self.misses += 1
                return None
            e["last_used"] = now
            e["hits"] = int(e.get("hits", 0)) + 1
            self.hits += 1
            self._save()
            return str(e["reply"])

    def put(self, scope: str, text: str, reply: str) -> None:
        with self._lock:
            self._load()
            now = self._clock()
            self._items[self.key(scope, text)] = {
                "question": normalize(text), "reply": reply, "ts": now,
                "last_used": now, "hits": 0}
            if len(self._items) > self.max_items:
                order = sorted(self._items, key=lambda k: self._items[k].get("last_used", 0))
                for k in order[: len(self._items) - self.max_items]:
                    del self._items[k]
            self._save()

    def clear(self) -> int:
        with self._lock:
            self._load()
            n = len(self._items)
            self._items = {}
            self._save()
            return n

    def stats(self) -> dict:
        with self._lock:
            self._load()
            return {"entries": len(self._items), "hits": self.hits, "misses": self.misses,
                    "ttl_s": self.ttl, "max": self.max_items}


_cache: ResponseCache | None = None
_cache_lock = threading.Lock()


def get_cache(cfg: dict | None = None) -> ResponseCache:
    """Process-wide cache stored in the data dir; settings follow the config."""
    global _cache
    with _cache_lock:
        if _cache is None:
            try:
                from core.data_home import data_path
                path = data_path("response_cache.json")
            except Exception:  # noqa: BLE001
                path = None
            _cache = ResponseCache(path)
        _cache.ttl = ttl_s(cfg)
        _cache.max_items = max_entries(cfg)
        return _cache
