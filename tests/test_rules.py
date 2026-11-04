"""core/rules.py - plain-language rules (#66)."""
from datetime import datetime

import pytest

from core import rules as R


def test_parse_time_interval_condition_and_actions():
    r = R.parse("Every weekday at 7:30 send me the briefing")
    assert r["trigger"] == {"type": "time", "at": "07:30", "days": [0, 1, 2, 3, 4]}
    assert r["action"] == {"type": "report", "kind": "briefing"}
    assert R.parse("mondays and fridays at 6pm tell me")["trigger"]["days"] == [0, 4]
    assert R.parse("every day at 12am notify me")["trigger"]["at"] == "00:00"
    assert R.parse("every 2 hours remind me to stretch") == {
        "trigger": {"type": "interval", "every_s": 7200},
        "action": {"type": "notify", "text": "Reminder: stretch"}}
    assert R.parse("when the disk is below 10% notify me")["trigger"] == {
        "type": "condition", "what": "disk_free_pct", "below": 10.0}
    assert R.parse("if ram drops below 2 gb alert me")["trigger"]["below"] == 2048.0
    assert R.parse("if ollama is down alert me")["trigger"] == {
        "type": "condition", "what": "probe_down", "name": "ollama"}
    assert R.parse("every monday at 9am send the weekly report")["action"]["kind"] == "report"


@pytest.mark.parametrize("bad", [
    "", "do something nice", "every day at 7:30", "notify me", "every 1 minute notify me",
    "every day at 25:00 notify me", "delete my files every day at 3am",
])
def test_unparseable_or_unsafe_sentences_are_refused(bad):
    with pytest.raises(R.RuleError):
        R.parse(bad)


def make(tmp_path, now, hit=(False, ""), cfg=None):
    sent = []
    st = {"now": now, "ts": 1_000_000.0, "hit": hit}
    eng = R.RuleEngine(cfg or {}, path=tmp_path / "rules.json", clock=lambda: st["now"],
                       now_s=lambda: st["ts"], notify=lambda m, **k: sent.append(m) or True,
                       report=lambda kind: f"<{kind}>", condition=lambda t, c: st["hit"])
    return eng, sent, st


def test_time_rule_fires_once_per_day_and_survives_reload(tmp_path):
    eng, sent, st = make(tmp_path, datetime(2026, 10, 5, 7, 30))        # a Monday
    eng.add("every day at 7:30 send me the briefing")
    assert len(eng.tick()) == 1 and sent == ["<briefing>"]
    assert eng.tick() == []
    eng2, sent2, _ = make(tmp_path, datetime(2026, 10, 5, 7, 31))       # restart
    assert eng2.tick() == [] and sent2 == []
    st["now"] = datetime(2026, 10, 6, 7, 30)
    assert len(eng.tick()) == 1


def test_missed_time_is_not_replayed_late(tmp_path):
    eng, sent, st = make(tmp_path, datetime(2026, 10, 5, 12, 0))
    eng.add("every day at 7:30 notify me")
    assert eng.tick() == [] and sent == []


def test_interval_rule(tmp_path):
    eng, sent, st = make(tmp_path, datetime(2026, 10, 5, 9, 0))
    eng.add("every 30 minutes remind me to drink water")
    assert eng.tick() == []
    st["ts"] += 1800
    assert len(eng.tick()) == 1 and sent == ["Reminder: drink water"]
    st["ts"] += 600
    assert eng.tick() == []


def test_condition_rule_is_edge_triggered_with_cooldown(tmp_path):
    eng, sent, st = make(tmp_path, datetime(2026, 10, 5, 9, 0), cfg={"rule_cooldown_s": 3600})
    eng.add("when the disk is below 10% notify me")
    st["hit"] = (True, "disk is 8.0% free")
    assert len(eng.tick()) == 1 and sent == ["disk is 8.0% free"]
    st["ts"] += 120
    assert eng.tick() == [] and len(sent) == 1                         # still low: no spam
    st["hit"] = (False, "")
    eng.tick()                                                          # clears -> re-arms
    st["hit"] = (True, "disk is 7.0% free")
    st["ts"] += 60
    assert eng.tick() == [] and len(sent) == 1                         # cooldown not over
    st["ts"] += 3600
    assert len(eng.tick()) == 1 and sent[-1] == "disk is 7.0% free"


def test_failed_push_is_retried_next_tick(tmp_path):
    eng, _, st = make(tmp_path, datetime(2026, 10, 5, 7, 30))
    calls = []
    eng._notify = lambda m, **k: calls.append(m) and False
    eng.add("every day at 7:30 notify me")
    assert eng.tick() == [] and eng.tick() == [] and len(calls) == 2


def test_manage_rules_and_command_surface(tmp_path, monkeypatch):
    eng, _, _ = make(tmp_path, datetime(2026, 10, 5, 9, 0))
    monkeypatch.setattr(R, "_engine", eng)
    assert "No rules yet" in R.handle_command("/rules")
    out = R.handle_command("/rule add every weekday at 7:30 send me the briefing")
    assert "added" in out and "07:30" in out
    rid = eng.load()[0]["id"]
    assert "turned off" in R.handle_command(f"/rule off {rid}")
    assert eng.tick() == [] and "[off]" in R.handle_command("/rules")
    assert "removed" in R.handle_command(f"/rule remove {rid}") and eng.load() == []
    assert R.handle_command("/rule add make coffee").startswith("Rule not added")
    assert R.handle_command("/something") is None
    assert "Usage" in R.handle_command("/rule")


def test_rule_limit_and_corrupt_file(tmp_path):
    eng, _, _ = make(tmp_path, datetime(2026, 10, 5, 9, 0))
    (tmp_path / "rules.json").write_text("{not json")
    assert eng.load() == []
    for _ in range(30):
        eng.add("every 2 hours notify me")
    with pytest.raises(R.RuleError):
        eng.add("every 2 hours notify me")
