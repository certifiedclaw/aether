"""core/reports.py - daily briefing and weekly report scheduling (#63, #65)."""
from datetime import datetime, timedelta

from core import reports as r


def at(h, m=0, day=5):   # 2026-10-{day}; Oct 5 2026 is a Monday
    return datetime(2026, 10, day, h, m)


def test_config_parsing():
    assert r.schedules_from_config({}) == []
    s = r.schedules_from_config({"briefing_time": "7:05", "briefing_days": "weekdays",
                                 "weekly_report_day": "sun"})
    assert [x.name for x in s] == ["daily_briefing", "weekly_report"]
    assert s[0].at == "07:05" and s[0].days == set(range(5))
    assert s[1].days == {6} and s[1].at == "09:00"
    assert r.schedules_from_config({"briefing_time": "25:99"}) == []      # invalid time ignored
    assert r.parse_days("mon, FRI") == {0, 4} and r.parse_days("garbage") == set(range(7))


def test_is_due_window_day_and_once_per_day():
    s = r.Schedule("b", "07:30", {0, 1, 2, 3, 4})
    assert not r.is_due(s, at(7, 29), None)                  # too early
    assert r.is_due(s, at(7, 30), None)
    assert r.is_due(s, at(12, 0), None)                      # machine was off: catch up
    assert not r.is_due(s, at(14, 0), None)                  # >6h late: skip, don't send stale
    assert not r.is_due(s, at(8, 0), "2026-10-05")           # already sent today
    assert not r.is_due(s, at(8, 0, day=10), None)           # Saturday
    assert not r.is_due(s, at(8, 0), None, retry_after=at(8, 30))


def make_runner(tmp_path, cfg, ok=True, now=None):
    sent = []
    clock = {"now": now or at(8, 0)}
    run = r.ReportRunner(cfg, {"weather": lambda c, w: f"sunny in {c}", "goals": lambda: [],
                               "supervisor": type("S", (), {"probes": []})()},
                         state_path=tmp_path / "state.json",
                         send=lambda m, **k: (sent.append((m, k)) or ok), clock=lambda: clock["now"])
    return run, sent, clock


def test_runner_sends_once_and_survives_restart(tmp_path):
    cfg = {"briefing_time": "07:30", "briefing_city": "Boston"}
    run, sent, clock = make_runner(tmp_path, cfg)
    assert run.run_due() == ["daily_briefing"] and len(sent) == 1
    text, kw = sent[0]
    assert "sunny in Boston" in text and "Goals: nothing active" in text and "Server: all healthy" in text
    assert kw["title"] == "Aether daily briefing"
    assert run.run_due() == []
    run2, sent2, _ = make_runner(tmp_path, cfg)                      # "restart": new runner, same state file
    assert run2.run_due() == [] and sent2 == []
    run2._clock = lambda: at(8, 0, day=6)                             # next day
    assert run2.run_due() == ["daily_briefing"]


def test_failed_push_is_retried_later_not_every_minute(tmp_path):
    cfg = {"briefing_time": "07:30"}
    run, sent, clock = make_runner(tmp_path, cfg, ok=False)
    assert run.run_due() == [] and len(sent) == 1
    clock["now"] += timedelta(minutes=5)
    run.run_due()
    assert len(sent) == 1                                             # no retry storm
    clock["now"] += timedelta(minutes=30)
    run.run_due()
    assert len(sent) == 2


def test_broken_section_does_not_lose_the_report():
    def boom(cfg, ctx):
        raise RuntimeError("x")
    sch = r.Schedule("t", "07:00", sections=("weather", "goals"))
    orig = r.SECTIONS["weather"]
    r.SECTIONS["weather"] = boom
    try:
        text = r.build_report(sch, {}, {"goals": lambda: []}, at(7))
    finally:
        r.SECTIONS["weather"] = orig
    assert "weather: unavailable (RuntimeError)" in text and "Goals:" in text


def test_register_reports_is_noop_unless_configured():
    class Sched:
        def __init__(self): self.tasks = []
        def register(self, name, fn, interval_sec): self.tasks.append((name, interval_sec))
    s = Sched()
    assert r.register_reports(s, {}) is None and s.tasks == []
    assert r.register_reports(s, {"briefing_time": "07:30"}) is not None
    assert s.tasks == [("reports", 60.0)]


def test_preview_uses_default_sections():
    out = r.preview("report", {}, {"supervisor": type("S", (), {"probes": []})()})
    assert "weekly report" in out and "Usage, last" in out and "Log analysis" in out
