"""Chained approvals (#67): one dialog for a whole plan; grants exactly what was shown."""
import pytest

import core.risk as risk
import core.safety as safety


@pytest.fixture
def ui(monkeypatch):
    class UI:
        answer = True
        asked = []
    UI.asked, UI.answer = [], True
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": UI.asked.append((op, detail)) or UI.answer)
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    monkeypatch.setattr(risk, "_cfg_int", lambda key, default, lo, hi: default)
    risk.reset_state()
    return UI


def _run(tool, **params):
    @risk.gated(tool)
    def fn(parameters, player=None):
        return "ran"
    return fn(params)


PLAN = [
    {"step": 1, "tool": "web_search", "description": "look it up", "parameters": {"query": "x"}},
    {"step": 2, "tool": "file_controller", "description": "tidy desktop",
     "parameters": {"action": "organize_desktop", "mode": "by_type"}},
    {"step": 3, "tool": "send_message", "description": "tell Sam",
     "parameters": {"receiver": "Sam", "message_text": "done"}},
]


def test_one_dialog_then_each_approved_step_runs_without_asking(ui):
    risk.begin_turn("clean up and tell sam")
    ok, n = risk.request_plan_approval("clean up", PLAN)
    assert ok and n == 2 and len(ui.asked) == 1
    detail = ui.asked[0][1]
    assert "NEEDS APPROVAL  file_controller.organize_desktop" in detail
    assert "runs freely      web_search" in detail
    assert _run("file_controller", action="organize_desktop", mode="by_type") == "ran"
    assert _run("send_message", receiver="Sam", message_text="done") == "ran"
    assert len(ui.asked) == 1                                  # no further dialogs


def test_refusal_grants_nothing_and_steps_still_ask(ui):
    risk.begin_turn("x")
    ui.answer = False
    ok, _ = risk.request_plan_approval("g", PLAN)
    assert not ok
    n = len(ui.asked)
    assert "Cancelled" in _run("send_message", receiver="Sam", message_text="done")
    assert len(ui.asked) == n + 1                              # asked individually, and was refused


def test_grant_is_exact_and_single_use(ui):
    risk.begin_turn("x")
    risk.request_plan_approval("g", PLAN)
    base = len(ui.asked)
    ui.answer = False
    # different recipient / text than the plan showed -> not covered
    assert "Cancelled" in _run("send_message", receiver="Eve", message_text="done")
    assert "Cancelled" in _run("send_message", receiver="Sam", message_text="other")
    assert len(ui.asked) == base + 2
    ui.answer = True
    assert _run("send_message", receiver="Sam", message_text="done") == "ran"   # exact match
    ui.answer = False
    assert "Cancelled" in _run("send_message", receiver="Sam", message_text="done")  # used up


def test_grant_dies_with_the_request_and_with_time(ui, monkeypatch):
    t = {"v": 100.0}
    monkeypatch.setattr(risk.time, "monotonic", lambda: t["v"])
    risk.begin_turn("a")
    risk.request_plan_approval("g", PLAN)
    risk.begin_turn("b")                                       # new user request
    ui.answer = False
    assert "Cancelled" in _run("send_message", receiver="Sam", message_text="done")
    ui.answer = True
    risk.begin_turn("c")
    risk.request_plan_approval("g", PLAN)
    t["v"] += 601                                              # past risk_plan_ttl
    ui.answer = False
    assert "Cancelled" in _run("send_message", receiver="Sam", message_text="done")


def test_blocked_step_refuses_the_whole_plan_without_a_dialog(ui):
    risk.begin_turn("x")
    bad = PLAN + [{"tool": "extra_tool", "description": "py", "parameters": {"action": "run_python", "code": "1"}}]
    assert risk.request_plan_approval("g", bad) == (False, 0)
    assert ui.asked == []


def test_plan_with_nothing_gated_needs_no_dialog(ui):
    risk.begin_turn("x")
    assert risk.request_plan_approval("g", PLAN[:1]) == (True, 0)
    assert ui.asked == []


def test_plan_grant_never_unblocks_run_python(ui):
    risk.begin_turn("x")
    risk.request_plan_approval("g", PLAN)
    out = _run("extra_tool", action="run_python", code="1")
    assert "Blocked" in out or "disabled" in out.lower()


def test_executor_asks_once_when_enabled_and_cancels_on_refusal(ui, monkeypatch):
    import agent.executor as ex
    monkeypatch.setattr(risk, "chained_approvals_enabled", lambda: True)
    monkeypatch.setattr(ex, "create_plan", lambda goal: {"steps": PLAN})
    spoken = []
    risk.begin_turn("x")
    ui.answer = False
    out = ex.AgentExecutor().execute("g", speak=spoken.append)
    assert "not approved" in out and len(ui.asked) == 1 and spoken
