"""Saving the settings dialog must not delete settings it does not show, and must keep
keyring-resident keys coherent (the keyring wins at read time)."""
import json
import re
import sys
import types
from pathlib import Path

import pytest

from core import config_io, credentials as cred

ROOT = Path(__file__).resolve().parent.parent

DIALOG = {   # exactly what the settings dialog emits
    "stt_engine": "whisper", "stt_model": "small", "stt_language": "auto",
    "llm_provider": "ollama", "llm_url": "http://localhost:11434", "llm_model": "qwen2.5:7b",
    "tts_engine": "edgetts", "tts_voice": "en-US-GuyNeural", "tts_speed": 1.0,
    "elevenlabs_api_key": "", "gemini_api_key": "",
}
EXTRAS = {"ntfy_topic": "my-topic", "gemini_escalation_model": "gemini-3.8-flash",
          "gemini_cooldown": 90, "risk_overrides": {"send_message": "block"},
          "risk_task_ttl": 60, "os_system": "windows", "echo_guard": True}


class FakeKeyring(types.ModuleType):
    def __init__(self, store=None):
        super().__init__("keyring"); self.store = dict(store or {})
    def get_password(self, s, n): return self.store.get((s, n))
    def set_password(self, s, n, v): self.store[(s, n)] = v
    def delete_password(self, s, n): self.store.pop((s, n), None)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    cred.clear_cache()
    for n in cred.SECRET_NAMES:
        for v in cred._env_names(n):
            monkeypatch.delenv(v, raising=False)
    yield
    cred.clear_cache()


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "cfg" / "api_keys.json"
    p.parent.mkdir()
    p.write_text(json.dumps({**DIALOG, **EXTRAS, "gemini_api_key": "file-gem"}, indent=4))
    return p


def read(p):
    return json.loads(p.read_text())


# ── the data-loss bug ───────────────────────────────────────────────────────
def test_saving_settings_keeps_every_setting_the_dialog_does_not_show(cfg, no_keyring):
    config_io.save_config(cfg, {**DIALOG, "llm_model": "qwen3:8b", "gemini_api_key": "file-gem"})
    out = read(cfg)
    for k, v in EXTRAS.items():
        assert out[k] == v, k                                  # nothing silently deleted
    assert out["llm_model"] == "qwen3:8b"                       # dialog values still apply


def test_old_behaviour_was_destructive_so_this_test_would_have_failed_before(cfg, no_keyring):
    """What both ui.py save paths used to do: overwrite with only the dialog's fields."""
    cfg.write_text(json.dumps(DIALOG, indent=4))
    assert "ntfy_topic" not in read(cfg)                        # the loss, reproduced
    cfg.write_text(json.dumps({**DIALOG, **EXTRAS}))
    config_io.save_config(cfg, DIALOG)
    assert read(cfg)["ntfy_topic"] == "my-topic"


def test_both_ui_save_paths_use_the_merging_helper():
    src = (ROOT / "ui.py").read_text(encoding="utf-8")
    assert src.count("save_config(API_FILE, cfg)") == 2
    assert not re.search(r"API_FILE\.write_text\(\s*json\.dumps\(cfg", src)


def test_first_run_with_no_file_just_writes_the_dialog_values(tmp_path, no_keyring):
    p = tmp_path / "new" / "dir" / "api_keys.json"
    config_io.save_config(p, DIALOG)
    assert read(p) == DIALOG


def test_corrupt_or_non_object_existing_file_does_not_block_saving(tmp_path, no_keyring):
    p = tmp_path / "api_keys.json"
    for junk in ("{ not json", "[1, 2, 3]", ""):
        p.write_text(junk)
        config_io.save_config(p, DIALOG)
        assert read(p) == DIALOG


def test_write_is_atomic_and_leaves_no_temp_files(cfg, no_keyring):
    config_io.save_config(cfg, DIALOG)
    assert not list(cfg.parent.glob("*.tmp"))


def test_a_failed_write_leaves_the_original_file_intact(cfg, no_keyring, monkeypatch):
    before = cfg.read_text()
    monkeypatch.setattr(config_io.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        config_io.save_config(cfg, {**DIALOG, "llm_model": "changed"})
    assert cfg.read_text() == before and not list(cfg.parent.glob("*.tmp"))


# ── keys that live in the file behave exactly as before ─────────────────────
def test_file_resident_key_typed_replaced_and_cleared_as_before(cfg, no_keyring):
    config_io.save_config(cfg, {**DIALOG, "gemini_api_key": "new-typed"})
    assert read(cfg)["gemini_api_key"] == "new-typed"
    config_io.save_config(cfg, {**DIALOG, "gemini_api_key": ""})          # user cleared the field
    assert read(cfg)["gemini_api_key"] == ""


# ── keyring-resident keys ───────────────────────────────────────────────────
def test_typing_a_new_key_when_the_old_one_is_in_the_keyring_updates_the_keyring(cfg, monkeypatch):
    fake = FakeKeyring({("aether", "gemini_api_key"): "old-in-keyring"})
    monkeypatch.setitem(sys.modules, "keyring", fake)
    config_io.save_config(cfg, {**DIALOG, "gemini_api_key": "  brand-new  "})
    assert fake.store[("aether", "gemini_api_key")] == "brand-new"       # not silently ignored
    assert read(cfg)["gemini_api_key"] == ""                              # and not left in the file
    assert cred.get_secret("gemini_api_key", read(cfg)) == "brand-new"


def test_a_blank_field_never_deletes_a_keyring_entry(cfg, monkeypatch):
    fake = FakeKeyring({("aether", "gemini_api_key"): "keep-me"})
    monkeypatch.setitem(sys.modules, "keyring", fake)
    config_io.save_config(cfg, {**DIALOG, "gemini_api_key": ""})
    assert fake.store[("aether", "gemini_api_key")] == "keep-me"
    assert read(cfg)["gemini_api_key"] == ""


def test_keyring_refusing_the_update_keeps_the_typed_key_in_the_file(cfg, monkeypatch):
    class Refusing(FakeKeyring):
        def set_password(self, s, n, v): raise RuntimeError("locked")
    fake = Refusing({("aether", "gemini_api_key"): "old"})
    monkeypatch.setitem(sys.modules, "keyring", fake)
    config_io.save_config(cfg, {**DIALOG, "gemini_api_key": "typed"})
    assert read(cfg)["gemini_api_key"] == "typed"                          # not lost


def test_a_key_not_in_the_keyring_is_untouched_by_keyring_logic(cfg, monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setitem(sys.modules, "keyring", fake)
    config_io.save_config(cfg, {**DIALOG, "gemini_api_key": "plain"})
    assert fake.store == {} and read(cfg)["gemini_api_key"] == "plain"


def test_values_never_appear_in_log_output(cfg, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "keyring", FakeKeyring({("aether", "gemini_api_key"): "SECRET-OLD"}))
    with caplog.at_level("DEBUG"):
        config_io.save_config(cfg, {**DIALOG, "gemini_api_key": "SECRET-NEW"})
    assert "SECRET" not in caplog.text
