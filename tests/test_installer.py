"""Focused tests for dependency-install batching and failure recovery."""
from types import SimpleNamespace

from core import installer


def _configure_small_installer(monkeypatch):
    monkeypatch.setenv("AETHER_SKIP_INSTALL", "0")
    monkeypatch.setattr(installer, "_CORE", [("alpha", "alpha-pkg"), ("beta", "beta-pkg")])
    monkeypatch.setattr(installer, "_STT", {})
    monkeypatch.setattr(installer, "_TTS", {})
    monkeypatch.setattr(installer, "_WINDOWS", [])
    monkeypatch.setattr(installer, "_INTEGRATIONS", [])
    monkeypatch.setattr(installer.platform, "system", lambda: "Linux")
    monkeypatch.setattr(installer, "_available", lambda module: module == "playwright")


def test_missing_packages_install_in_one_pip_invocation(monkeypatch):
    _configure_small_installer(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    installer.install_for_config({"stt_engine": "custom", "tts_engine": "elevenlabs"})

    assert len(calls) == 1
    assert calls[0][4:6] == ["alpha-pkg", "beta-pkg"]
    assert "--prefer-binary" in calls[0]


def test_failed_batch_falls_back_to_individual_packages(monkeypatch):
    _configure_small_installer(monkeypatch)
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=1 if len(calls) == 1 else 0, stderr=b"batch failed")

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    installer.install_for_config({"stt_engine": "custom", "tts_engine": "elevenlabs"})

    assert len(calls) == 3
    assert calls[1][4] == "alpha-pkg"
    assert calls[2][4] == "beta-pkg"
