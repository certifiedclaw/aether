import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def tts(monkeypatch):
    audio = types.ModuleType("sounddevice")
    audio.play = Mock()
    audio.wait = Mock()
    audio.stop = Mock()
    monkeypatch.setitem(sys.modules, "sounddevice", audio)
    import core.tts
    return core.tts


def test_elevenlabs_replaces_voice_ids_from_other_engines(tts):
    engine = tts.ElevenLabsTTSEngine("api-key", "en-US-GuyNeural")
    assert engine.voice_id == tts.ElevenLabsTTSEngine.DEFAULT_VOICE_ID

    engine = tts.ElevenLabsTTSEngine("api-key", "af_heart")
    assert engine.voice_id == tts.ElevenLabsTTSEngine.DEFAULT_VOICE_ID

    engine = tts.ElevenLabsTTSEngine("api-key", "custom-elevenlabs-voice")
    assert engine.voice_id == "custom-elevenlabs-voice"


def test_elevenlabs_missing_key_has_actionable_error(tts):
    engine = tts.ElevenLabsTTSEngine("")
    with pytest.raises(RuntimeError, match="API key is missing"):
        engine.speak("Hello.")


def test_elevenlabs_uses_audio_endpoint_and_surfaces_api_errors(tts, monkeypatch):
    import requests

    response = Mock()
    response.content = b"audio"
    response.raise_for_status.return_value = None
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    play = Mock()
    monkeypatch.setattr(tts, "_play_audio_bytes", play)

    tts.ElevenLabsTTSEngine("api-key", "voice-123").speak("Hello.")

    assert post.call_args.args[0] == (
        "https://api.elevenlabs.io/v1/text-to-speech/voice-123"
    )
    assert post.call_args.kwargs["headers"]["xi-api-key"] == "api-key"
    assert post.call_args.kwargs["headers"]["Accept"] == "audio/mpeg"
    play.assert_called_once_with(b"audio")


def test_tts_player_propagates_synthesis_errors(tts):
    class BrokenEngine:
        def speak(self, _text):
            raise RuntimeError("ElevenLabs rejected the request")

    player = tts.TTSPlayer(BrokenEngine())
    with pytest.raises(RuntimeError, match="rejected the request"):
        player.speak("Hello.")
    assert not player.is_playing
