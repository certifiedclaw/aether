"""core/ui_prefs.py - everything about making Aether's UI easy for non-technical people.

THE ONE FILE TO EDIT for first-run wording, text-size presets and the high-contrast
palette. The on/off switches live in the normal config (config/api_keys.json):

    ui.simple_setup    true   first run shows a plain-language page; the technical
                              options sit behind "Advanced setup"
    ui.text_scale      1.0    1.0 normal, 1.25 large, 1.5 extra large (0.8 - 2.0)
    ui.min_font_pt     9      no text smaller than this after scaling (0 = off)
    ui.high_contrast   false  brighter text and borders (takes effect on restart)
    tts_enabled        true   false = text only, Aether never speaks

Pure functions, no Qt: the UI calls them and the tests exercise them directly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ── text-size presets shown as buttons: (id, label, scale) ───────────────
TEXT_SIZES: tuple[tuple[str, str, float], ...] = (
    ("normal", "Normal", 1.0),
    ("large", "Large", 1.25),
    ("xlarge", "Extra large", 1.5),
)
MIN_SCALE, MAX_SCALE = 0.8, 2.0
DEFAULT_MIN_FONT_PT = 9.0

# ── first-run wording (edit freely; no code depends on the exact text) ───
WORDING = {
    "title":        "Welcome to Aether",
    "subtitle":     "Let's get you set up. This takes about a minute.",
    "picked":       "Aether chose settings that suit this computer.",
    "picked_model": "It thinks on this computer, so nothing you say leaves it ({tag}).",
    "picked_voice": "It listens and talks using free built-in voices. No account needed.",
    "ask_speak":    "Should Aether speak its answers out loud?",
    "speak_yes":    "Yes, speak to me",
    "speak_no":     "No, text only",
    "ask_size":     "Text size",
    "go":           "Get started",
    "advanced":     "Advanced setup (for technical users)",
    "simple":       "← Back to simple setup",
    "adv_title":    "ADVANCED SETUP",
    "adv_sub":      "Choose the speech, AI and voice engines yourself.",
    "speak_row":    "Speak answers out loud",
    "size_row":     "TEXT SIZE",
}

# ── high-contrast overrides for core colours in ui.py (class C) ──────────
CONTRAST_OVERRIDES = {
    "TEXT":     "#ffffff",
    "TEXT_MED": "#e6eeee",
    "TEXT_DIM": "#c3cfd2",
    "BORDER":   "#566b72",
    "BORDER_B": "#8aa0a6",
    "BORDER_A": "#6d858c",
    "PRI_DIM":  "#8bc9b2",
}

SAFE_FALLBACK_MODEL = "qwen2.5:1.5b"     # smallest model: runs on almost anything
DEFAULT_VOICE = "en-US-GuyNeural"


# ── settings ──────────────────────────────────────────────────────────────

def _flag(v: Any, default: bool) -> bool:
    if v is None:
        return default
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


@dataclass(frozen=True)
class UiPrefs:
    text_scale: float = 1.0
    min_font_pt: float = DEFAULT_MIN_FONT_PT
    simple_setup: bool = True
    high_contrast: bool = False
    tts_enabled: bool = True


def _num(v: Any, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _get(cfg: dict | None, name: str, default: Any = None) -> Any:
    cfg = cfg or {}
    if f"ui.{name}" in cfg:
        return cfg[f"ui.{name}"]
    nested = cfg.get("ui")
    if isinstance(nested, dict) and name in nested:
        return nested[name]
    return default


def load(cfg: dict | None) -> UiPrefs:
    """Settings with bad or missing values replaced by safe defaults. Never raises."""
    cfg = cfg or {}
    return UiPrefs(
        text_scale=_num(_get(cfg, "text_scale"), 1.0, MIN_SCALE, MAX_SCALE),
        min_font_pt=_num(_get(cfg, "min_font_pt"), DEFAULT_MIN_FONT_PT, 0.0, 24.0),
        simple_setup=_flag(_get(cfg, "simple_setup"), True),
        high_contrast=_flag(_get(cfg, "high_contrast"), False),
        tts_enabled=_flag(cfg.get("tts_enabled"), True),
    )


def size_id(scale: float) -> str:
    """The preset closest to `scale` (for highlighting the right button)."""
    return min(TEXT_SIZES, key=lambda s: abs(s[2] - scale))[0]


# ── scaling ───────────────────────────────────────────────────────────────

def scaled_pt(pt: float, scale: float, floor_pt: float) -> float:
    """`pt` times `scale`, but never below the floor (the floor only ever raises)."""
    out = pt * scale
    return max(out, floor_pt) if floor_pt > 0 else out


_FONT_SIZE = re.compile(r"(font-size\s*:\s*)(\d+(?:\.\d+)?)(pt|px)", re.I)


def scale_stylesheet(ss: str, scale: float, floor_pt: float) -> str:
    """Rewrite every `font-size: Npt|Npx` in a Qt stylesheet. Always start from the
    ORIGINAL stylesheet, not an already scaled one, or sizes compound."""
    if not ss or "font-size" not in ss:
        return ss

    def repl(m: re.Match) -> str:
        n, unit = float(m.group(2)), m.group(3).lower()
        if unit == "pt":
            new = scaled_pt(n, scale, floor_pt)
        else:                                     # px: floor expressed at 96 dpi
            new = scaled_pt(n, scale, floor_pt * 96 / 72 if floor_pt > 0 else 0)
        return f"{m.group(1)}{new:g}{unit}"
    return _FONT_SIZE.sub(repl, ss)


def scaled_height(px: int, scale: float) -> int:
    """Fixed widget heights grow with the text (never shrink)."""
    return int(round(px * scale)) if scale > 1.0 else px


# ── first-run: choose settings for the user ───────────────────────────────

def pick_model(recs: list | None) -> str:
    """The recommended Ollama tag for this computer (first flagged, else first, else the
    smallest safe model). `recs` are core.device_specs.ModelRecommendation objects."""
    recs = list(recs or [])
    for r in recs:
        if getattr(r, "recommended", False):
            return str(r.tag)
    if recs:
        return str(recs[0].tag)
    return SAFE_FALLBACK_MODEL


def simple_config(*, model_tag: str, speak: bool, text_scale: float = 1.0,
                  stt_model: str = "small", voice: str = DEFAULT_VOICE) -> dict:
    """The complete first-run config for the plain-language path: local Ollama model,
    Whisper listening, free Edge voice. No account or API key needed."""
    return {
        "stt_engine": "whisper", "stt_model": stt_model, "stt_language": "auto",
        "llm_provider": "ollama", "llm_url": "http://localhost:11434", "llm_model": model_tag,
        "tts_engine": "edgetts", "tts_voice": voice, "tts_speed": "1.0",
        "tts_enabled": bool(speak),
        "ui.text_scale": _num(text_scale, 1.0, MIN_SCALE, MAX_SCALE),
        "elevenlabs_api_key": "", "gemini_api_key": "", "openai_api_key": "",
    }
