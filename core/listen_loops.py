"""core/listen_loops.py — AetherLocal's microphone/STT background loops.

Extracted out of main.py (same mechanical-extraction pass as
core/tool_declarations.py, core/conversation_core.py, core/tool_dispatch.py,
core/chat_commands.py, and core/prompt_builder.py -- main.py was, and still
is, the largest oversized file per HEALTH_REPORT.md).

_VADBuffer, SAMPLE_RATE_IN, BLOCK_SIZE, and CHANNELS moved here too since
they're used nowhere else in the codebase -- confirmed via a full-repo grep
before moving, the same check done for every extraction in this pass.

`sd` (sounddevice) and `log_crash` are NOT imported at module level here.
main.py imports sounddevice defensively (it can be entirely unavailable on
a headless server with no PortAudio, in which case main.py sets `sd = None`
and mic-based STT simply isn't used) and log_crash has its own no-op
fallback defined in main.py's V4_OK import guard. Importing either of those
directly here would either duplicate that handling (and risk drifting out
of sync with it) or reintroduce it a second time. Instead this mixin's
methods import them lazily from main at call time -- the same
"lazy: avoids circular import at module load time" pattern already used
throughout core/conversation_core.py and core/tool_dispatch.py for exactly
this kind of main.py-owned, headless-sensitive state.
"""
from __future__ import annotations

import collections
import queue
import threading
import time
import traceback

import numpy as np

SAMPLE_RATE_IN = 16_000
BLOCK_SIZE     = 1_024
CHANNELS       = 1


class _VADBuffer:
    def __init__(
        self,
        sample_rate:    int   = 16_000,
        silence_sec:    float = 0.7,
        speech_thresh:  float = 0.008,
        silence_thresh: float = 0.004,
        min_speech_sec: float = 0.3,
        max_speech_sec: float = 30.0,
        preroll_sec:    float = 0.40,
    ):
        self._sr            = sample_rate
        self._sil_n         = int(silence_sec * sample_rate)
        self._speech_thresh = speech_thresh
        self._sil_thresh    = silence_thresh
        self._min_n         = int(min_speech_sec * sample_rate)
        self._max_n         = int(max_speech_sec * sample_rate)
        self._buf:          list[np.ndarray] = []
        # Audio from just BEFORE the level crossed the threshold. Without it the first
        # soft consonants of every sentence are lost ("hat time is it").
        self._pre:          collections.deque = collections.deque()
        self._pre_n         = 0
        self._pre_max       = int(preroll_sec * sample_rate)
        self._lead_n        = 0
        self._in_spch       = False
        self._sil_cnt       = 0

    @property
    def in_speech(self) -> bool:
        """True while an utterance is being captured (used to keep a push-to-talk
        window open so it can't close on someone mid-sentence)."""
        return self._in_spch

    def process(self, chunk: np.ndarray) -> np.ndarray | None:
        rms     = float(np.sqrt(np.mean(chunk ** 2)))
        total_n = sum(len(c) for c in self._buf)
        if rms > self._speech_thresh:
            if not self._in_spch:
                self._buf = list(self._pre)
                self._lead_n = self._pre_n
                self._pre.clear()
                self._pre_n = 0
                total_n = self._lead_n
            self._in_spch = True
            self._sil_cnt = 0
            self._buf.append(chunk.copy())
        elif not self._in_spch:
            self._pre.append(chunk.copy())
            self._pre_n += len(chunk)
            while self._pre_n > self._pre_max and self._pre:
                self._pre_n -= len(self._pre.popleft())
        elif self._in_spch:
            self._buf.append(chunk.copy())
            if rms < self._sil_thresh:
                self._sil_cnt += len(chunk)
            if self._sil_cnt >= self._sil_n or total_n >= self._max_n:
                audio         = np.concatenate(self._buf)
                lead          = self._lead_n
                self._buf     = []
                self._lead_n  = 0
                self._in_spch = False
                self._sil_cnt = 0
                if len(audio) - lead >= self._min_n:   # the minimum is about real speech, not pre-roll
                    return audio
        return None


