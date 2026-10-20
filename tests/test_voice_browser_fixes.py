"""Voice + browser regressions from real-hardware logs (clipped words, background audio
transcribed, barge-in crash risk, extra blank browser tabs)."""
import asyncio
import threading
import time
from types import SimpleNamespace

import numpy as np

from core.listen_loops import ListenLoopsMixin, _BargeInAudioBuffer, _VADBuffer
from core.stt import WhisperSTT


# ── VAD pre-roll: the start of the first word must survive ────────────────────────
def _block(level, n=1024):
    return np.full(n, level, dtype=np.float32)


def test_vad_keeps_audio_from_just_before_speech_starts():
    vad = _VADBuffer(silence_sec=0.2, min_speech_sec=0.1)
    out = None
    for _ in range(10):                      # quiet room
        assert vad.process(_block(0.001)) is None
    soft_onset = _block(0.006)               # below the speech threshold: a soft "wh"
    vad.process(soft_onset)
    for _ in range(6):                       # the loud part of the word
        vad.process(_block(0.05))
    for _ in range(40):
        got = vad.process(_block(0.001))
        if got is not None:
            out = got
    assert out is not None
    assert np.isclose(out, 0.006).any()      # the soft onset made it into the clip
    assert len(out) > 7 * 1024               # ...along with the loud part


def test_vad_min_length_ignores_preroll_so_clicks_are_still_dropped():
    vad = _VADBuffer(silence_sec=0.1, min_speech_sec=0.5)
    for _ in range(10):
        vad.process(_block(0.001))
    vad.process(_block(0.05))                # one short click
    out = None
    for _ in range(20):
        got = vad.process(_block(0.001))
        if got is not None:
            out = got
    assert out is None


def test_confirmed_barge_in_preserves_prompt_onset_and_resets_after_playback():
    audio = _BargeInAudioBuffer(max_blocks=4)
    echo = _block(0.01)
    onset = _block(0.03)
    follow_up = _block(0.04)

    audio.observe(echo, False)
    audio.observe(onset, True)
    audio.observe(follow_up, True)
    captured = audio.confirm()

    assert len(captured) == 2
    assert np.array_equal(captured[0], onset)
    assert np.array_equal(captured[1], follow_up)
    assert audio.capturing is True

    audio.reset()
    assert audio.capturing is False
    assert audio.confirm() == []


# ── STT: Whisper's own doubts discard background-media segments ─────────────────
def _seg(text, no_speech=0.0, logprob=-0.2, compress=1.2):
    return SimpleNamespace(text=text, no_speech_prob=no_speech, avg_logprob=logprob, compression_ratio=compress)


class _FakeModel:
    def __init__(self, segs):
        self.segs, self.kwargs = segs, None

    def transcribe(self, audio, **kw):
        self.kwargs = kw
        return iter(self.segs), SimpleNamespace(language_probability=0.34)


def _stt(segs):
    s = object.__new__(WhisperSTT)
    s._model, s._language = _FakeModel(segs), None
    return s


def test_real_speech_is_kept():
    stt = _stt([_seg("what time is it")])
    assert stt.transcribe(np.zeros(16000, dtype=np.float32)) == "what time is it"


def test_low_confidence_background_audio_is_dropped():
    stt = _stt([_seg("subscribe to my channel", no_speech=0.8, logprob=-1.4),
                _seg("la la la la la la", compress=3.1)])
    assert stt.transcribe(np.zeros(16000, dtype=np.float32)) == ""


def test_only_the_doubtful_segment_is_dropped():
    stt = _stt([_seg("open youtube"), _seg("thanks for watching", no_speech=0.9, logprob=-1.3)])
    assert stt.transcribe(np.zeros(16000, dtype=np.float32)) == "open youtube"


def test_transcribe_uses_wide_beam_and_padded_vad():
    stt = _stt([_seg("hi")])
    stt.transcribe(np.zeros(16000, dtype=np.float32))
    kw = stt._model.kwargs
    assert kw["beam_size"] >= 3
    assert kw["vad_parameters"]["speech_pad_ms"] >= 300
    assert kw["vad_parameters"]["threshold"] < 0.5


# ── barge-in must not run inside the audio callback ───────────────────────────────
class _Host(ListenLoopsMixin):
    def __init__(self, delay=0.0):
        self.calls, self.thread_names, self.delay = 0, [], delay

    def _interrupt_speaking(self, reason="barge-in"):
        time.sleep(self.delay)
        self.calls += 1
        self.thread_names.append(threading.current_thread().name)


def test_barge_in_runs_on_its_own_thread_and_returns_immediately():
    host = _Host(delay=0.2)
    start = time.monotonic()
    host._request_barge_in()
    assert time.monotonic() - start < 0.1     # the audio callback is not blocked
    time.sleep(0.4)
    assert host.calls == 1 and host.thread_names == ["aether-barge-in"]


def test_repeated_barge_in_triggers_are_collapsed_while_one_is_running():
    host = _Host(delay=0.2)
    for _ in range(5):
        host._request_barge_in()
    time.sleep(0.5)
    assert host.calls == 1


def test_barge_in_failure_does_not_escape_and_allows_the_next_one():
    host = _Host()

    def boom(reason="barge-in"):
        raise RuntimeError("driver error")

    host._interrupt_speaking = boom
    host._request_barge_in()
    time.sleep(0.2)
    assert host._barge_in_busy is False


# ── browser: reuse the blank tab instead of adding more ──────────────────────────
class _Page:
    def __init__(self, url="about:blank"):
        self.url, self.closed = url, False

    def is_closed(self):
        return self.closed

    async def close(self):
        self.closed = True


class _Ctx:
    def __init__(self, pages):
        self.pages, self.created = list(pages), 0

    async def new_page(self):
        self.created += 1
        p = _Page()
        self.pages.append(p)
        return p


def test_adopt_page_reuses_the_launch_blank_and_closes_extras():
    from actions.browser_control import adopt_page
    a, b, real = _Page(), _Page("chrome://newtab/"), _Page("https://example.com")
    ctx = _Ctx([real, a, b])
    page = asyncio.run(adopt_page(ctx))
    assert page is a and b.closed and not real.closed and ctx.created == 0


def test_adopt_page_creates_one_only_when_no_blank_exists():
    from actions.browser_control import adopt_page
    ctx = _Ctx([_Page("https://example.com")])
    asyncio.run(adopt_page(ctx))
    assert ctx.created == 1


def test_footer_hotkey_label_follows_the_configured_hotkey(monkeypatch):
    import ui
    monkeypatch.setattr(ui._config_home, "load_config", lambda path=None: {"hotkey.toggle": "ctrl+alt+j"})
    assert ui.toggle_hotkey_label() == "Ctrl+Alt+J"
    monkeypatch.setattr(ui._config_home, "load_config", lambda path=None: {})
    assert ui.toggle_hotkey_label() == "Ctrl+Alt+A"


def test_hf_models_load_from_cache_first_and_fall_back_online():
    from memory.hybrid_memory import _load_cached_first
    seen = []

    def cached(name, **kw):
        seen.append(kw)
        return "from-cache"

    assert _load_cached_first(cached, "m") == "from-cache" and seen == [{"local_files_only": True}]

    def not_cached(name, **kw):
        if kw.get("local_files_only"):
            raise OSError("not in cache")
        return "downloaded"

    assert _load_cached_first(not_cached, "m") == "downloaded"
