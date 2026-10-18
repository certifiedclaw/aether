"""Stable per-user storage for Aether's persistent data."""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path

from core import config_home

logger = logging.getLogger("aether")

APP_ROOT = config_home.APP_ROOT
_lock = threading.Lock()
_migrated = False


def legacy_data_dir(app_root: Path | None = None) -> Path:
    return Path(app_root or APP_ROOT) / "data"


def _user_data_dir() -> Path:
    override = os.environ.get("AETHER_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return config_home.config_dir() / "data"


def _legacy_long_term_path(app_root: Path | None = None) -> Path:
    return Path(app_root or APP_ROOT) / "memory" / "long_term.json"


def _legacy_chat_history_path() -> Path:
    return Path.home() / ".aether" / "chat_history.json"


def long_term_path() -> Path:
    return data_dir() / "long_term.json"


def chat_history_path() -> Path:
    return data_dir() / "chat_history.json"


def data_path(relative_path: str | Path) -> Path:
    """Return a path inside the stable data directory."""
    return data_dir() / relative_path


def resolve_data_path(value: str | Path | None, default_name: str) -> Path:
    """Resolve old ``data/...`` settings into the stable data directory.

    Explicit paths outside the legacy app data folder remain user-controlled.
    """
    if value is None or not str(value).strip():
        return data_path(default_name)
    path = Path(os.path.expanduser(str(value)))
    legacy = legacy_data_dir()
    if path.is_absolute():
        try:
            relative = path.relative_to(legacy)
        except ValueError:
            return path
        return data_path(relative)
    if path.parts and path.parts[0].casefold() == "data":
        return data_path(Path(*path.parts[1:])) if len(path.parts) > 1 else data_dir()
    return path


def _copy_missing_tree(source: Path, destination: Path) -> list[Path]:
    copied: list[Path] = []
    if not source.is_dir():
        return copied
    source_resolved, destination_resolved = source.resolve(), destination.resolve()
    if (
        source_resolved == destination_resolved
        or source_resolved in destination_resolved.parents
    ):
        return copied
    destination.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source, followlinks=False):
        source_root = Path(root)
        dirs[:] = [name for name in dirs if not (source_root / name).is_symlink()]
        relative = source_root.relative_to(source)
        target_root = destination / relative
        target_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            old_file = source_root / name
            if old_file.is_symlink():
                continue
            new_file = target_root / name
            try:
                if _copy_file_missing(old_file, new_file):
                    copied.append(old_file)
            except (FileExistsError, FileNotFoundError):
                continue
    return copied


def _copy_file_missing(source: Path, destination: Path) -> bool:
    """Copy a file only if an exclusive create succeeds."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as old, destination.open("xb") as new:
            shutil.copyfileobj(old, new)
        shutil.copystat(source, destination)
        return True
    except FileExistsError:
        return False
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def ensure_migrated(app_root: Path | None = None) -> list[str]:
    """Copy legacy data into the per-user directory once; never delete or overwrite."""
    global _migrated
    with _lock:
        if _migrated:
            return []
        _migrated = True

    notes: list[str] = []
    try:
        root = Path(app_root or APP_ROOT)
        destination = _user_data_dir()
        legacy = legacy_data_dir(root)
        destination.mkdir(parents=True, exist_ok=True)
        if destination.resolve() != legacy.resolve():
            copied = _copy_missing_tree(legacy, destination)
            if copied:
                notes.append(f"Copied {len(copied)} legacy data file(s) to the per-user data folder.")

        old_long_term = _legacy_long_term_path(root)
        new_long_term = destination / "long_term.json"
        if old_long_term.is_file() and _copy_file_missing(old_long_term, new_long_term):
            notes.append("Copied legacy long-term memory to the per-user data folder.")

        old_chat = _legacy_chat_history_path()
        new_chat = destination / "chat_history.json"
        if (
            not os.environ.get("AETHER_CHAT_HISTORY_PATH")
            and old_chat.is_file()
        ):
            if _copy_file_missing(old_chat, new_chat):
                notes.append("Copied chat history to the per-user data folder.")
    except Exception as exc:  # migration is best effort; originals remain available
        logger.warning("[data] could not migrate legacy user data: %s", exc)
    for note in notes:
        logger.warning("[data] %s", note)
    return notes


def data_dir() -> Path:
    """Return the stable user-data directory, creating it and migrating on first use."""
    ensure_migrated()
    destination = _user_data_dir()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(destination, 0o700)
        return destination
    except OSError:
        return legacy_data_dir()


def stale_legacy_copies(app_root: Path | None = None) -> list[Path]:
    """Return legacy files that differ from the active per-user copy."""
    try:
        root = Path(app_root or APP_ROOT)
        destination = data_dir()
        legacy = legacy_data_dir(root)
        legacy_resolved, destination_resolved = legacy.resolve(), destination.resolve()
        if (
            destination_resolved == legacy_resolved
            or legacy_resolved in destination_resolved.parents
        ):
            return []
        stale = []
        if legacy.is_dir():
            for old_file in legacy.rglob("*"):
                if old_file.is_file() and not old_file.is_symlink():
                    new_file = destination / old_file.relative_to(legacy)
                    if not new_file.is_file() or old_file.read_bytes() != new_file.read_bytes():
                        stale.append(old_file)

        old_pairs = [
            (_legacy_long_term_path(root), destination / "long_term.json"),
        ]
        if not os.environ.get("AETHER_CHAT_HISTORY_PATH"):
            old_pairs.append((_legacy_chat_history_path(), destination / "chat_history.json"))
        for old_file, new_file in old_pairs:
            if old_file.is_file() and old_file.resolve() != new_file.resolve():
                if not new_file.is_file() or old_file.read_bytes() != new_file.read_bytes():
                    stale.append(old_file)
        return stale
    except OSError:
        return []


def reset_for_tests() -> None:
    global _migrated
    with _lock:
        _migrated = False
