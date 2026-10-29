"""Tests for the MARK LV ports: EchoGuard/OutputTap, WakeGate, Gemini opt-in route."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pytest

from core import echo, tier_router
from core.tier_router import Tier
from core.wake_word import WakeGate

SR = 16000
BLK = 1024


def _voice(seed, shape, n=SR * 6):
    """Band-shaped noise: `shape` weights 9 speech bands, so two seeds/shapes sound different."""
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    edges = echo._BAND_EDGES
    gain = np.full_like(f, 0.02)
    for i, w in enumerate(shape):
        gain[(f >= edges[i]) & (f < edges[i + 1])] = w
    x = np.fft.irfft(spec * gain, n=n).astype(np.float32)
    return x / (np.abs(x).max() + 1e-9) * 0.6


OURS = _voice(1, [1, 2, 3, 1, .3, .2, .1, .1])          # what we "play"
THEIRS = _voice(7, [.1, .1, .3, .3, 1, 3, 3, 1])         # a different voice


def _run(mic_fn, blocks=60):
    g = echo.EchoGuard()
    t = 1000.0
    hits = []
    for i in range(blocks):
        sl = OURS[i * BLK:(i + 1) * BLK]
        g.note_output(sl * 32768.0, SR, echo.pcm_level(sl), when=t)
        mic = mic_fn(i, sl)
        hits.append(g.is_user_speech(mic * 32768.0, SR, echo.pcm_level(mic), when=t))
        t += BLK / SR
    return g, hits


def test_pure_echo_is_not_user_speech():
    _, hits = _run(lambda i, sl: 0.4 * sl)
    assert sum(hits[16:]) <= 2          # after warmup, essentially never a "voice"


def test_different_voice_over_echo_is_detected():
    def mic(i, sl):
        return 0.4 * sl + (THEIRS[i * BLK:(i + 1) * BLK] if i >= 30 else 0)
    _, hits = _run(mic)
    assert sum(hits[34:]) >= 15


def test_silent_mic_never_triggers():
    _, hits = _run(lambda i, sl: np.zeros_like(sl))
    assert not any(hits)


def test_pcm_level_scale():
    assert echo.pcm_level(np.zeros(100, np.float32)) == 0.0
    assert echo.pcm_level(np.full(100, 0.5, np.float32)) == 1.0
    assert 0.0 < echo.pcm_level(np.full(100, 0.03, np.float32)) < 1.0


def test_output_tap_feeds_guard_in_order():
    class Rec:
        def __init__(self): self.calls = []
        def note_output(self, pcm, sr, level, when=None): self.calls.append(when)
    rec = Rec()
    tap = echo.OutputTap(guard=rec, latency_s=0.0)
    tap.start(OURS[:SR // 2], SR)       # 0.5 s of audio -> ~8 slices
    time.sleep(0.8)
    assert len(rec.calls) >= 7
    assert rec.calls == sorted(rec.calls)


def test_output_tap_stop_cancels():
    class Rec:
        def __init__(self): self.n = 0
        def note_output(self, *a, **k): self.n += 1
    rec = Rec()
    tap = echo.OutputTap(guard=rec, latency_s=0.0)
    tap.start(OURS[:SR * 3], SR)
    time.sleep(0.15)
    tap.stop()
    n = rec.n
    time.sleep(0.4)
    assert rec.n <= n + 1


def test_tts_playback_taps_output_and_stop(monkeypatch):
    import types
    calls = {"play": 0, "stop": 0}
    fake_sd = types.SimpleNamespace(play=lambda *a, **k: calls.__setitem__("play", calls["play"] + 1),
                                    wait=lambda: None,
                                    stop=lambda: calls.__setitem__("stop", calls["stop"] + 1))
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)
    sys.modules.pop("core.tts", None)
    import core.tts as tts
    tapped = []
    monkeypatch.setattr(tts, "_tap_output", lambda arr, sr: tapped.append((len(arr), sr)))
    stopped = []
    monkeypatch.setattr(tts, "_stop_tap", lambda: stopped.append(1))
    tts._play_np(OURS[:1600], 16000)
    assert tapped == [(1600, 16000)] and calls["play"] == 1
    tts.TTSPlayer(engine=None).stop()
    assert stopped and calls["stop"] == 1
    sys.modules.pop("core.tts", None)


# ---- WakeGate
class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_wake_gate_disabled_always_awake():
    g = WakeGate(enabled=False)
    assert g.awake and not g.wake() and not g.check_sleep()


def test_wake_gate_cycle():
    c = Clock()
    g = WakeGate(enabled=True, timeout=120, clock=c)
    assert not g.awake
    assert g.wake() and g.awake
    c.t = 100; assert not g.check_sleep()
    g.touch(); c.t = 215; assert not g.check_sleep()      # touch restarted the window
    c.t = 230; assert g.check_sleep() and not g.awake


def test_wake_gate_does_not_sleep_while_speaking():
    c = Clock()
    g = WakeGate(enabled=True, timeout=10, clock=c)
    g.wake(); c.t = 500
    assert not g.check_sleep(speaking=True) and g.awake


# ---- Gemini opt-in
def _w(msg, **kw):
    base = dict(flag=True, provider="ollama", has_key=True, tier=Tier.EVERYDAY)
    base.update(kw)
    return tier_router.wants_gemini_for_tools(msg, **base)


def test_gemini_opt_in_default_off():
    assert not _w("Turn the volume down", flag=False)


def test_gemini_opt_in_requires_key_and_tools_and_local_primary():
    assert _w("Turn the volume down a little.")
    assert not _w("Turn the volume down", has_key=False)
    assert not _w("Turn the volume down", provider="gemini")
    assert not _w("Turn the volume down", tier=Tier.QUICK)
    assert not _w("Tell me a joke about cats.")


def test_config_flag_parsing(monkeypatch):
    from core import llm_client
    monkeypatch.setattr(llm_client, "_load_config", lambda: {"a": "true", "b": "no", "c": True})
    assert llm_client.get_config_flag("a") and not llm_client.get_config_flag("b")
    assert llm_client.get_config_flag("c") and llm_client.get_config_flag("zz", True)


# ---- _listen_whisper end-to-end with a fake mic ----------------------------
class _Done(Exception):
    pass


def _make_listener(monkeypatch, scripted, cfg):
    """scripted: list of (speaking_flag, block) fed to the real mic callback."""
    import threading
    import types
    from core import llm_client, listen_loops

    monkeypatch.setattr(llm_client, "_load_config", lambda: cfg)
    seen = {"texts": [], "interrupts": 0}

    class FakeStream:
        def __init__(self, **kw): self.cb = kw["callback"]
        def __enter__(self):
            for speaking, blk in scripted:
                obj.set_speaking(speaking)
                self.cb(blk.reshape(-1, 1).astype(np.float32), len(blk), None, None)
            obj._finished = True
            return self
        def __exit__(self, *a): return False

    fake_main = types.SimpleNamespace(sd=types.SimpleNamespace(InputStream=FakeStream),
                                      log_crash=lambda e, context="": seen.setdefault("crash", repr(e)))
    monkeypatch.setitem(sys.modules, "main", fake_main)

    class Obj(listen_loops.ListenLoopsMixin):
        def __init__(self):
            self.ui = types.SimpleNamespace(muted=False, write_log=lambda m: seen.setdefault("logs", []).append(m),
                                            set_state=lambda s: None)
            self._speak_flag = False
            self._finished = False
            self._reads = 0
            self._speaking_lock = threading.Lock()
            self._barge_in_count = 0
            self._BARGE_IN_THRESHOLD = 0.02
            self._BARGE_IN_FRAMES = 6
            self._stt = types.SimpleNamespace(transcribe=lambda a: "hi there")
        @property
        def _speaking(self):
            if self._finished:
                self._reads += 1
                if self._reads > 25:      # loop has drained everything: stop the test
                    raise _Done()
            return self._speak_flag
        @_speaking.setter
        def _speaking(self, v): self._speak_flag = v
        def set_speaking(self, v): self._speak_flag = v
        def _interrupt_speaking(self): seen["interrupts"] += 1; self._speak_flag = False
        def _process_message(self, t): seen["texts"].append(t); raise _Done()
    obj = Obj()
    return obj, seen


def _loud(n=1024, amp=0.3):
    t = np.arange(n) / SR
    return (amp * np.sin(2 * np.pi * 300 * t) + 0.1 * np.random.default_rng(0).standard_normal(n)).astype(np.float32)


def test_listener_queues_speech_when_idle(monkeypatch):
    blocks = [(False, _loud()) for _ in range(12)] + [(False, np.zeros(1024, np.float32)) for _ in range(20)]
    obj, seen = _make_listener(monkeypatch, blocks, {"echo_guard": True})
    obj._listen_whisper()
    assert seen["texts"] == ["hi there"]


def test_listener_wake_gate_blocks_audio_until_awake(monkeypatch):
    from core import wake_word
    monkeypatch.setattr(wake_word, "is_ready", lambda: False)   # model missing -> stay always-awake
    blocks = [(False, _loud()) for _ in range(12)] + [(False, np.zeros(1024, np.float32)) for _ in range(20)]
    obj, seen = _make_listener(monkeypatch, blocks, {"wake_word_enabled": True})
    obj._listen_whisper()
    assert seen["texts"] == ["hi there"]
    assert any("not ready" in m for m in seen["logs"])


def test_wake_word_is_on_by_default_and_warns_when_not_ready(monkeypatch):
    """Roadmap #95: no config at all -> wake gate requested; model missing -> loud warning."""
    from core import wake_word
    monkeypatch.setattr(wake_word, "is_ready", lambda: False)
    blocks = [(False, _loud()) for _ in range(12)] + [(False, np.zeros(1024, np.float32)) for _ in range(20)]
    obj, seen = _make_listener(monkeypatch, blocks, {})
    obj._listen_whisper()
    assert any("not ready" in m and "background audio" in m for m in seen["logs"])


