"""The cat's voice in Aether's replies: core.prompt_builder.persona_guidance and its
placement in the system prompt (config `assistant_personality`)."""
import sys
from datetime import datetime
from types import ModuleType

import pytest

from core.prompt_builder import (
    _PERSONA_GUIDANCE, _PERSONA_PLAIN_EXAMPLES, _PERSONA_QUIP_EXAMPLES,
    _QUICK_SYSTEM_PROMPT, PromptBuilderMixin, persona_examples, persona_guidance,
)

NOW = datetime(2026, 10, 25, 20, 0)


@pytest.fixture(autouse=True)
def _no_eval_env(monkeypatch):
    monkeypatch.delenv("AETHER_EVAL_OVERRIDES", raising=False)


def test_persona_is_on_by_default():
    assert persona_guidance({}).startswith(_PERSONA_GUIDANCE)
    assert persona_guidance(None).startswith(_PERSONA_GUIDANCE)


def test_config_can_turn_persona_off():
    assert persona_guidance({"assistant_personality": False}) == ""
    assert persona_guidance({"assistant_personality": True}).startswith(_PERSONA_GUIDANCE)


def test_persona_is_off_during_benchmark_runs(monkeypatch):
    monkeypatch.setenv("AETHER_EVAL_OVERRIDES", '{"llm_model": "x"}')
    assert persona_guidance({}) == ""


def test_persona_rules_protect_accuracy():
    t = _PERSONA_GUIDANCE.lower()
    for must in ("night-cat", "never let the voice change facts", "tool use",
                 "format or length", "code and data", "errors, safety",
                 "never invent", "explain fully", "follow-up questions"):
        assert must in t


def test_persona_stays_small_enough_for_quick_turns():
    # Rules plus the sampled examples; quick turns are prefill-sensitive.
    assert len(persona_guidance({}, NOW)) < 1500


def test_examples_mix_restraint_and_quips_and_never_ask_questions():
    out = persona_examples(NOW)
    plain = {r for _, r in _PERSONA_PLAIN_EXAMPLES}
    quip = {r for _, r in _PERSONA_QUIP_EXAMPLES}
    assert sum(1 for r in plain if r in out) == 2
    assert sum(1 for r in quip if r in out) == 2
    # The prompt bans follow-up questions, so no example may end in one.
    for _, reply in _PERSONA_PLAIN_EXAMPLES + _PERSONA_QUIP_EXAMPLES:
        assert not reply.rstrip().endswith("?")


def test_examples_are_stable_within_an_hour_and_rotate_across_hours():
    a = persona_examples(datetime(2026, 10, 25, 20, 5))
    b = persona_examples(datetime(2026, 10, 25, 20, 55))
    assert a == b  # cache-friendly prompt prefix
    seen = {persona_examples(datetime(2026, 10, 25, h, 0)) for h in range(24)}
    assert len(seen) > 1


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


def test_identity_lines_no_longer_fight_the_persona():
    assert "professional" not in _QUICK_SYSTEM_PROMPT.lower()
    from pathlib import Path
    head = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt").read_text(
        encoding="utf-8").splitlines()[1]
    assert "night-cat" in head and "professional" not in head.lower()
