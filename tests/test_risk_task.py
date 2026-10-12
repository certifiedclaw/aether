"""The TASK tier: ask once per user request (quoting the user), cover that tool for that
request only, never leak into the next request, never cover ASK-tier actions."""
import threading

import pytest

import core.risk as risk
import core.safety as safety
from core.risk import ASK, TASK


@pytest.fixture
def ui(monkeypatch):
    class UI:
        answer = True
        asked = []
    UI.asked = []
    UI.answer = True
    monkeypatch.setattr(safety, "confirm_action",
                        lambda op, detail="": UI.asked.append((op, detail)) or UI.answer)
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    monkeypatch.setattr(risk, "_cfg_int", lambda key, default, lo, hi: default)
    return UI


class Clock:
    t = 5000.0


@pytest.fixture
def clock(monkeypatch):
    Clock.t = 5000.0
    monkeypatch.setattr(risk.time, "monotonic", lambda: Clock.t)
    return Clock


def _click(tool="computer_control", action="click", **p):
    """Run one gated action; returns the tool's result."""
    @risk.gated(tool)
    def fn(parameters, player=None):
        return "did it"
    return fn({"action": action, **p})


def test_one_dialog_covers_the_whole_request(ui):
    risk.begin_turn("open notepad and type hello")
    assert _click(action="click", x=1) == "did it"
    for a in ("type", "press", "hotkey", "scroll", "drag"):
        assert _click(action=a) == "did it"
    assert len(ui.asked) == 1                                   # asked once, six actions ran


def test_dialog_quotes_the_users_own_words_and_the_limits(ui):
    risk.begin_turn('open   notepad\nand type "hello"  ')
    _click(action="type", text="hello", password="hunter2")
    op, detail = ui.asked[0]
    assert "computer_control" in op
    assert "\u201copen notepad and type \"hello\"\u201d" in detail     # whitespace collapsed
    assert "mouse and keyboard" in detail and "THIS request only" in detail
    assert "60 actions" in detail and "120 seconds" in detail
    assert "hunter2" not in detail and "(hidden)" in detail            # secrets masked
    assert "type" in detail.split("First action:")[1]


def test_works_without_request_text_and_says_so(ui):
    risk.reset_state()
    _click(action="click")
    assert "request text is not available" in ui.asked[0][1]


def test_a_new_request_ends_the_approval(ui):
    risk.begin_turn("first request")
    _click(); _click()
    assert len(ui.asked) == 1
    risk.begin_turn("second request")
    _click()
    assert len(ui.asked) == 2 and "second request" in ui.asked[1][1]


def test_refusal_sticks_for_the_request_so_a_retrying_model_cannot_spam_dialogs(ui):
    ui.answer = False
    risk.begin_turn("do the thing")
    first = _click()
    assert first.startswith("Cancelled") and len(ui.asked) == 1
    for _ in range(5):
        assert _click(action="type").startswith("Cancelled")
    assert len(ui.asked) == 1                                    # never asked again
    ui.answer = True
    risk.begin_turn("ok try again")                              # a new request may ask again
    assert _click() == "did it" and len(ui.asked) == 2


def test_approval_expires_after_the_time_limit(ui, clock):
    risk.begin_turn("slow task")
    _click()
    clock.t += 119
    _click()
    assert len(ui.asked) == 1
    clock.t += 2                                                 # now past 120 s
    _click()
    assert len(ui.asked) == 2


def test_approval_is_capped_in_actions(ui, monkeypatch):
    monkeypatch.setattr(risk, "_cfg_int", lambda key, default, lo, hi: 3 if key == "risk_task_max_actions" else default)
    risk.begin_turn("lots of clicks")
    results = [_click() for _ in range(3)]
    assert results == ["did it"] * 3 and len(ui.asked) == 1      # approval + 2 more = 3 actions
    _click()                                                     # the 4th needs a new approval
    assert len(ui.asked) == 2


def test_grant_is_per_tool(ui):
    risk.begin_turn("browse and click")
    _click("computer_control", "click")
    _click("browser_control", "click")
    assert len(ui.asked) == 2                                    # each tool is approved separately
    _click("computer_control", "type"); _click("browser_control", "fill_form")
    assert len(ui.asked) == 2


def test_a_grant_never_covers_ask_tier_actions(ui, monkeypatch):
    monkeypatch.setitem(risk.RISK_TABLE, "mixed", {"click": TASK, "wipe": ASK})

    @risk.gated("mixed")
    def fn(parameters):
        return "did it"
    risk.begin_turn("work")
    fn({"action": "click"})
    assert len(ui.asked) == 1
    fn({"action": "wipe"}); fn({"action": "wipe"})
    assert len(ui.asked) == 3                                    # ask-tier asked every single time
    fn({"action": "click"})
    assert len(ui.asked) == 3                                    # the task grant is still valid


def test_safe_and_monitor_actions_inside_a_tool_do_not_use_or_consume_the_grant(ui):
    risk.begin_turn("look at the screen")
    assert _click(action="screenshot") == "did it" and ui.asked == []    # SAFE: no dialog


