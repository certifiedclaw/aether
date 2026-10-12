"""Shutdown/restart must be approved by the USER (UI dialog), never by a model-supplied
'confirmed' argument. The real power commands are always stubbed here."""
import pytest

import actions.computer_settings as cs


@pytest.fixture
def power(monkeypatch):
    """Stub the real power actions and record whether they ran."""
    ran = []
    monkeypatch.setattr(cs, "_PYAUTOGUI", True)
    monkeypatch.setitem(cs.ACTION_MAP, "shutdown", lambda: ran.append("shutdown"))
    monkeypatch.setitem(cs.ACTION_MAP, "restart", lambda: ran.append("restart"))
    return ran


@pytest.mark.parametrize("action", ["shutdown", "restart"])
@pytest.mark.parametrize("claimed", ["yes", "true", "1", "confirm", "YES"])
def test_model_supplied_confirmed_flag_no_longer_approves(monkeypatch, power, action, claimed):
    asked = []
    monkeypatch.setattr(cs, "_confirm_dangerous", lambda a: asked.append(a) or False)
    out = cs.computer_settings({"action": action, "confirmed": claimed})
    assert power == []                                   # nothing ran
    assert out.startswith("Cancelled")
    assert asked == [action]                             # the USER was asked, flag ignored


@pytest.mark.parametrize("action", ["shutdown", "restart"])
def test_runs_only_after_the_user_approves(monkeypatch, power, action):
    monkeypatch.setattr(cs, "_confirm_dangerous", lambda a: True)
    out = cs.computer_settings({"action": action})       # note: no 'confirmed' at all
    assert power == [action] and out == f"Done: {action}."


def test_confirmation_denied_without_ui_callback(monkeypatch):
    import core.safety as safety

    class NoUI:
        def request_confirmation(self, op, detail=""):
            return False                                  # SafetyLayer with no callback
    monkeypatch.setattr(safety, "get_safety", lambda config=None: NoUI())
    assert cs._confirm_dangerous("shutdown") is False


def test_confirmation_error_means_deny(monkeypatch):
    import core.safety as safety

    def boom(config=None):
        raise RuntimeError("no config")
    monkeypatch.setattr(safety, "get_safety", boom)
    assert cs._confirm_dangerous("restart") is False


def test_dialog_names_the_action_and_receives_the_users_answer(monkeypatch):
    import core.safety as safety
    seen = {}

    class UI:
        def request_confirmation(self, op, detail=""):
            seen["op"], seen["detail"] = op, detail
            return True
    monkeypatch.setattr(safety, "get_safety", lambda config=None: UI())
    assert cs._confirm_dangerous("shutdown") is True
    assert "shutdown" in seen["op"] and "shutdown" in seen["detail"]


def test_harmless_actions_are_not_gated(monkeypatch, power):
    asked = []
    monkeypatch.setattr(cs, "_confirm_dangerous", lambda a: asked.append(a) or False)
    monkeypatch.setitem(cs.ACTION_MAP, "mute", lambda: power.append("mute"))
    out = cs.computer_settings({"action": "mute"})
    assert asked == [] and power == ["mute"] and out == "Done: mute."
