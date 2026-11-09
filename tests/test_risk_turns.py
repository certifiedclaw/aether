"""core/risk approvals are scoped to the request that got them."""
import threading
import time

from agent import task_queue as tq


def _risk_env(monkeypatch, answers):
    """Real core.risk with the dialog replaced by a scripted answer list (default: deny)."""
    from core import risk, safety
    risk.reset_state()
    monkeypatch.setattr(risk, "_overrides", lambda: {})
    asked = []

    def fake_confirm(op, detail=""):
        asked.append(op)
        return answers.pop(0) if answers else False
    monkeypatch.setattr(safety, "confirm_action", fake_confirm)
    return risk, asked


def test_background_plan_approval_survives_a_new_user_message_and_is_not_spendable_by_it(monkeypatch):
    risk, asked = _risk_env(monkeypatch, [])
    step = {"tool": "file_controller", "description": "delete",
            "parameters": {"action": "delete", "path": "/home/x/a.txt"}}
    assert risk.tier_for("file_controller", "delete") == risk.ASK
    approved, release, result = threading.Event(), threading.Event(), []

    def background():
        risk.begin_turn("[background task] clean up", background=True)
        ok, _ = risk.request_plan_approval("clean up", [step], confirm=lambda op, detail="": True)
        approved.set()
        release.wait(5)
        result.append((ok, risk.check("file_controller", "delete", step["parameters"])))

    t = threading.Thread(target=background)
    t.start()
    assert approved.wait(5)
    risk.begin_turn("something the user typed")                       # new user request
    out = risk.check("file_controller", "delete", step["parameters"])
    assert out and out.startswith("Cancelled") and asked              # had to ask: not B's grant
    release.set()
    t.join(5)
    assert result == [(True, None)]                                   # B's own grant still valid


def test_background_task_cannot_spend_the_users_task_grant(monkeypatch):
    risk, asked = _risk_env(monkeypatch, [True])                      # user approves once
    assert risk.tier_for("computer_control", "click") == risk.TASK
    risk.begin_turn("open notepad")
    assert risk.check("computer_control", "click", {}) is None        # dialog 1 -> grant
    assert risk.check("computer_control", "click", {}) is None        # covered, no dialog
    assert len(asked) == 1
    out = []

    def background():
        risk.begin_turn("[background task] other", background=True)
        out.append(risk.check("computer_control", "click", {}))

    t = threading.Thread(target=background)
    t.start()
    t.join(5)
    assert out and out[0].startswith("Cancelled")                     # had to ask (and was denied)
    assert len(asked) == 2


def test_background_dialog_names_the_task_not_the_users_last_message(monkeypatch):
    risk, _ = _risk_env(monkeypatch, [])
    from core import safety
    seen = []
    monkeypatch.setattr(safety, "confirm_action", lambda op, detail="": seen.append(detail) or False)
    risk.begin_turn("what is the weather")

    def background():
        risk.begin_turn("[background task] tidy desktop", background=True)
        risk.check("computer_control", "click", {})

    t = threading.Thread(target=background)
    t.start()
    t.join(5)
    assert "[background task] tidy desktop" in seen[0] and "weather" not in seen[0]


def test_helper_thread_without_its_own_turn_shares_the_latest_user_request(monkeypatch):
    risk, asked = _risk_env(monkeypatch, [True])
    risk.begin_turn("parallel work")
    assert risk.check("computer_control", "click", {}) is None
    out = []
    t = threading.Thread(target=lambda: out.append(risk.check("computer_control", "click", {})))
    t.start()
    t.join(5)
    assert out == [None] and len(asked) == 1


def test_stale_user_turn_gets_no_grants(monkeypatch):
    risk, asked = _risk_env(monkeypatch, [True])
    risk.begin_turn("first")
    # a newer user message arrives on another thread (as the command loops do) ...
    t = threading.Thread(target=lambda: risk.begin_turn("second"))
    t.start()
    t.join()
    # ... so a late call from "first" asks afresh and nothing it approves is stored
    assert risk.check("computer_control", "click", {}) is None
    assert risk.check("computer_control", "click", {}) is not None
    assert len(asked) == 2


def test_task_queue_runs_each_task_in_a_background_turn():
    from core import risk
    seen = []

    class Probe:
        def execute(self, goal, speak=None, cancel_flag=None):
            t = risk.current_turn()
            seen.append((t.background, t.text))
            return "ok"

    q = tq.TaskQueue(max_concurrent=1, persist_path=None)
    q._executor = Probe()
    q.submit("tidy the downloads folder")
    q.start()
    deadline = time.time() + 5
    while time.time() < deadline and not seen:
        time.sleep(0.05)
    q.stop()
    assert seen == [(True, "[background task] tidy the downloads folder")]


def test_failed_begin_turn_drops_the_previous_requests_approvals(monkeypatch):
    from core.conversation_core import ConversationCoreMixin
    from tests.test_risk_task import _FakeCore
    risk, asked = _risk_env(monkeypatch, [True, True])
    ConversationCoreMixin._process_message_inner(_FakeCore(), "first request")
    assert risk.check("computer_control", "click", {}) is None      # asks, approved
    assert risk.check("computer_control", "click", {}) is None      # covered by the grant
    assert len(asked) == 1
    monkeypatch.setattr(risk, "begin_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ConversationCoreMixin._process_message_inner(_FakeCore(), "second request")   # must not raise
    assert risk.check("computer_control", "click", {}) is None      # asks afresh
    assert len(asked) == 2
