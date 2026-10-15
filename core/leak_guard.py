"""core/leak_guard.py - keep secrets out of requests to cloud LLMs (roadmap #43).

Applied at the single point where messages are shaped for a hosted provider
(Gemini / OpenAI API). Local backends (Ollama, LM Studio, your own PC's Ollama)
are never touched.

What it does:
  * redacts API keys, tokens, private keys, JWTs, card numbers (Luhn-valid) and
    the exact secret values stored in config/api_keys.json;
  * optionally refuses cloud calls entirely (config "cloud_allow": false);
  * records COUNTS of what was redacted - never the values.
Config (config/api_keys.json): cloud_allow (default true), cloud_redact (default true).
"""
from __future__ import annotations

import copy
import logging
import re
import threading

logger = logging.getLogger("aether")

CLOUD_PROVIDERS = frozenset({"gemini", "openai_api"})
_MIN_SECRET_LEN = 8
_SECRET_KEY_HINT = re.compile(r"(key|token|secret|passw|pwd|credential)", re.I)

_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)),
    ("google_key",  re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b")),
    ("openai_key",  re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("aws_key",     re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jwt",         re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer",      re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*")),
    ("assignment",  re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|passw(?:or)?d|pwd)\b[\"']?\s*[:=]\s*[\"']?([^\s\"',;]{8,})")),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")

_lock = threading.Lock()
_stats: dict[str, int] = {}


class CloudBlocked(RuntimeError):
    """Raised when config forbids cloud LLM calls."""


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


def known_secrets(cfg: dict | None) -> list[str]:
    """Exact secret values from config (longest first so overlaps redact cleanly)."""
    vals = []
    for k, v in (cfg or {}).items():
        if isinstance(v, str) and len(v) >= _MIN_SECRET_LEN and _SECRET_KEY_HINT.search(str(k)):
            vals.append(v)
    return sorted(set(vals), key=len, reverse=True)


def redact_text(text: str, secrets: list[str] | None = None) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}

    def bump(name: str) -> None:
        counts[name] = counts.get(name, 0) + 1

    for s in secrets or []:
        if s in text:
            n = text.count(s)
            text = text.replace(s, "[REDACTED:secret]")
            counts["config_secret"] = counts.get("config_secret", 0) + n
    for name, pat in _PATTERNS:
        if name == "assignment":
            def repl(m, _n=name):
                bump(_n)
                return m.group(0).replace(m.group(1), "[REDACTED:secret]")
            text = pat.sub(repl, text)
        else:
            def repl(m, _n=name):
                bump(_n)
                return f"[REDACTED:{_n}]"
            text = pat.sub(repl, text)

    def card(m):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            bump("card_number")
            return "[REDACTED:card_number]"
        return m.group(0)
    text = _CARD.sub(card, text)
    return text, counts


def _walk(value, fn):
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk(v, fn) for v in value]
    if isinstance(value, dict):
        return {k: (_walk(v, fn) if k in ("content", "text", "arguments", "tool_calls", "function") or
                    isinstance(v, (list, dict)) else v) for k, v in value.items()}
    return value


def guard_messages(messages: list, provider: str, cfg: dict | None = None) -> list:
    """Return a scrubbed deep copy for cloud providers; the input is untouched.
    Non-cloud providers get `messages` back unchanged."""
    if provider not in CLOUD_PROVIDERS:
        return messages
    if cfg is None:
        try:
            from core.llm_client import _load_config
            cfg = _load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    if cfg.get("cloud_allow", True) is False:
        raise CloudBlocked(
            f"Cloud model ({provider}) blocked by config (cloud_allow=false). "
            "Using local models only.")
    if cfg.get("cloud_redact", True) is False:
        return messages
    secrets = known_secrets(cfg)
    total: dict[str, int] = {}

    def fn(s: str) -> str:
        out, c = redact_text(s, secrets)
        for k, v in c.items():
            total[k] = total.get(k, 0) + v
        return out

    out = copy.deepcopy(messages)
    for m in out:
        if isinstance(m, dict):
            for k in ("content", "tool_calls"):
                if k in m:
                    m[k] = _walk(m[k], fn)
    if total:
        with _lock:
            for k, v in total.items():
                _stats[k] = _stats.get(k, 0) + v
        logger.warning("[leak-guard] redacted before sending to %s: %s", provider,
                       ", ".join(f"{k}x{v}" for k, v in sorted(total.items())))
    return out


def cloud_allowed(cfg: dict | None = None) -> bool:
    """False when config forbids hosted models. Routers check this to stay local."""
    if cfg is None:
        try:
            from core.llm_client import _load_config
            cfg = _load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    return cfg.get("cloud_allow", True) is not False


def stats() -> dict[str, int]:
    with _lock:
        return dict(_stats)


def reset_stats() -> None:
    with _lock:
        _stats.clear()