def test_override_to_ask_makes_every_action_ask(ui, monkeypatch):
    monkeypatch.setattr(risk, "_overrides", lambda: {"computer_control.click": "ask"})
    risk.begin_turn("clicky")
    _click(action="click"); _click(action="click"); _click(action="click")
    assert len(ui.asked) == 3
    _click(action="type"); _click(action="type")
    assert len(ui.asked) == 4                                    # type is still task-tier, asked once


def test_concurrent_actions_produce_exactly_one_dialog_and_respect_the_cap(ui, monkeypatch):
    monkeypatch.setattr(risk, "_cfg_int", lambda key, default, lo, hi: 10 if key == "risk_task_max_actions" else default)
    risk.begin_turn("parallel")
    ran, lock = [], threading.Lock()

    def worker():
        r = _click()
        with lock:
            ran.append(r)
    ts = [threading.Thread(target=worker) for _ in range(40)]
    [t.start() for t in ts]; [t.join() for t in ts]
    # calls are serialised per tool, so each approval covers exactly 10 actions:
    # 40 calls -> exactly 4 dialogs, and all 40 actions run (the user said yes each time)
    assert len(ui.asked) == 4
    assert ran.count("did it") == 40


def test_new_request_during_the_dialog_cancels_instead_of_carrying_approval_over(monkeypatch):
    risk.begin_turn("old request")

    def ask_then_user_speaks_again(op, detail=""):
        risk.begin_turn("a different request")                   # user talks while the dialog is open
        return True
    monkeypatch.setattr(safety, "confirm_action", ask_then_user_speaks_again)
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    out = _click()
    assert out.startswith("Cancelled: a new request started")
    assert risk._GRANTS == {}


def test_no_ui_means_denied_and_remembered(monkeypatch):
    class NoUI:
        def request_confirmation(self, op, detail=""):
            return False
    monkeypatch.setattr(safety, "get_safety", lambda config=None: NoUI())
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    risk.begin_turn("headless")
    assert _click().startswith("Cancelled") and _click().startswith("Cancelled")


def test_config_limits_are_clamped(monkeypatch):
    import core.llm_client as lc
    for cfg, ttl, cap in (({}, 120, 60), ({"risk_task_ttl": 1, "risk_task_max_actions": 0}, 10, 1),
                          ({"risk_task_ttl": 99999, "risk_task_max_actions": 99999}, 900, 500),
                          ({"risk_task_ttl": "x", "risk_task_max_actions": "y"}, 120, 60)):
        monkeypatch.setattr(lc, "_load_config", lambda c=cfg: dict(c))
        assert risk.task_ttl() == ttl and risk.task_max_actions() == cap


def test_real_computer_control_function_is_gated_end_to_end(ui):
    import actions.computer_control as cc
    ui.answer = False
    risk.begin_turn("click the thing")
    out = cc.computer_control({"action": "click", "x": 10, "y": 10})
    assert out.startswith("Cancelled") and "click the thing" in ui.asked[0][1]
    assert cc.computer_control({"action": "type", "text": "x"}).startswith("Cancelled")
    assert len(ui.asked) == 1


def test_real_browser_control_function_is_gated_end_to_end(ui):
    import actions.browser_control as bc
    ui.answer = False
    risk.begin_turn("fill in the form")
    out = bc.browser_control({"action": "fill_form", "fields": {"a": "b"}})
    assert out.startswith("Cancelled") and "fill in the form" in ui.asked[0][1]
    assert "web browser" in ui.asked[0][1]


# ── the turn hook in conversation_core ───────────────────────────────────────
class _FakeCore:
    """Just enough of ConversationCore for _process_message_inner to run its first lines
    and return (the chat-command handler claims the message)."""
    class _Obs:
        def count(self, *a, **k): pass
    _loop_guard = None
    _obs = _Obs()
    def _handle_chat_command(self, text): return True


def _run_turn(text):
    from core.conversation_core import ConversationCoreMixin as ConversationCore
    ConversationCore._process_message_inner(_FakeCore(), text)


def test_each_user_message_starts_a_new_request_for_the_risk_layer(ui):
    _run_turn("first thing the user said")
    _click()
    assert "first thing the user said" in ui.asked[0][1]
    _click()
    assert len(ui.asked) == 1
    _run_turn("second thing the user said")
    _click()
    assert len(ui.asked) == 2 and "second thing the user said" in ui.asked[1][1]


def test_system_alerts_are_not_user_requests_and_do_not_end_the_approval(ui):
    _run_turn("do my task")
    _click()
    _run_turn("[SYSTEM_ALERT] GPU load is at 100%. Briefly inform the user.")
    _click()
    assert len(ui.asked) == 1                                   # the user's approval is still in force


def test_a_failure_in_the_risk_layer_never_breaks_the_turn(monkeypatch):
    monkeypatch.setattr(risk, "begin_turn", lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    _run_turn("anything")                                       # must not raise
