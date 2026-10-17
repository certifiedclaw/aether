"""core/config_io.py -- save the settings dialog's values WITHOUT destroying the rest.

The settings dialog only knows about eleven fields. Both of its save paths used to write
the whole config file as just those fields, silently deleting every other setting
(ntfy_topic, gemini_escalation_model, gemini_cooldown, risk_overrides, ...).

save_config() merges the dialog's values into what is already on disk, and understands
where secrets live (core/credentials.py):
  * a key that currently lives in the OS keyring and is typed again in the dialog is
    UPDATED in the keyring (the keyring wins at read time, so writing only the file
    would make the new key silently ignored); the file keeps "".
  * a blank key field never deletes a keyring entry (after a migration the field is
    blank by default). Use `tools/secrets_cli.py delete` for that.
  * a key that lives in the file behaves exactly as before (typed -> stored, cleared -> "").
The write is atomic (temp file + replace). Values are never logged.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

from core import credentials


def _read_existing(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - missing or corrupt: start from the new values
        return {}


def _is_valid_object(path: Path) -> bool:
    try:
        return isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    except Exception:  # noqa: BLE001
        return False


def merge_for_save(existing: dict, new: dict) -> dict:
    """Pure merge (no I/O except keyring updates for keyring-resident keys)."""
    merged = dict(existing)
    merged.update(new)
    for name in credentials.SECRET_NAMES:
        if name not in new:
            continue
        typed = str(new.get(name) or "").strip()
        in_keyring = bool(credentials._keyring_get(name))
        if in_keyring:
            if typed:
                try:
                    credentials.set_secret(name, typed)
                    merged[name] = ""
                except RuntimeError:
                    merged[name] = typed        # keyring refused: keep it in the file
            else:
                merged[name] = ""               # blank field: keyring entry stays
        else:
            merged[name] = typed
    return merged


def save_config(path: Path, new: dict, indent: int = 4, replace: bool = False) -> dict:
    """Save config atomically; merge by default, or replace non-secret settings."""
    path = Path(path)
    existing = _read_existing(path)
    if replace:
        merged = dict(new)
        secret_updates = {
            name: new[name] for name in credentials.SECRET_NAMES if name in new
        }
        for name in credentials.SECRET_NAMES:
            if name not in merged and name in existing:
                merged[name] = existing[name]
        if secret_updates:
            merged.update(merge_for_save({}, secret_updates))
    else:
        merged = merge_for_save(existing, new)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not _is_valid_object(path):
        try:   # keep what we cannot parse: it may hold settings worth recovering by hand
            shutil.copy2(path, path.with_name(path.name + ".corrupt"))
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=indent, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return merged
