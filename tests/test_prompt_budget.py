"""Roadmap #93: keep the per-turn prompt small.

Guards three things: the always-on system prompt stays under a token ceiling,
the optional-integration docs only appear when relevant, static prompt blocks
come before per-turn ones (Ollama prefix-cache friendliness), and small-talk
turns on the local model carry a capped slice of history.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import conversation_core as cc
from core.prompt_builder import PromptBuilderMixin

PROMPT = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")


def _tok(s: str) -> int:
    return len(s) // 4          # the project's own estimator (core/smart_context)


# ---------------- always-on system prompt ----------------

def test_base_system_prompt_stays_under_token_ceiling():
    # Was ~1390 tokens before #93 moved the integration docs out. Raise this only on purpose.
    assert _tok(PROMPT) <= 1200, f"prompt.txt grew to ~{_tok(PROMPT)} tokens"


def test_integration_docs_are_not_in_the_always_on_prompt():
    for needle in ("INTEGRATION TOOLS", "TOOL SELECTION GUIDE", "llama_index(", "open_interpreter("):
        assert needle not in PROMPT, needle
    # the rules small models depend on are still there
    for needle in ("TOOL CALLING", "MEMORY RULE", "OPEN vs MESSAGE", "computer_settings"):
        assert needle in PROMPT, needle


# ---------------- assembly: conditional + ordered ----------------

def _builder(monkeypatch, *, lc=False, oi=False, llama=False):
    m = types.ModuleType("main")
    m._load_system_prompt = lambda: "BASE"
    m.load_memory = lambda: {"identity": {"name": {"value": "Claw"}}}
    m._LANGCHAIN_OK, m._OI_OK, m._LLAMA_OK = lc, oi, llama
    monkeypatch.setitem(sys.modules, "main", m)

    class B(PromptBuilderMixin):
        _hybrid_mem = MagicMock()
        _semantic_mem = None
        _goals = MagicMock()
        _mcp = MagicMock()
        _plugins = MagicMock()
    b = B()
    b._hybrid_mem.inject_context.return_value = "[RETRIEVED] fact about this turn"
    b._goals.format_for_prompt.return_value = "[GOALS] ship it"
    b._mcp.list_all_tools.return_value = ["mcp.tool_a"]
    b._plugins.list_tools.return_value = ["plug_a"]
    return b


def test_integration_docs_appear_only_when_the_integration_is_available(monkeypatch):
    none = _builder(monkeypatch)._build_system_prompt("hello world question")
    assert "langchain" not in none and "llama_index" not in none and "open_interpreter" not in none

    full = _builder(monkeypatch, lc=True, oi=True, llama=True)._build_system_prompt("hello world question")
    assert "langchain(action=research)" in full
    assert "open_interpreter(action=run)" in full
    assert "llama_index(action=query)" in full
    assert "Use action=research for complex questions" in full      # original wording preserved

    only_lc = _builder(monkeypatch, lc=True)._build_system_prompt("hello world question")
    assert "langchain(" in only_lc and "llama_index" not in only_lc


def test_light_turns_skip_integration_docs_memory_and_goals(monkeypatch):
    b = _builder(monkeypatch, lc=True, oi=True, llama=True)
    light = b._build_system_prompt("thanks", light=True)
    assert "langchain" not in light and "[RETRIEVED]" not in light and "[GOALS]" not in light
    assert "Claw" in light and "[CURRENT DATE & TIME]" in light       # facts + clock still present


def test_static_blocks_come_before_per_turn_blocks_and_time_is_last(monkeypatch):
    p = _builder(monkeypatch, lc=True)._build_system_prompt("tell me something")
    order = [p.index(x) for x in ("BASE", "Claw", "[CONNECTED MCP TOOLS]", "[LOADED PLUGINS]",
                                  "[ACTIVE INTEGRATIONS]", "[RETRIEVED]", "[GOALS]", "[CURRENT DATE & TIME]")]
    assert order == sorted(order), order


# ---------------- history cap on small-talk turns ----------------

def _host(monkeypatch, cfg=None, n_msgs=60):
    m = types.ModuleType("main"); m._LANGCHAIN_OK = False
    monkeypatch.setitem(sys.modules, "main", m)
    monkeypatch.setattr("core.llm_client._load_config", lambda: cfg or {})
    host = MagicMock()
    host._hybrid_mem = host._semantic_mem = None
    host._config = {}
    host._build_system_prompt.return_value = "S" * 4000                 # ~1000 tokens
    host._conversation = []
    for i in range(n_msgs):                                           # ~100 tokens each
        host._conversation.append({"role": "user" if i % 2 == 0 else "assistant",
                                   "content": f"m{i} " + "x" * 400})
    return host


def _total(messages) -> int:
    return sum(_tok(str(m.get("content", ""))) for m in messages)


def _prep(host, light, history_budget=None):
    return cc.ConversationCoreMixin._prepare_conversation_messages(
        host, "quick question", light=light, tools=None, history_budget=history_budget)[0]


def test_light_local_turn_is_capped_and_keeps_the_newest_messages(monkeypatch):
    host = _host(monkeypatch)
    before = len(host._conversation)
    heavy = _prep(host, light=False)
    light = _prep(host, light=True)
    assert _total(light) <= 1000 + cc._QUICK_HISTORY_DEFAULT + 150
    assert _total(light) < _total(heavy)
    assert light[-1]["content"] == "quick question"            # the current request is never dropped
    assert light[-2]["content"] == host._conversation[-2]["content"]   # most recent exchange kept
    # saved chat history is untouched: the only growth is the two user messages the two calls added
    assert len(host._conversation) == before + 2


def test_cap_is_configurable_and_zero_disables_it(monkeypatch):
    small = _total(_prep(_host(monkeypatch, {"llm.quick_history_budget": 500}), light=True))
    default = _total(_prep(_host(monkeypatch), light=True))
    off = _total(_prep(_host(monkeypatch, {"llm.quick_history_budget": 0}), light=True))
    heavy = _total(_prep(_host(monkeypatch), light=False))
    assert small < default < heavy
    assert off == heavy                                          # 0 = no cap, same as a normal turn


@pytest.mark.parametrize("junk", ["lots", -5, None, "2k"])
def test_junk_cap_values_fall_back_to_the_default(monkeypatch, junk):
    got = _total(_prep(_host(monkeypatch, {"llm.quick_history_budget": junk}), light=True))
    ref = _total(_prep(_host(monkeypatch), light=True))
    assert got == ref


def test_cloud_routed_light_turns_are_not_capped(monkeypatch):
    host = _host(monkeypatch)
    capped = _total(_prep(host, light=True))
    cloud = _total(_prep(host, light=True, history_budget=cc._CLOUD_HISTORY_BUDGET))
    assert cloud > capped
