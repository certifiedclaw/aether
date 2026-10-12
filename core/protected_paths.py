"""core/protected_paths.py -- files the model must never be able to read through any tool.

config/api_keys.json holds live API keys. Several tools accept arbitrary paths and hand the
content to the model (and, on a HARD turn, on to a cloud model): extra_tool diff/word_count,
file_processor, pdf_tool. They call is_protected() first. file_controller is stricter and
refuses the whole install folder (see actions/file_controller._PROTECTED_ROOTS).
"""
from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_PROTECTED_FILES = ("config/api_keys.json", "config/api_keys.json.corrupt")
_PROTECTED_DIRS = ("config/certs", "data/security")
_PROTECTED_NAMES = frozenset({"api_keys.json"})
_PROTECTED_SUFFIXES = frozenset({".pem", ".key"})


def is_protected(path) -> bool:
    """True if `path` is (or resolves to) a secrets file. Fails closed on any error."""
    try:
        p = Path(os.path.expanduser(str(path))).resolve()
        if p.name.lower() in _PROTECTED_NAMES or p.suffix.lower() in _PROTECTED_SUFFIXES:
            return True
        for rel in _PROTECTED_FILES:
            if p == (_ROOT / rel).resolve():
                return True
        for rel in _PROTECTED_DIRS:
            d = (_ROOT / rel).resolve()
            if p == d or d in p.parents:
                return True
        return False
    except Exception:  # noqa: BLE001
        return True


DENIED = "Access denied: that file holds secrets and is not available to tools."
