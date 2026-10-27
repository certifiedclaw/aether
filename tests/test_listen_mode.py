"""Roadmap #95: listen modes / push-to-talk so background media is never transcribed.

Pure logic first (mode resolution, PushToTalkGate), then the real mic loop
(_listen_whisper) driven by a fake microphone from a feeder thread, so audio
arrives interleaved with processing the way it does with PortAudio.
"""
from __future__ import annotations

import sys
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import listen_mode as lm
from core.listen_loops import _VADBuffer
from core.listen_mode import PushToTalkGate, resolve_listen_mode


# --------------------------------------------------------------------------
# resolve_listen_mode
# --------------------------------------------------------------------------
def R(cfg, wake=False, ptt=False):
    return resolve_listen_mode(cfg, wake_ready=wake, ptt_available=ptt)


def test_auto_is_the_default_and_prefers_wake_then_ptt_then_always():
    assert R({}, wake=True, ptt=True)[0] == "wake"
    assert R({}, wake=False, ptt=True)[0] == "ptt"
    mode, note = R({}, wake=False, ptt=False)
    assert mode == "always" and "WILL be transcribed" in note
    assert R({"listen_mode": "auto"}, ptt=True)[0] == "ptt"


def test_explicit_modes():
    assert R({"listen_mode": "always"}, wake=True, ptt=True) == ("always", "")
    assert R({"listen_mode": "ptt"}, ptt=True)[0] == "ptt"
    assert R({"listen_mode": "wake"}, wake=True, ptt=True)[0] == "wake"
    assert R({"listen_mode": " PTT "}, ptt=True)[0] == "ptt"          # trimmed, case-insensitive


def test_explicit_modes_never_leave_the_user_deaf():
    mode, note = R({"listen_mode": "ptt"}, ptt=False)
    assert mode == "always" and "keyboard" in note
    mode, note = R({"listen_mode": "wake"}, wake=False, ptt=True)
    assert mode == "always" and "not ready" in note     # explicit wake keeps its old fallback


def test_legacy_wake_word_enabled_is_respected_when_listen_mode_unset():
    assert R({"wake_word_enabled": True}, wake=True, ptt=True)[0] == "wake"
    assert R({"wake_word_enabled": "true"}, wake=True)[0] == "wake"
    # explicit false = the owner chose always-listening; must NOT be flipped to auto
    assert R({"wake_word_enabled": False}, wake=True, ptt=True) == ("always", "")
    mode, note = R({"wake_word_enabled": True}, wake=False, ptt=True)
    assert mode == "always" and "not ready" in note


def test_listen_mode_wins_over_legacy_flag():
    assert R({"listen_mode": "ptt", "wake_word_enabled": True}, wake=True, ptt=True)[0] == "ptt"


def test_unknown_value_falls_back_to_auto_with_a_note():
    mode, note = R({"listen_mode": "pushtotalk"}, ptt=True)
    assert mode == "ptt" and "unknown listen_mode" in note


# --------------------------------------------------------------------------
# PushToTalkGate
# --------------------------------------------------------------------------
class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_ptt_gate_starts_asleep_and_press_opens_it():
    g = PushToTalkGate(idle_s=6, clock=Clock())
    assert g.enabled and not g.awake
    assert g.wake() is True and g.awake
    assert g.wake() is False and g.awake           # second press: already open, just refreshes


def test_ptt_gate_idle_timeout_closes_window_once():
    c = Clock()
    g = PushToTalkGate(idle_s=6, clock=c)
    g.wake()
    c.t = 5; assert not g.check_sleep() and g.awake
    c.t = 7; assert g.check_sleep() and not g.awake
    assert not g.check_sleep()                      # only reports the close once


def test_ptt_gate_touch_and_repress_extend_the_window():
    c = Clock()
    g = PushToTalkGate(idle_s=6, clock=c)
    g.wake()
    c.t = 5; g.touch()
    c.t = 10; assert not g.check_sleep() and g.awake
    c.t = 15; g.wake()                              # press while open refreshes the timer
    c.t = 20; assert not g.check_sleep()


def test_ptt_gate_does_not_close_while_speaking():
    c = Clock()
    g = PushToTalkGate(idle_s=1, clock=c)
    g.wake(); c.t = 99
    assert not g.check_sleep(speaking=True) and g.awake


def test_ptt_gate_utterance_done_closes_immediately():
    g = PushToTalkGate(clock=Clock())
    g.wake(); g.utterance_done()
    assert not g.awake


def test_wake_gate_has_the_same_utterance_done_interface():
    from core.wake_word import WakeGate
    g = WakeGate(enabled=True, clock=Clock())
    g.wake(); g.utterance_done()
    assert g.awake                                   # wake mode stays awake until its own timeout


