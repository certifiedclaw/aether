"""core/config_home: stable per-user config location, one-time migration, typo survival."""
import json
from pathlib import Path

import pytest

from core import config_home as ch


@pytest.fixture
def env(tmp_path, monkeypatch):
    user = tmp_path / "user"
    app = tmp_path / "app"
    (app / "config").mkdir(parents=True)
    monkeypatch.setenv("AETHER_CONFIG_DIR", str(user))
    monkeypatch.setattr(ch, "APP_ROOT", app)
    ch.reset_for_tests()
    yield user, app
    ch.reset_for_tests()


def test_env_override_wins(env):
    user, _ = env
    assert ch.config_dir() == user
    assert ch.config_path() == user / "api_keys.json"
    assert ch.certs_dir() == user / "certs"


def test_explicit_root_keeps_legacy_layout(env, tmp_path):
    assert ch.config_dir(tmp_path / "x") == tmp_path / "x" / "config"


def test_migration_copies_once_and_keeps_original(env):
    user, app = env
    (app / "config" / "api_keys.json").write_text('{"llm_model": "m"}')
    (app / "config" / "certs").mkdir()
    (app / "config" / "certs" / "aether.crt").write_text("c")
    ch.ensure_migrated()
    assert json.loads((user / "api_keys.json").read_text()) == {"llm_model": "m"}
    assert (user / "certs" / "aether.crt").read_text() == "c"
    assert (app / "config" / "api_keys.json").exists()          # never deleted
    (user / "api_keys.json").write_text('{"llm_model": "edited"}')
    ch.ensure_migrated()                                        # does not overwrite
    assert json.loads((user / "api_keys.json").read_text())["llm_model"] == "edited"


def test_stale_legacy_copy_detected(env):
    user, app = env
    (app / "config" / "api_keys.json").write_text('{"a": 1}')
    ch.ensure_migrated()
    assert ch.stale_legacy_copy() is None
    (user / "api_keys.json").write_text('{"a": 2}')
    assert ch.stale_legacy_copy() == app / "config" / "api_keys.json"


def test_load_missing_returns_empty(env):
    assert ch.load_config() == {}


def test_typo_falls_back_to_last_good(env):
    user, _ = env
    user.mkdir(parents=True)
    p = user / "api_keys.json"
    p.write_text('{"llm_model": "good"}')
    assert ch.load_config(p)["llm_model"] == "good"
    p.write_text('{"llm_model": "bad",')          # broken JSON
    assert ch.load_config(p)["llm_model"] == "good"
    assert ch.last_good_path(p).exists()


def test_typo_without_last_good_returns_empty_not_raise(env):
    user, _ = env
    user.mkdir(parents=True)
    p = user / "api_keys.json"
    p.write_text("{nope")
    assert ch.load_config(p) == {}


def test_cache_invalidates_on_change(env):
    user, _ = env
    user.mkdir(parents=True)
    p = user / "api_keys.json"
    p.write_text('{"a": 1}')
    assert ch.load_config(p)["a"] == 1
    p.write_text('{"a": 22}')
    assert ch.load_config(p)["a"] == 22


def test_config_dir_is_protected_from_tools(env):
    from core.protected_paths import is_protected
    user, _ = env
    user.mkdir(parents=True)
    assert is_protected(user / "api_keys.json")
    assert is_protected(user / "api_keys.json.last_good")
    assert is_protected(user / "certs" / "aether.crt")
