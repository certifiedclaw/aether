"""Regression tests for tier routing, known folders, weather and open_app honesty."""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from core import tier_router, tool_router


@pytest.mark.parametrize("msg", [
    "Turn the volume down a little.", "Turn the volume up a little.",
    "List the files on my desktop.", "What is 240 times 17?",
    "What's the weather in London right now?", "Open an application called Zorblax Quantum Editor.",
    "Remember that my favorite color is teal.",
])
def test_tool_prompts_not_quick(msg):
    assert tier_router.classify(msg).tier != tier_router.Tier.QUICK


@pytest.mark.parametrize("msg", ["Hello.", "Hello, how are you today?", "thanks"])
def test_greetings_stay_quick_or_everyday(msg):
    assert tier_router.classify(msg).tier in (tier_router.Tier.QUICK, tier_router.Tier.EVERYDAY)


def test_greeting_pattern_still_quick():
    assert tier_router.classify("Hello.").tier == tier_router.Tier.QUICK


def test_tool_router_selects_right_tools():
    assert "computer_settings" in tool_router.select_tool_names("Turn the volume down a little.")
    assert "file_controller" in tool_router.select_tool_names("List the files on my desktop.")
    both = tool_router.select_tool_names("Turn the volume down a little, then tell me the weather in London.")
    assert {"computer_settings", "weather_report"} <= both


def test_known_folder_none_off_windows():
    from core import known_folders
    with mock.patch("platform.system", return_value="Linux"):
        assert known_folders.known_folder("desktop") is None
    assert known_folders.desktop_dir().name  # always returns a path


def test_file_controller_uses_known_folder(tmp_path):
    from actions import file_controller as fc
    with mock.patch.object(fc, "_known_folder", return_value=tmp_path):
        assert fc._get_desktop() == tmp_path


def test_weather_returns_data_without_browser():
    from actions import weather_report as wr
    payload = {"current_condition": [{"temp_C": "12", "FeelsLikeC": "10", "humidity": "70",
                                      "weatherDesc": [{"value": "Light rain"}]}], "weather": []}
    resp = mock.Mock(); resp.json.return_value = payload; resp.raise_for_status.return_value = None
    with mock.patch("requests.get", return_value=resp), \
         mock.patch("webbrowser.open") as wb:
        out = wr.weather_action({"city": "London"})
    assert "12 degrees" in out and "light rain" in out
    wb.assert_not_called()


def test_weather_falls_back_to_browser():
    from actions import weather_report as wr
    with mock.patch("requests.get", side_effect=OSError("down")), \
         mock.patch("webbrowser.open", return_value=True) as wb:
        out = wr.weather_action({"city": "London"})
    assert "Showing the weather" in out
    wb.assert_called_once()


def test_open_app_unknown_app_not_claimed_opened():
    from actions import open_app as oa
    with mock.patch.dict(oa._OS_LAUNCHERS, {oa._SYSTEM: lambda name: False}):
        out = oa.open_app({"app_name": "Zorblax Quantum Editor"})
    assert "Opened" not in out and "Could not confirm" in out


def test_window_appeared_false_without_match():
    from actions import open_app as oa
    fake = mock.Mock(); fake.getAllTitles.return_value = ["Notepad", "Chrome"]
    with mock.patch.dict(sys.modules, {"pygetwindow": fake}):
        assert oa._window_appeared("Zorblax Quantum Editor", timeout=0.1) is False
        assert oa._window_appeared("Notepad", timeout=0.1) is True
