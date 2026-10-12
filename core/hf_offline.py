"""core/hf_offline.py -- temporary network access for first-run model downloads.

Aether sets HF_HUB_OFFLINE=1 at launch so a cached model never phones
huggingface.co. When a model is not cached yet, STT/TTS need ONE online load.
`allow_online()` lifts the offline flags for that load only and puts them back
afterwards, so the rest of the session (and every later launch) stays offline.
huggingface_hub copies the env var into its constants at import time, so the
constant is flipped as well when the library is already loaded.
"""
from __future__ import annotations

import contextlib
import os
import sys

_ENV_KEYS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")


@contextlib.contextmanager
def allow_online():
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    consts = sys.modules.get("huggingface_hub.constants")
    saved_const = getattr(consts, "HF_HUB_OFFLINE", None) if consts else None
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    if consts is not None and saved_const is not None:
        consts.HF_HUB_OFFLINE = False
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
        if consts is not None and saved_const is not None:
            consts.HF_HUB_OFFLINE = saved_const
