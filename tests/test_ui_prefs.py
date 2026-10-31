"""Easy-to-use UI: plain-language first run, text size, contrast, text-only mode.
Settings and wording live in core/ui_prefs.py."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core import ui_prefs as up


# ---------------- settings ----------------

def test_defaults_are_friendly():
    p = up.load({})
    assert p.simple_setup is True and p.text_scale == 1.0 and p.tts_enabled is True
    assert p.high_contrast is False and p.min_font_pt == up.DEFAULT_MIN_FONT_PT


def test_flat_and_nested_keys_and_bad_values():
    assert up.load({"ui.text_scale": 1.5}).text_scale == 1.5
    assert up.load({"ui": {"text_scale": "1.25", "simple_setup": "false"}}).simple_setup is False
    assert up.load({"ui.text_scale": "huge"}).text_scale == 1.0
    assert up.load({"ui.text_scale": 99}).text_scale == up.MAX_SCALE
    assert up.load({"ui.text_scale": 0.1}).text_scale == up.MIN_SCALE
    assert up.load({"tts_enabled": "no"}).tts_enabled is False
    assert up.load({"tts_enabled": False}).tts_enabled is False
    assert up.load({"ui.high_contrast": "yes"}).high_contrast is True
    assert up.load(None).text_scale == 1.0


def test_size_presets_map_back():
    for sid, _label, scale in up.TEXT_SIZES:
        assert up.size_id(scale) == sid
    assert up.size_id(1.3) == "large"


# ---------------- scaling ----------------

def test_floor_only_raises_and_zero_disables():
    assert up.scaled_pt(7, 1.0, 9) == 9
    assert up.scaled_pt(12, 1.0, 9) == 12
    assert up.scaled_pt(7, 1.5, 9) == 10.5
    assert up.scaled_pt(7, 1.0, 0) == 7


def test_stylesheet_font_sizes_are_rewritten_not_compounded():
    ss = "QLabel { color: red; font-size: 8pt; } QPushButton { font-size: 12px; }"
    once = up.scale_stylesheet(ss, 1.5, 9)
    assert "font-size:12pt" in once.replace(" ", "") and "font-size:18px" in once.replace(" ", "")
    # rescaling the ORIGINAL again gives the same result (callers never feed back output)
    assert up.scale_stylesheet(ss, 1.5, 9) == once
    assert up.scale_stylesheet("QLabel { color: red; }", 1.5, 9) == "QLabel { color: red; }"
    assert up.scale_stylesheet("", 1.5, 9) == ""
    assert "font-size:9pt" in up.scale_stylesheet("x { font-size: 7pt; }", 1.0, 9).replace(" ", "")


def test_fixed_heights_grow_but_never_shrink():
    assert up.scaled_height(30, 1.5) == 45
    assert up.scaled_height(30, 1.0) == 30
    assert up.scaled_height(30, 0.8) == 30


# ---------------- first-run choices ----------------

def test_pick_model_prefers_recommended_then_first_then_safe_default():
    a = SimpleNamespace(tag="a", recommended=False)
    b = SimpleNamespace(tag="b", recommended=True)
    assert up.pick_model([a, b]) == "b"
    assert up.pick_model([a]) == "a"
    assert up.pick_model([]) == up.SAFE_FALLBACK_MODEL
    assert up.pick_model(None) == up.SAFE_FALLBACK_MODEL


def test_simple_config_is_complete_and_needs_no_account():
    cfg = up.simple_config(model_tag="llama3.2:3b", speak=False, text_scale=1.5)
    assert cfg["llm_provider"] == "ollama" and cfg["llm_model"] == "llama3.2:3b"
    assert cfg["stt_engine"] == "whisper" and cfg["tts_engine"] == "edgetts"
    assert cfg["tts_enabled"] is False and cfg["ui.text_scale"] == 1.5
    assert cfg["gemini_api_key"] == "" and cfg["openai_api_key"] == "" and cfg["elevenlabs_api_key"] == ""
    json.dumps(cfg)
    assert up.simple_config(model_tag="m", speak=True, text_scale=50)["ui.text_scale"] == up.MAX_SCALE


def test_every_wording_key_the_ui_uses_exists():
    src = (Path(__file__).resolve().parent.parent / "ui.py").read_text(encoding="utf-8")
    import re
    used = set(re.findall(r'W\["(\w+)"\]', src))
    assert used and used <= set(up.WORDING), used - set(up.WORDING)


def test_main_reads_tts_enabled_through_the_shared_parser():
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    assert "_tts_enabled(self._config)" in src and "_tts_enabled(new_config)" in src
    from core.ui_prefs import load
    assert load({"tts_enabled": "false"}).tts_enabled is False


# ---------------- the real dialog, driven under Qt ----------------

@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication
        return QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("no usable Qt platform plugin in this environment")


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    monkeypatch.setenv("AETHER_CONFIG_DIR", str(tmp_path))
    import core.config_home as ch
    ch._cache.clear()
    yield tmp_path
    ch._cache.clear()


def _window(qapp):
    import ui
    w = ui.MainWindow("")
    w.resize(1280, 800)
    w.show()
    qapp.processEvents()
    return ui, w


def test_first_run_shows_plain_page_and_hides_the_technical_one(qapp, fresh_config):
    ui, w = _window(qapp)
    ov = w._overlay
    assert ov._simple_box.isVisible() and not ov._advanced_box.isVisible()
    texts = " ".join(b.text() for b in ov._simple_box.findChildren(ui.QPushButton))
    for word in ("Yes, speak to me", "No, text only", "Get started", "Advanced setup"):
        assert word in texts
    shown = " ".join([l.text() for l in ov._simple_box.findChildren(ui.QLabel)] + [texts]).lower()
    for bad in ("whisper", "vosk", "neural", "ollama", "initialis", "tts", "api", "stt", "llm",
                "kokoro", "elevenlabs", "gemini", "openai"):
        assert bad not in shown, bad


def test_get_started_emits_a_working_config_without_touching_anything_technical(qapp, fresh_config):
    ui, w = _window(qapp)
    got = []
    w._overlay.done.disconnect()
    w._overlay.done.connect(got.append)
    w._overlay._simple_go()
    cfg = json.loads(got[0])
    assert cfg["llm_provider"] == "ollama" and cfg["llm_model"]
    assert cfg["tts_enabled"] is True and cfg["stt_engine"] == "whisper"


def test_text_only_choice_and_text_size_choice_are_saved(qapp, fresh_config):
    ui, w = _window(qapp)
    ov = w._overlay
    got = []
    ov.done.disconnect()
    ov.done.connect(got.append)
    ov._simple_speak_btns[False].click()
    ov._simple_size_btns["xlarge"].click()
    ov._simple_go()
    cfg = json.loads(got[0])
    assert cfg["tts_enabled"] is False and cfg["ui.text_scale"] == 1.5


def test_text_size_changes_the_whole_window_and_never_compounds(qapp, fresh_config):
    ui, w = _window(qapp)
    ov = w._overlay
    title = w.findChild(ui.QLabel)
    sizes = {}
    for sid in ("normal", "large", "xlarge", "large", "normal"):
        ov._simple_size_btns[sid].click()
        qapp.processEvents()
        sizes.setdefault(sid, []).append(w._input.font().pointSizeF())
    assert sizes["xlarge"][0] > sizes["large"][0] > sizes["normal"][0]
    assert sizes["large"][0] == sizes["large"][1]          # same size again: no drift
    assert sizes["normal"][0] == sizes["normal"][1]


def test_nothing_is_smaller_than_the_minimum_font(qapp, fresh_config):
    ui, w = _window(qapp)
    floor = up.DEFAULT_MIN_FONT_PT
    too_small = [(l.text()[:30], l.font().pointSizeF()) for l in w.findChildren(ui.QLabel)
                 if l.text() and 0 < l.font().pointSizeF() < floor - 0.01
                 and "font-size" not in l.styleSheet()]
    assert not too_small, too_small[:5]


def test_advanced_page_is_one_click_away_and_back_again(qapp, fresh_config):
    ui, w = _window(qapp)
    ov = w._overlay
    ov._show_advanced()
    assert ov._advanced_box.isVisible() and not ov._simple_box.isVisible()
    assert ov._llm_model_input.text()                       # prefilled with the recommended model
    ov._show_simple()
    assert ov._simple_box.isVisible() and not ov._advanced_box.isVisible()


def test_dialog_stays_centred_when_the_window_is_resized(qapp, fresh_config):
    ui, w = _window(qapp)
    ov = w._overlay
    for size in ((1280, 800), (1000, 700)):
        w.resize(*size)
        qapp.processEvents()
        c = w.centralWidget()
        assert abs((ov.x() + ov.width() / 2) - c.width() / 2) <= 2
        assert abs((ov.y() + ov.height() / 2) - c.height() / 2) <= 2
        assert ov.width() <= c.width() and ov.height() <= c.height()


def test_settings_screen_goes_straight_to_the_full_page_with_the_new_rows(qapp, fresh_config):
    ui, w = _window(qapp)
    w._overlay.hide()
    w._overlay = None
    w._show_config()
    ov = w._overlay
    assert ov._simple_box is None and ov._advanced_box.isVisible()
    got = []
    ov.done.disconnect()
    ov.done.connect(got.append)
    ov._speak_btns["no"].click()
    ov._size_btns["large"].click()
    ov._llm_model_input.setText("qwen3:8b")
    ov._submit()
    cfg = json.loads(got[0])
    assert cfg["tts_enabled"] is False and cfg["ui.text_scale"] == 1.25


def test_simple_setup_can_be_switched_off(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("AETHER_CONFIG_DIR", str(tmp_path))
    import core.config_home as ch
    ch._cache.clear()
    (tmp_path / "api_keys.json").write_text(json.dumps({"ui.simple_setup": False}), encoding="utf-8")
    ch._cache.clear()
    ui, w = _window(qapp)
    assert w._overlay._simple_box is None and w._overlay._advanced_box.isVisible()
    ch._cache.clear()


def test_high_contrast_brightens_dim_text():
    import ui
    assert up.CONTRAST_OVERRIDES["TEXT_DIM"] != "#8b9b9f"
    def lum(h):
        h = h.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    assert lum(up.CONTRAST_OVERRIDES["TEXT_DIM"]) > lum("#8b9b9f")
    assert lum(up.CONTRAST_OVERRIDES["BORDER"]) > lum("#2a393f")