def test_wake_word_can_be_disabled_explicitly(monkeypatch):
    from core import wake_word
    monkeypatch.setattr(wake_word, "is_ready", lambda: False)
    blocks = [(False, _loud()) for _ in range(12)] + [(False, np.zeros(1024, np.float32)) for _ in range(20)]
    obj, seen = _make_listener(monkeypatch, blocks, {"wake_word_enabled": False})
    obj._listen_whisper()
    assert seen["texts"] == ["hi there"]
    assert not any("openwakeword" in m for m in seen["logs"])


def test_default_gate_keeps_background_speech_out_when_model_ready(monkeypatch):
    """With the detector available and never triggered, loud background audio is not transcribed."""
    from core import wake_word

    class Silent:
        def __init__(self, **kw): pass
        def start(self): return True
        def feed(self, pcm): pass
    monkeypatch.setattr(wake_word, "is_ready", lambda: True)
    monkeypatch.setattr(wake_word, "WakeWordDetector", Silent)
    blocks = [(False, _loud()) for _ in range(12)] + [(False, np.zeros(1024, np.float32)) for _ in range(30)]
    obj, seen = _make_listener(monkeypatch, blocks, {})
    obj._finished = True
    try:
        obj._listen_whisper()
    except Exception:
        pass
    assert seen["texts"] == []
    assert any("Hey Jarvis" in m for m in seen["logs"])


