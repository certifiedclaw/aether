"""Portable, secret-free export and restore for Aether's local user data."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from core.archive_safety import archive_size_error
from core.chat_history import _valid_store, history_path
from core.credentials import SECRET_NAMES
from memory.config_manager import BASE_DIR, CONFIG_FILE

_FORMAT_VERSION = 1
_FIXED_MEMBERS = {
    "chat_history.json",
    "long_term.json",
    "memory.db",
    "goals.json",
    "settings.json",
}
_PORTABLE_PATHS = {
    "memory.db_path": "./data/memory.db",
    "rag.index_path": "./data/rag_index",
    "goals.path": "./data/goals.json",
    "llama_index.chroma_dir": "./data/chroma_db",
}


def _resolve_configured_path(value: str, default: str) -> Path:
    path = Path(os.path.expanduser(str(value or default)))
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


def _read_object(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return dict(default or {})
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _settings_for_export() -> dict:
    settings = _read_object(CONFIG_FILE)
    for key in SECRET_NAMES:
        settings.pop(key, None)
    settings.update(_PORTABLE_PATHS)
    return settings


def _add_file(archive: zipfile.ZipFile, path: Path, member: str) -> None:
    archive.write(path, member)


def _write_bundle(destination: Path) -> list[str]:
    config = _read_object(CONFIG_FILE)
    db_path = _resolve_configured_path(config.get("memory.db_path"), "./data/memory.db")
    rag_path = _resolve_configured_path(config.get("rag.index_path"), "./data/rag_index")
    chroma_path = _resolve_configured_path(
        config.get("llama_index.chroma_dir"), "./data/chroma_db"
    )
    goals_path = _resolve_configured_path(config.get("goals.path"), "./data/goals.json")
    chat_path = history_path()
    long_term_path = BASE_DIR / "memory" / "long_term.json"

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aether_bundle_") as temp_dir:
        temp = Path(temp_dir)
        chat_member = temp / "chat_history.json"
        if chat_path.exists():
            payload = json.loads(chat_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") not in (1, 2):
                raise ValueError("Chat history has an unsupported format.")
            if payload.get("version") == 2 and not isinstance(payload.get("sessions"), list):
                raise ValueError("Chat history has an invalid sessions list.")
            store = _valid_store(payload)
        else:
            store = {"active_id": "", "sessions": []}
        chat_member.write_text(
            json.dumps({"version": 2, **store}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        settings_member = temp / "settings.json"
        settings_member.write_text(
            json.dumps(_settings_for_export(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        long_term_member = temp / "long_term.json"
        long_term_member.write_text(
            json.dumps(_read_object(long_term_path), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        members = ["chat_history.json", "long_term.json", "settings.json"]
        staged_db = None
        if db_path.is_file():
            staged_db = temp / "memory.db"
            source = sqlite3.connect(str(db_path))
            target = sqlite3.connect(str(staged_db))
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
            members.append("memory.db")
        if goals_path.is_file():
            goals = json.loads(goals_path.read_text(encoding="utf-8"))
            goals_member = temp / "goals.json"
            goals_member.write_text(json.dumps(goals, ensure_ascii=False, indent=2), encoding="utf-8")
            members.append("goals.json")

        rag_members = []
        if rag_path.is_symlink():
            raise ValueError(f"RAG index directory is a symbolic link: {rag_path}")
        if rag_path.is_dir():
            for source in sorted(rag_path.rglob("*")):
                if source.is_symlink():
                    raise ValueError(f"RAG index contains a symbolic link: {source}")
                if source.is_file():
                    relative = source.relative_to(rag_path).as_posix()
                    rag_members.append((source, f"rag_index/{relative}"))
            members.extend(member for _, member in rag_members)
        chroma_members = []
        if chroma_path.is_symlink():
            raise ValueError(f"LlamaIndex store is a symbolic link: {chroma_path}")
        if chroma_path.is_dir():
            for source in sorted(chroma_path.rglob("*")):
                if source.is_symlink():
                    raise ValueError(f"LlamaIndex store contains a symbolic link: {source}")
                if source.is_file():
                    relative = source.relative_to(chroma_path).as_posix()
                    chroma_members.append((source, f"chroma_db/{relative}"))
            members.extend(member for _, member in chroma_members)

        manifest = {
            "format": "aether-portable-bundle",
            "format_version": _FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": members,
            "excluded_credentials": list(SECRET_NAMES),
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
            _add_file(archive, chat_member, "chat_history.json")
            _add_file(archive, long_term_member, "long_term.json")
            _add_file(archive, settings_member, "settings.json")
            if staged_db:
                _add_file(archive, staged_db, "memory.db")
            if "goals.json" in members:
                _add_file(archive, temp / "goals.json", "goals.json")
            for source, member in rag_members:
                _add_file(archive, source, member)
            for source, member in chroma_members:
                _add_file(archive, source, member)
    return members


def export_bundle(output_path: str = "") -> str:
    """Export chats, memories, documents, goals, and non-secret settings to a ZIP."""
    destination = (
        Path(os.path.expanduser(output_path))
        if output_path
        else BASE_DIR / "data" / f"aether_bundle_{datetime.now():%Y%m%d_%H%M%S_%f}.zip"
    )
    if not destination.is_absolute():
        destination = BASE_DIR / destination
    temporary = None
    try:
        config = _read_object(CONFIG_FILE)
        database = _resolve_configured_path(
            config.get("memory.db_path"), "./data/memory.db"
        )
        rag = _resolve_configured_path(config.get("rag.index_path"), "./data/rag_index")
        chroma = _resolve_configured_path(
            config.get("llama_index.chroma_dir"), "./data/chroma_db"
        )
        goals = _resolve_configured_path(config.get("goals.path"), "./data/goals.json")
        protected_files = {
            CONFIG_FILE.resolve(),
            history_path().resolve(),
            (BASE_DIR / "memory" / "long_term.json").resolve(),
            database.resolve(),
            goals.resolve(),
        }
        output = destination.resolve()
        if output in protected_files or any(
            parent.resolve() == output or parent.resolve() in output.parents
            for parent in (rag, chroma)
        ):
            raise ValueError("Output path would overwrite Aether data.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix="aether_bundle_", suffix=".tmp", delete=False
        ) as file:
            temporary = Path(file.name)
        members = _write_bundle(temporary)
        os.replace(temporary, destination)
        return (
            f"Exported Aether bundle: {destination}\n"
            f"Included {len(members)} data file(s). Secret credentials were excluded."
        )
    except Exception as exc:
        return f"Bundle export failed: {exc}"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_member(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name:
        return False
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        return False
    return name in _FIXED_MEMBERS or (
        len(path.parts) > 1 and path.parts[0] in {"rag_index", "chroma_db"}
    )


def _stage_bundle(source: Path, stage: Path) -> tuple[dict, dict]:
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        size_error = archive_size_error(infos)
        if size_error:
            raise ValueError(size_error)
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise ValueError("Bundle contains duplicate archive members.")
        for info in infos:
            if not _safe_member(info.filename) and info.filename != "manifest.json":
                raise ValueError(f"Bundle contains an unsafe or unsupported path: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Bundle contains a symbolic link: {info.filename}")
        if "manifest.json" not in names:
            raise ValueError("Bundle is missing manifest.json.")
        manifest = json.loads(archive.read("manifest.json"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != "aether-portable-bundle"
            or manifest.get("format_version") != _FORMAT_VERSION
            or not isinstance(manifest.get("files"), list)
        ):
            raise ValueError("Unsupported or invalid Aether bundle manifest.")
        expected = set(manifest["files"])
        actual = set(names) - {"manifest.json"}
        if (
            len(expected) != len(manifest["files"])
            or expected != actual
            or any(not _safe_member(name) for name in expected)
        ):
            raise ValueError("Bundle manifest does not match its contents.")

        for name in expected:
            target = stage.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))

    chat_path = stage / "chat_history.json"
    if not chat_path.is_file():
        raise ValueError("Bundle is missing chat history.")
    chat = json.loads(chat_path.read_text(encoding="utf-8"))
    if not isinstance(chat, dict) or chat.get("version") not in (1, 2):
        raise ValueError("Bundle chat history has an unsupported format.")
    if chat.get("version") == 2 and not isinstance(chat.get("sessions"), list):
        raise ValueError("Bundle chat history has an invalid sessions list.")
    chat_store = _valid_store(chat)
    chat_path.write_text(
        json.dumps({"version": 2, **chat_store}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    settings_path = stage / "settings.json"
    if not settings_path.is_file():
        raise ValueError("Bundle is missing settings.")
    settings = _read_object(settings_path)
    for secret in SECRET_NAMES:
        settings.pop(secret, None)
    settings.update(_PORTABLE_PATHS)
    for member in ("long_term.json", "goals.json"):
        path = stage / member
        if path.is_file():
            json.loads(path.read_text(encoding="utf-8"))
    long_term_path = stage / "long_term.json"
    if long_term_path.exists() and not isinstance(
        json.loads(long_term_path.read_text(encoding="utf-8")), dict
    ):
        raise ValueError("Bundle long-term memory must be a JSON object.")
    db_path = stage / "memory.db"
    if db_path.is_file():
        db = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
        try:
            result = db.execute("PRAGMA quick_check").fetchone()
        finally:
            db.close()
        if not result or result[0] != "ok":
            raise ValueError("Bundle memory database failed its integrity check.")
    return manifest, settings


def _replace_file(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not staged.is_file():
        destination.unlink(missing_ok=True)
        return
    with tempfile.NamedTemporaryFile(
        dir=destination.parent, prefix=destination.name + "_", suffix=".tmp", delete=False
    ) as file:
        temporary = Path(file.name)
    try:
        shutil.copy2(staged, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _replace_directory(staged: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    replacement = Path(tempfile.mkdtemp(prefix=destination.name + "_new_", dir=destination.parent))
    previous = destination.with_name(f"{destination.name}.pre-import-{uuid.uuid4().hex}")
    try:
        if staged.is_dir():
            shutil.copytree(staged, replacement, dirs_exist_ok=True)
        if destination.exists():
            os.replace(destination, previous)
        os.replace(replacement, destination)
    except Exception:
        if destination.exists() and previous.exists():
            _remove_path(destination)
        if previous.exists() and not destination.exists():
            os.replace(previous, destination)
        raise
    finally:
        if replacement.exists():
            shutil.rmtree(replacement, ignore_errors=True)
    if previous.exists():
        _remove_path(previous)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def import_bundle(input_path: str) -> str:
    """Restore a portable bundle, replacing local data after saving a recovery bundle."""
    source = Path(os.path.expanduser(input_path))
    if not source.is_absolute():
        source = BASE_DIR / source
    if not source.is_file():
        return f"Bundle not found: {input_path}"

    backup_path = BASE_DIR / "data" / "backups" / (
        f"before_import_{datetime.now():%Y%m%d_%H%M%S_%f}.zip"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="aether_import_") as temp_dir:
            stage = Path(temp_dir)
            _manifest, settings = _stage_bundle(source, stage)
            backup_members = _write_bundle(backup_path)
            (BASE_DIR / "memory").mkdir(parents=True, exist_ok=True)
            data_dir = BASE_DIR / "data"
            data_dir.mkdir(parents=True, exist_ok=True)

            _replace_file(stage / "memory.db", data_dir / "memory.db")
            _replace_file(stage / "long_term.json", BASE_DIR / "memory" / "long_term.json")
            _replace_file(stage / "goals.json", data_dir / "goals.json")
            _replace_file(stage / "chat_history.json", history_path())
            _replace_directory(stage / "rag_index", data_dir / "rag_index")
            _replace_directory(stage / "chroma_db", data_dir / "chroma_db")

            from core.config_io import save_config
            save_config(CONFIG_FILE, settings, replace=True)
        return (
            f"Imported Aether bundle: {source}\n"
            f"Current data was replaced. Recovery bundle: {backup_path} "
            f"({len(backup_members)} data file(s)). API credentials were not imported."
        )
    except Exception as exc:
        return f"Bundle import failed: {exc}. Recovery bundle, if created: {backup_path}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export or restore an Aether portable bundle.")
    commands = parser.add_subparsers(dest="action", required=True)
    export = commands.add_parser("export", help="Create a portable bundle")
    export.add_argument("path", nargs="?", default="", help="Output ZIP path (default: data/)")
    restore = commands.add_parser("import", help="Restore a portable bundle")
    restore.add_argument("path", help="Input ZIP path")
    args = parser.parse_args()
    result = export_bundle(args.path) if args.action == "export" else import_bundle(args.path)
    print(result)
    return 1 if result.startswith((
        "Bundle export failed:",
        "Bundle import failed:",
        "Bundle not found:",
    )) else 0


if __name__ == "__main__":
    raise SystemExit(main())
