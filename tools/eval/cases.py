"""Eval cases that make sense over the model-only API, and their grading.

Standard library only: inspect_task.py is loaded inside inspect-ai's own process, which may be a
separate venv without Aether's dependencies. Grading mirrors tools/live_grader.grade_case for the
reply checks (case-insensitive, multiline); tests/test_eval_adapters.py keeps the two in step.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE_PATH = ROOT / "tests" / "eval_suite_core.json"

# The API exposes no memory, tools or latency data, so these cannot be judged through it.
EXCLUDED_CATEGORIES = {"memory"}
_NEEDS_AETHER = ("tier", "not_exhausted", "max_latency_ms")

GLOBAL_BAD_REPLY = r"<tool_call|</tool_call>|\w+\(category=|Traceback|object has no attribute"
_REPLY_CHECKS = (("reply", True), ("reply_any", True), ("reply_also", True), ("reply_not", False))


def load_api_cases(path: Path = SUITE_PATH) -> list[dict]:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))["cases"]
    return [c for c in cases
            if c.get("category") not in EXCLUDED_CATEGORIES
            and not any(k in c for k in _NEEDS_AETHER)]


def _search(pat: str, text: str) -> bool:
    return re.search(pat, text, re.IGNORECASE | re.MULTILINE) is not None


def grade_reply(case: dict, reply: str) -> list[str]:
    """Failure reasons for one reply (empty list = pass)."""
    reply = reply or ""
    fails: list[str] = []
    for key, must_match in _REPLY_CHECKS:
        pat = case.get(key)
        if pat and _search(pat, reply) != must_match:
            fails.append(f"reply {'missing' if must_match else 'matched forbidden'} /{pat}/")
    if re.search(GLOBAL_BAD_REPLY, reply):
        fails.append("reply contains raw tool-call / traceback text")
    return fails


# ── promptfoo ────────────────────────────────────────────────────────────────
def to_js_regex(pattern: str) -> tuple[str, str]:
    """Python pattern -> (JavaScript source, flags). The graders search with IGNORECASE|MULTILINE.

    A leading inline flag group such as (?i) is Python-only syntax (JS throws on it, and a throwing
    check would silently count as a failure), so it is folded into the flags. Any other construct
    JS does not share is refused loudly rather than mis-graded.
    """
    flags = "im"
    m = re.match(r"\(\?([a-zA-Z]+)\)", pattern)
    if m:
        extra = set(m.group(1)) - set("im")
        if extra - {"s"}:
            raise ValueError(f"inline flag(s) {sorted(extra - {'s'})} in {pattern!r} have no JS equivalent")
        if "s" in extra:
            flags += "s"
        pattern = pattern[m.end():]
    if re.search(r"\(\?[a-zA-Z]|\\[AZ]|\(\?#", pattern):
        raise ValueError(f"pattern {pattern!r} uses Python-only syntax that JavaScript lacks")
    return pattern, flags


def _js_regex_test(pattern: str, expect: bool) -> str:
    """promptfoo's `regex` assertion cannot set flags, so use an inline JS check."""
    source, flags = to_js_regex(pattern)
    test = f"new RegExp({json.dumps(source)}, {json.dumps(flags)}).test(output)"
    return test if expect else f"!{test}"


def to_promptfoo_config(cases: list[dict], model: str, base_url: str,
                        api_key_envar: str = "AETHER_API_TOKEN") -> dict:
    """promptfoo config. The token is read from an environment variable; it is never written here."""
    tests = []
    for c in cases:
        asserts = []
        for key, must_match in _REPLY_CHECKS:
            if c.get(key):
                asserts.append({"type": "javascript", "metric": c["category"],
                                "value": _js_regex_test(c[key], must_match)})
        asserts.append({"type": "javascript", "metric": "no_tool_leak",
                        "value": "!" + f"new RegExp({json.dumps(GLOBAL_BAD_REPLY)}).test(output)"})
        tests.append({"description": c["id"], "vars": {"prompt": c["prompt"]},
                      "metadata": {"category": c["category"]}, "assert": asserts})
    return {
        "description": "Aether core eval suite over the OpenAI-compatible API",
        "prompts": ["{{prompt}}"],
        "providers": [{"id": f"openai:chat:{model}",
                       "config": {"apiBaseUrl": base_url.rstrip("/") + "/v1",
                                  "apiKeyEnvar": api_key_envar}}],
        "tests": tests,
    }