# --------------------------------------------------------------------------
# VAD in_speech
# --------------------------------------------------------------------------
SR = 16_000


def _loud(n=1024, amp=0.3, seed=0):
    t = np.arange(n) / SR
    return (amp * np.sin(2 * np.pi * 300 * t)
            + 0.1 * np.random.default_rng(seed).standard_normal(n)).astype(np.float32)


def _quiet(n=1024):
    return np.zeros(n, np.float32)


def test_vad_in_speech_tracks_the_utterance():
    v = _VADBuffer()
    assert v.in_speech is False
    for i in range(12):                       # ~0.77s of speech, above the VAD's 0.3s minimum
        v.process(_loud(seed=i))
    assert v.in_speech is True
    out = None
    for _ in range(40):
        got = v.process(_quiet())
        if got is not None:
            out = got
    assert out is not None and v.in_speech is False


# --------------------------------------------------------------------------
# The real mic loop, driven by a fake mic on a feeder thread
# --------------------------------------------------------------------------
class _Done(Exception):
    pass


def _harness(monkeypatch, cfg, feeder, register_ok=True):
    """Run ListenLoopsMixin._listen_whisper against a fake mic.

    feeder(ctl) runs on its own thread (like PortAudio's callback thread) and may call
    ctl.feed(block), ctl.press(), ctl.wait_processed(). The loop is ended once the
    feeder has finished and the loop has idled for a while.
    """
    from core import listen_loops, llm_client, wake_word

    monkeypatch.setattr(llm_client, "_load_config", lambda: cfg)
    monkeypatch.setattr(wake_word, "is_ready", lambda: False)
    monkeypatch.setattr(lm, "keyboard_available", lambda: True)

    seen = {"texts": [], "logs": [], "hotkey": None, "unregistered": False}
    processed = threading.Event()
    state = {"cb": None, "finished": False, "reads": 0, "on_press": None}

    def fake_register(hotkey, on_press):
        seen["hotkey"] = hotkey
        if not register_ok:
            return False, (lambda: None), f"could not register hotkey '{hotkey}': boom"
        state["on_press"] = on_press
        def _unreg(): seen["unregistered"] = True
        return True, _unreg, ""
    monkeypatch.setattr(lm, "register_push_to_talk", fake_register)

    class Ctl:
        def feed(self, blk):
            state["cb"](blk.reshape(-1, 1).astype(np.float32), len(blk), None, None)
        def feed_many(self, blk_fn, n, pause=0.0):
            for i in range(n):
                self.feed(blk_fn(i))
                if pause:
                    time.sleep(pause)
        def press(self):
            assert state["on_press"] is not None, "hotkey was never registered"
            state["on_press"]()
        def wait_processed(self, timeout=5.0):
            assert processed.wait(timeout), "utterance was never processed"
            time.sleep(0.15)    # let the loop thread finish its post-utterance bookkeeping
        seen_ = seen

    class FakeStream:
        def __init__(self, **kw): state["cb"] = kw["callback"]
        def __enter__(self):
            def run():
                try:
                    feeder(Ctl())
                finally:
                    state["finished"] = True
            threading.Thread(target=run, daemon=True).start()
            return self
        def __exit__(self, *a): return False

    fake_main = types.SimpleNamespace(
        sd=types.SimpleNamespace(InputStream=FakeStream),
        log_crash=lambda e, context="": seen.setdefault("crash", repr(e)))
    monkeypatch.setitem(sys.modules, "main", fake_main)

    class Obj(listen_loops.ListenLoopsMixin):
        def __init__(self):
            self.ui = types.SimpleNamespace(muted=False, set_state=lambda s: None,
                                            write_log=lambda m: seen["logs"].append(m))
            self._speaking_lock = threading.Lock()
            self._barge_in_count = 0
            self._BARGE_IN_THRESHOLD = 0.02
            self._BARGE_IN_FRAMES = 6
            self._stt = types.SimpleNamespace(transcribe=lambda a: "hi there")
        @property
        def _speaking(self):
            if state["finished"]:
                state["reads"] += 1
                if state["reads"] > 25:         # ~2.5s idle after the feeder ended
                    raise _Done()
            return False
        def _interrupt_speaking(self): pass
        def _process_message(self, t):
            seen["texts"].append(t)
            processed.set()

    Obj()._listen_whisper()
    return seen


def _speech(ctl, blocks=12, pause=0.0):
    ctl.feed_many(lambda i: _loud(seed=i), blocks, pause)
    ctl.feed_many(lambda i: _quiet(), 20)


BASE = {"echo_guard": False, "listen_mode": "ptt"}


