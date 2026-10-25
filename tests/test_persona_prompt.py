"""The cat's voice in Aether's replies: core.prompt_builder.persona_guidance and its
placement in the system prompt (config `assistant_personality`)."""
import sys
from types import ModuleType

import pytest

from core.prompt_builder import (
    _PERSONA_GUIDANCE, _QUICK_SYSTEM_PROMPT, PromptBuilderMixin, persona_guidance,
)


@pytest.fixture(autouse=True)
def _no_eval_env(monkeypatch):
    monkeypatch.delenv("AETHER_EVAL_OVERRIDES", raising=False)


def test_persona_is_on_by_default():
    assert persona_guidance({}) == _PERSONA_GUIDANCE
    assert persona_guidance(None) == _PERSONA_GUIDANCE


def test_config_can_turn_persona_off():
    assert persona_guidance({"assistant_personality": False}) == ""
    assert persona_guidance({"assistant_personality": True}) == _PERSONA_GUIDANCE


def test_persona_is_off_during_benchmark_runs(monkeypatch):
    monkeypatch.setenv("AETHER_EVAL_OVERRIDES", '{"llm_model": "x"}')
    assert persona_guidance({}) == ""


def test_persona_text_protects_accuracy_and_stays_small():
    t = _PERSONA_GUIDANCE.lower()
    for must in ("night-cat", "never let it change facts", "tool use", "format or length",
                 "code and data", "errors, safety"):
        assert must in t
    assert len(_PERSONA_GUIDANCE) < 450  # quick turns are prefill-sensitive


def _builder(monkeypatch, cfg):
    main = ModuleType("main")
    main._load_system_prompt = lambda: "base prompt"
    main.load_memory = lambda: {}
    main._load_config = lambda: cfg
    main._LANGCHAIN_OK = main._OI_OK = main._LLAMA_OK = False
    monkeypatch.setitem(sys.modules, "main", main)

    class PB(PromptBuilderMixin):
        _hybrid_mem = _semantic_mem = _goals = _mcp = _plugins = None

    return PB()


@pytest.mark.parametrize("light", [False, True])
def test_persona_reaches_full_and_quick_prompts_after_the_base(monkeypatch, light):
    prompt = _builder(monkeypatch, {})._build_system_prompt(light=light)
    base = _QUICK_SYSTEM_PROMPT if light else "base prompt"
    assert _PERSONA_GUIDANCE in prompt
    assert prompt.index(base) < prompt.index(_PERSONA_GUIDANCE)


@pytest.mark.parametrize("light", [False, True])
def test_persona_flag_off_leaves_prompt_untouched(monkeypatch, light):
    prompt = _builder(monkeypatch, {"assistant_personality": False})._build_system_prompt(light=light)
    assert "[PERSONALITY]" not in prompt


def test_broken_config_loader_never_breaks_prompt_building(monkeypatch):
    b = _builder(monkeypatch, {})
    sys.modules["main"]._load_config = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    assert _PERSONA_GUIDANCE in b._build_system_prompt()
