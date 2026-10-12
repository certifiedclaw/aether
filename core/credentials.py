"""core/credentials.py -- one place to ask "what is the API key for X?".

Resolution order (first non-empty wins):
    1. environment   AETHER_<NAME> then <NAME>   e.g. AETHER_GEMINI_API_KEY, GEMINI_API_KEY
    2. OS keyring    Windows Credential Manager / macOS Keychain / Secret Service
    3. config file   config/api_keys.json  (the fallback, so nothing changes for anyone
                     who has not opted in)

WHY
    The Gemini and ElevenLabs keys used to live only in a plain JSON file that travels
    inside every zip of the project. With this module the key can live in the OS
    credential store instead, and the file can hold an empty string.

Only three places in the code consume a secret: llm_client (Gemini, twice) and tts
(ElevenLabs). Everything else reads non-secret settings from the same file.

The keyring package is OPTIONAL and imported lazily; if it is missing or the backend
misbehaves, resolution silently continues to the file. Values are never logged.
Keyring lookups are cached for a short time (the app asks on every request); the
cache is cleared by set/delete here. A key changed by ANOTHER process (the CLI) is
picked up within CACHE_SECONDS.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger("aether")

SERVICE = "aether"
SECRET_NAMES = ("gemini_api_key", "elevenlabs_api_key")
CACHE_SECONDS = 30

_CACHE: dict[str, tuple[float, str | None]] = {}
_LOCK = threading.Lock()
_warned = False


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _env_names(name: str) -> tuple[str, str]:
    return f"AETHER_{name.upper()}", name.upper()


def _from_env(name: str) -> str:
    for var in _env_names(name):
        v = (os.environ.get(var) or "").strip()
        if v:
            return v
    return ""


def _keyring_module():
    try:
        import keyring  # noqa: PLC0415 - optional dependency, imported lazily
        return keyring
    except Exception:  # noqa: BLE001
        return None


def keyring_available() -> bool:
    return _keyring_module() is not None


def _warn_once(msg: str) -> None:
    global _warned
    if not _warned:
        _warned = True
        logger.warning("%s", msg)


def _keyring_get(name: str) -> str:
    """The keyring value for `name`, "" if absent/unavailable. Cached briefly."""
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(name)
        if hit and hit[0] > now:
            return hit[1] or ""
    kr = _keyring_module()
    value: str | None = None
    if kr is not None:
        try:
            value = (kr.get_password(SERVICE, name) or "").strip() or None
        except Exception as e:  # noqa: BLE001 - a broken backend must never break the app
            _warn_once(f"OS keyring unavailable ({type(e).__name__}); using config file keys")
            value = None
    with _LOCK:
        _CACHE[name] = (now + CACHE_SECONDS, value)
    return value or ""


def get_secret(name: str, cfg: dict | None = None) -> str:
    """The secret for `name` ("" if none): environment, then keyring, then `cfg`/file."""
    v = _from_env(name) or _keyring_get(name)
    if v:
        return v
    if cfg is None:
        try:
            from core.llm_client import _load_config
            cfg = _load_config()
        except Exception:  # noqa: BLE001
            cfg = {}
    return str((cfg or {}).get(name) or "").strip()


def source_of(name: str, cfg: dict | None = None) -> str | None:
    """Where get_secret(name) would come from: "env", "keyring", "file", or None."""
    if _from_env(name):
        return "env"
    if _keyring_get(name):
        return "keyring"
    return "file" if get_secret(name, cfg) else None


def set_secret(name: str, value: str) -> None:
    """Store in the OS keyring. Raises RuntimeError if there is no usable keyring."""
    kr = _keyring_module()
    if kr is None:
        raise RuntimeError("the 'keyring' package is not installed (pip install keyring)")
    value = (value or "").strip()
    if not value:
        raise RuntimeError("refusing to store an empty value")
    try:
        kr.set_password(SERVICE, name, value)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"the OS keyring refused the write ({type(e).__name__})") from None
    clear_cache()


def delete_secret(name: str) -> bool:
    """Remove from the keyring. True if something was removed."""
    kr = _keyring_module()
    if kr is None:
        return False
    try:
        had = bool((kr.get_password(SERVICE, name) or "").strip())
        if had:
            kr.delete_password(SERVICE, name)
        clear_cache()
        return had
    except Exception:  # noqa: BLE001
        clear_cache()
        return False