def test_background_audio_is_never_transcribed_in_ptt_mode(monkeypatch):
    """The whole point of #95: a TikTok playing in the room must not reach Whisper."""
    def feeder(ctl):
        _speech(ctl, blocks=40)           # loud, speech-like background audio, no key press
    seen = _harness(monkeypatch, dict(BASE), feeder)
    assert seen["texts"] == []
    assert seen["hotkey"] == lm.DEFAULT_PTT_HOTKEY
    assert any("Push-to-talk on" in m for m in seen["logs"])
    assert seen["unregistered"] is True    # hotkey hook released when the loop ends


def test_control_without_ptt_the_same_background_audio_is_transcribed(monkeypatch):
    """Same audio with the gate off IS transcribed, proving the test above bites."""
    def feeder(ctl):
        _speech(ctl, blocks=40)
    seen = _harness(monkeypatch, {"echo_guard": False, "listen_mode": "always"}, feeder)
    assert seen["texts"] == ["hi there"]


def test_press_then_speak_is_transcribed_and_window_closes_after(monkeypatch):
    def feeder(ctl):
        ctl.press()
        _speech(ctl)
        ctl.wait_processed()
        _speech(ctl, blocks=40)           # background audio AFTER the utterance: must be ignored
    seen = _harness(monkeypatch, dict(BASE), feeder)
    assert seen["texts"] == ["hi there"]
    assert any("Listening - speak now" in m for m in seen["logs"])


def test_each_utterance_needs_its_own_press(monkeypatch):
    def feeder(ctl):
        ctl.press(); _speech(ctl); ctl.wait_processed()
        ctl.press(); _speech(ctl); time.sleep(0.4)
    seen = _harness(monkeypatch, dict(BASE), feeder)
    assert seen["texts"] == ["hi there", "hi there"]


def test_window_closes_after_idle_and_late_speech_is_ignored(monkeypatch):
    def feeder(ctl):
        ctl.press()
        time.sleep(0.9)                   # nobody speaks; ptt_idle_secs is 0.3
        _speech(ctl, blocks=30)
    seen = _harness(monkeypatch, {**BASE, "ptt_idle_secs": 0.3}, feeder)
    assert seen["texts"] == []
    assert any("window closed" in m for m in seen["logs"])


def test_window_stays_open_while_the_user_is_mid_sentence(monkeypatch):
    """Speech lasting longer than ptt_idle_secs must not be cut off."""
    def feeder(ctl):
        ctl.press()
        _speech(ctl, blocks=14, pause=0.07)    # ~1s of speech, idle limit is 0.4s
        time.sleep(0.4)
    seen = _harness(monkeypatch, {**BASE, "ptt_idle_secs": 0.4}, feeder)
    assert seen["texts"] == ["hi there"]


def test_hotkey_failure_falls_back_to_always_listening_with_a_warning(monkeypatch):
    def feeder(ctl):
        _speech(ctl)
        time.sleep(0.4)
    seen = _harness(monkeypatch, dict(BASE), feeder, register_ok=False)
    assert seen["texts"] == ["hi there"]            # not deaf
    warn = [m for m in seen["logs"] if m.startswith("WRN") and "always-listening" in m]
    assert warn and "WILL be transcribed" in warn[0] and "boom" in warn[0]


def test_custom_hotkey_from_config(monkeypatch):
    def feeder(ctl):
        time.sleep(0.2)
    seen = _harness(monkeypatch, {**BASE, "ptt_hotkey": "f9"}, feeder)
    assert seen["hotkey"] == "f9"
    assert any("press f9" in m for m in seen["logs"])


def test_bad_idle_value_does_not_crash(monkeypatch):
    def feeder(ctl):
        ctl.press(); _speech(ctl); time.sleep(0.4)
    seen = _harness(monkeypatch, {**BASE, "ptt_idle_secs": "soon"}, feeder)
    assert seen["texts"] == ["hi there"]


def test_register_push_to_talk_never_raises_without_the_keyboard_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyboard", None)     # makes `import keyboard` raise ImportError
    ok, unregister, why = lm.register_push_to_talk("ctrl+alt+space", lambda: None)
    assert ok is False and "ctrl+alt+space" in why
    unregister()                                            # harmless no-op


def test_register_push_to_talk_registers_and_unregisters(monkeypatch):
    calls = []
    fake = types.ModuleType("keyboard")
    fake.add_hotkey = lambda hk, cb, suppress=False: calls.append(("add", hk, suppress)) or "H"
    fake.remove_hotkey = lambda h: calls.append(("remove", h))
    monkeypatch.setitem(sys.modules, "keyboard", fake)
    ok, unregister, why = lm.register_push_to_talk("f9", lambda: None)
    assert ok and why == ""
    unregister()
    assert calls == [("add", "f9", False), ("remove", "H")]
