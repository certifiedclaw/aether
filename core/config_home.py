"""core/config_home.py - where Aether's settings live, and loading them safely.

Problem this solves: config/api_keys.json used to sit INSIDE the version folder, so every
new Aether zip meant copying it over by hand, and a single typo in the JSON made Aether
silently run with NO settings at all.

Now:
  * the live config lives in a stable per-user folder that outlives any version folder
        Windows  %APPDATA%\\Aether            macOS ~/Library/Application Support/Aether
        Linux    $XDG_CONFIG_HOME/aether or ~/.config/aether
    (override with the AETHER_CONFIG_DIR environment variable; Docker sets it to /app/config)
  * the first run after upgrading COPIES your old <app>/config/api_keys.json (and
    config/certs) there. The old files are left untouched, so going back a version works.
    If both exist afterwards, the per-user copy wins; doctor warns if they differ.
  * a config file that no longer parses does not wipe your settings: Aether keeps using the
    last good copy (api_keys.json.last_good), says so loudly once, and doctor tells you.
Nothing here ever raises into the caller.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import sys
import threading
from pathlib import Path

logger = logging.getLogger("aether")

CONFIG_NAME = "api_keys.json"
APP_ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent

_lock = threading.Lock()
_migrated = False
_warned: set[str] = set()
_cache: dict[str, tuple[int, dict]] = {}


def _user_dir() -> Path:
    env = os.environ.get("AETHER_CONFIG_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Aether"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Aether"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "aether"


def legacy_dir(app_root: Path | None = None) -> Path:
    return Path(app_root or APP_ROOT) / "config"


def config_dir(app_root: Path | None = None) -> Path:
    """Directory holding api_keys.json and certs/. An explicit non-default app_root (tests,
    tools pointed at another checkout) keeps the old <root>/config layout."""
    if app_root is not None and Path(app_root).resolve() != APP_ROOT.resolve():
        return legacy_dir(app_root)
    d = _user_dir()
    ensure_migrated()
    try:
        d.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(d, 0o700)
        return d
    except OSError:
        return legacy_dir()        # read-only home: keep working from the app folder


def config_path(app_root: Path | None = None) -> Path:
    return config_dir(app_root) / CONFIG_NAME


def certs_dir(app_root: Path | None = None) -> Path:
    return config_dir(app_root) / "certs"


def last_good_path(path: Path) -> Path:
    return path.with_name(path.name + ".last_good")


# ── one-time copy from the old location ──────────────────────────────────────────

def ensure_migrated() -> list[str]:
    """Copy the old in-app config (and certs) to the per-user folder, once, if it is not there
    yet. Returns human-readable notes about what was done. Never deletes the originals."""
    global _migrated
    notes: list[str] = []
    with _lock:
        if _migrated:
            return notes
        _migrated = True
    try:
        dest, old = _user_dir(), legacy_dir()
        if dest.resolve() == old.resolve():
            return notes
        dest.mkdir(parents=True, exist_ok=True)
        src_cfg, dst_cfg = old / CONFIG_NAME, dest / CONFIG_NAME
        if src_cfg.is_file() and not dst_cfg.exists():
            shutil.copy2(src_cfg, dst_cfg)
            _private(dst_cfg)
            notes.append(f"Copied your settings from {src_cfg} to {dst_cfg}. "
                         "From now on edit the new one; new Aether versions will find it automatically.")
        src_certs, dst_certs = old / "certs", dest / "certs"
        if src_certs.is_dir() and not dst_certs.exists():
            shutil.copytree(src_certs, dst_certs)
            notes.append(f"Copied dashboard certificates to {dst_certs}.")
        for n in notes:
            logger.warning("[config] %s", n)
            print(f"[Aether] {n}")
    except Exception as e:  # noqa: BLE001 - migration is best-effort; the old location still works
        logger.warning("[config] could not move settings to the per-user folder: %s", e)
    return notes


def _private(path: Path) -> None:
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError:
        pass


def stale_legacy_copy() -> Path | None:
    """The old in-app config if it still exists AND differs from the live one (doctor warns)."""
    try:
        live, old = config_path(), legacy_dir() / CONFIG_NAME
        if live.resolve() == old.resolve() or not old.is_file() or not live.is_file():
            return None
        return None if old.read_bytes() == live.read_bytes() else old
    except OSError:
        return None


# ── loading that survives a typo ─────────────────────────────────────────────────

def load_config(path: Path | None = None) -> dict:
    """Read the config as a dict. Cached by mtime. Never raises, never returns a half-read file.

    Missing file -> {}. Unparseable file -> the last good copy (loudly, once), else {}.
    A successfully parsed file refreshes the last-good copy."""
    p = Path(path) if path else config_path()
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        return {}
    key = str(p)
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    data, error = _parse(p)
    if data is None:
        good, _ = _parse(last_good_path(p))
        _warn_once(key, mtime, f"{p} is not valid JSON ({error}). "
                   + ("Using your last good settings until it is fixed." if good is not None
                      else "No earlier good copy exists, so built-in defaults are in use."))
        data = good if good is not None else {}
    else:
        _remember_good(p)
    _cache[key] = (mtime, data)
    return data


def _parse(p: Path) -> tuple[dict | None, str]:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return None, "not a JSON object"
        return d, ""
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"[:160]


def _remember_good(p: Path) -> None:
    try:
        lg = last_good_path(p)
        raw = p.read_bytes()
        if not lg.exists() or lg.read_bytes() != raw:
            tmp = lg.with_name(lg.name + ".tmp")
            tmp.write_bytes(raw)
            _private(tmp)
            os.replace(tmp, lg)
    except OSError:
        pass


def _warn_once(key: str, mtime, msg: str) -> None:
    token = f"{key}:{mtime}"
    if token in _warned:
        return
    _warned.add(token)
    logger.error("[config] %s", msg)
    print(f"[Aether] WARNING: {msg}")


def reset_for_tests() -> None:
    global _migrated
    _migrated = False
    _warned.clear()
    _cache.clear()
