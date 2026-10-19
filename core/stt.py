"""
Speech-to-Text engines for Aether.

Whisper  – offline transcription via faster-whisper (VAD-buffered)
Vosk     – offline streaming transcription (lighter)
"""
import json
import numpy as np


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
            try:
                segments, _ = self._model.transcribe(audio, hotwords="Aether", **kwargs)
            except TypeError:        # older faster-whisper without hotwords
                segments, _ = self._model.transcribe(audio, **kwargs)
            kept = [s.text for s in segments if self.keep_segment(s)]
            return " ".join(kept).strip()
        except Exception as e:
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