class ListenLoopsMixin:
    _barge_in_busy = False

    def _request_barge_in(self) -> None:
        """Interrupt TTS from the microphone callback WITHOUT doing the work there.

        Stopping playback (sd.stop, draining queues, touching the UI) inside a PortAudio
        callback can deadlock or crash the audio driver, and blocks the capture stream.
        The callback only hands the request to a short-lived thread; repeat triggers while
        one is already running are ignored."""
        if self._barge_in_busy:
            return
        self._barge_in_busy = True

        def _run():
            try:
                self._interrupt_speaking()
            except Exception as e:      # noqa: BLE001 - an interrupt must never take the app down
                print(f"[Barge-in] interrupt failed: {e}")
            finally:
                self._barge_in_busy = False

        threading.Thread(target=_run, name="aether-barge-in", daemon=True).start()

    """Mixed into AetherLocal (see main.py). Methods assume the usual
    AetherLocal instance attributes: self.ui, self._stt, self._speaking(_lock),
    self._barge_in_*, self._dashboard, and self._process_message (provided
    by ConversationCoreMixin)."""

    def _listen_whisper(self) -> None:
        from main import sd, log_crash  # lazy: avoids circular import at module load time
        from core.llm_client import get_config_flag
        from core.echo import get_guard
        from core.wake_word import WakeGate, WakeWordDetector, is_ready as _wake_ready
        from core.llm_client import _load_config
        from core import listen_mode as _lm

        vad = _VADBuffer()
        q: queue.Queue = queue.Queue(maxsize=200)

        # ── Echo guard (ported from MARK LV; config echo_guard, default on) ──
        # Replaces the old "loud audio = barge-in" test, which on speakers made
        # Aether interrupt itself, and drops our own voice's tail after a reply
        # so it stops answering its last sentence.
        use_echo = get_config_flag("echo_guard", True)
        guard = get_guard()
        state = {"was_speaking": False, "tail_until": 0.0}
        TAIL_S = 0.45  # device output latency + room margin

        # ── Listen mode (roadmap #95): which gate sits in front of the VAD ──
        # config listen_mode = auto (default) | always | wake | ptt. Without a gate,
        # loud background audio (TikTok, TV) is treated as speech and transcribed.
        # See core/listen_mode.py; every fallback ends in "always", never in silence.
        cfg = _load_config()
        mode, mode_note = _lm.resolve_listen_mode(
            cfg, wake_ready=_wake_ready(), ptt_available=_lm.keyboard_available())
        if mode_note:
            self.ui.write_log(f"WRN: {mode_note}")

        gate = WakeGate(enabled=False)
        detector = None
        unregister_ptt = None
        if mode == "wake":
            gate = WakeGate(enabled=True)
            detector = WakeWordDetector(
                on_detect=lambda: gate.wake() and self.ui.write_log("SYS: Awake - wake word."),
                logger=lambda m: print(f"[Wake] {m}"))
            if not detector.start():
                detector = None
                gate = WakeGate(enabled=False)
                mode = "always"
                self.ui.write_log(f"WRN: {_lm._WAKE_NOT_READY}")
            else:
                self.ui.write_log("SYS: Wake word on - say 'Hey Jarvis'. Sleeping.")
        elif mode == "ptt":
            hotkey = str(cfg.get("ptt_shortcut") or _lm.DEFAULT_PTT_HOTKEY)
            try:
                idle_s = float(cfg.get("ptt_idle_secs", _lm.DEFAULT_PTT_IDLE_S))
            except (TypeError, ValueError):
                idle_s = _lm.DEFAULT_PTT_IDLE_S
            ptt_gate = _lm.PushToTalkGate(idle_s=idle_s)
            ok, unregister_ptt, why = _lm.register_push_to_talk(
                hotkey,
                lambda: ptt_gate.wake() and self.ui.write_log("SYS: Listening - speak now."))
            if ok:
                gate = ptt_gate
                self.ui.write_log(f"SYS: Push-to-talk on - press {hotkey} and speak. "
                                  f"Background audio is ignored.")
            else:
                mode = "always"
                self.ui.write_log(f"WRN: {why} - staying always-listening, so loud "
                                  f"background audio WILL be transcribed")

        def callback(indata, frames, time_info, status):
            if self.ui.muted:
                self._barge_in_count = 0
                return
            with self._speaking_lock:
                is_speaking = self._speaking
            now = time.monotonic()
            mono = indata.reshape(-1)

            if is_speaking:
                state["was_speaking"] = True
                if use_echo:
                    from core.echo import pcm_level
                    if guard.is_user_speech(mono * 32768.0, SAMPLE_RATE_IN, pcm_level(mono)):
                        self._barge_in_count += 1
                    else:
                        self._barge_in_count = max(0, self._barge_in_count - 1)
                    need = guard.required_blocks
                else:
                    rms = float(np.sqrt(np.mean(mono ** 2)))
                    self._barge_in_count = (self._barge_in_count + 1) if rms > self._BARGE_IN_THRESHOLD \
                        else max(0, self._barge_in_count - 1)
                    need = self._BARGE_IN_FRAMES
                if self._barge_in_count >= need:
                    self._barge_in_count = 0
                    self._request_barge_in()
                return

            self._barge_in_count = 0
            if state["was_speaking"]:
                state["was_speaking"] = False
                state["tail_until"] = now + TAIL_S

            if use_echo and now < state["tail_until"]:
                from core.echo import pcm_level
                if not guard.is_user_speech(mono * 32768.0, SAMPLE_RATE_IN, pcm_level(mono)):
                    return  # our own voice still leaving the speakers
                state["tail_until"] = 0.0  # a real voice ends the tail early
            elif use_echo:
                guard.reset()

            if gate.enabled and not gate.awake:
                if detector is not None:
                    detector.feed((mono * 32768.0).astype(np.int16))
                return

            try:
                q.put_nowait(indata.copy())
            except queue.Full:
                pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="float32",
                blocksize=BLOCK_SIZE,
                callback=callback,
            ):
                self.ui.write_log("SYS: Mic active (Whisper STT).")
                while True:
                    try:
                        if gate.check_sleep(speaking=self._speaking):
                            if mode == "ptt":
                                self.ui.write_log("SYS: Push-to-talk window closed - press the hotkey to talk.")
                            else:
                                self.ui.write_log("SYS: Sleeping - no speech for 2 minutes. Say 'Hey Jarvis'.")
                        chunk = q.get(timeout=0.1)
                        audio = vad.process(chunk.flatten())
                        if vad.in_speech:
                            gate.touch()    # PTT: never close the window on someone mid-sentence
                        if audio is not None:
                            gate.touch()
                            gate.utterance_done()   # PTT: close now, so audio during STT/LLM isn't queued
                            self.ui.set_state("THINKING")
                            text = self._stt.transcribe(audio)
                            if text.strip():
                                self._process_message(text)
                    except queue.Empty:
                        pass
        except Exception as e:
            log_crash(e, context="STT-Whisper")
            print(f"[STT-Whisper] Mic error: {e}")
            traceback.print_exc()
        finally:
            if unregister_ptt is not None:
                unregister_ptt()    # don't leave a global hotkey hook behind

    def _listen_vosk(self) -> None:
        from main import sd, log_crash  # lazy: avoids circular import at module load time
        q: queue.Queue = queue.Queue(maxsize=200)

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                is_speaking = self._speaking
            if not is_speaking and not self.ui.muted:
                try:
                    q.put_nowait(indata.copy())
                except queue.Full:
                    pass

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE_IN,
                channels=CHANNELS,
                dtype="int16",
                blocksize=4096,
                callback=callback,
            ):
                self.ui.write_log("SYS: Mic active (Vosk STT).")
                while True:
                    try:
                        chunk = q.get(timeout=0.1)
                        text, is_final = self._stt.process_chunk(chunk.tobytes())
                        if is_final and text.strip():
                            self._process_message(text)
                    except queue.Empty:
                        pass
        except Exception as e:
            log_crash(e, context="STT-Vosk")
            print(f"[STT-Vosk] Mic error: {e}")
            traceback.print_exc()

    def _phone_audio_stt_loop(self) -> None:
        """Drains phone-mic PCM frames queued by the dashboard's
        /ws/phone-audio websocket and runs them through the same
        VAD-buffered Whisper pipeline as the local mic threads.

        Previously nothing drained `self._dashboard._phone_audio_queue` —
        frames were accepted and queued, then silently dropped once the
        queue filled. The dashboard client (app.html doMic()) already
        resamples to 16 kHz mono and sends signed 16-bit PCM, matching what
        `_stt.transcribe()` / `_VADBuffer` expect once converted to float32.
        Uses its own `_VADBuffer` instance — sharing one with the local-mic
        loop would interleave two independent speakers' buffered audio.
        """
        from main import log_crash  # lazy: avoids circular import at module load time
        vad = _VADBuffer()
        while True:
            if self._dashboard is None:
                time.sleep(1.0)
                continue
            if self._stt is None:
                time.sleep(0.5)
                continue
            try:
                frame = self._dashboard._phone_audio_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            except Exception:
                time.sleep(0.5)
                continue
            try:
                data = frame.get("data") if isinstance(frame, dict) else frame
                pcm16 = np.frombuffer(data, dtype="<i2")
                chunk = pcm16.astype(np.float32) / 32768.0
                audio = vad.process(chunk)
                if audio is not None:
                    self.ui.set_state("THINKING")
                    text = self._stt.transcribe(audio)
                    if text.strip():
                        self._process_message(text)
            except Exception as e:
                log_crash(e, context="Phone-Audio-STT")

