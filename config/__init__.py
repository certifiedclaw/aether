# config/__init__.py
import json
import platform
from pathlib import Path

from core import config_home as _config_home

_CONFIG_PATH = _config_home.config_path()

def get_config() -> dict:
    return _config_home.load_config(_CONFIG_PATH)

def get_os() -> str:
    """Returns 'windows' | 'mac' | 'linux' — detected at runtime, not from config."""
    s = platform.system().lower()
    if s == "darwin":
        return "mac"
    if s == "windows":
        return "windows"
    return "linux"

def is_windows() -> bool: return get_os() == "windows"
def is_mac()     -> bool: return get_os() == "mac"
def is_linux()   -> bool: return get_os() == "linux"
