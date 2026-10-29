"""Roadmap #16: the dead `session_memory` parameter is gone from actions/,
while the plugin API (which genuinely passes a memory engine) keeps it."""
import inspect
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def test_actions_no_longer_declare_session_memory():
    offenders = [p.name for p in (REPO / "actions").glob("*.py")
                 if "session_memory" in p.read_text(encoding="utf-8")]
    assert not offenders, f"vestigial session_memory is back in: {offenders}"


def test_dispatcher_does_not_pass_it_to_actions():
    src = (REPO / "core" / "tool_dispatch.py").read_text(encoding="utf-8")
    # The only remaining mention is the plugin call, which is a real API.
    assert src.count("session_memory") == 1
    assert "self._plugins.execute_tool" in src.split("session_memory")[0][-400:]


def test_plugin_api_still_receives_session_memory():
    from core import plugins
    seen = {}

    def run(parameters, player=None, session_memory=None):
        seen["mem"] = session_memory
        return "ok"

    sentinel = object()
    assert plugins._call_plugin(run, {}, player=None, session_memory=sentinel) == "ok"
    assert seen["mem"] is sentinel


def test_weather_action_works_without_the_parameter():
    import actions.weather_report as wr
    assert "session_memory" not in inspect.signature(wr.weather_action).parameters
    with patch.object(wr, "_fetch_weather", return_value="Sunny, 21C"):
        assert wr.weather_action({"city": "Lisbon"}) == "Sunny, 21C"
    with patch.object(wr, "_fetch_weather", return_value=None), \
         patch.object(wr.webbrowser, "open", return_value=True):
        assert "Lisbon" in wr.weather_action({"city": "Lisbon", "time": "today"})