def test_listener_drops_own_voice_during_tail(monkeypatch):
    """Playback ends, then the mic still hears our own last words: must not be transcribed."""
    g = echo.get_guard()
    g.__init__()  # fresh learning state
    t0 = time.monotonic()
    # prime the guard as if we had been playing OURS for a second
    for i in range(16):
        sl = OURS[i * BLK:(i + 1) * BLK]
        g.note_output(sl * 32768.0, SR, echo.pcm_level(sl), when=t0 + i * BLK / SR - 1.0)
    script = [(True, 0.4 * OURS[i * BLK:(i + 1) * BLK]) for i in range(20)]
    script += [(False, 0.4 * OURS[(20 + i) * BLK:(21 + i) * BLK]) for i in range(5)]   # tail: pure echo
    script += [(False, np.zeros(1024, np.float32)) for _ in range(25)]
    obj, seen = _make_listener(monkeypatch, script, {"echo_guard": True})
    obj._listen_whisper()
    assert seen["texts"] == []
    assert seen.get("crash") == "_Done()"      # only the test's own exit hook ended the loop


def test_listener_control_without_guard_hears_the_echo(monkeypatch):
    """Same script with echo_guard off: our own tail IS transcribed, proving the test above bites."""
    script = [(True, 0.4 * OURS[i * BLK:(i + 1) * BLK]) for i in range(3)]
    script += [(False, 0.4 * OURS[(20 + i) * BLK:(21 + i) * BLK]) for i in range(8)]
    script += [(False, np.zeros(1024, np.float32)) for _ in range(25)]
    obj, seen = _make_listener(monkeypatch, script, {"echo_guard": False})
    obj._listen_whisper()
    assert seen["texts"] == ["hi there"]


def test_gemini_health_check_url(monkeypatch):
    from core import llm_client
    seen = {}
    monkeypatch.setattr(llm_client, "get_llm_settings", lambda: (llm_client.GEMINI_BASE_URL, "gemini-2.5-flash"))
    monkeypatch.setattr(llm_client, "get_llm_provider", lambda: "gemini")
    monkeypatch.setattr(llm_client, "_load_config", lambda: {"gemini_api_key": "k"})
    class R: status_code = 200
    monkeypatch.setattr(llm_client.requests, "get", lambda u, **kw: seen.setdefault("u", u) and R())
    assert llm_client.ensure_ollama_running() is True
    assert seen["u"] == llm_client.GEMINI_BASE_URL + "/models"
