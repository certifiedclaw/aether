import json
from pathlib import Path

import pytest

from core import config_home, data_home


@pytest.fixture
def paths(tmp_path, monkeypatch):
    app = tmp_path / "app"
    data = app / "data"
    (data / "rag_index").mkdir(parents=True)
    (data / "rag_index" / "chunks.json").write_text('{"chunks": 1}', encoding="utf-8")
    (data / "memory.db").write_bytes(b"old database")
    (app / "memory").mkdir()
    (app / "memory" / "long_term.json").write_text('{"notes": {"old": true}}', encoding="utf-8")
    user_data = tmp_path / "user" / "data"
    monkeypatch.setenv("AETHER_DATA_DIR", str(user_data))
    monkeypatch.setenv("AETHER_CONFIG_DIR", str(tmp_path / "user" / "config"))
    monkeypatch.delenv("AETHER_CHAT_HISTORY_PATH", raising=False)
    monkeypatch.setattr(data_home, "APP_ROOT", app)
    data_home.reset_for_tests()
    config_home.reset_for_tests()
    yield app, user_data
    data_home.reset_for_tests()
    config_home.reset_for_tests()


def test_migration_copies_once_and_keeps_originals(paths):
    app, user_data = paths

    notes = data_home.ensure_migrated()

    assert notes
    assert (user_data / "rag_index" / "chunks.json").read_text(encoding="utf-8") == '{"chunks": 1}'
    assert (user_data / "long_term.json").read_text(encoding="utf-8") == '{"notes": {"old": true}}'
    assert (app / "data" / "memory.db").read_bytes() == b"old database"
    assert (app / "memory" / "long_term.json").exists()

    (app / "data" / "new-after-migration.txt").write_text("late", encoding="utf-8")
    assert data_home.ensure_migrated() == []
    assert not (user_data / "new-after-migration.txt").exists()


def test_migration_never_overwrites_files_in_user_data(paths):
    app, user_data = paths
    (user_data / "rag_index").mkdir(parents=True)
    (user_data / "rag_index" / "chunks.json").write_text("newer", encoding="utf-8")
    (user_data / "long_term.json").write_text('{"notes": {"user": true}}', encoding="utf-8")
    (user_data / "memory.db").write_bytes(b"newer database")

    data_home.ensure_migrated()

    assert (user_data / "rag_index" / "chunks.json").read_text(encoding="utf-8") == "newer"
    assert json.loads((user_data / "long_term.json").read_text(encoding="utf-8")) == {
        "notes": {"user": True}
    }
    assert (user_data / "memory.db").read_bytes() == b"newer database"
    assert (app / "data" / "memory.db").read_bytes() == b"old database"


def test_env_override_and_default_data_sibling(paths, monkeypatch, tmp_path):
    _, user_data = paths
    assert data_home.data_dir() == user_data
    override = tmp_path / "override"
    monkeypatch.setenv("AETHER_DATA_DIR", str(override))
    data_home.reset_for_tests()
    assert data_home.data_dir() == override

    monkeypatch.delenv("AETHER_DATA_DIR")
    config_home.reset_for_tests()
    data_home.reset_for_tests()
    config_dir = tmp_path / "another-user" / "config"
    monkeypatch.setenv("AETHER_CONFIG_DIR", str(config_dir))
    assert data_home.data_dir() == config_dir / "data"


def test_stale_copy_detection_reports_changed_legacy_files(paths):
    app, user_data = paths
    data_home.ensure_migrated()
    assert data_home.stale_legacy_copies(app) == []

    (app / "data" / "memory.db").write_bytes(b"changed old file")
    stale = data_home.stale_legacy_copies(app)
    assert app / "data" / "memory.db" in stale


def test_only_security_subdirectory_is_protected(paths):
    _, user_data = paths
    from core.protected_paths import is_protected

    assert is_protected(user_data / "security" / "sessions.json")
    assert not is_protected(user_data / "memory.db")
    assert not is_protected(user_data / "long_term.json")


def test_legacy_chat_history_is_copied_without_deleting(monkeypatch, tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    home = tmp_path / "home"
    old_chat = home / ".aether" / "chat_history.json"
    old_chat.parent.mkdir(parents=True)
    old_chat.write_text('{"version": 2, "sessions": []}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(data_home, "APP_ROOT", app)
    destination = tmp_path / "user-data"
    monkeypatch.setenv("AETHER_DATA_DIR", str(destination))
    monkeypatch.delenv("AETHER_CHAT_HISTORY_PATH", raising=False)
    data_home.reset_for_tests()

    assert data_home.chat_history_path() == destination / "chat_history.json"
    assert (destination / "chat_history.json").read_text(encoding="utf-8") == old_chat.read_text(encoding="utf-8")
    assert old_chat.exists()
