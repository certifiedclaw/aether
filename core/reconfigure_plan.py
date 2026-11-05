"""core/reconfigure_plan.py - decide what a settings change actually needs rebuilt.

AetherLocal._do_reconfigure used to rebuild the TTS player and reload Whisper on
EVERY settings apply, even when only the LLM model changed. Reloading Whisper is
a CPU model load plus a Hugging Face request, so switching Gemini models five
times in three minutes meant five Whisper reloads. This module compares only the
settings each subsystem really reads, with the same defaults the constructors use.

    plan = plan_reconfigure(old_cfg, new_cfg)
    plan.rebuild_tts / plan.rebuild_stt / plan.stt_engine_changed
"""
from __future__ import annotations

from dataclasses import dataclass

# Keys each subsystem reads (see core.tts.create_tts_player and main.py's STT setup).
# elevenlabs_api_key is compared (never logged) because a new key needs a new player.
_TTS_DEFAULTS = {
    "tts_engine": "edgetts",
    "tts_voice": None,
    "tts_speed": None,
    "elevenlabs_api_key": None,
}
_STT_DEFAULTS = {
    "stt_engine": "whisper",
    "stt_model": "small",
    "stt_language": "auto",
    "vosk_model_path": None,
}
_LOWER = {"tts_engine", "stt_engine", "stt_language"}


def _view(cfg: dict | None, defaults: dict) -> tuple:
    cfg = cfg or {}
    out = []
    for key, default in defaults.items():
        value = cfg.get(key)
        if value is None or value == "":
            value = default
        if key in _LOWER and isinstance(value, str):
            value = value.strip().lower()
        out.append(value)
    return tuple(out)


def tts_changed(old: dict | None, new: dict | None) -> bool:
    return _view(old, _TTS_DEFAULTS) != _view(new, _TTS_DEFAULTS)


def stt_changed(old: dict | None, new: dict | None) -> bool:
    return _view(old, _STT_DEFAULTS) != _view(new, _STT_DEFAULTS)


def stt_engine_changed(old: dict | None, new: dict | None) -> bool:
    return _view(old, {"stt_engine": "whisper"}) != _view(new, {"stt_engine": "whisper"})


@dataclass(frozen=True)
class ReconfigurePlan:
    rebuild_tts: bool
    rebuild_stt: bool
    stt_engine_changed: bool


def plan_reconfigure(old: dict | None, new: dict | None, *,
                     have_tts: bool = True, have_stt: bool = True) -> ReconfigurePlan:
    """What to rebuild. A subsystem that doesn't exist yet (failed to load at
    startup) is always rebuilt. An STT *engine* change still needs a restart, so
    it is reported separately and never hot-rebuilt."""
    engine_changed = stt_engine_changed(old, new)
    return ReconfigurePlan(
        rebuild_tts=(not have_tts) or tts_changed(old, new),
        rebuild_stt=(not engine_changed) and ((not have_stt) or stt_changed(old, new)),
        stt_engine_changed=engine_changed,
    )
