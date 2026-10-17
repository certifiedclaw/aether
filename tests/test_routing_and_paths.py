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


def test_tier_model_uses_installed_configured_tag_and_selected_host(monkeypatch):
    from core import llm_client

    monkeypatch.setattr(llm_client, "_ollama_models_cache", {})
    monkeypatch.setattr(llm_client, "_load_config", lambda: {
        "llm_quick_model": "qwen2.5:1.5b",
    })
    monkeypatch.setattr(
        llm_client, "get_llm_settings",
        lambda: ("http://gpu-box:11434", "qwen2.5:7b"),
    )
    response = mock.Mock(status_code=200)
    response.json.return_value = {"models": [{"name": "qwen2.5:1.5b"}]}
    request = mock.Mock(return_value=response)
    monkeypatch.setattr(llm_client.requests, "get", request)

    assert llm_client.get_tier_model_settings("quick") == (
        "http://gpu-box:11434", "qwen2.5:1.5b", "configured", "qwen2.5:1.5b",
    )
    request.assert_called_once_with("http://gpu-box:11434/api/tags", timeout=2.5)


def test_tier_model_missing_tag_falls_back_to_selected_default(monkeypatch):
    from core import llm_client

    monkeypatch.setattr(llm_client, "_ollama_models_cache", {})
    monkeypatch.setattr(llm_client, "_load_config", lambda: {
        "llm_hard_model": "qwen3:14b",
    })
    monkeypatch.setattr(
        llm_client, "get_llm_settings",
        lambda: ("http://localhost:11434", "qwen2.5:7b"),
    )
    response = mock.Mock(status_code=200)
    response.json.return_value = {"models": [{"name": "qwen2.5:7b"}]}
    monkeypatch.setattr(llm_client.requests, "get", mock.Mock(return_value=response))

    assert llm_client.get_tier_model_settings("hard") == (
        "http://localhost:11434", "qwen2.5:7b", "missing", "qwen3:14b",
    )


def test_tier_model_unavailable_check_falls_back_and_is_cached(monkeypatch):
    from core import llm_client

    monkeypatch.setattr(llm_client, "_ollama_models_cache", {})
    monkeypatch.setattr(llm_client, "_load_config", lambda: {
        "llm_everyday_model": "llama3.2:3b",
    })
    monkeypatch.setattr(
        llm_client, "get_llm_settings",
        lambda: ("http://remote:11434", "qwen2.5:7b"),
    )
    request = mock.Mock(side_effect=llm_client.requests.ConnectionError("offline"))
    monkeypatch.setattr(llm_client.requests, "get", request)

    expected = ("http://remote:11434", "qwen2.5:7b", "unavailable", "llama3.2:3b")
    assert llm_client.get_tier_model_settings("everyday") == expected
    assert llm_client.get_tier_model_settings("everyday") == expected
    request.assert_called_once()


def test_stream_tier_routing_only_overrides_ollama_models(monkeypatch):
    from core import conversation_core as cc

    monkeypatch.setattr(
        cc, "get_tier_model_settings",
        lambda tier: ("http://localhost:11434", "qwen2.5:1.5b", "configured", "qwen2.5:1.5b"),
    )
    assert cc._local_tier_stream_route(tier_router.Tier.QUICK, "ollama") == (
        {
            "provider_override": "ollama",
            "url_override": "http://localhost:11434",
            "model_override": "qwen2.5:1.5b",
        },
        "configured", "qwen2.5:1.5b", "qwen2.5:1.5b",
    )
    assert cc._local_tier_stream_route(tier_router.Tier.QUICK, "gemini") == (
        {}, "default", "", "",
    )


def test_qwen_15b_prompt_guidance_targets_benchmark_failures(monkeypatch):
    import sys
    from types import ModuleType

    from core.prompt_builder import model_prompt_guidance
    from core.prompt_builder import PromptBuilderMixin

    guidance = model_prompt_guidance("qwen2.5:1.5b")
    assert "exact output constraints" in guidance
    assert "check each premise" in guidance.lower()
    assert "translate the exact word" in guidance.lower()
    assert "without tools" in guidance
    assert "never invent" in guidance.lower()
    assert model_prompt_guidance("qwen2.5:1.5b-instruct-q4_K_M") == guidance
    assert model_prompt_guidance("qwen2.5:7b") == ""
    assert model_prompt_guidance("qwen3:8b") == ""
    assert model_prompt_guidance("") == ""

    main = ModuleType("main")
    main._load_system_prompt = lambda: "base prompt"
    main.load_memory = lambda: {}
    main._LANGCHAIN_OK = main._OI_OK = main._LLAMA_OK = False
    monkeypatch.setitem(sys.modules, "main", main)

    class PromptBuilder(PromptBuilderMixin):
        _hybrid_mem = _semantic_mem = _goals = _mcp = _plugins = None

    builder = PromptBuilder()
    tuned = builder._build_system_prompt(model_name="qwen2.5:1.5b")
    untuned = builder._build_system_prompt(model_name="qwen2.5:7b")
    assert guidance in tuned
    assert guidance not in untuned


def test_tool_router_selects_right_tools():
    assert "computer_settings" in tool_router.select_tool_names("Turn the volume down a little.")
    assert "file_controller" in tool_router.select_tool_names("List the files on my desktop.")
    both = tool_router.select_tool_names("Turn the volume down a little, then tell me the weather in London.")
    assert {"computer_settings", "weather_report"} <= both


def test_explicit_output_only_requests_do_not_get_action_tools():
    prompt = "Write a JSON object with keys name and age. Output only JSON."
    assert tool_router.select_tool_names(prompt) == set()
    assert tool_router.select_tool_names_hard(prompt) == set()

    prompt_with_action = "Output only JSON and search the web for Python 3.14."
    assert "web_search" in tool_router.select_tool_names(prompt_with_action)


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
