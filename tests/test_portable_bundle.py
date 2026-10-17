import json
import sqlite3
import zipfile

from core import portable_bundle as bundle


def _seed_install(root, monkeypatch, model, secret, chat_title):
    config = root / "config" / "api_keys.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({
        "llm_model": model,
        "gemini_api_key": secret,
        "source_only_setting": model,
        "memory.db_path": "./data/memory.db",
        "rag.index_path": "./data/rag_index",
        "llama_index.chroma_dir": "./data/chroma_db",
    }), encoding="utf-8")
    monkeypatch.setattr(bundle, "BASE_DIR", root)
    monkeypatch.setattr(bundle, "CONFIG_FILE", config)

    history = root / "profile" / "chat_history.json"
    history.parent.mkdir(parents=True)
    history.write_text(json.dumps({
        "version": 2,
        "active_id": "active",
        "sessions": [{
            "id": "active",
            "title": chat_title,
            "updated_at": 1,
            "messages": [{"role": "user", "content": chat_title}],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(bundle, "history_path", lambda: history)

    data = root / "data"
    data.mkdir(exist_ok=True)
    db = sqlite3.connect(data / "memory.db")
    try:
        db.execute("CREATE TABLE memories (content TEXT)")
        db.execute("INSERT INTO memories VALUES (?)", (model,))
        db.commit()
    finally:
        db.close()
    (root / "memory").mkdir()
    (root / "memory" / "long_term.json").write_text(
        json.dumps({"identity": {"model": model}}), encoding="utf-8"
    )
    (data / "goals.json").write_text(json.dumps({"goal": model}), encoding="utf-8")
    rag = data / "rag_index"
    rag.mkdir()
    (rag / "chunks.json").write_text(json.dumps([model]), encoding="utf-8")
    chroma = data / "chroma_db"
    chroma.mkdir()
    (chroma / "index.bin").write_text(model, encoding="utf-8")
    return config, history


def test_bundle_round_trip_restores_data_and_excludes_credentials(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    _seed_install(source_root, monkeypatch, "source-model", "SOURCE_SECRET", "Source chat")
    archive = source_root / "portable.zip"

    exported = bundle.export_bundle(str(archive))

    assert "Secret credentials were excluded" in exported
    with zipfile.ZipFile(archive) as saved:
        assert "settings.json" in saved.namelist()
        assert b"SOURCE_SECRET" not in saved.read("settings.json")
        assert "rag_index/chunks.json" in saved.namelist()
        assert "chroma_db/index.bin" in saved.namelist()

    target_root = tmp_path / "target"
    target_root.mkdir()
    config, history = _seed_install(
        target_root, monkeypatch, "target-model", "TARGET_SECRET", "Target chat"
    )
    target_settings = json.loads(config.read_text(encoding="utf-8"))
    target_settings["target_only_setting"] = "must be replaced"
    config.write_text(json.dumps(target_settings), encoding="utf-8")
    imported = bundle.import_bundle(str(archive))

    assert "Current data was replaced." in imported
    assert "API credentials were not imported." in imported
    assert json.loads(history.read_text(encoding="utf-8"))["sessions"][0]["title"] == "Source chat"
    assert json.loads((target_root / "memory" / "long_term.json").read_text())["identity"]["model"] == "source-model"
    assert json.loads((target_root / "data" / "goals.json").read_text())["goal"] == "source-model"
    assert json.loads((target_root / "data" / "rag_index" / "chunks.json").read_text()) == ["source-model"]
    assert (target_root / "data" / "chroma_db" / "index.bin").read_text(encoding="utf-8") == "source-model"
    db = sqlite3.connect(target_root / "data" / "memory.db")
    try:
        assert db.execute("SELECT content FROM memories").fetchone()[0] == "source-model"
    finally:
        db.close()
    saved_config = json.loads(config.read_text(encoding="utf-8"))
    assert saved_config["llm_model"] == "source-model"
    assert saved_config["source_only_setting"] == "source-model"
    assert "target_only_setting" not in saved_config
    assert saved_config["gemini_api_key"] == "TARGET_SECRET"
    assert saved_config["memory.db_path"] == "./data/memory.db"
    assert list((target_root / "data" / "backups").glob("before_import_*.zip"))


def test_bundle_import_rejects_unsafe_archive_paths(tmp_path, monkeypatch):
    root = tmp_path / "target"
    root.mkdir()
    _seed_install(root, monkeypatch, "keep-model", "LOCAL_SECRET", "Keep chat")
    malicious = tmp_path / "malicious.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("manifest.json", json.dumps({
            "format": "aether-portable-bundle",
            "format_version": 1,
            "files": ["../outside.txt"],
        }))
        archive.writestr("../outside.txt", "must not be written")

    result = bundle.import_bundle(str(malicious))

    assert result.startswith("Bundle import failed: Bundle contains an unsafe")
    assert not (tmp_path / "outside.txt").exists()
    assert json.loads((root / "config" / "api_keys.json").read_text())["llm_model"] == "keep-model"


def test_bundle_export_refuses_to_overwrite_config(tmp_path, monkeypatch):
    root = tmp_path / "target"
    root.mkdir()
    config, _ = _seed_install(root, monkeypatch, "keep-model", "LOCAL_SECRET", "Keep chat")
    original = config.read_bytes()

    result = bundle.export_bundle(str(config))

    assert result.startswith("Bundle export failed: Output path would overwrite Aether data.")
    assert config.read_bytes() == original
