"""Roadmap #53 (self-check pass) and #55 (confidence / 'I don't know' flagging)."""
from types import SimpleNamespace

import pytest

from core import answer_check as ac
from core import conversation_core as cc

LONG = "The capital of Australia is Sydney, which is also its largest city."


# ---------------- parsing ----------------

def test_parse_plain_json():
    r = ac.parse_check('{"mistake": true, "correction": "It is Canberra.", "confidence": 0.9}')
    assert r.mistake and r.correction == "It is Canberra." and r.confidence == 0.9


def test_parse_tolerates_fences_and_chatter():
    r = ac.parse_check('Sure!\n```json\n{"mistake": false, "correction": "", "confidence": 0.3}\n```\nDone.')
    assert r is not None and not r.mistake and r.confidence == 0.3


@pytest.mark.parametrize("raw", ["", "no json here", "{broken", "[1, 2]", None])
def test_parse_rejects_unusable(raw):
    assert ac.parse_check(raw) is None


def test_parse_clamps_and_defaults():
    assert ac.parse_check('{"confidence": 7}').confidence == 1.0
    assert ac.parse_check('{"confidence": -2}').confidence == 0.0
    assert ac.parse_check('{"confidence": "high"}').confidence == 1.0
    # only a literal true counts: the string "false" must not read as a mistake
    assert ac.parse_check('{"mistake": "false", "correction": "x"}').mistake is False


def test_parse_truncates_long_correction():
    r = ac.parse_check('{"mistake": true, "correction": "%s"}' % ("x" * 2000))
    assert len(r.correction) == 300


# ---------------- follow-up decision ----------------

def test_mistake_with_correction_wins():
    r = ac.CheckResult(mistake=True, correction="It is Canberra.", confidence=0.1)
    assert r.follow_up() == "Correction: It is Canberra."


def test_mistake_without_correction_is_not_announced():
    assert ac.CheckResult(mistake=True, correction="", confidence=0.9).follow_up() == ""


def test_low_confidence_offers_search():
    assert "search the web" in ac.CheckResult(confidence=0.2).follow_up()


def test_low_confidence_silent_when_search_already_used():
    assert ac.CheckResult(confidence=0.2).follow_up(can_search=False) == ""


def test_confident_answer_has_no_follow_up():
    assert ac.CheckResult(confidence=0.9).follow_up() == ""
    assert ac.CheckResult(confidence=0.5).follow_up(min_confidence=0.5) == ""  # boundary: not below


# ---------------- config ----------------

def test_disabled_by_default():
    assert not ac.is_enabled({}) and not ac.is_enabled(None)
    assert ac.is_enabled({"self_check_enabled": True})


@pytest.mark.parametrize("val,expected", [(0.7, 0.7), ("0.3", 0.3), ("x", 0.5), (5, 0.5), (-1, 0.5), (None, 0.5)])
def test_min_confidence_falls_back(val, expected):
    assert ac.min_confidence({"self_check_min_confidence": val}) == expected


# ---------------- gating ----------------

def test_should_check_skips_trivial_code_and_tool_grounded():
    assert ac.should_check(LONG, [])
    assert not ac.should_check("Done.", [])
    assert not ac.should_check("```python\n" + "x = 1\n" * 20 + "```", [])
    assert not ac.should_check(LONG, [("get_weather", "72F")])


def test_used_search():
    assert ac.used_search([("web_search", "...")])
    assert not ac.used_search([("get_weather", "...")]) and not ac.used_search(None)


# ---------------- run_check ----------------

def test_run_check_uses_injected_llm_and_fast_tier():
    seen = {}

    def fake(prompt, system=None, tier="default"):
        seen.update(prompt=prompt, system=system, tier=tier)
        return '{"mistake": false, "correction": "", "confidence": 0.4}'

    r = ac.run_check("capital of Australia?", LONG, llm=fake)
    assert r.confidence == 0.4 and seen["tier"] == "fast"
    assert "capital of Australia?" in seen["prompt"] and LONG in seen["prompt"]


def test_run_check_swallows_model_errors():
    def boom(*a, **k):
        raise ConnectionError("ollama down")
    assert ac.run_check("q", LONG, llm=boom) is None


# ---------------- wired into the turn ----------------

class _Obs:
    def __init__(self):
        self.counts = []

    def count(self, name, **kw):
        self.counts.append(name)


def _fake_core():
    spoken, logged = [], []
    core = SimpleNamespace(
        _conversation=[], _obs=_Obs(), _evaluator=None,
        ui=SimpleNamespace(write_log=logged.append),
        speak=spoken.append,
    )
    core._self_check_follow_up = lambda *a: cc.ConversationCoreMixin._self_check_follow_up(core, *a)
    return core, spoken, logged


def _patch(monkeypatch, cfg, verdict):
    monkeypatch.setattr("core.llm_client._load_config", lambda: cfg)
    monkeypatch.setattr(ac, "run_check", lambda user, reply, llm=None: verdict)


def test_turn_sends_correction(monkeypatch):
    core, spoken, logged = _fake_core()
    _patch(monkeypatch, {"self_check_enabled": True},
           ac.CheckResult(mistake=True, correction="It is Canberra.", confidence=0.9))
    core._self_check_follow_up("capital of Australia?", LONG, [])
    assert spoken == ["Correction: It is Canberra."]
    assert core._conversation[-1] == {"role": "assistant", "content": "Correction: It is Canberra."}
    assert logged == ["Aether: Correction: It is Canberra."]
    assert "self_check.correction" in core._obs.counts


def test_turn_offers_search_on_low_confidence(monkeypatch):
    core, spoken, _ = _fake_core()
    _patch(monkeypatch, {"self_check_enabled": True}, ac.CheckResult(confidence=0.2))
    core._self_check_follow_up("who won last night?", LONG, [])
    assert len(spoken) == 1 and "search the web" in spoken[0]
    assert "self_check.low_confidence" in core._obs.counts


def test_turn_is_silent_when_disabled(monkeypatch):
    core, spoken, _ = _fake_core()
    _patch(monkeypatch, {}, pytest.fail)  # run_check must never be reached
    core._self_check_follow_up("q", LONG, [])
    assert spoken == [] and core._conversation == []


def test_turn_is_silent_for_tool_grounded_reply(monkeypatch):
    core, spoken, _ = _fake_core()
    _patch(monkeypatch, {"self_check_enabled": True}, ac.CheckResult(confidence=0.0))
    core._self_check_follow_up("weather?", LONG, [("get_weather", "72F")])
    assert spoken == []


def test_turn_counts_unusable_verdict_and_stays_silent(monkeypatch):
    core, spoken, _ = _fake_core()
    _patch(monkeypatch, {"self_check_enabled": True}, None)
    core._self_check_follow_up("q", LONG, [])
    assert spoken == [] and core._obs.counts == ["self_check.unusable"]


def test_turn_never_raises(monkeypatch):
    core, spoken, _ = _fake_core()
    monkeypatch.setattr("core.llm_client._load_config", lambda: (_ for _ in ()).throw(RuntimeError("cfg")))
    core._self_check_follow_up("q", LONG, [])  # must not raise
    assert spoken == []


def test_finalize_calls_self_check_only_for_llm_replies():
    core, _, _ = _fake_core()
    calls = []
    core._self_check_follow_up = lambda *a: calls.append(a)
    fin = cc.ConversationCoreMixin._finalize_response
    fin(core, "q", LONG, None, [], True)
    fin(core, "q", LONG, None, [], False)   # raw tool result, not model-written
    fin(core, "q", "", None, [], True)      # cancelled turn
    assert len(calls) == 1
