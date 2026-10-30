"""
Speech-to-Text engines for Aether.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
import json
import time
import traceback

import numpy as np

from core import trace_log as _trace


class WhisperSTT:
    """Offline transcription using faster-whisper."""

    def __init__(self, model_name: str = "small", language: str | None = None):
        import os
        from faster_whisper import WhisperModel
        print(f"[STT] Loading Whisper '{model_name}'…")
        try:
            import torch
            device  = "cuda" if torch.cuda.is_available() else "cpu"
            compute = "float16" if device == "cuda" else "int8"
        except Exception:
            device, compute = "cpu", "int8"

        try:
            self._model = WhisperModel(model_name, device=device, compute_type=compute)
        except Exception as _first_err:
            # Offline flag set but model not cached yet → download once, then offline forever
            _e = str(_first_err).lower()
            if any(k in _e for k in ("offline", "not found", "cache", "localentry", "does not exist")):
                print(f"[STT] '{model_name}' not cached — downloading (internet required for first run)…")
                from core.hf_offline import allow_online
                with allow_online():
                    self._model = WhisperModel(model_name, device=device, compute_type=compute)
            else:
                raise

        self._language = None if (not language or language.strip().lower() == "auto") else language.strip().lower()
        print(f"[STT] Whisper '{model_name}' ready ({device})")

    # Whisper's own "this is not speech / not confident" signals. Background video audio,
    # music and room noise come back as low-confidence segments; real speech does not.
    NO_SPEECH_MAX     = 0.6
    AVG_LOGPROB_MIN   = -1.0
    COMPRESSION_MAX   = 2.4      # highly repetitive output = hallucination loop

    @classmethod
    def keep_segment(cls, seg) -> bool:
        """False for a segment Whisper itself doubts is real speech."""
        no_speech = getattr(seg, "no_speech_prob", 0.0) or 0.0
        logprob   = getattr(seg, "avg_logprob", 0.0) or 0.0
        compress  = getattr(seg, "compression_ratio", 0.0) or 0.0
        if no_speech > cls.NO_SPEECH_MAX and logprob < cls.AVG_LOGPROB_MIN:
            return False
        if compress > cls.COMPRESSION_MAX:
            return False
        return logprob >= -1.6        # very low confidence even when it thinks it is speech

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a float32 mono 16 kHz numpy array. Returns transcript string.
        Segments Whisper is not confident are speech are dropped, so a video playing in
        the background does not turn into prompts."""
        try:
            from core import config_home
            cfg = config_home.load_config()
            # Short clips are cheap, so use a wider beam: it is what stops dropped letters.
            seconds = len(audio) / 16_000.0
            beam = int(cfg.get("stt_beam_size", 5 if seconds <= 12 else 2))
            kwargs = dict(
                language=self._language,
                beam_size=max(1, beam),
                condition_on_previous_text=False,  # no hallucinations, faster
                vad_filter=True,
                vad_parameters={
                    # lower threshold + padding: the default clipped soft word starts/ends
                    "threshold": float(cfg.get("stt_vad_threshold", 0.35)),
                    "min_silence_duration_ms": 500,
                    "speech_pad_ms": 400,
                },
            )
            _t0 = time.monotonic()
            try:
                segments, info = self._model.transcribe(audio, hotwords="Aether", **kwargs)
            except TypeError:        # older faster-whisper without hotwords
                segments, info = self._model.transcribe(audio, **kwargs)
            # Record EVERY segment with the numbers keep_segment() judged it on, so a
            # dropped (or wrongly kept) phrase can be explained after the fact.
            kept, seg_log = [], []
            for s in segments:
                ok = self.keep_segment(s)
                if ok:
                    kept.append(s.text)
                seg_log.append({
                    "text": s.text, "kept": ok,
                    "start": round(getattr(s, "start", 0.0) or 0.0, 2),
                    "end": round(getattr(s, "end", 0.0) or 0.0, 2),
                    "avg_logprob": round(getattr(s, "avg_logprob", 0.0) or 0.0, 3),
                    "no_speech_prob": round(getattr(s, "no_speech_prob", 0.0) or 0.0, 3),
                    "compression_ratio": round(getattr(s, "compression_ratio", 0.0) or 0.0, 2),
                })
            text = " ".join(kept).strip()
            _trace.event(
                "stt_transcribe", engine="faster-whisper", audio_s=round(seconds, 2),
                audio_rms=round(float(np.sqrt(np.mean(np.square(audio)))), 5) if len(audio) else 0.0,
                audio_peak=round(float(np.max(np.abs(audio))), 4) if len(audio) else 0.0,
                beam_size=beam, language=self._language,
                detected_language=getattr(info, "language", None),
                language_probability=round(getattr(info, "language_probability", 0.0) or 0.0, 3),
                elapsed_ms=round((time.monotonic() - _t0) * 1000, 1),
                segments=seg_log, dropped=sum(1 for x in seg_log if not x["kept"]), text=text)
            return text
        except Exception as e:
            _trace.event("stt_error", error=f"{type(e).__name__}: {e}",
                         traceback=traceback.format_exc())
            print(f"[STT] Transcription error: {e}")
            raise


class VoskSTT:
    """Streaming transcription using Vosk."""

    def __init__(self, model_path: str | None = None, language: str = "en-us"):
        from vosk import Model, KaldiRecognizer
        print("[STT] Loading Vosk model…")
        if model_path:
            model = Model(model_path)
        else:
            lang  = language.strip().lower() if language and language.strip().lower() != "auto" else "en-us"
            model = Model(lang=lang)
        self._rec = KaldiRecognizer(model, 16000)
        print("[STT] Vosk ready.")

    def process_chunk(self, audio_bytes: bytes) -> tuple[str, bool]:
        """Feed raw int16 LE PCM bytes. Returns (text, is_final)."""
        if self._rec.AcceptWaveform(audio_bytes):
            result = json.loads(self._rec.Result())
            return result.get("text", ""), True
        partial = json.loads(self._rec.PartialResult())
        return partial.get("partial", ""), False
