"""Regression tests for the v12d review fixes (each fails against v12d)."""
import json
from pathlib import Path

import pytest

import actions.desktop as d


# 1. `match` class patterns reach attributes by string and skipped the AST checks
@pytest.mark.parametrize("code", [
    'match "":\n    case str(__class__=c):\n        print(c)\n',
    "match 1:\n    case int(real=r):\n        print(r)\n",
])
def test_match_statements_are_rejected(code):
    assert d._validate_generated_code(code) is not None


# 2. the user must be shown ALL the code that will run
def test_confirmation_dialog_shows_the_whole_code(monkeypatch):
    seen = {}

    class FakeSafety:
        def request_confirmation(self, op, detail=""):
            seen["detail"] = detail
            return True

    import core.safety as safety
    monkeypatch.setattr(safety, "get_safety", lambda *a, **k: FakeSafety())
    code = "x = 1\n" * 600 + "print('TAIL_MARKER')"          # > 1500 chars, < 4000
    assert 1500 < len(code) < d._MAX_GENERATED_CODE
    assert d._confirm_generated_code(code) is True
    assert "TAIL_MARKER" in seen["detail"]


# 3. Aether's own folder is not reachable through the file tool
def test_file_controller_denies_the_install_directory():
    import actions.file_controller as fc
    root = Path(fc.__file__).resolve().parent.parent
    assert fc._is_safe_path(root / "config" / "api_keys.json") is False
    assert fc._is_safe_path(root / "plugins" / "evil.py") is False
    assert fc._is_safe_path(root / "core" / "risk.py") is False


def test_file_controller_still_allows_normal_home_paths(monkeypatch, tmp_path):
    import actions.file_controller as fc
    monkeypatch.setattr(fc, "_SAFE_ROOTS", [tmp_path])
    monkeypatch.setattr(fc, "_PROTECTED_ROOTS", [tmp_path / "aether"])
    assert fc._is_safe_path(tmp_path / "notes.txt") is True
    assert fc._is_safe_path(tmp_path / "aether" / "x.py") is False


# 4. the approval dialog must show hotkey keys and long message bodies
def test_risk_detail_shows_keys_and_long_values_but_hides_secrets():
    from core import risk
    out = risk._detail("computer_control", "hotkey",
                       {"action": "hotkey", "keys": "ctrl+alt+delete",
                        "password": "hunter2", "api_key": "AIzaXXX", "message": "m" * 500})
    assert "ctrl+alt+delete" in out
    assert "hunter2" not in out and "AIzaXXX" not in out
    assert "m" * 500 in out


# 5. an unreadable config is backed up, not silently discarded
def test_corrupt_config_is_backed_up_before_overwrite(tmp_path):
    from core import config_io
    p = tmp_path / "api_keys.json"
    p.write_text("{ not json", encoding="utf-8")
    config_io.save_config(p, {"llm_provider": "ollama"})
    assert json.loads(p.read_text(encoding="utf-8"))["llm_provider"] == "ollama"
    assert (tmp_path / "api_keys.json.corrupt").read_text(encoding="utf-8") == "{ not json"


# 6. migrate must not overwrite a different keyring value with the file's copy
def test_migrate_does_not_clobber_a_different_keyring_value(tmp_path, monkeypatch, capsys):
    from core import credentials
    from tools import secrets_cli
    store = {"gemini_api_key": "NEW-IN-KEYRING"}

    class FakeKR:
        def get_password(self, svc, name): return store.get(name)
        def set_password(self, svc, name, v): store[name] = v
        def delete_password(self, svc, name): store.pop(name, None)

    monkeypatch.setattr(credentials, "_keyring_module", lambda: FakeKR())
    credentials.clear_cache()
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "OLD-IN-FILE", "elevenlabs_api_key": ""}),
                   encoding="utf-8")
    rc = secrets_cli.main(["--config", str(cfg), "migrate", "--apply"])
    assert rc == 1
    assert store["gemini_api_key"] == "NEW-IN-KEYRING"
    assert json.loads(cfg.read_text(encoding="utf-8"))["gemini_api_key"] == "OLD-IN-FILE"


def test_migrate_still_moves_a_key_when_keyring_is_empty(tmp_path, monkeypatch):
    from core import credentials
    from tools import secrets_cli
    store = {}

    class FakeKR:
        def get_password(self, svc, name): return store.get(name)
        def set_password(self, svc, name, v): store[name] = v
        def delete_password(self, svc, name): store.pop(name, None)

    monkeypatch.setattr(credentials, "_keyring_module", lambda: FakeKR())
    credentials.clear_cache()
    cfg = tmp_path / "api_keys.json"
    cfg.write_text(json.dumps({"gemini_api_key": "K1"}), encoding="utf-8")
    assert secrets_cli.main(["--config", str(cfg), "migrate", "--apply"]) == 0
    assert store["gemini_api_key"] == "K1"
    assert json.loads(cfg.read_text(encoding="utf-8"))["gemini_api_key"] == ""
