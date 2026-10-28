"""
Aether — Dependency auto-installer.

Called automatically on first launch and after engine reconfiguration.
Installs only the packages that are actually missing, then exits cleanly.
"""
from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from typing import Callable

# Package lists
# Each entry: (import_name, pip_package_name)

_CORE: list[tuple[str, str]] = [
    ("psutil",             "psutil"),
    ("PIL",                "pillow"),
    ("sounddevice",        "sounddevice"),
    ("numpy",              "numpy"),
    ("requests",           "requests"),
    ("bs4",                "beautifulsoup4"),
    ("duckduckgo_search",  "duckduckgo-search"),
    ("pyautogui",          "pyautogui"),
    ("pyperclip",          "pyperclip"),
    ("pygetwindow",        "pygetwindow"),
    ("mss",                "mss"),
    ("cv2",                "opencv-python"),
    ("soundfile",          "soundfile"),
    ("miniaudio",          "miniaudio"),
    ("send2trash",         "send2trash"),
    ("pptx",               "python-pptx"),
    ("youtube_transcript_api", "youtube-transcript-api"),
]

# Windows-only (pywinauto, pycaw, win10toast, comtypes)
_WINDOWS: list[tuple[str, str]] = [
    ("comtypes",   "comtypes"),
    ("pycaw",      "pycaw"),
    ("win10toast", "win10toast"),
    ("pywinauto",  "pywinauto"),
]

# STT engine packages
_STT: dict[str, list[tuple[str, str]]] = {
    "whisper": [("faster_whisper", "faster-whisper")],
    "vosk":    [("vosk",           "vosk")],
}

# TTS engine packages
_TTS: dict[str, list[tuple[str, str]]] = {
    "edgetts":    [("edge_tts", "edge-tts")],
    # kokoro>=0.9 dropped AlbertModel/AutoModel from transformers — version pin is critical
    "kokoro":     [("kokoro",   "kokoro>=0.9"), ("soundfile", "soundfile")],
    "elevenlabs": [],   # uses only requests, already in core
}

# v3 integration packages — installed only when the integration is enabled in config
_INTEGRATIONS: list[tuple[str, str]] = [
    # LangChain
    ("langchain",            "langchain"),
    ("langchain_community",  "langchain-community"),
    ("langchain_ollama",     "langchain-ollama"),
    # LlamaIndex
    ("llama_index",          "llama-index"),
    # Open Interpreter
    ("interpreter",          "open-interpreter"),
]


# Helpers

def _available(module: str) -> bool:
    """Return True if the module can be imported (no actual import)."""
    return importlib.util.find_spec(module) is not None


def _pip(package: str, log: Callable | None = None) -> bool:
    if log:
        log(f"SYS: pip install {package} …")
    result = subprocess.run(
        [
            sys.executable, "-m", "pip", "install", package,
            "--quiet", "--disable-pip-version-check",
        ],
        capture_output=True,
    )
    ok = result.returncode == 0
    if not ok and log:
        stderr = result.stderr.decode(errors="replace").strip()
        log(f"ERR: {package} install failed — {stderr[:140]}")
    return ok


# Public API

def installs_disabled() -> bool:
    """True when runtime package installs must not happen (servers/containers).

    AETHER_SKIP_INSTALL=1 forces it off-limits, =0 forces it allowed; unset
    defaults to disabled under AETHER_HEADLESS so a server never hangs on
    pip / browser downloads at boot.
    """
    import os
    v = os.environ.get("AETHER_SKIP_INSTALL", "").strip().lower()
    if v in ("1", "true", "yes"):
        return True
    if v in ("0", "false", "no"):
        return False
    return os.environ.get("AETHER_HEADLESS", "").strip().lower() in ("1", "true", "yes")


def install_for_config(config: dict, log: Callable | None = None) -> None:
    """
    Install all missing packages required by *config*.

    Blocking — always call from a background thread.
    Progress is reported via the optional *log* callback (receives a str).
    """
    if installs_disabled():
        if log:
            log("SYS: Runtime installs disabled (server mode) - skipping dependency check.")
        return

    stt = config.get("stt_engine", "whisper").lower()
    tts = config.get("tts_engine", "edgetts").lower()

    needed: list[tuple[str, str]] = list(_CORE)
    needed += _STT.get(stt, [])
    if tts == "kokoro" and sys.version_info >= (3, 13):
        # kokoro requires Python <3.13 -- pip can only fail here, on every config change.
        if log:
            log("SYS: Kokoro supports Python 3.10-3.12 only - skipping its install "
                "(Aether will use the EdgeTTS fallback).")
    else:
        needed += _TTS.get(tts, [])
    if platform.system() == "Windows":
        needed += _WINDOWS
    # Install integration packages when enabled in config
    if config.get("integrations.langchain", True):
        needed += [p for p in _INTEGRATIONS if "langchain" in p[1]]
    if config.get("integrations.llama_index", True):
        needed += [p for p in _INTEGRATIONS if "llama" in p[1]]
    if config.get("integrations.open_interpreter", False):  # off by default — runs code
        needed += [p for p in _INTEGRATIONS if "interpreter" in p[1]]

    # Deduplicate (preserve order, key = pip name)
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for mod, pkg in needed:
        if pkg not in seen:
            seen.add(pkg)
            unique.append((mod, pkg))

    missing = [(mod, pkg) for mod, pkg in unique if not _available(mod)]

    if not missing:
        if log:
            log("SYS: All dependencies already installed ✓")
        return

    pkg_names = ", ".join(p for _, p in missing)
    if log:
        log(f"SYS: Installing {len(missing)} package(s): {pkg_names}")

    for _mod, pkg in missing:
        _pip(pkg, log)

    # Playwright: install the package + download Chromium browser
    if not _available("playwright"):
        _pip("playwright", log)
        if log:
            log("SYS: Downloading Playwright browser (Chromium, ~150 MB — one-time)…")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
        )
        if log:
            log("SYS: Playwright browser ready.")

    if log:
        log("SYS: All dependencies ready ✓")
