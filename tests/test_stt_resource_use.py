import builtins
import sys
import types

from core.stt import WhisperSTT


def test_whisper_cuda_detection_uses_ctranslate2_without_importing_torch(monkeypatch):
    seen = {}

    class Model:
        def __init__(self, model_name, *, device, compute_type):
            seen.update(
                model_name=model_name,
                device=device,
                compute_type=compute_type,
            )

    fake_ctranslate2 = types.SimpleNamespace(get_cuda_device_count=lambda: 1)
    fake_faster_whisper = types.SimpleNamespace(WhisperModel=Model)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake_ctranslate2)
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_faster_whisper)

    real_import = builtins.__import__

    def reject_torch(name, *args, **kwargs):
        if name == "torch":
            raise AssertionError("Whisper device detection must not import PyTorch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_torch)
    WhisperSTT("base", language="en")

    assert seen == {
        "model_name": "base",
        "device": "cuda",
        "compute_type": "float16",
    }


def test_whisper_cuda_detection_falls_back_to_cpu(monkeypatch):
    seen = {}

    class Model:
        def __init__(self, model_name, *, device, compute_type):
            seen.update(device=device, compute_type=compute_type)

    monkeypatch.setitem(
        sys.modules, "ctranslate2",
        types.SimpleNamespace(get_cuda_device_count=lambda: 0),
    )
    monkeypatch.setitem(
        sys.modules, "faster_whisper",
        types.SimpleNamespace(WhisperModel=Model),
    )

    WhisperSTT("base")

    assert seen == {"device": "cpu", "compute_type": "int8"}
