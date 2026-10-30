"""core/answer_check.py -- self-check pass and confidence flagging (roadmap #53, #55).

After Aether has already delivered a reply, ask the model once to re-read it.
The reply is streamed and spoken as it is generated, so it can't be rewritten
before the user hears it; instead this returns an optional short follow-up:

  * a correction, when the re-read finds a concrete mistake        (#53)
  * "I'm not sure -- want me to search the web?", when the model rates its
    own answer low-confidence and a web search wasn't already used  (#55)

Opt-in and OFF by default: it costs one extra model round per checked reply,
which is real latency on a CPU-only host. Config (config/api_keys.json):

    "self_check_enabled":        false   # master switch
    "self_check_min_confidence": 0.5     # below this, offer to search (0-1)

Fails quiet: any model/parse error means "no follow-up", never a broken turn.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_MIN_CONFIDENCE = 0.5
_MIN_REPLY_CHARS = 40       # one-liners ("Done.", "Noted.") have nothing to check
_MAX_REPLY_CHARS = 1500     # keep the check prompt small for CPU models
_MAX_CORRECTION_CHARS = 300
_SEARCH_TOOLS = {"web_search"}

_SYSTEM = """You re-read an AI assistant's answer to check it before the user relies on it.

Given the user's question and the assistant's answer, report:
- "mistake": true ONLY if the answer contains a clear factual, logical or arithmetic error.
  Style, brevity or missing detail is NOT a mistake. When unsure, false.
- "correction": if mistake is true, one or two sentences giving the corrected answer. Else "".
- "confidence": 0.0-1.0, how sure you are the answer is right. Use low values for
  guesses, recent events, prices, versions, or anything you would need to look up.

Respond with ONLY valid JSON:
{"mistake": false, "correction": "", "confidence": 0.9}"""


@dataclass
class CheckResult:
    mistake: bool = False
    correction: str = ""
    confidence: float = 1.0

    def follow_up(self, min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                  can_search: bool = True) -> str:
        """The message to send the user, or "" if the answer needs no follow-up."""
        if self.mistake and self.correction:
            return f"Correction: {self.correction}"
        if can_search and self.confidence < min_confidence:
            return "I'm not sure about that one. Want me to search the web to check?"
        return ""


def is_enabled(cfg: dict | None) -> bool:
    return bool((cfg or {}).get("self_check_enabled", False))


def min_confidence(cfg: dict | None) -> float:
    try:
        v = float((cfg or {}).get("self_check_min_confidence", DEFAULT_MIN_CONFIDENCE))
    except (TypeError, ValueError):
        return DEFAULT_MIN_CONFIDENCE
    return v if 0.0 <= v <= 1.0 else DEFAULT_MIN_CONFIDENCE


def should_check(reply: str, tool_results: list | None = None) -> bool:
    """Skip replies with nothing to verify. Tool-grounded replies are skipped:
    their facts came from a tool, not from the model's memory."""
    r = (reply or "").strip()
    if len(r) < _MIN_REPLY_CHARS or "```" in r:
        return False
    return not tool_results


def used_search(tool_results: list | None) -> bool:
    return any(name in _SEARCH_TOOLS for name, _ in (tool_results or []))


def parse_check(raw: str) -> CheckResult | None:
    """Parse the model's JSON verdict. None when it isn't usable."""
    text = re.sub(r"```(?:json)?", "", raw or "").strip().strip("`").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        conf = float(data.get("confidence", 1.0))
    except (TypeError, ValueError):
        conf = 1.0
    correction = str(data.get("correction") or "").strip()[:_MAX_CORRECTION_CHARS]
    return CheckResult(
        mistake=data.get("mistake") is True,
        correction=correction,
        confidence=max(0.0, min(1.0, conf)),
    )


def run_check(user_text: str, reply: str, llm=None) -> CheckResult | None:
    """One model call. `llm` is injectable for tests; defaults to call_llm_text."""
    if llm is None:
        from core.llm_client import call_llm_text as llm
    prompt = (f"User question: {user_text[:500]}\n\n"
              f"Assistant answer:\n{reply[:_MAX_REPLY_CHARS]}")
    try:
        return parse_check(llm(prompt, system=_SYSTEM, tier="fast"))
    except Exception as e:
        logger.debug("Self-check failed (non-fatal): %s", e)
        return None
