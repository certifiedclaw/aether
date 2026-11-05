"""Settings changes must only rebuild what they affect.

Regression for a live log: switching Gemini models five times in three minutes
reloaded Whisper (CPU model load + a Hugging Face request) and rebuilt TTS each time.
"""
import ast
import re
from pathlib import Path

import pytest

from core import reconfigure_plan as rp
from core.reconfigure_plan import plan_reconfigure

BASE = {
    "llm_model": "qwen3:8b", "gemini_model": "gemini-3.8-flash",
    "tts_engine": "edgetts", "tts_voice": "en-US-GuyNeural",
    "stt_engine": "whisper", "stt_model": "small", "stt_language": "auto",
}


def _plan(**changes):
    return plan_reconfigure(BASE, {**BASE, **changes})


def test_identical_config_rebuilds_nothing():
    p = plan_reconfigure(BASE, dict(BASE))
    assert (p.rebuild_tts, p.rebuild_stt, p.stt_engine_changed) == (False, False, False)


@pytest.mark.parametrize("changes", [
    {"llm_model": "llama3.2:3b"},
    {"gemini_model": "gemini-2.5-pro"},
    {"gemini_api_key": "new-key"},
    {"gemini_cooldown": 30},
    {"desktop_mate": False, "assistant_personality": False},
])
def test_unrelated_settings_never_touch_audio(changes):
    p = _plan(**changes)
    assert not p.rebuild_tts and not p.rebuild_stt


@pytest.mark.parametrize("key,value", [
    ("tts_voice", "en-GB-RyanNeural"), ("tts_engine", "kokoro"),
    ("tts_speed", 1.2), ("elevenlabs_api_key", "k"),
])
def test_tts_relevant_changes_rebuild_only_tts(key, value):
    p = _plan(**{key: value})
    assert p.rebuild_tts and not p.rebuild_stt


@pytest.mark.parametrize("key,value", [
    ("stt_model", "base"), ("stt_language", "en"), ("vosk_model_path", "C:/vosk"),
])
def test_stt_relevant_changes_rebuild_only_stt(key, value):
    p = _plan(**{key: value})
    assert p.rebuild_stt and not p.rebuild_tts and not p.stt_engine_changed


def test_defaults_and_case_do_not_count_as_changes():
    old = {"tts_engine": "EdgeTTS", "stt_language": "AUTO"}
    new = {"tts_engine": "edgetts", "stt_model": "small", "stt_language": "auto", "tts_voice": ""}
    p = plan_reconfigure(old, new)
    assert not p.rebuild_tts and not p.rebuild_stt


def test_stt_engine_change_is_reported_and_never_hot_rebuilt():
    p = _plan(stt_engine="vosk", stt_model="other")
    assert p.stt_engine_changed and not p.rebuild_stt


def test_a_subsystem_that_failed_to_load_is_rebuilt_even_if_unchanged():
    p = plan_reconfigure(BASE, dict(BASE), have_tts=False, have_stt=False)
    assert p.rebuild_tts and p.rebuild_stt
    q = plan_reconfigure(BASE, {**BASE, "stt_engine": "vosk"}, have_stt=False)
    assert q.stt_engine_changed and not q.rebuild_stt   # engine change still needs a restart


def test_none_configs_are_tolerated():
    p = plan_reconfigure(None, None)
    assert not p.rebuild_tts and not p.rebuild_stt


def test_plan_watches_every_key_create_tts_player_reads():
    # If create_tts_player ever reads a new setting, a change to it would silently
    # stop applying. This fails first.
    # Read it from source: importing core.tts needs PortAudio via sounddevice.
    tts_src = (Path(__file__).resolve().parent.parent / "core" / "tts.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(tts_src))
              if isinstance(n, ast.FunctionDef) and n.name == "create_tts_player")
    src = ast.get_source_segment(tts_src, fn)
    read = set(re.findall(r"config\.get\(\s*[\"']([a-z_0-9]+)[\"']", src))
    read |= set(re.findall(r"get_secret\(\s*[\"']([a-z_0-9]+)[\"']", src))
    assert read, "found no config keys - pattern out of date"
    assert read <= set(rp._TTS_DEFAULTS), read - set(rp._TTS_DEFAULTS)


def test_main_reconfigure_uses_the_plan():
    # main.py is heavy to import; check the wiring from its source instead.
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_do_reconfigure")
    body = ast.get_source_segment(src, fn)
    assert "plan_reconfigure(" in body
    assert "if plan.rebuild_tts" in body and "plan.rebuild_stt" in body
    assert "restart required" in body   # engine change path kept
